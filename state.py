# the shared pipeline state + the runtime resources nodes and runners both need.

import operator
import os
import tempfile
from typing import TypedDict, Annotated, Any, Required

from langchain_core.runnables import RunnableConfig
from browser_use import BrowserSession, Tools, ActionResult
from browser_use.filesystem.file_system import FileSystem

# safety cap so a confused planner can't loop forever. 15 matches WebVoyager's
# own iteration limit (--max_iter 15 in its run.sh), so evaluation runs compare
MAX_STEPS = 15

# how long browser-use waits before reading a page, in seconds. its defaults
# (0.25 s minimum, 0.5 s for network idle) are too short for an Amazon product
# page: in the 16 Oct baseline the agent read two of them blank right after the
# click and wasted a step (suggestions #11). this slows every step a little
PAGE_LOAD_WAITS = {"minimum_wait_page_load_time": 1.0, "wait_for_network_idle_page_load_time": 2.0}


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
    # size with them); the gui and mvp.py pass none and get browser_use's
    # defaults, apart from the page-load waits, which every caller gets
    browser_path = os.getenv("BROWSER_PATH")
    if browser_path:
        browser_options["executable_path"] = browser_path
    for option, seconds in PAGE_LOAD_WAITS.items():
        browser_options.setdefault(option, seconds)
    session = BrowserSession(keep_alive=True, **browser_options)
    await session.start()
    tools = Tools()
    file_system = FileSystem(tempfile.mkdtemp(prefix="mvp_fs_"))
    return session, tools, file_system


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
