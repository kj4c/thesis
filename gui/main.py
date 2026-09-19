import asyncio
import concurrent.futures
import json
import os
import threading
from urllib.parse import urlparse

import wx

from browser_use import Agent, BrowserSession, ChatGoogle
from dotenv import load_dotenv
from .glasses_server import GlassesServer
from .voice import VoiceRecorder, transcribe as transcribe_voice

load_dotenv()

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'settings.json')

DEFAULT_SETTINGS = {
    'input_method': 'text',
    'shortcut': 'Ctrl+Return',
    'permission_frequency': 'every_step',  # every_step | new_site | on_error
    'glasses_port': 8765,
    'glasses_autostart': False,
}


def load_settings():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        return {**DEFAULT_SETTINGS, **data}
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(settings, f, indent=2)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


def _describe_page(title: str, url: str) -> str:
    if title and title.strip().lower() not in ('', 'empty tab', 'new tab'):
        domain = _domain(url)
        return f'{title} ({domain})' if domain else title
    return _domain(url) or 'a new blank tab'


def _describe_click(p: dict) -> str:
    if p.get('index') is not None:
        return f'Clicking element #{p["index"]} on the page'
    if p.get('coordinate_x') is not None:
        return f'Clicking at position ({p["coordinate_x"]}, {p["coordinate_y"]}) on the page'
    return 'Clicking something on the page'


# plain-English phrasing for each browser_use action, keyed by the action's
# field name (see browser_use/tools/service.py action registrations)
ACTION_DESCRIPTIONS = {
    'search': lambda p: f'Searching {p.get("engine", "the web")} for "{p.get("query", "")}"',
    'navigate': lambda p: (
        f'Opening {p.get("url", "a page")}' + (' in a new tab' if p.get('new_tab') else '')
    ),
    'go_back': lambda p: 'Going back to the previous page',
    'wait': lambda p: f'Waiting {p.get("seconds", "a few")} seconds',
    'click': _describe_click,
    'input': lambda p: f'Typing "{p["text"]}"' if p.get('text') else 'Clearing a text field',
    'scroll': lambda p: 'Scrolling down the page' if p.get('down', True) else 'Scrolling up the page',
    'send_keys': lambda p: f'Pressing {p.get("keys", "")}',
    'switch': lambda p: 'Switching to another browser tab',
    'close': lambda p: 'Closing a browser tab',
    'extract': lambda p: f'Reading the page to find: {p.get("query", "")}',
    'search_page': lambda p: f'Searching the page for "{p.get("pattern", "")}"',
    'find_elements': lambda p: 'Looking for items on the page',
    'find_text': lambda p: f'Looking for "{p.get("text", "")}" on the page',
    'screenshot': lambda p: 'Taking a screenshot',
    'save_as_pdf': lambda p: 'Saving the page as a PDF',
    'select_dropdown': lambda p: f'Selecting "{p.get("text", "")}" from a dropdown',
    'dropdown_options': lambda p: 'Checking the options in a dropdown',
    'upload_file': lambda p: 'Uploading a file',
    'write_file': lambda p: f'Saving notes to {p.get("file_name", "a file")}',
    'read_file': lambda p: f'Reading {p.get("file_name", "a file")}',
    'replace_file': lambda p: f'Updating {p.get("file_name", "a file")}',
    'evaluate': lambda p: 'Running a script on the page',
    'done': lambda p: p.get('text') or 'Finishing up',
}


def _describe_action(action) -> str:
    try:
        data = action.model_dump(exclude_unset=True)
    except Exception:
        return str(action)
    if not data:
        return ''
    name, params = next(iter(data.items()))
    params = params or {}
    formatter = ACTION_DESCRIPTIONS.get(name)
    if formatter:
        try:
            return formatter(params)
        except Exception:
            pass
    return name.replace('_', ' ').capitalize()


# permission dialog

class PermissionDialog(wx.Dialog):
    def __init__(self, parent, description: str):
        super().__init__(parent, title='Permission Required',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.STAY_ON_TOP)
        self.approved = False
        self._build(description)
        self.Centre()

    def _build(self, description: str):
        sizer = wx.BoxSizer(wx.VERTICAL)

        heading = wx.StaticText(self, label='The agent wants to perform:')
        heading.SetFont(heading.GetFont().Bold())
        sizer.Add(heading, 0, wx.ALL, 12)

        desc_ctrl = wx.TextCtrl(
            self, value=description,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SIMPLE,
        )
        desc_ctrl.SetName('Requested action details')
        desc_ctrl.SetMinSize((460, 100))
        sizer.Add(desc_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        btn_sizer = wx.StdDialogButtonSizer()
        approve = wx.Button(self, wx.ID_OK, 'Approve')
        deny = wx.Button(self, wx.ID_CANCEL, 'Deny')
        approve.SetDefault()
        btn_sizer.AddButton(approve)
        btn_sizer.AddButton(deny)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizerAndFit(sizer)
        approve.Bind(wx.EVT_BUTTON, lambda _: self._close(True))
        deny.Bind(wx.EVT_BUTTON, lambda _: self._close(False))

    def _close(self, approved: bool):
        self.approved = approved
        self.EndModal(wx.ID_OK if approved else wx.ID_CANCEL)


# glasses data dialog - shown when data arrives from the glasses
# matches the act/dismiss/cancel decision in figure 5.2

class GlassesDataDialog(wx.Dialog):

    ACT = 'act'
    DISMISS = 'dismiss'
    CANCEL = 'cancel'

    def __init__(self, parent, item: dict):
        super().__init__(parent, title='Glasses Data Received',
                         style=wx.DEFAULT_DIALOG_STYLE | wx.STAY_ON_TOP)
        self.choice = self.CANCEL
        self._build(item)
        self.Centre()

    def _build(self, item: dict):
        sizer = wx.BoxSizer(wx.VERTICAL)

        media_label = wx.StaticText(
            self, label=f"Received {item.get('media_type', 'data')} from glasses:"
        )
        media_label.SetFont(media_label.GetFont().Bold())
        sizer.Add(media_label, 0, wx.ALL, 12)

        prompt_ctrl = wx.TextCtrl(
            self, value=item.get('prompt', ''),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SIMPLE,
        )
        prompt_ctrl.SetName('Prompt received from glasses')
        prompt_ctrl.SetMinSize((440, 80))
        sizer.Add(prompt_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        # show filename if media was saved to disk
        media_path = item.get('media_path', '')
        if media_path and os.path.exists(media_path):
            path_label = wx.StaticText(self, label=f'File: {os.path.basename(media_path)}')
            path_label.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            sizer.Add(path_label, 0, wx.LEFT | wx.TOP, 12)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.ALL, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)

        act_btn = wx.Button(self, label='Act on it')
        act_btn.SetDefault()
        btn_row.Add(act_btn, 0, wx.RIGHT, 8)

        dismiss_btn = wx.Button(self, label='Dismiss for now')
        btn_row.Add(dismiss_btn, 0, wx.RIGHT, 8)

        cancel_btn = wx.Button(self, label='Cancel (erase)')
        btn_row.Add(cancel_btn, 0)

        sizer.Add(btn_row, 0, wx.ALL | wx.ALIGN_CENTER, 12)
        self.SetSizerAndFit(sizer)

        act_btn.Bind(wx.EVT_BUTTON, lambda _: self._close(self.ACT))
        dismiss_btn.Bind(wx.EVT_BUTTON, lambda _: self._close(self.DISMISS))
        cancel_btn.Bind(wx.EVT_BUTTON, lambda _: self._close(self.CANCEL))

    def _close(self, choice: str):
        self.choice = choice
        self.EndModal(wx.ID_OK)


# main window

class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title='GUI Agent', size=(720, 580))
        self.settings = load_settings()
        self._agent_future: concurrent.futures.Future | None = None
        self._permission_ = threading.Event()
        self._permission_approved = False
        self._prev_domain: str = ''
        self._agent: Agent | None = None
        self._browser_session: BrowserSession | None = None

        # one persistent asyncio loop for the app's lifetime, so a kept-alive
        # BrowserSession's async resources (event bus, CDP connection) survive
        # across multiple "Run Agent" clicks instead of dying with a one-shot loop
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

        port = self.settings.get('glasses_port', 8765)
        self._glasses_server = GlassesServer(
            port, lambda item: wx.CallAfter(self._on_glasses_data, item)
        )
        self._voice_recorder = VoiceRecorder()

        self._build_ui()
        self._apply_shortcut()
        self.Centre()
        self.Show()

        if self.settings.get('glasses_autostart'):
            self._start_glasses_server()

        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _build_ui(self):
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        # prompt input
        root.Add(wx.StaticText(panel, label='Task:'), 0, wx.LEFT | wx.TOP, 10)
        self.prompt = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.prompt.SetMinSize((-1, 80))
        self.prompt.SetHint('Describe the task for the agent…')
        self.prompt.SetName('Task description')
        root.Add(self.prompt, 0, wx.EXPAND | wx.ALL, 10)

        # buttons
        btn_row = wx.BoxSizer(wx.HORIZONTAL)

        self.run_btn = wx.Button(panel, label='Run Agent')
        self.run_btn.SetDefault()
        btn_row.Add(self.run_btn, 0, wx.RIGHT, 8)

        self.voice_btn = wx.ToggleButton(panel, label='Voice Input')
        self.voice_btn.SetValue(self.settings.get('input_method') == 'voice')
        btn_row.Add(self.voice_btn, 0, wx.RIGHT, 8)

        self.glasses_btn = wx.ToggleButton(panel, label='Glasses: Off')
        btn_row.Add(self.glasses_btn, 0, wx.RIGHT, 8)

        self.new_task_btn = wx.Button(panel, label='New Task')
        self.new_task_btn.SetToolTip(
            'Close the current browser session so the next run starts fresh, '
            'instead of continuing as a follow-up.'
        )
        btn_row.Add(self.new_task_btn, 0, wx.RIGHT, 8)

        btn_row.AddStretchSpacer()
        settings_btn = wx.Button(panel, label='Settings')
        btn_row.Add(settings_btn, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # activity log
        root.Add(wx.StaticText(panel, label='Activity:'), 0, wx.LEFT, 10)
        self.log = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_AUTO_URL | wx.BORDER_SIMPLE,
        )
        self.log.SetName('Activity log')
        root.Add(self.log, 1, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(root)

        self.status_bar = self.CreateStatusBar(2)
        self.status_bar.SetStatusWidths([-1, 180])
        self.status_bar.SetStatusText('Ready')
        self.status_bar.SetStatusText('Glasses: off', 1)

        self.prompt.Bind(wx.EVT_KEY_DOWN, self._on_prompt_key_down)
        self.run_btn.Bind(wx.EVT_BUTTON, self._on_run)
        self.new_task_btn.Bind(wx.EVT_BUTTON, self._on_new_task)
        self.voice_btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_voice_toggle)
        self.glasses_btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_glasses_toggle)
        settings_btn.Bind(wx.EVT_BUTTON, self._on_settings)

    def _on_prompt_key_down(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.ShiftDown():
            self._on_run(event)
            return
        event.Skip()

    def _apply_shortcut(self):
        shortcut = self.settings.get('shortcut', 'Ctrl+Return')
        mod = wx.ACCEL_CTRL if 'Ctrl' in shortcut else wx.ACCEL_NORMAL
        key = wx.WXK_RETURN if 'Return' in shortcut else ord(shortcut[-1])
        self.SetAcceleratorTable(
            wx.AcceleratorTable([(mod, key, self.run_btn.GetId())])
        )

    # log helpers, safe to call from any thread

    def _log(self, msg: str):
        wx.CallAfter(self.log.AppendText, msg + '\n')

    def _set_status(self, text: str):
        wx.CallAfter(self.status_bar.SetStatusText, text)

    def _announce_run_finished(self):
        # move focus to the end of the log so screen readers pick up the
        # final [Done]/[Stopped]/[Error] line without narrating every step
        self.log.SetInsertionPointEnd()
        self.log.SetFocus()

    # agent execution

    def _on_run(self, _):
        if self._agent_future is not None and not self._agent_future.done():
            self._log('[!] Agent is already running.')
            return

        task = self.prompt.GetValue().strip()
        if not task:
            wx.MessageBox('Please enter a task.', 'No Task',
                          wx.OK | wx.ICON_WARNING, self)
            return

        if self._agent is not None:
            self._log(f'\nFollow-up: {task}')
        else:
            self.log.Clear()
            self._prev_domain = ''
            self._log(f'Task: {task}')

        self.prompt.Clear()
        self._set_status('Running…')
        self.run_btn.Disable()
        self.new_task_btn.Disable()

        self._agent_future = asyncio.run_coroutine_threadsafe(
            self._run_agent_async(task), self._loop
        )

    async def _run_agent_async(self, task: str):
        try:
            if self._agent is None:
                llm = ChatGoogle(model='gemini-flash-latest')
                # keep_alive keeps the browser open after run() so a follow-up
                # question can continue on the same page instead of starting over
                self._browser_session = BrowserSession(keep_alive=True)
                self._agent = Agent(
                    task=task,
                    llm=llm,
                    browser_session=self._browser_session,
                    register_new_step_callback=self._on_agent_step,
                )
            else:
                self._agent.add_new_task(task)

            await self._agent.run()
            self._log('\n[Done] Agent completed.')
            self._set_status('Done')
        except RuntimeError as e:
            if 'denied' in str(e).lower():
                self._log('[Stopped] Permission denied by user.')
                self._set_status('Stopped')
            else:
                self._log(f'[Error] {e}')
                self._set_status('Error')
        except Exception as e:
            self._log(f'[Error] {e}')
            self._set_status('Error')
        finally:
            wx.CallAfter(self.run_btn.Enable)
            wx.CallAfter(self.new_task_btn.Enable)
            wx.CallAfter(self._announce_run_finished)

    def _on_new_task(self, _):
        if self._agent_future is not None and not self._agent_future.done():
            wx.MessageBox('Please wait for the current run to finish.', 'Agent Running',
                          wx.OK | wx.ICON_WARNING, self)
            return

        session = self._browser_session
        self._agent = None
        self._browser_session = None
        self._prev_domain = ''
        self.log.Clear()
        self._set_status('Ready')

        if session is not None:
            asyncio.run_coroutine_threadsafe(session.kill(), self._loop)
            self._log('[New Task] Previous browser session closed.')
        else:
            self._log('[New Task] Ready.')

    async def _on_agent_step(self, state, output, step_num: int):
        goal = getattr(output, 'next_goal', '') or ''
        actions = getattr(output, 'action', None) or []
        url = getattr(state, 'url', '') or ''
        title = getattr(state, 'title', '') or ''

        self._log(f'\nStep {step_num}')
        self._log(f'  On page: {_describe_page(title, url)}')
        if goal:
            self._log(f'  Plan:    {goal}')
        for action in actions:
            description = _describe_action(action)
            if description:
                self._log(f'  Doing:   {description}')

        if self._should_ask_permission(url, output):
            description = self._format_permission_description(goal, actions, url, title)
            await asyncio.get_running_loop().run_in_executor(
                None, self._block_for_permission, description
            )
            if not self._permission_approved:
                raise RuntimeError('User denied permission.')

        self._prev_domain = _domain(url)

    def _should_ask_permission(self, url: str, output) -> bool:
        freq = self.settings.get('permission_frequency', 'every_step')
        if freq == 'every_step':
            return True
        if freq == 'new_site':
            return _domain(url) != self._prev_domain
        if freq == 'on_error':
            evaluation = getattr(output, 'evaluation_previous_goal', '') or ''
            return any(w in evaluation.lower() for w in ('fail', 'error', 'could not', 'unable'))
        return False

    @staticmethod
    def _format_permission_description(goal: str, actions, url: str, title: str) -> str:
        lines = []
        if goal:
            lines.append(f'Plan: {goal}')
        descriptions = [_describe_action(a) for a in (actions or [])]
        for description in descriptions:
            if description:
                lines.append(f'Action: {description}')
        if url:
            lines.append(f'Page: {_describe_page(title, url)}')
        return '\n'.join(lines) if lines else 'Continue to next step?'

    def _block_for_permission(self, description: str):
        self._permission_.clear()
        self._permission_approved = False
        wx.CallAfter(self._show_permission_dialog, description)
        self._permission_.wait()

    def _show_permission_dialog(self, description: str):
        dlg = PermissionDialog(self, description)
        dlg.ShowModal()
        self._permission_approved = dlg.approved
        dlg.Destroy()
        self._permission_.set()

    # glasses server

    def _start_glasses_server(self):
        started = self._glasses_server.start()
        if started:
            port = self._glasses_server.port
            self._log(f'[Glasses] Listening on port {port}.')
            self.status_bar.SetStatusText(f'Glasses: :{port}', 1)
            self.glasses_btn.SetLabel('Glasses: On')
            self.glasses_btn.SetValue(True)

    def _stop_glasses_server(self):
        self._glasses_server.stop()
        self._log('[Glasses] Listener stopped.')
        self.status_bar.SetStatusText('Glasses: off', 1)
        self.glasses_btn.SetLabel('Glasses: Off')
        self.glasses_btn.SetValue(False)

    def _on_glasses_toggle(self, _):
        if self.glasses_btn.GetValue():
            self._start_glasses_server()
        else:
            self._stop_glasses_server()

    def _on_glasses_data(self, item: dict):
        # called on the wx main thread when the http server receives glasses data
        self._log(f'\n[Glasses] Received {item["media_type"]}: {item["prompt"]}')

        dlg = GlassesDataDialog(self, item)
        dlg.ShowModal()
        choice = dlg.choice
        dlg.Destroy()

        store = self._glasses_server.store

        if choice == GlassesDataDialog.ACT:
            store.update_status(item['id'], 'acted')
            self._log(f'[Glasses] Acting on: {item["prompt"]}')
            self.prompt.SetValue(item['prompt'])
            self._on_run(None)

        elif choice == GlassesDataDialog.DISMISS:
            store.update_status(item['id'], 'dismissed')
            self._log('[Glasses] Dismissed — data kept for later.')

        else:
            store.update_status(item['id'], 'cancelled')
            store.delete_media(item)
            self._log('[Glasses] Cancelled — data erased.')

    # settings and voice

    def _on_voice_toggle(self, _):
        self.settings['input_method'] = 'voice' if self.voice_btn.GetValue() else 'text'
        save_settings(self.settings)
        if self.voice_btn.GetValue():
            self._start_voice_recording()
        else:
            self._stop_voice_recording()

    def _start_voice_recording(self):
        try:
            self._voice_recorder.start()
        except Exception as e:
            self._log(f'[Voice] Failed to start recording: {e}')
            self.voice_btn.SetValue(False)
            return
        self.voice_btn.SetLabel('Recording… (click to stop)')
        self._set_status('Recording voice input…')
        self._log('[Voice] Recording started.')

    def _stop_voice_recording(self):
        self.voice_btn.SetLabel('Voice Input')
        self.voice_btn.Disable()
        self._set_status('Transcribing…')
        self._log('[Voice] Recording stopped, transcribing…')
        threading.Thread(target=self._transcribe_recording, daemon=True).start()

    def _transcribe_recording(self):
        try:
            audio_bytes = self._voice_recorder.stop()
            if not audio_bytes:
                self._log('[Voice] No audio captured.')
                return
            text = transcribe_voice(audio_bytes)
            if text:
                wx.CallAfter(self._apply_transcript, text)
                self._log(f'[Voice] Transcribed: {text}')
            else:
                self._log('[Voice] Could not transcribe audio (empty result).')
        except Exception as e:
            self._log(f'[Voice] Transcription failed: {e}')
        finally:
            self._set_status('Ready')
            wx.CallAfter(self.voice_btn.Enable)

    def _apply_transcript(self, text: str):
        existing = self.prompt.GetValue().strip()
        combined = f'{existing} {text}'.strip() if existing else text
        self.prompt.SetValue(combined)

    def _on_settings(self, _):
        from .settings import SettingsDialog
        dlg = SettingsDialog(self, self.settings, self._glasses_server)
        if dlg.ShowModal() == wx.ID_OK:
            self.settings = dlg.get_settings()
            save_settings(self.settings)
            self._apply_shortcut()
            self.voice_btn.SetValue(self.settings.get('input_method') == 'voice')
        dlg.Destroy()

    def _on_close(self, event):
        self._glasses_server.stop()
        if self._browser_session is not None:
            asyncio.run_coroutine_threadsafe(self._browser_session.kill(), self._loop)
        # loop thread is a daemon; it dies with the process once wx's MainLoop returns,
        # so no explicit loop.stop() here (that could race with the kill() cleanup above)
        event.Skip()


class AgentApp(wx.App):
    def OnInit(self):
        MainFrame()
        return True


if __name__ == '__main__':
    app = AgentApp()
    app.MainLoop()
