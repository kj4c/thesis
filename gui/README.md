# gui agent

a desktop app (wxPython) that lets you give a browser-automation agent (the
LangGraph pipeline in `graph.py` / `nodes.py`, which drives
[browser-use](https://github.com/browser-use/browser-use)) a task in plain
english, watch it work step by step, and approve or deny what it does along
the way. it also supports voice input and can receive prompts pushed from a
pair of Meta smart glasses.

## setup

requires Python 3.14 (see `.venv/pyvenv.cfg`).

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install browser-use langgraph wxPython aiohttp zeroconf sounddevice numpy google-genai python-dotenv
playwright install chromium   # browser-use drives a real Chromium instance
```

copy `.env.example` to `.env` and fill in your api keys:

```bash
cp .env.example .env
```

- the agent's models are chosen in `.env`, not in the gui: `PLANNER_PROVIDER`
  picks the planner, `VISION_PROVIDER` the second-opinion check, and
  `GLASSES_VISION_PROVIDER` the model that reads photos from the glasses
  (see `llm.py` for the defaults). fill in the key for each provider you use.
- `GOOGLE_API_KEY` — also needed for voice transcription, whichever
  providers the agent uses.

## running the ui

```bash
python run.py
```

this launches the main window (`gui/main.py:AgentApp`).

## features

### task input and agent runs
- type a task into the **task** box and click **run agent** (or press the
  configured shortcut, default `Ctrl+Return`) to start the browser agent.
- while an agent is active, submitting a new task sends it as a **follow-up**
  on the same page/session rather than starting over, and the agent remembers
  the conversation so far.
- **new task** closes the current browser session and forgets the
  conversation, so the next run starts fresh instead of continuing as a
  follow-up.

### activity log
- every agent step is logged in plain english: what page it's on, its
  current plan, and the action(s) it's taking (navigating, clicking, typing,
  scrolling, extracting page content, etc.). if the agent's own check turns
  a step down, the log says why and the agent plans again.
- a run ends with the agent's reply, logged as `[Done]` if the agent finished
  the task and `[Error]` if it didn't (gave up, ran out of steps, or failed);
  when the reply was shortened for speaking aloud, the full answer is shown
  underneath.
- the status bar's first field says what the agent is doing:
  - **ready**: on, and doing nothing.
  - **running**: working on a task.
  - **done**: finished the task successfully; back to ready after 5 seconds.
  - **stopped**: waiting for you to decide (an approval, or what to do with a
    glasses prompt); back to running if the agent carries on, ready if not.
  - **error**: the agent reported a failure; back to running if it carries on
    (a failed step it retries), ready after 5 seconds once the task has ended.
- voice input shows its state on the voice button and in the spoken log, not
  in the status bar.

### permission prompts
- before the agent acts in the browser, a dialog can pop up describing
  exactly what it's about to do — you can **approve** or **deny** each step.
  the final "here is the answer" step never asks.
- configurable frequency (see settings → permissions):
  - **every step** — ask before every action.
  - **when switching to a new site** — only ask when the agent navigates to
    a new domain.
  - **only when an error occurs** — only ask if the previous step failed or
    was turned down by the agent's own check.
- denying a step stops the agent run; the browser stays open and you can
  send a follow-up.

### voice input
- toggle **voice input** to record from your microphone; toggle it again to
  stop and transcribe. the transcription (via Gemini) is appended into the
  task box.

### smart glasses integration
- toggle **glasses** to start a local http listener (`GlassesServer`) that
  the companion ios app (`testApp.swift`) POSTs prompts and photos to
  (`/data` endpoint; `/health` for connectivity checks). it takes the place
  of `serve.py` and uses the same port (8765), so run one or the other.
- set `host` in `testApp.swift` to the lan address shown in settings →
  smart glasses.
- when data arrives, a dialog shows the prompt (and any received photo/video)
  with three choices:
  - **act on it** — runs the prompt as an agent task. a photo is described
    by the glasses vision model first, so "this product" means what you were
    looking at.
  - **dismiss for now** — keeps the item but takes no action.
  - **cancel (erase)** — deletes the item and its saved media.
- the phone waits for the outcome and reads it aloud: the agent's answer, or
  that the request was dismissed, cancelled, denied, or arrived while the
  agent was busy. it gives up after 3 minutes, so a run that sits on an
  approval dialog for too long is reported on the phone as a network error.
- settings → smart glasses shows the lan and mdns addresses to point the
  glasses app at, lets you change the listening port, and toggle
  auto-start on launch.

### screen readers
the app is set up for VoiceOver on macos (`gui/a11y.py`):
- every control has a spoken name, including the task box, the activity
  log, and the fields and options in settings. the status bar is readable
  as a line of text.
- the moments that matter are spoken without moving focus: the agent
  starting, its answer (or that it stopped or hit an error), voice
  recording and transcription, the glasses listener turning on or off, and
  a phone request being turned away because the agent is busy. individual
  steps are not narrated; they are in the activity log.
- a permission or glasses dialog brings the app to the front and starts on
  its details, so it is read out even if the browser window was in front.

this relies on `pyobjc-framework-Cocoa`, which `browser-use` already pulls
in on macos. on other platforms the names are still set, but nothing is
spoken.

### settings
opened via the **settings** button; three tabs:
- **input** — choose text or voice as the default input method, and set the
  keyboard shortcut used to submit a task.
- **permissions** — choose how often the agent asks for approval (see above).
- **smart glasses** — connection info, port, autostart, and start/stop the
  listener.

settings are persisted to `settings.json` in the project root.
