# how the agent sees the page and who checks its steps, by version: the
# PERCEPTION setting (dom | hybrid | vision), the front desk, the task's plan,
# the verifier, and the user's approval before a step runs.

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from browser_use.dom.views import DOMRect
from langgraph.graph import END

import nodes
from nodes import PlanDecision, RespondDecision, TaskPlan, Verdict, VisionPlanDecision
from conftest import FakeLLM, FakeSession, has_image, message_text


def _state(**kw):
    return {"query": "find spoons", "messages": [{"role": "user", "content": "find spoons"}], **kw}


def _box(x=20, y=20, w=80, h=40, text="Search", **attributes):
    # an element as the selector map holds it: enough for the numbered
    # screenshot and for the approval step's label
    return SimpleNamespace(absolute_position=DOMRect(x=x, y=y, width=w, height=h),
                           tag_name="button", attributes=attributes, backend_node_id=42,
                           get_meaningful_text_for_llm=lambda: text)


def _plan_call(llm):
    # the planner call's user message (llm.calls[i] = (messages, schema))
    return next(m[1] for m, fmt in llm.calls if fmt in (PlanDecision, VisionPlanDecision))


# ── the numbered screenshot ──────────────────────────────────────────────────

async def test_screenshots_draw_a_box_on_each_element():
    s = FakeSession(selector_map={1: _box()})
    clean, numbered = await nodes._screenshots(await s.get_browser_state_summary())

    img = Image.open(io.BytesIO(base64.b64decode(numbered))).convert("RGB")
    plain = Image.open(io.BytesIO(base64.b64decode(clean))).convert("RGB")
    assert img.size == plain.size
    # the box lands on the element; the far corner of the page is untouched
    assert any(img.getpixel((x, 20)) != (255, 255, 255) for x in range(20, 100))
    assert img.getpixel((190, 110)) == (255, 255, 255)


async def test_screenshots_without_a_screenshot():
    s = FakeSession()
    summary = await s.get_browser_state_summary(include_screenshot=False)
    assert await nodes._screenshots(summary) == (None, None)


# ── plan, by PERCEPTION ──────────────────────────────────────────────────────

async def test_dom_planner_reads_the_element_list_only(use_llms, config):
    planner = FakeLLM(PlanDecision(reasoning="r", action="click", index=1))
    use_llms(planner=planner)

    out = await nodes.node_plan(_state(), config)

    assert out["last_decision"].action == "click" and out["step"] == 1
    user = _plan_call(planner)
    assert not has_image(user)
    assert "find spoons" in message_text(user) and "[1]<button" in message_text(user)


async def test_hybrid_planner_gets_the_screenshot_and_the_list(use_llms, perception, session, config):
    perception("hybrid")
    session.selector_map = {1: _box()}
    planner = FakeLLM(PlanDecision(reasoning="r", action="click", index=1))
    use_llms(planner=planner)

    await nodes.node_plan(_state(), config)

    user = _plan_call(planner)
    assert has_image(user) and "[1]<button" in message_text(user)


async def test_vision_planner_gets_the_screenshot_and_keeps_notes(use_llms, perception, session, config):
    perception("vision")
    session.selector_map = {1: _box()}
    planner = FakeLLM(VisionPlanDecision(reasoning="r", action="scroll", notes="Spoon A is $3"))
    use_llms(planner=planner)

    out = await nodes.node_plan(_state(notes="saw the search page"), config)

    user = _plan_call(planner)
    text = message_text(user)
    assert has_image(user) and "[1]<button" not in text
    assert "Your notes so far: saw the search page" in text
    assert out["notes"] == "Spoon A is $3"


async def test_plan_prompt_carries_history_progress_and_plan(use_llms, config):
    planner = FakeLLM(PlanDecision(reasoning="r", action="scroll"))
    use_llms(planner=planner)
    state = _state(
        query="and blue?",
        messages=[{"role": "user", "content": "price of a red spoon"},
                  {"role": "assistant", "content": "$4.99"},
                  {"role": "user", "content": "and blue?"}],
        scratch=[{"action": {"navigate": {"url": "https://google.com"}}, "outcome": "now on google"}],
        plan=["Search for blue spoons", "Read the price"],
    )

    await nodes.node_plan(state, config)

    text = message_text(_plan_call(planner))
    assert "price of a red spoon" in text and "$4.99" in text, "follow-ups need prior turns"
    assert "now on google" in text, "planner must see actions already taken"
    assert "1. Search for blue spoons" in text, "planner must see the task's plan"
    assert text.count("and blue?") == 1, "current query is not duplicated into history"


async def test_done_answer_and_source_are_joined(use_llms, config):
    use_llms(planner=FakeLLM(PlanDecision(reasoning="r", action="done", answer="Yeovil Town",
                                          source="From the club's Wikipedia page.")))
    out = await nodes.node_plan(_state(), config)
    assert out["last_decision"].text == "Yeovil Town. From the club's Wikipedia page."


async def test_bot_check_stops_without_a_model_call(monkeypatch, config):
    async def cloudflare(session):
        return "cloudflare check"
    monkeypatch.setattr(nodes, "_bot_check", cloudflare)

    out = await nodes.node_plan(_state(), config)  # ExplodingLLM: no model call allowed

    d = out["last_decision"]
    assert d.action == "done" and d.success is False and d.text == nodes.BOT_CHECK_MESSAGE


# ── respond (the front desk) ─────────────────────────────────────────────────

async def test_respond_answers_from_screenshot(use_llms, config):
    vision = FakeLLM(RespondDecision(kind="answer", answer="A spoon shop.", reasoning="r"))
    use_llms(vision=vision)

    cmd = await nodes.node_respond(_state(query="what's on this page"), config)

    assert cmd.goto == END
    assert cmd.update["messages"] == [{"role": "assistant", "content": "A spoon shop."}]
    assert has_image(vision.calls[0][0][1])


async def test_respond_routes_tasks_to_make_plan(use_llms, config):
    use_llms(vision=FakeLLM(RespondDecision(kind="task", reasoning="r")))
    cmd = await nodes.node_respond(_state(), config)
    assert cmd.goto == "make_plan"


async def test_respond_falls_back_to_text_model(use_llms, config):
    planner = FakeLLM(RespondDecision(kind="task", reasoning="r"))
    use_llms(planner=planner, vision=FakeLLM(RuntimeError("boom")))
    cmd = await nodes.node_respond(_state(), config)
    assert cmd.goto == "make_plan"
    assert not has_image(planner.calls[0][0][1])


async def test_respond_without_vision_takes_no_screenshot(use_llms, session, config):
    use_llms(planner=FakeLLM(RespondDecision(kind="task", reasoning="r")))
    await nodes.node_respond(_state(), config)
    assert session.summary_calls[0]["include_screenshot"] is False


# ── make_plan ────────────────────────────────────────────────────────────────

async def test_make_plan_writes_the_tasks_plan(use_llms, config):
    use_llms(planner=FakeLLM(TaskPlan(steps=["Search Google", "Open the result"], summary="I'll search.")))
    out = await nodes.node_make_plan(_state(), config)
    assert out == {"plan": ["Search Google", "Open the result"], "plan_summary": "I'll search."}


async def test_price_task_skips_the_plan(config):
    out = await nodes.node_make_plan(_state(query="how much is a spoon"), config)  # no model call
    assert out == {"plan": [], "plan_summary": ""}


# ── analyse (the verifier) ───────────────────────────────────────────────────

def _click_state(**kw):
    return _state(last_decision=PlanDecision(reasoning="r", action="click", index=1), **kw)


async def test_dom_verdict_decides_over_a_vision_objection(use_llms, config):
    planner = FakeLLM(Verdict(approved=True, reason="[1] is the search button"))
    vision = FakeLLM(Verdict(approved=False, reason="a popup is showing"))
    use_llms(planner=planner, vision=vision)

    cmd = await nodes.node_analyse(_click_state(), config)

    assert cmd.goto == "approve"
    assert planner.calls[0][0][0] is nodes.VERIFIER_SYSTEM
    assert vision.calls[0][0][0] is nodes.VISION_VERIFIER_SYSTEM and has_image(vision.calls[0][0][1])


async def test_dom_rejection_goes_back_to_plan_with_both_reasons(use_llms, config):
    use_llms(planner=FakeLLM(Verdict(approved=False, reason="wrong element")),
             vision=FakeLLM(Verdict(approved=False, reason="a popup covers it")))

    cmd = await nodes.node_analyse(_click_state(), config)

    assert cmd.goto == "plan"
    outcome = cmd.update["scratch"][-1]["outcome"]
    assert "DOM: wrong element" in outcome and "VISION: a popup covers it" in outcome


async def test_dom_verifier_alone_checks_done(use_llms, config):
    vision = FakeLLM(Verdict(approved=False, reason="unused"))
    use_llms(planner=FakeLLM(Verdict(approved=True, reason="answered")), vision=vision)
    state = _state(last_decision=PlanDecision(reasoning="r", action="done", text="Spoons are in aisle 3."))

    cmd = await nodes.node_analyse(state, config)

    assert cmd.goto == "approve" and vision.calls == []


async def test_vision_only_verdict_decides(use_llms, perception, config):
    perception("vision")
    vision = FakeLLM(Verdict(approved=False, reason="box 1 is an ad"))
    use_llms(vision=vision)  # the text model must not be asked

    cmd = await nodes.node_analyse(_click_state(), config)

    assert cmd.goto == "plan"
    assert "VISION: box 1 is an ad" in cmd.update["scratch"][-1]["outcome"]


async def test_vision_only_lets_the_step_through_without_a_verdict(use_llms, perception, config):
    perception("vision")
    use_llms(vision=FakeLLM(RuntimeError("vision down")))
    cmd = await nodes.node_analyse(_click_state(), config)
    assert cmd.goto == "approve"


async def test_vision_only_has_no_extract(perception, config):
    perception("vision")
    state = _state(last_decision=VisionPlanDecision(reasoning="r", action="extract", text="price"))
    cmd = await nodes.node_analyse(state, config)
    assert cmd.goto == "plan"
    assert "extract isn't available" in cmd.update["scratch"][-1]["outcome"]


# ── approve (the user's say) ─────────────────────────────────────────────────

@pytest.fixture
def answers(monkeypatch):
    # answers(reply): the user's reply to every approval question; returns the
    # list of questions asked
    def _set(reply):
        asked = []

        def fake_interrupt(question):
            asked.append(question)
            return reply
        monkeypatch.setattr(nodes, "interrupt", fake_interrupt)
        return asked
    return _set


def _approval_config(config, policy):
    config["configurable"]["approval"] = policy
    return config


async def test_every_step_asks_and_yes_runs_it(answers, config):
    asked = answers({"decision": "yes"})
    cmd = await nodes.node_approve(_click_state(), _approval_config(config, "every_step"))
    assert cmd.goto == "execute"
    assert len(asked) == 1 and asked[0]["action"]["action"] == "click"


async def test_no_with_a_reason_goes_back_to_plan(answers, config):
    answers({"decision": "no", "reason": "use the other shop"})
    cmd = await nodes.node_approve(_click_state(), _approval_config(config, "every_step"))
    assert cmd.goto == "plan"
    assert cmd.update["scratch"][-1]["outcome"] == "user said no: use the other shop"


@pytest.mark.parametrize("reply,message", [
    ({"decision": "no"}, nodes.DENIED_MESSAGE),
    ({"decision": "stop"}, nodes.STOPPED_MESSAGE),
])
async def test_no_without_a_reason_or_stop_ends_the_task(answers, config, reply, message):
    answers(reply)
    cmd = await nodes.node_approve(_click_state(scratch=[{"action": {}, "outcome": ""}]),
                                   _approval_config(config, "every_step"))
    assert cmd.goto == END
    assert cmd.update["messages"][0]["content"] == message
    assert cmd.update["scratch"] == [] and cmd.update["status"] == "stopped"


@pytest.mark.parametrize("policy", ["auto", "every_step"])
async def test_done_and_auto_never_ask(answers, config, policy):
    asked = answers({"decision": "stop"})
    state = _state(last_decision=PlanDecision(reasoning="r", action="done", text="ok"))
    cmd = await nodes.node_approve(state, _approval_config(config, policy))
    assert cmd.goto == "execute" and asked == []
    if policy == "auto":
        cmd = await nodes.node_approve(_click_state(), _approval_config(config, "auto"))
        assert cmd.goto == "execute" and asked == []


@pytest.mark.parametrize("label,asks", [("Add to Cart", True), ("Next page", False)])
async def test_risky_policy_asks_only_before_committing(answers, session, config, label, asks):
    asked = answers({"decision": "yes"})
    session.selector_map = {1: _box(text=label)}
    cmd = await nodes.node_approve(_click_state(), _approval_config(config, "risky"))
    assert cmd.goto == "execute"
    assert bool(asked) is asks


@pytest.mark.parametrize("url,asks", [("https://example.com/b", False), ("https://other.com", True)])
async def test_new_site_policy_asks_when_leaving_the_site(answers, config, url, asks):
    asked = answers({"decision": "yes"})
    state = _state(last_decision=PlanDecision(reasoning="r", action="navigate", url=url))
    await nodes.node_approve(state, _approval_config(config, "new_site"))
    assert bool(asked) is asks
