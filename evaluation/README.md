# evaluation

runs the agent on a subset of the WebVoyager benchmark and logs every step, for
the baseline and every later comparison.

- `tasks.jsonl`: the 29 tasks (15 Google Search, 14 Amazon), each with its
  question, start site and reference answer. this is all a run needs.
- `make_subset.py`: rebuilds `tasks.jsonl` from the pinned WebVoyager data.
  the selection rule is at the top of the file.
- `run_eval.py`: runs the tasks and writes `results/<time>_<label>/`. see the
  top of the file for what each output file holds.

```bash
python evaluation/run_eval.py --label baseline
python evaluation/run_eval.py --ids "Google Search--40,Amazon--0" --label smoke
```

evaluation always runs with `PRICE_TUNING=false`; the other settings come from
the pinned block in `.env`. commit code changes before a run, so its
`config.json` points to a clean commit.

## scoring: human review

success is judged by a person, as in WebVoyager's human evaluation: look at
the task's screenshots, supplemented by the agent's step log, and decide
whether it did what the task asked. record it in the run's `summary.csv`.

- **already scored**: a task that didn't end with status `ok` (step limit,
  timeout, failure) has `success` 0. only the rest need reviewing.
- **where to look**: `screenshots/<task>_r<repeat>/` (`stepNN.png` is the page
  the planner read at step NN, `final.png` the page at the end) and that
  task's lines in `steps.jsonl` (each step's action, reasoning and outcome).
- **1 or 0**: 1 only if every part of the task is answered; a task with
  several parts that misses one is 0. where the answer and the screenshots
  disagree, the screenshots win; a detail in the answer that no screenshot
  shows may come from an extract, so check `steps.jsonl` before marking it
  wrong. (these are WebVoyager's own judging rules.)
- **Google Search**: the answer has to agree with the golden reference. if the
  fact has changed since the reference was written ("the latest…") and the
  screenshots show the current one, accept it and say so in `notes`.
- **Amazon**: judge whether the answer meets the task's conditions on the
  store the agent was sent to (see the redirect note below), not whether it
  matches the reference product.
- **notes**: one line on why, for every 0 and for any judgement call.

if two people are available, have both score the same few runs independently
and report how often they agree; it makes the human scores much easier to
defend.

## differences from WebVoyager's own runs

- **Amazon redirects to amazon.com.au.** runs are made from Australia, and
  Amazon sends the browser to its Australian store: prices are in AUD and the
  listings differ from the US ones the reference answers came from. we accept
  this rather than use a VPN. Amazon's references are only "possible" answers
  anyway, so an Amazon answer is judged on whether it meets the task's
  conditions (colour, rating, price range…) on the store the agent was sent
  to, not on matching the reference product. WebVoyager's own reference file
  notes that Amazon results depend on time and location.
- **a subset**: 29 of the 643 tasks, on two sites (see `make_subset.py`).
- **windowed, not headless**: same 1024x768 page area, but headless chrome is
  met with more bot checks and captchas.
- **a 600 s cap per task** on top of the 15-step limit, which WebVoyager
  doesn't have.

## source and licence

the tasks and reference answers come from WebVoyager:

- He et al., *WebVoyager: Building an End-to-End Web Agent with Large
  Multimodal Models*, arXiv:2401.13919, 2024.
- data: github.com/MinorJerry/WebVoyager, commit
  `5a7896738c10bfb8b9edccce6bb0e0411f8ae569`, files
  `data/WebVoyager_data.jsonl` and `data/reference_answer.json`
  (sha256 pinned in `make_subset.py`).

WebVoyager is licensed under the Apache License 2.0, copied in
`WEBVOYAGER_LICENSE`. `tasks.jsonl` is derived from it: a subset of the tasks,
with each task's reference answer and answer type added to its line. no task
wording or reference answer was changed.
