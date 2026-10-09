"""Everything that touches the desktop: launching apps and sites, placing
windows on a monitor, instant system actions (volume, media keys, lock,
clipboard), screenshots, and JARVIS's own app window and autostart.

Window placement and the Start Menu lookup use the Windows API; on macOS and
Linux the same functions fall back to the platform's own tools, or say the
action isn't available there.
"""
import base64
import contextlib
import io
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from . import config

NO_WINDOW = subprocess.CREATE_NO_WINDOW if config.IS_WINDOWS else 0

if config.IS_WINDOWS:
    import ctypes
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


def _find_shortcut(name: str):
    """Fuzzy-match a Start Menu shortcut (where installed apps register)."""
    q = name.lower().strip()
    best, best_score = None, 0.0
    for root in START_MENU_DIRS:
        if not root.is_dir():
            continue
        for lnk in root.rglob("*.lnk"):
            stem = lnk.stem.lower()
            if q == stem:
                return lnk
            score = 0.0
            if q in stem:
                score = 2 + len(q) / len(stem)   # substring: prefer tightest match
            elif all(w in stem for w in q.split()):
                score = 1
            # Penalise uninstallers and docs.
            if any(bad in stem for bad in ("uninstall", "désinstaller", "readme", "website")):
                score -= 2
            if score > best_score:
                best, best_score = lnk, score
    return best


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
    lnk = _find_shortcut(name)
    if lnk:
        os.startfile(lnk)  # deliberate: local launcher
        return _placed(name, monitor, before, lnk.stem)
    # Fallback: resolve via PATH then the App Paths registry (chrome, notepad...).
    exe = shutil.which(name) or shutil.which(name + ".exe") or _registered_exe(name)
    if not exe:
        return {"ok": False, "error": f"Application '{name}' introuvable sur ce PC."}
    try:
        subprocess.Popen([exe], cwd=str(Path(exe).parent))
        res = _placed(name, monitor, before, Path(exe).stem)
        res.setdefault("via", "exe")
        return res
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


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


def _powershell(script: str, env: dict | None = None) -> str:
    """Run a PowerShell snippet. -EncodedCommand side-steps every quoting issue."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW,
        env={**os.environ, **(env or {})},
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "PowerShell a échoué").strip()[:300])
    return r.stdout


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


def save_screenshot(monitor: str | None = None) -> Path:
    folder = Path.home() / "Pictures" / "JARVIS"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"capture-{datetime.now():%Y%m%d-%H%M%S}.png"
    _grab(monitor).save(path)
    return path


# ---------------------------------------------------------------- JARVIS's own window

def _app_browser():
    """A Chromium browser able to open JARVIS as a standalone app window."""
    if config.IS_WINDOWS:
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates = [
            _registered_exe("msedge"), _registered_exe("chrome"),
            Path(pf86) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif config.IS_MAC:
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                      "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        candidates = [shutil.which(n) for n in
                      ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    return None


def open_app_window(url: str):
    """Open JARVIS in its own window (no tabs, no address bar).

    A dedicated browser profile keeps it separate from the everyday browser,
    remembers the microphone permission, and lets the autoplay flag apply so
    JARVIS can speak after a wake word without a click first.
    """
    exe = _app_browser()
    if not exe:
        webbrowser.open(url)
        return
    profile = config.DATA_DIR / "app-window"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
         "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
         "--window-size=1440,900"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **({"creationflags": NO_WINDOW} if config.IS_WINDOWS else {"start_new_session": True}),
    )


def show_app_window(url: str):
    """Bring JARVIS's window to the front (a second launch, a reminder).

    For now it opens a window like open_app_window; WP13 focuses the
    existing one instead of opening another.
    """
    open_app_window(url)


# ---------------------------------------------------------------- attention & notifications (stubs, WP13)

def attention_state() -> str:
    """'ok', or why monsieur shouldn't be interrupted (fullscreen, presentation...)."""
    return "ok"


def toast(title: str, body: str):
    """Native Windows notification, for when no JARVIS page is open."""


def keep_awake():
    """Keep the PC from sleeping while a task runs."""


def flash_app_window():
    """Flash JARVIS's taskbar button to draw attention without stealing focus."""


def reveal_in_explorer(path):
    """Show a file Claude created in the Explorer (never run it)."""
    raise NotImplementedError("Afficher dans l'explorateur : pas encore disponible.")


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
