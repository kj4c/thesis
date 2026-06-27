This is a primer on the state of and plan for the MVP if anyone needs to take over or complete a specific task.

requirements
- describe and complete gui agent actions (w/ or w/o user approval based on preferences)
- describe webpages (aka conversationally answer questions about what's on the screen)
- save relevant data to its database (which may be in an aws bucket because of the glasses)
- create training data for non-commercial ai (temporary)
- receive images from meta glasses, prompt user about them and do* something w/ them
\*meaning of 'do' is unclear but assumedly process in some way, such as reading links or looking up products
## current state - before implementation
note: the current version of everything after 'before implemention' was written purely based off report, not research
plan
- before implementation
  - research specific coding options (e.g. how branching works in langchain & pydantic schemas)
  - define & draw proper graph w/ technical concepts this time
  - check w/ others
- pipeline state should likely include
  - original query (str)
  - stoppers based on preference (e.g. do not attempt to logins, assuming they're saved in settings)
  - human approval frequencies (saved in settings)
  - set or properties that update as the graph progresses
    - relevant perception information (to adjust the plan, may not be required if put into one node, string? coordinates? both?)
    - plan (to execute and to send to the user in plain text for approval, if needed, most likely string)
    - what to save in database for training
    - probably other things i am not thinking about
- nodes
  - receive prompt (not a node, connects to UI and waits)
  - START (prompt received)
  - respond --> plan or END
    - answers questions about what the webpage looks like or where an element is
    - can be reused to ask what went wrong when human approval is denied
    - if senses task, offers to complete task
    - if it's obviously a task from the start, skip and switch to task immediately
  - plan (seems to be handled on its own, but need to figure out the DOM vs vis mapping first)
  - analyse prompt and adjust plan, ask for approval --> execute or respond (now adjusted to ask what's wrong)
  - execute step
  - resolve, save relevant data (send to database) and figure out if plan executed --> END or plan
  - END (back to listening)
- edges require safety measures (reset limits, branching paths after failure...)
- database requires a schema + method to send relevant information to the database before it's reset for the next prompt
- need to pay close attention to token usage, context window... re: usage costs