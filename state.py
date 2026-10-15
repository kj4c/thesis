# the shared pipeline state + the runtime resources nodes and runners both need.

import operator
import os
import tempfile
from typing import TypedDict, Annotated, Any

from langchain_core.runnables import RunnableConfig
from browser_use import BrowserSession, Tools, ActionResult
from browser_use.filesystem.file_system import FileSystem

# safety cap so a confused planner can't loop forever
MAX_STEPS = 12


class PipelineState(TypedDict, total=False):
    # the current user turn (latest natural language input)
    query: str

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
    #   ok | max_steps | timed_out | rate_limited | llm_failed | execute_failed | failed
    status: str
    '''
    TODO:
    properties that update as it progresses based on current node plan
    - relevant perception information  (partially done: read live in node_plan)
    - adjusted query
    - current plan (to execute)        (done: `plan` field above)
    '''


def _resources(config: RunnableConfig) -> tuple[BrowserSession, Tools, FileSystem]:
    # grab the live browser + executor + filesystem out of config 
    cfg = (config or {}).get("configurable", {})
    return cfg["session"], cfg["tools"], cfg["file_system"]


async def _start_resources() -> tuple[BrowserSession, Tools, FileSystem]:
    # spin up the per-run resources: a live browser (kept alive so follow-ups
    # reuse the page), the action executor, and a scratch filesystem 
    # BROWSER_PATH (optional, .env) pins the browser binary: browser_use misses
    # newer playwright chromium ("Google Chrome for Testing.app"), and a fixed
    # browser keeps evaluation runs comparable. unset = browser_use's own search
    browser_path = os.getenv("BROWSER_PATH")
    session = BrowserSession(keep_alive=True, executable_path=browser_path) if browser_path \
        else BrowserSession(keep_alive=True)
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
