# the safety nets that keep a confused model from looping, wasting steps, or
# claiming success it didn't earn — and the fast paths for price lookups.

from langgraph.graph import END

import nodes
from nodes import PlanDecision, Verdict, STUCK_MESSAGE, UNCONFIRMED_NOTE
from state import MAX_STEPS
from browser_use import ActionResult
from conftest import FakeLLM


def _d(**kw):
    return PlanDecision(reasoning="r", **kw)


# ── analyse: what gets verified ──────────────────────────────────────────────
# an action that passes analyse goes to approve (the user's say), then execute

async def test_safe_actions_skip_verification(config):
    # ExplodingLLM is installed by default: any LLM call would fail the test
    for action in (_d(action="navigate", url="https://www.google.com"),
                   _d(action="scroll"), _d(action="input", index=1, text="x"),
                   _d(action="extract", text="price")):
        cmd = await nodes.node_analyse({"query": "q", "last_decision": action}, config)
        assert cmd.goto == "approve"


async def test_done_with_price_skips_verifier(config):
    cmd = await nodes.node_analyse(
        {"query": "how much is this", "last_decision": _d(action="done", text="$9.99")}, config)
    assert cmd.goto == "approve"


async def test_incomplete_action_is_sent_back_without_a_model_call(config):
    cmd = await nodes.node_analyse({"query": "q", "last_decision": _d(action="click")}, config)
    assert cmd.goto == "plan"
    assert "no `index`" in cmd.update["scratch"][-1]["outcome"]


async def test_done_without_answer_is_sent_back(config):
    cmd = await nodes.node_analyse({"query": "q", "last_decision": _d(action="done")}, config)
    assert cmd.goto == "plan"
    assert "gate rejected" in cmd.update["scratch"][-1]["outcome"]


async def test_scrolling_past_the_nudge_is_rejected(config):
    scroll = {"action": {"scroll": {"down": True}}, "outcome": "x"}
    state = {"query": "q", "last_decision": _d(action="scroll"), "scratch": [scroll] * nodes.SCROLL_NUDGE}
    cmd = await nodes.node_analyse(state, config)
    assert cmd.goto == "plan"
    assert "scrolls in a row" in cmd.update["scratch"][-1]["outcome"]


async def test_no_verifier_version_skips_the_model_checks(monkeypatch, config):
    monkeypatch.setattr(nodes, "VERIFIER", False)
    cmd = await nodes.node_analyse({"query": "q", "last_decision": _d(action="click", index=1)}, config)
    assert cmd.goto == "approve"


# ── analyse: rejection loop guard ────────────────────────────────────────────

REJ = "(rejected: click idx=1 url=None)"


async def test_repeated_rejection_without_progress_stops_honestly(config):
    state = {"query": "add to cart", "last_decision": _d(action="click", index=1),
             "scratch": [{"action": REJ, "outcome": "gate rejected: overlay"}]}

    cmd = await nodes.node_analyse(state, config)

    stop = cmd.update["last_decision"]
    assert cmd.goto == "approve"
    assert stop.action == "done" and stop.success is False
    assert stop.text == STUCK_MESSAGE


async def test_stuck_stop_keeps_a_rejected_answer(config):
    # the verifier turned down an answer earlier: the user gets it, flagged as
    # unchecked, rather than nothing
    state = {"query": "add to cart", "last_decision": _d(action="click", index=1),
             "scratch": [{"action": "(rejected: done answer=abc)", "outcome": "gate rejected: x",
                          "answer": "Free returns within 30 days."},
                         {"action": REJ, "outcome": "gate rejected: overlay"}]}

    stop = (await nodes.node_analyse(state, config)).update["last_decision"]

    assert stop.text == f"Free returns within 30 days. {UNCONFIRMED_NOTE}"


async def test_repeated_rejection_after_progress_is_reverified(use_llms, config):
    planner = FakeLLM(Verdict(approved=True, reason="now fine"))
    vision = FakeLLM(Verdict(approved=True, reason="no overlay"))
    use_llms(planner=planner, vision=vision)
    state = {"query": "add to cart", "last_decision": _d(action="click", index=1),
             "scratch": [{"action": REJ, "outcome": "gate rejected: overlay"},
                         {"action": {"click": {"index": 9}}, "outcome": "closed the popup"}]}

    cmd = await nodes.node_analyse(state, config)

    assert cmd.goto == "approve" and "last_decision" not in (cmd.update or {})
    assert len(planner.calls) == 1 and len(vision.calls) == 1


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
                          "step": 0, "done": False, "scratch": [], "notes": "", "status": "ok"}


async def test_resolve_marks_an_honest_give_up():
    state = {"query": "q", "done": True, "step": 2,
             "last_result": ActionResult(is_done=True, success=False, extracted_content="Couldn't find it.")}
    cmd = await nodes.node_resolve(state)
    assert cmd.update["status"] == "gave_up"


async def test_resolve_stops_at_max_steps():
    cmd = await nodes.node_resolve({"query": "q", "step": MAX_STEPS, "last_result": ActionResult()})
    assert cmd.goto == END
    assert f"within {MAX_STEPS} steps" in cmd.update["messages"][0]["content"]
    assert cmd.update["status"] == "max_steps"


async def test_resolve_finishes_price_task_on_first_price():
    state = {"query": "how much is it", "step": 2,
             "last_result": ActionResult(extracted_content="It is $7.25 on sale")}
    cmd = await nodes.node_resolve(state)
    assert cmd.goto == END
    assert cmd.update["messages"][0]["content"] == "$7.25"


async def test_resolve_keeps_going_when_not_done():
    cmd = await nodes.node_resolve({"query": "open google", "step": 1, "last_result": ActionResult()})
    assert cmd.goto == "plan"
