This is a primer on the state of and plan for the MVP if anyone needs to take over or complete a specific task.
## current state - last bit of design consideration before implementation
requirements
- describe and complete gui agent actions (w/ or w/o user approval based on preferences)
- describe webpages (aka conversationally answer questions about what's on the screen)
- save relevant data to its database (which may be in an aws bucket because of the glasses)
- create training data for non-commercial ai (temporary)
- receive images from meta glasses, prompt user about them and do* something w/ them
\*meaning of 'do' is unclear but assumedly process in some way, such as reading links or looking up products
## plan
### pipeline state
- original query (str)
- stoppers based on preference (e.g. do not attempt to logins, assuming they're saved in settings)
- human approval frequencies (saved in settings)
- set or properties that update as the graph progresses
  - relevant perception information (to adjust the plan, may not be required if put into one node, string? coordinates? both?)
  - plan (to execute and to send to the user in plain text for approval, if needed, most likely string)
  - what to save in database for training
  - probably other things i am not thinking about
### nodes
- receive prompt (not a node, connects to UI and waits)
- START (prompt received)
- respond --> plan or END (orchestrator)
  - answers questions about what the webpage looks like or where an element is
  - can be reused to ask what went wrong when human approval is denied
  - if senses task, offers to complete task
  - if it's obviously a task from the start, skip and switch to task immediately
- plan (seems to be handled on its own, but need to figure out the DOM vs vis mapping first, parallelisation)
- analyse prompt and adjust plan, ask for approval --> execute or respond (now adjusted to ask what's wrong, synthesiser + evaluator + router, may be doing too much)
- execute step
- resolve, save relevant data (send to database) and figure out if plan executed --> END or plan (router)
- END (back to listening)
### others
- error: default handler, define statuses, and test how it's going
- data:
  - checkpoints for human in the loop, and managing length of checkpoints
  - schema + method to send relevant information to the database before it's reset for the next prompt; may use stor
  - these have to be changed once production is involved
- prompts: need to pay close attention to token usage, context window... re: usage costs