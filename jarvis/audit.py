"""Remote access audit trail and security alerts.

- data/remote-audit.jsonl: one JSON line per remote request or action. Only the
  fields of FIELDS are ever written (a reason code, a route template, a short
  title), so no cookie, token, secret, prompt or URL can reach the file.
  Refusal lines are throttled per IP (a flood leaves a count, not 10 000 lines).
- data/remote-alerts.jsonl: every alert line again, so a flood of refused
  requests can never push an alert out of the record.
- Each line is written open-append-close under one lock, and rotation happens
  under that same lock: Windows cannot rename a file another thread holds open.
- alert(): the audit line always; then, unless the same alert just went out, a
  PC toast, a PC warning card and the hooks (ntfy). Hooks get the kind's fixed
  sentence, never the PC text: no device name, IP or login leaves the PC.

Nothing here ever raises: an audit failure must never break a request.
"""
import json
import logging
import math
import threading
import time
from collections import deque

from . import config, desktop, events

log = logging.getLogger(__name__)

# fn(kind, ntfy_text) for alerts whose ALERTS[kind]["ntfy"] is True. Hooks get
# the kind's fixed sentence, never the PC text (no name, IP or login leaves the PC).
ALERT_HOOKS: list = []

TITLE = "JARVIS · sécurité"


def _kind(toast: bool, ntfy: bool, dedupe_s: int, ntfy_text: str = "") -> dict:
    return {"title": TITLE, "toast": toast, "ntfy": ntfy, "dedupe_s": dedupe_s, "ntfy_text": ntfy_text}


# kind -> {"title", "toast", "ntfy", "dedupe_s", "ntfy_text"} (spec 4.9)
ALERTS: dict = {
    "pair_request": _kind(True, False, 0),
    "new_device": _kind(True, True, 0, "nouvel appareil associé."),
    "funnel": _kind(True, True, 600, "requête venue d'internet refusée (Funnel) : vérifiez Tailscale."),
    "auth_failures": _kind(True, True, 600, "échecs répétés d'authentification : accès bloqué 15 minutes."),
    "remote_complet": _kind(True, True, 0, "tâche avec accès complet lancée depuis l'iPhone."),
    "login_change": _kind(True, True, 600, "compte Tailscale inattendu pour un appareil associé : refusé."),
    "ip_change": _kind(True, True, 600, "appareil associé utilisé depuis une autre machine : refusé."),
    "secret_copied": _kind(True, True, 0, "secret d'appareil utilisé depuis une autre machine : appareil retiré."),
    "siri_key": _kind(True, False, 0),
    "remote_paused": _kind(True, False, 0),
    "remote_on": _kind(True, True, 0, "accès à distance activé sur le PC."),
    "remote_off": _kind(True, True, 0, "accès à distance coupé sur le PC."),
    "complet_optin": _kind(True, True, 0, "accès complet depuis l'iPhone autorisé sur le PC."),
    "listener_error": _kind(True, False, 600),
    "serve_misconfig": _kind(True, True, 3600,
                             "Tailscale publie JARVIS d'une façon dangereuse : vérifiez Réglages › Accès à distance."),
    "pc_login_change": _kind(True, True, 3600, "le compte Tailscale du PC a changé."),
}

AUDIT_MAX_BYTES = 5 * 1024 * 1024  # remote-audit.jsonl beyond this becomes remote-audit.1.jsonl
ALERTS_MAX_LINES = 500             # remote-alerts.jsonl reaching this becomes remote-alerts.1.jsonl
THROTTLE_WINDOW_S = 600
THROTTLE_MAX = 20                  # refusal lines per IP per window, then one count line

# The only keys a line may hold, with the longest text each may carry.
FIELDS = {"kind": 16, "caller": 16, "device": 24, "key": 24, "ip": 64, "login": 200, "method": 10,
          "route": 120, "reason": 24, "tool": 64, "profile": 24, "title": 80, "pending": 32,
          "decision": 16, "alert": 32, "text": 160}
NUMBERS = ("status", "count")

_lock = threading.Lock()       # the two files: one write or rotation at a time
_mem_lock = threading.Lock()   # dedupe and throttle windows
_last_alert: dict = {}         # (kind, device or ip) -> when it last reached the PC
_refusals: dict = {}           # ip -> deque of refusal line times
_suppressed: dict = {}         # ip -> refusal lines left out since the last one written


def _now() -> float:
    return time.time()


def _audit_paths():
    return config.DATA_DIR / "remote-audit.jsonl", config.DATA_DIR / "remote-audit.1.jsonl"


def _alert_paths():
    return config.DATA_DIR / "remote-alerts.jsonl", config.DATA_DIR / "remote-alerts.1.jsonl"


def _line(kind: str, caller, fields: dict) -> dict:
    """The line as written: known keys only, texts cut, numbers finite."""
    raw = {"kind": kind}
    if caller is not None:
        raw.update(caller=getattr(caller, "kind", ""), device=getattr(caller, "device_id", ""),
                   key=getattr(caller, "key_id", ""), ip=getattr(caller, "ip", ""),
                   login=getattr(caller, "login", ""))
    raw.update(fields)
    line = {"t": round(_now(), 3)}
    for key, limit in FIELDS.items():
        value = raw.get(key)
        if isinstance(value, str) and value:
            line[key] = value.replace("\n", " ").replace("\r", " ")[:limit]
    for key in NUMBERS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            line[key] = int(value)
    return line


def _append(path, old, line: dict, *, max_bytes: int = 0, max_lines: int = 0) -> None:
    """One line, open-append-close; rotated first when full. Caller holds _lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        full = (max_bytes and path.stat().st_size > max_bytes) or \
            (max_lines and path.read_bytes().count(b"\n") >= max_lines)
    except FileNotFoundError:
        full = False
    if full:
        path.replace(old)  # one old file kept
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _write(line: dict, alert: bool = False) -> None:
    with _lock:
        path, old = _audit_paths()
        _append(path, old, line, max_bytes=AUDIT_MAX_BYTES)
        if alert:
            path, old = _alert_paths()
            _append(path, old, line, max_lines=ALERTS_MAX_LINES)


def _throttled(line: dict) -> dict | None:
    """For a refusal line: None when this IP is over its quota (counted), else
    the count line to write first ({} when there is none)."""
    ip = line.get("ip", "")
    now = _now()
    with _mem_lock:
        if len(_refusals) > 1000:  # addresses seen once never make it grow for good
            for stale in [k for k, w in _refusals.items() if not w or now - w[-1] > THROTTLE_WINDOW_S]:
                del _refusals[stale]
        window = _refusals.setdefault(ip, deque())
        while window and now - window[0] > THROTTLE_WINDOW_S:
            window.popleft()
        if len(window) >= THROTTLE_MAX:
            _suppressed[ip] = _suppressed.get(ip, 0) + 1
            return None
        window.append(now)
        count = _suppressed.pop(ip, 0)
    if not count:
        return {}
    return {"t": line["t"], "kind": "request", "ip": ip, "reason": "throttled", "count": count}


def event(caller, kind: str, **fields) -> None:
    """One audit line; never raises."""
    try:
        line = _line(kind, caller, fields)
        if kind == "request" and line.get("status", 0) >= 400:
            first = _throttled(line)
            if first is None:
                return
            if first:
                _write(first)
        _write(line)
    except Exception:  # the audit must never break a request
        log.exception("JARVIS: journal d'accès distant non écrit")


def alert(kind: str, text: str, caller=None, **fields) -> None:
    """An audit line, then (unless deduplicated) a PC toast, a PC warning card and the hooks."""
    spec = ALERTS.get(kind) or _kind(True, False, 0)
    text = str(text or "")[:160]
    try:
        _write(_line("alert", caller, {**fields, "alert": kind, "text": text}), alert=True)
    except Exception:  # a full disk must not hide the alert itself
        log.exception("JARVIS: alerte d'accès distant non écrite (%s)", kind)
    if spec["dedupe_s"] > 0:
        # Per device when one is named, else per address.
        who = getattr(caller, "device_id", "") or str(fields.get("device") or "") \
            or getattr(caller, "ip", "") or str(fields.get("ip") or "")
        now = _now()
        with _mem_lock:
            last = _last_alert.get((kind, who))
            if last is not None and now - last < spec["dedupe_s"]:
                return
            _last_alert[(kind, who)] = now
    if spec["toast"]:
        try:
            desktop.toast(spec["title"], text)
        except Exception:
            log.exception("JARVIS: notification d'alerte impossible (%s)", kind)
    try:
        events.publish_pc("warning", {"kind": "remote", "text": text, "plain": True})
    except Exception:
        log.exception("JARVIS: alerte non affichée sur le PC (%s)", kind)
    if spec["ntfy"]:
        for hook in ALERT_HOOKS[:]:  # a copy: a hook may be added meanwhile
            try:
                hook(kind, spec["ntfy_text"])
            except Exception:  # one hook must never stop the others
                log.exception("JARVIS: relais d'alerte en échec (%s)", kind)


def _read(path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for text in raw.splitlines():
        try:
            item = json.loads(text)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def tail(limit: int = 50) -> list[dict]:
    """The last audit lines, newest first."""
    try:
        limit = max(0, int(limit))
        with _lock:
            path, old = _audit_paths()
            lines = _read(path)
            if len(lines) < limit:
                lines = _read(old) + lines
        return list(reversed(lines[-limit:])) if limit else []
    except Exception:
        log.exception("JARVIS: journal d'accès distant illisible")
        return []


def reset_memory() -> None:
    """Tests: forget the dedupe and throttle windows."""
    with _mem_lock:
        _last_alert.clear()
        _refusals.clear()
        _suppressed.clear()
