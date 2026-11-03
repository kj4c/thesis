"""Extract event details from a glasses photo and add confirmed events to Google Calendar."""

from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from browser_use.llm.messages import (
    ContentPartImageParam,
    ContentPartTextParam,
    ImageURL,
    SystemMessage,
    UserMessage,
)

from llm import _ainvoke_with_retry, make_glasses_vision_llm
import calendar_storage


class EventDetails(BaseModel):
    """Candidate details read from the poster; the user reviews these before saving."""

    title: str = Field(description="Event name or a short, useful event title")
    start_date: str | None = Field(
        default=None, description="Event start date as YYYY-MM-DD, or null if not readable"
    )
    start_time: str | None = Field(
        default=None, description="Event start time as 24-hour HH:MM, or null if not readable"
    )
    end_date: str | None = Field(
        default=None, description="Event end date as YYYY-MM-DD, or null if not shown"
    )
    end_time: str | None = Field(
        default=None, description="Event end time as 24-hour HH:MM, or null if not shown"
    )
    location: str | None = Field(default=None, description="Event venue or address, if shown")
    description: str = Field(
        default="", description="Brief event information useful in the calendar entry"
    )


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def is_calendar_request(prompt: str) -> bool:
    """Return true only for an explicit request to put an event on a calendar."""
    import re

    text = (prompt or "").lower()
    action = r"(?:add|put|save|schedule|book|create)"
    target = r"(?:this|it|the event|event|date|poster|that)"
    calendar = r"(?:my\s+)?(?:google\s+)?calendar"
    return bool(
        re.search(rf"\b{action}\b.{{0,50}}\b(?:{target}|{calendar})\b", text)
        or re.search(rf"\b{target}\b.{{0,50}}\b{action}\b.{{0,30}}\b{calendar}\b", text)
        or re.search(rf"\b{calendar}\b.{{0,40}}\b{action}\b", text)
    )


def local_timezone_name() -> str:
    """Return the system IANA timezone name when Python can identify it."""
    configured = os.getenv("TZ", "").strip()
    if configured:
        try:
            ZoneInfo(configured)
            return configured
        except (ValueError, KeyError):
            pass
    tz = datetime.now().astimezone().tzinfo
    name = getattr(tz, "key", None)
    if name:
        return name
    # macOS and Linux commonly expose the selected zone through /etc/localtime.
    try:
        target = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in target:
            candidate = target.split(marker, 1)[1]
            ZoneInfo(candidate)
            return candidate
    except (OSError, ValueError, KeyError):
        pass
    return "UTC"


def _image_mime(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "image/jpeg")


async def extract_event_from_image(image_path: Path, user_prompt: str) -> EventDetails:
    """Read event details without guessing missing dates or times."""
    if image_path.suffix.lower() not in _IMAGE_SUFFIXES or not image_path.is_file():
        raise ValueError("I couldn't find a readable event photo. Please send a clear photo.")

    today = date.today().isoformat()
    timezone = local_timezone_name()
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    user_message = UserMessage(content=[
        ContentPartTextParam(text=(
            f"User request: {user_prompt}\nToday is {today}. The user's local timezone is "
            f"{timezone}. Read the event details from the image. Use the printed year; "
            "if no year is printed, use the nearest upcoming occurrence of that month/day. "
            "Use 24-hour HH:MM times. Return null for any date or time that cannot be read. "
            "Do not invent event details."
        )),
        ContentPartImageParam(image_url=ImageURL(
            url=f"data:{_image_mime(image_path)};base64,{image_data}"
        )),
    ])
    system_message = SystemMessage(content=(
        "Read an event poster or event notice for a calendar entry. Extract only information "
        "that is visible or unambiguously stated. Distinguish the event date from publication, "
        "ticket-sale, and deadline dates. If the poster gives one date and one time, treat them "
        "as the event start. If it gives a date range or end time, include the end. Do not "
        "calculate a missing end time. Keep the title concise and preserve the event's location."
    ))
    result = await _ainvoke_with_retry(
        make_glasses_vision_llm(), [system_message, user_message], EventDetails
    )
    return result.completion


async def extract_event_from_text(user_prompt: str) -> EventDetails:
    """Turn a direct calendar request into event fields without opening a browser."""
    import re

    # Preserve explicit details from common typed requests exactly. The model can
    # still fill in less regular requests below, but should not shift a clearly
    # stated date or rewrite the user's title.
    month_match = re.search(
        r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|"
        r"august|aug|september|sep|sept|october|oct|november|nov|december|dec)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
        user_prompt,
        re.IGNORECASE,
    )
    title_match = re.search(r"\b(?:titled|called|named)\s+(.+?)\s*$", user_prompt, re.I)
    time_match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:-|–|—|to)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        user_prompt,
        re.IGNORECASE,
    )
    if month_match and title_match and time_match:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
            "dec": 12,
        }
        month_name = month_match.group(1).lower()[:4]
        month = months.get(month_name[:3], 9 if month_name == "sept" else None)
        year = int(month_match.group(3) or date.today().year)
        month_day = (month, int(month_match.group(2)))
        try:
            event_date = date(year, *month_day)
            if not month_match.group(3) and event_date < date.today():
                event_date = date(year + 1, *month_day)
        except ValueError:
            event_date = None

        def parse_clock(hour_text: str, minute_text: str | None, period: str) -> str | None:
            raw_hour = int(hour_text)
            minute = int(minute_text or 0)
            if not 1 <= raw_hour <= 12 or not 0 <= minute <= 59:
                return None
            hour = raw_hour % 12
            if period.lower() == "pm":
                hour += 12
            return f"{hour:02d}:{minute:02d}"

        if event_date:
            start_time = parse_clock(time_match.group(1), time_match.group(2), time_match.group(3))
            end_time = parse_clock(time_match.group(4), time_match.group(5), time_match.group(6))
            title = title_match.group(1).strip().strip("\"'“”‘’")
            if title and start_time and end_time and end_time > start_time:
                return EventDetails(
                    title=title,
                    start_date=event_date.isoformat(),
                    start_time=start_time,
                    end_date=event_date.isoformat(),
                    end_time=end_time,
                )

    today = date.today().isoformat()
    timezone = local_timezone_name()
    user_message = UserMessage(content=(
        f"User request: {user_prompt}\nToday is {today}. The user's local timezone is "
        f"{timezone}. Extract the requested calendar event. Interpret a month/day without "
        "a year as its nearest upcoming occurrence. Preserve the title as requested. Use "
        "24-hour HH:MM times. Return null for any date or time that is not specified. "
        "Do not invent event details."
    ))
    system_message = SystemMessage(content=(
        "Convert the user's explicit request to create a calendar event into structured "
        "event details. Keep the requested title, date, start time, and end time. Do not "
        "add an assumed location or description."
    ))
    result = await _ainvoke_with_retry(
        make_glasses_vision_llm(), [system_message, user_message], EventDetails
    )
    return result.completion


def connect_google_calendar(client_file: str) -> str:
    """Open Google's account consent in the browser and securely save the connection."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Calendar support is not installed. Install the Google API packages "
            "listed in the project setup instructions."
        ) from exc

    client_path = Path(client_file).expanduser()
    if not client_path.is_file():
        raise RuntimeError(
            "Choose a valid Google OAuth Desktop client JSON file before connecting."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_path), _CALENDAR_SCOPES, autogenerate_code_verifier=True
    )
    credentials = flow.run_local_server(port=0, open_browser=True)
    identity = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    email = identity.userinfo().get().execute().get("email")
    if not email:
        raise RuntimeError("Google did not return an account email. No account was saved.")
    calendar_storage.save_account(email, str(client_path.resolve()), credentials.to_json())
    return email


def disconnect_google_calendar() -> None:
    """Revoke Google consent, then remove this app's encrypted local token."""
    saved = calendar_storage.load_credentials_json()
    if saved:
        token = json.loads(saved).get("refresh_token")
        if token:
            request = UrlRequest(
                "https://oauth2.googleapis.com/revoke",
                data=urlencode({"token": token}).encode("ascii"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=8):
                    pass
            except HTTPError as exc:
                # Google returns 400 if the grant was already revoked/invalid.
                if exc.code != 400:
                    raise RuntimeError("Google could not revoke the saved Calendar connection.") from exc
            except URLError as exc:
                raise RuntimeError(
                    "Could not reach Google to revoke access. The saved connection was kept; try again."
                ) from exc
    calendar_storage.remove_account()


def _get_calendar_service():
    """Load the locally connected account, refreshing its token when needed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Calendar support is not installed. Install the Google API packages "
            "listed in the project setup instructions."
        ) from exc

    saved = calendar_storage.load_credentials_json()
    if not saved:
        raise RuntimeError("Connect a Google account from Settings before adding an event.")
    try:
        credentials = Credentials.from_authorized_user_info(
            json.loads(saved), scopes=_CALENDAR_SCOPES
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("The saved Google account needs to be connected again in Settings.") from exc

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise RuntimeError("Google authorization expired. Reconnect the account in Settings.") from exc
        calendar_storage.update_credentials_json(credentials.to_json())
    if not credentials.valid:
        raise RuntimeError("Reconnect the Google account from Settings before adding an event.")
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def add_event_to_primary_calendar(event: dict[str, Any]) -> str:
    """Insert a user-confirmed event into the primary calendar and return its link."""
    service = _get_calendar_service()
    created = service.events().insert(calendarId="primary", body=event).execute()
    return str(created.get("htmlLink", ""))

