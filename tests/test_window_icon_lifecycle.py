"""Actual Windows Tk roots and tray, not mocked iconbitmap/WM_GETICON calls."""
import os
import time
import tkinter as tk

import pytest

from workbench import branding

pytestmark=[pytest.mark.native_process,pytest.mark.skipif(os.name!='nt',reason='Windows native icons')]


def pump(window,seconds=.3):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        window.update();time.sleep(.01)


def isolated_main():
    from gui.main_window import AplusMainWindow
    window=AplusMainWindow(enable_tray=True)
    # No Store/service/user data, and no remote version check during this probe.
    window.after_cancel(window.version_controller.startup_check_id)
    window.version_controller.startup_check_id=None
    return window


def test_main_and_later_dialog_keep_approved_icons_through_minimize_and_tray():
    from tests.window_icon_probe import IconReader
    reader=IconReader();window=isolated_main();tray=window._tray_icon
    hwnd=reader.hwnd(window)
    try:
        pump(window)
        assert '_icon' not in window.__dict__,'ttkbootstrap default PhotoImage must not be installed'
        assert tray is not None and tray.visible,'A real Windows notification icon must be running'
        expected=reader.approved_hashes(branding.application_icon())
        def check():
            # The root must carry explicit small/large icons, without falling
            # back to the class or a stale framework/default image.
            assert all(reader.user.SendMessageW(reader.hwnd(window),0x7F,kind,0) for kind in (0,1))
            assert all(reader.hash(image) in expected for image in reader.window_images(window).values())
            dialog=tk.Toplevel(window)
            try:
                pump(window,.05)
                assert all(reader.hash(image) in expected for image in reader.window_images(dialog).values())
            finally:dialog.destroy()
        check()
        window.iconify();pump(window)
        assert window.state()=='iconic'
        window.deiconify();window.state('normal');pump(window);check()
        window._hide_to_tray();pump(window)
        assert window.state()=='withdrawn'
        # Invoke the actual native tray menu callback; its queue is consumed by
        # the normal Tk poller. No mock tray or direct call to restore is used.
        list(tray.menu)[0](tray);pump(window,.6)
        assert window.state()=='normal';check()
        list(tray.menu)[-1](tray)
        deadline=time.monotonic()+3
        while not window._closing and time.monotonic()<deadline:pump(window,.05)
        assert window._closing and not reader.user.IsWindow(hwnd)
        tray._thread.join(timeout=3)
        assert not tray._running and not tray._thread.is_alive()
    finally:
        if not window._closing:window._exit_from_tray()


@pytest.mark.parametrize('failure',['missing','corrupt'])
def test_native_main_still_opens_when_icon_resource_is_unavailable(tmp_path,monkeypatch,caplog,failure):
    from gui.main_window import AplusMainWindow
    icon=tmp_path/'unavailable.ico'
    if failure=='corrupt':icon.write_bytes(b'not an icon')
    monkeypatch.setattr(branding,'application_icon',lambda:icon)
    window=AplusMainWindow()
    try:
        window.after_cancel(window.version_controller.startup_check_id)
        window.version_controller.startup_check_id=None
        pump(window,.1)
        assert window.winfo_exists() and window.toolbar.winfo_exists()
        assert str(icon) in caplog.text
    finally:window._exit_from_tray()
