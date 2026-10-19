# the full graph (respond -> plan -> analyse -> execute -> resolve) with fake
# models and a fake browser: one conversation must remember earlier turns, each
# task must start with a clean step counter, and threads must not leak.

from graph import build_agent
from mvp import run_turn, run_turn_with_result
from nodes import PlanDecision, RespondDecision, Verdict
from state import MAX_STEPS, _run_config
from conftest import FakeLLM, FakeSession, FakeTools, message_text


class ScriptedVision(FakeLLM):
    # answers by schema: front desk says "task", verifier approves, planner
    # follows `plans` (one list per turn, consumed in order)
    def __init__(self, plans):
        super().__init__(self._answer)
        self.plans = list(plans)

    def _answer(self, messages, output_format):
        if output_format is RespondDecision:
            return RespondDecision(kind="task", reasoning="r")
        if output_format is Verdict:
            return Verdict(approved=True, reason="ok")
        return self.plans.pop(0)

    def planner_prompts(self):
        return [message_text(m[1]) for m, fmt in self.calls if fmt is PlanDecision]


def _plan(**kw):
    return PlanDecision(reasoning="r", **kw)


def _config(thread_id="chat"):
    return _run_config(FakeSession(), FakeTools(), None, thread_id)


async def test_task_runs_end_to_end(use_llms):
    vision = ScriptedVision([
        _plan(action="navigate", url="https://www.google.com/search?q=spoon+price"),
        _plan(action="done", text="The spoon is $4.99"),
    ])
    use_llms(vision=vision)
    workflow, config = build_agent(), _config()

    reply = await run_turn_with_result(workflow, "how much is a spoon", config)

    assert reply == "$4.99"
    assert [list(a)[0] for a in config["configurable"]["tools"].acted] == ["navigate", "done"]


async def test_follow_up_remembers_previous_turn(use_llms):
    vision = ScriptedVision([
        _plan(action="done", text="The red spoon is $4.99"),
        _plan(action="done", text="The blue spoon is $5.49"),
    ])
    use_llms(vision=vision)
    workflow, config = build_agent(), _config()

    first = await run_turn_with_result(workflow, "how much is the red spoon", config)
    second = await run_turn_with_result(workflow, "and the blue one, how much?", config)

    assert (first, second) == ("$4.99", "$5.49")
    follow_up_prompt = vision.planner_prompts()[-1]
    assert "how much is the red spoon" in follow_up_prompt
    assert "$4.99" in follow_up_prompt

    msgs = workflow.get_state(config).values["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


async def test_each_task_starts_with_clean_progress(use_llms):
    vision = ScriptedVision([
        _plan(action="navigate", url="https://www.google.com"),
        _plan(action="done", text="Opened Google."),
        _plan(action="done", text="Still here."),
    ])
    use_llms(vision=vision)
    workflow, config = build_agent(), _config()

    await run_turn(workflow, "open google", config)
    await run_turn(workflow, "are you there", config)

    second_task_prompt = vision.planner_prompts()[-1]
    assert "none yet" in second_task_prompt, "previous task's actions leaked into the new task"
    state = workflow.get_state(config).values
    assert state["step"] == 0 and state["scratch"] == []


async def test_threads_do_not_share_memory(use_llms):
    vision = ScriptedVision([
        _plan(action="done", text="secret answer one"),
        _plan(action="done", text="answer two"),
    ])
    use_llms(vision=vision)
    workflow = build_agent()

    await run_turn(workflow, "remember the word pineapple", _config("phone"))
    await run_turn(workflow, "what word did I say", _config("desktop"))

    assert "pineapple" not in vision.planner_prompts()[-1]


async def test_direct_answer_skips_the_browser(use_llms):
    vision = FakeLLM(RespondDecision(kind="answer", answer="It's a cooking site.", reasoning="r"))
    use_llms(vision=vision)
    workflow, config = build_agent(), _config()

    reply = await run_turn_with_result(workflow, "what's on this page", config)

    assert reply == "It's a cooking site."
    assert config["configurable"]["tools"].acted == []


async def test_runaway_task_is_capped_at_max_steps(use_llms):
    # scrolling is exempt from the repeat guard, so only MAX_STEPS can stop this
    vision = ScriptedVision([_plan(action="scroll")] * (MAX_STEPS + 5))
    use_llms(vision=vision)
    workflow, config = build_agent(), _config()

    reply = await run_turn_with_result(workflow, "scroll forever", config)

    assert "MAX_STEPS" in reply
    assert len(config["configurable"]["tools"].acted) == MAX_STEPS
