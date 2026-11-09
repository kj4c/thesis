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
  date as well (e.g. 3 May 2023 in the UK, versionB r0). the date it leads with
  has to be a release: 19 April 2023 in South Korea was a press event, not a
  release, so an answer that leads with it is a 0 (`baseline_second`,
  `baseline_third`).
  Google Search--3 ("comedy movies, sorted by user ratings … the Top 5") doesn't
  say which list. the reference matches IMDb's Top 250 filtered to comedy; a
  plain sort by user rating puts titles with a handful of votes, all rated 10,
  at the top. so it's a 1 if the answer is IMDb's comedy list sorted by user
  rating with a minimum number of votes (25,000 and 100,000 both count), read
  correctly and in order, and the source names the minimum; it's a 0 without a
  vote minimum, even when the answer says those ratings aren't reliable. (e.g.
  versionB r0 at 25,000 and r2 at 100,000 are 1s; versionC_more r1 and
  pilot_hybrid, the 10/10 lists, are 0s.) the 25,000-vote list currently has an
  unreleased film at the top (*You Can See Everything*, 9.1): an answer that
  lists it is still read correctly, so it's not marked down for it.
  Google Search--21 ("the top 5 highest-grossing animated movies, sorted by box
  office earnings") has changed since 2023: Wikipedia's "List of
  highest-grossing animated films" now starts with Ne Zha 2, Zootopia 2 and
  Inside Out 2. the list as the screenshots show it on the day of the run is
  the reference: a 1 if the top 5 are that list's first five, in order (the
  figures aren't required). (e.g. every pilot run, which gave Ne Zha 2,
  Zootopia 2, Inside Out 2, The Lion King (2019), Frozen II.)
  Google Search--18 ("when and where the most recent World Cup was held, and
  which team was the winner") has changed since 2023: the 2026 World Cup
  (Canada, Mexico and the United States, 11 June – 19 July 2026, won by Spain)
  had been played by the time of the runs. the most recent tournament as the
  screenshots show it is the reference, as with Google Search--21: an answer
  of 2022 (Qatar, Argentina) is a 0, even though it matches the 2023
  reference, when the screenshots show the 2026 result (compare_r1_vision,
  which read on Wikipedia that Spain won in 2026 and then answered 2022). all
  three parts (dates, hosts, winner) are needed for a 1.
  Google Search--19 ("the first 7 bits of the SHA of the Bert's latest
  commit") needs `eedf571`, the merge of pull request #1027, which is the
  latest commit on master. `8028c04` is the pull request's own commit ("Add
  links to 24 smaller BERT models"), one before it, reached by opening the
  pull request or the commit title: it's a 0, even though the change it
  describes is the same (pilot_vision_clean r1, pilot2_noverifier).
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
  a list sorted by Avg. Customer Review doesn't back "none" (decided 9 Nov):
  it sorts by star rating, so highly rated strollers with a handful of
  reviews come first and one with 20,000 reviews needn't be near the top.
  only Best Sellers ranks by how much a product sells, which follows its
  number of reviews closely enough. (e.g. `baseline_first`, compare_r3_vision:
  0s.)
  Amazon--6's "none" needs three things from the screenshots: the $100–200
  price filter visibly applied on the list it read (the result count and a
  "Clear" filter link, or the filter chip), more than a single Featured page
  (two or more pages read, or one page sorted by Best Sellers, where the most
  reviewed come first), and a source sentence that says truthfully which pages,
  filters and sort it read. a list whose filters were typed into the URL counts
  only if the page it read last shows them applied. on a Best Sellers page
  the top listings are enough: a stroller with over 20,000 reviews would be
  among the first few best sellers, so reading the top 10 or so of that page
  counts, as long as the source says how much it read. (e.g. pilot3_noverifier,
  two Featured pages with the filter applied, is a 1; versionC r0, one Best
  Sellers page, is a 1; pilot4_dom, about 11 listings of one Best Sellers page
  with the filter shown, is a 1; pilot3_hybrid, which sorted by Best Sellers and then
  read a typed URL's page on Featured, is a 0; pilot2_vision, whose price
  filter never applied, is a 0.)
  Amazon--18 ("Open Amazon's home page and tell me what the deal is … list the
  names of at least 2 items that are on offer and … what percent off they
  are") is about the home page: the deal and its items have to be ones the home
  page shows. items read from another page are a 0, whether Today's Deals
  (versionB) or the deal card's own page, whose items differ from the home
  page's tiles (pilot3_noverifier). each named item's name has to be on the
  home page: as text on the screen, or in the page's element labels for the
  versions that read them (check `steps.jsonl` or the step's `_dom.txt`). the
  device tiles show only a picture and a percentage, so in vision only a name
  guessed from the picture ("a Kindle e-reader") doesn't count, and one printed
  on the product image does ("Hisense 50" QLED E7"). fewer than two such names
  is a 0, even when the answer says the names are uncertain (pilot3_vision).
  pilot4_vision, "Fire TV Cube" and "Toshiba Z670R" printed on the product
  images, is a 1. the deal and the items may come from different parts of the
  home page, e.g. the hero banner named as the deal and the items from "Save
  on Amazon Devices" (pilot4_dom, a 1).
  an answer read from a page reached through Google (or any other site) is a
  0 in every Amazon task (decided 9 Nov): the task is to find it on Amazon,
  with Amazon's own search, menus and filters. a detour through Google that
  ends back on Amazon's own search still counts as long as the answer is read
  from that search; an Amazon page opened from Google's results doesn't.
  (e.g. `baseline_bugfix_first` Amazon--36 and `_rerun3` Amazon--33, which
  went to Google after bot checks on Amazon; Amazon--30's rule below says the
  same.)
  where the task names what to search for, the search has to use those words:
  rewording it changes the results, so the answer is to a different task and
  is a 0. (e.g. Amazon--3, "climbing gears": a search for "climbing gear"
  returns different first results, versionA r2.)
  Amazon--33 ("a portable air conditioner … suitable for a room size of 300 sq
  ft, with energy efficiency rating, and compare the prices of the top three
  search results") asks for three conditions, and each has to be met either by
  the search or by what the answer checked (clarified 9 Nov):
  "300 sq ft" can be left out of the search if the answer states each of the
  three listings' room size and they cover 300 sq ft; "energy efficiency" is
  harder to check from a listing, so it has to be in the search ("energy
  efficient" and "energy efficiency rating" count: compare_r1_hybrid, r2_hybrid,
  r3_hybrid), unless the answer gives an efficiency rating (CEER, EER, Energy
  Star) it actually read for each of the three. BTU or SACC figures are
  capacity, not efficiency. so a search for "portable air conditioner 300 sq
  ft" whose answer says no rating was shown, or that it didn't open the product
  pages, is a 0 (versionB r0, every DOM, vision-only and no-verifier comparison
  answer). once the search has it, the task is the price comparison, so an
  answer that says truthfully the three listings don't show a rating is fine;
  it doesn't have to open each product page. the search has to be Amazon's
  own: a results page reached through Google is a 0 (`baseline_bugfix_first
  _rerun3`, which reached an Amazon page through Google after its searches hit
  bot checks).
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
  a material the task asks for (e.g. Amazon--21's "stainless steel") has to be
  shown for the product answered: in its title, its specifications or details
  (Material, Finish), or by Amazon's own material filter, applied on the list
  the product was picked from. it doesn't count when the word is only in the
  search box, or only in another colour option of the same listing: the
  agent has to check, not assume. (e.g. Amazon--21: the Cuisinart DCC-1200NAS
  is listed as "Brushed Chrome", with "Black Stainless Steel" only as a
  separate colour, DCC-1200BKSNAS; none of the 8 comparison answers that named
  it applied the Stainless Steel filter, so all 8 are 0s, even though the
  machine may well be stainless and a person might have assumed the same. the
  DCC-1200BKSNAS, "Black Stainless Steel" in its title, is a 1.)
  Amazon--12 ("give me the top review about this Ride On Car") needs the text
  of a customer's review: the excerpt the page shows counts (see "content that
  needs a sign-in", below). a review's title and author without its text is a
  0 (compare_r1_dom), and so is Amazon's AI "Customers say" summary, which is
  not a customer's review, even when the answer says it's the summary
  (6 comparison answers). if the page shows no customer excerpt, the answer has
  to say that there isn't one, not stand in the summary for it.
  Amazon--16 ("If Free return is available, tell me how to return this
  item"): the steps have to be read from Amazon's pages for this item, i.e. the
  FREE Returns popup ("How to return this item") or the return help page it
  links to, and match what they show. the 2023 reference's "print the return
  shipping label; ship it" is out of date: in November 2026 the popup says "Go
  to Your Orders to start the return; select your preferred free shipping
  option; drop off and leave!", and the help page describes a QR code taken to
  a drop-off location, so either version is a 1 (confirmed with the team,
  9 Nov). the colour can be any option named Black (e.g. "934 Black" or "965
  Black"). the size has to be selected, as the page's "Size:" label shows it:
  a clicked or outlined XX-Large button doesn't count while the label still
  says X-Large, and an answer that says XX-Large was selected when the label
  shows another size is a 0, even though the return steps are the same for
  every size: a user who pressed Buy at that point would get the wrong shirt.
  **the agent has to have checked both, and the run's record has to show it
  (decided 9 Nov): it can't be right by accident.** the record is the
  screenshots, the saved page text (`stepNN_dom.txt`, from 1 Nov) and the
  agent's `extract` results in `steps.jsonl`. so:
  - the steps: the popup or help page text on a screenshot, in the saved
    page text of a step the agent read, or in an `extract` result. the popup's
    "How to return this item" is in the page's text even while the popup is
    closed, so an extract that quotes it counts. steps from "general Amazon
    flow", or that appear nowhere in the record, are a 0, even when they
    match (compare_r1_vision; `baseline_first`, whose extracts never had them).
  - the size: the "Size:" label showing XX-Large on a screenshot, in the saved
    page text, or in an extract that reports it as the selected size. an
    extract that only repeats the size the agent asked about doesn't count
    (versionA r1: its request said "(Black, XX-Large)"). a page reached through
    Amazon's "black xx-large" suggestion usually opens on XX-Large, but if
    nothing in the record shows the label, it's a 0
    (`baseline_bugfix_first_rerun5`, though replaying its route live showed
    XX-Large).
  (e.g. 1s: compare_r1_hybrid, r2_dom_reruns, r3_dom, r3_hybrid; versionC r0
  and versionC_more r0, r1 (saved page text); `baseline_second`, versionA r2,
  versionB r0 (extracts that report "Size: XX-Large" and quote the popup).
  0s: compare_r1_dom, r2_hybrid, r3_vision, r1_noverifier and r2_noverifier,
  whose label stayed X-Large.)
  checked live on 9 Nov: a click that works changes the "Size:" label within
  a second, along with the selected colour's price ($24.29 for X-Large, $26.99
  for XX-Large on 934 Black) and the stock line. the "Selected Size is …"
  label read out for the collapsed size header (`aria-label`) doesn't update
  even when the click works: don't go by it.
  Amazon--39 ("a travel guide book on Amazon for Japan"): take the most general
  reading of the task, a guide to Japan. a guide to one or two cities is a 0,
  even when the answer says so (compare_r1_noverifier: Lonely Planet Pocket
  Kyoto & Osaka).
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
