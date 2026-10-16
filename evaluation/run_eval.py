# run the agent on the WebVoyager subset (tasks.jsonl) and log every step.
#
# goes through the same graph as mvp.py: no approval, so nothing waits on a
# human. each task gets a fresh browser opened on the task's site and a fresh
# thread, and is phrased the way WebVoyager phrases it to its own agent.
#
# writes evaluation/results/<time>_<label>/:
#   config.json   the setup the run used (models, flags, git commit, versions)
#   steps.jsonl   one line per graph node: what it decided or did, how long it
#                 took, and the tokens and cost of the model calls inside it
#   tasks.jsonl   one line per task: answer, status, steps, time, tokens, cost
#   summary.csv   the same per task, with a `success` column to score. a task
#                 that didn't end with status ok is filled in as 0 already,
#                 except status blocked (the site showed a captcha, checkpoint
#                 or sign-in wall): left blank, to re-run rather than score;
#                 and a gave_up with the agent's own answer: left blank, to review
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
from state import MAX_STEPS, PipelineState, _resources, _run_config, _start_resources

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


async def _block(session) -> dict | None:
    try:
        page = await session.must_get_current_page()
        kind = await page.evaluate(_BLOCK_JS)
        return {"kind": kind, "url": await page.evaluate("() => location.href")} if kind else None
    except Exception:
        return None  # mid-load, or no page: not evidence of a block


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


def _recording(build):
    def wrapped(provider, model):
        chat = build(provider, model)
        original = chat.ainvoke

        async def ainvoke(messages, output_format=None, **kwargs):
            response = await original(messages, output_format=output_format, **kwargs)
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
            await session.take_screenshot(path=str(folder / f"step{state.get('step', 0) + 1:02d}.png"))
        except Exception as e:
            print(f"  (screenshot failed: {e!r})")
    return await _node_plan(state, config)


nodes.node_plan = _plan_with_screenshot


# ── what each node did, from its state update ────────────────────────────────
def _describe(node: str, update: dict | None) -> dict:
    update = update or {}
    if node == "respond":
        return {"routed": "answer" if update.get("messages") else "task"}
    if node == "plan":
        d = update["last_decision"]
        return {"step": update.get("step"), "action": d.model_dump(exclude_none=True, exclude={"reasoning"}),
                "reasoning": d.reasoning}
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
    return {k: update[k] for k in ("status",) if k in update} | (
        {"message": update["messages"][-1]["content"]} if update.get("messages") else {})


# ── scoring ──────────────────────────────────────────────────────────────────
# gave_up covers two endings. a loop guard or an unbuildable action stops the
# task with one of these stock messages: no answer, a failure. otherwise the
# planner stopped itself with its own answer, which can be right: "no product
# meets the conditions", backed by the screenshots, is the correct answer when
# a reference has gone stale (Amazon--6). those are reviewed like ok answers
_STOCK_STOPS = {nodes.STUCK_MESSAGE, nodes.BAD_ACTION_MESSAGE}


def _needs_review(status: str | None, answer: str | None) -> bool:
    if status == "gave_up":
        text = (answer or "").strip()
        return bool(text) and text not in _STOCK_STOPS
    return status in ("ok", "blocked")


def _notes(r: dict) -> str:
    if r["status"] == "blocked":
        first = r["blocked"][0]
        return f"blocked by the site ({first['kind']} at step {first['step']}): re-run, don't score"
    if r["status"] == "gave_up":
        if _needs_review(r["status"], r["answer"]):
            return "gave up with its own answer: review it (it can be right, e.g. nothing meets the conditions)"
        return "status gave_up: stopped with a stock message, no answer"
    return "" if r["status"] == "ok" else f"status {r['status']}: no answer"


# ── one task ─────────────────────────────────────────────────────────────────
async def run_task(task: dict, repeat: int, steps_file, out: Path) -> dict:
    calls: list = []
    token = _usage.set(calls)
    shots = out / "screenshots" / f"{task['id'].replace(' ', '_')}_r{repeat}"
    shots.mkdir(parents=True)
    shots_token = _screenshots.set(shots)
    blocks: list = []
    blocks_token = _blocks.set(blocks)
    session, tools, file_system = await _start_resources(**BROWSER)
    start = time.perf_counter()
    state = {"last": start, "seen": 0, "steps": 0}
    status, answer, error, setup_error = None, None, None, None

    def record(node: str, update: dict | None):
        now = time.perf_counter()
        new = calls[state["seen"]:]
        state["seen"] = len(calls)
        info = _describe(node, update)
        state["steps"] = max(state["steps"], info.get("step") or 0)
        steps_file.write(json.dumps({
            "task_id": task["id"], "repeat": repeat, "node": node,
            "t": round(now - start, 2), "seconds": round(now - state["last"], 2),
            **info, **_tally(new),
        }, default=str) + "\n")
        steps_file.flush()
        state["last"] = now

    try:
        await session.navigate_to(task["web"])
        if task["web_name"] == "Amazon":
            setup_error = await _set_us_delivery(session, task["web"])
            if setup_error:
                print(f"  ({setup_error}; running with the IP's region)")
        workflow = build_agent()
        config = _run_config(session, tools, file_system, f"{task['id']}#{repeat}")
        query = PROMPT.format(ques=task["ques"], web=task["web"])
        payload: PipelineState = {"query": query, "messages": [{"role": "user", "content": query}]}

        async def drive():
            async for chunk in workflow.astream(payload, config, stream_mode="updates"):
                for node, update in chunk.items():
                    record(node, update)

        await asyncio.wait_for(drive(), TASK_TIMEOUT)
        status = (await workflow.aget_state(config)).values.get("status")
        answer = last_assistant_reply(workflow, config)
    except asyncio.TimeoutError:
        status = "harness_timeout"
    except Exception as e:
        status, error = "harness_error", repr(e)
    finally:
        _usage.reset(token)
        _screenshots.reset(shots_token)
        _blocks.reset(blocks_token)
        block = await _block(session)
        if block:
            blocks.append({"step": "final", **block})
        try:
            await session.take_screenshot(path=str(shots / "final.png"))
        except Exception as e:
            print(f"  (final screenshot failed: {e!r})")
        try:
            await session.kill()
        except Exception:
            pass

    # a task the site blocked that then didn't finish says nothing about the
    # agent: it's marked to re-run, not scored. one that finished anyway keeps
    # its status, with the blocks still listed
    if blocks and status != "ok":
        status = "blocked"
    return {
        "task_id": task["id"], "repeat": repeat, "web_name": task["web_name"],
        "ques": task["ques"], "reference": task["reference"],
        "reference_type": task["reference_type"],
        "answer": answer, "status": status, "error": error, "setup_error": setup_error,
        "blocked": blocks or None,
        # anything else (step limit, timeout, failure, a loop-guard stop) ended
        # without an answer: a failure, as in WebVoyager. answers still need judging
        "success": None if _needs_review(status, answer) else 0,
        "steps": state["steps"], "seconds": round(time.perf_counter() - start, 1),
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
        "task_ids": [t["id"] for t in tasks], "repeats": args.repeats,
        "tasks_sha256": hashlib.sha256(TASKS.read_bytes()).hexdigest(),
        "planner": [llm.PLANNER_PROVIDER, llm.PLANNER_MODELS.get(llm.PLANNER_PROVIDER)],
        "claude_effort": os.getenv("CLAUDE_EFFORT", "medium"),
        "use_vision": llm.USE_VISION, "vision": [llm.VISION_PROVIDER, llm.VISION_MODEL],
        "extraction": [llm.EXTRACTION_PROVIDER, llm.EXTRACTION_MODEL],
        "price_tuning": llm.PRICE_TUNING, "max_steps": MAX_STEPS, "task_timeout": TASK_TIMEOUT,
        "amazon_us_zip": US_ZIP, "amazon_gap": args.amazon_gap,
        "browser_path": os.getenv("BROWSER_PATH"), "browser": BROWSER, "prices_usd_per_mtok": PRICES,
        "python": platform.python_version(),
        "packages": {p: version(p) for p in ("browser-use", "langgraph", "langchain-core", "pydantic")},
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="run", help="name for the results folder")
    parser.add_argument("--ids", help="comma-separated task ids (default: all)")
    parser.add_argument("--limit", type=int, help="only the first N tasks")
    parser.add_argument("--repeats", type=int, default=1, help="runs per task")
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

    results = []
    amazon_ended = None  # when the last Amazon task finished
    with open(out / "steps.jsonl", "w") as steps_file, open(out / "tasks.jsonl", "w") as tasks_file:
        total = len(tasks) * args.repeats
        for repeat in range(args.repeats):
            for task in tasks:
                n = len(results) + 1
                print(f"\n=== [{n}/{total}] {task['id']} (repeat {repeat}) ===")
                amazon = task["web_name"] == "Amazon"
                if amazon and amazon_ended is not None:
                    wait = args.amazon_gap - (time.monotonic() - amazon_ended)
                    if wait > 0:
                        print(f"  (waiting {wait:.0f}s since the last Amazon task)")
                        await asyncio.sleep(wait)
                result = await run_task(task, repeat, steps_file, out)
                if amazon:
                    amazon_ended = time.monotonic()
                results.append(result)
                tasks_file.write(json.dumps(result, default=str) + "\n")
                tasks_file.flush()
                for b in result["blocked"] or []:
                    print(f"  (blocked at step {b['step']}: {b['kind']}, {b['url'][:80]})")
                print(f"=== {result['status']} | {result['steps']} steps | {result['seconds']}s | "
                      f"${result['cost_usd']} | answer: {str(result['answer'])[:100]}")

    columns = ["task_id", "repeat", "web_name", "ques", "reference", "reference_type", "answer",
               "status", "steps", "seconds", "calls", "input_tokens", "output_tokens", "cost_usd"]
    with open(out / "summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns + ["success", "notes"])
        for r in results:
            writer.writerow([r[c] for c in columns] + ["" if r["success"] is None else r["success"], _notes(r)])

    costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    print(f"\n{len(results)} runs | statuses: "
          f"{ {s: sum(r['status'] == s for r in results) for s in {r['status'] for r in results}} } | "
          f"total ${sum(costs):.2f} | results in {out.relative_to(ROOT)}")
    blocked = sorted({r["task_id"] for r in results if r["status"] == "blocked"})
    if blocked:
        print(f"blocked by the site, re-run with: --ids \"{','.join(blocked)}\"")


if __name__ == "__main__":
    asyncio.run(main())
