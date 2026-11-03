# wiring the nodes into the langgraph state machine.

from langgraph.graph import StateGraph, START
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import RetryPolicy

from state import PipelineState
import nodes  # reference nodes.node_* so tests can monkeypatch them

_RETRY = RetryPolicy(max_attempts=3)


def build_workflow():
    # same graph as build_agent(), with per-node retries. (TimeoutPolicy /
    # set_node_defaults are from a newer langgraph API — not in 1.x yet.)
    g: StateGraph[PipelineState] = StateGraph(PipelineState)
    g.add_node("respond", nodes.node_respond, retry_policy=_RETRY)
    g.add_node("plan", nodes.node_plan, retry_policy=_RETRY)
    g.add_node("analyse", nodes.node_analyse, retry_policy=_RETRY)
    g.add_node("execute", nodes.node_execute, retry_policy=_RETRY)
    g.add_node("resolve", nodes.node_resolve, retry_policy=_RETRY)
    g.add_edge(START, "respond")
    g.add_edge("plan", "analyse")
    g.add_edge("execute", "resolve")
    return g.compile(checkpointer=InMemorySaver())

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
    g.add_node("respond", nodes.node_respond)  # front desk; routes plan | END via Command
    g.add_node("plan", nodes.node_plan)
    g.add_node("analyse", nodes.node_analyse)  # verify before acting; routes execute | plan
    g.add_node("execute", nodes.node_execute)
    g.add_node("resolve", nodes.node_resolve)  # routes plan | END via Command
    g.add_edge(START, "respond")
    g.add_edge("plan", "analyse")
    g.add_edge("execute", "resolve")
    # InMemorySaver keeps state between prompts within one process run, keyed by
    # thread_id — that's what gives us conversation memory. state stays fully
    # serialisable because the resources live in config, not state.
    return g.compile(checkpointer=InMemorySaver())
