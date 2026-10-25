# the shared pipeline state + the runtime resources nodes and runners both need.

import asyncio
import operator
import os
import tempfile
import time
from typing import TypedDict, Annotated, Any, Required

from langchain_core.runnables import RunnableConfig
from browser_use import BrowserSession, Tools, ActionResult
from browser_use.filesystem.file_system import FileSystem

# safety cap so a confused planner can't loop forever. 15 matches WebVoyager's
# own iteration limit (--max_iter 15 in its run.sh), so evaluation runs compare
MAX_STEPS = 15

# browser-use reads a page almost at once: it waits 0.3 s at most, skips a tab
# still on about:blank, and its profile's page-load waits
# (minimum_wait_page_load_time etc.) are never read in 0.13.1. so after a click
# into an Amazon product page, or one that opens a new tab, the planner could
# read a blank page and waste a step (Amazon--16 and --12 on 16 Oct; Google
# Search--3, --7 and Amazon--36 on 24 Oct, all after a new tab). _wait_for_page
# waits for the page to finish loading first, up to PAGE_READY_TIMEOUT seconds;
# a page that never finishes (ads, long polling) is read anyway when time's up.
# a new tab gets ABOUT_BLANK_GRACE seconds to leave about:blank, since the start
# page can be about:blank too (the gui, mvp.py), and that shouldn't cost the
# full timeout
PAGE_READY_TIMEOUT = 6.0
ABOUT_BLANK_GRACE = 2.0
_PAGE_READY_JS = "() => location.protocol + ' ' + document.readyState"


class PipelineState(TypedDict, total=False):
    # the current user turn (latest natural language input). Required: every
    # entry point (mvp.py, serve.py, the gui) sends it with each turn
    query: Required[str]

    # chat memory
    messages: Annotated[list[dict], operator.add]

    # result of the most recently executed action (set by node_execute)
    last_result: ActionResult

    # the planner's structured decision 
    last_decision: Any
    # set once the planner emits a `done` action (or execution reports is_done)
    done: bool
    # how many plan/execute cycles we've done (guards against infinite loops)
    step: int
    # within-task memory: the actions already taken THIS task, so the planner
    # doesn't redo steps and oscillate (click link → navigate back → click again
    # forever)
    # different from `messages`, which is the cross-turn chat memory.
    scratch: list[dict]

    # how the last turn ended, for the UI / logs (placeholder set).
    # set by respond/resolve on a normal finish and by
    # graph.default_error_handler on a failure:
    #   ok | gave_up | max_steps | timed_out | rate_limited | llm_failed | execute_failed | failed
    # gave_up: the agent stopped itself without completing the task (done with
    # success=False, from the planner or a loop guard)
    status: str
    # the error behind a failure status, set by graph.default_error_handler, so
    # logs can tell an overloaded API from bad model output. not cleared on a
    # later success: read it only alongside a failure status
    last_error: str
    '''
    TODO:
    properties that update as it progresses based on current node plan
    - relevant perception information  (partially done: read live in node_plan)
    - adjusted query
    - current plan (to execute)        (done: `plan` field above)
    '''


def _resources(config: RunnableConfig | None) -> tuple[BrowserSession, Tools, FileSystem]:
    # grab the live browser + executor + filesystem out of config 
    cfg = (config or {}).get("configurable", {})
    return cfg["session"], cfg["tools"], cfg["file_system"]


async def _start_resources(**browser_options: Any) -> tuple[BrowserSession, Tools, FileSystem]:
    # spin up the per-run resources: a live browser (kept alive so follow-ups
    # reuse the page), the action executor, and a scratch filesystem 
    # BROWSER_PATH (optional, .env) pins the browser binary: browser_use misses
    # newer playwright chromium ("Google Chrome for Testing.app"), and a fixed
    # browser keeps evaluation runs comparable. unset = browser_use's own search
    # browser_options go straight to BrowserSession (evaluation fixes the window
    # size with them); the gui and mvp.py pass none and get browser_use's defaults
    browser_path = os.getenv("BROWSER_PATH")
    if browser_path:
        browser_options["executable_path"] = browser_path
    session = BrowserSession(keep_alive=True, **browser_options)
    await session.start()
    tools = Tools()
    file_system = FileSystem(tempfile.mkdtemp(prefix="mvp_fs_"))
    return session, tools, file_system


async def _page_state(session: BrowserSession) -> tuple[str, str]:
    page = await session.must_get_current_page()
    protocol, ready_state = (await page.evaluate(_PAGE_READY_JS)).split()
    return protocol, ready_state


async def _wait_for_page(session: BrowserSession) -> None:
    # wait until the current tab has finished loading (see PAGE_READY_TIMEOUT).
    # returns at once on a page that's already loaded, so most steps cost one
    # quick check. each check is itself cut off at the time left: a tab that
    # stops answering (Amazon--0 in the 24 Oct smoke run) would otherwise hold
    # the check, and the node, until the node's own 30 s timeout
    start = time.monotonic()
    while True:
        left = PAGE_READY_TIMEOUT - (time.monotonic() - start)
        if left <= 0:
            return
        try:
            protocol, ready_state = await asyncio.wait_for(_page_state(session), left)
            web = protocol in ("http:", "https:")
            if ready_state == "complete" and (web or time.monotonic() - start >= ABOUT_BLANK_GRACE):
                return
        except TimeoutError:
            return  # the tab isn't answering: read it as it is
        except Exception:
            pass  # mid-navigation, or the new tab isn't attached yet
        await asyncio.sleep(0.25)


# pages where a site asks for a person: a captcha, an "I am human" checkbox,
# Amazon's "Continue shopping" robot check, Cloudflare's "Verify you are human".
# the agent never gets past one itself (decided 24 Oct, suggestions #5): it
# stops and hands over. an invisible reCAPTCHA (a badge on an ordinary form) is
# left out, since it asks nothing of the user
_BOT_CHECK_JS = """() => {
  const host = location.host, url = location.href, path = location.pathname;
  const text = document.body ? document.body.innerText : '';
  if (host.includes('amazon.')) {
    if (path.startsWith('/checkpoint/') || (text.includes('To continue, please click the') && text.includes('Continue shopping'))) return 'amazon checkpoint';
    if (url.includes('validateCaptcha') || text.includes('Enter the characters you see below')) return 'amazon captcha';
  }
  if (host.includes('google.') && path.startsWith('/sorry')) return 'google captcha';
  if (document.querySelector('iframe[src*="challenges.cloudflare.com"]') || /^just a moment/i.test(document.title)) return 'cloudflare check';
  const widget = [...document.querySelectorAll('iframe[src*="recaptcha/api2/anchor"], iframe[src*="hcaptcha.com"]')]
    .find(f => !/size=invisible/.test(f.src) && f.offsetWidth > 0);
  return widget ? 'captcha' : '';
}"""
BOT_CHECK_TIMEOUT = 5.0


async def _bot_check(session: BrowserSession) -> str | None:
    # the kind of bot check the current page shows, or None. a page that can't
    # be read in time isn't evidence of one
    async def check() -> str:
        page = await session.must_get_current_page()
        return await page.evaluate(_BOT_CHECK_JS)
    try:
        return await asyncio.wait_for(check(), BOT_CHECK_TIMEOUT) or None
    except Exception:
        return None


def _run_config(session: BrowserSession, tools: Tools, file_system: FileSystem,
                thread_id: str) -> RunnableConfig:
    return {
        "configurable": {
            "session": session, "tools": tools,
            "file_system": file_system, "thread_id": thread_id,
        },
        # recursion_limit has to be bigger than the nodes-per-step so our own
        # MAX_STEPS guard (not langgraph's) is what stops the loop
        "recursion_limit": 5 * MAX_STEPS + 5,
    }
