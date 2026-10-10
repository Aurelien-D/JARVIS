"""Tailscale helper: detection, whois, the Serve status and « Publier sur Tailscale ».

- Tailscale is run only as a list of fixed arguments (or validated values),
  never through a shell, never by string concatenation. On Windows the
  installed tailscale.exe is used (Program Files first); a tailscale.cmd or
  .bat found on a user-writable PATH folder is never run.
- « Publier sur Tailscale » runs exactly one command, the one the README shows:
  tailscale serve --bg --https=443 http://127.0.0.1:<REMOTE_PORT>. Never Funnel,
  never a TCP forward, one process at a time. When Tailscale asks for consent
  (HTTPS not enabled on the tailnet yet), only a login.tailscale.com link is
  ever shown.
- serve_status() reads what Tailscale publishes and flags what would be
  dangerous: Funnel (the internet), a raw TCP forward (no proxy header: aimed
  at the PC port it would hand out the PC page), or another target.
- A watch thread re-checks that every 10 minutes and alerts the PC (and the
  iPhone through ntfy), whether remote access is on or off.

publish() and unpublish() never raise: remote.set_enabled calls them best effort.
"""
import ipaddress
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, HTTPException, Request

from . import config

router = APIRouter()
log = logging.getLogger(__name__)

RUN = None  # tests replace it: fn(args: list[str], timeout: float) -> subprocess.CompletedProcess

WINDOWS_PATHS = (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe")
MAC_PATH = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
# The PowerShell form of the manual command names this literal path, never a detected one.
POWERSHELL_EXE = WINDOWS_PATHS[0]
CONSENT_RE = re.compile(r"^https://login\.tailscale\.com/[\w\-/?=&.%]+$")
TAILNET_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
DANGEROUS = ("funnel", "tcp", "wrong_target")

SELF_TTL = 30          # status --json: the guard asks for the PC's own addresses on every request
WHOIS_TTL = 300
WHOIS_TIMEOUT = 5
STATUS_TIMEOUT = 10
PUBLISH_WAIT = 20      # how long « Publier » reads Tailscale's answer
PUBLISH_KILL = 600     # a publish left waiting for consent is stopped after 10 minutes
WATCH_FIRST = 30
WATCH_EVERY = 600

DETAILS = {
    "ready": "Tailscale publie JARVIS sur votre réseau Tailscale seulement, vers le port {port} de ce PC.",
    "absent": "JARVIS n'est pas publié sur Tailscale : cliquez Publier sur Tailscale.",
    "funnel": "Funnel actif : Tailscale publie ce PC sur internet. Tapez tailscale serve reset dans "
              "PowerShell, puis cliquez Publier sur Tailscale.",
    "tcp": "Relais TCP actif : Tailscale transmet des connexions brutes à ce PC, sans contrôle. Tapez "
           "tailscale serve reset dans PowerShell, puis cliquez Publier sur Tailscale.",
    "wrong_target": "Cible inattendue : Tailscale publie autre chose que JARVIS (port {port}). Tapez "
                    "tailscale serve reset dans PowerShell, puis cliquez Publier sur Tailscale.",
    "stopped": "Tailscale est arrêté ou déconnecté sur ce PC : connectez-le (icône près de l'horloge).",
    "no_tailscale": "Tailscale n'est pas installé sur ce PC.",
    "unknown": "La configuration de Tailscale Serve n'a pas pu être lue : réessayez.",
}
MISCONFIG_TEXT = ("Tailscale publie JARVIS d'une façon dangereuse (Funnel ou relais TCP) : "
                  "ouvrez Réglages › Accès à distance.")
PC_LOGIN_TEXT = "Le compte Tailscale du PC a changé : vérifiez Réglages › Accès à distance."

_lock = threading.Lock()          # caches
_cache: dict = {}                 # "self" -> (at, info); ("whois", ip) -> (at, info)
_proc_lock = threading.Lock()     # one publish process at a time
_proc: dict = {"process": None, "timer": None}
_watch_lock = threading.Lock()
_watch: dict = {"thread": None, "stop": None}

# ---------------------------------------------------------------- running tailscale


def _exists(path: str) -> bool:
    return os.path.isfile(path)


def exe_path() -> str | None:
    """The tailscale executable, or None. Windows: Program Files first, then PATH
    only for a real .exe (a .cmd or .bat there could be anyone's)."""
    if config.IS_WINDOWS:
        for path in WINDOWS_PATHS:
            if _exists(path):
                return path
        found = shutil.which("tailscale")
        return found if found and found.lower().endswith(".exe") else None
    if sys.platform == "darwin" and _exists(MAC_PATH):
        return MAC_PATH
    return shutil.which("tailscale")


def _no_window() -> dict:
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if config.IS_WINDOWS else {}


def _run(args: list, timeout: float):
    """tailscale <args>, an argument list, no shell, no console window, no stdin."""
    if RUN is not None:
        return RUN(list(args), timeout)
    exe = exe_path()
    if not exe:
        raise FileNotFoundError("tailscale")
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL, shell=False, check=False, **_no_window())


def _popen(exe: str, args: list):
    """The publish process: the same flags as _run, its output read line by line."""
    return subprocess.Popen([exe, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                            bufsize=1, shell=False, **_no_window())


def _json(text) -> dict:
    data = json.loads(text or "{}")
    return data if isinstance(data, dict) else {}


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _cached(key, ttl: float):
    with _lock:
        hit = _cache.get(key)
    return dict(hit[1]) if hit and time.monotonic() - hit[0] < ttl else None


def _remember(key, info: dict) -> dict:
    with _lock:
        _cache[key] = (time.monotonic(), dict(info))
    return info


def _forget(key) -> None:
    with _lock:
        _cache.pop(key, None)


def _port() -> int:
    port = int(config.REMOTE_PORT)
    if not 0 < port < 65536:
        raise ValueError("JARVIS_REMOTE_PORT invalide")
    return port


def _serve_args() -> list:
    """The one Serve command JARVIS runs: HTTPS on the tailnet only, to the Serve port."""
    return ["serve", "--bg", "--https=443", f"http://127.0.0.1:{_port()}"]

# ---------------------------------------------------------------- reading tailscale


def self_info() -> dict:
    """This PC on the tailnet: {installed, running, dns_name, login, tailnet,
    magicdns, https, ip, ips}; cached 30 s."""
    hit = _cached("self", SELF_TTL)
    if hit is not None:
        return hit
    exe = exe_path()
    info = {"installed": exe is not None, "running": False, "ips": []}
    if exe is None:
        return _remember("self", info)
    try:
        data = _json(_run(["status", "--json"], STATUS_TIMEOUT).stdout)
        me = _dict(data.get("Self"))
        user = _dict(_dict(data.get("User")).get(str(me.get("UserID"))))
        tailnet = _dict(data.get("CurrentTailnet"))
        ips = [str(a) for a in (me.get("TailscaleIPs") or []) if isinstance(a, str)]
        info = {"installed": True, "running": data.get("BackendState") == "Running",
                "dns_name": str(me.get("DNSName") or "").rstrip(".").lower(),
                "login": str(user.get("LoginName") or "").strip().lower(),
                "tailnet": str(tailnet.get("MagicDNSSuffix") or "").rstrip(".").lower(),
                "magicdns": bool(tailnet.get("MagicDNSEnabled")), "https": bool(data.get("CertDomains")),
                "ip": ips[0] if ips else "", "ips": ips}
    except Exception:  # noqa: BLE001 - not running, not logged in, an older CLI: just not usable
        log.info("JARVIS: état de Tailscale illisible")
    return _remember("self", info)


def _tailnet_ip(ip) -> str:
    """The address in canonical form when it is a tailnet one, else ''."""
    try:
        addr = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return ""
    return str(addr) if any(addr in net for net in TAILNET_NETS) else ""


def whois(ip: str) -> dict:
    """The tailnet node behind an address: {node_id, addresses, host_name, os,
    login}, or {}; cached 5 minutes per address, 5 s at most."""
    addr = _tailnet_ip(ip)
    if not addr:
        return {}
    hit = _cached(("whois", addr), WHOIS_TTL)
    if hit is not None:
        return hit
    if exe_path() is None:
        return {}
    try:
        data = _json(_run(["whois", "--json", addr], WHOIS_TIMEOUT).stdout)
    except Exception:  # noqa: BLE001 - unknown address, timeout: no whois
        return {}
    node = _dict(data.get("Node"))
    if not node:
        return {}
    hostinfo, profile = _dict(node.get("Hostinfo")), _dict(data.get("UserProfile"))
    info = {"node_id": str(node.get("StableID") or node.get("ID") or ""),
            "addresses": [a.split("/")[0] for a in (node.get("Addresses") or []) if isinstance(a, str)],
            "host_name": str(node.get("ComputedName") or hostinfo.get("Hostname") or ""),
            "os": str(hostinfo.get("OS") or ""),
            "login": str(profile.get("LoginName") or "").strip().lower()}
    return _remember(("whois", addr), info)


def _configs(data: dict) -> list:
    """The Serve config and the ones nested in it (foreground sessions, services):
    a danger in any of them counts."""
    out, todo = [], [data]
    while todo:
        conf = todo.pop()
        if not isinstance(conf, dict):
            continue
        out.append(conf)
        for key in ("Foreground", "Services"):
            nested = conf.get(key)
            if isinstance(nested, dict):
                todo.extend(nested.values())
    return out


def _status(state: str, url: str = "") -> dict:
    return {"state": state, "detail": DETAILS[state].format(port=config.REMOTE_PORT), "url": url}


def serve_status() -> dict:
    """What Tailscale Serve publishes: {state, detail, url}. state, checked in
    this order: no_tailscale, stopped, funnel, tcp, absent, wrong_target, ready
    (unknown when the config cannot be read)."""
    if exe_path() is None:
        return _status("no_tailscale")
    info = self_info()
    if not info.get("running"):
        return _status("stopped")
    dns = str(info.get("dns_name") or "")
    url = f"https://{dns}/" if dns else ""
    try:
        data = _json(_run(["serve", "status", "--json"], STATUS_TIMEOUT).stdout)
    except Exception:  # noqa: BLE001 - an older CLI, no rights, a timeout
        return _status("unknown", url)
    configs = _configs(data)
    if any(any(_dict(c.get("AllowFunnel")).values()) for c in configs):
        return _status("funnel", url)
    for conf in configs:
        if any(_dict(h).get("TCPForward") or _dict(h).get("TerminateTLS") for h in _dict(conf.get("TCP")).values()):
            return _status("tcp", url)
    webs = {}
    for conf in configs:
        webs.update(_dict(conf.get("Web")))
    ours = next((k for k in webs if (k == f"{dns}:443" if dns else str(k).endswith(":443"))), None)
    if ours is None:
        return _status("absent", url)
    handlers = _dict(webs[ours]).get("Handlers")
    expected = {"/": {"Proxy": f"http://127.0.0.1:{config.REMOTE_PORT}"}}
    if len(webs) != 1 or handlers != expected:
        return _status("wrong_target", url)
    return _status("ready", url)

# ---------------------------------------------------------------- publishing


def _failed(state: str, error: str) -> dict:
    return {"ok": False, "state": state, "consent_url": "", "error": error}


def _said(line: str) -> str:
    """Tailscale's last line for an error, at most 200 characters, with no link
    in it: only a login.tailscale.com consent link is ever shown."""
    return re.sub(r"\w+://\S+", "(lien retiré)", line)[:200]


def _pump(stream, lines: queue.Queue) -> None:
    """Reads the publish process's output to its end (also after publish()
    returned: a full pipe would block tailscale)."""
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


def _stop(process) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass


def publish() -> dict:
    """Runs the Serve command (_serve_args). {ok, state, consent_url, error}:
    ready, consent (Tailscale waits for HTTPS to be allowed on the tailnet: the
    process stays in the background, stopped after 10 minutes), busy, error."""
    exe = exe_path()
    if exe is None:
        return _failed("no_tailscale", DETAILS["no_tailscale"])
    with _proc_lock:
        running = _proc["process"]
        if running is not None and running.poll() is None:
            return _failed("busy", "Une publication est déjà en cours : patientez un instant.")
        try:
            process = _popen(exe, _serve_args())
        except (OSError, ValueError, subprocess.SubprocessError):
            log.exception("JARVIS: publication Tailscale impossible")
            return _failed("error", "Tailscale n'a pas pu être lancé.")
        _proc["process"] = process
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_pump, args=(process.stdout, lines), name="jarvis-tailscale-publish",
                     daemon=True).start()
    deadline = time.monotonic() + PUBLISH_WAIT
    last = ""
    ended = False
    while (left := deadline - time.monotonic()) > 0:
        try:
            line = lines.get(timeout=min(left, 0.5))
        except queue.Empty:
            continue
        if line is None:
            ended = True
            break
        text = line.strip()
        if not text:
            continue
        last = text
        consent = next((w for w in text.split() if CONSENT_RE.fullmatch(w)), "")
        if consent:
            timer = threading.Timer(PUBLISH_KILL, _stop, args=(process,))
            timer.daemon = True
            timer.start()
            with _proc_lock:
                _proc["timer"] = timer
            return {"ok": False, "state": "consent", "consent_url": consent, "error": ""}
    if not ended:
        _stop(process)
        return _failed("error", _said(last or "Tailscale n'a pas répondu à temps."))
    try:
        code = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _stop(process)
        code = None
    if code == 0:
        _forget("self")
        status = serve_status()
        ok = status["state"] == "ready"
        return {"ok": ok, "state": status["state"], "consent_url": "", "error": "" if ok else status["detail"]}
    return _failed("error", _said(last or "Tailscale a refusé la publication."))


def unpublish() -> dict:
    """Withdraws JARVIS's HTTPS publication (serve --https=443 off): {ok, state, error}."""
    if exe_path() is None:
        return {"ok": False, "state": "no_tailscale", "error": DETAILS["no_tailscale"]}
    try:
        r = _run(["serve", "--https=443", "off"], STATUS_TIMEOUT)
    except Exception:  # noqa: BLE001 - never raises: set_enabled calls it best effort
        return {"ok": False, "state": "error", "error": "Tailscale n'a pas pu être lancé."}
    if r.returncode != 0:
        out = [x.strip() for x in f"{r.stdout or ''}\n{r.stderr or ''}".splitlines() if x.strip()]
        return {"ok": False, "state": "error", "error": _said(out[-1] if out else "Tailscale a refusé.")}
    return {"ok": True, "state": serve_status()["state"], "error": ""}


def manual_command(full_path: bool = False) -> str:
    """The Serve command shown with [Copier]; full_path: PowerShell with the literal install path."""
    args = f"serve --bg --https=443 http://127.0.0.1:{config.REMOTE_PORT}"
    return f'& "{POWERSHELL_EXE}" {args}' if full_path else f"tailscale {args}"

# ---------------------------------------------------------------- the watch


def _watch_once() -> None:
    """One check: a dangerous Serve config (remote access on or off), and the PC's
    Tailscale account no longer the allowed one while remote access is on."""
    if exe_path() is None:
        return
    from . import audit, remote
    if serve_status()["state"] in DANGEROUS:
        audit.alert("serve_misconfig", MISCONFIG_TEXT)
    if remote.is_enabled():
        allowed = remote.logins()
        login = self_info().get("login") or ""
        if allowed and login and login not in allowed:
            audit.alert("pc_login_change", PC_LOGIN_TEXT)


def _watch_loop(stop: threading.Event) -> None:
    if stop.wait(WATCH_FIRST):
        return
    while True:
        try:
            _watch_once()
        except Exception:  # the next round must still run
            log.exception("JARVIS: vérification de Tailscale Serve impossible")
        if stop.wait(WATCH_EVERY):
            return


def start_watch() -> None:
    """The periodic Serve check: 30 s after the start, then every 10 minutes."""
    with _watch_lock:
        thread = _watch["thread"]
        if thread is not None and thread.is_alive():
            return
        stop = threading.Event()
        thread = threading.Thread(target=_watch_loop, args=(stop,), name="jarvis-tailscale-watch", daemon=True)
        _watch.update(thread=thread, stop=stop)
        thread.start()


def stop_watch() -> None:
    """Stops the periodic Serve check."""
    with _watch_lock:
        thread, stop = _watch["thread"], _watch["stop"]
        _watch.update(thread=None, stop=None)
    if stop is not None:
        stop.set()
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(2)


def reset_memory() -> None:
    """Tests: forget the caches and the running publish process."""
    with _lock:
        _cache.clear()
    with _proc_lock:
        process, timer = _proc["process"], _proc["timer"]
        _proc.update(process=None, timer=None)
    if timer is not None:
        timer.cancel()
    if process is not None:
        _stop(process)

# ---------------------------------------------------------------- routes (PC only)


def _pc(request: Request) -> None:
    # remote is imported inside functions everywhere (section 0)
    from . import remote
    if remote.caller_of(request).remote:
        raise HTTPException(403, "Réservé au PC.")


@router.get("/api/remote/serve")
def get_serve(request: Request):
    _pc(request)
    _forget("self")  # Revérifier: Tailscale may have just been started
    return {**serve_status(), "command": manual_command(), "command_full": manual_command(True)}


@router.post("/api/remote/serve/publish")
def post_publish(request: Request):
    from . import remote
    _pc(request)
    if not remote.is_enabled():
        raise HTTPException(409, "Activez d'abord l'accès à distance (Réglages › Accès à distance).")
    result = publish()
    if result.get("state") in ("ready", "consent"):
        remote.note_published(True)
    return result


@router.post("/api/remote/serve/unpublish")
def post_unpublish(request: Request):
    from . import remote
    _pc(request)
    result = unpublish()
    remote.note_published(False)  # monsieur withdrew it: switching on must not bring it back
    return result
