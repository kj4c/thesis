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
  screenshots show it just didn't manage the task. since 1 Nov, when a loop
  guard stops the agent after the checking step turned down an answer, it
  gives that answer, ending "I got stuck before I could finish checking this,
  so it may not be right." judge it like any other: the main answer has to be
  right and meet every condition, and the checking step's reason (the
  `analyse` line in `steps.jsonl`) says what it doubted.
- **blocked**: a task with status `blocked` hit a captcha, checkpoint or
  sign-in wall and didn't finish (see below). since 24 Oct the agent stops at
  a bot check itself ("Sorry, this site is asking me to prove I'm human…")
  instead of clicking through it, so these end `blocked` too. the runner retries it once at
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
- **a step timed out**: `timed_out` means one step hit its 30 s limit: a slow
  model API, or a browser tab that stopped answering (Amazon--0 in the 24 Oct
  smoke run). the error is in `error`. it's retried and, if it times out again,
  left blank like the others. different from `harness_timeout`, the 600 s cap
  on the whole task, which is a 0: the agent was still going.
- **retries**: after the planned tasks, every task that ended `blocked`,
  `setup_failed`, `llm_failed`, `rate_limited`, `timed_out` or `wrong_site` is run once more, Amazon gap
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
- **main answer only** (from 24 Oct): judge the answer, the first sentence of
  the reply. a wrong answer isn't rescued by a right one further down or by
  "another option". a task that asks for several things (a top 5, a date and a
  place) needs all of them in that answer. since 24 Oct the agent answers in
  this shape: the answer, then source and confidence, nothing else unless
  asked. for older runs, judge the first answer the reply gives.
- **Google Search**: the answer has to agree with the golden reference. if the
  fact has changed since the reference was written ("the latest…") and the
  screenshots show the current one, accept it and say so in `notes`.
  a relative date in the question has gone stale the same way: the tasks were
  written in 2023, so Google Search--14's "the year before last" meant 2021,
  when Jimmy Smith left Yeovil Town (the reference). an answer of Yeovil Town
  that says the retirement year doesn't match or isn't confirmed is a 1; the
  agent can't know when the question was written. (e.g. smoke_versionC_dom r2,
  where the checking step turned the right answer down over the year.)
  Google Search--0 ("initial release date") is ambiguous: the 22 April 2023
  premiere or the 5 May 2023 US release (the reference). an answer that leads
  with the premiere but explains the difference properly is a 1, an exception
  to the main-answer rule; it's a 0 only if the information is wrong. write
  "ambiguous: led with the premiere" in `notes` when it does. the same goes for
  an answer that leads with another country's earlier release and gives the US
  date as well (e.g. 3 May 2023 in the UK, versionB r0).
  an answer taken from Google's AI Overview is judged on whether it's right,
  as long as it says so: the source has to say it came from the Overview and
  wasn't confirmed on a source page. the source cards and chips inside the
  Overview (everything above "AI responses may include mistakes") are part of
  it, not a source page. an Overview answer that names a site as its source,
  or doesn't say where it came from, is a 0. (e.g. Google Search--12 in
  `baseline_bugfix_first` and `versionA_reruns`: both answered from the
  Overview and its Pro-Football-Reference card, said they didn't open the
  page, and are a 1.)
- **Amazon**: judge whether the answer meets the task's conditions on
  amazon.com as the screenshots show it (US delivery address, prices in USD),
  not whether it matches the reference product. Amazon's references are only
  "possible" answers and many have gone stale: listings, prices and review
  counts have changed since 2023. if no product meets the conditions any more,
  an answer that says so, backed by screenshots (e.g. the list filtered to the
  task's conditions, sorted by Best Sellers, and more than one page checked), is
  a 1; naming a product that doesn't meet them is a 0. say the reference is
  stale in `notes`. a "none" answer can't be proven by checking every page, so
  the source sentence has to say truthfully what was checked: if it names a
  sort, filter or page the screenshots don't show, it's a 0, even when the
  conclusion is right (for a user who can't see the screen, a search that
  didn't happen is as bad as a wrong product). being truthful isn't enough on
  its own, though: one page of a Featured list is too little to back "none",
  even when the answer says that's all it read (Amazon--6 versionB r0 and r1,
  16 of about 600 results; r2, which read 3 pages, is a 1). (e.g. Amazon--6: the reference
  stroller had 22,146 reviews; in October 2026 it has about 5.8K, and no black
  stroller at $100–200 has over 20,000. Amazon no longer offers a sort by
  number of reviews, at least without an account: `s=review-count-rank` in a
  URL leaves the page on "Sort by: Featured". answers that say "sorted by
  review count" claim a sort that didn't happen.)
  where the task names what to search for, the search has to use those words:
  rewording it changes the results, so the answer is to a different task and
  is a 0. (e.g. Amazon--3, "climbing gears": a search for "climbing gear"
  returns different first results, versionA r2.)
  Amazon--33 ("a portable air conditioner … suitable for a room size of 300 sq
  ft, with energy efficiency rating, and compare the prices of the top three
  search results") is read the same way: "energy efficiency" is part of what to
  search for, so a search without it is a 0 (versionB r0). the task is the
  price comparison, so an answer that says truthfully the three listings
  don't show a rating is fine; it doesn't have to open each product page.
  Amazon--30 ("the highest-rated fiction book released in 2024, with a
  minimum of 50 customer reviews") has no single right answer: taken
  literally it's a little-known 4.9-star book, while the reference reads it as
  a popular novel. so it's judged on how the agent searched, on Amazon. it's a 1
  only if the screenshots show it (1) limited the search to fiction with
  Amazon's own category (Books › Literature & Fiction), not by judging titles,
  (2) kept to 2024 releases: Amazon has no filter by year (only "Last 30/90
  days"), so a "2024" in the search counts, as long as the answer's title shows
  a 2024 date and earlier titles on the list were skipped, not answered,
  (3) sorted by customer rating or used an
  Amazon top-rated or best-seller list for 2024 fiction, and (4) answered with
  the top title on that list with at least 50 reviews. finding the book through
  another site, such as Google, is a 0.
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
  in `notes`. the runner can't tell the two apart from the page. since 1 Nov,
  a `gave_up` with its own answer whose only blocks were sign-in pages is left
  blank to review, with a note saying so: score it if the agent reached the
  sign-in itself for hidden content (e.g. by opening `/product-reviews/`) and
  answered as above, re-run it if it was a wall. before that these were marked
  `blocked` (Amazon--12 versionB r2, scored by hand).
- **another Amazon site**: Amazon sometimes redirects to its local site
  (amazon.com.au) despite the US ZIP. a run on any site other than amazon.com
  isn't valid: leave `success` blank and re-run it (Amazon--12 in
  `versionB_reruns`). since 1 Nov the runner checks the site after setting the
  ZIP (a `setup_failed` if it's wrong) and before every step: a task that
  reached another Amazon site ends `wrong_site`, is retried, and is left blank.
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
  that were stopped by captchas or sign-in. a blocked task is retried once at
  the end of the run. since 24 Oct the agent also stops itself at the first bot
  check it meets (Amazon's checkpoint or captcha, Google's captcha page, a
  captcha widget, or a Cloudflare check that hasn't cleared after 8 s) and
  hands it to the user, so a block now usually ends the task at once. before
  that it clicked through Amazon's checkpoint and often recovered (Amazon--36
  after 5 blocks in a row, Amazon--33 after 4, 24 Oct). the price: more tasks
  end `blocked` (1 of 29 first attempts in `baseline_bugfix_first`, 10 of 87
  in `versionA`), and more re-runs. the runner's own check doesn't look for
  Cloudflare; the agent's stop marks those tasks `blocked`.
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
