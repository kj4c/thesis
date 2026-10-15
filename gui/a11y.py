# screen reader support (VoiceOver on macos)

import wx

try:
    import AppKit
    import objc
except ImportError:
    AppKit = objc = None


def _native(window: wx.Window) -> list:
    view = objc.objc_object(c_void_p=window.GetHandle())
    if view.respondsToSelector_('documentView') and view.documentView() is not None:
        return [view.documentView()]
    if view.respondsToSelector_('cell') and view.cell() is not None:
        return [view, view.cell()]
    return [view]


def label(window: wx.Window, text: str, help_text: str = ''):
    # give a control the name (and optional hint) the screen reader speaks
    window.SetName(text)
    if AppKit is None:
        return
    windows = window.GetChildren() if isinstance(window, wx.SpinCtrl) else [window]
    for w in windows:
        for target in _native(w):
            target.setAccessibilityLabel_(text)
            if help_text:
                target.setAccessibilityHelp_(help_text)


def expose_text(window: wx.Window, text: str):
    if AppKit is None:
        return
    view = objc.objc_object(c_void_p=window.GetHandle())
    view.setAccessibilityElement_(True)
    view.setAccessibilityRole_(AppKit.NSAccessibilityStaticTextRole)
    view.setAccessibilityValue_(text)


def announce(text: str):
    # speak `text` without moving focus. call on the wx main thread.
    if AppKit is None or not text:
        return
    app = AppKit.NSApp()
    AppKit.NSAccessibilityPostNotificationWithUserInfo(
        app.keyWindow() or app.mainWindow() or app,
        AppKit.NSAccessibilityAnnouncementRequestedNotification,
        {
            AppKit.NSAccessibilityAnnouncementKey: text,
            AppKit.NSAccessibilityPriorityKey: AppKit.NSAccessibilityPriorityHigh,
        },
    )


def bring_to_front():
    # make this the active app, so a dialog that needs an answer is read out
    # even when the agent's browser window was in front
    if AppKit is None:
        return
    app = AppKit.NSApp()
    if app.respondsToSelector_('activate'):
        app.activate()
    else:
        app.activateIgnoringOtherApps_(True)
