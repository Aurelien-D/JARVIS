"""Remote access: a paired iPhone reaching JARVIS through Tailscale Serve.

Who is calling is decided here, never by the Host header:
- a request that arrived on REMOTE_PORT (the loopback port only Tailscale Serve
  uses), or that carries any proxy header, is remote;
- a remote request never receives nor authenticates with the PC's page token:
  it goes through remote_guard, which stamps request.state.caller.

The gate (remote_guard, spec 4.1) admits a request only when it came in on the
Serve port, remote access is on and not paused, Serve's headers are exactly
what this PC's Serve sends (name, https, one tailnet address, an allowed
Tailscale login, same origin), and it carries a paired device's cookie plus
that device's page token, or a Siri key on the Siri route; then the route table
REMOTE_ALLOW decides (deny by default).

- Identity: reverse pairing approved on the PC (4.7); the device is bound to its
  tailnet node and login (4.2); its 256-bit secret travels in an HttpOnly
  __Host- cookie and is stored hashed (devices.py).
- Page tokens are minted only for a top-level same-origin load of the page, kept
  in memory (hashed) and slide while the page's event stream is open (4.3).
- Lockouts are per credential and source address, never global: a stale cookie
  or Siri key never locks the iPhone out, nobody can lock another device (4.5).
- remote.json holds the switch (off by default, changed only from the PC), the
  pause, the full-access opt-in and whether Serve is published. READY keeps it
  all off until every protection is proven (C2).

All the in-memory state below sits behind one RLock: the Serve listener's
event loop and the main server's thread pool both use it. The gate's decision
runs in the thread pool, as it reads devices.json and may ask tailscale whois.
"""
import email.header
import hashlib
import ipaddress
import itertools
import logging
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date

from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.routing import Mount, compile_path

from . import config, devices, events, page, store

log = logging.getLogger(__name__)

# Flipped to True by the integrator in the C2 merge, once every P0 proof is green.
READY = False

PROXY_MARKERS = ("forwarded", "via", "x-real-ip")
# The Réglages keys the phone may change, and the sections it sees (4.10).
REMOTE_SETTINGS: frozenset = frozenset({"voice", "voice_speed", "briefing_time", "briefing_days", "briefing_news",
                                        "quiet_hours", "city"})
REMOTE_SECTIONS: tuple = ("voix", "proactivite", "distance", "notifications", "apropos")

REMOTE_FILE = "remote.json"
CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' https://api.openai.com; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'; manifest-src 'self'")

COOKIE = "__Host-jarvis"
PAIR_COOKIE = "__Host-jarvis-pair"
# Strict unless the device test shows a Home Screen launch drops it (then "Lax"):
# the exact Origin, Sec-Fetch-Site and the page token already stop cross-site requests.
COOKIE_SAMESITE = "Strict"
COOKIE_MAX_AGE = 34_560_000  # 400 days, refreshed on every page load
PAIR_MAX_AGE = 600

TOKEN_TTL_S = 12 * 3600       # sliding, while the page's stream is open
MAX_TOKENS_PER_DEVICE = 8
FAIL_WINDOW_S = 600
FAIL_MAX = 5                  # failures in FAIL_WINDOW_S lock that credential...
LOCK_S = 900                  # ...for 15 minutes
UNKNOWN_COOKIE_ALERT = 20     # more unknown cookies than this from one IP in 10 min: one alert
MINT_WINDOW_S = 600
MINTS_PER_WINDOW = 12
MINTS_PER_DAY = 40
PAIR_MINUTES = 10
MAX_WAITING = 3
WHOIS_WAIT_S = 3
TOUCH_EVERY_S = 60

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.[a-z0-9-]+\.ts\.net$")
LOGIN_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")
_DEVICE_COOKIE = re.compile(r"^(d_[0-9a-f]{16})\.([A-Za-z0-9_-]{20,128})$")
_PAIR_VALUE = re.compile(r"^(r_[0-9a-f]{12})\.([A-Za-z0-9_-]{20,128})$")
_SIRI_HEADER = re.compile(r"^Bearer jv_siri_(k_[0-9a-f]{16})\.([A-Za-z0-9_-]{20,128})$")
QUIET_PATHS = ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png")
# What pair.html loads (public files, no credential): served even while remote
# access is off, paused or the login is refused, so that pairing page can say why.
PAIR_ASSETS = ("/static/js/pair.js", "/static/js/strings-fr.js", "/static/css/pair.css", "/static/css/tokens.css")

OPEN, APP, SIRI = "open", "app", "siri"
# The remote route table (3.14): deny by default. Matched like Starlette's
# router matches (compile_path on the decoded path), HEAD as GET, no
# trailing-slash tolerance; /static/{path:path} stands for the static mount.
REMOTE_ALLOW = (
    (("GET",), "/", (OPEN, APP)),
    (("GET",), "/static/{path:path}", (OPEN, APP)),
    (("GET",), "/healthz", (OPEN, APP)),
    (("POST",), "/api/remote/pair-request", (OPEN, APP)),
    (("GET",), "/api/remote/pair-status", (OPEN, APP)),
    *((("GET",), p, (APP,)) for p in (
        "/api/config", "/api/onboarding", "/api/delivery", "/api/ares", "/api/remarques", "/api/pending",
        "/api/inbox", "/api/remote/state", "/api/notify", "/api/remote/siri-key",
        "/api/task/{task_id}", "/api/task/{task_id}/log", "/api/memory", "/api/events")),
    *((("GET", "POST"), p, (APP,)) for p in ("/api/tasks", "/api/schedules", "/api/journal", "/api/usage")),
    (("GET", "PUT"), "/api/settings", (APP,)),
    *((("POST",), p, (APP,)) for p in (
        "/api/session", "/api/tool", "/api/presence", "/api/dnd", "/api/voice/turn", "/api/voice/taint",
        "/api/task/{task_id}/cancel", "/api/task/{task_id}/retry", "/api/schedules/{item_id}/snooze",
        "/api/remarques/{key}/dismiss", "/api/undo/{fact_id}", "/api/inbox/{item_id}/ack",
        "/api/pending/{pending_id}/decide", "/api/remote/pause", "/api/remote/forget", "/api/notify/test")),
    *((("PATCH", "DELETE"), p, (APP,)) for p in ("/api/schedules/{item_id}", "/api/memory/{fact_id}")),
    (("POST",), "/api/raccourci", (SIRI,)),
)
_ALLOW = [(frozenset(m), t, frozenset(s), compile_path(t)[0]) for m, t, s in REMOTE_ALLOW]
_OPEN_API = {("POST", "/api/remote/pair-request"), ("GET", "/api/remote/pair-status")}

_NOT_YET = "Accès à distance pas encore disponible dans cette version."
T_FUNNEL = "Refusé : JARVIS ne répond jamais depuis internet."
T_PROXY = "Requête refusée : un proxy ou un antivirus modifie les requêtes locales de JARVIS."
T_OFF = "Accès à distance coupé sur le PC."
T_PAUSED = "Accès à distance en pause jusqu'à {until}."
T_REFUSED = "Requête distante refusée."
T_LOGIN = "Compte Tailscale non autorisé : connectez l'iPhone avec le même compte que le PC."
T_LOCKED = "Trop d'échecs : réessayez dans 15 minutes."
T_SIRI_UNKNOWN = "Clé Siri inconnue : recréez-la sur le PC."
T_SIRI_REFUSED = "Clé Siri refusée : recréez-la sur le PC."
T_REVOKED = "Cet appareil a été retiré : associez-le à nouveau."
T_MOVED = "Appareil associé depuis une autre adresse ou un autre compte : associez-le à nouveau."
T_PAGE_KEY = "Jeton de session invalide : recharge la page."
T_UNPAIRED = "Appareil non associé : associez-le depuis le PC."
T_PC_ONLY = "Réservé au PC."
T_IPHONE_ONLY = "Réservé à l'iPhone."
T_RATE = "Trop de demandes : patientez un instant."
T_NO_CAP = ("Fixez d'abord un plafond de dépense par jour sur le PC (Réglages › Coûts) : il protège "
            "votre crédit OpenAI à distance.")
T_CAPPED = "Plafond du jour atteint : la voix reprendra demain (modifiable sur le PC, Réglages › Coûts)."
T_MINTS = "Trop de connexions vocales d'affilée : patientez quelques minutes."
T_CLOSED = "Association fermée : ouvrez-la sur le PC (Réglages › Accès à distance)."
T_PAIRED = "Cet iPhone est déjà associé."
T_TOO_MANY = "Trop de demandes en attente : réessayez dans quelques minutes."
T_CODE = "Code différent : vérifiez le code affiché sur l'iPhone."
T_LIMIT = f"{devices.MAX_DEVICES} appareils au plus : retirez-en un dans Réglages › Accès à distance."
T_NO_REQUEST = "Aucune demande d'association en cours sur cet iPhone : recommencez."
T_OTHER_IP = "Demande d'association faite depuis une autre adresse : recommencez."
T_UNKNOWN_REQUEST = "Demande inconnue ou expirée."
T_DONE_REQUEST = "Demande déjà traitée."
T_NEEDS_ON = "Accès à distance coupé : activez-le d'abord."
_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre")


class RemoteError(Exception):
    """Remote access refused an operation; the message is French, shown as is.
    status: the HTTP status a route answers with it."""

    def __init__(self, message: str = "", status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Caller:
    kind: str            # "pc" | "app" | "siri" | "unpaired"
    device_id: str = ""  # "d_<16 hex>" for app and siri (the paired device a Siri key belongs to)
    key_id: str = ""     # "k_<16 hex>" for siri
    ip: str = ""         # tailnet IP from X-Forwarded-For ("" for pc)
    login: str = ""      # Tailscale-User-Login, decoded and lowercased ("" for pc)
    name: str = ""       # device name, for audit and UI ("" for pc/unpaired)

    @property
    def remote(self) -> bool:
        return self.kind != "pc"

    @property
    def origin(self) -> str:
        """The value stored on sessions, tasks, pendings, schedules and inbox items."""
        if self.kind == "app":
            return f"app:{self.device_id}"
        if self.kind == "siri":
            return f"siri:{self.key_id}"
        return self.kind  # "pc" or "unpaired"


PC = Caller(kind="pc")
_UNPAIRED = Caller(kind="unpaired")


def caller_of(request) -> Caller:
    """request.state.caller; a request the guard did not stamp counts as unpaired (fail closed)."""
    caller = getattr(request.state, "caller", None)
    return caller if isinstance(caller, Caller) else _UNPAIRED


def kind_of(origin: str) -> str:
    """'pc' for '' or 'pc'; else the part before ':' ('app', 'siri'); anything else
    'unknown' (treated as remote)."""
    origin = str(origin or "")
    if origin in ("", "pc"):
        return "pc"
    head, sep, _ = origin.partition(":")
    return head if sep and head in ("app", "siri") else "unknown"


def is_remote_request(request) -> bool:
    """Arrived on the Serve port, or carries any proxy header: remote. A proxy
    header on the PC port still counts (fail closed); the gate then refuses it."""
    server = request.scope.get("server") or ()
    if len(server) > 1 and server[1] == config.REMOTE_PORT:
        return True
    return any(h in PROXY_MARKERS or h.startswith(("x-forwarded-", "tailscale-"))
               for h in (k.lower() for k in request.headers))

# ---------------------------------------------------------------- memory (one lock)

_lock = threading.RLock()
_tokens: dict = {}     # sha256(page token) -> {"device", "expires", "n" (mint order)}
_minted = itertools.count()
_fails: dict = {}      # credential key -> deque of failure times
_locks: dict = {}      # credential key -> locked until
_hits: dict = {}       # rate key -> deque of request times
_mints: dict = {}      # device id -> deque of voice session times (10-minute window)
_unknown: dict = {}    # ip -> deque of unknown-cookie times
_touched: dict = {}    # (kind, id) -> last devices.json touch
_pairing = {"until": 0.0}
_requests: dict = {}   # request id -> pairing request
_served: dict = {}     # id(app) -> compiled served templates (audit)


def _now() -> float:
    return time.time()


def reset_memory() -> None:
    """Tests: forget tokens, lockouts, rate windows, the pairing window and requests."""
    with _lock:
        for store_ in (_tokens, _fails, _locks, _hits, _mints, _unknown, _touched, _requests):
            store_.clear()
        _pairing["until"] = 0.0

# ---------------------------------------------------------------- remote.json

_DEFAULTS = {"enabled": False, "host": "", "logins": [], "paused_until": 0, "complet_until": 0,
             "published": False, "changed_at": 0, "changed_by": "pc"}


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _clean_host(value) -> str:
    return str(value or "").strip().lower().rstrip(".")


def _clean_login(value) -> str:
    return str(value or "").strip().lower()


def _settings() -> dict:
    """remote.json, every field present and typed (a hand edit never opens anything)."""
    raw = store.load(REMOTE_FILE, {})
    raw = raw if isinstance(raw, dict) else {}
    logins = raw.get("logins") if isinstance(raw.get("logins"), list) else []
    return {"enabled": raw.get("enabled") is True, "host": _clean_host(raw.get("host")),
            "logins": [x for x in (_clean_login(v) for v in logins if isinstance(v, str)) if x],
            "paused_until": _num(raw.get("paused_until")), "complet_until": _num(raw.get("complet_until")),
            "published": raw.get("published") is True, "changed_at": _num(raw.get("changed_at")),
            "changed_by": str(raw.get("changed_by") or "pc")[:40]}


def _save_settings(**changes) -> dict:
    with store.LOCK:
        data = {**_settings(), **changes}
        store.save(REMOTE_FILE, data)
    return data


def _env_logins() -> list:
    return [x for x in (_clean_login(v) for v in str(config.REMOTE_LOGINS or "").split(",")) if x]


def _host_of(saved: dict) -> str:
    return _clean_host(config.REMOTE_HOST) or saved["host"]


def _logins_of(saved: dict) -> list:
    return _env_logins() or list(saved["logins"])


def _enabled_of(saved: dict) -> bool:
    return bool(READY and saved["enabled"])


def _paused_of(saved: dict) -> float:
    return saved["paused_until"] if saved["paused_until"] > _now() else 0.0


def is_enabled() -> bool:
    return _enabled_of(_settings())


def paused_until() -> float:
    return _paused_of(_settings())


def host() -> str:
    """The exact Serve name; the environment wins over what Réglages saved."""
    return _host_of(_settings())


def logins() -> list[str]:
    """The allowed Tailscale logins; the environment wins over what Réglages saved."""
    return _logins_of(_settings())


def _clock(ts: float) -> str:
    t = time.localtime(ts)
    return f"{t.tm_hour} h {t.tm_min:02d}"


def _until_text(ts: float) -> str:
    """'14 h 30' today, 'demain 14 h 30' tomorrow, else the date."""
    days = (date(*time.localtime(ts)[:3]) - date(*time.localtime(_now())[:3])).days
    return _clock(ts) if days <= 0 else f"demain {_clock(ts)}" if days == 1 else _date(ts)


def _date(ts: float) -> str:
    """'17 octobre, 9 h 30'."""
    t = time.localtime(ts)
    return f"{'1er' if t.tm_mday == 1 else t.tm_mday} {_MONTHS[t.tm_mon - 1]}, {_clock(ts)}"

# ---------------------------------------------------------------- the switch, the pause, the opt-in


def set_enabled(on: bool, *, host: str | None = None, login: str | None = None, by: Caller = PC) -> dict:
    """Switch remote access on or off (4.6), from the PC only; the PC's state."""
    from . import audit, listener, tailscale, usage
    saved = _settings()
    now = _now()
    if on:
        if not READY:
            raise RemoteError(_NOT_YET)
        if by.kind != "pc":
            raise RemoteError("Seul le PC peut activer l'accès à distance.", 403)
        given_host = _clean_host(host)
        if not HOST_RE.fullmatch(_clean_host(config.REMOTE_HOST) or given_host or saved["host"]):
            raise RemoteError("Adresse Tailscale invalide (exemple : jarvis-pc.tail0000.ts.net).")
        given_logins = [_clean_login(login)] if login and str(login).strip() else []
        effective = _env_logins() or given_logins or saved["logins"]
        if not effective or not all(LOGIN_RE.fullmatch(x) for x in effective):
            raise RemoteError("Compte Tailscale inconnu : connectez Tailscale sur ce PC.")
        if usage.daily_cap() <= 0:
            raise RemoteError("Fixez d'abord un plafond de dépense par jour (Réglages › Coûts) : il protège "
                              "votre crédit OpenAI quand JARVIS est utilisé à distance.")
        if config.REMOTE_PORT == config.PORT:
            raise RemoteError("JARVIS_REMOTE_PORT doit différer de JARVIS_PORT.")
        started = listener.start()
        if not started.get("running"):
            raise RemoteError(started.get("error")
                              or f"Port {config.REMOTE_PORT} déjà utilisé : choisissez un autre JARVIS_REMOTE_PORT.")
        saved = _save_settings(
            enabled=True, host=given_host if HOST_RE.fullmatch(given_host) else saved["host"],
            logins=given_logins if given_logins and LOGIN_RE.fullmatch(given_logins[0]) else saved["logins"],
            changed_at=now, changed_by="pc")
        audit.event(by, "state", text="Accès à distance activé.")
        audit.alert("remote_on", "Accès à distance activé.")
        if saved["published"]:  # switched off earlier: Serve comes back as it was
            try:
                result = tailscale.publish()
                audit.event(by, "state", text=f"Publication Tailscale : {result.get('state', '?')}.")
            except Exception:  # best effort, the switch is on regardless
                log.exception("JARVIS: publication Tailscale impossible")
        return state_for(PC)
    if by.kind != "pc":
        raise RemoteError("Seul le PC peut couper l'accès à distance.", 403)
    _save_settings(enabled=False, changed_at=now, changed_by="pc")
    events.close_streams(lambda c: c is not None and c.remote)
    _drop_tokens()
    listener.stop()
    close_pairing()
    try:
        # Withdrawn only when it is exactly JARVIS's target; 'published' stays, so on restores it.
        if tailscale.serve_status().get("state") == "ready":
            result = tailscale.unpublish()
            audit.event(by, "state", text=f"Retrait Tailscale : {result.get('state', '?')}.")
    except Exception:
        log.exception("JARVIS: retrait de la publication Tailscale impossible")
    audit.event(by, "state", text="Accès à distance coupé.")
    audit.alert("remote_off", "Accès à distance coupé.")
    return state_for(PC)


def pause(hours: int, *, by: Caller) -> float:
    """Pause remote access: 1 or 24 hours from the phone (after its confirmation
    dialog), 0 to 24 from the PC (0 resumes). Returns the end (0: not paused)."""
    from . import audit
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours != int(hours):
        raise RemoteError("Durée de pause invalide.")
    hours = int(hours)
    if by.kind == "app":
        if hours not in (1, 24):
            raise RemoteError("Depuis l'iPhone, la pause dure 1 h ou 24 h.")
    elif by.kind != "pc":
        raise RemoteError(T_IPHONE_ONLY, 403)
    elif not 0 <= hours <= 24:
        raise RemoteError("La pause dure de 0 à 24 heures.")
    now = _now()
    until = now + hours * 3600 if hours else 0.0
    _save_settings(paused_until=until, changed_at=now, changed_by="pc" if by.kind == "pc" else by.origin)
    if until:
        events.close_streams(lambda c: c is not None and c.remote)
    audit.event(by, "state", text=f"Pause jusqu'à {_until_text(until)}." if until else "Pause levée.")
    if by.remote and until:
        audit.alert("remote_paused", f"Accès à distance mis en pause depuis l'iPhone jusqu'à {_until_text(until)}.",
                    by)
    return until


def complet_until() -> float:
    until = _settings()["complet_until"]
    return until if until > _now() else 0.0


def complet_allowed(now: float | None = None) -> bool:
    """The PC allowed full-access tasks from the phone, and that has not expired."""
    return (_now() if now is None else now) < _settings()["complet_until"]


def set_complet(duration: str) -> float:
    """'never', '24h' or '7d' (the route is PC-only); returns the new end (0: never)."""
    from . import audit
    spans = {"never": 0, "24h": 86400, "7d": 7 * 86400}
    if duration not in spans:
        raise RemoteError("Durée inconnue : Jamais, 24 h ou 7 jours.")
    until = _now() + spans[duration] if spans[duration] else 0.0
    _save_settings(complet_until=until)
    audit.event(PC, "state", text=f"Accès complet depuis l'iPhone : {duration}.")
    if until:
        audit.alert("complet_optin", f"Accès complet depuis l'iPhone autorisé jusqu'au {_date(until)}.")
    return until


def note_published(on: bool) -> None:
    """Remembers whether JARVIS's Serve configuration is published (A4's routes)."""
    _save_settings(published=bool(on))

# ---------------------------------------------------------------- remote voice cost (4.8)


def check_voice(caller) -> tuple[int, str] | None:
    """(status, French reason) when a remote voice session must be refused. Never counts."""
    from . import usage
    if caller is None or not caller.remote:
        return None
    if not caller.device_id:
        return (401, T_UNPAIRED)
    if usage.daily_cap() <= 0:
        return (403, T_NO_CAP)
    if usage.over_daily_cap():
        return (403, T_CAPPED)
    now = _now()
    with _lock:
        window = _mints.get(caller.device_id) or deque()
        recent = sum(1 for t in window if now - t < MINT_WINDOW_S)
    if recent >= MINTS_PER_WINDOW or mints_today(caller.device_id) >= MINTS_PER_DAY:
        return (429, T_MINTS)
    return None


def note_mint(caller) -> None:
    """One successful remote mint (counted after OpenAI answered)."""
    from . import usage
    if caller is None or not caller.remote or not caller.device_id:
        return
    now = _now()
    with _lock:
        window = _mints.setdefault(caller.device_id, deque())
        while window and now - window[0] >= MINT_WINDOW_S:
            window.popleft()
        window.append(now)
    usage.note_remote_mint(caller.device_id, now)


def mints_today(device_id: str) -> int:
    from . import usage
    return len(usage.remote_mints(device_id))

# ---------------------------------------------------------------- page tokens (4.3)


def _digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _mint_token(device_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with _lock:
        for h in [h for h, t in _tokens.items() if t["expires"] <= now]:
            del _tokens[h]
        mine = sorted((t["n"], h) for h, t in _tokens.items() if t["device"] == device_id)
        for _, h in mine[:max(0, len(mine) - (MAX_TOKENS_PER_DEVICE - 1))]:
            del _tokens[h]  # the oldest page of that device loses its token
        _tokens[_digest(token)] = {"device": device_id, "expires": now + TOKEN_TTL_S, "n": next(_minted)}
    return token


def _token_ok(device_id: str, given: str) -> bool:
    if not given:
        return False
    now = _now()
    with _lock:
        entry = _tokens.get(_digest(given))
        if entry is None or entry["device"] != device_id or entry["expires"] <= now:
            return False
        entry["expires"] = now + TOKEN_TTL_S
    return True


def _drop_tokens(device_id: str | None = None) -> None:
    with _lock:
        for h in [h for h, t in _tokens.items() if device_id is None or t["device"] == device_id]:
            del _tokens[h]


def keepalive(caller) -> None:
    """The device's event stream is still connected: its page tokens slide."""
    if caller is None or getattr(caller, "kind", "") != "app":
        return
    now = _now()
    with _lock:
        for entry in _tokens.values():
            if entry["device"] == caller.device_id and entry["expires"] > now:
                entry["expires"] = now + TOKEN_TTL_S

# ---------------------------------------------------------------- lockouts and rate limits (4.5)


MAX_WINDOWS = 1000  # keys kept per window table: stale ones go first


def _bounded(table: dict, now: float, span: float) -> None:
    """Forget windows with nothing left in their span (caller holds _lock):
    addresses that came once never make the tables grow for good."""
    if len(table) > MAX_WINDOWS:
        for key in [k for k, w in table.items() if not w or now - w[-1] >= span]:
            del table[key]


def _is_locked(key) -> bool:
    now = _now()
    with _lock:
        until = _locks.get(key)
        if until is None:
            return False
        if until > now:
            return True
        del _locks[key]
        return False


def _fail(key, ip: str, device: str = "") -> None:
    """One authentication failure under this key; the fifth in 10 minutes locks
    it for 15 minutes and alerts the PC."""
    from . import audit
    now = _now()
    with _lock:
        _bounded(_fails, now, FAIL_WINDOW_S)
        window = _fails.setdefault(key, deque())
        while window and now - window[0] >= FAIL_WINDOW_S:
            window.popleft()
        window.append(now)
        if len(window) < FAIL_MAX:
            return
        _fails.pop(key, None)
        _locks[key] = now + LOCK_S
    audit.alert("auth_failures", f"Échecs répétés d'authentification depuis {ip} : bloqué 15 minutes.",
                ip=ip, device=device)


def _note_unknown_cookie(ip: str) -> None:
    """Unknown cookies are cleared, never counted; a burst still tells the PC once."""
    from . import audit
    now = _now()
    with _lock:
        _bounded(_unknown, now, FAIL_WINDOW_S)
        window = _unknown.setdefault(ip, deque())
        while window and now - window[0] >= FAIL_WINDOW_S:
            window.popleft()
        window.append(now)
        burst = len(window) == UNKNOWN_COOKIE_ALERT + 1
    if burst:
        audit.alert("auth_failures", f"Échecs répétés d'authentification depuis {ip} : cookies d'appareil "
                                     "inconnus (pas de blocage).", ip=ip)


def _rate_ok(*rules) -> bool:
    """rules: (key, limit, window_s). Counted only when every rule passes."""
    now = _now()
    with _lock:
        _bounded(_hits, now, 86400)
        windows = []
        for key, limit, span in rules:
            window = _hits.setdefault(key, deque())
            while window and now - window[0] >= span:
                window.popleft()
            if len(window) >= limit:
                return False
            windows.append(window)
        for window in windows:
            window.append(now)
    return True

# ---------------------------------------------------------------- pairing (4.7)


def open_pairing(minutes: int = PAIR_MINUTES) -> float:
    """Open the pairing window (PC), in memory; returns its end."""
    if not is_enabled():
        raise RemoteError(T_NEEDS_ON, 409)
    minutes = max(1, min(PAIR_MINUTES, int(minutes)))
    until = _now() + minutes * 60
    with _lock:
        _pairing["until"] = until
        for r in _requests.values():
            if r["status"] in ("waiting", "approved"):
                r["expires"] = until
    return until


def close_pairing() -> None:
    """Close the window; what was not collected expires and leaves nothing behind."""
    now = _now()
    with _lock:
        _pairing["until"] = 0.0
        for r in _requests.values():
            if r["status"] in ("waiting", "approved"):
                r.update(status="expired", expires=now)


def pairing_until() -> float:
    with _lock:
        return _pairing["until"] if _pairing["until"] > _now() else 0.0


def _prune_requests(now: float) -> None:
    for rid, r in list(_requests.items()):
        if r["status"] in ("waiting", "approved") and r["expires"] <= now:
            r["status"] = "expired"
        if now - r["created"] > 3600:
            del _requests[rid]


def _public_request(r: dict, now: float) -> dict:
    return {"id": r["id"], "code": r["code"], "name": r["name"], "ip": r["ip"], "login": r["login"], "os": r["os"],
            "host_name": r["host_name"], "created": r["created"], "expires_in": max(0, int(r["expires"] - now)),
            "status": "waiting" if r["status"] == "delivering" else r["status"]}


def pairing_requests() -> list:
    """The requests the PC lists (oldest first)."""
    now = _now()
    with _lock:
        _prune_requests(now)
        return [_public_request(r, now) for r in sorted(_requests.values(), key=lambda r: r["created"])]


def _whois(ip: str, wait: float | None = None) -> dict:
    """tailscale whois, best effort (never raises); within `wait` seconds when given."""
    from . import tailscale

    def ask():
        try:
            info = tailscale.whois(ip)
            return info if isinstance(info, dict) else {}
        except Exception:  # noqa: BLE001 - no whois: the request goes on without it
            return {}
    if wait is None:
        return ask()
    out = {}
    thread = threading.Thread(target=lambda: out.update(ask()), name="jarvis-whois", daemon=True)
    thread.start()
    thread.join(wait)
    return dict(out) if not thread.is_alive() else {}


def request_pairing(caller, name="", ua: str = "") -> tuple[dict, str]:
    """A phone asks to be paired (unpaired caller, window open). Returns what the
    phone sees ({request_id, code, expires_in}) and its pairing cookie value."""
    from . import audit
    if caller.kind == "pc":
        raise RemoteError(T_IPHONE_ONLY, 403)
    ip, now = caller.ip, _now()
    refusal = None
    with _lock:
        _prune_requests(now)
        if caller.kind != "unpaired":
            refusal = T_PAIRED
        elif not pairing_until():
            refusal = T_CLOSED
        else:
            for rid in [rid for rid, r in _requests.items() if r["ip"] == ip and r["status"] == "waiting"]:
                del _requests[rid]  # one waiting request per address: the new one replaces it
            if sum(r["status"] == "waiting" for r in _requests.values()) >= MAX_WAITING:
                refusal = T_TOO_MANY
    if refusal:
        _fail(("pair", ip), ip)
        raise RemoteError(refusal, 409)
    info = _whois(ip, WHOIS_WAIT_S)
    secret = secrets.token_urlsafe(32)
    with _lock:
        if sum(r["status"] == "waiting" for r in _requests.values()) >= MAX_WAITING:
            refusal = T_TOO_MANY
        else:
            taken = {r["code"] for r in _requests.values() if r["status"] == "waiting"}
            code = f"{secrets.randbelow(10000):04d}"
            while code in taken:  # two phones never show the same code
                code = f"{secrets.randbelow(10000):04d}"
            rid = "r_" + secrets.token_hex(6)
            request = {"id": rid, "code": code, "secret_sha256": _digest(secret), "ip": ip, "login": caller.login,
                       "name": devices.clean_label(name, "iPhone"), "ua": str(ua or "")[:160],
                       "os": devices.clean_label(info.get("os"), "", 40),
                       "host_name": devices.clean_label(info.get("host_name"), "", 40),
                       "node_id": str(info.get("node_id") or ""),
                       "ips": [str(a) for a in info.get("addresses") or [] if isinstance(a, str)][:8],
                       "created": now, "expires": pairing_until() or now, "status": "waiting"}
            _requests[rid] = request
            public = _public_request(request, now)
    if refusal:
        _fail(("pair", ip), ip)
        raise RemoteError(refusal, 409)
    events.publish_pc("remote", {"kind": "pair_request", "request": public})
    where = ", ".join(x for x in (public["os"], ip) if x)
    audit.alert("pair_request", f"Demande d'association : code {public['code']} ({where}).", caller)
    return {"request_id": rid, "code": public["code"], "expires_in": public["expires_in"]}, f"{rid}.{secret}"


def allow_pairing(request_id: str, name=None, code=None) -> dict:
    """The PC allows a waiting request; the device is created only when the
    phone collects its secret (pairing_status)."""
    from . import audit
    now = _now()
    with _lock:
        _prune_requests(now)
        r = _requests.get(str(request_id))
        if r is None or r["status"] == "expired":
            raise RemoteError(T_UNKNOWN_REQUEST, 404)
        if r["status"] != "waiting":
            raise RemoteError(T_DONE_REQUEST, 409)
        waiting = sum(x["status"] == "waiting" for x in _requests.values())
        if waiting > 1 and str(code or "").strip() != r["code"]:
            raise RemoteError(T_CODE, 409)
        promised = sum(x["status"] in ("approved", "delivering") for x in _requests.values())
    if len(devices.active()) + promised >= devices.MAX_DEVICES:
        raise RemoteError(T_LIMIT, 409)
    with _lock:
        if r["status"] != "waiting":
            raise RemoteError(T_DONE_REQUEST, 409)
        r["status"] = "approved"
        if name is not None and str(name).strip():
            r["name"] = devices.clean_label(name, "iPhone")
        public = _public_request(r, now)
    audit.event(PC, "pair", decision="oui", text=f"Demande {public['code']} autorisée.")
    events.publish_pc("remote", {"kind": "pair_request", "request": public})
    return public


def deny_pairing(request_id: str) -> dict:
    from . import audit
    now = _now()
    with _lock:
        _prune_requests(now)
        r = _requests.get(str(request_id))
        if r is None or r["status"] == "expired":
            raise RemoteError(T_UNKNOWN_REQUEST, 404)
        if r["status"] not in ("waiting", "approved"):
            raise RemoteError(T_DONE_REQUEST, 409)
        r["status"] = "denied"
        public = _public_request(r, now)
    audit.event(PC, "pair", decision="non", text=f"Demande {public['code']} refusée.")
    events.publish_pc("remote", {"kind": "pair_request", "request": public})
    return public


def pairing_status(caller, cookie_value: str) -> tuple[dict, str | None]:
    """The phone's poll: ({status, code}, device cookie value or None). When the
    request was approved, the device is created now, once, for this browser
    (pairing cookie) at this address only."""
    from . import audit
    if caller.kind == "pc":
        raise RemoteError(T_IPHONE_ONLY, 403)
    if caller.kind != "unpaired":
        return {"status": "done"}, None
    match = _PAIR_VALUE.fullmatch(str(cookie_value or ""))
    if not match:
        raise RemoteError(T_NO_REQUEST, 401)
    now = _now()
    with _lock:
        _prune_requests(now)
        r = _requests.get(match.group(1))
        if r is None or not secrets.compare_digest(r["secret_sha256"], _digest(match.group(2))):
            raise RemoteError(T_NO_REQUEST, 401)
        if r["ip"] != caller.ip:
            raise RemoteError(T_OTHER_IP, 403)
        if r["status"] != "approved":
            status = "waiting" if r["status"] == "delivering" else r["status"]
            return {"status": status, "code": r["code"]}, None
        r["status"] = "delivering"  # claimed: a second poll can never get a second device
        request = dict(r)
    node, ips = request["node_id"], request["ips"]
    if not node:  # whois was slow at request time: once more, it binds the device to its node
        info = _whois(request["ip"], WHOIS_WAIT_S)
        node = str(info.get("node_id") or "")
        ips = [str(a) for a in info.get("addresses") or [] if isinstance(a, str)][:8]
    try:
        device, secret = devices.add(request["name"], ip=request["ip"], login=request["login"], os=request["os"],
                                     host_name=request["host_name"], node_id=node, ips=ips, ua=request["ua"],
                                     paired_seq=events.current_id())
    except ValueError:  # the device limit was reached meanwhile
        with _lock:
            r["status"] = "denied"
        return {"status": "denied", "code": request["code"]}, None
    except Exception:  # a disk error: nothing was delivered, the next poll may try again
        with _lock:
            r["status"] = "approved"
        raise
    with _lock:
        r["status"] = "done"
    who = Caller(kind="app", device_id=device["id"], ip=request["ip"], login=request["login"], name=device["name"])
    audit.alert("new_device", f"Nouvel appareil associé : « {device['name']} » ({request['ip']}).", who)
    events.publish_pc("remote", {"kind": "pair_done"})
    return {"status": "approved", "code": request["code"]}, f"{device['id']}.{secret}"


def cookie_header(value: str, max_age: int = COOKIE_MAX_AGE, name: str = COOKIE) -> str:
    """Set-Cookie for the device (or, with name, the pairing) cookie; value '' clears it."""
    if not value:
        return f"{name}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite={COOKIE_SAMESITE}"
    return f"{name}={value}; Path=/; Secure; HttpOnly; SameSite={COOKIE_SAMESITE}; Max-Age={max_age}"

# ---------------------------------------------------------------- devices: revocation, activity


def revoke_device(device_id: str, *, by: Caller = PC) -> int:
    """Revoke a device at once (4.7): its keys, tokens, streams, pendings, Siri
    conversations and running tasks end. Returns how many tasks were cancelled.
    KeyError for an unknown device."""
    from . import audit, confirm, raccourci, tasks
    device = devices.get(device_id)
    if device is None:
        raise KeyError(device_id)
    keys = [k["id"] for k in device["siri_keys"]]
    devices.revoke(device_id)
    _drop_tokens(device_id)
    events.close_streams(lambda c: c is not None and c.device_id == device_id)
    origins = [f"app:{device_id}", *(f"siri:{k}" for k in keys)]
    for origin in origins:
        try:
            confirm.cancel_for_origin(origin)
        except Exception:  # the revocation itself must go through
            log.exception("JARVIS: demandes de l'appareil retiré non annulées")
    try:
        raccourci.forget_device(device_id)
    except Exception:
        log.exception("JARVIS: conversations Siri de l'appareil retiré non oubliées")
    cancelled = 0
    for task in tasks.running():
        if task.get("via") in origins and tasks.cancel(task["id"]).get("ok"):
            cancelled += 1
    audit.event(by, "device", device=device_id, reason="revoked", count=cancelled,
                text=f"Appareil « {device['name']} » retiré.")
    events.publish_pc("remote", {"kind": "devices"})
    return cancelled


def origin_active(origin: str) -> bool:
    """Is the device or key behind this origin still allowed?"""
    kind = kind_of(origin)
    if kind == "pc":
        return True
    ident = str(origin).partition(":")[2]
    if kind == "app":
        device = devices.get(ident)
        return bool(device and not device["revoked"])
    if kind == "siri":
        return devices.siri_key_known(ident) and not devices.siri_key_revoked(ident)
    return False


def replay_floor(caller) -> int:
    """The first event a reconnecting remote stream may replay after: never
    before its pairing; an unknown device replays nothing."""
    if caller is None or not caller.remote:
        return 0
    device = devices.get(caller.device_id) if caller.device_id else None
    if device and not device["revoked"]:
        return device["paired_seq"]
    return events.current_id()


def state_for(caller) -> dict:
    """The body of GET /api/remote/state for this caller (4.6)."""
    from . import listener, tailscale, usage
    saved = _settings()
    if caller.kind == "app":
        device = devices.get(caller.device_id) or {}
        host_value = _host_of(saved)
        return {"enabled": _enabled_of(saved), "paused_until": _paused_of(saved),
                "device": {"id": caller.device_id, "name": device.get("name") or caller.name},
                "complet_until": complet_until(), "url": f"https://{host_value}/" if host_value else ""}
    if caller.kind != "pc":
        return {}
    try:
        info = tailscale.self_info() or {}
    except Exception:  # noqa: BLE001
        info = {}
    env_host = _clean_host(config.REMOTE_HOST)
    detected_host = _clean_host(info.get("dns_name"))
    host_value, host_source = next(((v, s) for v, s in ((env_host, "env"), (saved["host"], "saved"),
                                                         (detected_host, "detected")) if v), ("", "none"))
    detected_login = _clean_login(info.get("login"))
    login_value, login_source = next(((v, s) for v, s in ((_env_logins(), "env"), (saved["logins"], "saved"),
                                                           ([detected_login] if detected_login else [], "detected"))
                                      if v), ([], "none"))
    cap = usage.daily_cap()
    return {"enabled": _enabled_of(saved), "ready": READY, "paused_until": _paused_of(saved),
            "host": host_value, "host_source": host_source, "logins": list(login_value),
            "logins_source": login_source, "port": config.REMOTE_PORT, "listener": listener.state(),
            "cap_usd": cap, "cap_ok": cap > 0, "complet_until": complet_until(), "pairing_until": pairing_until(),
            "published": saved["published"], "devices": [devices.public(d) for d in devices.active()],
            "requests": pairing_requests(),
            "tailscale": {"installed": bool(info.get("installed")), "running": bool(info.get("running")),
                          "dns_name": str(info.get("dns_name") or ""), "login": str(info.get("login") or "")},
            "serve_command": tailscale.manual_command(), "url": f"https://{host_value}/" if host_value else ""}

# ---------------------------------------------------------------- the page (4.4)


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def render_remote_page(request, caller):
    """The page a remote caller gets: JARVIS with a fresh page token for a paired
    device's top-level load, else the pairing page."""
    if caller.kind == "app":
        dest = request.headers.get("sec-fetch-dest")
        site = request.headers.get("sec-fetch-site")
        if dest not in (None, "document") or site not in (None, "none", "same-origin"):
            # An <img>, an <iframe> or a sibling *.ts.net page: no token is ever minted for it.
            return _no_store(HTMLResponse(page.pairing_html("refused"), status_code=403))
        response = _no_store(HTMLResponse(page.index_html(_mint_token(caller.device_id), remote=True,
                                                          origin=caller.origin)))
        value = request.cookies.get(COOKIE, "")
        if _DEVICE_COOKIE.fullmatch(value) and value.startswith(caller.device_id + "."):
            response.headers.append("set-cookie", cookie_header(value))  # sliding 400 days
        return response
    if caller.kind == "unpaired":
        return _no_store(HTMLResponse(page.pairing_html("pair" if pairing_until() else "closed")))
    return _no_store(HTMLResponse(page.pairing_html("refused"), status_code=403))

# ---------------------------------------------------------------- the gate (4.1)


@dataclass
class _Ask:
    """What the gate reads of a request, taken once (the decision runs in a worker thread)."""
    method: str
    path: str
    port: object
    headers: dict
    cookies: dict
    query_token: str


@dataclass
class _Verdict:
    caller: Caller
    response: object = None   # a refusal, sent as is
    reason: str = ""
    route: str = "?"
    clear_cookie: bool = False
    quiet: bool = False       # no audit line (iOS icon probes)
    extra: dict = field(default_factory=dict)


def _ask(request) -> _Ask:
    headers: dict = {}
    for name, value in request.headers.items():  # repeated headers are joined: a list never passes
        headers[name] = f"{headers[name]},{value}" if name in headers else value
    server = request.scope.get("server") or ()
    return _Ask(method=request.method.upper(), path=request.scope.get("path", ""),
                port=server[1] if len(server) > 1 else None, headers=headers, cookies=dict(request.cookies),
                query_token=request.query_params.get("token", ""))


def _parse_ip(raw: str):
    raw = str(raw or "").strip()
    if not raw or "," in raw:
        return None
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


def _in_tailnet(ip) -> bool:
    return (ip.version == 4 and ip in TAILNET_V4) or (ip.version == 6 and ip in TAILNET_V6)


def _decode_login(value) -> str:
    if not value:
        return ""
    try:
        text = str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # noqa: BLE001 - a malformed header is no login
        return ""
    return text.strip().lower()


def _self_ips() -> set:
    from . import tailscale
    try:
        ips = (tailscale.self_info() or {}).get("ips") or []
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for raw in ips:
        ip = _parse_ip(raw)
        if ip is not None:
            out.add(str(ip))
    return out


def _allowed(method: str, path: str) -> tuple[set, str]:
    """The scopes REMOTE_ALLOW grants this (method, path), and the first template matching the path."""
    method = "GET" if method == "HEAD" else method
    scopes, first = set(), ""
    for methods, template, granted, regex in _ALLOW:
        if regex.match(path):
            first = first or template
            if method in methods:
                scopes |= granted
    return scopes, first


def _served_templates(app) -> list:
    with _lock:
        cached = _served.get(id(app))
    if cached is not None:
        return cached
    found = []

    def walk(routes, prefix):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                context = getattr(route, "include_context", None)
                walk(inner.routes, prefix + (getattr(context, "prefix", "") or ""))
            elif isinstance(route, Mount):
                found.append(prefix + route.path + "/{path:path}")
            elif getattr(route, "path", None):
                found.append(prefix + route.path)
    try:
        walk(getattr(app, "routes", []), "")
        compiled = [(t, compile_path(t)[0]) for t in found]
    except Exception:  # noqa: BLE001 - the audit falls back to "?"
        compiled = []
    with _lock:
        _served[id(app)] = compiled
    return compiled


def _template(app, path: str) -> str:
    """The route template the audit names: REMOTE_ALLOW's, else the served one, else '?'."""
    first = _allowed("GET", path)[1]
    if first:
        return first
    return next((t for t, regex in _served_templates(app) if regex.match(path)), "?")


def _refusal(status: int, text: str, where: str, state: str = "refused"):
    if where == "page":
        return HTMLResponse(page.pairing_html(state), status_code=status)
    if where == "siri":  # Siri reads it aloud
        return PlainTextResponse(text, status_code=status)
    return JSONResponse({"detail": text}, status_code=status)


def _bound_to_node(device: dict, ip: str, caller: Caller) -> bool:
    """A valid secret from an address of the device's own node (4.2): True. From
    another address: the node's other one (whois) is learnt; another machine
    means the secret was copied, and the device is revoked."""
    from . import audit
    if ip in device["ips"]:
        return True
    node = str(_whois(ip).get("node_id") or "")
    if node and device["node_id"] and node == device["node_id"]:
        devices.add_ip(device["id"], ip)
        return True
    who = Caller(kind=caller.kind, device_id=device["id"], key_id=caller.key_id, ip=ip, login=caller.login,
                 name=device["name"])
    if node and device["node_id"]:
        revoke_device(device["id"], by=who)
        audit.alert("secret_copied", f"Secret de « {device['name']} » utilisé depuis une autre machine : "
                                     "appareil retiré.", who)
    else:  # whois could not tell: refused, kept
        audit.alert("ip_change", f"« {device['name']} » utilisé depuis une autre machine : refusé.", who)
    return False


def _touch(caller: Caller) -> None:
    now = _now()
    with _lock:
        last = _touched.get((caller.kind, caller.key_id or caller.device_id), 0)
        due = now - last >= TOUCH_EVERY_S
        if due:
            _touched[(caller.kind, caller.key_id or caller.device_id)] = now
    if due:
        devices.touch(caller.device_id, caller.ip)
        if caller.key_id:
            devices.touch_siri_key(caller.key_id)


def _decide(ask: _Ask, app) -> _Verdict:
    """Steps 1 to 17 of 4.1, in order; the first failing check answers."""
    from . import audit
    path, method = ask.path, ask.method
    reading = method in ("GET", "HEAD")
    where = "page" if reading and path == "/" else "siri" if path == "/api/raccourci" else "api"
    route = _template(app, path)
    raw_ip = _parse_ip(ask.headers.get("x-forwarded-for", ""))
    login = _decode_login(ask.headers.get("tailscale-user-login"))
    base = Caller(kind="unpaired", ip=str(raw_ip) if raw_ip else "", login=login)

    def refuse(status, text, reason, state="refused", caller=None, clear=False):
        return _Verdict(caller=caller or base, response=_refusal(status, text, where, state), reason=reason,
                        route=route, clear_cookie=clear)

    # 1. Funnel: from the internet, whatever the switch says.
    if "tailscale-funnel-request" in ask.headers:
        audit.alert("funnel", "Requête refusée venant d'internet (Funnel) : vérifiez Tailscale.", base)
        return refuse(403, T_FUNNEL, "funnel")
    # 2. Only the Serve listener is a remote door.
    if ask.port != config.REMOTE_PORT:
        return refuse(403, T_PROXY, "proxy_on_pc_port")
    # 3. iOS probes these icons on its own: a quiet 404.
    if path in QUIET_PATHS:
        return _Verdict(caller=base, response=Response(status_code=404), route=route, quiet=True)
    saved = _settings()
    pair_asset = reading and path in PAIR_ASSETS
    # 4. Off (READY False counts as off).
    if not _enabled_of(saved) and not pair_asset:
        return refuse(403, T_OFF, "off", "off")
    # 5. Paused.
    until = _paused_of(saved)
    if until and not pair_asset:
        return refuse(403, T_PAUSED.format(until=_until_text(until)), "paused", "paused")
    # 6. The exact Serve name.
    expected = _host_of(saved)
    given = ask.headers.get("host", "").strip().lower()
    given = given.removesuffix(":443")
    if not expected or given != expected:
        return refuse(403, T_REFUSED, "host")
    # 7. Serve terminated TLS.
    if ask.headers.get("x-forwarded-proto") != "https":
        return refuse(403, T_REFUSED, "proto")
    # 8. One tailnet address, neither loopback nor this PC's own.
    ip_obj = _parse_ip(ask.headers.get("x-forwarded-for", ""))
    if ip_obj is not None and ip_obj.is_loopback:
        return refuse(403, T_REFUSED, "self")
    if ip_obj is None or not _in_tailnet(ip_obj):
        return refuse(403, T_REFUSED, "xff")
    ip = str(ip_obj)
    if ip in _self_ips():
        return refuse(403, T_REFUSED, "self")
    base = Caller(kind="unpaired", ip=ip, login=login)
    # 9. An allowed Tailscale login.
    if (not login or login not in _logins_of(saved)) and not pair_asset:
        known = _DEVICE_COOKIE.fullmatch(ask.cookies.get(COOKIE, ""))
        device = devices.get(known.group(1)) if known else None
        if device and not device["revoked"]:
            audit.alert("login_change", f"Compte Tailscale inattendu pour « {device['name']} » : refusé.", base,
                        device=device["id"])
        return refuse(403, T_LOGIN, "login")
    # 10. The exact origin.
    origin = ask.headers.get("origin")
    if origin is not None and origin != "https://" + expected:
        return refuse(403, T_REFUSED, "origin")
    # 11. Never from another site, not even a sibling *.ts.net one.
    site = {p.strip() for p in ask.headers.get("sec-fetch-site", "").split(",")}
    if site & {"cross-site", "same-site"} and (path.startswith("/api/") or where == "page"):
        return refuse(403, T_REFUSED, "site")
    # 12. Open static paths: no credential is read, nothing is counted.
    if reading and (path.startswith("/static/") or path == "/healthz"):
        return _Verdict(caller=base, route=route)
    # 13 and 14. The credential: a lock first, then who it is.
    clear = False
    caller = base
    if where == "siri":
        match = _SIRI_HEADER.fullmatch(ask.headers.get("authorization", ""))
        key_id = match.group(1) if match else ""
        if _is_locked(("siri-ip", ip)) or (key_id and _is_locked(("siri", key_id, ip))):
            return refuse(429, T_LOCKED, "locked", "locked")
        if not match or not (devices.siri_key_known(key_id) or devices.siri_key_revoked(key_id)):
            _fail(("siri-ip", ip), ip)
            return refuse(401, T_SIRI_UNKNOWN, "unpaired")
        if devices.siri_key_revoked(key_id):
            return refuse(401, T_SIRI_REFUSED, "revoked")
        found = devices.verify_siri_key(key_id, match.group(2))
        if found is None:
            owner = devices.siri_key_device(key_id)
            if owner and ip in owner["ips"]:
                _fail(("siri", key_id, ip), ip, owner["id"])
            else:
                _fail(("ip", ip), ip)
            return refuse(401, T_SIRI_REFUSED, "unpaired")
        device, _ = found
        who = Caller(kind="siri", device_id=device["id"], key_id=key_id, ip=ip, login=login, name=device["name"])
        if not _bound_to_node(device, ip, who):
            return refuse(403, T_MOVED, "ip", caller=who)
        if login != device["login"]:
            audit.alert("login_change", f"Compte Tailscale inattendu pour « {device['name']} » : refusé.", who)
            return refuse(403, T_MOVED, "login", caller=who)
        caller = who
    else:
        value = ask.cookies.get(COOKIE)
        match = _DEVICE_COOKIE.fullmatch(value or "")
        device_id = match.group(1) if match else ""
        pair_post = method == "POST" and path == "/api/remote/pair-request"
        if _is_locked(("ip", ip)) or (device_id and _is_locked(("cookie", device_id, ip))) \
                or (pair_post and _is_locked(("pair", ip))):
            return refuse(429, T_LOCKED, "locked", "locked")
        if value is not None:
            record = devices.get(device_id) if match else None
            if match and ((record and record["revoked"]) or (record is None and devices.is_revoked(device_id))):
                return refuse(401, T_REVOKED, "revoked", "revoked", clear=True)
            if record is None:  # malformed, or unknown and never revoked: cleared, never counted
                clear = True
                _note_unknown_cookie(ip)
            else:
                device = devices.verify(device_id, match.group(2))
                if device is None:  # a wrong secret for a known device: counted, still unpaired
                    if ip in record["ips"]:
                        _fail(("cookie", device_id, ip), ip, device_id)
                    else:
                        _fail(("ip", ip), ip)
                else:
                    who = Caller(kind="app", device_id=device["id"], ip=ip, login=login, name=device["name"])
                    if not _bound_to_node(device, ip, who):
                        return refuse(403, T_MOVED, "ip", caller=who)
                    if login != device["login"]:
                        audit.alert("login_change", f"Compte Tailscale inattendu pour « {device['name']} » : "
                                                    "refusé.", who)
                        return refuse(403, T_MOVED, "login", caller=who)
                    caller = who
        # The two open pairing routes answer a paired phone without acting (409,
        # "done"): its pairing page holds no page token, so none is asked there.
        open_route = (method, path) in _OPEN_API or (reading and ("GET", path) in _OPEN_API)
        if caller.kind == "app" and path.startswith("/api/") and not open_route:
            given = ask.headers.get("x-jarvis-token") or \
                (ask.query_token if reading and path == "/api/events" else "")
            if not _token_ok(caller.device_id, given):  # a restart forgets every token: not counted
                return refuse(401, T_PAGE_KEY, "token", caller=caller)
    # 15. The route table.
    scopes = _allowed(method, path)[0]
    scope = {"unpaired": OPEN, "app": APP, "siri": SIRI}.get(caller.kind, "")
    if scope not in scopes:
        if caller.kind == "unpaired" and APP in scopes:
            return refuse(401, T_UNPAIRED, "unpaired", clear=clear)
        return refuse(403, T_PC_ONLY, "scope", caller=caller, clear=clear)
    # 16. Rate limits.
    if caller.kind == "app":
        ok = _rate_ok((("app", caller.device_id), 3000, 600))
    elif caller.kind == "siri":
        ok = _rate_ok((("siri-min", caller.key_id), 6, 60), (("siri-day", caller.key_id), 60, 86400))
    elif path.startswith("/api/"):
        if path == "/api/remote/pair-request":
            ok = _rate_ok((("pair-request", ip), 5, 600))
        elif path == "/api/remote/pair-status":
            ok = _rate_ok((("pair-status", ip), 400, 600))
        else:
            ok = _rate_ok((("unpaired", ip), 60, 600))
    else:
        ok = True  # the page itself is never counted
    if not ok:
        return refuse(429, T_RATE, "rate", caller=caller, clear=clear)
    # 17. Admitted.
    if caller.kind in ("app", "siri"):
        _touch(caller)
    return _Verdict(caller=caller, route=route, clear_cookie=clear)


def _finish(ask: _Ask, verdict: _Verdict, response) -> None:
    """Step 18: the safe headers on every remote response, then its audit line."""
    from . import audit
    try:
        if ask.path == "/" or ask.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = CSP
        if verdict.clear_cookie:
            response.headers.append("set-cookie", cookie_header(""))
    except Exception:
        log.exception("JARVIS: en-têtes de réponse distante non posés")
    status = getattr(response, "status_code", 0)
    if verdict.quiet:
        return
    if status < 400 and ask.method in ("GET", "HEAD") and (
            ask.path.startswith("/static/") or ask.path == "/api/remote/pair-status"):
        return  # every page load and every 2 s poll would drown the rest
    audit.event(verdict.caller, "request", method=ask.method, route=verdict.route, status=status,
                reason=verdict.reason)


async def remote_guard(request, call_next):
    """The remote gate (4.1): a refusal, or the request with its caller stamped."""
    ask = _ask(request)
    try:
        verdict = await run_in_threadpool(_decide, ask, request.scope.get("app"))
    except Exception:  # fail closed
        log.exception("JARVIS: garde d'accès distant en échec")
        verdict = _Verdict(caller=_UNPAIRED, response=JSONResponse({"detail": T_REFUSED}, status_code=403),
                           reason="scope")
    if verdict.response is not None:
        response = verdict.response
    else:
        request.state.caller = verdict.caller
        response = await call_next(request)
    _finish(ask, verdict, response)
    return response
