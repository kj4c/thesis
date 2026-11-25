# the report's charts, from the scored runs in thesis_evaluation_runs/. writes
# PNGs next to this file; the numbers behind each are in tables.md (analyse.py).
# needs matplotlib (pip install matplotlib):
#   python evaluation/thesis_evaluation_runs/analysis/charts.py
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

RUNS = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent

VERSIONS = [
    ("20261016-1955_baseline", "Baseline"), ("20261025-1918_versionA", "A"),
    ("20261031-1856_versionB", "B"), ("20261101-1545_versionC", "C"),
    ("20261107-2035_compare_dom", "DOM"), ("20261107-2129_compare_hybrid", "Hybrid"),
    ("20261107-2225_compare_vision", "Vision\nonly"), ("20261107-2326_compare_noverifier", "No\nverifier"),
]
# validated categorical slots, in fixed order (light mode, surface #fcfcfb)
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
SEQ = ["#cde2fb", "#86b6ef", "#3987e5", "#184f95"]  # 0..3 repeats passed
SURFACE, TEXT, TEXT2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": TEXT2, "xtick.color": TEXT2,
    "ytick.color": TEXT2, "text.color": TEXT, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "axes.spines.top": False, "axes.spines.right": False,
})


def rows(folder):
    return list(csv.DictReader(open(RUNS / folder / "summary.csv")))


def save(fig, name):
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print("wrote", OUT / name)


def scores_by_site():
    # share of answers passed, Google and Amazon side by side, per version
    names = [s for _, s in VERSIONS]
    g = [sum(int(r["success"]) for r in rows(f) if r["web_name"] == "Google Search") / 45 * 100 for f, _ in VERSIONS]
    a = [sum(int(r["success"]) for r in rows(f) if r["web_name"] == "Amazon") / 42 * 100 for f, _ in VERSIONS]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    x = range(len(names)); w = 0.36
    bars_g = ax.bar([i - w / 2 - 0.01 for i in x], g, w, color=SLOTS[0], label="Google Search (45)")
    bars_a = ax.bar([i + w / 2 + 0.01 for i in x], a, w, color=SLOTS[1], label="Amazon (42)")
    for bars in (bars_g, bars_a):
        for b in bars:
            ax.annotate(f"{b.get_height():.0f}", (b.get_x() + b.get_width() / 2, b.get_height()),
                        ha="center", va="bottom", fontsize=8, color=TEXT2, xytext=(0, 2), textcoords="offset points")
    ax.axvline(3.5, color=GRID, lw=1)
    ax.text(1.5, 108, "versions (DOM agent)", ha="center", color=TEXT2, fontsize=9)
    ax.text(5.5, 108, "perception comparison", ha="center", color=TEXT2, fontsize=9)
    ax.set_xticks(list(x), names)
    ax.set_ylim(0, 112); ax.set_ylabel("answers passed (%)")
    ax.yaxis.grid(True, color=GRID, lw=0.8); ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=9, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.02))
    save(fig, "scores_by_site.png")


def tags_by_version():
    # the 0s of each version, stacked by failure tag; the five commonest, the rest as "other"
    top = ["condition_missed", "overview_source", "stuck", "wrong_fact", "went_around"]
    names = [s for _, s in VERSIONS]
    counts = {t: [] for t in top + ["other"]}
    for f, _ in VERSIONS:
        zs = [r["failure_tag"] for r in rows(f) if r["success"] == "0"]
        for t in top:
            counts[t].append(zs.count(t))
        counts["other"].append(sum(1 for z in zs if z not in top))
    fig, ax = plt.subplots(figsize=(8, 3.8))
    bottom = [0] * len(names)
    for i, t in enumerate(top + ["other"]):
        ax.bar(names, counts[t], 0.62, bottom=bottom, color=SLOTS[i], label=t.replace("_", " "),
               edgecolor=SURFACE, linewidth=2)
        bottom = [b + c for b, c in zip(bottom, counts[t])]
    for i, total in enumerate(bottom):
        ax.annotate(str(total), (i, total), ha="center", va="bottom", fontsize=8, color=TEXT2,
                    xytext=(0, 2), textcoords="offset points")
    ax.axvline(3.5, color=GRID, lw=1)
    ax.set_ylabel("answers failed (of 87)")
    ax.yaxis.grid(True, color=GRID, lw=0.8); ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper right")
    save(fig, "failure_tags.png")


def cost_per_success():
    names = [s for _, s in VERSIONS]
    vals = []
    for f, _ in VERSIONS:
        r = rows(f)
        vals.append(sum(float(x["cost_usd"] or 0) for x in r) / sum(int(x["success"]) for x in r))
    fig, ax = plt.subplots(figsize=(8, 3.0))
    bars = ax.bar(names, vals, 0.55, color=SLOTS[0])
    for b in bars:
        ax.annotate(f"${b.get_height():.2f}", (b.get_x() + b.get_width() / 2, b.get_height()), ha="center",
                    va="bottom", fontsize=8, color=TEXT2, xytext=(0, 2), textcoords="offset points")
    ax.axvline(3.5, color=GRID, lw=1)
    ax.set_ylabel("cost per answer passed (USD)")
    ax.yaxis.grid(True, color=GRID, lw=0.8); ax.set_axisbelow(True)
    save(fig, "cost_per_success.png")


def score_by_task():
    # repeats passed (0-3) for every task and version
    names = [s for _, s in VERSIONS]
    data = {s: rows(f) for f, s in VERSIONS}
    tasks = sorted({r["task_id"] for r in data["Baseline"]},
                   key=lambda t: (t.startswith("Amazon"), int(t.split("--")[1])))
    fig, ax = plt.subplots(figsize=(7, 9))
    for yi, t in enumerate(tasks):
        for xi, s in enumerate(names):
            v = sum(int(r["success"]) for r in data[s] if r["task_id"] == t)
            ax.add_patch(Rectangle((xi + 0.04, yi + 0.04), 0.92, 0.92, color=SEQ[v], lw=0))
            ax.text(xi + 0.5, yi + 0.5, str(v), ha="center", va="center", fontsize=8,
                    color="#ffffff" if v >= 2 else TEXT)
    ax.set_xlim(0, len(names)); ax.set_ylim(len(tasks), 0)
    ax.set_xticks([i + 0.5 for i in range(len(names))], [n.replace("\n", " ") for n in names], rotation=30, ha="right")
    ax.set_yticks([i + 0.5 for i in range(len(tasks))], tasks)
    ax.tick_params(length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.axvline(4, color=TEXT2, lw=1)
    ax.axhline(15, color=TEXT2, lw=1)
    ax.set_title("Repeats passed out of 3", loc="left", fontsize=10, color=TEXT2)
    save(fig, "score_by_task.png")


if __name__ == "__main__":
    scores_by_site()
    tags_by_version()
    cost_per_success()
    score_by_task()
