# shared fakes so the agent can be tested offline: no browser, no api calls.
#
#   FakeLLM      scripted stand-in for a browser-use chat model
#   FakeSession  stand-in for BrowserSession (page summary + screenshot)
#   FakeTools    real browser-use action models, fake execution

import base64
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PIL import Image
from pydantic import BaseModel

from browser_use import ActionResult, Tools

import nodes
from state import _run_config


def png_b64(width: int = 200, height: int = 120, color=(255, 255, 255)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeLLM:
    # each response is a model instance, an exception to raise, or a callable
    # (messages, output_format) -> model. the last response repeats once the
    # script runs out.
    def __init__(self, *responses, model: str = "fake-model"):
        self.responses = list(responses)
        self.calls: list[tuple[list, type]] = []
        self.model = model

    async def ainvoke(self, messages, output_format=None):
        self.calls.append((messages, output_format))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(r) and not isinstance(r, BaseModel):
            r = r(messages, output_format)
        if isinstance(r, BaseException):
            raise r
        return SimpleNamespace(completion=r)


class ExplodingLLM:
    model = "unconfigured"

    async def ainvoke(self, messages, output_format=None):
        raise AssertionError("a test hit an LLM it didn't configure")


def message_text(msg) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    return "\n".join(getattr(p, "text", "") for p in content)


def has_image(msg) -> bool:
    return not isinstance(msg.content, str) and any(
        getattr(p, "type", "") == "image_url" for p in msg.content
    )


class FakeSession:
    def __init__(self, title="Test page", url="https://example.com",
                 dom="[1]<button>Search />\n[2]<input placeholder=Search />",
                 selector_map=None):
        self.title, self.url, self.dom = title, url, dom
        self.selector_map = selector_map or {}
        self.screenshot = png_b64()
        self.summary_calls: list[dict] = []

    async def get_browser_state_summary(self, include_screenshot=True, cached=False,
                                        include_recent_events=False):
        self.summary_calls.append({"include_screenshot": include_screenshot, "cached": cached})
        return SimpleNamespace(
            title=self.title,
            url=self.url,
            screenshot=self.screenshot if include_screenshot else None,
            dom_state=SimpleNamespace(llm_representation=lambda: self.dom,
                                      selector_map=self.selector_map),
        )

    async def get_or_create_cdp_session(self, target_id=None, focus=True):
        return None


class FakeTools:
    _registry = None

    def __init__(self):
        if FakeTools._registry is None:
            FakeTools._registry = Tools().registry
        self.registry = FakeTools._registry
        self.acted: list[dict] = []

    async def act(self, action, browser_session, file_system=None, page_extraction_llm=None, **_):
        dump = action.model_dump(exclude_unset=True)
        self.acted.append(dump)
        name, params = next(iter(dump.items()))
        if name == "done":
            return ActionResult(is_done=True, success=params.get("success", True),
                                extracted_content=params.get("text"))
        if name == "extract":
            return ActionResult(extracted_content=f"extracted: {params.get('query')}")
        return ActionResult()


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def tools():
    return FakeTools()


@pytest.fixture
def config(session, tools):
    return _run_config(session, tools, None, "test-thread")


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    # any LLM a test doesn't explicitly configure fails loudly instead of
    # silently calling a paid api
    monkeypatch.setattr(nodes, "make_planner_llm", lambda: ExplodingLLM())
    monkeypatch.setattr(nodes, "make_vision_llm", lambda: None)
    monkeypatch.setattr(nodes, "USE_VISION", False)
    monkeypatch.setattr(nodes, "make_extraction_llm", lambda: None)


@pytest.fixture
def use_llms(monkeypatch):
    # use_llms(planner=FakeLLM(...), vision=FakeLLM(...)); vision=None means
    # USE_VISION=false
    def _set(planner=None, vision=None):
        monkeypatch.setattr(nodes, "make_planner_llm", lambda: planner or ExplodingLLM())
        monkeypatch.setattr(nodes, "make_vision_llm", lambda: vision)
        monkeypatch.setattr(nodes, "USE_VISION", vision is not None)
    return _set
