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
#                 that didn't end with status ok is filled in as 0 already
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
# headless: headless chrome is more often met with bot checks and captchas
BROWSER = {"headless": False, "viewport": {"width": 1024, "height": 768}}

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
_node_plan = nodes.node_plan


async def _plan_with_screenshot(state, config):
    folder = _screenshots.get()
    if folder is not None:
        session, _, _ = _resources(config)
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


# ── one task ─────────────────────────────────────────────────────────────────
async def run_task(task: dict, repeat: int, steps_file, out: Path) -> dict:
    calls: list = []
    token = _usage.set(calls)
    shots = out / "screenshots" / f"{task['id'].replace(' ', '_')}_r{repeat}"
    shots.mkdir(parents=True)
    shots_token = _screenshots.set(shots)
    session, tools, file_system = await _start_resources(**BROWSER)
    start = time.perf_counter()
    state = {"last": start, "seen": 0, "steps": 0}
    status, answer, error = None, None, None

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
        try:
            await session.take_screenshot(path=str(shots / "final.png"))
        except Exception as e:
            print(f"  (final screenshot failed: {e!r})")
        try:
            await session.kill()
        except Exception:
            pass

    return {
        "task_id": task["id"], "repeat": repeat, "web_name": task["web_name"],
        "ques": task["ques"], "reference": task["reference"],
        "reference_type": task["reference_type"],
        "answer": answer, "status": status, "error": error,
        # anything but ok (step limit, timeout, failure) ended without an
        # answer: a failure, as in WebVoyager. ok answers still need judging
        "success": None if status == "ok" else 0,
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
    with open(out / "steps.jsonl", "w") as steps_file, open(out / "tasks.jsonl", "w") as tasks_file:
        total = len(tasks) * args.repeats
        for repeat in range(args.repeats):
            for task in tasks:
                n = len(results) + 1
                print(f"\n=== [{n}/{total}] {task['id']} (repeat {repeat}) ===")
                result = await run_task(task, repeat, steps_file, out)
                results.append(result)
                tasks_file.write(json.dumps(result, default=str) + "\n")
                tasks_file.flush()
                print(f"=== {result['status']} | {result['steps']} steps | {result['seconds']}s | "
                      f"${result['cost_usd']} | answer: {str(result['answer'])[:100]}")

    columns = ["task_id", "repeat", "web_name", "ques", "reference", "reference_type", "answer",
               "status", "steps", "seconds", "calls", "input_tokens", "output_tokens", "cost_usd"]
    with open(out / "summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns + ["success", "notes"])
        for r in results:
            notes = "" if r["status"] == "ok" else f"status {r['status']}: no answer"
            writer.writerow([r[c] for c in columns] + ["" if r["success"] is None else r["success"], notes])

    costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    print(f"\n{len(results)} runs | statuses: "
          f"{ {s: sum(r['status'] == s for r in results) for s in {r['status'] for r in results}} } | "
          f"total ${sum(costs):.2f} | results in {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
