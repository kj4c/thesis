import socket
import threading
from pathlib import Path

import wx

from . import a11y
import calendar_storage
from calendar_events import connect_google_calendar, disconnect_google_calendar
from network import tailscale_ip

PERMISSION_OPTIONS = [
    ('Every step', 'every_step'),
    ('When switching to a new site', 'new_site'),
    ('Only for risky steps (buying, sending, signing in)', 'risky'),
    ('Only when an error occurs', 'on_error'),
]

INPUT_OPTIONS = [
    ('Text', 'text'),
    ('Voice', 'voice'),
]


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def _get_mdns_hostname() -> str:
    return socket.gethostname() + '.local'


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, settings: dict, glasses_server=None):
        super().__init__(parent, title='Settings', size=(540, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._settings = dict(settings)
        self._glasses_server = glasses_server
        self._calendar_operation_pending = False
        self._build_ui()
        self._load_values()
        self.Centre()
        self.Bind(wx.EVT_CLOSE, self._on_dialog_close)

    def _build_ui(self):
        notebook = wx.Notebook(self)
        notebook.AddPage(self._build_input_tab(notebook), 'Input')
        notebook.AddPage(self._build_permissions_tab(notebook), 'Permissions')
        notebook.AddPage(self._build_glasses_tab(notebook), 'Smart Glasses')
        notebook.AddPage(self._build_calendar_tab(notebook), 'Calendar')

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(notebook, 1, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        save_btn = wx.Button(self, wx.ID_OK, 'Save')
        self._settings_save_btn = save_btn
        cancel_btn = wx.Button(self, wx.ID_CANCEL, 'Cancel')
        self._settings_cancel_btn = cancel_btn
        save_btn.SetDefault()
        btn_sizer.AddButton(save_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        outer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)

        self.SetSizer(outer)
        save_btn.Bind(wx.EVT_BUTTON, self._on_save)

    def _build_input_tab(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label='Input method:'), 0, wx.ALL, 10)
        self._input_radios: list[wx.RadioButton] = []
        for i, (label, value) in enumerate(INPUT_OPTIONS):
            style = wx.RB_GROUP if i == 0 else 0
            rb = wx.RadioButton(panel, label=label, style=style)
            rb._value = value  # type: ignore[attr-defined]
            # the heading above isn't tied to the buttons for a screen reader
            a11y.label(rb, f'Input method: {label}')
            self._input_radios.append(rb)
            sizer.Add(rb, 0, wx.LEFT, 24)

        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.ALL, 10)

        sizer.Add(wx.StaticText(panel, label='Keyboard shortcut to submit:'), 0, wx.LEFT | wx.TOP, 10)
        self._shortcut_ctrl = wx.TextCtrl(panel)
        a11y.label(self._shortcut_ctrl, 'Keyboard shortcut to submit', 'For example Ctrl+Return')
        sizer.Add(self._shortcut_ctrl, 0, wx.EXPAND | wx.ALL, 10)
        hint = wx.StaticText(panel, label='e.g. Ctrl+Return')
        hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(hint, 0, wx.LEFT, 10)

        panel.SetSizer(sizer)
        return panel

    def _build_permissions_tab(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(
            wx.StaticText(panel, label='Ask for user permission:'),
            0, wx.ALL, 10,
        )
        self._perm_radios: list[wx.RadioButton] = []
        for i, (label, value) in enumerate(PERMISSION_OPTIONS):
            style = wx.RB_GROUP if i == 0 else 0
            rb = wx.RadioButton(panel, label=label, style=style)
            rb._value = value  # type: ignore[attr-defined]
            a11y.label(rb, f'Ask for user permission: {label}')
            self._perm_radios.append(rb)
            sizer.Add(rb, 0, wx.LEFT, 24)

        panel.SetSizer(sizer)
        return panel

    def _build_glasses_tab(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(
            wx.StaticText(panel, label='Meta Glasses — Local HTTP Listener'),
            0, wx.ALL, 10,
        )

        # connection addresses
        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        grid.AddGrowableCol(1)

        lan_ip = _get_lan_ip()
        port = self._settings.get('glasses_port', 8765)

        grid.Add(wx.StaticText(panel, label='LAN address:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self._lan_label = wx.TextCtrl(
            panel, value=f'{lan_ip}:{port}',
            style=wx.TE_READONLY | wx.BORDER_NONE,
        )
        a11y.label(self._lan_label, 'LAN address')
        grid.Add(self._lan_label, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label='mDNS address:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self._mdns_label = wx.TextCtrl(
            panel, value=f'{_get_mdns_hostname()}:{port}',
            style=wx.TE_READONLY | wx.BORDER_NONE,
        )
        a11y.label(self._mdns_label, 'mDNS address')
        grid.Add(self._mdns_label, 1, wx.EXPAND)

        ts_ip = tailscale_ip()
        grid.Add(wx.StaticText(panel, label='Tailscale address:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self._tailscale_label = wx.TextCtrl(
            panel, value=f'{ts_ip}:{port}' if ts_ip else 'Tailscale is not running',
            style=wx.TE_READONLY | wx.BORDER_NONE,
        )
        a11y.label(self._tailscale_label, 'Tailscale address')
        grid.Add(self._tailscale_label, 1, wx.EXPAND)

        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        hint = wx.StaticText(
            panel,
            label='Enter one of these in the glasses app on your phone.\n'
                  'LAN and mDNS need the phone on the same Wi-Fi. The Tailscale\n'
                  'address works from any network once Tailscale is running on\n'
                  'both, signed in to the same account.',
        )
        hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(hint, 0, wx.ALL, 12)

        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        # port and autostart
        port_row = wx.BoxSizer(wx.HORIZONTAL)
        port_row.Add(
            wx.StaticText(panel, label='Port:'),
            0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8,
        )
        self._port_ctrl = wx.SpinCtrl(panel, min=1024, max=65535, value=str(port))
        self._port_ctrl.SetMinSize((80, -1))
        a11y.label(self._port_ctrl, 'Listener port')
        port_row.Add(self._port_ctrl, 0, wx.RIGHT, 12)

        self._autostart_chk = wx.CheckBox(panel, label='Start listener on launch')
        port_row.Add(self._autostart_chk, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(port_row, 0, wx.ALL, 12)

        # listener status and toggle
        listener_row = wx.BoxSizer(wx.HORIZONTAL)
        listener_row.Add(
            wx.StaticText(panel, label='Listener:'),
            0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8,
        )
        is_running = self._glasses_server.running if self._glasses_server else False
        self._listener_status = wx.StaticText(
            panel, label='Running' if is_running else 'Stopped'
        )
        listener_row.Add(self._listener_status, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 12)

        label = 'Stop Listener' if is_running else 'Start Listener'
        self._toggle_btn = wx.Button(panel, label=label)
        listener_row.Add(self._toggle_btn, 0)
        sizer.Add(listener_row, 0, wx.LEFT | wx.BOTTOM, 12)

        self._port_ctrl.Bind(wx.EVT_SPINCTRL, self._on_port_change)
        self._toggle_btn.Bind(wx.EVT_BUTTON, self._on_listener_toggle)

        panel.SetSizer(sizer)
        return panel

    def _build_calendar_tab(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(panel, label='Google Calendar account')
        heading.SetFont(heading.GetFont().Bold())
        sizer.Add(heading, 0, wx.ALL, 12)

        description = wx.StaticText(
            panel,
            label='Connect your Google account to add events from glasses photos. '
                  'Google sign-in opens in your default browser; your Google password '
                  'is never entered into this app.',
        )
        description.Wrap(480)
        sizer.Add(description, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        account = calendar_storage.connected_account()
        default_client_file = (
            account.get('client_file') if account else
            self._settings.get('calendar_client_file', '')
        )
        sizer.Add(wx.StaticText(panel, label='OAuth Desktop client file:'), 0, wx.LEFT | wx.TOP, 12)
        file_row = wx.BoxSizer(wx.HORIZONTAL)
        self._calendar_client_file = wx.TextCtrl(
            panel, value=default_client_file or '', style=wx.TE_READONLY | wx.BORDER_SIMPLE,
        )
        self._calendar_client_file.SetHint('Choose the OAuth client JSON file')
        a11y.label(self._calendar_client_file, 'Google OAuth Desktop client file')
        file_row.Add(self._calendar_client_file, 1, wx.RIGHT | wx.EXPAND, 8)
        self._calendar_browse_btn = wx.Button(panel, label='Choose…')
        file_row.Add(self._calendar_browse_btn, 0)
        sizer.Add(file_row, 0, wx.EXPAND | wx.ALL, 12)

        self._calendar_status = wx.StaticText(panel, label='')
        self._calendar_status.Wrap(480)
        a11y.label(self._calendar_status, 'Google Calendar connection status')
        sizer.Add(self._calendar_status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self._calendar_connect_btn = wx.Button(panel, label='Connect Google Account')
        self._calendar_disconnect_btn = wx.Button(panel, label='Disconnect')
        button_row.Add(self._calendar_connect_btn, 0, wx.RIGHT, 8)
        button_row.Add(self._calendar_disconnect_btn, 0)
        sizer.Add(button_row, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        security_note = wx.StaticText(
            panel,
            label='The encrypted refresh token is stored in a local SQLite database. '
                  'Its encryption key is kept in the operating system credential store.',
        )
        security_note.Wrap(480)
        security_note.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(security_note, 0, wx.EXPAND | wx.ALL, 12)

        self._set_calendar_status(account)
        self._calendar_browse_btn.Bind(wx.EVT_BUTTON, self._on_calendar_browse)
        self._calendar_connect_btn.Bind(wx.EVT_BUTTON, self._on_calendar_connect)
        self._calendar_disconnect_btn.Bind(wx.EVT_BUTTON, self._on_calendar_disconnect)
        panel.SetSizer(sizer)
        return panel

    def _set_calendar_status(self, account):
        if account:
            self._calendar_status.SetLabel(f"Connected as {account['email']}.")
            self._calendar_connect_btn.Disable()
            self._calendar_disconnect_btn.Enable()
        else:
            self._calendar_status.SetLabel('Not connected.')
            self._calendar_connect_btn.Enable()
            self._calendar_disconnect_btn.Disable()

    def _on_calendar_browse(self, _event):
        dlg = wx.FileDialog(
            self, 'Choose Google OAuth Desktop client JSON',
            wildcard='JSON files (*.json)|*.json|All files (*.*)|*.*',
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self._calendar_client_file.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_calendar_connect(self, _event):
        client_file = self._calendar_client_file.GetValue().strip()
        if not client_file or not Path(client_file).is_file():
            wx.MessageBox(
                'Choose your Google OAuth Desktop client JSON file first. '
                'See the Calendar section of the README for setup instructions.',
                'OAuth Client File Required', wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        self._calendar_connect_btn.Disable()
        self._calendar_browse_btn.Disable()
        self._settings_save_btn.Disable()
        self._settings_cancel_btn.Disable()
        self._calendar_operation_pending = True
        self._calendar_status.SetLabel('Opening Google sign-in in your browser…')
        threading.Thread(
            target=self._connect_calendar_worker, args=(client_file,), daemon=True
        ).start()

    def _connect_calendar_worker(self, client_file):
        try:
            email = connect_google_calendar(client_file)
            wx.CallAfter(self._calendar_connection_finished, email, '')
        except Exception as exc:
            wx.CallAfter(self._calendar_connection_finished, '', str(exc))

    def _calendar_connection_finished(self, email, error):
        self._calendar_operation_pending = False
        self._calendar_browse_btn.Enable()
        self._settings_save_btn.Enable()
        self._settings_cancel_btn.Enable()
        if error:
            self._calendar_status.SetLabel(f'Could not connect: {error}')
            self._calendar_connect_btn.Enable()
            self._calendar_disconnect_btn.Disable()
            return
        account = calendar_storage.connected_account()
        self._settings['calendar_client_file'] = self._calendar_client_file.GetValue().strip()
        self._set_calendar_status(account)
        a11y.announce(f'Connected to Google Calendar as {email}.')

    def _on_calendar_disconnect(self, _event):
        if wx.MessageBox(
            'Revoke this app’s Google Calendar access and remove its saved connection?',
            'Disconnect Google Calendar', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self,
        ) != wx.YES:
            return
        self._calendar_disconnect_btn.Disable()
        self._calendar_browse_btn.Disable()
        self._settings_save_btn.Disable()
        self._settings_cancel_btn.Disable()
        self._calendar_operation_pending = True
        self._calendar_status.SetLabel('Disconnecting…')
        threading.Thread(target=self._disconnect_calendar_worker, daemon=True).start()

    def _disconnect_calendar_worker(self):
        try:
            disconnect_google_calendar()
            wx.CallAfter(self._calendar_disconnection_finished, '')
        except Exception as exc:
            wx.CallAfter(self._calendar_disconnection_finished, str(exc))

    def _calendar_disconnection_finished(self, error):
        self._calendar_operation_pending = False
        self._calendar_browse_btn.Enable()
        self._settings_save_btn.Enable()
        self._settings_cancel_btn.Enable()
        if error:
            self._calendar_status.SetLabel(f'Could not disconnect: {error}')
            self._calendar_disconnect_btn.Enable()
            return
        self._settings.pop('calendar_client_file', None)
        self._calendar_client_file.Clear()
        self._set_calendar_status(None)
        a11y.announce('Google Calendar disconnected.')

    def _on_dialog_close(self, event):
        if self._calendar_operation_pending:
            wx.MessageBox(
                'Wait for the Google Calendar account action to finish before closing Settings.',
                'Calendar Connection In Progress', wx.OK | wx.ICON_INFORMATION, self,
            )
            event.Veto()
            return
        event.Skip()

    def _load_values(self):
        input_method = self._settings.get('input_method', 'text')
        for rb in self._input_radios:
            rb.SetValue(rb._value == input_method)  # type: ignore[attr-defined]

        self._shortcut_ctrl.SetValue(self._settings.get('shortcut', 'Ctrl+Return'))

        perm_freq = self._settings.get('permission_frequency', 'every_step')
        for rb in self._perm_radios:
            rb.SetValue(rb._value == perm_freq)  # type: ignore[attr-defined]

        self._autostart_chk.SetValue(self._settings.get('glasses_autostart', False))

    def _on_port_change(self, _):
        new_port = self._port_ctrl.GetValue()
        lan_ip = _get_lan_ip()
        self._lan_label.SetValue(f'{lan_ip}:{new_port}')
        self._mdns_label.SetValue(f'{_get_mdns_hostname()}:{new_port}')

    def _on_listener_toggle(self, _):
        if not self._glasses_server:
            return

        if self._glasses_server.running:
            self._glasses_server.stop()
            self._listener_status.SetLabel('Stopped')
            self._toggle_btn.SetLabel('Start Listener')
            a11y.announce('Listener stopped.')
        else:
            new_port = self._port_ctrl.GetValue()
            self._glasses_server.restart(new_port)
            self._listener_status.SetLabel('Running')
            self._toggle_btn.SetLabel('Stop Listener')
            a11y.announce(f'Listener running on port {new_port}.')

    def _on_save(self, _event):
        for rb in self._input_radios:
            if rb.GetValue():
                self._settings['input_method'] = rb._value  # type: ignore[attr-defined]

        self._settings['shortcut'] = self._shortcut_ctrl.GetValue().strip()

        for rb in self._perm_radios:
            if rb.GetValue():
                self._settings['permission_frequency'] = rb._value  # type: ignore[attr-defined]

        self._settings['glasses_port'] = self._port_ctrl.GetValue()
        self._settings['glasses_autostart'] = self._autostart_chk.GetValue()
        client_file = self._calendar_client_file.GetValue().strip()
        if client_file:
            self._settings['calendar_client_file'] = client_file
        else:
            self._settings.pop('calendar_client_file', None)

        self.EndModal(wx.ID_OK)

    def get_settings(self) -> dict:
        return self._settings
