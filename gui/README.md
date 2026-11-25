# the desktop app

A wxPython app, built for VoiceOver, for giving the agent a task in plain
English, following it step by step, and approving or denying what it does. It
also takes voice input and prompts from a pair of Meta smart glasses.

Setup and the models are in the [main README](../README.md). Start it with:

```bash
python run.py
```

## connecting the glasses from any network

The agent runs on this Mac; the phone reaches it through tailscale, a free
private network between your own devices (nothing is exposed to the internet).

1. On the Mac: `brew install --cask tailscale-app`, open tailscale and sign in.
2. On the iPhone: install **tailscale** from the app store and sign in with the
   same account.
3. Start the agent (`python run.py` or `python serve.py`). The Mac's tailscale
   address (`100.x.x.x:8765`) is shown in settings → smart glasses, or printed
   by `serve.py` at startup.
4. In the glasses app, tap the ladybug and enter that address as the backend.
   It's remembered, so switch back to the wi-fi address the same way.

The Mac has to stay awake: keep it plugged in and run the agent under
`caffeinate -i`, e.g. `caffeinate -i python run.py`.

## features

### task input and agent runs
- Type a task into the **task** box and click **run agent** (or press the
  configured shortcut, default `Ctrl+Return`) to start the browser agent.
- While an agent is active, submitting a new task sends it as a **follow-up**
  on the same page/session rather than starting over, and the agent remembers
  the conversation so far.
- **New task** closes the current browser session and forgets the
  conversation, so the next run starts fresh instead of continuing as a
  follow-up.

### activity log
- Every agent step is logged in plain English: what page it's on, its
  current plan, and the action(s) it's taking (navigating, clicking, typing,
  scrolling, extracting page content, etc.). If the agent's own check turns
  a step down, the log says why and the agent plans again.
- A run ends with the agent's reply, logged as `[Done]` if the agent finished
  the task and `[Error]` if it didn't (gave up, ran out of steps, or failed).
  The screen reader reads the whole reply; only the reply sent back to the
  glasses phone is shortened.
- The status bar's first field says what the agent is doing:
  - **Ready**: on, and doing nothing.
  - **Running**: working on a task.
  - **Done**: finished the task successfully; back to ready after 5 seconds.
  - **Stopped**: waiting for you to decide (an approval, or what to do with a
    glasses prompt); back to running if the agent carries on, ready if not.
  - **Error**: the agent reported a failure; back to running if it carries on
    (a failed step it retries), ready after 5 seconds once the task has ended.
- Voice input shows its state on the voice button and in the spoken log, not
  in the status bar.

### permission prompts
- Before the agent acts in the browser, a dialog can pop up describing
  exactly what it's about to do — you can **approve** or **deny** each step.
  the final "here is the answer" step never asks.
- Configurable frequency (see settings → permissions):
  - **Every step** — ask before every action.
  - **When switching to a new site** — only ask when the agent navigates to
    a new domain.
  - **Only for risky steps** — only ask before a click or typing that commits
    to something (buying, sending, signing in, passwords and card details).
  - **Only when an error occurs** — only ask if the previous step failed or
    was turned down by the agent's own check.
- Deny with a reason (e.g. "wrong product") and the agent tries another way;
  deny without one, or press **stop task**, and the task ends. The browser
  stays open either way, so you can send a follow-up.

### voice input
- Toggle **voice input** to record from your microphone; toggle it again to
  stop and transcribe. The transcription (via Gemini) is appended into the
  task box.

### smart glasses integration
- Toggle **glasses** to start a local http listener (`GlassesServer`) that
  the companion ios app (`testApp.swift`) POSTs prompts and photos to
  (`/data` endpoint; `/health` for connectivity checks). It takes the place
  of `serve.py` and uses the same port (8765), so run one or the other.
- In the glasses app, tap the ladybug and enter the lan address shown in
  settings → smart glasses (or the tailscale one, below).
- When data arrives, a dialog shows the prompt (and any received photo/video)
  with three choices:
  - **Act on it** — runs the prompt as an agent task. A photo is described
    by the glasses vision model first, so "this product" means what you were
    looking at.
  - **Dismiss for now** — keeps the item but takes no action.
  - **Cancel (erase)** — deletes the item and its saved media.
- The phone waits for the outcome and reads it aloud: the agent's answer, or
  that the request was dismissed, cancelled, denied, or arrived while the
  agent was busy. It gives up after 3 minutes, so a run that sits on an
  approval dialog for too long is reported on the phone as a network error.
- Settings → smart glasses shows the lan and mdns addresses to point the
  glasses app at, lets you change the listening port, and toggle
  auto-start on launch.

### add an event from a glasses photo
- Ask the glasses app to capture the event poster and say something explicit
  such as **“Add this event to my calendar.”** The same `/data` server delivers
  the photo and prompt to the GUI.
- The app reads the title, date, time, and location, then opens an editable
  review dialog. It adds nothing to Google Calendar until you select **Add to
  Calendar**. If a date or time is unclear, fill it in before confirming.
- Connect your account in **Settings → Calendar → Choose… → Connect Google Account**.
  Google sign-in opens in your default browser, and you grant the app permission
  there. The app never asks for your Google password or an API key.
- One-time app setup requires an OAuth Desktop client configuration:
  1. In Google Cloud Console, create or select a project and enable the Google
     Calendar API.
  2. Configure OAuth consent for your Google account and create an OAuth
     client with application type **Desktop app**. For a project in testing
     mode, add your Google account as a test user.
  3. Download the client JSON, then select it in Settings. This file identifies
     the app to Google; each user still connects their own account in the browser.
- The connection record is stored in a local SQLite database under the user's
  application-support directory. OAuth credentials are encrypted in that
  database with a key kept in the operating system credential store. Disconnect
  in Settings to revoke Google access and remove the saved local connection.
- The app asks for access to events on the account's primary calendar. The
  client JSON is not a password or a private API key: desktop apps are public
  OAuth clients. A public release may need Google's OAuth verification.

### screen readers
The app is set up for VoiceOver on macos (`gui/a11y.py`):
- Every control has a spoken name, including the task box, the activity
  log, and the fields and options in settings. The status bar is readable
  as a line of text.
- The moments that matter are spoken without moving focus: the agent
  starting, its answer (or that it stopped or hit an error), voice
  recording and transcription, the glasses listener turning on or off, and
  a phone request being turned away because the agent is busy. Individual
  steps are not narrated; they are in the activity log.
- Chrome takes the front when it opens and whenever it opens or switches a
  tab; the app takes it back after the browser starts, after each step, for
  each dialog, and when a task ends (then speaks the outcome a moment later).
- While a task runs, focus waits on the activity log, and closing a dialog
  (by any button, return or escape) puts it back there; when the task ends,
  focus goes to the task box.
- In the dialog's reason field, return denies, the same as escape.
- With approval on every step the plan isn't spoken, since the dialog that
  follows shows it and would cut the speech off.
- A permission or glasses dialog brings the app to the front and starts on
  its details, so it is read out even if the browser window was in front.

This relies on `pyobjc-framework-Cocoa`, which `browser-use` already pulls
in on macOS. On other platforms the names are still set, but nothing is
spoken.

### settings
Opened via the **settings** button; four tabs:
- **Input** — choose text or voice as the default input method, and set the
  keyboard shortcut used to submit a task.
- **Permissions** — choose how often the agent asks for approval (see above).
- **Smart glasses** — connection info, port, autostart, and start/stop the
  listener.
- **Calendar** — connect or disconnect a Google account (see above).

Settings are persisted to `settings.json` in the project root.
