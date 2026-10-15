# wiring the nodes into the langgraph state machine.

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, RetryPolicy, TimeoutPolicy
# RetryPolicy's own default, but missing from langgraph.types' __all__
from langgraph.types import default_retry_on  # pyright: ignore[reportPrivateImportUsage]
from langgraph.errors import NodeError, NodeTimeoutError

from llm import LLMLimitError
from state import PipelineState, MAX_STEPS
import nodes  # reference nodes.node_* so tests can monkeypatch them


# ── error handling ───────────────────────────────────────────────────────────
# what the user is told for each failure status. plain language, said aloud to a
# BLV user, so: what happened, and whether anything more was done on the page.
# the technical detail goes to the console only
FAILURE_MESSAGES = {
    "timed_out": "Sorry, that was taking too long, so I stopped the task. "
                 "I didn't do anything more on the page.",
    "rate_limited": "Sorry, I've hit the usage limit for the AI service, so I "
                    "stopped the task. Please try again in a few minutes.",
    "llm_failed": "Sorry, something went wrong while I was working out what to "
                  "do, so I stopped the task. I didn't do anything more on the "
                  "page. You can try asking again.",
    "execute_failed": "Sorry, I couldn't carry out an action on the page, even "
                      "after trying a different way, so I stopped the task. The "
                      "last action may not have gone through.",
    "failed": "Sorry, something went wrong, so I stopped the task. You can try "
              "asking again.",
}


def _failure_status(error: NodeError) -> str:
    if isinstance(error.error, NodeTimeoutError):
        return "timed_out"
    if isinstance(error.error, LLMLimitError):
        return "rate_limited"
    if error.node == "execute":
        return "execute_failed"
    if error.node in ("respond", "plan", "analyse"):
        return "llm_failed"
    return "failed"


def _retry_on(exc: Exception) -> bool:
    # langgraph's default rules, minus timeouts: a hung call retried 3× freezes
    # the agent for minutes before the user hears anything
    return not isinstance(exc, NodeTimeoutError) and default_retry_on(exc)


async def default_error_handler(state: PipelineState, error: NodeError) -> Command:
    # runs once a node has failed for good (after its retries). execute failures
    # go back to the planner once, as the game plan says: it re-reads the page,
    # so it notices if the action went through anyway. everything else ends the
    # task: tell the user why, and reset the per-task state like resolve does so
    # the next prompt starts clean
    status = _failure_status(error)
    scratch = state.get("scratch", [])
    print("── error ────────────────────────────────")
    print(f"  failed:   {error.node} ({status})")
    print(f"  error:    {error.error!r}")

    if error.node == "execute":
        decision = state.get("last_decision")
        action = decision.model_dump(exclude_none=True, exclude={"reasoning"}) if decision else {}
        # retry via the planner only if the previous action didn't fail too, and
        # there are steps left (resolve, which enforces MAX_STEPS, gets skipped)
        if not (scratch and scratch[-1].get("failed")) and state.get("step", 0) < MAX_STEPS:
            what = "timed out" if status == "timed_out" else "failed"
            entry = {"action": action, "failed": True,
                     "outcome": f"error: this action {what} ({type(error.error).__name__}); "
                                f"check the page, it may or may not have gone through"}
            print("  next:     back to plan")
            return Command(goto="plan", update={"scratch": scratch + [entry]})
        status = "execute_failed"

    message = FAILURE_MESSAGES[status]
    print(f"  told user: {message}")
    return Command(goto=END, update={
        "messages": [{"role": "assistant", "content": message}],
        "status": status,
        "step": 0,
        "done": False,
        "scratch": [],
    })


# ── the actual agent loop (with chat memory) ─────────────────────────────────
'''
    the loop:

        START → respond →(task)→ plan → analyse →(approved)→ execute → resolve →(loop)→ plan
                   │                       │                                    →(done)→ END
                   └──(answer)──→ END       └────(rejected)────→ plan

    respond is the front desk: every turn it decides if the message is a task to
    run in the browser (→ plan) or a question it can answer directly (→ END).
    plan reads the page and picks an action, analyse independently verifies it
    (so a wrong or premature action never reaches the browser), execute does it,
    resolve loops back or finishes.

    it's also a chat that remembers: a checkpointer + a stable thread_id means
    re-invoking with the same thread_id reloads the prior state (incl. the
    messages transcript), so each new prompt continues the conversation instead
    of starting blank. the live browser + Tools live in config, not state,
    because a live session isn't serialisable and would crash the checkpointer.

    still to come: a SQLite checkpointer so memory survives restarts, and a Store
    (written by resolve) for cross-conversation long-term memory.
'''
def build_agent():
    g: StateGraph[PipelineState] = StateGraph(PipelineState)
    
    g.set_node_defaults(
        retry_policy=RetryPolicy(max_attempts=3, retry_on=_retry_on),
        # langgraph's type hint misses its own documented (state, error: NodeError) form
        error_handler=default_error_handler,  # pyright: ignore[reportArgumentType]
        timeout=TimeoutPolicy(run_timeout=30),
    )
    g.add_node("respond", nodes.node_respond)  # front desk; routes plan | END via Command
    g.add_node("plan", nodes.node_plan)
    g.add_node("analyse", nodes.node_analyse)  # verify before acting; routes execute | plan | resolve
    g.add_node("execute", nodes.node_execute, retry_policy=RetryPolicy(max_attempts=1))
    g.add_node("resolve", nodes.node_resolve)  # routes plan | END via Command
    g.add_edge(START, "respond")
    g.add_edge("plan", "analyse")
    g.add_edge("execute", "resolve")
    return g.compile(checkpointer=InMemorySaver())
