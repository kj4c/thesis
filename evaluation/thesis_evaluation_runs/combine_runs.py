# combine each version's repeats and re-runs into one results folder laid out
# as if run_eval.py had run all 3 repeats at once (--repeats 3, re-runs as later
# attempts), plus a folder with just the combined summaries.
# usage: python combine_runs.py <evaluation/results>
# it built this folder's first version in evaluation/results/combined. the marks,
# tags and notes here were then hand-checked, so running it again rebuilds from
# the original runs without them: don't run it over this folder.
import csv, json, os, sys, shutil
from pathlib import Path

RES = Path(sys.argv[1])
OUT = RES / "combined"
OUT_SUM = RES / "combined_summaries"

# version -> list of (folder, {(task_id or None): combined repeat} or repeat offset)
# a "main" source maps its own repeat r to r + offset; a "rerun" source maps
# each task to the combined repeat whose slot it filled
VERSIONS = {
    "baseline": [("20261016-1955_baseline_first", 0), ("20261016-2042_baseline_second", 1),
                 ("20261016-2130_baseline_third", 2)],
    "versionA": [("20261025-1918_versionA", 0),
                 ("20261026-2025_versionA_reruns", {"Amazon--12": 2, "Amazon--27": 1, "Google Search--12": 1}),
                 ("20261027-2008_versionA_reruns2", {"Google Search--12": 2}),
                 ("20261109-2200_versionA_reruns3", {"Amazon--12": 0})],
    "versionB": [("20261031-1856_versionB", 0),
                 ("20261031-2208_versionB_reruns", {"Amazon--12": 2})],
    "versionC": [("20261101-1545_versionC", 0), ("20261101-1654_versionC_more", 1)],
}
for v in ("dom", "hybrid", "vision", "noverifier"):
    src = []
    for d in sorted(p.name for p in RES.iterdir() if p.is_dir() and "_compare_r" in p.name):
        rep, ver = d.split("_compare_r")[1].split("_")[:2]
        if ver != v:
            continue
        if d.endswith("_reruns"):
            ids = json.loads((RES / d / "config.json").read_text())["task_ids"]
            src.append((d, {t: int(rep) - 1 for t in ids}))
        else:
            src.append((d, int(rep) - 1))
    VERSIONS[f"compare_{v}"] = src

SUM_COLS = ["task_id", "repeat", "attempt", "web_name", "ques", "reference", "reference_type", "answer",
            "status", "steps", "seconds", "calls", "input_tokens", "output_tokens", "cost_usd", "success", "notes"]


def read_csv(p):
    if not p.exists():
        return []
    return list(csv.DictReader(open(p, newline="", encoding="utf-8-sig")))


def jsonl(p):
    return [json.loads(l) for l in open(p)] if p.exists() else []


def shot_name(task_id, rep, att):
    return f"{task_id.replace(' ', '_')}_r{rep}" + (f"_a{att}" if att else "")


def link_tree(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_dir():
            link_tree(f, dst / f.name)
        else:
            os.link(f, dst / f.name)


report = {}
for version, sources in VERSIONS.items():
    first = sources[0][0]
    name = f"{first.split('_')[0]}_{version}"
    out = OUT / name
    if out.exists():
        shutil.rmtree(out)
    (out / "screenshots").mkdir(parents=True)

    configs = []
    task_order = None
    attempts = []   # dicts with keys: task, rep, src, src_rep, src_att, record
    slot_next = {}  # (task, rep) -> next combined attempt number
    rows = {}       # (task, rep) -> (summary row, src folder, combined attempt)
    for folder, mapping in sources:
        d = RES / folder
        cfg = json.loads((d / "config.json").read_text())
        configs.append((folder, cfg))
        if task_order is None:
            task_order = cfg["task_ids"]

        def combined_rep(task, r):
            return r + mapping if isinstance(mapping, int) else mapping[task]

        att_map = {}  # (task, src_rep, src_att) -> combined attempt
        for t in jsonl(d / "tasks.jsonl"):
            r = combined_rep(t["task_id"], t["repeat"])
            key = (t["task_id"], r)
            a = slot_next.get(key, 0)
            slot_next[key] = a + 1
            att_map[(t["task_id"], t["repeat"], t.get("attempt", 0))] = a
            attempts.append(dict(task=t["task_id"], rep=r, att=a, src=folder,
                                 src_rep=t["repeat"], src_att=t.get("attempt", 0), record=t))
        for s in read_csv(d / "summary.csv"):
            r = combined_rep(s["task_id"], int(s["repeat"]))
            a = att_map[(s["task_id"], int(s["repeat"]), int(s.get("attempt") or 0))]
            rows.setdefault((s["task_id"], r), []).append((s, folder, a))
        # steps, screenshots after attempts are numbered
        shots = d / "screenshots"
        if shots.exists():
            for sub in shots.iterdir():
                if not sub.is_dir():
                    continue
                base, _, att = sub.name.rpartition("_a") if "_a" in sub.name.split("_r")[-1] else (sub.name, "", "0")
                tid_us, _, srep = base.rpartition("_r")
                task = next(t for t in task_order + cfg["task_ids"] if t.replace(" ", "_") == tid_us)
                r = combined_rep(task, int(srep))
                a = att_map[(task, int(srep), int(att))]
                link_tree(sub, out / "screenshots" / shot_name(task, r, a))

    # attempt order as one run would write it: every first attempt, repeat by
    # repeat in task order, then each round of retries
    pos = {t: i for i, t in enumerate(task_order)}
    attempts.sort(key=lambda x: (x["att"], x["rep"], pos[x["task"]]))

    # steps.jsonl: per attempt, in the same order
    steps_by = {}
    for folder, mapping in sources:
        for st in jsonl(RES / folder / "steps.jsonl"):
            steps_by.setdefault((folder, st["task_id"], st["repeat"], st.get("attempt", 0)), []).append(st)
    with open(out / "steps.jsonl", "w") as sf, open(out / "tasks.jsonl", "w") as tf:
        for x in attempts:
            rec = dict(x["record"])
            rec["repeat"], rec["attempt"] = x["rep"], x["att"]
            rec = {k: rec[k] for k in ["task_id", "repeat", "attempt"]} | {k: v for k, v in rec.items() if k not in ("task_id", "repeat", "attempt")}
            rec["combined_from"] = f"{x['src']} (repeat {x['src_rep']}, attempt {x['src_att']})"
            tf.write(json.dumps(rec) + "\n")
            for st in steps_by.get((x["src"], x["task"], x["src_rep"], x["src_att"]), []):
                st = dict(st)
                st["repeat"], st["attempt"] = x["rep"], x["att"]
                st = {k: st[k] for k in ["task_id", "repeat", "attempt"]} | {k: v for k, v in st.items() if k not in ("task_id", "repeat", "attempt")}
                sf.write(json.dumps(st) + "\n")

    # summary: repeat by repeat, in task order
    main = sources[0][0]
    keys = sorted(rows, key=lambda k: (k[1], pos[k[0]]))
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(SUM_COLS)
        for k in keys:
            entries = rows[k]
            scored = [e for e in entries if e[0]["success"].strip() != ""]
            chosen = scored[-1] if scored else entries[-1]
            s, folder, a = chosen
            parts = []
            if len(scored) > 1:
                raise SystemExit(f"{version} {k}: scored in more than one source: {[e[1] for e in scored]}")
            if chosen is not entries[0]:
                before = "; ".join(f"{e[1]} attempt {e[2]}: {e[0]['status']}" for e in entries[:entries.index(chosen)])
                parts.append(f"combined: from re-run {folder} (attempt {a}), replacing the unscored earlier attempt(s) ({before})")
            for e in entries[entries.index(chosen) + 1:]:
                parts.append(f"combined: later re-run {e[1]} (attempt {e[2]}, {e[0]['status']}) not used: {e[0]['notes'][:300]}")
            notes = " | ".join(parts + ([s["notes"]] if s.get("notes") else []))
            vals = dict(s, repeat=k[1], attempt=a, notes=notes)
            w.writerow([vals.get(c, "") for c in SUM_COLS])

    # config: the main run's, with the sources listed
    base_cfg = dict(configs[0][1])
    varying = sorted({k for _, c in configs for k in set(c) | set(base_cfg)
                      if c.get(k) != base_cfg.get(k)} - {"label", "started", "task_ids", "repeats", "retries"})
    base_cfg.update(label=version, repeats=3, task_ids=task_order,
                    combined_from=[{"folder": fo, "label": c.get("label"), "started": c.get("started"),
                                    "git_commit": c.get("git_commit"), "git_dirty": c.get("git_dirty"),
                                    "repeats": c.get("repeats"), "task_ids": c.get("task_ids")} for fo, c in configs],
                    combined_note="Made 12 Nov 2026 from the folders in combined_from, laid out as one --repeats 3 run: "
                                  "each source's repeat is renumbered, and each re-run is the next attempt of the "
                                  "repeat it filled. Fields that differ between sources: " + (", ".join(varying) or "none"))
    (out / "config.json").write_text(json.dumps(base_cfg, indent=2))

    OUT_SUM.mkdir(exist_ok=True)
    shutil.copy(out / "summary.csv", OUT_SUM / f"{name}_summary.csv")

    succ = [float(e[0]["success"]) for k in keys for e in rows[k] if e[0]["success"].strip() != ""]
    report[name] = dict(rows=len(keys), scored=len(succ), score=sum(succ), attempts=len(attempts), varying=varying)

print(json.dumps(report, indent=1))
