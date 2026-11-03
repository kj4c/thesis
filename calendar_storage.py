"""Local SQLite store for a connected Google account and its encrypted OAuth token."""

from __future__ import annotations

import os
import platform
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_KEYRING_SERVICE = "gui-agent-google-calendar"
_KEYRING_USER = "oauth-token-encryption-key"


def database_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "GUI Agent" / "calendar.sqlite3"


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS calendar_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email TEXT NOT NULL,
            client_file TEXT NOT NULL,
            encrypted_credentials BLOB NOT NULL,
            connected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.commit()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return connection


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _fernet(create: bool):
    try:
        import keyring
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "Secure account storage is unavailable. Install the keyring and "
            "cryptography packages from the project setup instructions."
        ) from exc

    try:
        backend = keyring.get_keyring()
        if getattr(backend, "priority", 0) <= 0:
            raise RuntimeError("No operating-system credential store is available.")
        key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if key is None and create:
            key = Fernet.generate_key().decode("ascii")
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, key)
            if keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER) != key:
                raise RuntimeError("Could not save the encryption key in the system credential store.")
        if key is None:
            return None
        return Fernet(key.encode("ascii"))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Could not access the operating-system credential store for Google Calendar."
        ) from exc


def save_account(email: str, client_file: str, credentials_json: str) -> None:
    cipher = _fernet(create=True)
    encrypted = cipher.encrypt(credentials_json.encode("utf-8"))
    with _database() as connection:
        connection.execute("""
            INSERT INTO calendar_account (id, email, client_file, encrypted_credentials)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                email = excluded.email,
                client_file = excluded.client_file,
                encrypted_credentials = excluded.encrypted_credentials,
                connected_at = CURRENT_TIMESTAMP
        """, (email, client_file, encrypted))


def connected_account() -> dict | None:
    with _database() as connection:
        row = connection.execute(
            "SELECT email, client_file FROM calendar_account WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return {"email": row[0], "client_file": row[1]}


def load_credentials_json() -> str | None:
    with _database() as connection:
        row = connection.execute(
            "SELECT encrypted_credentials FROM calendar_account WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    cipher = _fernet(create=False)
    if cipher is None:
        raise RuntimeError("The Google Calendar encryption key is missing from the system credential store.")
    try:
        return cipher.decrypt(bytes(row[0])).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("The saved Google Calendar connection could not be decrypted.") from exc


def update_credentials_json(credentials_json: str) -> None:
    cipher = _fernet(create=False)
    if cipher is None:
        raise RuntimeError("The Google Calendar encryption key is missing from the system credential store.")
    encrypted = cipher.encrypt(credentials_json.encode("utf-8"))
    with _database() as connection:
        connection.execute(
            "UPDATE calendar_account SET encrypted_credentials = ? WHERE id = 1",
            (encrypted,),
        )


def remove_account() -> None:
    with _database() as connection:
        connection.execute("DELETE FROM calendar_account WHERE id = 1")
    try:
        import keyring
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:
        pass
