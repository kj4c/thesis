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
python evaluation/run_eval.py --ids "Amazon--6" --amazon-gap 120 --label rerun
```

evaluation always runs with `PRICE_TUNING=false`; the other settings come from
the pinned block in `.env`. commit code changes before a run, so its
`config.json` points to a clean commit.

## scoring: human review

success is judged by a person, as in WebVoyager's human evaluation: look at
the task's screenshots, supplemented by the agent's step log, and decide
whether it did what the task asked. record it in the run's `summary.csv`.

- **already scored**: a task that didn't end with an answer (step limit,
  timeout, failure, or a loop guard stopping it with "Sorry, I got stuck…")
  has `success` 0. only the rest need reviewing.
- **gave up with its own answer**: a `gave_up` where the agent wrote its own
  answer is left blank, to review like an `ok` one. it can be right: when no
  product meets the task's conditions any more, "none of them qualify", backed
  by screenshots, is the correct answer (see Amazon below). it's a 0 if the
  screenshots show it just didn't manage the task.
- **blocked**: a task with status `blocked` hit a captcha, checkpoint or
  sign-in wall and didn't finish (see below). the runner retries it once at
  the end of the run (see **retries** below); if it's still blocked, its
  `success` is left blank: don't score it, re-run it. the end of a run prints
  the `--ids` to re-run.
- **setup failed**: a task with status `setup_failed` never reached the agent:
  starting the browser, opening its site or setting Amazon's ZIP raised first,
  usually a page-load timeout. the error is in `setup_error`. like `blocked`, it's retried, and if
  it still fails its `success` is left blank and it's in the `--ids` to re-run. (before 23 Oct these were recorded
  as `harness_timeout` after about 33 s with 0 steps, e.g. Amazon--12 in
  `baseline_first`.)
- **model API failed**: `llm_failed` or `rate_limited` means a model call
  failed even after its own retries, not that the agent decided wrongly. like
  `blocked`, it's retried, and if it still fails it's left blank, not scored.
  (before 24 Oct these were scored 0, e.g. Amazon--33 in `baseline_bugfix_first`.)
- **retries**: after the planned tasks, every task that ended `blocked`,
  `setup_failed`, `llm_failed` or `rate_limited` is run once more, Amazon gap
  included (`--retries N` for more, `--retries 0` for none). `tasks.jsonl` keeps
  every attempt, with an `attempt` field (0 is the planned run); `summary.csv`
  has the last attempt only, and its `notes` say when a task was retried. a
  retry's screenshots are in `screenshots/<task>_r<repeat>_a<attempt>/`. the
  run's total cost counts every attempt.
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
- **Amazon**: judge whether the answer meets the task's conditions on
  amazon.com as the screenshots show it (US delivery address, prices in USD),
  not whether it matches the reference product. Amazon's references are only
  "possible" answers and many have gone stale: listings, prices and review
  counts have changed since 2023. if no product meets the conditions any more,
  an answer that says so, backed by screenshots (e.g. a filtered list sorted by
  review count), is a 1; naming a product that doesn't meet them is a 0. say
  the reference is stale in `notes`. (e.g. Amazon--6: the reference stroller
  had 22,146 reviews; in October 2026 it has about 5.8K, and no black stroller
  at $100–200 has over 20,000.)
- **content that needs a sign-in**: the agent never signs in, so part of a
  page can be out of reach (e.g. Amazon's full reviews: "Sign in to see
  customer reviews"). if the agent correctly says that a sign-in is what
  stopped it, and gives the closest answer the page allows instead, that is a
  1. e.g. Amazon--12: the full top review needs a sign-in, so the review
  snippet the page shows counts, and so does the ratings count standing in for
  the number of reviews. it's a 0 if the screenshots show the content was
  there without signing in, or if the answer leaves out what the sign-in hid
  without saying so. this is different from **blocked** (below): a captcha,
  checkpoint or sign-in wall that covers the whole site because Amazon or
  Google took the browser for a bot. those are re-run, not scored. say which
  in `notes`.
- **notes**: one line on why, for every 0 and for any judgement call.

if two people are available, have both score the same few runs independently
and report how often they agree; it makes the human scores much easier to
defend.

## differences from WebVoyager's own runs

- **Amazon gets a US delivery address.** runs are made from Australia, and a
  fresh browser gets its delivery country from the IP: amazon.com then shows
  AUD prices with international shipping, hides US-only details such as FREE
  Returns (Amazon--16's golden answer), and sometimes redirects to
  amazon.com.au. so before each Amazon task the runner sets the delivery ZIP
  to 10001 (New York) through amazon.com's own "Deliver to" popup, retrying up
  to 3 times. the agent starts on a page already priced for the US. if it
  still fails, the task runs with the IP's region and the reason is in that
  task's `setup_error`. this is not a VPN: the IP is still Australian, so
  anything Amazon decides by IP rather than delivery address can still differ.
  WebVoyager's own reference file notes that Amazon results depend on time
  and location.
- **sites that block the browser are re-run, not scored.** before every plan
  step, and on the final page, the runner checks for Amazon's "Continue
  shopping" checkpoint, captcha and sign-in wall, and Google's captcha page.
  each hit is listed in the task's `blocked` field (step, kind, URL). a task
  that hits one and doesn't finish gets status `blocked`; one that finishes
  anyway keeps `ok` and is scored as usual. WebVoyager also leaves out tasks
  that were stopped by captchas or sign-in. a blocked task still runs to its
  step limit, then is retried at the end of the run. it isn't stopped early:
  tasks recover from long runs of blocks (Amazon--36 after 5 in a row,
  Amazon--33 after 4, 24 Oct), so any early cut-off would end good tasks.
- **a gap between Amazon tasks.** Amazon flags a burst of fresh, cookie-less
  sessions from one IP as bots, and a flagged session stays blocked for the
  whole task. an Amazon task waits until 90 s after the previous one ended
  (`--amazon-gap` to change it, recorded in `config.json`). avoid quick test
  runs just before a scored one.
- **a subset**: 29 of the 643 tasks, on two sites (see `make_subset.py`).
- **windowed, not headless**: same 1024x768 page area, but Google sends
  headless chrome to its captcha page on every search (tested October 2026).
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
