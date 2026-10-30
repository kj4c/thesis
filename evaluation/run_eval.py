# run the agent on the WebVoyager subset (tasks.jsonl) and log every step.
#
# goes through the same graph as mvp.py: no approval, so nothing waits on a
# human. each task gets a fresh browser opened on the task's site and a fresh
# thread, and is phrased the way WebVoyager phrases it to its own agent.
#
# writes evaluation/results/<time>_<label>/:
#   config.json   the setup the run used (models, flags, git commit, versions)
#   steps.jsonl   one line per graph node: what it decided or did, how long it
#                 took, and the tokens and cost of the model calls inside it.
#                 llm_errors lists any model call in it that raised, even one
#                 a retry then recovered; the error handler's line has the
#                 failure in last_error
#   tasks.jsonl   one line per attempt at a task: answer, status, steps, time,
#                 tokens, cost, the error behind a failure status (error) and
#                 every failed model call (llm_errors). attempt 0 is the
#                 planned run, 1+ are retries
#   summary.csv   the same per task (its last attempt), with a `success`
#                 column to score. a task that didn't end with status ok is
#                 filled in as 0 already, except: an ending in RETRY_STATUSES
#                 (the site blocked the browser, setup failed, or the model API
#                 failed) that is still there after the retries, left blank to
#                 re-run rather than score; and a gave_up with the agent's own
#                 answer, left blank to review
#   screenshots/  per task: the page the planner read at each step (stepNN.png)
#                 and the page at the end (final.png). both of WebVoyager's
#                 scoring methods judge from these
#
# run from the project root:
#   python evaluation/run_eval.py --label baseline
#   python evaluation/run_eval.py --ids "Google Search--0,Amazon--3" --label smoke

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# evaluation always runs without the glasses-demo price tuning. set before the
# project modules load .env (load_dotenv doesn't override what's already set)
os.environ["PRICE_TUNING"] = "false"

import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import json
import logging
import platform
import subprocess
import time
from contextvars import ContextVar
from datetime import datetime

import llm
import nodes
from graph import build_agent
from mvp import last_assistant_reply
from state import ABOUT_BLANK_GRACE, MAX_STEPS, PAGE_READY_TIMEOUT, PipelineState, _resources, _run_config, _start_resources

TASKS = Path(__file__).parent / "tasks.jsonl"
RESULTS = Path(__file__).parent / "results"

# a hard cap per task on top of the agent's own MAX_STEPS and node timeouts, so
# one hung task can't stall the whole run
TASK_TIMEOUT = 600

# a fixed browser so runs compare: WebVoyager uses a 1024x768 window, so the
# page area (and every screenshot) is 1024x768 here too. windowed rather than
# headless: Google sends headless chrome (which names itself "HeadlessChrome"
# in its user agent) straight to its captcha page
BROWSER = {"headless": False, "viewport": {"width": 1024, "height": 768}}

# browser-use waits 3 s (same site) or 8 s (new site) for a page's load event
# after navigating, then logs "Page readiness timeout" and carries on. it isn't
# a failure: it can't match the load event of a page that redirected (Google
# adds &sei=, Amazon ?th=1), and when it gives up the page is already complete
# or nearly so, and reading the page for the planner waits for the rest
# (checked October 2026). a longer timeout would only slow every redirected
# navigation, and past ~10 s it trips browser-use's 30 s navigation limit and
# fails the navigation. so the warning is dropped from the console instead
class _NoReadinessTimeout(logging.Filter):
    def filter(self, record):
        return "Page readiness timeout" not in record.getMessage()


for _handler in logging.getLogger("browser_use").handlers:
    _handler.addFilter(_NoReadinessTimeout())


# WebVoyager's Amazon references were collected with a US delivery address
# (free returns, US prices). a fresh browser gets its delivery country from the
# IP instead, so from Australia amazon.com shows AUD prices and no free returns.
# Amazon tasks set this ZIP through the site's own "Deliver to" popup first
US_ZIP = "10001"

# types the ZIP into the "Deliver to" popup and applies it. runs in the page, so
# it's async JS that polls for each element. Amazon then asks to confirm with a
# "Continue" button, which reloads the page, so that click is a separate call
_SET_ZIP_JS = """(zip) => new Promise(async (resolve) => {
  const wait = async (sel) => {
    for (let i = 0; i < 50; i++) {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) return el;
      await new Promise(r => setTimeout(r, 200));
    }
    return null;
  };
  const link = await wait('#nav-global-location-popover-link');
  if (!link) return resolve('no location link');
  link.click();
  const input = await wait('#GLUXZipUpdateInput');
  if (!input) return resolve('no zip input');
  // the popup's handlers bind a moment after it shows; an early Apply is ignored
  await new Promise(r => setTimeout(r, 1500));
  input.value = zip;
  input.dispatchEvent(new Event('input', {bubbles: true}));
  const apply = await wait('#GLUXZipUpdate input');
  if (!apply) return resolve('no apply button');
  apply.click();
  for (let i = 0; i < 50; i++) {
    const pop = document.querySelector('.a-popover[aria-hidden=false]');
    if (pop && pop.innerText.includes('now shopping for delivery')) return resolve('applied');
    await new Promise(r => setTimeout(r, 200));
  }
  resolve('zip not applied');
})"""

_CONTINUE_JS = """() => {
  const pop = document.querySelector('.a-popover[aria-hidden=false]');
  const button = pop && [...pop.querySelectorAll('input, button, .a-button-text')]
    .find(el => (el.value || el.innerText || '').trim() === 'Continue');
  if (!button) return 'no continue button';
  setTimeout(() => button.click(), 0);
  return 'clicked';
}"""


async def _set_us_delivery(session, url: str, attempts: int = 3) -> str | None:
    # url is the task's own page (amazon.com), reopened before each retry.
    # confirming reloads it, so the agent starts on a page already priced for
    # the US address. returns the last error, or None once the header shows the ZIP
    error = None
    for attempt in range(attempts):
        if attempt:
            await session.navigate_to(url)
        error = await _try_set_zip(await session.must_get_current_page())
        if error is None:
            return None
    return error


async def _try_set_zip(page) -> str | None:
    try:
        # amazon can still be loading after navigate_to gives up waiting on it
        for _ in range(20):
            try:
                if await page.evaluate("() => !!document.querySelector('#nav-global-location-popover-link')") == "True":
                    break
            except Exception:
                pass  # mid-load
            await asyncio.sleep(1)
        applied = await page.evaluate(_SET_ZIP_JS, US_ZIP)
        if applied != "applied":
            return f"set zip failed: {applied}"
        clicked = await page.evaluate(_CONTINUE_JS)
        if clicked != "clicked":
            return f"set zip failed: {clicked}"
        for _ in range(20):
            await asyncio.sleep(1)
            try:
                line = await page.evaluate(
                    "() => (document.querySelector('#glow-ingress-line2') || {}).textContent || ''")
            except Exception:
                continue  # mid-reload
            if US_ZIP in line:
                return None
        return "set zip failed: header never showed the zip"
    except Exception as e:
        return f"set zip failed: {e!r}"


# Amazon scores fresh, cookie-less sessions from one IP as bots, and a burst of
# them gets the next one a "Continue shopping" checkpoint and then a sign-in
# wall. an Amazon task waits until this long after the last one ended
AMAZON_GAP = 90

# endings that say nothing about the agent's decisions: the site blocked the
# browser, setup failed before the agent started, the model API failed or hit
# its limit, or a step hit its 30 s node timeout (a slow API, or a browser tab
# that stopped answering, as in Amazon--0 in the 24 Oct smoke run).
# after the planned tasks, each one that ended like this is run again (--retries
# times at most), last so Amazon has had longest to forget the session
RETRY_STATUSES = ("blocked", "setup_failed", "llm_failed", "rate_limited", "timed_out")

# pages that mean the site blocked the browser rather than the agent failing:
# a task that hits one and doesn't finish is marked blocked, to re-run, not
# scored a failure (WebVoyager drops these too). checked before every plan step
_BLOCK_JS = """() => {
  const host = location.host, url = location.href;
  const text = document.body ? document.body.innerText : '';
  if (host.includes('amazon.')) {
    if (text.includes('To continue, please click the') && text.includes('Continue shopping')) return 'amazon checkpoint';
    if (url.includes('validateCaptcha') || text.includes('Enter the characters you see below')) return 'amazon captcha';
    if (url.includes('/ap/signin') || url.includes('/ax/claim') || (text.includes('Sign in or create account') && !document.querySelector('#twotabsearchtextbox'))) return 'amazon sign-in';
  }
  if (host.includes('google.') && location.pathname.startsWith('/sorry')) return 'google captcha';
  return '';
}"""


# how long the runner's own checks on the browser (block check, final
# screenshot, closing it) may take. a tab that stops answering held each of
# them indefinitely: 70 s at the end of Amazon--0 in the 24 Oct smoke run
BROWSER_CHECK_TIMEOUT = 10


async def _block(session) -> dict | None:
    async def check():
        page = await session.must_get_current_page()
        kind = await page.evaluate(_BLOCK_JS)
        return {"kind": kind, "url": await page.evaluate("() => location.href")} if kind else None
    try:
        return await asyncio.wait_for(check(), BROWSER_CHECK_TIMEOUT)
    except Exception:
        return None  # mid-load, no page, or not answering: not evidence of a block


# WebVoyager's own wording (WebVoyager-main/run.py)
PROMPT = "Now given a task: {ques}  Please interact with {web} and get the answer."

# USD per million tokens, Anthropic first-party API rates (checked 15 Oct 2026).
# thinking tokens are billed as output. a model missing here gets cost None
PRICES = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
}


# ── token accounting ─────────────────────────────────────────────────────────
# every model call the agent makes goes through llm._build_llm's chat objects,
# including the extraction model browser-use calls inside `extract`. wrap their
# ainvoke so each call's usage lands in the current task's list
_usage: ContextVar[list | None] = ContextVar("usage", default=None)
# failed calls, including ones a retry then recovered: an API error (overload,
# rate limit) shows up here even when the task carried on, as a slow node
_llm_errors: ContextVar[list | None] = ContextVar("llm_errors", default=None)


def _recording(build):
    def wrapped(provider, model):
        chat = build(provider, model)
        original = chat.ainvoke

        async def ainvoke(messages, output_format=None, **kwargs):
            try:
                response = await original(messages, output_format=output_format, **kwargs)
            except Exception as e:
                errors = _llm_errors.get()
                if errors is not None:
                    errors.append(f"{model}: {e!r}"[:300])
                raise
            log = _usage.get()
            if log is not None and response.usage is not None:
                log.append((model, response.usage))
            return response

        chat.ainvoke = ainvoke
        return chat
    return wrapped


llm._build_llm = _recording(llm._build_llm)


def _cost(model: str, usage) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    # browser-use counts cache reads inside prompt_tokens; cache writes are separate
    cached = usage.prompt_cached_tokens or 0
    written = usage.prompt_cache_creation_tokens or 0
    usd = ((usage.prompt_tokens - cached) * p["input"] + cached * p["cache_read"]
           + written * p["cache_write"] + usage.completion_tokens * p["output"]) / 1e6
    return usd * (usage.pricing_multiplier or 1)


def _tally(calls: list) -> dict:
    costs = [_cost(model, u) for model, u in calls]
    known = [c for c in costs if c is not None]
    return {
        "calls": len(calls),
        "input_tokens": sum(u.prompt_tokens + (u.prompt_cache_creation_tokens or 0) for _, u in calls),
        "output_tokens": sum(u.completion_tokens for _, u in calls),
        "cost_usd": round(sum(known), 6) if len(known) == len(costs) else None,
    }


# ── screenshots ──────────────────────────────────────────────────────────────
# taken at the start of each plan step, so stepNN.png is the page the planner
# read for step NN. wraps nodes.node_plan, which graph.py looks up when the
# graph is built, so the agent itself is unchanged
_screenshots: ContextVar[Path | None] = ContextVar("screenshots", default=None)
# block pages the current task met, from the same per-step check
_blocks: ContextVar[list | None] = ContextVar("blocks", default=None)
_node_plan = nodes.node_plan


async def _plan_with_screenshot(state, config):
    folder = _screenshots.get()
    blocks = _blocks.get()
    session, _, _ = _resources(config)
    if blocks is not None:
        block = await _block(session)
        if block:
            blocks.append({"step": state.get("step", 0) + 1, **block})
    if folder is not None:
        try:
            await asyncio.wait_for(
                session.take_screenshot(path=str(folder / f"step{state.get('step', 0) + 1:02d}.png")),
                BROWSER_CHECK_TIMEOUT)
        except Exception as e:
            print(f"  (screenshot failed: {e!r})")
    return await _node_plan(state, config)


nodes.node_plan = _plan_with_screenshot


# ── what each node did, from its state update ────────────────────────────────
def _describe(node: str, update: dict | None) -> dict:
    update = update or {}
    if node == "respond":
        return {"routed": "answer" if update.get("messages") else "task"}
    if node == "make_plan":
        return {"plan": update.get("plan"), "summary": update.get("plan_summary")}
    if node == "plan":
        d = update["last_decision"]
        # a revised plan is in the action as `revised_plan`; "plan" says it was taken
        return {"step": update.get("step"), "action": d.model_dump(exclude_none=True, exclude={"reasoning"}),
                "reasoning": d.reasoning} | ({"plan": update["plan"]} if "plan" in update else {})
    if node == "analyse":
        if "last_decision" in update:  # a loop guard swapped in a stop
            return {"verdict": "forced_stop", "action": update["last_decision"].model_dump(exclude_none=True)}
        if update.get("scratch"):
            return {"verdict": "rejected", "reason": update["scratch"][-1].get("outcome")}
        return {"verdict": "passed"}
    if node == "execute":
        entry = (update.get("scratch") or [{}])[-1]
        result = update.get("last_result")
        return {"action": entry.get("action"), "outcome": entry.get("outcome"),
                "error": getattr(result, "error", None), "done": update.get("done")}
    # resolve and the error handler: how the task ended, if it did
    return {k: update[k] for k in ("status", "last_error") if k in update} | (
        {"message": update["messages"][-1]["content"]} if update.get("messages") else {})


# ── scoring ──────────────────────────────────────────────────────────────────
# gave_up covers two endings. a loop guard or an unbuildable action stops the
# task with one of these stock messages: no answer, a failure. otherwise the
# planner stopped itself with its own answer, which can be right: "no product
# meets the conditions", backed by the screenshots, is the correct answer when
# a reference has gone stale (Amazon--6). those are reviewed like ok answers
_STOCK_STOPS = {nodes.STUCK_MESSAGE, nodes.BAD_ACTION_MESSAGE, nodes.BOT_CHECK_MESSAGE}


def _needs_review(status: str | None, answer: str | None) -> bool:
    if status == "gave_up":
        text = (answer or "").strip()
        return bool(text) and text not in _STOCK_STOPS
    return status == "ok" or status in RETRY_STATUSES


def _notes(r: dict) -> str:
    note = _ending_note(r)
    if r["attempt"]:
        note = f"retry {r['attempt']}, earlier attempts in tasks.jsonl. {note}".strip()
    return note


def _ending_note(r: dict) -> str:
    if r["status"] == "blocked":
        first = r["blocked"][0]
        return f"blocked by the site ({first['kind']} at step {first['step']}): re-run, don't score"
    if r["status"] == "setup_failed":
        return f"setup failed before the agent started ({r['setup_error']}): re-run, don't score"
    if r["status"] in ("llm_failed", "rate_limited"):
        return f"the model API failed ({r['status']}), not the agent: re-run, don't score"
    if r["status"] == "timed_out":
        return f"a step timed out ({r['error']}), not the agent's decision: re-run, don't score"
    if r["status"] == "gave_up":
        if _needs_review(r["status"], r["answer"]):
            return "gave up with its own answer: review it (it can be right, e.g. nothing meets the conditions)"
        return "status gave_up: stopped with a stock message, no answer"
    return "" if r["status"] == "ok" else f"status {r['status']}: no answer"


# ── one task ─────────────────────────────────────────────────────────────────
class _SetupFailed(Exception):
    # opening the task's site (or setting Amazon's ZIP) raised before the agent ran
    pass


async def run_task(task: dict, repeat: int, attempt: int, steps_file, out: Path) -> dict:
    calls: list = []
    token = _usage.set(calls)
    shots = out / "screenshots" / (f"{task['id'].replace(' ', '_')}_r{repeat}"
                                   + (f"_a{attempt}" if attempt else ""))
    shots.mkdir(parents=True)
    shots_token = _screenshots.set(shots)
    blocks: list = []
    blocks_token = _blocks.set(blocks)
    llm_errors: list = []
    llm_errors_token = _llm_errors.set(llm_errors)
    session = None  # stays None if the browser fails to start
    start = time.perf_counter()
    state = {"last": start, "seen": 0, "errors_seen": 0, "steps": 0}
    status, answer, error, setup_error, setup_seconds = None, None, None, None, None

    def record(node: str, update: dict | None):
        now = time.perf_counter()
        new = calls[state["seen"]:]
        state["seen"] = len(calls)
        info = _describe(node, update)
        if len(llm_errors) > state["errors_seen"]:
            info["llm_errors"] = llm_errors[state["errors_seen"]:]
            state["errors_seen"] = len(llm_errors)
        state["steps"] = max(state["steps"], info.get("step") or 0)
        steps_file.write(json.dumps({
            "task_id": task["id"], "repeat": repeat, "attempt": attempt, "node": node,
            "t": round(now - start, 2), "seconds": round(now - state["last"], 2),
            **info, **_tally(new),
        }, default=str) + "\n")
        steps_file.flush()
        state["last"] = now

    try:
        # setup is caught on its own: a page-load timeout here (navigate_to
        # gives up after about 30 s) is a TimeoutError too, and used to be
        # recorded as the 600 s task cap. the agent never started, so it's
        # setup_failed, re-run rather than scored. that includes the browser
        # failing to start, which used to crash the whole run
        try:
            session, tools, file_system = await _start_resources(**BROWSER)
            # time the task from a running browser, as before
            start = time.perf_counter()
            state["last"] = start
            await session.navigate_to(task["web"])
            if task["web_name"] == "Amazon":
                setup_error = await _set_us_delivery(session, task["web"])
                if setup_error:
                    print(f"  ({setup_error}; running with the IP's region)")
        except Exception as e:
            setup_error = repr(e)
            raise _SetupFailed from e
        # the first node's seconds start here, not at browser start: they
        # used to include opening the site and Amazon's ZIP, which read as a
        # slow respond (up to 50 s on Amazon). the task's total still counts setup
        state["last"] = time.perf_counter()
        setup_seconds = round(state["last"] - start, 1)
        workflow = build_agent()
        # evaluation never waits for a person: the approve node is in the graph,
        # as in every run, but on the auto policy
        config = _run_config(session, tools, file_system, f"{task['id']}#{repeat}.{attempt}",
                             approval="auto")
        query = PROMPT.format(ques=task["ques"], web=task["web"])
        payload: PipelineState = {"query": query, "messages": [{"role": "user", "content": query}]}

        async def drive():
            async for chunk in workflow.astream(payload, config, stream_mode="updates"):
                for node, update in chunk.items():
                    record(node, update)

        # only the cap expiring is a harness_timeout. a TimeoutError raised
        # inside the agent is a harness_error like any other exception
        cap = asyncio.timeout(TASK_TIMEOUT)
        try:
            async with cap:
                await drive()
        except TimeoutError:
            if not cap.expired():
                raise
            status = "harness_timeout"
        else:
            values = (await workflow.aget_state(config)).values
            status = values.get("status")
            error = values.get("last_error")  # fresh thread: only set by a failure
            answer = last_assistant_reply(workflow, config)
    except _SetupFailed:
        status = "setup_failed"
        print(f"  (setup failed: {setup_error}; re-run it)")
    except Exception as e:
        status, error = "harness_error", repr(e)
    finally:
        _usage.reset(token)
        _screenshots.reset(shots_token)
        _blocks.reset(blocks_token)
        _llm_errors.reset(llm_errors_token)
        if session is not None:
            block = await _block(session)
            if block:
                blocks.append({"step": "final", **block})
            try:
                await asyncio.wait_for(session.take_screenshot(path=str(shots / "final.png")),
                                       BROWSER_CHECK_TIMEOUT)
            except Exception as e:
                print(f"  (final screenshot failed: {e!r})")
            try:
                await asyncio.wait_for(session.kill(), BROWSER_CHECK_TIMEOUT)
            except Exception:
                pass

    # a task the site blocked that then didn't finish says nothing about the
    # agent: it's marked to re-run, not scored. one that finished anyway keeps
    # its status, with the blocks still listed
    # the agent stops at a bot check itself (nodes.BOT_CHECK_MESSAGE), and can
    # spot ones the runner's check doesn't (Cloudflare, a captcha widget): a
    # block either way
    if answer == nodes.BOT_CHECK_MESSAGE and not blocks:
        blocks.append({"step": "agent", "kind": "bot check (agent stopped)", "url": ""})
    if blocks and status != "ok":
        status = "blocked"
    return {
        "task_id": task["id"], "repeat": repeat, "attempt": attempt, "web_name": task["web_name"],
        "ques": task["ques"], "reference": task["reference"],
        "reference_type": task["reference_type"],
        "answer": answer, "status": status, "error": error, "setup_error": setup_error,
        "llm_errors": llm_errors or None,
        "blocked": blocks or None,
        # anything else (step limit, timeout, failure, a loop-guard stop) ended
        # without an answer: a failure, as in WebVoyager. answers still need judging
        "success": None if _needs_review(status, answer) else 0,
        "steps": state["steps"], "seconds": round(time.perf_counter() - start, 1),
        "setup_seconds": setup_seconds,
        **_tally(calls),
    }


# ── the run ──────────────────────────────────────────────────────────────────
def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def _config(args, tasks: list) -> dict:
    def version(pkg):
        try:
            return importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            return None
    return {
        "label": args.label, "started": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--", "*.py", "gui")),
        "task_ids": [t["id"] for t in tasks], "repeats": args.repeats, "retries": args.retries,
        "retry_statuses": RETRY_STATUSES,
        "tasks_sha256": hashlib.sha256(TASKS.read_bytes()).hexdigest(),
        "planner": [llm.PLANNER_PROVIDER, llm.PLANNER_MODELS.get(llm.PLANNER_PROVIDER)],
        "claude_effort": os.getenv("CLAUDE_EFFORT", "medium"),
        "use_vision": llm.USE_VISION, "vision": [llm.VISION_PROVIDER, llm.VISION_MODEL],
        "extraction": [llm.EXTRACTION_PROVIDER, llm.EXTRACTION_MODEL],
        "price_tuning": llm.PRICE_TUNING, "max_steps": MAX_STEPS, "task_timeout": TASK_TIMEOUT, "approval": "auto",
        "amazon_us_zip": US_ZIP, "amazon_gap": args.amazon_gap,
        "browser_path": os.getenv("BROWSER_PATH"), "browser": BROWSER, "page_ready": {"timeout": PAGE_READY_TIMEOUT, "about_blank_grace": ABOUT_BLANK_GRACE}, "prices_usd_per_mtok": PRICES,
        "python": platform.python_version(),
        "packages": {p: version(p) for p in ("browser-use", "langgraph", "langchain-core", "pydantic")},
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="run", help="name for the results folder")
    parser.add_argument("--ids", help="comma-separated task ids (default: all)")
    parser.add_argument("--limit", type=int, help="only the first N tasks")
    parser.add_argument("--repeats", type=int, default=1, help="runs per task")
    parser.add_argument("--retries", type=int, default=1,
                        help=f"times to re-run a task that ended {'/'.join(RETRY_STATUSES)}, "
                             "after the planned tasks (0 = never)")
    parser.add_argument("--amazon-gap", type=float, default=AMAZON_GAP,
                        help="seconds between the end of one Amazon task and the start of the next")
    args = parser.parse_args()

    tasks = [json.loads(line) for line in open(TASKS)]
    if args.ids:
        wanted = [i.strip() for i in args.ids.split(",")]
        tasks = [t for t in tasks if t["id"] in wanted]
        missing = set(wanted) - {t["id"] for t in tasks}
        if missing:
            sys.exit(f"unknown task ids: {sorted(missing)}")
    if args.limit:
        tasks = tasks[:args.limit]

    out = RESULTS / f"{datetime.now():%Y%m%d-%H%M}_{args.label}"
    out.mkdir(parents=True, exist_ok=False)
    config = _config(args, tasks)
    (out / "config.json").write_text(json.dumps(config, indent=2))
    if config["git_dirty"]:
        print("warning: uncommitted code changes; this run can't be traced to a commit")

    attempts = []  # every attempt, in the order run
    latest = {}  # (task id, repeat) -> its last attempt: what summary.csv scores
    amazon_ended = None  # when the last Amazon task finished
    with open(out / "steps.jsonl", "w") as steps_file, open(out / "tasks.jsonl", "w") as tasks_file:

        async def attempt_task(task, repeat, attempt, label):
            nonlocal amazon_ended
            print(f"\n=== {label} {task['id']} (repeat {repeat}"
                  + (f", retry {attempt})" if attempt else ")") + " ===")
            amazon = task["web_name"] == "Amazon"
            if amazon and amazon_ended is not None:
                wait = args.amazon_gap - (time.monotonic() - amazon_ended)
                if wait > 0:
                    print(f"  (waiting {wait:.0f}s since the last Amazon task)")
                    await asyncio.sleep(wait)
            result = await run_task(task, repeat, attempt, steps_file, out)
            if amazon:
                amazon_ended = time.monotonic()
            attempts.append(result)
            latest[(task["id"], repeat)] = result
            tasks_file.write(json.dumps(result, default=str) + "\n")
            tasks_file.flush()
            for b in result["blocked"] or []:
                print(f"  (blocked at step {b['step']}: {b['kind']}, {b['url'][:80]})")
            print(f"=== {result['status']} | {result['steps']} steps | {result['seconds']}s | "
                  f"${result['cost_usd']} | answer: {str(result['answer'])[:100]}")

        total = len(tasks) * args.repeats
        for repeat in range(args.repeats):
            for task in tasks:
                await attempt_task(task, repeat, 0, f"[{len(attempts) + 1}/{total}]")

        by_id = {t["id"]: t for t in tasks}
        for attempt in range(1, args.retries + 1):
            again = [key for key, r in latest.items() if r["status"] in RETRY_STATUSES]
            if not again:
                break
            print(f"\n── retry {attempt}: {len(again)} task(s) ended {'/'.join(RETRY_STATUSES)} ──")
            for i, (task_id, repeat) in enumerate(again, 1):
                await attempt_task(by_id[task_id], repeat, attempt, f"[retry {attempt}: {i}/{len(again)}]")

    results = list(latest.values())

    columns = ["task_id", "repeat", "attempt", "web_name", "ques", "reference", "reference_type", "answer",
               "status", "steps", "seconds", "calls", "input_tokens", "output_tokens", "cost_usd"]
    with open(out / "summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns + ["success", "notes"])
        for r in results:
            writer.writerow([r[c] for c in columns] + ["" if r["success"] is None else r["success"], _notes(r)])

    # cost counts every attempt, retries included: they were paid for
    costs = [r["cost_usd"] for r in attempts if r["cost_usd"] is not None]
    retried = len(attempts) - len(results)
    print(f"\n{len(results)} runs, {retried} retry attempts | final statuses: "
          f"{ {s: sum(r['status'] == s for r in results) for s in {r['status'] for r in results}} } | "
          f"total ${sum(costs):.2f} | results in {out.relative_to(ROOT)}")
    rerun = sorted({r["task_id"] for r in results if r["status"] in RETRY_STATUSES})
    if rerun:
        print(f"still {'/'.join(RETRY_STATUSES)} after {args.retries} retries, "
              f"re-run with: --ids \"{','.join(rerun)}\"")


if __name__ == "__main__":
    asyncio.run(main())
