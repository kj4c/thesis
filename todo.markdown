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
- broken up into conversational and executive
  - conversational:
    - answers questions about what the webpage looks like or where an element is
    - can be reused to ask what went wrong when human approval is denied
    - beyond deciding what information to save, sounds like it just loops until the user is done w/ a specific context
    - (specific context as in the user asks for something to be executed or indicates they're done)
    - (for the former, might be good to be able to send context over to 'executive')
    - ALTERNATIVELY, conversational is a single node that handles if a query is a question or a task on its own
  - everything else applies to executive
- pipeline state should likely include
  - original query (str)
  - human approval needed & human approved, adjustable based on preference (bools)
  - question seems good to keep as well (str)
  - stoppers based on preference (e.g. do not attempt to login, but that's if we leave that up to preference, which...)
  - set or properties that update as the graph progresses
    - relevant perception information (to adjust the plan, may not be required if put into one node, string? coordinates? both?)
    - plan (to execute and to send to the user in plain text for approval, if needed, most likely string)
    - success status (some combination of number and string to give detail on what went wrong and where?)
    - is finished (bools)
  - method to send relevant information to the database before it's reset for the next prompt
- nodes
  - receive prompt (not a node technically, connects to UI and waits)
  - START (prompt received)
  - POTENTIALLY, turn conversation graph into node and put it here to figure out if the query is a question or task
  - perceive webpage (seems to be handled on its own, but need to figure out the DOM vs vis mapping first)
  - analyse prompt and adjust plan (good grow to encompass what the previous node does)
  - execute step
  - save relevant data (send to database)
  - END (back to listening)
- edges require safety measures (reset limits, branching paths after failure...)
- database requires a schema
- need to pay close attention to token usage, context window... re: usage costs