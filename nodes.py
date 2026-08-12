# the graph nodes — respond, plan, analyse, execute, resolve — plus the schemas
# and prompts they use. also keeps the original design stubs (superseded by the
# node_* functions below, but left in as the design sketch).

from typing import Literal, Any

from pydantic import BaseModel, Field
from langgraph.graph import END
from langgraph.types import Command
from langchain_core.runnables import RunnableConfig

from browser_use import Tools, ActionResult
from browser_use.llm.messages import (
    SystemMessage, UserMessage, ContentPartTextParam, ContentPartImageParam, ImageURL,
)

from llm import make_planner_llm, make_vision_llm, make_extraction_llm, _ainvoke_with_retry
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
        print(f"  respond → task")
        return Command(goto="plan")

    # a question / chat — answer it and go back to listening
    answer = decision.answer or "(no answer)"
    print(f"  respond → {answer[:120]}{'…' if len(answer) > 120 else ''}")
    return Command(goto=END, update={"messages": [{"role": "assistant", "content": answer}]})

# what the planner has to return: exactly one next action. kept deliberately
# small (6 actions) so the schema + prompt stay readable for the MVP; production
# would reuse browser-use's full AgentOutput schema
class PlanDecision(BaseModel):
    reasoning: str = Field(description="one sentence: why this action moves the task forward")
    action: Literal["navigate", "click", "input", "scroll", "extract", "done"]
    url: str | None = Field(default=None, description="for navigate: the URL to open")
    index: int | None = Field(default=None, description="for click/input: the [index] of the element")
    text: str | None = Field(default=None, description="for input: text to type; for extract: what info to read off the page; for done: the answer/result")
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
    "- extract: read the CURRENT page for a fact — set `text` to what you want "
    "(e.g. 'cheapest spoon price and product name'). Use this instead of scrolling "
    "around to hunt for information.\n"
    "- done: set `text` to the final answer/result for the user.\n\n"
    "SEARCHING THE WEB — Google is the default. To find or look something up, "
    "navigate DIRECTLY to a Google search URL in one step: "
    "https://www.google.com/search?q=<your+query+with+plus+signs>. Don't go to "
    "google.com and type — just navigate straight to the search URL.\n\n"
    "ANSWERING 'FIND / WHAT IS' QUERIES EFFICIENTLY: after the Google search loads, "
    "the answer is usually right there — in the AI Overview box or the shopping/"
    "result snippets. Use `extract` to read it, then `done`. Do NOT click through "
    "into individual shops or scroll repeatedly unless the answer truly isn't on "
    "the results page. (Clicking into a store is only needed to DO something there, "
    "like add to cart — not just to read a price.)\n\n"
    "Only navigate to a url the user gave you or a well-known site — NEVER invent a "
    "shop/product url. If a navigation FAILS (can't resolve), don't retry it — go "
    "to a Google search URL instead.\n\n"
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
    "and you should NOT go looking for one.\n\n"
    "IF YOUR LAST PROPOSAL WAS REJECTED (the actions list shows 'gate rejected: …'): "
    "do NOT repeat it. Read the reason and do something DIFFERENT — e.g. if it says "
    "the item is the wrong product (forks not spoons), scroll or search for the "
    "correct product and target THAT; if it says an overlay is blocking, dismiss "
    "the overlay. If after trying you genuinely cannot find what's needed on this "
    "page, choose done and honestly state what's missing — do NOT claim success."
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


# the VISION channel of the consensus gate: same Verdict schema, but it judges
# from the SCREENSHOT, not the DOM text — so it catches things the DOM misses,
# especially overlays/popups covering the page and whether a click visually lands
# on the intended element
VISION_VERIFIER_SYSTEM = SystemMessage(content=(
    "You are a VISION verifier for a browser agent. You are shown a SCREENSHOT of "
    "the current page and a proposed action. Judge ONLY from what you can SEE:\n"
    "- Does the action target the right VISIBLE element? (e.g. a click on the "
    "'Add to Cart' button really lands on that button, not something else.)\n"
    "- Is an OVERLAY covering the page — a cookie/consent/newsletter/region popup "
    "or modal? If so and the action isn't dismissing it, REJECT and say to dismiss "
    "the overlay first (clicks on elements underneath will fail).\n"
    "- For done: does the screen actually look consistent with the task being "
    "complete?\n"
    "Approve only if the action looks right given what's visible. Keep `reason` to "
    "one sentence describing what you see."
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
    if d.action == "extract" and d.text:
        return AM(extract={"query": d.text})
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

    print(f"  [{step}] plan → {decision.action}")
    return {"last_decision": decision, "step": step}

async def _verify_dom(decision: "PlanDecision", query: str, dom_text: str,
                      progress: str, title: str, url: str) -> Verdict:
    # channel 1: judge the action from the TEXT DOM (same model as the planner)
    verify_msg = UserMessage(content=(
        f"Task: {query}\n\n"
        f"{progress}"
        f"Proposed action: {decision.action} "
        f"(url={decision.url}, index={decision.index}, text={decision.text})\n"
        f"Planner's reasoning: {decision.reasoning}\n\n"
        f"Current page: {title} ({url})\n\n"
        f"Interactive elements:\n{dom_text}"
    ))
    r = await _ainvoke_with_retry(make_planner_llm(), [VERIFIER_SYSTEM, verify_msg], Verdict)
    return r.completion


async def _verify_vision(decision: "PlanDecision", query: str, progress: str,
                         screenshot: str | None) -> Verdict | None:
    # channel 2: judge the action from the SCREENSHOT (a vision model). returns
    # None if vision is off / no screenshot / the call fails — caller then falls
    # back to DOM-only so the agent never breaks just because vision is unavailable
    llm = make_vision_llm()
    if llm is None or not screenshot:
        return None
    try:
        parts = [
            ContentPartTextParam(text=(
                f"Task: {query}\n\n{progress}"
                f"Proposed action: {decision.action} "
                f"(index={decision.index}, url={decision.url}, text={decision.text})\n"
                f"Planner's reasoning: {decision.reasoning}\n\n"
                f"Judge from the screenshot below."
            )),
            ContentPartImageParam(image_url=ImageURL(url=f"data:image/png;base64,{screenshot}")),
        ]
        r = await _ainvoke_with_retry(llm, [VISION_VERIFIER_SYSTEM, UserMessage(content=parts)], Verdict)
        return r.completion
    except Exception as e:
        print(f"  (vision channel unavailable: {type(e).__name__}: {str(e)[:80]})")
        return None


async def node_analyse(state: PipelineState, config: RunnableConfig) -> Command[Literal["execute", "plan"]]:
    # verify before consequential actions (click, done). DOM is the decision;
    # vision notes overlay issues but does not block when DOM already approved —
    # otherwise a visible popup rejects a valid done answer and the agent loops.
    decision: PlanDecision = state["last_decision"]

    if decision.action not in ("click", "done"):
        return Command(goto="execute")

    rej_key = f"(rejected: {decision.action} idx={decision.index} url={decision.url})"

    # rejection loop guard: if this exact action was already rejected once, the
    # planner is ignoring the feedback and spinning (plan→analyse→plan…). the
    # execute-stage loop guard can't catch this because a rejected action never
    # reaches execute. stop cleanly instead of looping to MAX_STEPS.
    prior_rejections = [e for e in state.get("scratch", []) if e.get("action") == rej_key]
    if prior_rejections:
        last_reason = prior_rejections[-1].get("outcome", "the action was rejected repeatedly")
        print("  analyse → stop (same action rejected twice)")
        stop = PlanDecision(
            reasoning="stuck: the gate rejected the same action repeatedly",
            action="done",
            text=(f"couldn't complete this — {last_reason}. "
                  f"try rephrasing, or point me at a specific product/page."),
        )
        return Command(goto="execute", update={"last_decision": stop})

    session, _, _ = _resources(config)
    summary = await session.get_browser_state_summary(cached=True, include_screenshot=True)
    dom_text = summary.dom_state.llm_representation()[:6000]
    progress = _format_scratch(state.get("scratch", []))

    dom_v = await _verify_dom(decision, state["query"], dom_text, progress,
                              summary.title, summary.url)
    # skip vision on done — the answer lives in DOM/text; overlays don't invalidate it
    vis_v = None if decision.action == "done" else await _verify_vision(
        decision, state["query"], progress, summary.screenshot,
    )

    approved = dom_v.approved
    tag = "✓" if approved else "✗"
    note = dom_v.reason[:100]
    if vis_v and not vis_v.approved and approved:
        note += f" (vision: {vis_v.reason[:60]}…)"
    elif vis_v and not vis_v.approved and not approved:
        note = vis_v.reason[:100]
    print(f"  analyse [{tag}] {decision.action}: {note}")

    reason = dom_v.reason
    if vis_v:
        reason = f"DOM: {dom_v.reason} | VISION: {vis_v.reason}"

    if approved:
        return Command(goto="execute")

    entry = {"action": rej_key, "outcome": f"gate rejected: {reason}"}
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
        action_name = next(iter(action_dump), "")

    result: ActionResult = await tools.act(
        action, session, file_system=file_system, page_extraction_llm=make_extraction_llm()
    )
    # done when the planner emitted a `done` action (browser-use sets is_done)
    done = bool(getattr(result, "is_done", False))
    print(f"  exec → {action_name or action_dump}")
    if getattr(result, "error", None):
        print(f"  exec error: {result.error}")

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
        print(f"  done: {str(answer)[:120]}{'…' if len(str(answer)) > 120 else ''}")
        return Command(goto=END, update={
            "messages": [{"role": "assistant", "content": str(answer)}],
            "step": 0,
            "done": False,
            "scratch": [],  # clear the within-task memory for the next task
        })

    # not done — go plan the next step
    return Command(goto="plan")
