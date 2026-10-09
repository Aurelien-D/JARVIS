"""Everything that touches the desktop: launching apps and sites, placing
windows on a monitor, instant system actions (volume, media keys, lock,
clipboard), screenshots, JARVIS's own app window and autostart, and how
JARVIS gets monsieur's attention (notifications, a flashing taskbar button)
without interrupting a presentation or a game.

Window placement, the Start Menu lookup and the attention functions use the
Windows API; on macOS and Linux the same functions fall back to the
platform's own tools, or say the action isn't available there.
"""
import base64
import contextlib
import ctypes
import io
import json
import logging
import ntpath
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import config

NO_WINDOW = subprocess.CREATE_NO_WINDOW if config.IS_WINDOWS else 0

if config.IS_WINDOWS:
    import winreg
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    # accurate multi-monitor coordinates under display scaling
    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)

    _MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    _EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)


# ---------------------------------------------------------------- Win32 functions with their signatures

class FLASHWINFO(ctypes.Structure):
    """FlashWindowEx's argument (fixed-size fields: DWORD is 32-bit on Windows)."""
    _fields_ = [("cbSize", ctypes.c_uint32), ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint32), ("uCount", ctypes.c_uint32),
                ("dwTimeout", ctypes.c_uint32)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


_WIN = None  # SimpleNamespace of functions, False once loading failed


def _load_win32():
    """Separate DLL objects: setting argtypes on ctypes.windll's shared ones
    would change them for pystray too. Each function is optional (None when
    this Windows lacks it), so one missing entry point disables one feature."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    hwnd = ctypes.c_void_p

    def fn(dll, name, restype, *argtypes):
        f = getattr(dll, name, None)
        if f is not None:
            f.restype, f.argtypes = restype, argtypes
        return f

    return SimpleNamespace(
        SetForegroundWindow=fn(user32, "SetForegroundWindow", ctypes.c_int, hwnd),
        GetForegroundWindow=fn(user32, "GetForegroundWindow", hwnd),
        ShowWindow=fn(user32, "ShowWindow", ctypes.c_int, hwnd, ctypes.c_int),
        IsIconic=fn(user32, "IsIconic", ctypes.c_int, hwnd),
        GetClassNameW=fn(user32, "GetClassNameW", ctypes.c_int, hwnd, ctypes.c_wchar_p, ctypes.c_int),
        FlashWindowEx=fn(user32, "FlashWindowEx", ctypes.c_int, ctypes.POINTER(FLASHWINFO)),
        SHQueryUserNotificationState=fn(shell32, "SHQueryUserNotificationState", ctypes.c_long,
                                        ctypes.POINTER(ctypes.c_int)),
        SHGetKnownFolderPath=fn(shell32, "SHGetKnownFolderPath", ctypes.c_long, ctypes.POINTER(GUID),
                                ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)),
        CoTaskMemFree=fn(ole32, "CoTaskMemFree", None, ctypes.c_void_p),
        SetThreadExecutionState=fn(kernel32, "SetThreadExecutionState", ctypes.c_uint32, ctypes.c_uint32),
    )


def _win():
    """The Win32 functions, or None (not Windows, or they couldn't be loaded)."""
    global _WIN
    if _WIN is None:
        if not config.IS_WINDOWS:
            return None
        try:
            _WIN = _load_win32()
        except (OSError, AttributeError):
            logging.exception("JARVIS: fonctions Windows indisponibles")
            _WIN = False
    return _WIN or None


def _call(name: str, *args):
    """One Win32 call, or None when that function isn't there."""
    w = _win()
    f = getattr(w, name, None) if w else None
    return f(*args) if f else None


# ---------------------------------------------------------------- monitors & window placement (Windows API)

def _monitors():
    """List monitor rects as (left, top, right, bottom)."""
    if not config.IS_WINDOWS:
        return []
    mons = []

    def cb(hmon, hdc, lprc, lparam):
        r = lprc.contents
        mons.append((r.left, r.top, r.right, r.bottom))
        return 1

    user32.EnumDisplayMonitors(0, 0, _MonitorEnumProc(cb), 0)
    return mons


def _pick_monitor(target: str):
    mons = _monitors()
    if not mons:
        return None
    t = (target or "").strip().lower()
    if t.isdigit():
        i = int(t) - 1
        return mons[i] if 0 <= i < len(mons) else None
    key = {
        "left": lambda m: m[0], "gauche": lambda m: m[0],
        "top": lambda m: m[1], "haut": lambda m: m[1],
    }
    if t in key:
        return min(mons, key=key[t])
    key = {
        "right": lambda m: m[2], "droite": lambda m: m[2], "droit": lambda m: m[2],
        "bottom": lambda m: m[3], "bas": lambda m: m[3],
    }
    if t in key:
        return max(mons, key=key[t])
    # primary: the monitor containing the origin (0,0)
    for m in mons:
        if m[0] <= 0 < m[2] and m[1] <= 0 < m[3]:
            return m
    return mons[0]


def _visible_windows():
    """Map of visible top-level windows: hwnd -> title."""
    if not config.IS_WINDOWS:
        return {}
    wins = {}

    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                wins[hwnd] = buf.value
        return 1

    user32.EnumWindows(_EnumWindowsProc(cb), 0)
    return wins


def _move_to_monitor(hwnd, mon):
    left, top, right, bottom = mon
    SW_RESTORE, SW_MAXIMIZE = 9, 3
    user32.ShowWindow(hwnd, SW_RESTORE)  # a maximized window can't be moved
    user32.MoveWindow(hwnd, left + 40, top + 40,
                      max(400, (right - left) - 80), max(300, (bottom - top) - 80), True)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    user32.SetForegroundWindow(hwnd)


def _place_app_window(app_name: str, before: dict, mon, timeout: float = 20.0):
    """Wait for the app's window to appear, then move it to the target monitor.

    Prefers a NEW window whose title mentions the app; falls back to any new
    window, then to an existing title match (single-instance apps like Discord
    just refocus their already-open window).
    """
    q = app_name.lower()
    deadline = time.time() + timeout
    fallback = None
    while time.time() < deadline:
        wins = _visible_windows()
        new = {h: t for h, t in wins.items() if h not in before}
        for h, title in new.items():
            if q in title.lower():
                _move_to_monitor(h, mon)
                return title
        if new and fallback is None:
            fallback = max(new)  # remember, but keep hoping for a title match
        time.sleep(0.5)
        if fallback and time.time() > deadline - timeout / 2:
            break
    if fallback:
        wins = _visible_windows()
        _move_to_monitor(fallback, mon)
        return wins.get(fallback, app_name)
    # No new window: single-instance app already running -> match existing title.
    for h, title in _visible_windows().items():
        if q in title.lower():
            _move_to_monitor(h, mon)
            return title
    return None


# ---------------------------------------------------------------- open app / url

START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]


def fold(text: str) -> str:
    """'Paramètres' -> 'parametres': accents, case and punctuation don't count
    when matching what monsieur said against an app's name."""
    text = unicodedata.normalize("NFKD", str(text or "")).casefold().replace("œ", "oe").replace("æ", "ae")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


_JUNK = ("uninstall", "desinstaller", "desinstallation", "readme", "read me", "lisez moi", "website")


def _score(q: str, name: str) -> float:
    """How well a folded query matches a folded app name (0: not at all).
    Uninstallers and docs only ever match by their exact name: « Spotify »
    must never launch « Désinstaller Spotify », even when it is the only hit."""
    if not q or not name:
        return 0.0
    if q == name:
        return 100.0
    if len(q) < 3 or any(bad in name for bad in _JUNK):
        return 0.0
    if q in name:
        return 2 + len(q) / len(name)   # substring: prefer the tightest match
    if all(w in name for w in q.split()):
        return 1.0
    return 0.0


def _find_shortcut(name: str):
    """Fuzzy-match a Start Menu shortcut (where installed apps register)."""
    q = fold(name)
    best, best_score = None, 0.0
    for root in START_MENU_DIRS:
        if not root.is_dir():
            continue
        for lnk in root.rglob("*.lnk"):
            score = _score(q, fold(lnk.stem))
            if score >= 100:
                return lnk
            if score > best_score:
                best, best_score = lnk, score
    return best


# Names Windows doesn't list under what monsieur says. A value ending in ':'
# is a URI (opened by the Explorer), anything else an exe looked up as usual.
APP_ALIASES = {
    "parametres": "ms-settings:", "reglages": "ms-settings:", "parametres windows": "ms-settings:",
    "calculatrice": "calc", "calculette": "calc",
    "bloc notes": "notepad", "bloc note": "notepad",
    "explorateur": "explorer", "explorateur de fichiers": "explorer",
    "gestionnaire des taches": "taskmgr", "panneau de configuration": "control",
}

# Get-StartApps lists every app of the Start menu, Microsoft Store ones too
# (no .lnk for those): Name and AppID, launched through shell:AppsFolder.
START_APPS_TTL = 600
_start_apps_cache = {"at": 0.0, "apps": []}
_start_apps_lock = threading.Lock()


def parse_start_apps(text: str) -> list:
    """Get-StartApps | ConvertTo-Json: one object for a single app, a list otherwise."""
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    apps = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        name, app_id = item.get("Name"), item.get("AppID")
        if isinstance(name, str) and isinstance(app_id, str) and name.strip() and app_id.strip():
            apps.append({"name": name.strip(), "app_id": app_id.strip()})
    return apps


def start_apps(refresh: bool = False) -> list:
    """The Start menu's apps (cached 10 min: PowerShell takes a second)."""
    if not config.IS_WINDOWS:
        return []
    with _start_apps_lock:
        now = time.time()
        if not refresh and now - _start_apps_cache["at"] < START_APPS_TTL:
            return _start_apps_cache["apps"]
        try:
            out = _powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
            apps = parse_start_apps(out)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            logging.warning("JARVIS: liste des applications du menu Démarrer indisponible : %s", exc)
            apps = []  # an older Windows without Get-StartApps: not asked again for 10 min
        _start_apps_cache.update(at=now, apps=apps)
        return apps


def match_start_app(name: str, apps: list):
    """The Start menu app whose name best matches (accents and case ignored)."""
    q = fold(name)
    best, best_score = None, 0.0
    for app in apps:
        score = _score(q, fold(app["name"]))
        if score >= 100:
            return app
        if score > best_score:
            best, best_score = app, score
    return best


def _explorer() -> str:
    """Explorer by its full path: never an explorer.exe planted in the current folder."""
    return ntpath.join(os.environ.get("SystemRoot") or r"C:\Windows", "explorer.exe")


def _launch_start_app(app: dict):
    if '"' in app["app_id"]:
        raise ValueError("identifiant d'application invalide")
    subprocess.Popen([_explorer(), "shell:AppsFolder\\" + app["app_id"]])


def _registered_exe(name: str):
    """Resolve an exe through the App Paths registry (chrome, msedge, winword...)."""
    if not config.IS_WINDOWS:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows"
                                      rf"\CurrentVersion\App Paths\{name}.exe") as key:
                return winreg.QueryValueEx(key, None)[0].strip('"')
        except OSError:
            continue
    return None


def _placed(name: str, monitor: str | None, before: dict, launched: str):
    """Optionally move the freshly launched app to the requested screen."""
    if not monitor:
        return {"ok": True, "launched": launched}
    if not config.IS_WINDOWS:
        return {"ok": True, "launched": launched,
                "warning": "placement sur un écran précis : Windows uniquement"}
    mon = _pick_monitor(monitor)
    if not mon:
        return {"ok": True, "launched": launched,
                "warning": f"écran '{monitor}' introuvable, fenêtre laissée en place"}
    title = _place_app_window(name, before, mon)
    if title:
        return {"ok": True, "launched": launched, "monitor": monitor, "window": title}
    return {"ok": True, "launched": launched,
            "warning": "fenêtre non détectée, placement impossible"}


def open_target(name: str = "", url: str | None = None, monitor: str | None = None) -> dict:
    name = (name or "").strip()
    if url:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        before = _visible_windows() if monitor else {}
        if not webbrowser.open(url):
            return {"ok": False, "error": "Impossible d'ouvrir le navigateur."}
        # Match the browser window by the site's domain.
        domain = url.split("//", 1)[1].split("/", 1)[0].removeprefix("www.")
        return _placed(domain.split(".")[0], monitor, before, url)
    if not name:
        return {"ok": False, "error": "Nom d'application ou URL manquant."}
    if not config.IS_WINDOWS:
        return _launch_elsewhere(name)
    before = _visible_windows() if monitor else {}
    alias = APP_ALIASES.get(fold(name))
    if alias and alias.endswith(":"):  # a Windows page (Paramètres): the Explorer opens it
        subprocess.Popen([_explorer(), alias])
        return {"ok": True, "launched": name, "via": "uri"}
    try:
        # An alias names an exe: PATH first, before a shortcut that merely contains the word.
        exe = _find_exe(alias) if alias else None
        lnk = None if exe else _find_shortcut(name)
        if lnk:
            os.startfile(lnk)  # deliberate: local launcher
            return _placed(name, monitor, before, lnk.stem)
        # Then PATH and the App Paths registry (chrome, notepad...).
        exe = exe or _find_exe(name)
        if exe:
            subprocess.Popen([exe], cwd=str(Path(exe).parent))
            res = _placed(name, monitor, before, Path(exe).stem)
            res.setdefault("via", "exe")
            return res
        # Last, the Start menu's own list: Microsoft Store apps have no .lnk.
        app = match_start_app(name, start_apps())
        if app:
            _launch_start_app(app)
            res = _placed(app["name"], monitor, before, app["name"])
            res.setdefault("via", "menu Démarrer")
            return res
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": f"Application '{name}' introuvable sur ce PC."}


def _find_exe(name: str):
    return shutil.which(name) or shutil.which(name + ".exe") or _registered_exe(name)


def _launch_elsewhere(name: str) -> dict:
    """macOS / Linux: the platform's own launcher."""
    if config.IS_MAC:
        if subprocess.run(["open", "-a", name], capture_output=True).returncode == 0:
            return {"ok": True, "launched": name}
        return {"ok": False, "error": f"Application '{name}' introuvable sur ce Mac."}
    exe = shutil.which(name) or shutil.which(name.lower())
    if exe:
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {"ok": True, "launched": Path(exe).stem}
    if shutil.which("gtk-launch") and subprocess.run(
            ["gtk-launch", name.lower()], capture_output=True).returncode == 0:
        return {"ok": True, "launched": name}
    return {"ok": False, "error": f"Application '{name}' introuvable sur ce PC."}


# ---------------------------------------------------------------- instant system actions

# Windows virtual-key codes for the media keys.
VK = {"mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
      "next_track": 0xB0, "previous_track": 0xB1, "play_pause": 0xB3}


def _press(vk: int, times: int = 1):
    KEYEVENTF_KEYUP = 2
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _level(value) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        raise ValueError("niveau de volume invalide (0 à 100)") from None


def system_action(action: str, value=None) -> dict:
    if action == "read_clipboard":
        text = clipboard_read()
        return {"ok": True, "text": text[:4000], "truncated": len(text) > 4000}
    if action == "write_clipboard":
        clipboard_write(str(value or ""))
        return {"ok": True}
    if action == "save_screenshot":
        return {"ok": True, "path": str(save_screenshot(value or None))}
    if config.IS_WINDOWS:
        return _system_windows(action, value)
    if config.IS_MAC:
        return _system_mac(action, value)
    return _system_linux(action, value)


def _system_windows(action: str, value) -> dict:
    if action == "lock_screen":
        user32.LockWorkStation()
        return {"ok": True}
    if action == "set_volume":
        level = _level(value)
        _press(VK["volume_down"], 50)  # each key press moves 2 %: hit 0 first
        _press(VK["volume_up"], round(level / 2))
        return {"ok": True, "volume": level}
    if action in ("volume_up", "volume_down"):
        _press(VK[action], 5)  # +/- 10 %
        return {"ok": True}
    if action in VK:
        _press(VK[action])
        return {"ok": True}
    return {"ok": False, "error": f"Action inconnue : {action}"}


def _system_mac(action: str, value) -> dict:
    scripts = {
        "volume_up": "set volume output volume ((output volume of (get volume settings)) + 10)",
        "volume_down": "set volume output volume ((output volume of (get volume settings)) - 10)",
        "mute": "set volume output muted (not (output muted of (get volume settings)))",
    }
    if action == "set_volume":
        script = f"set volume output volume {_level(value)}"
    elif action in scripts:
        script = scripts[action]
    else:
        return {"ok": False, "error": f"'{action}' n'est pas disponible sur Mac."}
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
    return {"ok": True}


def _system_linux(action: str, value) -> dict:
    players = {"play_pause": "play-pause", "next_track": "next", "previous_track": "previous"}
    sink = ["pactl", "set-sink-volume", "@DEFAULT_SINK@"]
    if action in players:
        cmd = ["playerctl", players[action]]
    elif action == "volume_up":
        cmd = sink + ["+10%"]
    elif action == "volume_down":
        cmd = sink + ["-10%"]
    elif action == "set_volume":
        cmd = sink + [f"{_level(value)}%"]
    elif action == "mute":
        cmd = ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"]
    elif action == "lock_screen":
        cmd = ["loginctl", "lock-session"]
    else:
        return {"ok": False, "error": f"Action inconnue : {action}"}
    if not shutil.which(cmd[0]):
        return {"ok": False, "error": f"{cmd[0]} n'est pas installé sur ce PC."}
    subprocess.run(cmd, check=True, capture_output=True)
    return {"ok": True}


def _powershell(script: str, env: dict | None = None, timeout: float = 20) -> str:
    """Run a PowerShell snippet. -EncodedCommand side-steps every quoting issue,
    and UTF-8 output keeps the accents of app names and messages (the console's
    code page would mangle them)."""
    script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
        env={**os.environ, **(env or {})},
    )
    out = (r.stdout or b"").decode("utf-8", "replace")
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError((err or "PowerShell a échoué").strip()[:300])
    return out


def clipboard_read() -> str:
    if config.IS_WINDOWS:
        # Base64 keeps the console code page out of the way of accents.
        out = _powershell("$t = Get-Clipboard -Raw; if ($t) { "
                          "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($t)) }")
        return base64.b64decode(out.strip()).decode("utf-8", "replace")
    cmd = (["pbpaste"] if config.IS_MAC else
           ["wl-paste", "--no-newline"] if shutil.which("wl-paste") else
           ["xclip", "-selection", "clipboard", "-o"])
    if not shutil.which(cmd[0]):
        raise RuntimeError(f"{cmd[0]} n'est pas installé sur ce PC.")
    return subprocess.run(cmd, capture_output=True, check=True).stdout.decode("utf-8", "replace")


def clipboard_write(text: str):
    if config.IS_WINDOWS:
        # Passed through the environment: Unicode-safe, no quoting.
        _powershell("Set-Clipboard -Value $env:JARVIS_CLIPBOARD", env={"JARVIS_CLIPBOARD": text})
        return
    cmd = (["pbcopy"] if config.IS_MAC else
           ["wl-copy"] if shutil.which("wl-copy") else
           ["xclip", "-selection", "clipboard"])
    if not shutil.which(cmd[0]):
        raise RuntimeError(f"{cmd[0]} n'est pas installé sur ce PC.")
    subprocess.run(cmd, input=text.encode("utf-8"), check=True)


# ---------------------------------------------------------------- screenshots

def _grab(monitor: str | None):
    try:
        from PIL import ImageGrab
    except ImportError:
        raise RuntimeError("Pillow manquant : pip install -r requirements.txt") from None
    if not config.IS_WINDOWS:
        return ImageGrab.grab()
    target = (monitor or "primary").strip().lower()
    if target in ("all", "tous", "tout"):
        return ImageGrab.grab(all_screens=True)
    mon = _pick_monitor(target) or _pick_monitor("primary")
    return ImageGrab.grab(bbox=mon, all_screens=True)


def screenshot_jpeg(monitor: str | None = None, max_side: int = 1600) -> bytes:
    """A screen capture sized for the vision model (one monitor, ~1600 px)."""
    img = _grab(monitor).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return buf.getvalue()


FOLDERID_PICTURES = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"


def pictures_dir() -> Path:
    """Monsieur's Pictures folder: the known folder on Windows (it may have moved
    to OneDrive or another drive), ~/Pictures otherwise."""
    w = _win()
    if w and w.SHGetKnownFolderPath:
        path = ctypes.c_wchar_p()
        guid = GUID.from_buffer_copy(uuid.UUID(FOLDERID_PICTURES).bytes_le)
        try:
            if w.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0 and path.value:
                return Path(path.value)
        except OSError:
            logging.warning("JARVIS: dossier Images introuvable, ~/Pictures à la place")
        finally:
            if w.CoTaskMemFree:
                w.CoTaskMemFree(path)
    return Path.home() / "Pictures"


def save_screenshot(monitor: str | None = None) -> Path:
    folder = pictures_dir() / "JARVIS"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"capture-{datetime.now():%Y%m%d-%H%M%S}.png"
    _grab(monitor).save(path)
    return path


# ---------------------------------------------------------------- JARVIS's own window

APP_TITLE = "J.A.R.V.I.S."  # index.html's <title>: an --app window shows exactly that
# Top-level window classes of the browsers that can show the page in a tab.
BROWSER_CLASSES = ("Chrome_WidgetWin_", "MozillaWindowClass")


def _browser_candidates():
    """(chrome, edge) executables to try, most likely first."""
    if config.IS_WINDOWS:
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        chrome = [_registered_exe("chrome"),
                  *(Path(root) / "Google/Chrome/Application/chrome.exe" for root in (pf, pf86, local) if root)]
        edge = [_registered_exe("msedge"),
                *(Path(root) / "Microsoft/Edge/Application/msedge.exe" for root in (pf86, pf) if root)]
    elif config.IS_MAC:
        chrome = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
        edge = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        chrome = [shutil.which(n) for n in ("google-chrome", "google-chrome-stable",
                                            "chromium", "chromium-browser")]
        edge = [shutil.which(n) for n in ("microsoft-edge", "microsoft-edge-stable")]
    return chrome, edge


def _app_browser():
    """A Chromium browser able to open JARVIS as a standalone app window.

    config.BROWSER: auto (Chrome first: its on-device French recognition keeps
    the wake word local), chrome or edge; the other one if that isn't installed.
    """
    chrome, edge = _browser_candidates()
    wanted = (config.BROWSER or "auto").strip().lower()
    order = [("edge", edge), ("chrome", chrome)] if wanted == "edge" else [("chrome", chrome), ("edge", edge)]
    for kind, candidates in order:
        for c in candidates:
            if c and Path(c).is_file():
                if wanted in ("chrome", "edge") and kind != wanted:
                    logging.warning("JARVIS: %s introuvable, la fenêtre s'ouvre dans %s", wanted, kind)
                return str(c)
    return None


def _profile_dir(exe: str) -> Path:
    """One browser profile per browser: Chrome and Edge must never share one
    (a profile opened by the other browser gets migrated, or broken). Fixed
    folder names: nothing in a data path comes from outside."""
    stem = Path(exe).stem.lower()
    legacy = config.DATA_DIR / "app-window"  # before per-browser profiles, Edge came first
    if "edge" in stem:
        profile = config.DATA_DIR / "app-window-msedge"
        if legacy.is_dir() and not profile.exists():
            try:
                legacy.rename(profile)  # keeps the microphone permission already granted
            except OSError:
                pass  # in use or locked: a fresh profile then
    elif "chromium" in stem:
        profile = config.DATA_DIR / "app-window-chromium"
    else:
        profile = config.DATA_DIR / "app-window-chrome"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def open_app_window(url: str):
    """Open JARVIS in its own window (no tabs, no address bar).

    A dedicated browser profile keeps it separate from the everyday browser,
    remembers the microphone permission, and lets the autoplay flag apply so
    JARVIS can speak after a wake word without a click first. The disk cache
    is capped (100 MB): the profile lives in data/.
    """
    exe = _app_browser()
    if not exe:
        webbrowser.open(url)
        return
    subprocess.Popen(
        [exe, f"--app={url}", f"--user-data-dir={_profile_dir(exe)}", "--no-first-run",
         "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
         "--window-size=1440,900", "--disk-cache-size=104857600"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **({"creationflags": NO_WINDOW} if config.IS_WINDOWS else {"start_new_session": True}),
    )


def _window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = _call("GetClassNameW", hwnd, buf, 256)
    return buf.value if n else ""


def find_app_window():
    """JARVIS's window, or None: the --app window (its title is exactly the
    page's), else a browser window whose current tab is JARVIS."""
    best, best_rank = None, 0
    for hwnd, title in _visible_windows().items():
        title = (title or "").replace("\u200b", "").strip()  # Edge puts a zero-width space in its name
        if title == APP_TITLE:
            rank = 3
        elif APP_TITLE in title and _window_class(hwnd).startswith(BROWSER_CLASSES):
            rank = 2 if title.startswith(APP_TITLE) else 1
        else:
            continue
        if rank > best_rank:
            best, best_rank = hwnd, rank
    return best


def _bring_to_front(hwnd) -> bool:
    """Restore and focus a window. Windows only lets a process take the
    foreground in some cases (it received the last input: a hotkey, a click on
    the tray icon, a double-click on JARVIS.bat); otherwise the taskbar button
    flashes until monsieur clicks it. Returns whether it is in front."""
    SW_RESTORE = 9
    if _call("IsIconic", hwnd):
        _call("ShowWindow", hwnd, SW_RESTORE)
    done = bool(_call("SetForegroundWindow", hwnd))
    if done or _call("GetForegroundWindow") == hwnd:
        return True
    flash_app_window(hwnd)
    return False


def show_app_window(url: str) -> str:
    """Bring JARVIS's window to the front (a second launch, the hotkey, the tray
    icon, a reminder) instead of opening another one. Returns 'focused',
    'flashed' (Windows refused the focus: the taskbar button blinks) or 'opened'."""
    if config.IS_WINDOWS:
        hwnd = find_app_window()
        if hwnd:
            return "focused" if _bring_to_front(hwnd) else "flashed"
    elif not config.IS_MAC and shutil.which("wmctrl"):
        # X11 desktops: wmctrl activates a window by (part of) its title.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            if subprocess.run(["wmctrl", "-a", APP_TITLE], capture_output=True, timeout=5).returncode == 0:
                return "focused"
    open_app_window(url)
    return "opened"


def autostart_enabled() -> bool:
    """Is JARVIS in the Windows Startup folder? (the tray's ✓)"""
    appdata = os.environ.get("APPDATA")
    return bool(config.IS_WINDOWS and appdata and
                (Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup/JARVIS.lnk").exists())


# ---------------------------------------------------------------- attention & notifications

# SHQueryUserNotificationState (QUERY_USER_NOTIFICATION_STATE). 6, « quiet
# time », is only the first hour after a new account's first sign-in (not
# Focus / Ne pas déranger, which Windows doesn't expose here).
ATTENTION = {1: "not_present", 2: "busy", 3: "fullscreen", 4: "presentation",
             5: "ok", 6: "quiet_time", 7: "app"}


def attention_state() -> str:
    """'ok', or why monsieur shouldn't be interrupted: 'not_present' (locked,
    screen saver), 'busy' (a full-screen app), 'fullscreen' (a Direct3D game),
    'presentation', 'quiet_time', 'app' (a full-screen Store app). Elsewhere
    than Windows, or when Windows doesn't say: 'ok'."""
    w = _win()
    if not w or not w.SHQueryUserNotificationState:
        return "ok"
    value = ctypes.c_int(0)
    try:
        if w.SHQueryUserNotificationState(ctypes.byref(value)) != 0:  # an HRESULT: 0 is S_OK
            return "ok"
    except OSError:
        return "ok"
    state = ATTENTION.get(value.value, "ok")
    if state in ("busy", "fullscreen") and _jarvis_in_front():
        return "ok"  # JARVIS's own window in full screen (F11) must not silence JARVIS
    return state


def _jarvis_in_front() -> bool:
    front = _call("GetForegroundWindow")
    return bool(front) and front == find_app_window()


def keep_awake() -> bool:
    """Keep the PC from going to sleep for now: ES_SYSTEM_REQUIRED without
    ES_CONTINUOUS only resets the idle timer, so the caller repeats it (every
    30 s while a task runs) and nothing is left to undo when it stops.
    The screen may still turn off. Returns whether Windows accepted it."""
    ES_SYSTEM_REQUIRED = 0x1
    return bool(_call("SetThreadExecutionState", ES_SYSTEM_REQUIRED))


def flash_app_window(hwnd=None) -> bool:
    """Flash JARVIS's taskbar button until monsieur comes to it, without
    stealing the focus. Nothing to do when the window is already in front."""
    FLASHW_TRAY, FLASHW_TIMERNOFG = 0x2, 0xC
    w = _win()
    if not w or not w.FlashWindowEx:
        return False
    hwnd = hwnd or find_app_window()
    if not hwnd or _call("GetForegroundWindow") == hwnd:
        return False
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_TRAY | FLASHW_TIMERNOFG, 0, 0)
    w.FlashWindowEx(ctypes.byref(info))
    return True


def fit_line(text, limit: int) -> str:
    """One clean line of at most limit UTF-16 units (Windows' fixed buffers)."""
    text = " ".join(str(text or "").split())
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    while text and len(text.encode("utf-16-le")) // 2 > limit - 1:
        text = text[:-1]
    return text.rstrip() + "…"


# Windows' own toast, when the tray icon isn't there to show one. The texts
# go through the environment and become XML text nodes: never markup, never code.
_TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] | Out-Null;"
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$t = $x.GetElementsByTagName('text');"
    "$t.Item(0).AppendChild($x.CreateTextNode($env:JARVIS_TOAST_TITLE)) | Out-Null;"
    "$t.Item(1).AppendChild($x.CreateTextNode($env:JARVIS_TOAST_BODY)) | Out-Null;"
    "$n = [Windows.UI.Notifications.ToastNotification]::new($x);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n)"
)


def _toast_powershell(title: str, body: str):
    try:
        _powershell(_TOAST_PS, env={"JARVIS_TOAST_TITLE": title, "JARVIS_TOAST_BODY": body}, timeout=15)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        logging.warning("JARVIS: notification Windows impossible : %s", exc)


def toast(title: str, body: str) -> bool:
    """A native notification, for when no JARVIS page is open (inbox.py): through
    the tray icon, else Windows' own toast; notify-send or osascript elsewhere.
    Only while the app runs (shell.start): a test or a command-line call never
    pops one up. Returns whether one was sent."""
    from . import shell  # late: shell imports this module
    title, body = fit_line(title, 63) or "JARVIS", fit_line(body, 255)
    if shell.notify(title, body):
        return True
    if not shell.running():
        return False
    if config.IS_WINDOWS:
        # PowerShell takes a second or two: not on the publisher's thread.
        threading.Thread(target=_toast_powershell, args=(title, body), daemon=True,
                         name="jarvis-toast").start()
        return True
    title, body = title.lstrip("-") or "JARVIS", body.lstrip("-")  # never read as an option
    if config.IS_MAC and shutil.which("osascript"):
        cmd = ["osascript", "-e", "on run argv", "-e",
               "display notification (item 2 of argv) with title (item 1 of argv)",
               "-e", "end run", title, body]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "--app-name=JARVIS", "--", title, body]
    else:
        return False
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def reveal_in_explorer(path) -> list:
    """Show a file (one Claude wrote, say) selected in its folder: Explorer,
    Finder or the file manager. Never opened nor run, so never os.startfile:
    a .bat or a .lnk must not start. Returns the command it ran."""
    raw = str(path or "")
    if not raw or "\0" in raw or '"' in raw or not os.path.isabs(raw):
        raise ValueError("chemin de fichier invalide")
    target = os.path.normpath(raw)
    if not os.path.exists(target):
        raise FileNotFoundError(f"Le fichier n'existe plus : {os.path.basename(target)}")
    if config.IS_WINDOWS:
        # One argument: /select, then the quoted path (spaces, commas).
        cmd = [_explorer(), f'/select,"{target}"']
        subprocess.Popen(subprocess.list2cmdline(cmd[:1]) + " " + cmd[1])
        return cmd
    if config.IS_MAC:
        cmd = ["open", "-R", target]
    elif shutil.which("xdg-open"):
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        cmd = ["xdg-open", folder]  # the folder, not the file: a file would be opened
    else:
        raise NotImplementedError("Afficher dans l'explorateur : aucun gestionnaire de fichiers sur ce PC.")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cmd


def set_autostart(enabled: bool) -> str:
    """Add (or remove) a JARVIS shortcut in the Windows Startup folder."""
    if not config.IS_WINDOWS:
        raise RuntimeError("Le démarrage automatique n'est géré que sous Windows.")
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    lnk = startup / "JARVIS.lnk"
    if not enabled:
        lnk.unlink(missing_ok=True)
        return "JARVIS ne se lancera plus au démarrage de Windows."
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = pythonw if pythonw.exists() else Path(sys.executable)
    server = config.ROOT / "server.py"

    def q(s):  # PowerShell single-quoted string
        return "'" + str(s).replace("'", "''") + "'"

    arguments = f'"{server}" --app'
    _powershell(
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({q(lnk)}); "
        f"$s.TargetPath = {q(target)}; $s.Arguments = {q(arguments)}; "
        f"$s.WorkingDirectory = {q(config.ROOT)}; $s.Description = 'JARVIS Local'; $s.Save()"
    )
    return f"JARVIS se lancera au démarrage de Windows ({lnk})."
