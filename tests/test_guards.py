# the safety nets that keep a confused model from looping, wasting steps, or
# claiming success it didn't earn — and the fast paths for price lookups.

from langgraph.graph import END

import nodes
from nodes import PlanDecision, Verdict
from state import MAX_STEPS
from browser_use import ActionResult
from conftest import FakeLLM


def _d(**kw):
    return PlanDecision(reasoning="r", **kw)


# ── analyse: what gets verified ──────────────────────────────────────────────

async def test_safe_actions_skip_verification(config):
    # ExplodingLLM is installed by default: any LLM call would fail the test
    for action in (_d(action="navigate", url="https://www.google.com"),
                   _d(action="scroll"), _d(action="input", index=1, text="x"),
                   _d(action="extract", text="price")):
        cmd = await nodes.node_analyse({"query": "q", "last_decision": action}, config)
        assert cmd.goto == "execute"


async def test_done_with_price_skips_verifier(config):
    cmd = await nodes.node_analyse(
        {"query": "how much is this", "last_decision": _d(action="done", text="$9.99")}, config)
    assert cmd.goto == "execute"


# ── analyse: rejection loop guard ────────────────────────────────────────────

REJ = "(rejected: click idx=1 url=None)"


async def test_repeated_rejection_without_progress_stops_honestly(config):
    state = {"query": "add to cart", "last_decision": _d(action="click", index=1),
             "scratch": [{"action": REJ, "outcome": "gate rejected: overlay"}]}

    cmd = await nodes.node_analyse(state, config)

    stop = cmd.update["last_decision"]
    assert cmd.goto == "execute"
    assert stop.action == "done" and stop.success is False
    assert "overlay" in stop.text


async def test_repeated_rejection_after_progress_is_reverified(use_llms, config):
    vision = FakeLLM(Verdict(approved=True, reason="now fine"))
    use_llms(vision=vision)
    state = {"query": "add to cart", "last_decision": _d(action="click", index=1),
             "scratch": [{"action": REJ, "outcome": "gate rejected: overlay"},
                         {"action": {"click": {"index": 9}}, "outcome": "closed the popup"}]}

    cmd = await nodes.node_analyse(state, config)

    assert cmd.goto == "execute" and "last_decision" not in (cmd.update or {})
    assert len(vision.calls) == 1


async def test_stuck_loop_returns_price_already_found(config):
    state = {"query": "how much is a spoon", "last_decision": _d(action="click", index=1),
             "scratch": [{"action": {"extract": {"query": "price"}}, "outcome": "Spoon $3.49"},
                         {"action": REJ, "outcome": "gate rejected"}]}
    cmd = await nodes.node_analyse(state, config)
    assert cmd.update["last_decision"].text == "$3.49"


# ── execute ──────────────────────────────────────────────────────────────────

async def test_execute_runs_action_and_logs_it(config, tools):
    state = {"query": "q", "last_decision": _d(action="navigate", url="https://www.google.com")}
    out = await nodes.node_execute(state, config)
    assert tools.acted == [{"navigate": {"url": "https://www.google.com", "new_tab": False}}]
    assert out["scratch"][-1]["action"]["navigate"]["url"] == "https://www.google.com"
    assert out["done"] is False


async def test_third_identical_action_is_stopped(config, tools):
    nav = {"navigate": {"url": "https://www.google.com", "new_tab": False}}
    state = {"query": "q", "last_decision": _d(action="navigate", url="https://www.google.com"),
             "scratch": [{"action": nav, "outcome": "x"}, {"action": nav, "outcome": "x"}]}

    out = await nodes.node_execute(state, config)

    assert list(tools.acted[-1]) == ["done"]
    assert tools.acted[-1]["done"]["success"] is False
    assert out["done"] is True


async def test_repeated_scrolling_is_allowed(config, tools):
    scroll = {"scroll": {"down": True}}
    state = {"query": "q", "last_decision": _d(action="scroll"),
             "scratch": [{"action": scroll, "outcome": "x"}] * 3}
    await nodes.node_execute(state, config)
    assert list(tools.acted[-1]) == ["scroll"]


async def test_price_task_does_not_extract_twice(config, tools):
    state = {"query": "how much is a spoon", "last_decision": _d(action="extract", text="price"),
             "scratch": [{"action": {"extract": {"query": "price"}}, "outcome": "Spoon $2.00"}]}
    out = await nodes.node_execute(state, config)
    assert tools.acted[-1]["done"]["text"] == "$2.00"
    assert out["done"] is True


# ── resolve ──────────────────────────────────────────────────────────────────

async def test_resolve_finishes_and_resets_for_next_turn():
    state = {"query": "open google", "done": True, "step": 3,
             "scratch": [{"action": {}, "outcome": ""}],
             "last_result": ActionResult(is_done=True, extracted_content="Opened Google.")}
    cmd = await nodes.node_resolve(state)
    assert cmd.goto == END
    assert cmd.update == {"messages": [{"role": "assistant", "content": "Opened Google."}],
                          "step": 0, "done": False, "scratch": []}


async def test_resolve_stops_at_max_steps():
    cmd = await nodes.node_resolve({"query": "q", "step": MAX_STEPS, "last_result": ActionResult()})
    assert cmd.goto == END
    assert "MAX_STEPS" in cmd.update["messages"][0]["content"]


async def test_resolve_finishes_price_task_on_first_price():
    state = {"query": "how much is it", "step": 2,
             "last_result": ActionResult(extracted_content="It is $7.25 on sale")}
    cmd = await nodes.node_resolve(state)
    assert cmd.goto == END
    assert cmd.update["messages"][0]["content"] == "$7.25"


async def test_resolve_keeps_going_when_not_done():
    cmd = await nodes.node_resolve({"query": "open google", "step": 1, "last_result": ActionResult()})
    assert cmd.goto == "plan"
