"""Windows integration (WP13): one JARVIS window, the global hotkey, the tray
icon, native notifications, the PC kept awake during a task, the attention
state, the Explorer and Store apps.

The Windows API is replaced by fakes, so these run on Linux and on the
Windows CI runner alike; the few tests that call the real API at the end run
on Windows only."""
import base64
import ctypes
import json
import os
import queue
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from jarvis import config, desktop, events, inbox, security, shell, tasks

ON_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------- fakes

class FakeHotkeyApi:
    """user32/kernel32 for the hotkey thread: a real blocking message queue,
    RegisterHotKey answering from `taken` (combinations another program holds)."""

    def __init__(self, taken=(), error=1409):
        self.taken = set(taken)
        self.error = error
        self.registered = set()
        self.calls = []
        self.messages = queue.Queue()

    def RegisterHotKey(self, hwnd, ident, mods, vk):
        self.calls.append(("register", hwnd, ident, mods, vk))
        if (mods & ~shell.MOD_NOREPEAT, vk) in self.taken:
            return 0
        self.registered.add((mods & ~shell.MOD_NOREPEAT, vk))
        return 1

    def UnregisterHotKey(self, hwnd, ident):
        self.calls.append(("unregister", hwnd, ident))
        self.registered.clear()
        return 1

    def PeekMessageW(self, msg, hwnd, first, last, flags):
        self.calls.append(("peek", flags))
        return 0

    def GetCurrentThreadId(self):
        return 4242

    def get_last_error(self):
        return self.error

    def GetMessageW(self, msg, hwnd, first, last):
        message, wparam = self.messages.get(timeout=10)
        if message == shell.WM_QUIT:
            return 0
        msg._obj.message, msg._obj.wParam = message, wparam  # ctypes.byref(msg)._obj is msg
        return 1

    def PostThreadMessageW(self, tid, message, wparam, lparam):
        self.calls.append(("post", tid, message))
        self.messages.put((message, wparam))
        return 1

    def press(self):
        self.messages.put((shell.WM_HOTKEY, shell.HOTKEY_ID))


class FakeWin32:
    """desktop.py's Win32 functions, recording every call."""

    def __init__(self, foreground=None, set_foreground=1, iconic=0, classes=None, quns=5, hr=0):
        self.calls = []
        self.foreground = foreground
        self.set_foreground = set_foreground
        self.iconic = iconic
        self.classes = classes or {}
        self.quns, self.hr = quns, hr
        self.flashes = []
        self.SHGetKnownFolderPath = None
        self.CoTaskMemFree = None

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        if self.set_foreground:
            self.foreground = hwnd
        return self.set_foreground

    def GetForegroundWindow(self):
        return self.foreground

    def IsIconic(self, hwnd):
        return self.iconic

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("ShowWindow", hwnd, cmd))
        return 1

    def GetClassNameW(self, hwnd, buf, size):
        buf.value = self.classes.get(hwnd, "")
        return len(buf.value)

    def FlashWindowEx(self, info):
        f = info._obj
        self.flashes.append({"cbSize": f.cbSize, "hwnd": f.hwnd, "dwFlags": f.dwFlags,
                             "uCount": f.uCount, "dwTimeout": f.dwTimeout})
        return 0

    def SHQueryUserNotificationState(self, state):
        state._obj.value = self.quns
        return self.hr

    def SetThreadExecutionState(self, flags):
        self.calls.append(("SetThreadExecutionState", flags))
        return 0x80000000  # the previous state: nonzero is success


@pytest.fixture
def win32(monkeypatch):
    """desktop.py as on Windows, with the fake API."""
    fake = FakeWin32()
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop, "_win", lambda: fake)
    return fake


@pytest.fixture
def no_launch(monkeypatch):
    """No browser, Explorer or PowerShell starts for real: launches are recorded."""
    launched = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda cmd, *a, **k: launched.append(cmd))
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: launched.append(("browser", url)) or True)
    monkeypatch.setattr(os, "startfile", lambda *a, **k: pytest.fail("os.startfile appelé"),
                        raising=False)
    return launched


@pytest.fixture
def fresh_shell(monkeypatch):
    """shell.py's module state back to "not started" after each test."""
    yield
    shell.stop()
    monkeypatch.setattr(shell, "_hotkey", None)
    monkeypatch.setattr(shell, "_tray", None)


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- the combination

def test_default_hotkey_parses_to_ctrl_alt_shift_j():
    assert shell.parse_hotkey("ctrl+alt+shift+j") == (0x7, 0x4A)
    assert shell.parse_hotkey(" Ctrl + Alt + Maj + J ") == (0x7, 0x4A)
    assert shell.parse_hotkey(config.HOTKEY) == (0x7, 0x4A)  # the shipped default
    assert shell.label(0x7, 0x4A) == "Ctrl+Alt+Maj+J"
    assert shell.parse_hotkey("win+f9") == (shell.MOD_WIN, 0x78)
    assert shell.label(*shell.parse_hotkey("ctrl+shift+espace")) == "Ctrl+Maj+Espace"


@pytest.mark.parametrize("spec, why", [
    ("", "vide"),
    ("ctrl+alt+", "invalide"),
    ("hyper+j", "invalide"),
    ("ctrl+ctrl+j", "double"),
    ("ctrl+alt+é", "lettre"),
    ("j", "Ctrl, Alt ou Win"),          # would take the letter from every program
    ("shift+j", "Ctrl, Alt ou Win"),
    ("ctrl+f12", "réservée"),           # Windows keeps F12 for the debugger
    ("ctrl+alt+j", "A.R.E.S"),          # A.R.E.S holds Ctrl+Alt+V/M/J/K/Espace
    ("ctrl+alt+space", "A.R.E.S"),
])
def test_invalid_hotkey_is_refused_in_french(spec, why):
    with pytest.raises(ValueError, match=why):
        shell.parse_hotkey(spec)


# ---------------------------------------------------------------- the hotkey thread

def start_hotkey(api, combo=(0x7, 0x4A)):
    hk = shell.Hotkey(api, combo)
    hk.start()
    return hk


def test_taken_hotkey_publishes_a_french_warning(published):
    api = FakeHotkeyApi(taken={(0x7, 0x4A)})
    hk = start_hotkey(api)
    try:
        assert hk.ok is False
        warnings = [e for e in published if e["type"] == "warning"]
        assert len(warnings) == 1
        text = warnings[0]["text"]
        assert "indisponible" in text and "Ctrl+Alt+Maj+J" in text and "Réglages › Système" in text
        assert warnings[0]["kind"] == "hotkey"
    finally:
        hk.stop()
    assert not hk.thread.is_alive()


def test_other_windows_error_is_named(published):
    api = FakeHotkeyApi(taken={(0x7, 0x4A)}, error=5)
    hk = start_hotkey(api)
    hk.stop()
    assert "erreur Windows 5" in published[0]["text"] and "indisponible" in published[0]["text"]


def test_hotkey_press_raises_the_window_and_tells_the_page(published, monkeypatch):
    shown = []
    monkeypatch.setattr(desktop, "show_app_window", lambda url: shown.append(url) or "focused")
    api = FakeHotkeyApi()
    hk = start_hotkey(api)
    assert hk.ok and hk.tid == 4242
    # Registered on its own thread, with MOD_NOREPEAT: holding the keys fires once.
    assert ("register", None, shell.HOTKEY_ID, 0x7 | shell.MOD_NOREPEAT, 0x4A) in api.calls
    assert api.calls[0] == ("peek", shell.PM_NOREMOVE)  # the queue exists before anyone posts
    api.press()
    assert wait_for(lambda: any(e["type"] == "hotkey" for e in published))
    hot = [e for e in published if e["type"] == "hotkey"]
    assert hot[0]["action"] == "toggle" and abs(hot[0]["at"] - time.time()) < 5
    assert shown == [f"http://127.0.0.1:{config.PORT}"]
    hk.stop()
    assert not hk.thread.is_alive()
    assert ("post", 4242, shell.WM_QUIT) in api.calls
    assert ("unregister", None, shell.HOTKEY_ID) in api.calls  # released on the way out


def test_double_tap_toggles_once(published, monkeypatch):
    monkeypatch.setattr(desktop, "show_app_window", lambda url: "focused")
    api = FakeHotkeyApi()
    hk = start_hotkey(api)
    api.press()
    api.press()
    hk.stop()
    assert [e["action"] for e in published if e["type"] == "hotkey"] == ["toggle"]


def test_failing_window_does_not_stop_the_hotkey(published, monkeypatch):
    def broken(url):
        raise OSError("navigateur introuvable")
    monkeypatch.setattr(desktop, "show_app_window", broken)
    api = FakeHotkeyApi()
    hk = start_hotkey(api)
    api.press()
    assert wait_for(lambda: published)
    assert hk.thread.is_alive()  # the next press still works
    hk.stop()


def test_set_hotkey_swaps_now_and_keeps_the_old_one_when_taken(published, monkeypatch, fresh_shell):
    api = FakeHotkeyApi(taken={(shell.MOD_CONTROL | shell.MOD_ALT, ord("K"))})
    hk = start_hotkey(api)
    monkeypatch.setattr(shell, "_hotkey", hk)
    assert shell.hotkey_state() == {"active": True, "combo": "Ctrl+Alt+Maj+J"}

    assert shell.set_hotkey("ctrl+shift+f9") == {"ok": True}
    assert shell.hotkey_state()["combo"] == "Ctrl+Maj+F9"
    assert api.registered == {(shell.MOD_CONTROL | shell.MOD_SHIFT, 0x78)}

    api.taken.add((shell.MOD_WIN | shell.MOD_ALT, ord("J")))
    out = shell.set_hotkey("win+alt+j")
    assert out["ok"] is False and "indisponible" in out["error"]
    assert shell.hotkey_state()["combo"] == "Ctrl+Maj+F9"  # still works
    assert api.registered == {(shell.MOD_CONTROL | shell.MOD_SHIFT, 0x78)}
    assert any(e["type"] == "warning" and "Alt+Win+J" in e["text"] for e in published)

    assert shell.set_hotkey("off") == {"ok": True}
    assert shell.hotkey_state() == {"active": False, "combo": ""}
    with pytest.raises(ValueError):
        shell.set_hotkey("ctrl+alt+j")  # refused before reaching Windows


def test_set_hotkey_without_a_running_thread_waits_for_the_next_start(fresh_shell):
    assert shell.set_hotkey("ctrl+alt+shift+k") == {"ok": False, "applied": False}
    with pytest.raises(ValueError):
        shell.set_hotkey("shift+k")


# ---------------------------------------------------------------- lifetime

class FakeIcon:
    HAS_NOTIFICATION = True

    def __init__(self, name, image, title, menu=None):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self.visible = False
        self.notes = []
        self.stopped = threading.Event()

    def run(self, setup=None):
        self.visible = True
        self.stopped.wait(10)
        self.visible = False

    def stop(self):
        self.stopped.set()

    def notify(self, message, title=None):
        self.notes.append((title, message))


class FakeItem:
    def __init__(self, text, action=None, checked=None, default=False, **_):
        self.text, self.action, self.checked_fn, self.default = text, action, checked, default

    def click(self, icon):
        argc = self.action.__code__.co_argcount
        return self.action(*([icon] if argc else []))

    @property
    def checked(self):
        return self.checked_fn(self) if self.checked_fn else None


class FakeMenu:
    SEPARATOR = FakeItem("- - - -")

    def __init__(self, *items):
        self.items = items


@pytest.fixture
def fake_pystray(monkeypatch):
    module = types.SimpleNamespace(Icon=FakeIcon, MenuItem=FakeItem, Menu=FakeMenu)
    monkeypatch.setitem(sys.modules, "pystray", module)
    return module


def test_start_on_windows_runs_hotkey_tray_and_stops_cleanly(monkeypatch, fake_pystray, published,
                                                             fresh_shell):
    api = FakeHotkeyApi()
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(config, "TRAY", True)
    monkeypatch.setattr(config, "HOTKEY", "ctrl+alt+shift+j")
    monkeypatch.setattr(shell, "_win", lambda: api)
    quits = []
    shell.start("http://127.0.0.1:8788", lambda: quits.append(1))
    assert shell.running()
    hk, icon = shell._hotkey, shell._tray
    assert hk.ok and hk.thread.is_alive()
    assert isinstance(icon, FakeIcon) and icon.title == "JARVIS"
    assert icon.image.size == (64, 64)
    assert wait_for(lambda: icon.visible)
    assert shell.notify("Rappel", "Sortir le pain") is True
    assert icon.notes == [("Rappel", "Sortir le pain")]
    shell.stop()
    assert not shell.running()
    assert wait_for(lambda: not hk.thread.is_alive())
    assert icon.stopped.is_set()
    assert not published  # nothing went wrong, nothing to say


def test_invalid_hotkey_in_settings_is_said_and_the_rest_still_starts(monkeypatch, published,
                                                                      fresh_shell):
    api = FakeHotkeyApi()
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(config, "TRAY", False)
    monkeypatch.setattr(config, "HOTKEY", "ctrl+alt+j")
    monkeypatch.setattr(shell, "_win", lambda: api)
    shell.start("http://127.0.0.1:8788", lambda: None)
    assert shell._hotkey.thread.is_alive() and not shell._hotkey.ok
    assert not [c for c in api.calls if c[0] == "register"]
    assert "A.R.E.S" in published[0]["text"] and "Réglages › Système" in published[0]["text"]


def test_no_hotkey_wanted_registers_nothing(monkeypatch, published, fresh_shell):
    api = FakeHotkeyApi()
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(config, "TRAY", False)
    monkeypatch.setattr(config, "HOTKEY", "off")
    monkeypatch.setattr(shell, "_win", lambda: api)
    shell.start("http://127.0.0.1:8788", lambda: None)
    assert not [c for c in api.calls if c[0] == "register"] and not published


def test_missing_pystray_degrades_quietly(monkeypatch, published, fresh_shell):
    monkeypatch.setitem(sys.modules, "pystray", None)  # import pystray -> ImportError
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(config, "TRAY", True)
    monkeypatch.setattr(shell, "_win", lambda: FakeHotkeyApi())
    shell.start("http://127.0.0.1:8788", lambda: None)
    assert shell._tray is None and shell._hotkey.ok
    assert shell.notify("Rappel", "x") is False


SHELL_THREADS = ("jarvis-hotkey", "jarvis-tray", "jarvis-awake")  # jarvis-awake: never again (wave 3)


def shell_threads():
    return [t.name for t in threading.enumerate() if t.name in SHELL_THREADS]


def test_elsewhere_than_windows_nothing_is_started(monkeypatch, fresh_shell):
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    assert wait_for(lambda: not shell_threads())  # earlier tests' threads have ended
    shell.start("http://127.0.0.1:8788", lambda: None)
    assert shell.running() and shell._hotkey is None and shell._tray is None
    assert not shell_threads()
    shell.stop()
    assert not shell.running()


def test_tray_menu_speaks_french_and_quits_like_the_api(monkeypatch, fake_pystray, published,
                                                       fresh_shell):
    quits, shown, dnd, autostart = [], [], [], []
    monkeypatch.setattr(shell, "_state", {"url": "http://127.0.0.1:8788", "running": True,
                                          "on_quit": lambda: quits.append(1)})
    monkeypatch.setattr(desktop, "show_app_window", lambda url: shown.append(url) or "focused")
    monkeypatch.setattr(inbox, "set_dnd", lambda until: dnd.append(until))
    monkeypatch.setattr(desktop, "autostart_enabled", lambda: False)
    monkeypatch.setattr(desktop, "set_autostart", lambda on: autostart.append(on) or "JARVIS se lancera…")
    menu = shell.tray_menu(fake_pystray)
    items = {i.text: i for i in menu.items if i is not FakeMenu.SEPARATOR}
    assert list(items) == ["Ouvrir JARVIS", "Parler", "Mot d'éveil (activer ou couper)",
                           "Ne pas déranger 1 h", "Accès à distance (activer ou couper)",
                           "Démarrer avec Windows", "Quitter JARVIS"]
    assert [i.text for i in items.values() if i.default] == ["Ouvrir JARVIS"]  # a click on the icon
    icon = FakeIcon("jarvis", None, "JARVIS")

    items["Ouvrir JARVIS"].click(icon)
    assert shown == ["http://127.0.0.1:8788"]
    monkeypatch.setattr(events, "leader", lambda: "page-1")
    items["Parler"].click(icon)
    items["Mot d'éveil (activer ou couper)"].click(icon)
    assert [(e["type"], e["action"]) for e in published] == [("hotkey", "talk"), ("hotkey", "wake")]
    assert len(shown) == 2  # Parler raises the window; the wake word is switched in place
    monkeypatch.setattr(events, "leader", lambda: None)  # no page open: the window opens instead
    monkeypatch.setattr(shell, "_tray", icon)
    icon.visible = True
    items["Mot d'éveil (activer ou couper)"].click(icon)
    assert len(shown) == 3 and len(published) == 2
    assert "fenêtre JARVIS" in icon.notes[-1][1]

    assert items["Ne pas déranger 1 h"].checked is False
    items["Ne pas déranger 1 h"].click(icon)
    assert dnd and abs(dnd[0] - (time.time() + 3600)) < 5
    assert items["Démarrer avec Windows"].checked is False
    items["Démarrer avec Windows"].click(icon)
    assert autostart == [True]

    # Remote access: off on a fresh install, and the tray cannot switch it on before
    # Réglages has an address (the toast says where to go).
    assert items["Accès à distance (activer ou couper)"].checked is False
    items["Accès à distance (activer ou couper)"].click(icon)
    assert icon.notes[-1] == ("JARVIS", "Activez-le d'abord dans Réglages › Accès à distance.")
    assert items["Accès à distance (activer ou couper)"].checked is False

    items["Quitter JARVIS"].click(icon)
    assert quits == [1] and icon.stopped.is_set()


def test_quit_from_the_tray_stops_the_server(monkeypatch, fake_pystray):
    """Quitter -> server._request_shutdown, the same path as POST /api/shutdown."""
    stopped = []
    monkeypatch.setattr(tasks, "shutdown", lambda: stopped.append("tasks"))
    fake_server = types.SimpleNamespace(should_exit=False)
    monkeypatch.setattr(server, "SERVER", fake_server)
    monkeypatch.setattr(shell, "_state", {"url": "", "running": True, "on_quit": server._request_shutdown})
    items = {i.text: i for i in shell.tray_menu(fake_pystray).items}
    items["Quitter JARVIS"].click(FakeIcon("jarvis", None, "JARVIS"))
    assert stopped == ["tasks"] and fake_server.should_exit is True


def test_the_pc_is_kept_awake_once_by_the_scheduler_not_by_the_shell(monkeypatch, fresh_shell):
    """Wave 3: one owner for keep-awake. The scheduler's loop runs on every
    system (scheduler.periodic, every 30 ticks while a task runs); the shell
    starts no thread of its own for it, so Windows is not asked twice."""
    from jarvis import scheduler
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(config, "TRAY", False)
    monkeypatch.setattr(shell, "_win", lambda: FakeHotkeyApi())
    assert wait_for(lambda: "jarvis-awake" not in shell_threads())
    shell.start("http://127.0.0.1:8788", lambda: None)
    assert "jarvis-awake" not in shell_threads() and not hasattr(shell, "_awake_loop")
    shell.stop()
    calls = []
    running = [{"id": "t1", "status": "running"}]
    monkeypatch.setattr(tasks, "running", lambda: list(running))
    monkeypatch.setattr(desktop, "keep_awake", lambda: calls.append(1) or True)
    for n in range(1, scheduler.KEEP_AWAKE_TICKS * 3 + 1):  # 90 ticks: before the 5-minute refresh
        scheduler.periodic(n)
    assert len(calls) == 3
    running.clear()
    scheduler.periodic(scheduler.KEEP_AWAKE_TICKS * 4)
    assert len(calls) == 3  # no task: Windows may sleep again


# ---------------------------------------------------------------- one window

def test_show_app_window_focuses_the_existing_window(win32, monkeypatch, no_launch):
    monkeypatch.setattr(desktop, "_visible_windows",
                        lambda: {101: "Rapport.docx - Word", 202: "J.A.R.V.I.S."})
    monkeypatch.setattr(desktop, "open_app_window", lambda url: pytest.fail("seconde fenêtre ouverte"))
    win32.iconic = 1  # minimised: restored first
    assert desktop.show_app_window("http://127.0.0.1:8788") == "focused"
    assert ("ShowWindow", 202, 9) in win32.calls
    assert ("SetForegroundWindow", 202) in win32.calls
    assert not no_launch and not win32.flashes


def test_refused_focus_flashes_the_taskbar_button(win32, monkeypatch, no_launch):
    monkeypatch.setattr(desktop, "_visible_windows", lambda: {202: "J.A.R.V.I.S."})
    win32.set_foreground, win32.foreground = 0, 999  # Windows said no: another app keeps it
    assert desktop.show_app_window("http://127.0.0.1:8788") == "flashed"
    assert win32.flashes == [{"cbSize": ctypes.sizeof(desktop.FLASHWINFO), "hwnd": 202,
                              "dwFlags": 0x2 | 0xC, "uCount": 0, "dwTimeout": 0}]
    assert not no_launch


def test_no_window_yet_opens_one(win32, monkeypatch, no_launch):
    opened = []
    monkeypatch.setattr(desktop, "_visible_windows", lambda: {101: "Bloc-notes"})
    monkeypatch.setattr(desktop, "open_app_window", lambda url: opened.append(url))
    assert desktop.show_app_window("http://127.0.0.1:8788") == "opened"
    assert opened == ["http://127.0.0.1:8788"]


def test_startup_with_missed_reminders_opens_a_single_window(win32, monkeypatch, no_launch):
    # At startup the launcher (--app) and the reminders that came due while
    # JARVIS was off all want the window, before the browser has drawn it (no
    # title yet: find_app_window sees nothing). Only one window opens.
    monkeypatch.setattr(desktop, "_visible_windows", lambda: {101: "Bloc-notes"})
    opened = []
    monkeypatch.setattr(desktop, "open_app_window", lambda url: opened.append(url))
    monkeypatch.setattr(desktop, "toast", lambda title, body: None)
    monkeypatch.setattr(desktop, "attention_state", lambda: "ok")
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    monkeypatch.setattr(config, "REOPEN_ON_REMINDER", True)
    monkeypatch.setattr(events, "leader", lambda: None)
    monkeypatch.setattr(server, "_already_running", lambda url: True)
    inbox._toasted.clear()
    callers = [threading.Thread(target=inbox.notify_offline, args=("Rappel", f"Rappel manqué {i}"))
               for i in range(2)]
    callers.append(threading.Thread(target=server._open_when_ready, args=("http://127.0.0.1:8788",)))
    for t in callers:
        t.start()
    for t in callers:
        t.join(5)
    assert len(opened) == 1
    assert desktop.show_app_window("http://127.0.0.1:8788") == "opening"  # still coming up
    assert len(opened) == 1
    # Long after, a window closed by monsieur is opened again.
    monkeypatch.setitem(desktop._launched, "at", time.monotonic() - desktop.LAUNCH_GRACE - 1)
    assert desktop.show_app_window("http://127.0.0.1:8788") == "opened"
    assert len(opened) == 2
    inbox._toasted.clear()


def test_find_app_window_prefers_the_app_window_and_ignores_other_programs(win32, monkeypatch):
    win32.classes = {1: "Chrome_WidgetWin_1", 2: "Notepad", 3: "Chrome_WidgetWin_1",
                     4: "Chrome_WidgetWin_1"}
    windows = {1: "J.A.R.V.I.S. et 2 pages de plus - Personnel \u2013 Microsoft\u200b Edge",
               2: "J.A.R.V.I.S. idées.txt - Bloc-notes",   # a file, not JARVIS
               3: "J.A.R.V.I.S.",                          # the --app window
               4: "Météo - J.A.R.V.I.S. - Google Chrome"}
    monkeypatch.setattr(desktop, "_visible_windows", lambda: dict(windows))
    assert desktop.find_app_window() == 3
    del windows[3]
    assert desktop.find_app_window() == 1  # a tab whose title starts with it
    del windows[1], windows[4]
    assert desktop.find_app_window() is None


def test_flash_does_nothing_when_jarvis_is_already_in_front(win32, monkeypatch):
    monkeypatch.setattr(desktop, "_visible_windows", lambda: {202: "J.A.R.V.I.S."})
    win32.foreground = 202
    assert desktop.flash_app_window() is False and not win32.flashes
    win32.foreground = 7
    assert desktop.flash_app_window() is True and win32.flashes[0]["hwnd"] == 202


def test_first_launch_reuses_a_window_left_from_the_previous_run(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_already_running", lambda url: True)
    monkeypatch.setattr(server.desktop, "show_app_window", lambda url: calls.append(("show", url)))
    monkeypatch.setattr(server.desktop, "open_app_window", lambda url: calls.append(("open", url)))
    server._open_when_ready("http://127.0.0.1:8788")
    assert calls == [("show", "http://127.0.0.1:8788")]


# ---------------------------------------------------------------- the app window's browser

@pytest.fixture
def browsers(tmp_path, monkeypatch):
    chrome = tmp_path / "Google" / "chrome.exe"
    edge = tmp_path / "Edge" / "msedge.exe"
    for exe in (chrome, edge):
        exe.parent.mkdir(parents=True)
        exe.write_text("")
    monkeypatch.setattr(desktop, "_browser_candidates", lambda: ([None, chrome], [edge]))
    return types.SimpleNamespace(chrome=chrome, edge=edge)


def test_browser_setting_auto_chrome_edge(browsers, monkeypatch, caplog):
    monkeypatch.setattr(config, "BROWSER", "auto")
    assert desktop._app_browser() == str(browsers.chrome)  # its local recognition keeps the wake word here
    monkeypatch.setattr(config, "BROWSER", "edge")
    assert desktop._app_browser() == str(browsers.edge)
    monkeypatch.setattr(config, "BROWSER", "chrome")
    browsers.chrome.unlink()
    assert desktop._app_browser() == str(browsers.edge)
    assert "chrome introuvable" in caplog.text


def test_app_window_has_its_own_profile_per_browser_and_a_capped_cache(browsers, monkeypatch, no_launch):
    monkeypatch.setattr(config, "BROWSER", "auto")
    desktop.open_app_window("http://127.0.0.1:8788")
    cmd = no_launch[0]
    assert cmd[0] == str(browsers.chrome) and "--app=http://127.0.0.1:8788" in cmd
    assert f"--user-data-dir={config.DATA_DIR / 'app-window-chrome'}" in cmd
    assert "--disk-cache-size=104857600" in cmd
    # Edge takes over the profile of older versions (Edge came first then): the
    # microphone permission granted there is kept.
    legacy = config.DATA_DIR / "app-window"
    (legacy / "Default").mkdir(parents=True)
    monkeypatch.setattr(config, "BROWSER", "edge")
    desktop.open_app_window("http://127.0.0.1:8788")
    assert f"--user-data-dir={config.DATA_DIR / 'app-window-msedge'}" in no_launch[1]
    assert (config.DATA_DIR / "app-window-msedge" / "Default").is_dir() and not legacy.exists()


def test_no_chromium_browser_falls_back_to_the_default_browser(monkeypatch, no_launch):
    monkeypatch.setattr(desktop, "_browser_candidates", lambda: ([None], [None]))
    desktop.open_app_window("http://127.0.0.1:8788")
    assert no_launch == [("browser", "http://127.0.0.1:8788")]


# ---------------------------------------------------------------- attention, sleep, notifications

def test_attention_state_maps_windows_answers(win32, monkeypatch):
    monkeypatch.setattr(desktop, "_visible_windows", lambda: {})
    expected = {1: "not_present", 2: "busy", 3: "fullscreen", 4: "presentation", 5: "ok",
                6: "quiet_time", 7: "app", 42: "ok"}
    for value, name in expected.items():
        win32.quns = value
        assert desktop.attention_state() == name, value
    win32.quns, win32.hr = 2, -2147467259  # E_FAIL: Windows doesn't know
    assert desktop.attention_state() == "ok"


def test_jarvis_itself_in_full_screen_is_not_busy(win32, monkeypatch):
    monkeypatch.setattr(desktop, "_visible_windows", lambda: {202: "J.A.R.V.I.S."})
    win32.quns, win32.foreground = 2, 202  # F11 in JARVIS's own window
    assert desktop.attention_state() == "ok"
    win32.foreground = 303  # a game
    assert desktop.attention_state() == "busy"


def test_attention_elsewhere_than_windows_is_ok(monkeypatch):
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    monkeypatch.setattr(desktop, "_WIN", None)
    assert desktop.attention_state() == "ok"
    assert desktop.keep_awake() is False and desktop.flash_app_window() is False


def test_idle_seconds_counts_from_the_last_input(win32):
    """GetLastInputInfo and GetTickCount (ntfy's "only when I'm away", B1)."""
    seen = []

    def last_input(info, when=40_000, ok=1):
        seen.append(info._obj.cbSize)
        info._obj.dwTime = when
        return ok
    win32.GetLastInputInfo, win32.GetTickCount = last_input, lambda: 100_000
    assert desktop.idle_seconds() == 60.0
    assert seen == [ctypes.sizeof(desktop.LASTINPUTINFO)] == [8]
    # Both counters wrap after 49.7 days: the difference stays right across the wrap.
    win32.GetLastInputInfo = lambda info: last_input(info, when=0x1_0000_0000 - 5_000)
    win32.GetTickCount = lambda: 5_000
    assert desktop.idle_seconds() == 10.0
    win32.GetLastInputInfo = lambda info: last_input(info, ok=0)  # Windows said no
    assert desktop.idle_seconds() is None

    def broken(info):
        raise OSError("refusé")
    win32.GetLastInputInfo = broken
    assert desktop.idle_seconds() is None


def test_idle_seconds_is_unknown_elsewhere_than_windows(monkeypatch, win32):
    assert desktop.idle_seconds() is None  # this Windows lacks the functions
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    monkeypatch.setattr(desktop, "_win", lambda: None)
    assert desktop.idle_seconds() is None


def test_keep_awake_resets_the_idle_timer_without_holding_it(win32):
    assert desktop.keep_awake() is True
    # ES_SYSTEM_REQUIRED only: no ES_CONTINUOUS (0x80000000), nothing to undo later.
    assert win32.calls == [("SetThreadExecutionState", 0x1)]


def test_toast_goes_through_the_tray_icon(monkeypatch, fresh_shell):
    icon = FakeIcon("jarvis", None, "JARVIS")
    icon.visible = True
    monkeypatch.setattr(shell, "_tray", icon)
    long_body = "Résultat " + "très long " * 60
    assert desktop.toast("Tâche « Météo »", long_body) is True
    title, message = icon.notes[0]
    assert title == "Tâche « Météo »"
    assert len(message) <= 255 and message.endswith("…")  # Windows' fixed buffer


def test_toast_without_the_app_running_shows_nothing(monkeypatch, no_launch, fresh_shell):
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    shown = []
    monkeypatch.setattr(desktop, "_toast_powershell", lambda title, body: shown.append(title))
    assert desktop.toast("Rappel", "Thé") is False  # a test, a command line: no pop-up
    time.sleep(0.05)
    assert not shown and not no_launch


def test_toast_without_tray_uses_windows_own_notification(monkeypatch, fresh_shell):
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(shell, "_state", {"url": "", "on_quit": None, "running": True})
    scripts = []
    monkeypatch.setattr(desktop, "_powershell",
                        lambda script, env=None, timeout=20: scripts.append((script, env)) or "")
    title = "Rappel <b>&amp;"
    assert desktop.toast(title, "Sortir\nle pain $(Get-Process)") is True
    assert wait_for(lambda: scripts)
    script, env = scripts[0]
    # The texts travel in the environment and become XML text nodes: never markup or code.
    assert env == {"JARVIS_TOAST_TITLE": title, "JARVIS_TOAST_BODY": "Sortir le pain $(Get-Process)"}
    assert title not in script and "Get-Process" not in script
    assert "CreateTextNode($env:JARVIS_TOAST_TITLE)" in script


def test_powershell_output_is_utf8(monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        seen["script"] = base64.b64decode(cmd[-1]).decode("utf-16-le")
        seen["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="Météo".encode("utf-8"), stderr=b"")

    monkeypatch.setattr(desktop.subprocess, "run", run)
    assert desktop._powershell("Get-StartApps") == "Météo"
    assert seen["script"].startswith("[Console]::OutputEncoding=[Text.Encoding]::UTF8;")
    assert "text" not in seen["kwargs"]  # bytes, decoded as UTF-8 here
    assert set(os.environ) <= set(seen["kwargs"]["env"])  # SYSTEMROOT and the rest kept


# ---------------------------------------------------------------- Store apps

START_APPS = json.dumps([
    {"Name": "Calculatrice", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
    {"Name": "Météo", "AppID": "Microsoft.BingWeather_8wekyb3d8bbwe!App"},
    {"Name": "Paramètres", "AppID": "windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel"},
    {"Name": "Spotify", "AppID": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"},
    {"Name": "Désinstaller Spotify", "AppID": "C:\\Spotify\\uninstall.exe"},
    {"Name": "WhatsApp", "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"},
], ensure_ascii=False)


def test_start_apps_match_ignores_accents_and_case():
    apps = desktop.parse_start_apps(START_APPS)
    assert len(apps) == 6
    for said in ("calculatrice", "Calculatrice", "CALCULATRICE", "calculâtrice"):
        assert desktop.match_start_app(said, apps)["name"] == "Calculatrice", said
    assert desktop.match_start_app("meteo", apps)["name"] == "Météo"
    assert desktop.match_start_app("MÉTÉO", apps)["name"] == "Météo"
    assert desktop.match_start_app("spotify", apps)["name"] == "Spotify"  # not its uninstaller
    assert desktop.match_start_app("whatsapp", apps)["name"] == "WhatsApp"
    assert desktop.match_start_app("photoshop", apps) is None
    assert desktop.match_start_app("ca", apps) is None  # too short to guess from


def test_an_uninstaller_is_never_launched_by_a_fuzzy_match():
    apps = [{"name": "Désinstaller Spotify", "app_id": "C:\\Spotify\\uninstall.exe"},
            {"name": "Spotify - Lisez-moi", "app_id": "C:\\Spotify\\readme.txt"}]
    assert desktop.match_start_app("spotify", apps) is None
    assert desktop.match_start_app("Désinstaller Spotify", apps) is apps[0]  # said in full: his call


def test_start_apps_json_variants():
    one = json.dumps({"Name": "Courrier", "AppID": "microsoft.windowscommunicationsapps!mail"})
    assert desktop.parse_start_apps(one) == [{"name": "Courrier",
                                              "app_id": "microsoft.windowscommunicationsapps!mail"}]
    assert desktop.parse_start_apps("") == [] and desktop.parse_start_apps("pas du json") == []
    assert desktop.parse_start_apps('[{"Name": "x"}, 3, {"Name": "", "AppID": "y"}]') == []


@pytest.fixture
def store_pc(monkeypatch, no_launch):
    """Windows with no matching shortcut or exe: only the Start menu's list."""
    runs = []
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop, "_find_shortcut", lambda name: None)
    monkeypatch.setattr(desktop, "_find_exe", lambda name: None)
    monkeypatch.setattr(desktop, "_powershell", lambda script, **k: runs.append(script) or START_APPS)
    monkeypatch.setattr(desktop, "_start_apps_cache", {"at": 0.0, "apps": []})
    return types.SimpleNamespace(launched=no_launch, runs=runs)


def test_open_app_launches_a_store_app_through_shell_appsfolder(store_pc, monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    out = desktop.open_target(name="météo")
    assert out["ok"] and out["launched"] == "Météo" and out["via"] == "menu Démarrer"
    explorer, target = store_pc.launched[0]
    assert explorer.endswith("explorer.exe") and explorer.startswith(r"C:\Windows")
    assert target == "shell:AppsFolder\\Microsoft.BingWeather_8wekyb3d8bbwe!App"
    out = desktop.open_target(name="Calculatrice")  # alias calc not on PATH here: the Store app
    assert out["ok"] and store_pc.launched[1][1].startswith("shell:AppsFolder\\Microsoft.WindowsCalculator")
    assert len(store_pc.runs) == 1  # Get-StartApps cached (10 min)
    assert "Get-StartApps" in store_pc.runs[0]
    assert desktop.open_target(name="Photoshop")["ok"] is False


def test_settings_alias_opens_the_settings_page(store_pc):
    out = desktop.open_target(name="Paramètres")
    assert out["ok"] and store_pc.launched[0][1] == "ms-settings:"
    assert not store_pc.runs  # no need for the list


def test_alias_names_an_exe_found_on_path(monkeypatch, no_launch):
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop, "_find_exe", lambda name: r"C:\Windows\System32\calc.exe" if name == "calc" else None)
    monkeypatch.setattr(desktop, "_find_shortcut", lambda name: pytest.fail("raccourci cherché"))
    out = desktop.open_target(name="calculatrice")
    assert out["ok"] and out["via"] == "exe" and no_launch == [[r"C:\Windows\System32\calc.exe"]]


def test_start_apps_failure_is_cached_and_said(monkeypatch, caplog):
    calls = []

    def broken(script, **k):
        calls.append(script)
        raise RuntimeError("Get-StartApps : terme non reconnu")

    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop, "_powershell", broken)
    monkeypatch.setattr(desktop, "_start_apps_cache", {"at": 0.0, "apps": []})
    assert desktop.start_apps() == [] and desktop.start_apps() == []
    assert len(calls) == 1 and "menu Démarrer indisponible" in caplog.text


# ---------------------------------------------------------------- the Explorer

def test_reveal_selects_the_file_and_never_runs_it(tmp_path, monkeypatch, no_launch):
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    target = tmp_path / "dossier, avec espace" / "lancer.bat"
    target.parent.mkdir()
    target.write_text("echo pirate")
    desktop.reveal_in_explorer(str(target))
    command = no_launch[0]
    assert isinstance(command, str)  # passed as is: Explorer's own /select, syntax
    assert command == r"C:\Windows\explorer.exe" + f' /select,"{target}"'


@pytest.mark.parametrize("bad", ["relatif/fichier.txt", "", 'C:\\a"b.txt'])
def test_reveal_refuses_odd_paths(bad, monkeypatch, no_launch):
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    with pytest.raises(ValueError):
        desktop.reveal_in_explorer(bad)
    assert not no_launch


def test_reveal_missing_file(tmp_path, monkeypatch, no_launch):
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    with pytest.raises(FileNotFoundError):
        desktop.reveal_in_explorer(str(tmp_path / "parti.txt"))
    assert not no_launch


def test_reveal_elsewhere_opens_the_folder_not_the_file(tmp_path, monkeypatch, no_launch):
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    monkeypatch.setattr(config, "IS_MAC", False)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/" + name)
    target = tmp_path / "script.sh"
    target.write_text("rm -rf /")
    assert desktop.reveal_in_explorer(str(target)) == ["xdg-open", str(tmp_path)]
    monkeypatch.setattr(config, "IS_MAC", True)
    assert desktop.reveal_in_explorer(str(target)) == ["open", "-R", str(target)]  # Finder selects it
    monkeypatch.setattr(config, "IS_MAC", False)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    with pytest.raises(NotImplementedError):
        desktop.reveal_in_explorer(str(target))


def _reveal_route():
    """The route of 'Afficher dans l'explorateur' (the task panel's package serves it)."""
    from test_security import served_routes
    return next((path for path, methods in served_routes().items()
                 if "reveal" in path and "POST" in methods), None)


def test_reveal_route_refuses_a_file_the_task_did_not_write(tmp_path, monkeypatch):
    path = _reveal_route()
    if path is None:
        pytest.skip("route POST …/reveal pas encore servie (lot du panneau des tâches)")
    written, other = tmp_path / "rapport.md", tmp_path / "secret.bat"
    written.write_text("# Rapport")
    other.write_text("echo")
    tasks.TASKS["t1"] = {"id": "t1", "title": "Rapport", "status": "done", "files": [str(written)],
                         "started": time.time(), "profile": "lecture"}
    revealed = []
    monkeypatch.setattr(desktop, "reveal_in_explorer", lambda p: revealed.append(str(p)) or [])
    client = TestClient(server.app, base_url="http://127.0.0.1:8788")
    url = path.replace("{task_id}", "t1")
    headers = {"X-Jarvis-Token": security.TOKEN}
    r = client.post(url, json={"task_id": "t1", "path": str(other)}, headers=headers)
    assert r.status_code == 403 and not revealed
    r = client.post(url, json={"task_id": "t1", "path": str(written)}, headers=headers)
    assert r.status_code == 200 and revealed == [str(written)]


# ---------------------------------------------------------------- screenshots, .env

def test_screenshots_go_to_the_pictures_known_folder(win32, tmp_path):
    freed = []

    def known_folder(guid, flags, token, out):
        assert bytes(guid._obj) == __import__("uuid").UUID(desktop.FOLDERID_PICTURES).bytes_le
        out._obj.value = str(tmp_path / "OneDrive" / "Images")
        return 0

    win32.SHGetKnownFolderPath = known_folder
    win32.CoTaskMemFree = lambda p: freed.append(p)
    assert desktop.pictures_dir() == tmp_path / "OneDrive" / "Images"
    assert freed  # the string Windows allocated is given back
    win32.SHGetKnownFolderPath = lambda *a: -2147024894  # not found
    assert desktop.pictures_dir() == Path.home() / "Pictures"


def test_env_saved_in_windows_1252_keeps_its_accents(tmp_path, monkeypatch, caplog):
    env = tmp_path / ".env"
    env.write_bytes("JARVIS_TEST_CITY=Orléans\n".encode("cp1252"))
    monkeypatch.delenv("JARVIS_TEST_CITY", raising=False)
    config.load_env(env)
    assert os.environ["JARVIS_TEST_CITY"] == "Orléans"
    assert "Windows-1252" in caplog.text
    monkeypatch.delenv("JARVIS_TEST_CITY")
    env.write_bytes("\ufeffJARVIS_TEST_CITY=Orléans\n".encode("utf-8"))  # Notepad's UTF-8 with BOM
    caplog.clear()
    config.load_env(env)
    assert os.environ["JARVIS_TEST_CITY"] == "Orléans" and "Windows-1252" not in caplog.text


# ---------------------------------------------------------------- the real Windows API (Windows only)

win_only = pytest.mark.skipif(not ON_WINDOWS, reason="API Windows réelle")


@win_only
def test_real_windows_api_loads_and_answers():
    assert shell._win() is not None and desktop._win() is not None
    assert desktop.attention_state() in set(desktop.ATTENTION.values())
    assert isinstance(desktop.keep_awake(), bool)
    idle = desktop.idle_seconds()
    assert idle is None or idle >= 0
    hwnd = desktop.find_app_window()
    assert hwnd is None or isinstance(hwnd, int)
    assert isinstance(desktop.pictures_dir(), Path)


@win_only
def test_real_hotkey_thread_registers_and_stops(published):
    hk = shell.Hotkey(shell._win(), shell.parse_hotkey("ctrl+alt+shift+f24"))
    hk.start()
    try:
        # Registered, or Windows said why (a CI desktop may refuse): never a crash.
        assert hk.ok or any("indisponible" in e.get("text", "") for e in published)
        assert hk.tid
    finally:
        hk.stop()
    assert not hk.thread.is_alive()


def test_shell_imports_anywhere_without_side_effects():
    """The module loads on any system (the Windows CI job imports it too) and
    starts nothing by itself: no thread, no icon, no hotkey before start()."""
    import subprocess
    code = ("import threading; from jarvis import desktop, shell; "
            "assert threading.active_count() == 1, threading.enumerate(); "
            "assert shell._hotkey is None and shell._tray is None and not shell.running(); "
            "assert 'pystray' not in __import__('sys').modules; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parent.parent),
                         capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
