# the counts and tables the report uses, from the scored runs in
# thesis_evaluation_runs/. writes tables.md next to this file.
# usage: python evaluation/thesis_evaluation_runs/analysis/analyse.py
import collections
import csv
import json
import statistics
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "tables.md"
# suggested judgements for the lists at the end of tables.md, filled in by hand
JUDGEMENTS = Path(__file__).resolve().parent / "judgements.csv"

VERSIONS = [  # folder, short name, group
    ("20261016-1955_baseline", "Baseline", "versions"),
    ("20261025-1918_versionA", "A", "versions"),
    ("20261031-1856_versionB", "B", "versions"),
    ("20261101-1545_versionC", "C", "versions"),
    ("20261107-2035_compare_dom", "DOM", "comparison"),
    ("20261107-2129_compare_hybrid", "Hybrid", "comparison"),
    ("20261107-2225_compare_vision", "Vision only", "comparison"),
    ("20261107-2326_compare_noverifier", "No verifier", "comparison"),
]
SITES = ("Google Search", "Amazon")
TAGS = ["condition_missed", "overview_source", "wrong_fact", "stuck", "false_claim", "went_around",
        "wrong_page", "not_in_record", "element", "harness"]
# rejections made by checks in code (nodes.node_analyse), not by the verifier model
CODE_REJECTIONS = ("done has no", "has no `", "scrolls in a row", "extract isn't available")


def load(folder):
    rows = list(csv.DictReader(open(RUNS / folder / "summary.csv")))
    steps = [json.loads(line) for line in open(RUNS / folder / "steps.jsonl")]
    return rows, steps


def pct(n, d):
    return f"{n}/{d} ({round(100 * n / d)}%)" if d else "-"


def task_order(t):
    site, n = t.split("--")
    return (SITES.index(site), int(n))


def main():
    data = {short: load(folder) for folder, short, _ in VERSIONS}
    md = ["# Evaluation tables", "",
          "Made by `analyse.py` from `thesis_evaluation_runs/` (final marks, 14 Nov). Scored answers only, "
          "except the bot-check counts, which cover every attempt (a stop always ends an unscored attempt).", ""]

    # ── scores, spread, steps, time, cost ────────────────────────────────────
    md += ["## Scores, cost and effort per version", "",
           "| Version | Score | Google | Amazon | Per repeat (r0, r1, r2) | Steps per task | Seconds per task "
           "| Cost per task | Cost per success | Google per success | Amazon per success |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for folder, short, group in VERSIONS:
        rows, _ = data[short]
        ok = lambda R: sum(int(r["success"]) for r in R)
        by_site = {s: [r for r in rows if r["web_name"] == s] for s in SITES}
        reps = [ok([r for r in rows if r["repeat"] == k]) for k in "012"]
        cost = lambda R: sum(float(r["cost_usd"] or 0) for r in R)
        per_success = lambda R: f"${cost(R) / ok(R):.2f}" if ok(R) else "-"
        md.append(f"| {short} | {pct(ok(rows), len(rows))} | {ok(by_site['Google Search'])}/{len(by_site['Google Search'])} "
                  f"| {ok(by_site['Amazon'])}/{len(by_site['Amazon'])} | {', '.join(map(str, reps))} (range {max(reps) - min(reps)}) "
                  f"| {statistics.mean(int(r['steps']) for r in rows):.1f} "
                  f"| {statistics.mean(float(r['seconds']) for r in rows):.0f} "
                  f"| ${cost(rows) / len(rows):.2f} | {per_success(rows)} "
                  f"| {per_success(by_site['Google Search'])} | {per_success(by_site['Amazon'])} |")
    md += ["", "Repeats score out of 29. Seconds and steps are per scored answer; cost counts only the scored "
           "attempt (failed calls aren't in `cost_usd`).", ""]

    # ── score by task across versions ────────────────────────────────────────
    tasks = sorted({r["task_id"] for r in data["Baseline"][0]}, key=task_order)
    md += ["## Score by task (repeats passed, out of 3)", "",
           "| Task | " + " | ".join(s for _, s, _ in VERSIONS) + " |",
           "|---|" + "---|" * len(VERSIONS)]
    for t in tasks:
        cells = [str(sum(int(r["success"]) for r in data[s][0] if r["task_id"] == t)) for _, s, _ in VERSIONS]
        md.append(f"| {t} | " + " | ".join(cells) + " |")
    md.append("")

    # ── A–C against the baseline, per task ───────────────────────────────────
    md += ["## Versions A–C against the baseline", "",
           "Tasks whose repeats passed changed from the baseline (out of 3; + better, − worse).", "",
           "| Version | Tasks better | Tasks worse | Unchanged | Net repeats |", "|---|---|---|---|---|"]
    base = {t: sum(int(r["success"]) for r in data["Baseline"][0] if r["task_id"] == t) for t in tasks}
    for s in ("A", "B", "C"):
        cur = {t: sum(int(r["success"]) for r in data[s][0] if r["task_id"] == t) for t in tasks}
        diff = [cur[t] - base[t] for t in tasks]
        md.append(f"| {s} | {sum(d > 0 for d in diff)} | {sum(d < 0 for d in diff)} | {sum(d == 0 for d in diff)} "
                  f"| {sum(diff):+d} |")
    md.append("")

    # ── failure tags ─────────────────────────────────────────────────────────
    md += ["## Failure tags (one per 0)", "",
           "Google / Amazon in each cell.", "",
           "| Tag | " + " | ".join(s for _, s, _ in VERSIONS) + " | Total |",
           "|---|" + "---|" * (len(VERSIONS) + 1)]
    for tag in TAGS:
        cells, total = [], 0
        for _, s, _ in VERSIONS:
            g = sum(1 for r in data[s][0] if r["success"] == "0" and r["failure_tag"] == tag and r["web_name"] == "Google Search")
            a = sum(1 for r in data[s][0] if r["success"] == "0" and r["failure_tag"] == tag and r["web_name"] == "Amazon")
            total += g + a
            cells.append(f"{g} / {a}" if g or a else "")
        md.append(f"| `{tag}` | " + " | ".join(cells) + f" | {total} |")
    zeros = [sum(1 for r in data[s][0] if r["success"] == "0") for _, s, _ in VERSIONS]
    md += ["| **0s** | " + " | ".join(map(str, zeros)) + f" | {sum(zeros)} |", ""]

    # ── verifier ─────────────────────────────────────────────────────────────
    md += ["## The checking step (scored attempts)", "",
           "Actions checked = every action that reached analyse; turned down by code = the checks in "
           "`nodes.node_analyse` (an incomplete action, an empty `done`, a run of scrolls, extract in vision only); "
           "by the model = the verifier.", "",
           "| Version | Actions checked | Turned down | by code | by the model | Model, on `done` | Model, on `click` |",
           "|---|---|---|---|---|---|---|"]
    model_rejections = []
    for _, s, _ in VERSIONS:
        steps = data[s][1]
        scored = {(r["task_id"], int(r["repeat"]), int(r["attempt"] or 0)) for r in data[s][0]}
        last_action, last_step = {}, {}
        n = code = model = m_done = m_click = 0
        for d in steps:
            key = (d.get("task_id"), d.get("repeat"), d.get("attempt"))
            if d["node"] == "plan":
                last_action[key] = (d.get("action") or {}).get("action")
                last_step[key] = d.get("step")
            if d["node"] != "analyse" or key not in scored:
                continue
            n += 1
            if d.get("verdict") != "rejected":
                continue
            reason = d.get("reason", "")
            if any(c in reason for c in CODE_REJECTIONS):
                code += 1
                continue
            model += 1
            act = last_action.get(key)
            m_done += act == "done"
            m_click += act == "click"
            model_rejections.append((s, d.get("task_id"), d.get("repeat"), d.get("attempt"), last_step.get(key), act, reason))
        md.append(f"| {s} | {n} | {code + model} | {code} | {model} | {m_done} | {m_click} |")
    md.append("")

    # ── bot checks ───────────────────────────────────────────────────────────
    md += ["## Bot checks the agent stopped at (every attempt)", "",
           "| Version | " + " | ".join(["Cloudflare", "Google captcha", "Amazon checkpoint", "Amazon captcha", "other"]) + " | Total |",
           "|---|---|---|---|---|---|---|"]
    for _, s, _ in VERSIONS:
        kinds = collections.Counter()
        for d in data[s][1]:
            r = d.get("reasoning") or ""
            if d["node"] == "plan" and r.startswith("stopped at a bot check ("):
                kinds[r[len("stopped at a bot check ("):-1]] += 1
        cols = [kinds.pop("cloudflare check", 0), kinds.pop("google captcha", 0),
                kinds.pop("amazon checkpoint", 0), kinds.pop("amazon captcha", 0), sum(kinds.values())]
        md.append(f"| {s} | " + " | ".join(str(c) for c in cols) + f" | {sum(cols)} |")
    md += ["", "Each stop ends that attempt as `blocked`; it's re-run and never scored.", ""]

    # ── lists for judging by hand ────────────────────────────────────────────
    judged = {}
    if JUDGEMENTS.exists():
        for j in csv.DictReader(open(JUDGEMENTS)):
            judged[(j["kind"], j["version"], j["task"], j["repeat"], j["attempt"], j["step"])] = j
    comparison = [x for x in model_rejections if x[0] in ("DOM", "Hybrid", "Vision only", "No verifier")]
    md += ["## For judging by hand", "",
           "Judgements in `judgements.csv`: suggested by Claude, checked by Ana (14 Nov).", "",
           "### Every verifier rejection in the comparison's scored attempts", ""]
    verdicts = collections.Counter(judged.get(("rejection", s, t, str(rep), str(att), str(st)), {}).get("suggested", "not judged")
                                   for s, t, rep, att, st, _, _ in comparison)
    effects = collections.Counter(judged.get(("rejection", s, t, str(rep), str(att), str(st)), {}).get("effect", "not judged")
                                  for s, t, rep, att, st, _, _ in comparison)
    md += [f"Judged: " + ", ".join(f"{k} {v}" for k, v in verdicts.most_common()) + f" (of {len(comparison)}). "
           "Effect on the scored answer: " + ", ".join(f"{k} {v}" for k, v in effects.most_common()) + ".", "",
           "| Version | Task | Repeat | Attempt | Step | Action | Reason (cut) | Suggested | Effect | Why |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for s, t, rep, att, st, act, reason in comparison:
        j = judged.get(("rejection", s, t, str(rep), str(att), str(st)), {})
        md.append(f"| {s} | {t} | {rep} | {att} | {st} | {act} | {reason.replace('gate rejected: ', '').replace('|', '/')[:160]} "
                  f"| {j.get('suggested', '')} | {j.get('effect', '')} | {j.get('why', '').replace('|', '/')} |")
    md += ["", "### No-verifier 0s a verifier might have caught", "",
           "Candidates: the no-verifier 0s tagged `false_claim`, `not_in_record` or `element`.", "",
           "| Task | Repeat | Tag | Note | Suggested | Why |", "|---|---|---|---|---|---|"]
    for r in data["No verifier"][0]:
        if r["success"] == "0" and r["failure_tag"] in ("false_claim", "not_in_record", "element"):
            j = judged.get(("noverifier", "No verifier", r["task_id"], r["repeat"], r["attempt"] or "0", ""), {})
            md.append(f"| {r['task_id']} | {r['repeat']} | `{r['failure_tag']}` | {r['notes'].replace('|', '/')[:160]} "
                      f"| {j.get('suggested', '')} | {j.get('why', '').replace('|', '/')} |")
    md.append("")

    OUT.write_text("\n".join(md))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
