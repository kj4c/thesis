# the VLM is the primary browser model: it plans and verifies from a screenshot
# with numbered element boxes. the DOM text model must take over whenever vision
# is off or fails, so the agent never breaks because a vision call did.

import base64
import io
from types import SimpleNamespace

from PIL import Image
from browser_use.dom.views import DOMRect
from langgraph.graph import END

import nodes
from nodes import PlanDecision, RespondDecision, Verdict
from conftest import FakeLLM, FakeSession, has_image, message_text


def _state(**kw):
    return {"query": "find spoons", "messages": [{"role": "user", "content": "find spoons"}], **kw}


# ── numbered-box screenshot ─────────────────────────────────────────────────

async def test_marked_screenshot_draws_boxes_on_elements():
    box = SimpleNamespace(absolute_position=DOMRect(x=20, y=20, width=80, height=40),
                          tag_name="button", attributes={}, backend_node_id=42)
    s = FakeSession(selector_map={42: box})
    summary = await s.get_browser_state_summary()
    marked = await nodes._marked_screenshot(s, summary)

    img = Image.open(io.BytesIO(base64.b64decode(marked))).convert("RGB")
    plain = Image.open(io.BytesIO(base64.b64decode(s.screenshot))).convert("RGB")
    assert img.size == plain.size
    # the box outline lands on the element; far corner of the page is untouched
    assert any(img.getpixel((x, 20)) != (255, 255, 255) for x in range(20, 100))
    assert img.getpixel((190, 110)) == (255, 255, 255)


async def test_marked_screenshot_without_screenshot_is_none():
    s = FakeSession()
    summary = await s.get_browser_state_summary(include_screenshot=False)
    assert await nodes._marked_screenshot(s, summary) is None


# ── plan ─────────────────────────────────────────────────────────────────────

async def test_plan_uses_vision_model_with_screenshot(use_llms, session, config):
    vision = FakeLLM(PlanDecision(reasoning="r", action="click", index=1))
    use_llms(vision=vision)

    out = await nodes.node_plan(_state(), config)

    assert out["last_decision"].action == "click"
    assert out["step"] == 1
    system, user = vision.calls[0][0]
    assert system is nodes.VISION_PLANNER_SYSTEM
    assert has_image(user), "vision planner must receive the screenshot"
    text = message_text(user)
    assert "find spoons" in text and "[1]<button" in text, "DOM list still sent as reference"


async def test_plan_falls_back_to_dom_when_vision_fails(use_llms, config):
    vision = FakeLLM(RuntimeError("vision provider down"))
    planner = FakeLLM(PlanDecision(reasoning="r", action="scroll"))
    use_llms(planner=planner, vision=vision)

    out = await nodes.node_plan(_state(), config)

    assert out["last_decision"].action == "scroll"
    system, user = planner.calls[0][0]
    assert system is nodes.PLANNER_SYSTEM
    assert not has_image(user)


async def test_plan_dom_only_when_vision_off(use_llms, session, config):
    planner = FakeLLM(PlanDecision(reasoning="r", action="scroll"))
    use_llms(planner=planner, vision=None)

    await nodes.node_plan(_state(), config)

    assert session.summary_calls[0]["include_screenshot"] is False, "no screenshot cost when vision is off"
    assert len(planner.calls) == 1


async def test_plan_prompt_carries_chat_history_and_progress(use_llms, config):
    vision = FakeLLM(PlanDecision(reasoning="r", action="done", text="ok"))
    use_llms(vision=vision)
    state = _state(
        messages=[
            {"role": "user", "content": "price of a red spoon"},
            {"role": "assistant", "content": "$4.99"},
            {"role": "user", "content": "and blue?"},
        ],
        query="and blue?",
        scratch=[{"action": {"navigate": {"url": "https://www.google.com"}}, "outcome": "now on google"}],
    )

    await nodes.node_plan(state, config)

    text = message_text(vision.calls[0][0][1])
    assert "price of a red spoon" in text and "$4.99" in text, "follow-ups need prior turns"
    assert "now on google" in text, "planner must see actions already taken"
    assert text.count("and blue?") == 1, "current query is not duplicated into history"


# ── respond (front desk) ─────────────────────────────────────────────────────

async def test_respond_answers_from_screenshot(use_llms, config):
    vision = FakeLLM(RespondDecision(kind="answer", answer="A spoon shop.", reasoning="r"))
    use_llms(vision=vision)

    cmd = await nodes.node_respond(_state(query="what's on this page"), config)

    assert cmd.goto == END
    assert cmd.update["messages"] == [{"role": "assistant", "content": "A spoon shop."}]
    assert has_image(vision.calls[0][0][1])


async def test_respond_routes_tasks_to_plan(use_llms, config):
    use_llms(vision=FakeLLM(RespondDecision(kind="task", reasoning="r")))
    cmd = await nodes.node_respond(_state(), config)
    assert cmd.goto == "plan"


async def test_respond_falls_back_to_text_model(use_llms, config):
    planner = FakeLLM(RespondDecision(kind="task", reasoning="r"))
    use_llms(planner=planner, vision=FakeLLM(RuntimeError("boom")))
    cmd = await nodes.node_respond(_state(), config)
    assert cmd.goto == "plan"
    assert not has_image(planner.calls[0][0][1])


# ── analyse (verifier) ───────────────────────────────────────────────────────

def _click_state(**kw):
    return _state(last_decision=PlanDecision(reasoning="r", action="click", index=1), **kw)


async def test_vision_verdict_decides(use_llms, config):
    vision = FakeLLM(Verdict(approved=True, reason="box 1 is the search button"))
    planner = FakeLLM(Verdict(approved=False, reason="should never be asked"))
    use_llms(planner=planner, vision=vision)

    cmd = await nodes.node_analyse(_click_state(), config)

    assert cmd.goto == "execute"
    assert planner.calls == [], "DOM verifier must not run when vision answered"
    system, user = vision.calls[0][0]
    assert system is nodes.VISION_VERIFIER_SYSTEM and has_image(user)


async def test_vision_rejection_sends_back_to_plan_with_reason(use_llms, config):
    use_llms(vision=FakeLLM(Verdict(approved=False, reason="cookie banner covers the page")))

    cmd = await nodes.node_analyse(_click_state(), config)

    assert cmd.goto == "plan"
    entry = cmd.update["scratch"][-1]
    assert isinstance(entry["action"], str) and "rejected: click idx=1" in entry["action"]
    assert "cookie banner" in entry["outcome"]


async def test_dom_verifier_takes_over_when_vision_fails(use_llms, config):
    planner = FakeLLM(Verdict(approved=True, reason="fine"))
    use_llms(planner=planner, vision=FakeLLM(RuntimeError("vision 500")))

    cmd = await nodes.node_analyse(_click_state(), config)

    assert cmd.goto == "execute"
    assert planner.calls[0][0][0] is nodes.VERIFIER_SYSTEM


async def test_dom_verifier_when_vision_off(use_llms, config):
    planner = FakeLLM(Verdict(approved=False, reason="wrong element"))
    use_llms(planner=planner, vision=None)
    cmd = await nodes.node_analyse(_click_state(), config)
    assert cmd.goto == "plan"


async def test_both_verifiers_share_the_same_rules():
    assert nodes._VERIFY_RULES in nodes.VERIFIER_SYSTEM.content
    assert nodes._VERIFY_RULES in nodes.VISION_VERIFIER_SYSTEM.content
    assert nodes.PLANNER_SYSTEM.content in nodes.VISION_PLANNER_SYSTEM.content
