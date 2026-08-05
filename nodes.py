# the graph nodes — respond, plan, analyse, execute, resolve — plus the schemas
# and prompts they use. also keeps the original design stubs (superseded by the
# node_* functions below, but left in as the design sketch).

from typing import Literal, Any

from pydantic import BaseModel, Field
from langgraph.graph import END
from langgraph.types import Command
from langchain_core.runnables import RunnableConfig

from browser_use import Tools, ActionResult
from browser_use.llm.messages import SystemMessage, UserMessage

from llm import make_planner_llm, _ainvoke_with_retry
from state import PipelineState, MAX_STEPS, _resources

# ── Node 1: Conversational Node  ─────────────────────────────────────────────
'''
    This one is for questions like 'what is on this page' and 'where is the
    search bar'? May include an option to ask user if they want to switch to
    'executive mode' if it sounds like they want a specific task done.

    Potentially can be reused to judge what went wrong for a task after human
    approval is denied.
'''
async def respond(query: str) -> str:
    ... # TODO

def is_task(query: str) -> bool:
    # either LLM or action, depending on how we implement it
    ... # TODO

# ── Node 2: plan ──────────────────────────────────────────────---------------
'''
    As I see it, this is for collecting the vision-based coord mapping &
    text-only DOM for the next node by just hitting both AI with the OG query
    (and potentially the plan, or even information from the database to catch
    disruptions, e.g. popus, 404s). Its main point is to notice if there's a
    disruption and verify that the AI are mostly in agreement, or close to.

    Might split the node into one for VBC & one for DOM (AKA one for each AI).

    RE: plan/database as arguments, may be good to add for context, but we
    then have to figure the function overload, shape of the query & how to
    format either option.

    RE: verification, may create an extra node or cut it out and double down
    in next node instead of verifying within this node.

'''
async def plan(query: str) -> str:
    ... # TODO

# ── Node 3: analyse ──────────────────────────────────────────────────────────
'''
    Takes query and updated response to query (or specific step, still not
    clear on that detail), confirms (re-verifies) consensus and checks if
    approval is needed (based on user preference and verification result).

    Assume we choose 1 of the two AI for this node.
'''
async def analyse(query: str, perception_data: str) -> str:
    ... # TODO

def ask_approval(question: str) -> bool:
    # user input
    ... # TODO

# ── Node 4: execute ──────────────────────────────────────────────────────────
'''
    Self-explanatory, no AI, just browser control and a defined error class.

    Might just be a function instead, need to revisit what defines a node.
'''
async def execute(query: str, perception_data: str) -> str:
    ... # TODO

# ── Node 5: resolve ──────────────────────────────────────────────────────────
'''
    Primarily saving information to the database and training data. Might need
    AI to intuit what it should save? I don't know about that.

    Also, depending on how the edges work, this node may define if the
    graph goes to END now.
'''
async def resolve(query: str, success: str) -> str:
    ... # TODO

# ── LangGraph workflow ───────────────────────────────────────────────────────

# what node_respond returns: is this a task to run in the browser, or a question
# we can just answer directly? (the conversational front-desk decision.)
class RespondDecision(BaseModel):
    kind: Literal["task", "answer"] = Field(description="task = do something in the browser; answer = reply directly")
    answer: str | None = Field(default=None, description="when kind=answer: the reply to give the user")
    reasoning: str = Field(description="one sentence: why it's a task or an answer")


RESPOND_SYSTEM = SystemMessage(content=(
    "You are the front desk of a browser assistant. Look at the user's message "
    "(with the conversation so far and the current page) and decide:\n"
    "- kind='task' if they want you to DO something in the browser — search, "
    "navigate, click, buy, fill a form, add to cart, etc. Don't answer it "
    "yourself; the browser agent will handle it.\n"
    "- kind='answer' if it's something you can answer directly — a question about "
    "the current page ('what's on this page', 'where's the search bar'), or "
    "general chat. Put your reply in `answer`, using the current page and the "
    "conversation as context.\n"
    "If it sounds like an action, prefer task; if it sounds like a question, "
    "prefer answer."
))


async def node_respond(state: PipelineState, config: RunnableConfig) -> Command[Literal["plan", "__end__"]]:
    # the front desk: is this a task to run in the browser, or a question we can
    # just answer? tasks go to plan; answers we reply to and end the turn. this
    # is also the conversational mode (e.g. "what's on this page")
    session, _, _ = _resources(config)
    summary = await session.get_browser_state_summary(include_screenshot=False)
    dom_text = summary.dom_state.llm_representation()[:4000]

    history = _format_history(state.get("messages", [])[:-1])
    user_msg = UserMessage(content=(
        f"{history}"
        f"User: {state['query']}\n\n"
        f"Current page: {summary.title} ({summary.url})\n\n"
        f"Interactive elements:\n{dom_text}"
    ))

    llm = make_planner_llm()
    response = await _ainvoke_with_retry(llm, [RESPOND_SYSTEM, user_msg], RespondDecision)
    decision: RespondDecision = response.completion

    if decision.kind == "task":
        print(f"\nrespond: task → planning :: {decision.reasoning}")
        return Command(goto="plan")

    # a question / chat — answer it and go back to listening
    answer = decision.answer or "(no answer)"
    print(f"\nrespond: {answer}")
    return Command(goto=END, update={"messages": [{"role": "assistant", "content": answer}]})

# what the planner has to return: exactly one next action. kept deliberately
# small (5 actions) so the schema + prompt stay readable for the MVP; production
# would reuse browser-use's full AgentOutput schema
class PlanDecision(BaseModel):
    reasoning: str = Field(description="one sentence: why this action moves the task forward")
    action: Literal["navigate", "click", "input", "scroll", "done"]
    url: str | None = Field(default=None, description="for navigate: the URL to open")
    index: int | None = Field(default=None, description="for click/input: the [index] of the element")
    text: str | None = Field(default=None, description="for input: text to type; for done: the answer/result")
    down: bool | None = Field(default=None, description="for scroll: true=down, false=up")


PLANNER_SYSTEM = SystemMessage(content=(
    "You are the planner for a browser automation agent. Each turn you get the "
    "user's task and the current page's interactive elements, each tagged with a "
    "[index]. Choose the SINGLE next action that best progresses the task.\n\n"
    "You MUST fill the fields the chosen action requires — an action with a "
    "missing field is invalid and wastes a step:\n"
    "- navigate: set `url` to a FULL url. ONLY navigate to a url the user gave "
    "you or a well-known site (e.g. https://www.google.com, https://www.amazon.com). "
    "NEVER invent or guess a shop/product url — made-up domains fail to resolve.\n"
    "- click: set `index` to an element's [index] from the list below.\n"
    "- input: set `index` AND `text`.\n"
    "- scroll: set `down` (true=down, false=up).\n"
    "- done: set `text` to the final answer/result for the user.\n\n"
    "To FIND, buy, or look something up when you don't have a specific site: go to "
    "https://www.google.com, type the query into the search box, and submit — do "
    "NOT guess a product url. If a navigation FAILS (site unavailable / can't "
    "resolve), do not retry the same url — go to Google and search instead.\n\n"
    "POPUPS / OVERLAYS come FIRST. Cookie-consent, privacy, newsletter, region, or "
    "app-install banners sit on top of the page and block everything underneath — "
    "clicks on elements below them silently fail or time out. If the elements list "
    "has anything like 'Accept', 'Accept all', 'Reject', 'I agree', 'Continue', "
    "'Got it', 'Close', 'No thanks', or '×', DISMISS IT before doing anything else. "
    "Signs you're blocked by an overlay: your last click errored/timed out, or the "
    "button you want isn't clickable even though you can see it. In that case, look "
    "for a consent/close button and click that first.\n\n"
    "Only use an [index] that actually appears in the elements list. When the "
    "task is complete, or the answer is already visible, choose done and put the "
    "answer in `text`. Do not repeat an action that just ran.\n\n"
    "IMPORTANT — recognise completion from the actions-taken list. If it shows you "
    "already performed the action that completes the task (e.g. clicked 'Add to "
    "Cart' / 'Add to bag', submitted the form, logged in) and it did NOT error, "
    "the task is DONE — choose done. Do NOT re-click it, set a quantity, or take "
    "extra 'just to confirm' steps. A confirmation may not be visible on the page "
    "and you should NOT go looking for one."
))


# what the analyse (verifier) node returns: an independent check on the action
# the planner picked, before it runs. this is the seed of the consensus gate —
# one channel (DOM) for now, a vision channel slots in later.
class Verdict(BaseModel):
    approved: bool = Field(description="true only if the proposed action is correct AND grounded in the page")
    reason: str = Field(description="one sentence: why it's right, or what's wrong / what to do instead")


VERIFIER_SYSTEM = SystemMessage(content=(
    "You are an independent verifier for a browser automation agent. You did NOT "
    "choose the action. Judge ONLY whether the PROPOSED action is a reasonable "
    "step. Do NOT redesign the task, second-guess which item/target the planner "
    "picked, or propose a different item — assume the planner's target is correct "
    "unless the action is clearly ungrounded (e.g. an [index] that isn't the right "
    "element).\n\n"
    "Judge by action type:\n"
    "- INTERMEDIATE action (click/navigate/input/scroll — anything except done): "
    "APPROVE if it is a sensible next step toward the task, EVEN IF it does not by "
    "itself complete the task. Only REJECT if it is clearly wrong/ungrounded or "
    "REDUNDANT (it or an equivalent already appears in the actions-taken list). "
    "Never reject just because more steps remain.\n"
    "- `done`: decide by TASK TYPE.\n"
    "  • FIND / ANSWER task (e.g. 'find the cheapest spoon', 'what's the price'): "
    "APPROVE if the requested information is present on the current page or already "
    "stated in the done text. No click or committing action is required — reading "
    "the page IS the completion.\n"
    "  • DO / ACTION task (e.g. 'add to cart', 'submit', 'log in'): APPROVE if the "
    "actions-taken list already contains the completing action run WITHOUT error "
    "(e.g. an 'Add to bag' click). Do NOT require a visible confirmation — those are "
    "transient and often gone by now. Do NOT send the agent on a cart-verification "
    "detour.\n"
    "  REJECT done only if neither holds; then say what's still needed.\n\n"
    "When rejecting, give one concrete next step."
))


def _decision_to_action(tools: Tools, d: PlanDecision) -> Any:
    # turn the planner's PlanDecision into a real browser-use ActionModel. if a
    # required field is missing we fall back to `done` so a bad decision stops the
    # run cleanly instead of blowing up mid-loop.
    AM = tools.registry.create_action_model(include_actions=[d.action])
    if d.action == "navigate" and d.url:
        return AM(navigate={"url": d.url, "new_tab": False})
    if d.action == "click" and d.index is not None:
        return AM(click={"index": d.index})
    if d.action == "input" and d.index is not None and d.text is not None:
        return AM(input={"index": d.index, "text": d.text})
    if d.action == "scroll":
        return AM(scroll={"down": d.down if d.down is not None else True})
    if d.action == "done":
        return AM(done={"text": d.text or "done", "success": True})
    # malformed action (e.g. click with no index) -> stop gracefully
    stop = tools.registry.create_action_model(include_actions=["done"])
    return stop(done={"text": f"stopping: incomplete action {d.action!r}", "success": False})


def _format_history(messages: list[dict], limit: int = 6) -> str:
    # render recent conversation turns as text so the planner has chat context
    # (so a follow-up like 'now click the first result' makes sense)
    if not messages:
        return ""
    recent = messages[-limit:]
    lines = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
    return f"Conversation so far:\n{lines}\n\n"


def _format_scratch(scratch: list[dict], limit: int = 10) -> str:
    # render the actions already taken THIS task so the planner sees its own
    # progress and doesn't repeat itself / oscillate. this is the fix for the
    # click→navigate-back→click loop.
    if not scratch:
        return "Actions taken so far this task: (none yet — this is the first step)\n\n"
    recent = scratch[-limit:]
    lines = "\n".join(
        f"{i}. {e['action']} -> {e['outcome']}" for i, e in enumerate(recent, 1)
    )
    return (
        f"Actions you have ALREADY taken this task (do not repeat them; if these "
        f"show the task is complete, choose done):\n{lines}\n\n"
    )


async def node_plan(state: PipelineState, config: RunnableConfig) -> PipelineState:
    # read the page, ask the model for the next action. perception (DOM +
    # screenshot) comes from browser-use; the decision comes from our own llm
    # call, which is the whole point of owning the loop. we also feed in the
    # prior conversation so follow-up prompts have context.
    session, _, _ = _resources(config)  # planner only reads the page; execute acts
    step = state.get("step", 0) + 1

    summary = await session.get_browser_state_summary()
    dom_text = summary.dom_state.llm_representation()
    # cap the DOM text so a huge page doesn't blow up tokens/cost in the MVP
    dom_text = dom_text[:6000]

    history = _format_history(state.get("messages", [])[:-1])
    # actions taken THIS task — the within-task memory that stops oscillation
    progress = _format_scratch(state.get("scratch", []))
    user_msg = UserMessage(content=(
        f"{history}"
        f"Current request: {state['query']}\n\n"
        f"{progress}"
        f"Current page: {summary.title} ({summary.url})\n\n"
        f"Interactive elements:\n{dom_text}"
    ))

    llm = make_planner_llm()
    response = await _ainvoke_with_retry(llm, [PLANNER_SYSTEM, user_msg], PlanDecision)
    decision: PlanDecision = response.completion

    print(f"\nStep {step} — plan: {decision.action} :: {decision.reasoning}")
    return {"last_decision": decision, "step": step}

async def node_analyse(state: PipelineState, config: RunnableConfig) -> Command[Literal["execute", "plan"]]:
    # independently verify the planned action before it runs. this is the seed of
    # the per-step verification gate — one channel for now (a DOM-based verifier),
    # and once a vision channel is added it becomes the DOM-vs-vision consensus gate
    # approved -> execute; rejected -> back to plan with feedback, so a wrong
    # action (especially a premature `done`) never reaches the browser.
    decision: PlanDecision = state["last_decision"]

    # only bother verifying consequential actions — a click can submit/commit/
    # navigate, and done claims completion. navigate/scroll/input are safe and
    # reversible so we wave them through (skips an llm call, much faster).
    if decision.action not in ("click", "done"):
        return Command(goto="execute")

    # a stable key for this exact proposed action, so we can tell if the planner
    # keeps re-proposing something the verifier already rejected.
    rej_key = f"(rejected: {decision.action} idx={decision.index} url={decision.url})"

    # rejection loop guard: if this exact action was already rejected once, the
    # planner is ignoring the feedback and spinning (plan→analyse→plan…). the
    # execute-stage loop guard can't catch this because a rejected action never
    # reaches execute. stop cleanly instead of looping to MAX_STEPS.
    if any(e.get("action") == rej_key for e in state.get("scratch", [])):
        print("  analyse: ✗ same action rejected again — stopping to avoid a loop")
        stop = PlanDecision(
            reasoning="stuck: the verifier rejected the same action repeatedly",
            action="done",
            text=("couldn't finish — I kept trying to click an element that isn't "
                  "available. try rephrasing, or point me at a specific store/page."),
        )
        return Command(goto="execute", update={"last_decision": stop})

    session, _, _ = _resources(config)
    # reuse the page the planner just read (cached) — nothing has acted since
    summary = await session.get_browser_state_summary(cached=True, include_screenshot=False)
    # give the verifier the SAME DOM slice as the planner (6000). a smaller slice
    # made it falsely reject valid indices it simply couldn't see, which caused the
    # add-to-cart rejection loop.
    dom_text = summary.dom_state.llm_representation()[:6000]
    # actions already taken this task — lets the verifier catch redundant/finished work
    progress = _format_scratch(state.get("scratch", []))

    verify_msg = UserMessage(content=(
        f"Task: {state['query']}\n\n"
        f"{progress}"
        f"Proposed action: {decision.action} "
        f"(url={decision.url}, index={decision.index}, text={decision.text})\n"
        f"Planner's reasoning: {decision.reasoning}\n\n"
        f"Current page: {summary.title} ({summary.url})\n\n"
        f"Interactive elements:\n{dom_text}"
    ))

    llm = make_planner_llm()
    response = await _ainvoke_with_retry(llm, [VERIFIER_SYSTEM, verify_msg], Verdict)
    verdict: Verdict = response.completion

    if verdict.approved:
        print(f"  analyse: ✓ approved :: {verdict.reason}")
        return Command(goto="execute")

    # rejected — jot down why in the within-task log so the planner reconsiders,
    # then loop back to plan instead of executing a bad/premature action. the key
    # matches the loop guard above so a repeat of this exact action is caught.
    print(f"  analyse: ✗ REJECTED :: {verdict.reason}")
    entry = {"action": rej_key, "outcome": f"verifier rejected: {verdict.reason}"}
    return Command(goto="plan", update={"scratch": state.get("scratch", []) + [entry]})

async def node_execute(state: PipelineState, config: RunnableConfig) -> PipelineState:
    # run the action the planner chose. just browser-use's hands 
    session, tools, file_system = _resources(config)
    action = _decision_to_action(tools, state["last_decision"])

    # loop guard: if the planner picks the same action a 3rd time it's spinning
    # (navigate→click→navigate…), so force a clean stop. scroll is exempt since
    # repeating it to page through content is fine.
    action_dump = action.model_dump(exclude_unset=True)
    action_name = next(iter(action_dump), "")
    repeats = sum(1 for e in state.get("scratch", []) if e.get("action") == action_dump)
    if action_name != "scroll" and repeats >= 2:
        print(f"  ⚠ loop guard: '{action_name}' repeated {repeats + 1}×, stopping")
        action = _decision_to_action(tools, PlanDecision(
            reasoning="loop guard: same action repeated without progress",
            action="done",
            text="stopped: the agent kept repeating the same action without making progress",
        ))
        action_dump = action.model_dump(exclude_unset=True)

    # file_system is required by some actions (e.g. done, write_file)
    result: ActionResult = await tools.act(action, session, file_system=file_system)
    # done when the planner emitted a `done` action (browser-use sets is_done)
    done = bool(getattr(result, "is_done", False))
    print(f"  doing:  {action.model_dump(exclude_unset=True)}")
    if getattr(result, "error", None):
        print(f"  error:  {result.error}")

    # jot this action down in the within-task log so the next plan step sees it
    # read the page fresh so the outcome reflects where the action landed us
    after = await session.get_browser_state_summary(include_screenshot=False)
    outcome = result.error or result.extracted_content or f"now on {after.url}"
    entry = {"action": action_dump, "outcome": str(outcome)[:200]}
    scratch = state.get("scratch", []) + [entry]
    return {"last_result": result, "done": done, "scratch": scratch}

async def node_resolve(state: PipelineState) -> Command[Literal["plan", "__end__"]]:
    # save data + route. decide whether to loop back to plan or finish. on finish
    # we drop the outcome into chat memory and reset the per-task counters so the
    # next turn starts clean (state carries across turns via the checkpointer).
    # eventually this is also where durable long-term memory gets saved to a Store
    result = state.get("last_result")
    step = state.get("step", 0)
    finished = state.get("done", False) or step >= MAX_STEPS

    if finished:
        reason = "task done" if state.get("done") else f"hit MAX_STEPS ({MAX_STEPS})"
        answer = getattr(result, "extracted_content", None) or reason
        print("── resolve ──────────────────────────────")
        print(f"  finished: {reason}")
        print(f"  result:   {answer}")
        return Command(goto=END, update={
            "messages": [{"role": "assistant", "content": str(answer)}],
            "step": 0,
            "done": False,
            "scratch": [],  # clear the within-task memory for the next task
        })

    # not done — go plan the next step
    return Command(goto="plan")
