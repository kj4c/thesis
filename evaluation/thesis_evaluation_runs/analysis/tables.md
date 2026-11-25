# Evaluation tables

Made by `analyse.py` from `thesis_evaluation_runs/` (final marks, 14 Nov). Scored answers only, except the bot-check counts, which cover every attempt (a stop always ends an unscored attempt).

## Scores, cost and effort per version

| Version | Score | Google | Amazon | Per repeat (r0, r1, r2) | Steps per task | Seconds per task | Cost per task | Cost per success | Google per success | Amazon per success |
|---|---|---|---|---|---|---|---|---|---|---|
| Baseline | 36/87 (41%) | 8/45 | 28/42 | 11, 13, 12 (range 2) | 3.5 | 44 | $0.10 | $0.23 | $0.26 | $0.22 |
| A | 73/87 (84%) | 40/45 | 33/42 | 26, 22, 25 (range 4) | 3.9 | 60 | $0.12 | $0.15 | $0.12 | $0.18 |
| B | 71/87 (82%) | 42/45 | 29/42 | 24, 21, 26 (range 5) | 4.4 | 63 | $0.16 | $0.19 | $0.15 | $0.25 |
| C | 79/87 (91%) | 43/45 | 36/42 | 27, 26, 26 (range 1) | 3.7 | 61 | $0.15 | $0.16 | $0.13 | $0.21 |
| DOM | 69/87 (79%) | 38/45 | 31/42 | 20, 23, 26 (range 6) | 4.0 | 61 | $0.16 | $0.20 | $0.17 | $0.24 |
| Hybrid | 75/87 (86%) | 42/45 | 33/42 | 27, 25, 23 (range 4) | 4.5 | 68 | $0.20 | $0.23 | $0.18 | $0.29 |
| Vision only | 63/87 (72%) | 40/45 | 23/42 | 21, 20, 22 (range 2) | 5.3 | 73 | $0.13 | $0.17 | $0.13 | $0.24 |
| No verifier | 64/87 (74%) | 41/45 | 23/42 | 20, 22, 22 (range 2) | 5.2 | 64 | $0.10 | $0.14 | $0.10 | $0.19 |

Repeats score out of 29. Seconds and steps are per scored answer; cost counts only the scored attempt (failed calls aren't in `cost_usd`).

## Score by task (repeats passed, out of 3)

| Task | Baseline | A | B | C | DOM | Hybrid | Vision only | No verifier |
|---|---|---|---|---|---|---|---|---|
| Google Search--0 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--3 | 0 | 2 | 3 | 2 | 1 | 1 | 2 | 2 |
| Google Search--7 | 2 | 2 | 2 | 3 | 3 | 3 | 1 | 3 |
| Google Search--12 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--14 | 3 | 3 | 2 | 3 | 1 | 3 | 2 | 1 |
| Google Search--17 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--18 | 0 | 3 | 2 | 2 | 2 | 3 | 2 | 3 |
| Google Search--19 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--20 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--21 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 2 |
| Google Search--24 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--28 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--29 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Google Search--31 | 0 | 0 | 3 | 3 | 1 | 2 | 3 | 3 |
| Google Search--40 | 0 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Amazon--0 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Amazon--3 | 3 | 2 | 0 | 3 | 3 | 3 | 3 | 3 |
| Amazon--6 | 1 | 0 | 1 | 2 | 2 | 2 | 1 | 0 |
| Amazon--9 | 3 | 3 | 3 | 3 | 2 | 3 | 3 | 3 |
| Amazon--12 | 1 | 3 | 3 | 3 | 2 | 1 | 0 | 0 |
| Amazon--16 | 1 | 1 | 1 | 3 | 2 | 2 | 0 | 0 |
| Amazon--18 | 3 | 2 | 0 | 3 | 3 | 2 | 2 | 3 |
| Amazon--21 | 2 | 3 | 3 | 3 | 2 | 2 | 0 | 0 |
| Amazon--24 | 2 | 3 | 3 | 3 | 3 | 3 | 2 | 3 |
| Amazon--27 | 2 | 3 | 3 | 3 | 3 | 2 | 3 | 3 |
| Amazon--30 | 0 | 1 | 1 | 0 | 0 | 1 | 0 | 0 |
| Amazon--33 | 3 | 3 | 2 | 1 | 0 | 3 | 0 | 0 |
| Amazon--36 | 2 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Amazon--39 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 2 |

## Versions A–C against the baseline

Tasks whose repeats passed changed from the baseline (out of 3; + better, − worse).

| Version | Tasks better | Tasks worse | Unchanged | Net repeats |
|---|---|---|---|---|
| A | 18 | 3 | 8 | +37 |
| B | 19 | 4 | 6 | +35 |
| C | 21 | 1 | 7 | +43 |

## Failure tags (one per 0)

Google / Amazon in each cell.

| Tag | Baseline | A | B | C | DOM | Hybrid | Vision only | No verifier | Total |
|---|---|---|---|---|---|---|---|---|---|
| `condition_missed` | 3 / 1 | 1 / 2 | 0 / 7 | 1 / 4 | 2 / 6 | 2 / 5 | 1 / 11 | 1 / 8 | 55 |
| `overview_source` | 31 / 0 |  |  |  |  | 0 / 2 | 2 / 2 | 0 / 2 | 39 |
| `wrong_fact` | 1 / 0 | 3 / 0 | 2 / 0 | 1 / 0 | 5 / 2 | 1 / 0 | 1 / 1 | 1 / 0 | 18 |
| `stuck` | 2 / 6 | 1 / 1 | 1 / 1 |  | 0 / 1 |  | 1 / 3 | 2 / 2 | 21 |
| `false_claim` | 0 / 1 | 0 / 2 |  |  | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 3 | 9 |
| `went_around` | 0 / 2 | 0 / 1 |  | 0 / 2 | 0 / 1 |  |  | 0 / 3 | 9 |
| `wrong_page` |  | 0 / 1 | 0 / 4 |  |  | 0 / 1 |  |  | 6 |
| `not_in_record` | 0 / 1 | 0 / 1 |  |  |  |  | 0 / 1 |  | 3 |
| `element` |  | 0 / 1 | 0 / 1 |  |  |  |  | 0 / 1 | 3 |
| `harness` | 0 / 3 |  |  |  |  |  |  |  | 3 |
| **0s** | 51 | 14 | 16 | 8 | 18 | 12 | 24 | 23 | 166 |

## The checking step (scored attempts)

Actions checked = every action that reached analyse; turned down by code = the checks in `nodes.node_analyse` (an incomplete action, an empty `done`, a run of scrolls, extract in vision only); by the model = the verifier.

| Version | Actions checked | Turned down | by code | by the model | Model, on `done` | Model, on `click` |
|---|---|---|---|---|---|---|
| Baseline | 307 | 16 | 4 | 12 | 12 | 0 |
| A | 337 | 4 | 0 | 4 | 4 | 0 |
| B | 380 | 5 | 0 | 5 | 4 | 1 |
| C | 325 | 1 | 0 | 1 | 1 | 0 |
| DOM | 349 | 5 | 0 | 5 | 4 | 1 |
| Hybrid | 388 | 11 | 0 | 11 | 9 | 2 |
| Vision only | 462 | 7 | 1 | 6 | 3 | 3 |
| No verifier | 449 | 0 | 0 | 0 | 0 | 0 |

## Bot checks the agent stopped at (every attempt)

| Version | Cloudflare | Google captcha | Amazon checkpoint | Amazon captcha | other | Total |
|---|---|---|---|---|---|---|
| Baseline | 0 | 0 | 0 | 0 | 0 | 0 |
| A | 6 | 0 | 8 | 0 | 0 | 14 |
| B | 0 | 0 | 0 | 0 | 0 | 0 |
| C | 0 | 0 | 0 | 0 | 0 | 0 |
| DOM | 0 | 18 | 4 | 0 | 0 | 22 |
| Hybrid | 0 | 2 | 1 | 0 | 0 | 3 |
| Vision only | 0 | 1 | 3 | 0 | 0 | 4 |
| No verifier | 0 | 14 | 0 | 0 | 0 | 14 |

Each stop ends that attempt as `blocked`; it's re-run and never scored.

## For judging by hand

Judgements in `judgements.csv`: suggested by Claude, checked by Ana (14 Nov).

### Every verifier rejection in the comparison's scored attempts

Judged: right 14, wrong 5, over-strict 3 (of 22). Effect on the scored answer: no change 10, saved the answer 6, ignored 4, cost the answer 1, unclear 1.

| Version | Task | Repeat | Attempt | Step | Action | Reason (cut) | Suggested | Effect | Why |
|---|---|---|---|---|---|---|---|---|---|
| DOM | Google Search--12 | 0 | 0 | 3 | done | The page is a Dec 30, 2007 article, so it can't show whether Brady threw more than 50 TDs in a later season. The agent hasn't checked his later seasons, and the | right | no change | support: a 2007 article can't show 2007 was his career best; it checked Pro-Football-Reference and gave the same answer, now sourced [no change (1)] |
| DOM | Google Search--31 | 0 | 0 | 4 | done | The done text claims 66 goals in both 2011/12 and 2014/15 for Ronaldo, but the visible page doesn't support it. It says Messi scored 82 in 2011/12, and Ronaldo' | right | no change | support: the tie wasn't on the visible page (the ranking is an image); it answered the same tie, a 0 either way [no change (0)] |
| DOM | Amazon--6 | 1 | 0 | 2 | click | DOM: Element index 15095 is the Black color filter link, so the target is correct, but the planner should confirm; however the index [15095] exists as "Apply Bl | wrong | cost the answer | its own reason says the click is correct ("this is actually grounded"); the planner repeated the same click and the loop guard stopped the task [cost the answer (stuck, 0)] |
| DOM | Google Search--20 | 2 | 1 | 4 | done | The done text admits it didn't confirm the latest released film, so the answer is incomplete. Search for something like "Fast X release date" or "Fast & Furious | over-strict | no change | asked for the latest released film too; the rule accepts Fast Forever alone, so the rejected answer would have counted; the final one gives both [no change (1)] |
| DOM | Google Search--7 | 2 | 2 | 5 | done | The done text says the internet continuation appears only in the iOS 27 guide, but that isn't verified: the agent never opened the iOS 26 or earlier guide versi | right | saved the answer | wrong answer caught: said Apple gives no version; it then found iOS 17.1 in Apple's release notes [saved it (0 to 1)] |
| Hybrid | Amazon--21 | 0 | 0 | 5 | done | The proposed answer is the Cuisinart DCC-3400NAS at 4.0 stars. It is not confirmed as programmable, and the listing's description doesn't say it is. The page of | over-strict | no change | the DCC-3400NAS answer would have counted (1 in A–C); it applied Programmable and Stainless Steel and answered another fitting model [no change (1)] |
| Hybrid | Amazon--33 | 0 | 0 | 4 | done | The current page's Results list begins with Midea ($229.99), SereneLife SLPAC180W (about $157.31 used/new, no featured offer) and GarveeHome 8000 BTU ($216.99,  | right | saved the answer | the top three didn't match the page in front of it; the final answer reads the actual top three and the one CEER rating |
| Hybrid | Google Search--31 | 0 | 0 | 5 | done | The done text has a claim the page doesn't support. The page's table lists 2011/12 at 68 goals and 2014/15 at 66, and it is a niche site that conflicts with wel | right | saved the answer | wrong answer caught: 2011/12 (68) from a niche site; it checked Wikipedia and answered 2014–15, 61 [saved it (0 to 1)] |
| Hybrid | Amazon--6 | 1 | 0 | 8 | done | Only page 1 of 394 results was checked, and the 8.5K top count doesn't show that nothing qualifies. Sort by "Avg. Customer Review" or "Best Sellers" (or open pa | right | saved the answer | too little checked: one Featured page can't back "none"; it then read a Best Sellers page [saved it (0 to 1)] |
| Hybrid | Amazon--16 | 1 | 0 | 5 | click | DOM: The popup already shows the return policy details (FREE return, dropoff at 50,000 US locations, refund until Jan 31, 2027), so closing it is premature. Als | right | ignored | size not selected: the label still said X-Large; the agent went on to the return steps and claimed XX-Large anyway [ignored (0)] |
| Hybrid | Amazon--30 | 1 | 0 | 4 | click | DOM: Index 28762 is "Newest Arrivals", not "Avg. Customer Review"; click index 28760 to sort by Avg. Customer Review. / VISION: The sort dropdown is open and sh | right | saved the answer | wrong element: the index was Newest Arrivals; it clicked Avg. Customer Review instead, which the Amazon--30 rule needs [saved it (needed for the 1)] |
| Hybrid | Google Search--31 | 1 | 0 | 4 | done | The source is ambiguous: it gives 66 goals for both "2014/15" and "2011/12", and the visible page text never states Ronaldo's season. The extract's claim that 2 | right | no change | support: the page text doesn't say which season the 66 goals were; it went on to Wikipedia [no change (1)] |
| Hybrid | Google Search--31 | 1 | 0 | 5 | done | The visible page text never states the season for Ronaldo's 66 goals, and it mentions "2011/12" only for Messi's 82. The done text claims the graphic labels it  | right | no change | support: the claim that the graphic labels the season 2014/15 isn't shown on the page (checked on the step-5 screenshot, compare_r2_hybrid); it went on to Wikipedia |
| Hybrid | Google Search--31 | 1 | 0 | 8 | done | The Wikipedia extract is internally inconsistent. It first says 2011–12 was the peak with 60 goals, then "corrects" itself to 2014–15 with 61. The visible page  | wrong | no change | turned down a right, sourced answer (2014–15, 61, Wikipedia) because the extract was inconsistent; it answered the same three steps later [no change (1), cost steps] |
| Hybrid | Amazon--6 | 2 | 0 | 3 | done | The check is incomplete: the agent only looked at page 1 with the default Featured sort and did not apply the Black color filter or the 4 Stars & Up filter. Cli | right | ignored | too little checked: one Featured page; it added the Black and 4-star filters but not a sort or a second page, so still a 0 [half followed (0)] |
| Hybrid | Amazon--21 | 2 | 0 | 3 | done | The done text has a mismatch. The $179.99 price with a 4.0 rating belongs to the White/Stainless variant (listing 13742), not the item named. The one visible in | right | saved the answer | wrong fact caught: the $179.99 price belonged to another variant; the final answer gives the DCC-3400NAS at $149 [saved it (0 to 1)] |
| Vision only | Amazon--12 | 0 | 0 | 4 | click | VISION: Box 33138 sits on the "Visit the First Ride On Store" line, not on the "(1,036)" review count link just below it, so this click would likely open the st | wrong | unclear | the index was the review count, "(1,036)" (step-4 saved page text: [33138] acrCustomerReviewText), not the store link; the agent scrolled instead and got stuck at step 14, so the rejection may have cost the answer |
| Vision only | Amazon--16 | 0 | 0 | 6 | click | VISION: The proposed index 32504 appears to be on the XX-Large button, which is visible next to X-Large. But I can't tell for sure whether 32504 is the XX-Large | wrong | no change | its own reason ends "the action is fine"; the planner clicked the neighbouring box (also XX-Large); the 0 is for steps from general Amazon flow [no change (0)] |
| Vision only | Google Search--19 | 0 | 0 | 4 | click | VISION: Index 5348 is labeled on the "Files changed" tab, which is the right target, but the proposed click index 5348 matches that box (5348 near Files changed | wrong | no change | its own reason says "approving is correct"; the same click went through next step [no change (1), cost a step] |
| Vision only | Google Search--31 | 1 | 0 | 11 | done | VISION: The answer rests only on Google's AI Overview and the agent never opened a source page: its Wikipedia navigations (steps 2-5, 8, 9) are not noted as con | over-strict | no change | the AI Overview answer said it wasn't confirmed, so the rule would count it; it did confirm on Wikipedia, a better answer [no change (1)] |
| Vision only | Amazon--21 | 0 | 1 | 2 | done | VISION: The done text admits the stainless steel finish wasn't verified. The page also shows other candidates, including the 4.0-rated Cuisinart DCC-3400NAS, wh | right | ignored | stainless steel not shown for the DCC-1200NAS (the Amazon--21 rule); it never showed it, so still a 0 [ignored (0)] |
| Vision only | Amazon--21 | 0 | 1 | 4 | done | VISION: The current screenshot shows only a "Results" panel with one product (Cuisinart DCC-1200NAS, 4.5 stars, $129.95) and no filter sidebar. It doesn't show  | right | ignored | false claim caught: said a stainless filter was applied when none was; the final answer still claims it [ignored (0)] |

### No-verifier 0s a verifier might have caught

Candidates: the no-verifier 0s tagged `false_claim`, `not_in_record` or `element`.

| Task | Repeat | Tag | Note | Suggested | Why |
|---|---|---|---|---|---|
| Amazon--6 | 0 | `false_claim` | 9 Nov: the source claims a sort it read, but the list was Sort by: Featured / runner: gave up with its own answer: review it (it can be right, e.g. nothing meet) | likely caught | the DOM and hybrid verifiers turned down one-page "none" answers 3 times, and a sort the page doesn't show is in the verifier's done rules; catching isn't fixing (4 of 14 right rejections were ignored) |
| Amazon--16 | 0 | `false_claim` | 9 Nov: Size: label stayed X-Large in the saved page text after clicking XX-Large; the answer claims XX-Large | likely caught | hybrid's verifier caught exactly this ("Size: X-Large selected, not XX-Large"), though there the agent ignored it |
| Amazon--16 | 1 | `element` | 9 Nov: Size: label stayed X-Large (the answer says so itself); steps not read from the popup or help page | flagged, no change | the answer already says the size stayed X-Large; a verifier could flag it but not make the size button work |
| Amazon--6 | 2 | `false_claim` | 9 Nov: the source claims a sort it read, but the list was Sort by: Featured / runner: gave up with its own answer: review it (it can be right, e.g. nothing meet) | likely caught | the same claimed sort as r0 |
