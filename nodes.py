# the graph nodes — respond, make_plan, plan, analyse, approve, execute,
# resolve — plus the schemas and prompts they use.

from typing import Literal, Any
import hashlib
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from langgraph.graph import END
from langgraph.types import Command, interrupt
from langchain_core.runnables import RunnableConfig

from browser_use import Tools, ActionResult
from browser_use.llm.messages import (
    SystemMessage, UserMessage, ContentPartTextParam, ContentPartImageParam, ImageURL,
)

from llm import make_planner_llm, make_vision_llm, make_extraction_llm, _ainvoke_with_retry, PRICE_TUNING
from state import PipelineState, MAX_STEPS, _resources, _wait_for_page, _bot_check
from reply_format import compact_glasses_reply, is_price_task, price_from_text


def _price_shortcuts(query: str) -> bool:
    # the price-only early exits; off when PRICE_TUNING=FALSE (see llm.py)
    return PRICE_TUNING and is_price_task(query)

# ── LangGraph workflow ───────────────────────────────────────────────────────

# what node_respond returns: is this a task to run in the browser, or a question
# we can just answer directly? (the conversational front-desk decision.)
class RespondDecision(BaseModel):
    kind: Literal["task", "answer"] = Field(description="task = do something in the browser; answer = reply directly")
    answer: str | None = Field(default=None, description="when kind=answer: the reply to give the user")
    reasoning: str = Field(description="one sentence: why it's a task or an answer")


# with thinking on (llm.py), the structured-output tool is offered, not forced,
# and the model sometimes replied in plain text instead: browser-use raised
# "Expected tool use in response but none found" and the step was retried
# (Google Search--18 step 4, 25 Oct smoke run: two failed calls, 45 s lost).
# so every prompt that expects a structured reply says so
_TOOL_REPLY_RULE = ("ALWAYS reply by calling the tool with your decision. Never reply "
                    "in plain text: anything for the user goes in the tool's fields.\n\n")

RESPOND_SYSTEM = SystemMessage(content=(
    _TOOL_REPLY_RULE +
    "You are the front desk of a browser assistant for smart glasses. The user "
    "hears your reply spoken aloud — keep it SHORT.\n"
    "- kind='task' if they want you to DO something in the browser: search, find a "
    "price, navigate, click, buy, etc. ALWAYS use task for price/cost/find/on Amazon "
    "requests — never try to answer those yourself from a stale page.\n"
    "- kind='answer' ONLY for quick factual questions already fully answered on the "
    "current page. Put the reply in `answer` — ONE short sentence max, no preamble.\n"
    "When in doubt, prefer task."
))


# what node_make_plan returns: the task's plan, written once before the first
# step. it's for the user, the report's "long explanation for plan creation":
# per-step approval gives the short one. kept small on purpose,
# since version A had few loops for a plan to fix
class TaskPlan(BaseModel):
    steps: list[str] = Field(description="2 to 5 short steps, in the order you'd do them, in plain words for a "
                             "user who may be blind, e.g. 'Search Google for the film'. No element numbers, URLs "
                             "or technical terms")
    summary: str = Field(description="one short sentence said aloud before the first step, e.g. 'I'll search "
                         "Google, then check the date on its Wikipedia page.'")

    @field_validator("steps", mode="before")
    @classmethod
    def _clean_steps(cls, v):
        # a model that writes the steps as one string still gets a list
        if isinstance(v, str):
            v = [line.strip(" -•\t") for line in v.splitlines()]
        return [str(s).strip() for s in (v or []) if str(s).strip()]


MAKE_PLAN_SYSTEM = SystemMessage(content=(
    _TOOL_REPLY_RULE +
    "You write the plan for a browser assistant's task, before it starts. The "
    "user may be blind and hears the plan read aloud, so it's short and plain.\n"
    "- `steps`: 2 to 5 steps, in order, each a few plain words about WHAT to do, "
    "not how: 'Search Google for the film', 'Open a source page about it', 'Read "
    "the release date'. No element numbers, URLs or technical terms.\n"
    "- To look something up, the assistant searches Google, then confirms the fact "
    "on a source page, since Google's own summaries can be wrong. Plan that way, "
    "but never write a site's name in a step unless the user named it, not even as "
    "an example ('such as …'): which source to open is decided from the search "
    "results.\n"
    "- Don't assume what the task asks for exists. Write a search or a condition as "
    "a check ('Check whether any has over 20,000 reviews'), not as if the answer is "
    "already known ('Open that one'): finding that nothing qualifies is a valid "
    "result.\n"
    "- Plan only from the request. Don't guess facts, prices or how a site is "
    "laid out: the assistant reads each page as it goes.\n"
    "- `summary`: one short sentence said aloud before the first step."
))


async def node_respond(state: PipelineState, config: RunnableConfig) -> Command[Literal["make_plan", "__end__"]]:
    # the front desk: is this a task to run in the browser, or a question we can
    # just answer? tasks go to make_plan; answers we reply to and end the turn. this
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
        return Command(goto="make_plan")

    # a question / chat — answer it and go back to listening
    answer = decision.answer or "(no answer)"
    print(f"\nrespond: {answer}")
    return Command(goto=END, update={  # pyright: ignore[reportReturnType]  (END is typed as str)
        "messages": [{"role": "assistant", "content": answer}],
        "status": "ok",
    })

# what Claude's structured output puts in an optional field it doesn't need
_PLACEHOLDERS = ("", "null", "none", "n/a", "na", "unknown", "<unknown>", "undefined")


# what the planner has to return: exactly one next action. kept deliberately
# small (6 actions) so the schema + prompt stay readable for the MVP; production
# would reuse browser-use's full AgentOutput schema
class PlanDecision(BaseModel):
    reasoning: str = Field(description="one sentence: why this action moves the task forward")
    # what the approval dialog and the GUI log show the user, instead of the
    # model-facing reasoning (suggestions #3)
    user_summary: str | None = Field(default=None, description="one short plain sentence for the user, who may be "
                                     "blind: what this step does and why, e.g. 'Opening the Wikipedia page to check "
                                     "the release date.' No element numbers, URLs or technical terms")
    action: Literal["navigate", "click", "input", "scroll", "extract", "done"]
    url: str | None = Field(default=None, description="for navigate: the URL to open")
    index: int | None = Field(default=None, description="for click/input: the [index] of the element")
    text: str | None = Field(default=None, description="for input: text to type; for extract: what info to read off the page"
                             + ("; for done: the answer — for prices use ONLY e.g. '$54.99'" if PRICE_TUNING else
                                ". NOT for done: the answer goes in `answer`"))
    # the answer shape in the schema, not just the prompt: with the prompt alone
    # answers stayed 3–5 sentences (24 Oct smoke run). the answer has its own
    # field: when `text` doubled as the answer, the model filled `source` and left
    # `text` empty (Google Search--19, 25 Oct smoke run). node_plan joins answer
    # and source into `text`, the spoken answer, so nothing downstream changes
    answer: str | None = Field(default=None, description="for done ONLY: the answer itself, in one sentence: exactly "
                               "what was asked. No source, caveats or how you searched: the source goes in `source`, "
                               "the rest nowhere. If you couldn't finish, what's missing and why")
    source: str | None = Field(default=None, description="for done ONLY: one sentence naming the site the answer "
                               "came from, and any doubt ('not confirmed on the product page', 'sources disagree', "
                               "'the reviews need a sign-in'). Not the answer itself, which goes in `answer`")
    down: bool | None = Field(default=None, description="for scroll: true=down, false=up")
    # replanning happens in this call, not a second make_plan call: the planner
    # already sees the failure or the user's reason when it picks the next step
    # (decided 30 Oct). node_plan writes it to state
    revised_plan: list[str] | None = Field(default=None, description="ONLY when the plan no longer fits, after a "
                                           "failed step, a rejected proposal or 'user said no': the new plan, "
                                           "the remaining steps in the same plain style. Otherwise leave it empty")
    success: bool = Field(default=True, description="for done ONLY: true if the task genuinely succeeded; false if you are stopping WITHOUT completing it (couldn't find it, gave up, blocked). Be honest — never claim success for a task you did not complete.")

    # Claude's structured output fills not-applicable optional fields with JUNK
    # placeholders ("", "<UNKNOWN>", "null", "N/A", …) instead of omitting them,
    # which crashes int/bool parsing. Coerce BY TYPE (not by enumerating sentinels,
    # which is whack-a-mole): anything that isn't a real int/bool becomes None.
    @field_validator("index", mode="before")
    @classmethod
    def _clean_index(cls, v):
        if isinstance(v, bool) or v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None  # "", "<UNKNOWN>", "null", etc.

    @field_validator("down", mode="before")
    @classmethod
    def _clean_down(cls, v):
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "down"):
            return True
        if s in ("false", "0", "no", "up"):
            return False
        return None  # anything else -> unset

    @field_validator("url", "text", "answer", "source", "user_summary", mode="before")
    @classmethod
    def _clean_optional_str(cls, v):
        if isinstance(v, str) and v.strip().lower() in _PLACEHOLDERS:
            return None
        return v

    # same junk problem for the list: anything that isn't a list of real steps
    # means no revision
    @field_validator("revised_plan", mode="before")
    @classmethod
    def _clean_revised_plan(cls, v):
        if not isinstance(v, list):
            return None
        steps = [s.strip() for s in v if isinstance(s, str) and s.strip().lower() not in _PLACEHOLDERS]
        return steps or None

    # success is a plain bool (not optional); junk/blank -> default True
    @field_validator("success", mode="before")
    @classmethod
    def _clean_success(cls, v):
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("false", "0", "no"):
            return False
        return True


# price rules for the glasses demo (PRICE_TUNING); without them the planner gets
# the original done rule
_PRICE_PLANNER_RULES = (
    "- done: set `text` to the final answer. For PRICE tasks: ONLY `$54.99` or "
    "`$54.99 (different brand)` if the listing isn't the same brand as the user's "
    "photo. One line max — no extra commentary.\n\n"
    "PRICE / FIND TASKS — be fast (3–4 steps max):\n"
    "- On search results: ONE extract for the first listed price, then done. STOP.\n"
    "- Do NOT click into product pages, re-extract, or scroll for price-only tasks.\n"
    "- If the first result is a different brand, still done with `$price (different brand)` "
    "— do NOT keep searching.\n"
    "- If actions-taken already shows an extract with a $ price, choose done immediately.\n\n"
) if PRICE_TUNING else (
    # the answer shape (decided 24 Oct, suggestions #11 "Wordiness"): answers
    # are read aloud, and the extras made the median answer 50 words, the
    # longest about a minute in VoiceOver. an ambiguous question is settled by its
    # own words before "most common": with "a club season or a calendar year" as
    # the example, Google Search--31 ("in a single season") led with the calendar
    # year in version A, and put both meanings in `answer` (suggestions #11)
    "- done: the answer is read aloud by a screen reader, so it has two parts, one "
    "sentence each:\n"
    "  1. `answer` = THE ANSWER: exactly what was asked, one result, no alternatives. "
    "If the task asks for several things (a top 5, a date AND a place, two scores), "
    "give all of them, and nothing more. If the question could mean more than one "
    "thing, its own words decide first: a word that narrows it ('season', 'in the "
    "US', 'first', 'regular season') picks that meaning, even if another is more "
    "common. Only if its words don't settle it, answer the most common meaning (for "
    "a film's release date, the main release, not a premiere). `answer` gives that "
    "one meaning only; name the other in `source`, never in `answer`.\n"
    "  2. `source` = SOURCE AND CONFIDENCE: which site it came from, and any doubt "
    "('not confirmed on the product page', 'sources disagree', 'the reviews need a "
    "sign-in').\n"
    "  Nothing else: alternatives, extra facts, caveats and how you searched are "
    "only for when the user asks for more. If you couldn't finish, `answer` says what's "
    "missing and why, in one sentence. `answer` is never left empty.\n\n"
)

# how the planner answers a FIND / WHAT IS query from Google. the glasses demo
# (PRICE_TUNING) reads the results page and stops, for speed. otherwise Google's
# own summaries are only a hint: the AI Overview is another model's unsourced
# text, and a result snippet is a cut-off piece of a page, and the 16 Oct
# baseline passed on mistakes from both (suggestions #11, question 11). a BLV
# user can't easily check them, so the answer is confirmed on a source page
_ANSWERING_RULES = (
    "ANSWERING 'FIND / WHAT IS' QUERIES EFFICIENTLY: after the Google search loads, "
    "the answer is usually right there — in the AI Overview box or the shopping/"
    "result snippets. Use `extract` to read it, then `done`. Do NOT click through "
    "into individual shops or scroll repeatedly unless the answer truly isn't on "
    "the results page. (Clicking into a store is only needed to DO something there, "
    "like add to cart — not just to read a price.)\n\n"
) if PRICE_TUNING else (
    "ANSWERING 'FIND / WHAT IS' QUERIES: Google's own summaries are HINTS, not "
    "answers. The AI Overview is written by another AI without sources and can be "
    "wrong; a result snippet is a cut-off excerpt of a page and can be incomplete "
    "or out of context. Before choosing done on a fact from either, CHECK it "
    "against a real source: click (or navigate to) the result that should contain "
    "it — an official site, Wikipedia, or a reputable source — and `extract` the "
    "fact from that page. One source page that confirms the fact is enough. A source "
    "chip, citation or preview card inside Google's answer is NOT the source page: "
    "clicking one may only open a preview on Google. Before naming a site as your "
    "source, check the current page (and the 'now on' site after your click) is "
    "that site, not google.com; if you're still on Google, open the result itself. If the "
    "source page disagrees with the Overview or snippet, trust the source page and "
    "say they disagree in `source`. If no source page can be opened, you may answer "
    "from the summary, but say so in `source` (e.g. 'from Google's AI Overview, not "
    "confirmed on a source page'). Always name the site the answer came from, in `source`. "
    "Never fill gaps from your own knowledge: every fact in the answer must come "
    "from a page you read.\n\n"
)

PLANNER_SYSTEM = SystemMessage(content=(
    "You are the planner for a browser automation agent. Each turn you get the "
    "user's task and the current page's interactive elements, each tagged with a "
    "[index]. Choose the SINGLE next action that best progresses the task.\n\n"
    + _TOOL_REPLY_RULE +
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
    + _PRICE_PLANNER_RULES +
    "SEARCHING THE WEB — Google is the default. To find or look something up, "
    "navigate DIRECTLY to a Google search URL in one step: "
    "https://www.google.com/search?q=<your+query+with+plus+signs>. Don't go to "
    "google.com and type — just navigate straight to the search URL.\n\n"
    + _ANSWERING_RULES +
    "Only navigate to a url the user gave you or a well-known site — NEVER invent a "
    "shop/product url. If a navigation FAILS (can't resolve), don't retry it — go "
    "to a Google search URL instead.\n\n"
    "TYPED FILTERS AND SORTS: a site silently ignores a filter or sort code in a url "
    "it doesn't recognise. After navigating to a url with one, your NEXT step "
    "checks the page applied it, before relying on it: read the 'Sort by' label, "
    "the result count, the filter chips, and any 'No results… showing all "
    "departments' banner. A 'Sort by' still on 'Featured', or a result count that "
    "jumped up, means the code was dropped: don't type it again. Use the page's own "
    "sort and filter controls instead, and once you've opened a 'Sort by' menu, "
    "click the option in it rather than typing a url. If the menu has no such "
    "option, the site can't sort that way: say so. Never name a sort or filter in "
    "`source` unless the page shows it applied.\n\n"
    "REGIONAL SITES: if a site sends you to a regional version (e.g. amazon.com.au) "
    "or shows prices in another currency, stay on it and work in the currency it "
    "shows. Do NOT try to change the delivery country, region or currency: those "
    "menus open pop-ups that block the page and waste steps. Read any price limits "
    "in the task as amounts in the site's currency, and say in your answer which "
    "site and currency the prices are from.\n\n"
    "POPUPS / OVERLAYS come FIRST. Cookie-consent, privacy, newsletter, region, or "
    "app-install banners sit on top of the page and block everything underneath — "
    "clicks on elements below them silently fail or time out. If the elements list "
    "has anything like 'Accept', 'Accept all', 'Reject', 'I agree', 'Continue', "
    "'Got it', 'Close', 'No thanks', or '×', DISMISS IT before doing anything else. "
    "Signs you're blocked by an overlay: your last click errored/timed out, or the "
    "button you want isn't clickable even though you can see it. In that case, look "
    "for a consent/close button and click that first.\n\n"
    "BOT CHECKS are NOT popups. A page asking you to prove you're human (a captcha, "
    "an 'I'm not a robot' or 'I am human' checkbox, Amazon's 'Continue shopping' "
    "robot check, 'Verify you are human') is the site asking for a person. NEVER "
    "tick the box, solve it, or click through it: choose done with success=false "
    "and say the site asked to check you're human.\n\n"
    "Only use an [index] that actually appears in the elements list. When the "
    "task is complete, or the answer is already visible, choose done with the "
    "answer (see the done rule above). Do not repeat an action that just ran.\n\n"
    "IMPORTANT — recognise completion from the actions-taken list. If it shows you "
    "already performed the action that completes the task (e.g. clicked 'Add to "
    "Cart' / 'Add to bag', submitted the form, logged in) and it did NOT error, "
    "the task is DONE — choose done. Do NOT re-click it, set a quantity, or take "
    "extra 'just to confirm' steps. A confirmation may not be visible on the page "
    "and you should NOT go looking for one.\n\n"
    "IF YOUR LAST PROPOSAL WAS REJECTED (the actions list shows 'gate rejected: …', "
    "or 'user said no: …' when the user turned it down and said why): "
    "do NOT repeat it. Read the reason and do something DIFFERENT — e.g. if it says "
    "the item is the wrong product (forks not spoons), scroll or search for the "
    "correct product and target THAT; if it says an overlay is blocking, dismiss "
    "the overlay. If after trying you genuinely cannot find what's needed on this "
    "page, choose done and honestly state what's missing — do NOT claim success.\n\n"
    "THE PLAN: you may get a short plan for the task, written before you started. "
    "It's a guide, not a record: it says what was meant to happen, not what did. "
    "The page and the actions-taken list are the truth. If they differ from the "
    "plan, follow the page. Skip a step the page makes unnecessary, and never give "
    "an answer just to finish the plan: if nothing meets the task's conditions, "
    "say so. If the latest entry in the actions-taken list is 'user said no: …', "
    "ALWAYS set `revised_plan`, even if only one step changes: the remaining steps, "
    "rewritten to follow their reason, so the user hears what changed. Otherwise "
    "set it ONLY when the plan no longer fits: a step failed or your proposal was "
    "rejected. Give the remaining steps, in the plan's plain style. In every other "
    "case leave `revised_plan` empty."
))


# what the analyse (verifier) node returns: an independent check on the action
# the planner picked, before it runs. this is the seed of the consensus gate —
# one channel (DOM) for now, a vision channel slots in later.
class Verdict(BaseModel):
    approved: bool = Field(description="true only if the proposed action is correct AND grounded in the page")
    reason: str = Field(description="one sentence: why it's right, or what's wrong / what to do instead")


# how the verifier judges a FIND task's done: price rules for the glasses demo
# (PRICE_TUNING), else the original rule
_VERIFIER_FIND_RULE = (
    "  • FIND / PRICE task: APPROVE done if it contains a $ price OR a prior extract "
    "found one. Wrong brand is OK — the reply will note `(different brand)`. "
    "Do NOT reject and send the agent searching again for brand match.\n"
) if PRICE_TUNING else (
    "  • FIND / ANSWER task (e.g. 'find the cheapest spoon', 'what's the price'): "
    "APPROVE if the requested information is present on the current page or already "
    "stated in the done text. No click or committing action is required — reading "
    "the page IS the completion. EXCEPTION: if the answer rests ONLY on Google's AI "
    "Overview or a search-result snippet (no source page was opened or extracted in "
    "the actions-taken list), REJECT and say to confirm it on a source page — "
    "unless the done text itself says it wasn't confirmed on a source page. A "
    "click on a source chip or preview inside Google's answer doesn't count: if the "
    "done text names a site as its source but the agent never left Google (the "
    "current page is google.com and no click or navigate in the actions-taken list "
    "is 'now on' another site), REJECT and say to open that site's page.\n"
)

VERIFIER_SYSTEM = SystemMessage(content=(
    _TOOL_REPLY_RULE +
    "You are an independent verifier for a browser automation agent. You did NOT "
    "choose the action. Judge ONLY whether the PROPOSED action is a reasonable "
    "step. Do NOT redesign the task, second-guess which item/target the planner "
    "picked, or propose a different item — assume the planner's target is correct "
    "unless the action is clearly ungrounded (e.g. an [index] that isn't the right "
    "element).\n\n"
    "NEVER reject based on WHICH SITE or DOMAIN the page is on. A regional mirror "
    "(amazon.com vs amazon.com.au, google.com vs google.co.uk) is FINE — the task "
    "can be completed on any equivalent site, so the specific domain is irrelevant. "
    "Do not demand the agent return to some 'original' domain. The same goes for "
    "CURRENCY: prices in the site's own currency (e.g. AUD on amazon.com.au) are "
    "FINE. Read a price range in the task as amounts in the site's currency; never "
    "reject because the currency differs from the task's, and never ask the agent "
    "to change the delivery country, region or currency.\n\n"
    "If an OVERLAY / MODAL / interstitial / cookie or region banner is blocking the "
    "page, APPROVE any reasonable action that dismisses or closes it — clicking ×, "
    "Close, Dismiss, Continue, Accept, or ANY of the modal's own buttons. Getting "
    "past a blocker is always valid progress; do NOT quibble over which dismiss "
    "button is best, and do NOT reject a dismiss action because of where it leaves "
    "the user. EXCEPT a bot check (a captcha, an 'I'm not a robot' / 'I am human' "
    "checkbox, Amazon's 'Continue shopping' robot check, 'Verify you are human'): "
    "REJECT any action that ticks, solves or clicks through one, and say the agent "
    "must stop and tell the user.\n\n"
    "Judge by action type:\n"
    "- INTERMEDIATE action (click/navigate/input/scroll — anything except done): "
    "APPROVE if it is a sensible next step toward the task, EVEN IF it does not by "
    "itself complete the task. Only REJECT if it is clearly wrong/ungrounded or "
    "REDUNDANT (it or an equivalent already appears in the actions-taken list). "
    "Never reject just because more steps remain.\n"
    "- `done`: decide by TASK TYPE.\n"
    + _VERIFIER_FIND_RULE +
    "  • DO / ACTION task (e.g. 'add to cart', 'submit', 'log in'): APPROVE if the "
    "actions-taken list already contains the completing action run WITHOUT error "
    "(e.g. an 'Add to bag' click). Do NOT require a visible confirmation — those are "
    "transient and often gone by now. Do NOT send the agent on a cart-verification "
    "detour.\n"
    "  • SIGN-IN: the agent never signs in. If the content the task needs is behind "
    "a sign-in (e.g. Amazon's 'Sign in to see customer reviews', shown on the page or "
    "in an extract), and the done text says so and gives the closest answer the page "
    "allows, APPROVE. Do NOT send the agent looking for a way around the sign-in.\n"
    "  • NOTHING QUALIFIES: a done that says nothing meets the task's conditions is "
    "a valid answer. APPROVE it if the done text says truthfully what was checked "
    "(e.g. 'page 1 of 610 results', 'sorted by Avg. Customer Review') and the "
    "actions-taken list backs that up. Don't assume what the task asks for exists, "
    "and don't demand every page or a sort the site doesn't offer. You may REJECT "
    "once, to ask for ONE specific further check that's clearly worth it (e.g. a "
    "sort the page offers). If the actions-taken list already shows a 'nothing "
    "qualifies' done rejected, APPROVE this one if it's honest. REJECT if it claims "
    "a check (a sort, a filter, pages) the actions-taken list doesn't show.\n"
    "  REJECT done only if none of these holds; then say what's still needed.\n\n"
    "When rejecting, give one concrete next step."
))


# the VISION channel of the consensus gate: same Verdict schema, but it judges
# from the SCREENSHOT, not the DOM text — so it catches things the DOM misses,
# especially overlays/popups covering the page and whether a click visually lands
# on the intended element
VISION_VERIFIER_SYSTEM = SystemMessage(content=(
    _TOOL_REPLY_RULE +
    "You are a VISION verifier for a browser agent. You see a SCREENSHOT of the "
    "current VIEWPORT and a proposed action. Crucial: the agent AUTOMATICALLY "
    "SCROLLS the target element into view before clicking, so the target is OFTEN "
    "NOT visible in this screenshot — it can be below the fold. That is normal and "
    "expected.\n\n"
    "Your job is to catch VISIBLE problems, NOT to confirm you can see the target. "
    "REJECT only when you can actually SEE something wrong:\n"
    "- An OVERLAY / modal is covering the page (cookie/consent/newsletter/region "
    "popup) and the action isn't dismissing it — clicks underneath will silently "
    "fail. Say to dismiss the overlay first.\n"
    "- The element visible AT the target position clearly contradicts the action "
    "(e.g. the action says click 'Add to Cart' but that exact spot is plainly an "
    "unrelated banner or ad).\n\n"
    "If the target simply isn't visible (below the fold / off-screen), that is NOT "
    "a reason to reject — APPROVE and note you can't see it but nothing is blocking. "
    "Do NOT reject because the task needs more steps, and do NOT second-guess the "
    "FLOW — e.g. never reject an 'Add to Cart' click by arguing the product page "
    "must be opened first. Element targeting is the DOM channel's job; you veto "
    "only VISIBLE blockers.\n\n"
    "For done: reject only if the visible screen CLEARLY contradicts completion. "
    "Keep `reason` to one sentence describing what you see."
))


# said aloud when the agent stops itself (a loop guard, or an action it couldn't
# build): plain language, like graph.FAILURE_MESSAGES. the technical reason goes
# to the console only
STUCK_MESSAGE = ("Sorry, I got stuck and couldn't finish this, so I stopped. "
                 "You could try asking in a simpler or more specific way.")
BAD_ACTION_MESSAGE = ("Sorry, I couldn't work out the next step, so I stopped. "
                      "You could try asking again.")
BOT_CHECK_MESSAGE = ("Sorry, this site is asking me to prove I'm human. I don't get "
                     "past those checks myself, so I stopped. You can complete the "
                     "check on the page and ask me again.")


def _decision_to_action(tools: Tools, d: PlanDecision) -> Any:
    # turn the planner's PlanDecision into a real browser-use ActionModel. if a
    # required field is missing we fall back to `done` so a bad decision stops the
    # run cleanly instead of blowing up mid-loop.
    AM = tools.registry.create_action_model(include_actions=[d.action])
    if d.action == "navigate" and d.url:
        return AM.model_validate({"navigate": {"url": d.url, "new_tab": False}})
    if d.action == "click" and d.index is not None:
        return AM.model_validate({"click": {"index": d.index}})
    if d.action == "input" and d.index is not None and d.text is not None:
        return AM.model_validate({"input": {"index": d.index, "text": d.text}})
    if d.action == "scroll":
        return AM.model_validate({"scroll": {"down": d.down if d.down is not None else True}})
    if d.action == "extract" and d.text:
        return AM.model_validate({"extract": {"query": d.text}})
    if d.action == "done":
        return AM.model_validate({"done": {"text": d.text or "done", "success": d.success}})
    # malformed action (e.g. click with no index) -> stop gracefully
    stop = tools.registry.create_action_model(include_actions=["done"])
    print(f"  ⚠ incomplete action {d.action!r}, stopping")
    return stop.model_validate({"done": {"text": BAD_ACTION_MESSAGE, "success": False}})


# what the planner has to fill for each action, and what to tell it when it
# doesn't. the same checks as _decision_to_action, so analyse can send an
# incomplete action back before execute turns it into a stop
_REQUIRED_FIELDS = {
    "navigate": (("url", "set `url` to a full url"),),
    "click": (("index", "set `index` to an element's [index] from the list"),),
    "input": (("index", "set `index` to the field's [index] from the list"),
              ("text", "set `text` to what to type")),
    "extract": (("text", "set `text` to what to read off the page"),),
}


def _missing_field(d: PlanDecision) -> tuple[str, str] | None:
    for field, fix in _REQUIRED_FIELDS.get(d.action, ()):
        value = getattr(d, field)
        if value is None or (field == "text" and d.action == "extract" and not value.strip()):
            return field, fix
    return None


# how much of an action's outcome the planner sees in the actions-taken list.
# extract gets more: its outcome IS the information the task asked for, and a
# cut-off answer makes the planner extract again or fill in from memory. 1000
# was too short: 16 of 24 extracts in the 24 Oct run hit it, and Google
# Search--24 lost Proxima d to the cut (suggestions #11). so the latest extract
# is shown up to EXTRACT_OUTCOME_LIMIT, and older ones, already acted on, are
# cut back to OLDER_EXTRACT_LIMIT so a long task's prompt doesn't keep growing
OUTCOME_LIMIT = 200
EXTRACT_OUTCOME_LIMIT = 4000
OLDER_EXTRACT_LIMIT = 1000
CUT_MARK = " […cut off]"


def _cut(text: str, limit: int) -> str:
    # shorten to limit, saying so: an unmarked cut reads like the whole page
    # (Google Search--24 had to guess its extract was cut)
    return text if len(text) <= limit else text[:limit] + CUT_MARK


def _is_extract(entry: dict) -> bool:
    action = entry.get("action")
    return isinstance(action, dict) and "extract" in action


def _extract_answer(content: str) -> str:
    # browser-use wraps extract output as <url>…</url><query>…</query><result>…
    # </result> (or <structured_result>); keep just the result so the answer isn't
    # pushed past the limit by the wrapper
    m = re.search(r"<(structured_result|result)>\s*(.*?)\s*</\1>", content, re.S)
    return m.group(2) if m else content


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
    latest_extract = next((e for e in reversed(recent) if _is_extract(e)), None)

    def outcome(e: dict) -> str:
        text = str(e["outcome"])
        if _is_extract(e) and e is not latest_extract and not text.endswith(CUT_MARK):
            return _cut(text, OLDER_EXTRACT_LIMIT)
        return text

    lines = "\n".join(
        f"{i}. {e['action']} -> {outcome(e)}" for i, e in enumerate(recent, 1)
    )
    return (
        f"Actions you have ALREADY taken this task (do not repeat them; if these "
        f"show the task is complete, choose done):\n{lines}\n\n"
    )


def _format_plan(plan: list[str]) -> str:
    # the task's plan for the planner, labelled as intent so it isn't read as
    # a record of what happened (PLANNER_SYSTEM, "THE PLAN")
    if not plan:
        return ""
    lines = "\n".join(f"{i}. {s}" for i, s in enumerate(plan, 1))
    return f"The plan for this task (a guide; the page is the truth):\n{lines}\n\n"


async def node_make_plan(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    # write the task's plan once, before the first step: a few plain steps the
    # user sees in the first approval and the log, and hears as one line. the
    # glasses price tasks skip it so the demo stays fast (decided 30 Oct)
    query = state.get("query", "")
    if _price_shortcuts(query):
        return {"plan": [], "plan_summary": ""}

    session, _, _ = _resources(config)
    # only where the browser is, not the page's elements: the plan says what to
    # do, and the planner reads each page as it goes
    try:
        page = f"{await session.get_current_page_title()} ({await session.get_current_page_url()})"
    except Exception:
        page = "unknown"
    history = _format_history(state.get("messages", [])[:-1])
    user_msg = UserMessage(content=f"{history}Request: {query}\n\nThe browser is on: {page}")

    response = await _ainvoke_with_retry(make_planner_llm(), [MAKE_PLAN_SYSTEM, user_msg], TaskPlan)
    plan: TaskPlan = response.completion
    print("  make_plan → " + " | ".join(plan.steps))
    return {"plan": plan.steps, "plan_summary": plan.summary.strip()}


async def node_plan(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    # read the page, ask the model for the next action. perception (DOM +
    # screenshot) comes from browser-use; the decision comes from our own llm
    # call, which is the whole point of owning the loop. we also feed in the
    # prior conversation so follow-up prompts have context.
    session, _, _ = _resources(config)  # planner only reads the page; execute acts
    step = state.get("step", 0) + 1

    # a bot check is the site asking for a person, and the agent never gets past
    # one itself (state._BOT_CHECK_JS). checked in code, before the model call,
    # so the model can't talk itself past one; the prompt rule covers what the
    # check misses
    bot = await _bot_check(session)
    if bot:
        print(f"  [{step}] plan → stop: bot check ({bot})")
        stop = PlanDecision(reasoning=f"stopped at a bot check ({bot})", action="done",
                            success=False, text=BOT_CHECK_MESSAGE)
        return {"last_decision": stop, "step": step}

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
        f"{_format_plan(state.get('plan', []))}"
        f"{progress}"
        f"Current page: {summary.title} ({summary.url})\n\n"
        f"Interactive elements:\n{dom_text}"
    ))

    llm = make_planner_llm()
    response = await _ainvoke_with_retry(llm, [PLANNER_SYSTEM, user_msg], PlanDecision)
    decision: PlanDecision = response.completion
    # the spoken answer is the answer, then its source (PlanDecision.answer,
    # .source), joined into `text` so the verifier, the loop guard and the user
    # all see the same thing. an answer put in `text` out of habit (and the
    # glasses demo's price, which uses `text`) counts too
    if decision.action == "done":
        spoken = (decision.answer or decision.text or "").strip()
        if spoken and decision.source:
            spoken = f"{spoken} {decision.source.strip()}"
        decision.text = spoken or None

    print(f"  [{step}] plan → {decision.action}")
    # a price task has no plan to revise
    if decision.revised_plan and state.get("plan"):
        new_plan = " | ".join(decision.revised_plan)
        print(f"  [{step}] plan → new plan: {new_plan}")
        return {"last_decision": decision, "step": step, "plan": decision.revised_plan}
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


async def node_analyse(state: PipelineState, config: RunnableConfig) -> Command[Literal["approve", "plan", "resolve"]]:
    # verify before consequential actions (click, done). DOM is the decision;
    # vision notes overlay issues but does not block when DOM already approved —
    # otherwise a visible popup rejects a valid done answer and the agent loops.
    decision: PlanDecision = state["last_decision"]  # pyright: ignore[reportTypedDictNotRequiredAccess]  (always set by plan)

    # an action missing a field it needs is sent back below, whatever the action:
    # execute would turn it into a stop and end the task (Amazon--6, 30 Oct smoke
    # run: an extract with no `text` at step 2)
    missing = _missing_field(decision)
    if decision.action not in ("click", "done") and not missing:
        return Command(goto="approve")
    # the bot-check stop comes from code, not the model: nothing to verify
    if decision.action == "done" and decision.text == BOT_CHECK_MESSAGE:
        return Command(goto="approve")

    query = state.get("query", "")

    # price found in done text — skip slow verifier LLM call
    if PRICE_TUNING and decision.action == "done" and price_from_text(decision.text or ""):
        print("  analyse [✓] done: price in answer")
        return Command(goto="approve")

    rej_key = f"(rejected: {decision.action} idx={decision.index} url={decision.url})"
    # a done's index and url are always None, so without its answer every done
    # gets the same key and the guard below throws away a corrected answer as a
    # "repeat". a short hash of the text tells answers apart; a second empty
    # done still matches the first and stops
    answer_text = (decision.text or "").strip()
    if decision.action == "done" and answer_text:
        digest = hashlib.sha1(answer_text.encode()).hexdigest()[:8]
        rej_key = f"(rejected: done answer={digest})"
    # keyed on what's missing, not the junk in the other fields, so a second
    # incomplete action of the same kind trips the guard below and stops
    if missing:
        rej_key = f"(rejected: {decision.action} without {missing[0]})"

    # rejection loop guard: if this exact action was already rejected AND nothing
    # has actually executed since, the planner is ignoring the feedback and
    # spinning (plan→analyse→plan…). the execute-stage loop guard can't catch this
    # because a rejected action never reaches execute. stop cleanly instead of
    # looping to MAX_STEPS.
    #
    # BUT: only trip if there was NO real progress since the last such rejection.
    # scratch stores executed actions as a dict `action`, and gate rejections as
    # the rej_key STRING — so an executed (dict) entry after the last matching
    # rejection means the situation changed (e.g. a `done` rejected as premature,
    # then a modal 'Add to cart' actually clicked). re-evaluate rather than
    # force-stop, or a legit second `done` gets killed as a "repeat".
    scratch = state.get("scratch", [])
    last_rej_idx = None
    for i, e in enumerate(scratch):
        if e.get("action") == rej_key:
            last_rej_idx = i
    if last_rej_idx is not None:
        progressed = any(
            not isinstance(e.get("action"), str)  # executed action -> dict; rejection -> str
            for e in scratch[last_rej_idx + 1:]
        )
        if not progressed:
            # if we already have a price from extract, return it instead of erroring
            for e in reversed(scratch) if PRICE_TUNING else []:
                raw = str(e.get("outcome", ""))
                if price_from_text(raw):
                    answer = compact_glasses_reply(query, raw)
                    print(f"  analyse → stop, using price {answer}")
                    stop = PlanDecision(reasoning="returning best price found", action="done", text=answer)
                    return Command(goto="approve", update={"last_decision": stop})
            last_reason = scratch[last_rej_idx].get("outcome", "the action was rejected repeatedly")
            print(f"  analyse → stop (same action rejected twice): {last_reason[:120]}")
            stop = PlanDecision(
                reasoning="stuck: the gate rejected the same action repeatedly",
                action="done",
                success=False,
                text=STUCK_MESSAGE,
            )
            return Command(goto="approve", update={"last_decision": stop})

    # a done with no text has no answer to give: execute would report the word
    # "done". send it back so the planner writes the answer (or why it stopped).
    # no verifier call needed. a second empty done trips the loop guard above
    if decision.action == "done" and not (decision.text or "").strip():
        print("  analyse [✗] done: no answer text")
        field = "text" if PRICE_TUNING else "answer"
        # say exactly what's missing: "no text" alone didn't stop a model that
        # had filled `source` from doing it again (Google Search--19, 25 Oct)
        reason = (f"done has `source` but no `{field}`: the source isn't the answer. Put the "
                  f"answer itself in `{field}`, in one sentence"
                  if decision.source else
                  f"done has no `{field}`: put the answer to the request (or why you "
                  f"couldn't finish) in `{field}`")
        return _reject(state, rej_key, reason)

    if missing:
        field, fix = missing
        print(f"  analyse [✗] {decision.action}: no {field}")
        return _reject(state, rej_key, f"{decision.action} has no `{field}`: {fix}")

    session, _, _ = _resources(config)
    summary = await session.get_browser_state_summary(cached=True, include_screenshot=True)
    dom_text = summary.dom_state.llm_representation()[:6000]
    progress = _format_scratch(state.get("scratch", []))

    dom_v = await _verify_dom(decision, query, dom_text, progress,
                              summary.title, summary.url)
    # skip vision for done and for price tasks (saves ~2s per step, fewer overlay loops)
    skip_vision = decision.action == "done" or _price_shortcuts(query)
    vis_v = None if skip_vision else await _verify_vision(
        decision, query, progress, summary.screenshot,
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
        return Command(goto="approve")
    return _reject(state, rej_key, reason)


# typed like node_analyse's return: Command's type parameter is invariant, so a
# narrower Literal here doesn't type-check where analyse returns it
def _reject(state: PipelineState, rej_key: str, reason: str) -> Command[Literal["approve", "plan", "resolve"]]:
    # send a rejected action back to the planner with the reason in scratch
    entry = {"action": rej_key, "outcome": f"gate rejected: {reason}"}
    scratch = state.get("scratch", []) + [entry]
    # a rejection loops back to plan without passing resolve, which is what
    # enforces MAX_STEPS. so check it here: without this, a planner that keeps
    # proposing *different* rejected actions runs into langgraph's recursion
    # limit, which the error handler can't catch
    if state.get("step", 0) >= MAX_STEPS:
        return Command(goto="resolve", update={"scratch": scratch})
    return Command(goto="plan", update={"scratch": scratch})

# ── approval ─────────────────────────────────────────────────────────────────
# the user's say before a step runs: the thesis's core requirement (§5.2.1).
# it lives in the graph, so the gui, mvp.py and serve.py all get it, and it uses
# interrupt() so the answer can carry a reason back in. the policy (when to
# ask) comes from the run config: state.APPROVAL_POLICIES

# said aloud when the user ends the task at an approval
STOPPED_MESSAGE = "Okay, I've stopped. I didn't do anything more on the page."
DENIED_MESSAGE = "Okay, I didn't do that step, and I've stopped the task."

# words on a button or link that mean the step commits to something, for the
# "risky" policy. matched against the element's visible text and labels
RISKY_WORDS = (
    "buy", "checkout", "check out", "pay", "place order", "order now", "purchase",
    "add to cart", "add to basket", "add to bag", "subscribe", "submit", "send",
    "post", "delete", "remove", "cancel order", "sign in", "log in", "login",
    "sign up", "register", "book", "reserve", "confirm", "transfer", "donate",
)


async def _element_info(session, index: int | None) -> tuple[str, dict]:
    # the visible name and attributes of the element a step acts on
    if index is None:
        return "", {}
    try:
        node = await session.get_element_by_index(index)
    except Exception:
        return "", {}
    if node is None:
        return "", {}
    label = " ".join((node.get_meaningful_text_for_llm() or "").split())[:80]
    return label, dict(node.attributes or {})


def _is_risky(decision: "PlanDecision", label: str, attributes: dict) -> bool:
    # a click or typing that commits to something; reading, scrolling and
    # going to a page never do
    if decision.action not in ("click", "input"):
        return False
    if decision.action == "input":
        # typing a password or card details is risky whatever the field is called
        kind = str(attributes.get("type", "")).lower()
        autocomplete = str(attributes.get("autocomplete", "")).lower()
        if kind == "password" or autocomplete.startswith("cc-") or "current-password" in autocomplete:
            return True
    words = " ".join([label, *(str(attributes.get(k, "")) for k in ("aria-label", "value", "name", "id"))]).lower()
    return any(re.search(rf"\b{re.escape(w)}\b", words) for w in RISKY_WORDS)


def _should_ask(policy: str, decision: "PlanDecision", state: PipelineState,
                page_url: str, label: str, attributes: dict) -> bool:
    if decision.action == "done" or policy == "auto":
        return False  # done only reports the answer; nothing happens in the browser
    if policy == "every_step":
        return True
    if policy == "new_site":
        target = decision.url if decision.action == "navigate" and decision.url else page_url
        return urlparse(target).netloc != urlparse(page_url).netloc
    if policy == "risky":
        return _is_risky(decision, label, attributes)
    if policy == "on_error":
        # the previous step was turned down by the agent's own check, or ran
        # and came back with an error
        scratch = state.get("scratch") or []
        if not scratch:
            return False
        last = scratch[-1]
        return str(last.get("outcome", "")).startswith("gate rejected") or bool(last.get("failed")) or bool(
            getattr(state.get("last_result"), "error", None))
    return True  # an unknown policy asks rather than acts


async def node_approve(state: PipelineState, config: RunnableConfig) -> Command[Literal["execute", "plan", "resolve", "__end__"]]:
    # ask the user before the step runs, if the policy calls for it. the graph
    # pauses at interrupt(); the caller resumes it with
    # Command(resume={"decision": "yes" | "no" | "stop", "reason": optional str}).
    # on resume the node runs again from the top and interrupt() returns that
    decision: PlanDecision = state["last_decision"]  # pyright: ignore[reportTypedDictNotRequiredAccess]  (always set by plan)
    session, _, _ = _resources(config)
    policy = (config.get("configurable") or {}).get("approval", "every_step")
    if decision.action == "done" or policy == "auto":
        return Command(goto="execute")

    try:
        page_url = await session.get_current_page_url()
        page_title = await session.get_current_page_title()
    except Exception:
        page_url, page_title = "", ""
    label, attributes = await _element_info(session, decision.index)
    if not _should_ask(policy, decision, state, page_url, label, attributes):
        return Command(goto="execute")

    answer = interrupt({
        "summary": decision.user_summary or decision.reasoning,
        # the caller shows the plan on the first question and whenever it changes
        "plan": state.get("plan", []),
        "action": decision.model_dump(exclude_none=True, exclude={"reasoning", "user_summary", "revised_plan"}),
        "label": label,
        "page_url": page_url,
        "page_title": page_title,
        "policy": policy,
    })
    if not isinstance(answer, dict):
        answer = {"decision": str(answer)}
    choice = str(answer.get("decision", "")).lower()
    reason = str(answer.get("reason") or "").strip()
    print(f"  approve [{choice}]" + (f": {reason}" if reason else ""))

    if choice == "yes":
        return Command(goto="execute")
    if choice == "no" and reason:
        # back to the planner with the user's reason, like a rejection by the
        # agent's own check, so it plans something different
        entry = {"action": f"(user said no to: {decision.action} idx={decision.index} url={decision.url})",
                 "outcome": f"user said no: {reason}"}
        scratch = state.get("scratch", []) + [entry]
        if state.get("step", 0) >= MAX_STEPS:
            return Command(goto="resolve", update={"scratch": scratch})
        return Command(goto="plan", update={"scratch": scratch})
    # stop, a no without a reason, or anything unexpected: end the task, and
    # reset it like resolve does so the next prompt starts clean
    message = STOPPED_MESSAGE if choice == "stop" else DENIED_MESSAGE
    return Command(goto=END, update={  # pyright: ignore[reportReturnType]  (END is typed as str)
        "messages": [{"role": "assistant", "content": message}],
        "step": 0, "done": False, "scratch": [], "status": "stopped",
    })


async def node_execute(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
    # run the action the planner chose. just browser-use's hands 
    session, tools, file_system = _resources(config)
    action = _decision_to_action(tools, state["last_decision"])  # pyright: ignore[reportTypedDictNotRequiredAccess]  (always set by plan)

    # loop guard: if the planner picks the same action a 3rd time it's spinning
    # (navigate→click→navigate…), so force a clean stop. scroll is exempt since
    # repeating it to page through content is fine.
    action_dump = action.model_dump(exclude_unset=True)
    action_name = next(iter(action_dump), "")
    repeats = sum(1 for e in state.get("scratch", []) if e.get("action") == action_dump)

    # price task: if extract already returned a price, stop re-extracting
    query = state.get("query", "")
    if action_name == "extract" and _price_shortcuts(query):
        for e in state.get("scratch", []):
            if "extract" not in str(e.get("action", "")):
                continue
            raw = str(e.get("outcome", ""))
            if price_from_text(raw):
                answer = compact_glasses_reply(query, raw)
                print(f"  ⚠ already have price, finishing: {answer}")
                action = _decision_to_action(tools, PlanDecision(
                    reasoning="extract already found a price",
                    action="done",
                    text=answer,
                ))
                action_dump = action.model_dump(exclude_unset=True)
                action_name = "done"
                break

    if action_name != "scroll" and repeats >= 2:
        print(f"  ⚠ loop guard: '{action_name}' repeated {repeats + 1}×, stopping")
        action = _decision_to_action(tools, PlanDecision(
            reasoning="loop guard: same action repeated without progress",
            action="done",
            success=False,
            text=STUCK_MESSAGE,
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

    # let a page the action opened finish loading, so neither the outcome below
    # nor the next plan step reads it blank (see state.PAGE_READY_TIMEOUT)
    await _wait_for_page(session)

    # jot this action down in the within-task log so the next plan step sees it
    # read the page fresh so the outcome reflects where the action landed us
    after = await session.get_browser_state_summary(include_screenshot=False)
    landed = ""
    if result.error:
        outcome, limit = result.error, OUTCOME_LIMIT
    elif action_name == "extract" and result.extracted_content:
        outcome, limit = _extract_answer(result.extracted_content), EXTRACT_OUTCOME_LIMIT
    else:
        outcome, limit = result.extracted_content or f"now on {after.url}", OUTCOME_LIMIT
        if action_name == "click" and result.extracted_content:
            # say which site the click landed on: "Clicked a 'MacRumors'" alone
            # doesn't show whether it opened the page or only a preview on Google
            # (Google Search--7, 24 Oct smoke run). the host is short, so it goes
            # after the cut and always survives it
            landed = f" -> now on {urlparse(after.url).netloc or after.url}"
    entry = {"action": action_dump, "outcome": _cut(str(outcome), limit - len(landed)) + landed}
    scratch = state.get("scratch", []) + [entry]
    return {"last_result": result, "done": done, "scratch": scratch}

def _finish_status(done: bool, result: Any) -> str:
    # done with success=False means the task wasn't completed: the planner gave
    # up honestly, or a loop guard stopped it. only a successful done is "ok"
    if not done:
        return "max_steps"
    return "gave_up" if getattr(result, "success", None) is False else "ok"


async def node_resolve(state: PipelineState) -> Command[Literal["plan", "__end__"]]:
    result = state.get("last_result")
    step = state.get("step", 0)
    query = state.get("query", "")
    finished = state.get("done", False) or step >= MAX_STEPS

    # price task: first successful extract → finish immediately (don't plan again)
    if not finished and _price_shortcuts(query):
        content = getattr(result, "extracted_content", None) or ""
        if price_from_text(content):
            answer = compact_glasses_reply(query, str(content))
            print(f"  done (early): {answer}")
            return Command(goto=END, update={  # pyright: ignore[reportReturnType]  (END is typed as str)
                "messages": [{"role": "assistant", "content": answer}],
                "step": 0, "done": False, "scratch": [], "status": "ok",
            })

    if finished:
        reason = "task done" if state.get("done") else f"hit MAX_STEPS ({MAX_STEPS})"
        if state.get("done"):
            answer = getattr(result, "extracted_content", None) or reason
        else:
            # said aloud to the user, so plain language, not the console reason.
            # never the last action's result: that isn't an answer to the task
            answer = (f"Sorry, I couldn't finish this within {MAX_STEPS} steps, so I "
                      f"stopped. You could try asking in a simpler or more specific way.")
        # stored in full: the gui and serve.py shorten it themselves where it's
        # spoken or sent to the phone, and evaluation scores the full answer
        print(f"  done: {answer}")
        return Command(goto=END, update={  # pyright: ignore[reportReturnType]  (END is typed as str)
            "messages": [{"role": "assistant", "content": str(answer)}],
            "step": 0,
            "done": False,
            "scratch": [],  # clear the within-task memory for the next task
            "status": _finish_status(state.get("done", False), result),
        })

    # not done — go plan the next step
    return Command(goto="plan")
