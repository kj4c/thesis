import asyncio
import concurrent.futures
import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import wx

from browser_use import BrowserSession
from dotenv import load_dotenv
from glasses_vision import enrich_prompt_with_image
from graph import build_agent
from mvp import last_assistant_reply
from nodes import _decision_to_action
from reply_format import compact_glasses_reply
from state import PipelineState, _resources, _run_config, _start_resources
from . import a11y
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


async def _element_label(session: BrowserSession, index: int | None) -> str:
    # the visible name of a page element, so a click reads as
    # 'Clicking "Add to cart"' instead of 'Clicking element #12'
    if index is None:
        return ''
    try:
        node = await session.get_element_by_index(index)
        text = node.get_meaningful_text_for_llm() if node else ''
    except Exception:
        return ''
    return ' '.join(text.split())[:60]


def _describe_navigate(p: dict) -> str:
    url = p.get('url', '')
    parsed = urlparse(url)
    query = parse_qs(parsed.query).get('q')
    if 'google.' in parsed.netloc and parsed.path == '/search' and query:
        return f'Searching Google for "{query[0]}"'
    return f'Opening {url or "a page"}' + (' in a new tab' if p.get('new_tab') else '')


def _describe_input(p: dict) -> str:
    if not p.get('text'):
        return 'Clearing a text field'
    return f'Typing "{p["text"]}"' + (f' into "{p["label"]}"' if p.get('label') else '')


def _describe_click(p: dict) -> str:
    if p.get('label'):
        return f'Clicking "{p["label"]}"'
    if p.get('index') is not None:
        return f'Clicking element #{p["index"]} on the page'
    if p.get('coordinate_x') is not None:
        return f'Clicking at position ({p["coordinate_x"]}, {p["coordinate_y"]}) on the page'
    return 'Clicking something on the page'


# plain-English phrasing for each browser_use action, keyed by the action's
# field name (see browser_use/tools/service.py action registrations)
ACTION_DESCRIPTIONS = {
    'search': lambda p: f'Searching {p.get("engine", "the web")} for "{p.get("query", "")}"',
    'navigate': _describe_navigate,
    'go_back': lambda p: 'Going back to the previous page',
    'wait': lambda p: f'Waiting {p.get("seconds", "a few")} seconds',
    'click': _describe_click,
    'input': _describe_input,
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
    'done': lambda p: 'Finishing up',
}


def _describe_action(action, label: str = '') -> str:
    try:
        data = action.model_dump(exclude_unset=True)
    except Exception:
        return str(action)
    if not data:
        return ''
    name, params = next(iter(data.items()))
    params = {**(params or {}), 'label': label}
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
        a11y.label(desc_ctrl, 'Requested action details')
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
        # start on the details so a screen reader reads them when the dialog
        # opens (queued, because focus set before the dialog is shown is lost)
        wx.CallAfter(desc_ctrl.SetFocus)
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
        a11y.label(prompt_ctrl, 'Prompt received from glasses')
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
        # start on the prompt so a screen reader reads it when the dialog opens
        wx.CallAfter(prompt_ctrl.SetFocus)

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
        # the langgraph agent (graph.py) plus the config carrying its live
        # browser and thread_id; both are created on the first run
        self._workflow = None
        self._config = None
        self._browser_session: BrowserSession | None = None
        self._logged_decision = None
        self._full_answer = ''

        # one persistent asyncio loop for the app's lifetime, so a kept-alive
        # BrowserSession's async resources (event bus, CDP connection) survive
        # across multiple "Run Agent" clicks instead of dying with a one-shot loop
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

        port = self.settings.get('glasses_port', 8765)
        self._glasses_server = GlassesServer(
            port, lambda item, reply: wx.CallAfter(self._on_glasses_data, item, reply)
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
        a11y.label(self.prompt, 'Task description',
                   'Press Return to run the agent. Shift Return adds a new line.')
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
        a11y.label(self.log, 'Activity log')
        root.Add(self.log, 1, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(root)

        self.status_bar = self.CreateStatusBar(2)
        self.status_bar.SetStatusWidths([-1, 180])
        self._show_status('Ready')
        self._show_status('Glasses: off', 1)

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

    def _log(self, msg: str, speak: bool = False):
        # speak=True also has the screen reader say the line, for the moments a
        # user needs to hear without going to read the log
        wx.CallAfter(self.log.AppendText, msg + '\n')
        if speak:
            # '[Voice] Recording started.' is spoken as 'Voice: Recording started.'
            spoken = msg.strip().replace('[!] ', '').replace('[', '', 1).replace(']', ':', 1)
            wx.CallAfter(a11y.announce, spoken)

    def _set_status(self, text: str):
        wx.CallAfter(self._show_status, text)

    def _show_status(self, text: str, field: int = 0):
        # the status bar is drawn by wx, so a screen reader can't see it unless
        # its text is handed over separately
        self.status_bar.SetStatusText(text, field)
        a11y.expose_text(
            self.status_bar,
            f'Status: {self.status_bar.GetStatusText(0)}, {self.status_bar.GetStatusText(1)}',
        )

    def _announce_run_finished(self):
        # the outcome is spoken by _log(speak=True). focus stays where it is,
        # because moving it would cut that speech off; the log's cursor is left
        # at the end for when the user goes to read the detail.
        self.log.SetInsertionPointEnd()

    # agent execution

    def _agent_busy(self) -> bool:
        return self._agent_future is not None and not self._agent_future.done()

    def _on_run(self, _):
        if self._agent_busy():
            self._log('[!] Agent is already running.', speak=True)
            return

        task = self.prompt.GetValue().strip()
        if not task:
            wx.MessageBox('Please enter a task.', 'No Task',
                          wx.OK | wx.ICON_WARNING, self)
            return

        self.prompt.Clear()
        self._start_run(task)

    def _start_run(self, task: str, glasses_item: dict | None = None, reply=None):
        # glasses_item and reply are set when the task came from the phone app;
        # reply(result=..., error=...) answers the request the phone is waiting on
        if self._workflow is not None:
            self._log(f'\nFollow-up: {task}')
        else:
            self.log.Clear()
            self._prev_domain = ''
            self._log(f'Task: {task}')

        wx.CallAfter(a11y.announce, 'Agent started.')
        self._set_status('Running…')
        self.run_btn.Disable()
        self.new_task_btn.Disable()

        self._agent_future = asyncio.run_coroutine_threadsafe(
            self._run_agent_async(task, glasses_item, reply), self._loop
        )

    async def _run_agent_async(self, task: str, glasses_item: dict | None, reply):
        result, error = '', ''
        try:
            if self._workflow is None:
                # the browser is kept alive between runs so a follow-up can
                # continue on the same page, and one thread_id per session is
                # what lets the agent remember the conversation so far
                session, tools, file_system = await _start_resources()
                self._browser_session = session
                self._config = _run_config(session, tools, file_system, f'gui-{uuid.uuid4()}')
                self._workflow = build_agent()

            query = await self._add_photo_context(task, glasses_item)
            answer = await self._run_turn(query)
            if answer is None:
                error = 'A step was denied on the computer, so the agent stopped.'
                self._log('[Stopped] Permission denied by user.', speak=True)
                self._set_status('Stopped')
            else:
                # the phone reads the reply aloud, so it gets the short form
                result = compact_glasses_reply(query, answer)
                self._log(f'\n[Done] {answer}', speak=True)
                full = self._full_answer.strip()
                if len(full) > len(answer):
                    self._log(f'Full answer: {full}')
                self._set_status('Done')
        except Exception as e:
            error = str(e)
            self._log(f'[Error] {e}', speak=True)
            self._set_status('Error')
            await self._end_task(f'Stopped by an error: {e}')
        finally:
            if reply is not None:
                reply(result=result, error=error)
            wx.CallAfter(self.run_btn.Enable)
            wx.CallAfter(self.new_task_btn.Enable)
            wx.CallAfter(self._announce_run_finished)

    async def _add_photo_context(self, task: str, glasses_item: dict | None) -> str:
        # a photo from the glasses is described by a vision model and folded into
        # the prompt, so "this product" means the thing the user is looking at
        media_path = (glasses_item or {}).get('media_path')
        if not media_path:
            return task
        self._log('[Glasses] Looking at the photo…')
        try:
            enriched = await enrich_prompt_with_image(Path(media_path), task)
        except Exception as e:
            self._log(f'[Glasses] Could not read the photo ({e}); using the prompt only.')
            return task
        if enriched != task:
            self._log(f'[Glasses] {enriched}')
        return enriched

    async def _run_turn(self, query: str) -> str | None:
        # run one user turn through the graph, pausing before every `execute` so
        # the step can be approved first. returns the agent's reply, or None if
        # the user denied a step.
        workflow, config = self._workflow, self._config
        if workflow is None or config is None:
            raise RuntimeError('The agent has not been started.')
        self._logged_decision = None
        self._full_answer = ''
        payload: PipelineState | None = {
            'query': query, 'messages': [{'role': 'user', 'content': query}],
        }
        while True:
            async for chunk in workflow.astream(
                payload, config, stream_mode='updates', interrupt_before=['execute']
            ):
                for node, update in chunk.items():
                    await self._log_update(node, update or {})

            snapshot = await workflow.aget_state(config)
            if not snapshot.next:
                return last_assistant_reply(workflow, config)
            if not await self._approve_step(snapshot.values):
                await self._end_task('Stopped: the user denied this step.')
                return None
            payload = None  # resume from the pause

    async def _end_task(self, note: str):
        # close out a task that stopped part-way the same way node_resolve does,
        # so the next prompt starts clean but the chat memory is kept
        if self._workflow is None or self._config is None:
            return
        try:
            await self._workflow.aupdate_state(
                self._config,
                {
                    'messages': [{'role': 'assistant', 'content': note}],
                    'step': 0, 'done': False, 'scratch': [],
                },
                as_node='resolve',
            )
        except Exception as e:
            self._log(f'[!] Could not reset the agent: {e}')

    def _on_new_task(self, _):
        if self._agent_busy():
            wx.MessageBox('Please wait for the current run to finish.', 'Agent Running',
                          wx.OK | wx.ICON_WARNING, self)
            return

        session = self._browser_session
        self._workflow = None
        self._config = None
        self._browser_session = None
        self._prev_domain = ''
        self.log.Clear()
        self._set_status('Ready')

        if session is not None:
            asyncio.run_coroutine_threadsafe(session.kill(), self._loop)
            self._log('[New Task] Previous browser session closed.', speak=True)
        else:
            self._log('[New Task] Ready.', speak=True)

    async def _log_update(self, node: str, update: dict):
        # one graph node just finished; `update` is what it wrote to the state
        if node == 'plan':
            decision = update['last_decision']
            url, title = await self._current_page()
            description = await self._describe_decision(decision)
            self._log(f'\nStep {update.get("step", "?")}')
            self._log(f'  On page: {_describe_page(title, url)}')
            if decision.reasoning:
                self._log(f'  Plan:    {decision.reasoning}')
            if description:
                self._log(f'  Doing:   {description}')
            self._logged_decision = decision
        elif node == 'analyse' and update.get('scratch'):
            # the agent's own check turned the step down, so it plans again
            reason = str(update['scratch'][-1].get('outcome', ''))
            self._log(f'  Check:   Not doing that. {reason.removeprefix("gate rejected: ")}')
        elif node == 'execute':
            result = update.get('last_result')
            error = getattr(result, 'error', None)
            if error:
                self._log(f'  Problem: {error}')
            if update.get('done'):
                self._full_answer = str(getattr(result, 'extracted_content', '') or '')

    async def _current_page(self) -> tuple[str, str]:
        session = self._browser_session
        if session is None:
            return '', ''
        try:
            return await session.get_current_page_url(), await session.get_current_page_title()
        except Exception:
            return '', ''

    async def _describe_decision(self, decision) -> str:
        session, tools, _ = _resources(self._config)
        action = _decision_to_action(tools, decision)
        return _describe_action(action, await _element_label(session, decision.index))

    async def _approve_step(self, values: dict) -> bool:
        # the graph is paused just before `execute`; ask the user if the
        # settings call for it. returns False if they deny the step.
        decision = values['last_decision']
        description = await self._describe_decision(decision)
        if decision != self._logged_decision:
            # the agent's check swapped in a different action (e.g. a forced stop)
            self._log(f'  Doing:   {description}')
            self._logged_decision = decision

        url, title = await self._current_page()
        going_to = decision.url if decision.action == 'navigate' and decision.url else url
        if self._should_ask_permission(decision, going_to, values):
            text = self._format_permission_description(
                decision.reasoning, description, url, title
            )
            await asyncio.get_running_loop().run_in_executor(
                None, self._block_for_permission, text
            )
            if not self._permission_approved:
                return False

        self._prev_domain = _domain(going_to)
        return True

    def _should_ask_permission(self, decision, url: str, values: dict) -> bool:
        if decision.action == 'done':
            return False  # only reports the answer; nothing happens in the browser
        freq = self.settings.get('permission_frequency', 'every_step')
        if freq == 'every_step':
            return True
        if freq == 'new_site':
            return _domain(url) != self._prev_domain
        if freq == 'on_error':
            # the previous step this task was turned down by the agent's own
            # check, or ran and came back with an error
            scratch = values.get('scratch') or []
            if not scratch:
                return False
            outcome = str(scratch[-1].get('outcome', ''))
            error = getattr(values.get('last_result'), 'error', None)
            return outcome.startswith('gate rejected') or bool(
                error and outcome == str(error)[:200]
            )
        return False

    @staticmethod
    def _format_permission_description(goal: str, description: str, url: str, title: str) -> str:
        lines = []
        if goal:
            lines.append(f'Plan: {goal}')
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
        a11y.bring_to_front()
        dlg.ShowModal()
        self._permission_approved = dlg.approved
        dlg.Destroy()
        self._permission_.set()

    # glasses server

    def _start_glasses_server(self):
        started = self._glasses_server.start()
        if started:
            port = self._glasses_server.port
            self._log(f'[Glasses] Listening on port {port}.', speak=True)
            self._show_status(f'Glasses: on, port {port}', 1)
            self.glasses_btn.SetLabel('Glasses: On')
            self.glasses_btn.SetValue(True)

    def _stop_glasses_server(self):
        self._glasses_server.stop()
        self._log('[Glasses] Listener stopped.', speak=True)
        self._show_status('Glasses: off', 1)
        self.glasses_btn.SetLabel('Glasses: Off')
        self.glasses_btn.SetValue(False)

    def _on_glasses_toggle(self, _):
        if self.glasses_btn.GetValue():
            self._start_glasses_server()
        else:
            self._stop_glasses_server()

    def _on_glasses_data(self, item: dict, reply):
        # called on the wx main thread when the phone app posts a prompt. the
        # phone waits on that request for the agent's answer (it reads it aloud),
        # so every path below has to end in reply(...)
        self._log(f'\n[Glasses] Received {item["media_type"]}: {item["prompt"]}')
        store = self._glasses_server.store
        busy = 'The agent is busy with another task. Try again in a moment.'

        if self._agent_busy():
            self._log('[Glasses] Agent is already running — told the phone to try again.', speak=True)
            reply(error=busy)
            return

        dlg = GlassesDataDialog(self, item)
        a11y.bring_to_front()
        dlg.ShowModal()
        choice = dlg.choice
        dlg.Destroy()

        if choice == GlassesDataDialog.ACT:
            if self._agent_busy():
                # another request was acted on while this dialog was open
                self._log('[Glasses] Agent is already running — told the phone to try again.', speak=True)
                reply(error=busy)
                return
            store.update_status(item['id'], 'acted')
            self._log(f'[Glasses] Acting on: {item["prompt"]}')
            self._start_run(item['prompt'], item, reply)

        elif choice == GlassesDataDialog.DISMISS:
            store.update_status(item['id'], 'dismissed')
            self._log('[Glasses] Dismissed — data kept for later.')
            reply(result='Saved on the computer for later.')

        else:
            store.update_status(item['id'], 'cancelled')
            store.delete_media(item)
            self._log('[Glasses] Cancelled — data erased.')
            reply(result='Cancelled on the computer.')

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
            self._log(f'[Voice] Failed to start recording: {e}', speak=True)
            self.voice_btn.SetValue(False)
            return
        self.voice_btn.SetLabel('Recording… (click to stop)')
        self._set_status('Recording voice input…')
        self._log('[Voice] Recording started.', speak=True)

    def _stop_voice_recording(self):
        self.voice_btn.SetLabel('Voice Input')
        self.voice_btn.Disable()
        self._set_status('Transcribing…')
        self._log('[Voice] Recording stopped, transcribing…', speak=True)
        threading.Thread(target=self._transcribe_recording, daemon=True).start()

    def _transcribe_recording(self):
        try:
            audio_bytes = self._voice_recorder.stop()
            if not audio_bytes:
                self._log('[Voice] No audio captured.', speak=True)
                return
            text = transcribe_voice(audio_bytes)
            if text:
                wx.CallAfter(self._apply_transcript, text)
                self._log(f'[Voice] Transcribed: {text}', speak=True)
            else:
                self._log('[Voice] Could not transcribe audio (empty result).', speak=True)
        except Exception as e:
            self._log(f'[Voice] Transcription failed: {e}', speak=True)
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
