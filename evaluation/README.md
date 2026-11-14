# evaluation

Runs the agent on a subset of the WebVoyager benchmark and logs every step, for
the baseline and every later comparison.

- `tasks.jsonl`: the 29 tasks (15 Google Search, 14 Amazon), each with its
  question, start site and reference answer. This is all a run needs.
- `make_subset.py`: rebuilds `tasks.jsonl` from the pinned WebVoyager data.
  The selection rule is at the top of the file.
- `run_eval.py`: runs the tasks and writes `results/<time>_<label>/`. See the
  top of the file for what each output file holds.
- `thesis_evaluation_runs/`: the runs the thesis reports, scored: one folder
  per version (the baseline, A, B, C and the comparison's four), each laid out
  as one 3-repeat run, and `combined_summaries/` with just their summaries.
  Each run's `final_screenshots/` has the page at the end of every scored
  answer (`<task>_r<repeat>.png`). The step-by-step screenshots stay in the
  original runs' folders in `results/`, which aren't in the repository.

```bash
python evaluation/run_eval.py --label baseline
python evaluation/run_eval.py --ids "Google Search--40,Amazon--0" --label smoke
python evaluation/run_eval.py --ids "Amazon--6" --amazon-gap 120 --label rerun
```

Evaluation always runs with `PRICE_TUNING=false`; the other settings come from
the pinned block in `.env`. Pick a version on the command line, e.g.
`PERCEPTION=vision VERIFIER=off python evaluation/run_eval.py --label noverifier`
(the versions are in the [main README](../README.md#versions)). Commit code
changes before a run, so its `config.json` points to a clean commit: each run
records its own. The comparison of the four versions ran on the frozen agent,
tagged `evaluated-agent` (`90bbe10`).

## scoring: human review

A person judges success, as in WebVoyager's human evaluation: look at the
task's screenshots and step log, decide whether it did what the task asked,
and record 1 or 0 in the run's `summary.csv`.

### where to look

- `screenshots/<task>_r<repeat>/`: `stepNN.png` is the page the planner read
  at step NN, `final.png` the page at the end.
- `steps.jsonl`: each step's action, reasoning and outcome.
- `stepNN_dom.txt`: the page text the planner had at step NN.

### what's already decided

- **No answer** (step limit, timeout, failure, or a loop guard's "Sorry, I
  got stuck…"): already 0.
- **`gave_up` with its own answer**: left blank, to review like `ok`. It can
  be right (see "none" answers under Amazon).
- **Answer ending "I got stuck before I could finish checking this…"**: the
  checking step turned it down first. Judge it like any other; its `analyse`
  line in `steps.jsonl` says what it doubted.
- **Not about the agent, so left blank and re-run**: `blocked` (a captcha,
  checkpoint or sign-in wall, including the agent's own bot-check stop),
  `setup_failed` (the browser, site or Amazon ZIP failed before the agent
  started; see `setup_error`), `llm_failed` or `rate_limited` (a model call
  failed after its retries), `timed_out` (one step hit its 30 s limit; see
  `error`) and `wrong_site` (see Amazon).
- **`harness_timeout`**: the 600 s cap on the whole task. A 0: the agent was
  still going.

### retries

- Every task that ended in a re-run status is run once more at the end,
  Amazon gap included (`--retries N` to change it; `--retries 0` for none).
  The end of a run prints the `--ids` of any still left to re-run.
- `tasks.jsonl` keeps every attempt (`attempt` 0 is the planned run).
  `summary.csv` has the last one, with a note when it was retried.
- A retry's screenshots are in `screenshots/<task>_r<repeat>_a<attempt>/`.
  The run's cost counts every attempt.

### the general rules

- **1 only if every part is answered.** A task with several parts that misses
  one is 0.
- **The screenshots win** where they and the answer disagree. A detail no
  screenshot shows may come from an extract: check `steps.jsonl` before
  marking it wrong. (These two are WebVoyager's own rules.)
- **Judge the main answer**: the reply's first sentence. A right answer
  further down doesn't rescue a wrong one, and a task that asks for several
  things needs all of them there. For older runs, judge the first answer the
  reply gives.
- **The record has to show it.** The record is the screenshots, the saved page
  text and the `extract` results. A detail in nothing the agent saw is a 0,
  even when it's right.
- **A selection counts only when the page's own label shows it** (a size, a
  colour). An extract that only repeats what the agent asked for doesn't.
- **Take the most general reading** of what the task asks for.
- **Write a note** for every 0 and every judgement call.

### Google Search

- **Agree with the golden reference.**
- **A fact that has changed since 2023** ("the latest…"): the fact the
  screenshots show on the day of the run is the reference, and an answer
  matching the old one is a 0 when the screenshots show the new one. Say so in
  `notes`.
- **A relative date has gone stale too**: the tasks were written in 2023, so
  "the year before last" meant 2021. An answer that says the date-dependent
  part doesn't match, or isn't confirmed, is a 1.
- **A question that can be read more than one way**: any reading the
  screenshots support is a 1. Leading with one reading while explaining the
  difference is a 1 too, an exception to the main-answer rule. Note
  "ambiguous: …".
- **From the AI Overview**: judged on whether it's right, as long as the
  source says it came from the Overview and wasn't checked on a source page.
  Naming a site as its source, or no source, is a 0.
- **The Overview's source cards and chips** (everything above "AI responses
  may include mistakes") are part of the Overview, not source pages. An answer
  resting on one has to name the Overview: calling it "a Google snippet" names
  a site.
- **A snippet in the ordinary results** ("Web results", below the Overview)
  is a 1 if it's right and the source says the page wasn't opened. It's still
  not a source page.
- **To tell a card from a web result**, look in `stepNN_dom.txt`: a card comes
  before the Overview's "Good response" / "Bad response" buttons, a web result
  after "Web results".

### Amazon

- **Judge on amazon.com as the screenshots show it** (US address, USD), not
  against the reference product. The references are only "possible" answers,
  and many have gone stale since 2023.
- **Every condition has to be met**, by the search or by what the answer
  checked for each item. A condition that's hard to check on a listing has to
  be in the search, unless the answer gives the value it read for each item.
- **Use the task's own search words.** Rewording them changes the results: a
  0.
- **Use Amazon's own search, menus and filters.** An answer read from a page
  reached through Google or any other site is a 0. A detour that ends back on
  Amazon's search is fine if the answer is read from that search.
- **A material** has to show for the product answered: in its title, its
  details (Material, Finish), or an applied material filter. Only in the
  search box, or only in another colour option, doesn't count.
- **A named item has to be named on the page**: as text, or in the element
  labels for versions that read them. In vision only, a name printed on the
  product image counts; one guessed from the picture doesn't.
- **"Customers say"** (Amazon's AI summary) is not a customer's review, even
  when the answer calls it a summary. The
  excerpts under its topic buttons are customers' words and count; their
  record is the `extract` that read them.
- **"None of them qualify"** is a 1 when it's true and the screenshots back
  it: the list filtered to the task's conditions, with more than one page
  read or one page sorted by Best Sellers. Naming a product that doesn't meet
  the conditions is a 0. Note the reference is stale.
  - the source has to say truthfully what was checked: a sort, filter or page
    the screenshots don't show is a 0, even when the conclusion is right: for
    a user who can't see the screen, a search that didn't happen is as bad as
    a wrong product.
  - one page of a Featured list is too little, even when the answer says so.
  - Amazon has no sort by number of reviews (`s=review-count-rank` stays on
    "Featured"), so "sorted by review count" claims a sort that didn't happen.
  - a list sorted by Avg. Customer Review doesn't back "none" about review
    counts: it puts products with a few high ratings first. Best Sellers
    follows review counts closely enough.
  - filters typed into the URL count only if the page read last shows them
    applied.
- **Another Amazon site** (e.g. amazon.com.au, despite the US ZIP) isn't
  valid: the runner ends it `wrong_site`, to re-run.

### content behind a sign-in

- **The agent never signs in**, so part of a page can be out of reach (e.g.
  Amazon's full reviews). Saying a sign-in stopped it and giving the closest
  answer the page allows is a 1. Leaving out what the sign-in hid without
  saying so, or claiming a sign-in when the content was there, is a 0.
- **A sign-in wall over the whole site** (a bot check) is different: `blocked`,
  re-run. The runner can't tell the two apart, so a `gave_up` whose only blocks
  were sign-in pages is left blank with a note: score it if the agent opened
  the sign-in itself for hidden content (e.g. `/product-reviews/`), re-run it
  if it was a wall.

If two people are available, have both score a few runs independently and
report how often they agree: it makes the scores much easier to defend.

## differences from WebVoyager's own runs

- **A US delivery address for Amazon.** From Australia, amazon.com shows AUD
  prices, hides US-only details such as FREE Returns, and sometimes redirects
  to amazon.com.au. So before each Amazon task the runner sets the ZIP to
  10001 (New York) through Amazon's own "Deliver to" popup, up to 3 tries; if
  that fails, the reason is in `setup_error`. It isn't a VPN: anything Amazon
  decides by IP can still differ. (WebVoyager's reference file also notes that
  Amazon results depend on time and location.)
- **Blocked sites are re-run, not scored.** Before every plan step and on the
  final page, the runner checks for Amazon's "Continue shopping" checkpoint,
  captcha and sign-in wall, and Google's captcha page, and lists each hit in
  `blocked`. A task that hits one and doesn't finish ends `blocked`; one that
  finishes anyway is scored. WebVoyager also leaves these out.
- **The agent stops at a bot check itself** (Amazon's checkpoint or captcha,
  Google's captcha, a captcha widget, or a Cloudflare check that hasn't
  cleared in 8 s) and hands it to the user. So more tasks end `blocked` and
  get re-run. The runner doesn't check for Cloudflare; the agent's stop marks
  those.
- **A gap between Amazon tasks.** Amazon flags a burst of fresh sessions from
  one IP as bots, for the whole task. An Amazon task waits until 90 s after the
  last one ended (`--amazon-gap`, recorded in `config.json`). Avoid quick test
  runs just before a scored one.
- **A subset**: 29 of the 643 tasks, on two sites (see `make_subset.py`).
- **Windowed, not headless**: the same 1024x768 page area, but Google sends
  headless Chrome to its captcha page on every search.
- **A 600 s cap per task**, on top of the 15-step limit.

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
