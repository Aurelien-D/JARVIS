"""Windows shell integration, for as long as the server runs (server.py calls
start() before it and stop() after):

- a global hotkey (config.HOTKEY, Ctrl+Alt+Maj+J by default): JARVIS's window
  comes to the front and the page starts or stops listening ('hotkey' event).
  A combination another program already holds is refused by Windows: JARVIS
  then says so in French ('warning' event) instead of failing silently;
- an icon in the notification area (pystray, optional): Ouvrir JARVIS,
  Parler, Mot d'éveil, Ne pas déranger 1 h, Démarrer avec Windows, Quitter;
- native notifications through that icon (desktop.toast);
- the PC kept awake while a Claude task runs.

Elsewhere than Windows, or without pystray, each part is simply absent and
JARVIS works as before.
"""
import atexit
import ctypes
import logging
import threading
import time

from . import config, desktop, events

# RegisterHotKey's modifiers and messages (winuser.h).
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_QUIT, WM_HOTKEY, WM_USER, WM_APP = 0x0012, 0x0312, 0x0400, 0x8000
WM_SWAP = WM_APP + 0x4A  # set_hotkey(): register another combination on the hotkey thread
PM_NOREMOVE = 0
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 0x4A52  # any id below 0xC000 for an application
AWAKE_SECONDS = 30
OFF = ("", "0", "off", "non", "aucun", "none", "false")

_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "ctl": MOD_CONTROL,
              "alt": MOD_ALT, "shift": MOD_SHIFT, "maj": MOD_SHIFT, "majuscule": MOD_SHIFT,
              "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN}
_NAMED_KEYS = {"space": 0x20, "espace": 0x20, "pause": 0x13, "insert": 0x2D, "inser": 0x2D,
               "home": 0x24, "debut": 0x24, "end": 0x23, "fin": 0x23,
               "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "gauche": 0x25, "up": 0x26,
               "haut": 0x26, "right": 0x27, "droite": 0x27, "down": 0x28, "bas": 0x28}
_KEY_LABELS = {0x13: "Pause", 0x2D: "Inser", 0x24: "Début", 0x23: "Fin", 0x21: "PgPréc",
               0x22: "PgSuiv", 0x25: "Gauche", 0x26: "Haut", 0x27: "Droite", 0x28: "Bas"}
# A.R.E.S registers these at its start (main.cjs): taking one would break it.
_ARES = {(MOD_CONTROL | MOD_ALT, ord(k)) for k in "VMJK"} | {(MOD_CONTROL | MOD_ALT, 0x20)}
_HINT = "Choisissez-en un autre dans Réglages › Système."


# ---------------------------------------------------------------- the combination

def parse_hotkey(spec: str) -> tuple:
    """'ctrl+alt+shift+j' -> (MOD_CONTROL|MOD_ALT|MOD_SHIFT, 0x4A), without
    MOD_NOREPEAT (added at registration). ValueError, in French, for anything
    Windows would refuse or that would get in monsieur's way."""
    text = str(spec or "").strip().lower().replace(" ", "")
    parts = text.split("+") if text else []
    if not parts:
        raise ValueError("Raccourci vide : par exemple ctrl+alt+maj+j.")
    if any(not p for p in parts):
        raise ValueError(f"Raccourci « {spec} » invalide : par exemple ctrl+alt+maj+j.")
    *mods, key = parts
    flags = 0
    for m in mods:
        bit = _MODIFIERS.get(m)
        if bit is None:
            raise ValueError(f"Raccourci « {spec} » invalide : « {m} » n'est pas Ctrl, Alt, Maj ni Win.")
        if flags & bit:
            raise ValueError(f"Raccourci « {spec} » invalide : « {m} » est en double.")
        flags |= bit
    fkey = key[1:].isdigit() and key.startswith("f") and 1 <= int(key[1:]) <= 24
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        vk = ord(key.upper())
    elif fkey:
        if key == "f12":
            raise ValueError("F12 est réservée par Windows (débogueur) : choisissez une autre touche.")
        vk = 0x70 + int(key[1:]) - 1
    elif key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    else:
        raise ValueError(f"Raccourci « {spec} » invalide : la dernière touche doit être une lettre, "
                         "un chiffre ou F1 à F24.")
    # Maj alone (or nothing) with a letter would take that letter from every program.
    if not fkey and not flags & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise ValueError(f"Raccourci « {spec} » invalide : ajoutez Ctrl, Alt ou Win.")
    if (flags, vk) in _ARES:
        raise ValueError(f"{label(flags, vk)} est déjà pris par A.R.E.S : choisissez-en un autre.")
    return flags, vk


def label(mods: int, vk: int) -> str:
    """(7, 0x4A) -> 'Ctrl+Alt+Maj+J', the way the French keyboard names it."""
    names = [n for bit, n in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Maj"),
                              (MOD_WIN, "Win")) if mods & bit]
    if 0x70 <= vk <= 0x87:
        key = f"F{vk - 0x6F}"
    elif vk == 0x20:
        key = "Espace"
    elif 0x30 <= vk <= 0x5A:
        key = chr(vk)
    else:
        key = _KEY_LABELS.get(vk, hex(vk))
    return "+".join(names + [key])


def unavailable_text(combo: str, code: int = ERROR_HOTKEY_ALREADY_REGISTERED) -> str:
    why = "déjà utilisé" if code in (0, ERROR_HOTKEY_ALREADY_REGISTERED) else f"erreur Windows {code}"
    return f"Raccourci {combo} indisponible ({why}). {_HINT}"


def _warn(text: str):
    logging.warning("JARVIS: %s", text)
    try:
        events.publish("warning", {"kind": "hotkey", "text": text})
    except Exception:  # noqa: BLE001 - the log has it
        logging.exception("JARVIS: avertissement non publié")


def _wanted(spec):
    """config.HOTKEY as (mods, vk), or None (no hotkey, or an invalid one: said once)."""
    if str(spec or "").strip().lower() in OFF:
        return None
    try:
        return parse_hotkey(spec)
    except ValueError as exc:
        _warn(f"{exc} {_HINT}")
        return None


# ---------------------------------------------------------------- Win32 (hotkey thread)

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", POINT), ("lPrivate", ctypes.c_uint32)]


_API = None


def _load_api():
    from types import SimpleNamespace
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def fn(dll, name, restype, *argtypes):
        f = getattr(dll, name)
        f.restype, f.argtypes = restype, argtypes
        return f

    lpmsg, hwnd, uint = ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint
    return SimpleNamespace(
        RegisterHotKey=fn(user32, "RegisterHotKey", ctypes.c_int, hwnd, ctypes.c_int, uint, uint),
        UnregisterHotKey=fn(user32, "UnregisterHotKey", ctypes.c_int, hwnd, ctypes.c_int),
        GetMessageW=fn(user32, "GetMessageW", ctypes.c_int, lpmsg, hwnd, uint, uint),
        PeekMessageW=fn(user32, "PeekMessageW", ctypes.c_int, lpmsg, hwnd, uint, uint, uint),
        PostThreadMessageW=fn(user32, "PostThreadMessageW", ctypes.c_int, ctypes.c_uint32, uint,
                              ctypes.c_size_t, ctypes.c_ssize_t),
        GetCurrentThreadId=fn(kernel32, "GetCurrentThreadId", ctypes.c_uint32),
        get_last_error=ctypes.get_last_error,
    )


def _win():
    """user32/kernel32 functions for the hotkey, or None elsewhere than Windows."""
    global _API
    if _API is None:
        try:
            _API = _load_api() if config.IS_WINDOWS else False
        except (OSError, AttributeError):
            logging.exception("JARVIS: raccourci global indisponible")
            _API = False
    return _API or None


class Hotkey:
    """The thread that owns the global hotkey. RegisterHotKey with no window
    posts WM_HOTKEY to the calling thread's queue: registering, the message
    loop and UnregisterHotKey all happen on this one thread."""

    def __init__(self, api, combo):
        self.api = api
        self.combo = combo          # (mods, vk) or None
        self.ok = False             # registered with Windows
        self.tid = 0
        self.ready = threading.Event()
        self._swap = None           # set_hotkey()'s request: (combo, done, result)
        self._swap_lock = threading.Lock()
        self._last = 0.0
        self.thread = threading.Thread(target=self.run, name="jarvis-hotkey", daemon=True)

    def start(self, timeout: float = 2.0) -> bool:
        self.thread.start()
        self.ready.wait(timeout)
        return self.ok

    def _register(self, combo) -> int:
        """0 when registered, else Windows' error code."""
        if self.api.RegisterHotKey(None, HOTKEY_ID, combo[0] | MOD_NOREPEAT, combo[1]):
            return 0
        return self.api.get_last_error() or ERROR_HOTKEY_ALREADY_REGISTERED

    def run(self):
        api, msg = self.api, MSG()
        try:
            # The queue must exist before stop() or set_hotkey() post to this thread.
            api.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)
            self.tid = api.GetCurrentThreadId()
            if self.combo:
                code = self._register(self.combo)
                self.ok = code == 0
                if not self.ok:
                    _warn(unavailable_text(label(*self.combo), code))
        finally:
            self.ready.set()
        try:
            while True:
                got = api.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got == 0:  # WM_QUIT: stop()
                    break
                if got == -1:
                    logging.error("JARVIS: boucle du raccourci global interrompue")
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self._pressed()
                elif msg.message == WM_SWAP:
                    self._do_swap()
        finally:
            if self.ok:
                api.UnregisterHotKey(None, HOTKEY_ID)
                self.ok = False

    def _pressed(self):
        now = time.monotonic()
        if now - self._last < 0.4:  # a double tap toggles once
            return
        self._last = now
        try:
            on_hotkey("toggle")
        except Exception:  # noqa: BLE001 - the next press must still work
            logging.exception("JARVIS: raccourci global")

    def _do_swap(self):
        request, self._swap = self._swap, None
        if not request:
            return
        combo, done, result = request
        old, was_ok = self.combo, self.ok
        if self.ok:
            self.api.UnregisterHotKey(None, HOTKEY_ID)
            self.ok = False
        if combo is None:
            self.combo = None
            result["ok"] = True
        else:
            code = self._register(combo)
            if code == 0:
                self.combo, self.ok = combo, True
                result["ok"] = True
            else:
                result.update(ok=False, error=unavailable_text(label(*combo), code))
                if was_ok and old:  # the new one is taken: keep the one that worked
                    self.ok = self._register(old) == 0
                _warn(result["error"])
        done.set()

    def swap(self, combo, timeout: float = 2.0) -> dict:
        with self._swap_lock:  # one change at a time
            done, result = threading.Event(), {"ok": False}
            self._swap = (combo, done, result)
            if not self.api.PostThreadMessageW(self.tid, WM_SWAP, 0, 0):
                self._swap = None
                return {"ok": False, "error": "Le raccourci n'a pas pu être changé : redémarrez JARVIS."}
            done.wait(timeout)
            return result

    def stop(self, timeout: float = 2.0):
        if self.tid:
            self.api.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout)


# ---------------------------------------------------------------- what the hotkey and the icon do

_state = {"url": "", "on_quit": None, "running": False}
_hotkey = None   # Hotkey
_tray = None     # pystray.Icon
_awake_stop = threading.Event()


def _url() -> str:
    return _state["url"] or f"http://127.0.0.1:{config.PORT}"


def on_hotkey(action: str = "toggle"):
    """The page acts on 'hotkey': 'toggle' (the hotkey: talk or back to
    standby), 'talk' (the icon's Parler), 'wake' (the wake word on or off, a
    page setting). The window comes to the front first, except for 'wake' while
    a page is open: the page's own toast says the new state. 'at' lets a page
    ignore an old event replayed after a reconnection."""
    if action != "wake" or events.leader() is None:
        try:
            desktop.show_app_window(_url())
        except Exception:  # noqa: BLE001
            logging.exception("JARVIS: fenêtre JARVIS introuvable")
    if action == "wake" and events.leader() is None:
        notify("JARVIS", "Le mot d'éveil se règle dans la fenêtre JARVIS, qui s'ouvre.")
        return
    events.publish("hotkey", {"action": action, "at": time.time()})


def set_hotkey(spec) -> dict:
    """Swap the global hotkey now (Réglages › Système). ValueError (French) for
    an invalid combination; an unavailable one keeps the previous hotkey and
    says so. Without a running hotkey thread (not Windows, JARVIS not started),
    config.HOTKEY is simply used at the next start."""
    combo = None if str(spec or "").strip().lower() in OFF else parse_hotkey(spec)
    hk = _hotkey
    if hk is None or not hk.thread.is_alive():
        return {"ok": False, "applied": False}
    return hk.swap(combo)


def hotkey_state() -> dict:
    """For the settings and the health check: which combination works now."""
    hk = _hotkey
    active = bool(hk and hk.ok and hk.combo)
    return {"active": active, "combo": label(*hk.combo) if active else ""}


def running() -> bool:
    return _state["running"]


def notify(title: str, body: str) -> bool:
    """A notification from the tray icon (Windows shows it as a toast).
    False when there is no icon to show it: desktop.toast has other ways."""
    icon = _tray
    if icon is None or not getattr(icon, "HAS_NOTIFICATION", False) or not getattr(icon, "visible", False):
        return False
    try:
        icon.notify(desktop.fit_line(body, 255) or " ", desktop.fit_line(title, 63) or "JARVIS")
        return True
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: notification impossible")
        return False


def _safely(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - a tray click must never stop the icon
        logging.exception("JARVIS: action de l'icône")
        return None


def _dnd_on() -> bool:
    from . import inbox  # late: inbox imports desktop, which imports this module
    return _safely(inbox.dnd_until) is not None


def _toggle_dnd():
    from . import inbox
    _safely(inbox.set_dnd, None if _dnd_on() else time.time() + 3600)


def _toggle_autostart(icon):
    message = _safely(desktop.set_autostart, not desktop.autostart_enabled())
    if message:
        notify("JARVIS", message)


def _quit(icon):
    on_quit = _state["on_quit"]
    try:
        if on_quit:
            on_quit()  # the same path as POST /api/shutdown
    finally:
        _safely(icon.stop)


def tray_menu(pystray):
    item, menu = pystray.MenuItem, pystray.Menu
    return menu(
        item("Ouvrir JARVIS", lambda: _safely(desktop.show_app_window, _url()), default=True),
        item("Parler", lambda: _safely(on_hotkey, "talk")),
        item("Mot d'éveil (activer ou couper)", lambda: _safely(on_hotkey, "wake")),
        item("Ne pas déranger 1 h", _toggle_dnd, checked=lambda _item: _dnd_on()),
        item("Démarrer avec Windows", _toggle_autostart,
             checked=lambda _item: bool(_safely(desktop.autostart_enabled))),
        menu.SEPARATOR,
        item("Quitter JARVIS", _quit),
    )


def tray_image():
    """The orb, small: a cyan ring around a cyan core, on transparency."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((3, 3, 60, 60), fill=(5, 13, 20, 255), outline=(64, 220, 255, 255), width=6)
    draw.ellipse((22, 22, 41, 41), fill=(64, 220, 255, 255))
    return img


def _start_tray():
    if not config.TRAY:
        return None
    try:
        import pystray
        icon = pystray.Icon("jarvis", tray_image(), "JARVIS", menu=tray_menu(pystray))
    except Exception as exc:  # noqa: BLE001 - ImportError, or no backend for this desktop
        logging.info("JARVIS: pas d'icône dans la zone de notification (%s)", exc)
        return None
    threading.Thread(target=_run_tray, args=(icon,), name="jarvis-tray", daemon=True).start()
    return icon


def _run_tray(icon):
    """pystray's loop on this daemon thread (safe on Windows, where the icon's
    window lives on the thread that runs it). run_detached() would start a
    non-daemon thread that keeps a windowless JARVIS alive if stop() were ever
    skipped; here even pystray's setup thread inherits daemon."""
    try:
        icon.run()
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: icône de la zone de notification arrêtée")


def _awake_loop():
    """While a Claude task runs or waits its turn, keep Windows from sleeping:
    desktop.keep_awake() only resets the idle timer, so it is repeated and
    simply stops once the tasks are over."""
    from . import tasks  # late: tasks is heavy and not needed to import this module
    while not _awake_stop.wait(AWAKE_SECONDS):
        try:
            if tasks.running():
                desktop.keep_awake()
        except Exception:  # noqa: BLE001
            logging.exception("JARVIS: maintien en éveil")


# ---------------------------------------------------------------- lifetime

def start(url: str, on_quit):
    """Register the hotkey, show the tray icon, watch for running tasks.
    on_quit stops JARVIS (the icon's Quitter)."""
    global _hotkey, _tray
    if _state["running"]:
        stop()  # started twice: one hotkey thread, one icon
    _state.update(url=url, on_quit=on_quit, running=True)
    if not config.IS_WINDOWS:
        return
    api = _win()
    if api:
        # Started even with no combination: set_hotkey() can register one later.
        _hotkey = Hotkey(api, _wanted(config.HOTKEY))
        _hotkey.start()
    _tray = _start_tray()
    _awake_stop.clear()
    threading.Thread(target=_awake_loop, name="jarvis-awake", daemon=True).start()
    # server.py stops us in a finally; this covers any other way out, while the
    # threads still run (a pystray icon still running at exit would wait for its
    # thread forever in its __del__).
    atexit.unregister(stop)
    atexit.register(stop)


def stop():
    """Release the hotkey and remove the icon."""
    global _hotkey, _tray
    _state["running"] = False
    _awake_stop.set()
    hk, _hotkey = _hotkey, None
    if hk:
        _safely(hk.stop)
    icon, _tray = _tray, None
    if icon:
        _safely(icon.stop)
