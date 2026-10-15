# build the WebVoyager subset the evaluation runs on (writes tasks.jsonl).
#
# scope matches the MVP (§4.1): a search engine and one store.
#   - Google Search: every task with a "golden" (fixed) reference answer, minus
#     Google Search--15, which asks the agent to log into twitter with test
#     credentials. golden answers don't go stale, so these can be scored reliably
#   - Amazon: every 3rd task (ids 0, 3, …, 39) plus Amazon--16, its only golden
#     task, minus Amazon--15, which adds an item to the cart. Amazon references
#     are "possible" answers only: listings change with time and location
#
# the source data is downloaded from a pinned WebVoyager commit and checked
# against known hashes, so the subset is the same on every machine and the
# report can name the exact version. see evaluation/README.md for attribution.
#
# run from the project root:  python evaluation/make_subset.py

import hashlib
import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent / "tasks.jsonl"

# github.com/MinorJerry/WebVoyager (Apache 2.0)
WEBVOYAGER_COMMIT = "5a7896738c10bfb8b9edccce6bb0e0411f8ae569"
SOURCE_SHA256 = {
    "WebVoyager_data.jsonl": "69b19fd86c23f1a500244a3724e039aa7ca6a1223d03e11eb10e308d4f11c488",
    "reference_answer.json": "5473818408d8a19960c4f820c1e5721d218b87b547499d7281c2a0fc4267b0c4",
}

EXCLUDED = {
    "Google Search--15",  # logs into twitter with credentials
    "Amazon--15",         # adds an item to the cart
}


def _download(name: str) -> str:
    url = (f"https://raw.githubusercontent.com/MinorJerry/WebVoyager/"
           f"{WEBVOYAGER_COMMIT}/data/{name}")
    data = urllib.request.urlopen(url, timeout=30).read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256[name]:
        raise SystemExit(f"{name}: sha256 {digest} doesn't match the pinned version")
    return data.decode("utf-8")


def main():
    rows = [json.loads(line) for line in _download("WebVoyager_data.jsonl").splitlines() if line.strip()]
    refs = json.loads(_download("reference_answer.json"))

    def reference(row):
        n = int(row["id"].split("--")[1])
        for a in refs[row["web_name"]]["answers"]:
            if a["id"] == n:
                return a
        return {}

    picked = []
    for row in rows:
        ref = reference(row)
        n = int(row["id"].split("--")[1])
        if row["id"] in EXCLUDED:
            continue
        if row["web_name"] == "Google Search" and ref.get("type") == "golden":
            picked.append((row, ref))
        elif row["web_name"] == "Amazon" and (n % 3 == 0 or n == 16):
            picked.append((row, ref))

    with open(OUT, "w") as f:
        for row, ref in picked:
            f.write(json.dumps({
                **row,
                "reference": ref.get("ans"),
                "reference_type": ref.get("type"),  # golden | possible
            }) + "\n")
    print(f"wrote {len(picked)} tasks to {OUT}")


if __name__ == "__main__":
    main()
