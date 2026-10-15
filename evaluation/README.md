# evaluation

runs the agent on a subset of the WebVoyager benchmark and logs every step, for
the baseline and every later comparison (suggestions.markdown #10).

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
