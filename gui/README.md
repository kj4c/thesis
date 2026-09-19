# gui agent

a desktop app (wxPython) that lets you give a browser-automation agent
([browser-use](https://github.com/browser-use/browser-use)) a task in plain
english, watch it work step by step, and approve or deny what it does along
the way. it also supports voice input and can receive prompts pushed from a
pair of Meta smart glasses.

## setup

requires Python 3.14 (see `.venv/pyvenv.cfg`).

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install browser-use wxPython aiohttp sounddevice numpy google-genai python-dotenv
playwright install chromium   # browser-use drives a real Chromium instance
```

copy `.env.example` to `.env` and fill in your api keys:

```bash
cp .env.example .env
```

- `GOOGLE_API_KEY` — required. powers the agent's llm (Gemini) and voice
  transcription.
- `CLAUDE_API_KEY` / `OPENAI_API_KEY` — only needed if you switch the agent
  to a different model in `gui/main.py`.

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
  on the same page/session rather than starting over.
- **new task** closes the current browser session so the next run starts
  fresh instead of continuing as a follow-up.

### activity log
- every agent step is logged in plain english: what page it's on, its
  current plan, and the action(s) it's taking (navigating, clicking, typing,
  scrolling, extracting page content, etc.).
- status bar shows `ready` / `running…` / `done` / `stopped` / `error`.

### permission prompts
- before the agent proceeds, a dialog can pop up describing exactly what
  it's about to do — you can **approve** or **deny** each step.
- configurable frequency (see settings → permissions):
  - **every step** — ask before every action.
  - **when switching to a new site** — only ask when the agent navigates to
    a new domain.
  - **only when an error occurs** — only ask if the agent's own evaluation
    of its last step reports a failure.
- denying a step stops the agent run.

### voice input
- toggle **voice input** to record from your microphone; toggle it again to
  stop and transcribe. the transcription (via Gemini) is appended into the
  task box.

### smart glasses integration
- toggle **glasses** to start a local http listener (`GlassesServer`) that
  Meta glasses' companion app can POST prompts/media to (`/data` endpoint;
  `/health` for connectivity checks).
- when data arrives, a dialog shows the prompt (and any received photo/video)
  with three choices:
  - **act on it** — runs the prompt as a new agent task immediately.
  - **dismiss for now** — keeps the item but takes no action.
  - **cancel (erase)** — deletes the item and its saved media.
- settings → smart glasses shows the lan and mdns addresses to point the
  glasses app at, lets you change the listening port, and toggle
  auto-start on launch.

### settings
opened via the **settings** button; three tabs:
- **input** — choose text or voice as the default input method, and set the
  keyboard shortcut used to submit a task.
- **permissions** — choose how often the agent asks for approval (see above).
- **smart glasses** — connection info, port, autostart, and start/stop the
  listener.

settings are persisted to `settings.json` in the project root.
