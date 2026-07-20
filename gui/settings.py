import socket

import wx

PERMISSION_OPTIONS = [
    ('Every step', 'every_step'),
    ('When switching to a new site', 'new_site'),
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
        super().__init__(parent, title='Settings', size=(480, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._settings = dict(settings)
        self._glasses_server = glasses_server
        self._build_ui()
        self._load_values()
        self.Centre()

    def _build_ui(self):
        notebook = wx.Notebook(self)
        notebook.AddPage(self._build_input_tab(notebook), 'Input')
        notebook.AddPage(self._build_permissions_tab(notebook), 'Permissions')
        notebook.AddPage(self._build_glasses_tab(notebook), 'Smart Glasses')

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(notebook, 1, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        save_btn = wx.Button(self, wx.ID_OK, 'Save')
        cancel_btn = wx.Button(self, wx.ID_CANCEL, 'Cancel')
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
            self._input_radios.append(rb)
            sizer.Add(rb, 0, wx.LEFT, 24)

        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.ALL, 10)

        sizer.Add(wx.StaticText(panel, label='Keyboard shortcut to submit:'), 0, wx.LEFT | wx.TOP, 10)
        self._shortcut_ctrl = wx.TextCtrl(panel)
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
        grid.Add(self._lan_label, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label='mDNS address:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self._mdns_label = wx.TextCtrl(
            panel, value=f'{_get_mdns_hostname()}:{port}',
            style=wx.TE_READONLY | wx.BORDER_NONE,
        )
        grid.Add(self._mdns_label, 1, wx.EXPAND)

        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        hint = wx.StaticText(
            panel,
            label='Point the Meta glasses companion app to either address above.\n'
                  'Both require your phone and this computer to be on the same Wi-Fi.',
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
        else:
            new_port = self._port_ctrl.GetValue()
            self._glasses_server.restart(new_port)
            self._listener_status.SetLabel('Running')
            self._toggle_btn.SetLabel('Stop Listener')

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

        self.EndModal(wx.ID_OK)

    def get_settings(self) -> dict:
        return self._settings
