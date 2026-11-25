# The agent's graph

Every route in `graph.py` and the nodes' own routing (`nodes.py`: each node's
`Command[Literal[...]]`). Mermaid; paste into any Mermaid renderer for the report.

```mermaid
flowchart LR
    START([start]) --> respond
    respond -- "a task" --> make_plan
    respond -- "a question: answered" --> END([end])
    make_plan --> plan
    plan --> analyse
    analyse -- "passed" --> approve
    analyse -- "turned down, with the reason" --> plan
    analyse -- "turned down at the step limit" --> resolve
    approve -- "yes, or not asked" --> execute
    approve -- "no, with a reason" --> plan
    approve -- "no, at the step limit" --> resolve
    approve -- "stop, or no without a reason" --> END
    execute --> resolve
    resolve -- "not done" --> plan
    resolve -- "done, or the step limit" --> END
```

- **respond**: the front desk; a task, or a question it answers itself.
- **make_plan**: the task's plan, a few plain steps, once per task (skipped for the glasses' price lookups).
- **plan**: picks the next action from the page.
- **analyse**: the checks in code, then the verifier model (versions with `VERIFIER=on`).
- **approve**: asks the user, if the approval setting calls for it.
- **execute**: does the action in the browser.
- **resolve**: finishes the task or goes back to plan.

When a node fails after its retries, the error handler (`graph.default_error_handler`)
ends the task with a plain message, except a failed `execute`, which goes back to
plan once.
