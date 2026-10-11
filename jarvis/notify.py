"""ntfy notifications on the iPhone: a word that something happened, never what.

- The topic in data/ntfy.json is the only address of monsieur's phone on the
  ntfy server: "jarvis-" + 192 random bits, made at the first GET /api/notify
  and renewed from the PC only. It is never written to .env, a log, an audit
  line or an error text: whoever knows it reads the notifications.
- events.HOOKS: a task that ends, a reminder, a confirmation waiting, each
  with a fixed sentence (MESSAGES below). audit.ALERT_HOOKS: security alerts,
  with the kind's fixed sentence. No task output, prompt, title, device name,
  IP or login ever leaves the PC this way; only the reminder's own text, and
  only when monsieur asked for it.
- siri_late(): Siri promised « je vous préviens sur l'iPhone » and answered
  after its deadline; a fixed sentence says the answer is ready.
- Quiet hours, « Ne pas déranger » and "only when I'm away" hold back tasks
  and confirmations ("only when away" just the PC's own: the PC never speaks
  a result from the iPhone or Siri); reminders and security alerts always
  go. A task result held back by quiet hours is counted and told in one
  message once they end (a confirmation expires long before: it is not).
  A task's status is told once.
- Tasks and confirmations: one message per kind every RATE_S seconds; what
  the window refuses is counted, never dropped, and told in one message when
  it closes (« JARVIS : 2 tâches terminées. »). Reminders, Siri's late answer
  and security alerts are never rate-limited (audit.py already deduplicates
  alerts, and an alert must never be dropped).
- Sending: POST {server}/{topic} on a daemon thread, with no redirect and no
  click URL (a notification with a link never comes from JARVIS); tried again
  after RETRY_S on a timeout, a connection error or a 5xx. A failure is kept
  in last_error, without the topic, and never reaches events.publish.
"""
import logging
import re
import secrets
import threading
import time
from collections import Counter

import httpx
from fastapi import APIRouter, HTTPException, Request

from . import config, desktop, events, inbox, settings, store

router = APIRouter()
log = logging.getLogger(__name__)

TRANSPORT = None  # an httpx transport (tests)
NTFY_FILE = "ntfy.json"
TOPIC_PREFIX = "jarvis-"
_TOPIC_SHAPE = re.compile(r"jarvis-[A-Za-z0-9_-]{20,64}")  # never a '/' or '..' into the URL
_TOPIC_IN_TEXT = re.compile(r"jarvis-[A-Za-z0-9_-]{20,64}")
RATE_S = 10            # one task or confirmation message at most this often (the rest are counted)
HELD_CHECK_S = 60      # task results held back by quiet hours look for their end this often
RETRY_S = (2, 10)      # a message that failed on the way is tried again after these delays
AWAY_IDLE_S = 600      # no keyboard or mouse for this long: monsieur is not at the PC
TIMEOUT_S = 5
REMINDER_TEXT_MAX = 60
MAX_SEEN = 2000        # task statuses and confirmations already told

# The title header is plain ASCII; the bodies are UTF-8. Fixed sentences only.
TITLE = "JARVIS"
TASK_DONE = "JARVIS : tâche terminée."
TASK_FAILED = "JARVIS : une tâche n'a pas abouti."
TASKS_DONE = "JARVIS : {n} tâches terminées."
TASKS_FAILED = "JARVIS : {n} tâches finies, au moins une n'a pas abouti."
REMINDER = "JARVIS : un rappel."
REMINDER_WITH_TEXT = "JARVIS · rappel : {text}"
PENDING = "JARVIS : une confirmation vous attend."
PENDINGS = "JARVIS : {n} confirmations vous attendent."
ALERT = "JARVIS · sécurité : {text}"
ALERT_UNKNOWN = "alerte sur le PC : vérifiez Réglages › Accès à distance."
TEST = "JARVIS : notification de test."
SIRI_READY = "JARVIS : la réponse de Siri est prête, redemandez-la à Siri dans les 5 minutes."
SIRI_FAILED = "JARVIS : Siri n'a pas pu terminer, réessayez."
_FAILED = ("error", "interrompue")
# A reminder's own text never carries a link either.
_LINK = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S*")

ERR_OFF = "Notifications coupées : activez-les d'abord dans Réglages › Notifications, sur le PC."
ERR_NO_TOPIC = "Pas encore de sujet : ouvrez Réglages › Notifications."
ERR_RATE = "Une notification vient de partir : réessayez dans quelques secondes."
ERR_TIMEOUT = "Le serveur ntfy ne répond pas (délai dépassé)."
ERR_CONNECT = "Serveur ntfy injoignable : vérifiez la connexion du PC."
ERR_SEND = "Envoi vers ntfy impossible."
ERR_STATUS = "Le serveur ntfy a refusé l'envoi (code {code})."
ERR_SAVE = "Réglage non enregistré : le dossier des données n'est pas modifiable (disque plein ou protégé)."

_lock = threading.Lock()
_topic_lock = threading.Lock()
_last: dict = {}       # kind -> time.monotonic() of its last message
_seen: dict = {}       # (task id, status) or ("pending", id) -> None, oldest first
_status = {"last_error": "", "last_sent": 0.0}
_workers: list = []    # sending threads still running (tests wait for them)
_later: dict = {}      # kind -> Counter of what its window refused, told when it closes
_timers: dict = {}     # kind -> the threading.Timer that tells _later[kind]
_held = {"pc": Counter(), "other": Counter()}  # task outcomes held back by quiet hours, by origin
_held_timer: list = []  # the threading.Timer looking for the end of quiet hours (at most one)


class _HideTopic(logging.Filter):
    """httpx logs every request's URL at INFO: the topic is cut out of it."""

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(_TOPIC_IN_TEXT.sub(TOPIC_PREFIX + "…", str(a)) if TOPIC_PREFIX in str(a) else a
                                for a in record.args)
        return True


_HIDE_TOPIC = _HideTopic()


class _NotSent(Exception):
    """Nothing went out: args are (French text for last_error, reason code for the log)."""


# ---------------------------------------------------------------- the topic

def _stored() -> str:
    data = store.load(NTFY_FILE, {})
    value = data.get("topic") if isinstance(data, dict) else None
    return value if isinstance(value, str) and _TOPIC_SHAPE.fullmatch(value) else ""


def _make() -> str:
    value = TOPIC_PREFIX + secrets.token_urlsafe(24)
    store.save(NTFY_FILE, {"topic": value, "created": time.time()})
    return value


def topic(create: bool = True) -> str:
    """The topic; made on first need when create (a hand-edited file of the
    wrong shape counts as none). '' when there is none and create is False."""
    with _topic_lock, store.LOCK:
        value = _stored()
        if value or not create:
            return value
        return _make()


def renew_topic() -> str:
    """A new topic: the old one's subscribers hear nothing more."""
    with _topic_lock, store.LOCK:
        return _make()

# ---------------------------------------------------------------- when to tell

def at_pc() -> bool:
    """Monsieur at the PC: a JARVIS page open, Windows not saying he's away or
    busy, and some input in the last AWAY_IDLE_S seconds (when that is known)."""
    if events.leader() is None:
        return False
    try:
        if desktop.attention_state() != "ok":
            return False
    except Exception:  # noqa: BLE001 - unknown counts as present, as in inbox.py
        pass
    try:
        idle = desktop.idle_seconds()
    except Exception:  # noqa: BLE001 - unknown: the other two signs decide
        idle = None
    return idle is None or idle < AWAY_IDLE_S


def _quiet() -> bool:
    """Quiet hours or « Ne pas déranger »."""
    try:
        return bool(inbox.is_quiet())
    except Exception:  # noqa: BLE001 - a broken state file must not silence the phone
        return False


def _pc_tells_it(via=None) -> bool:
    """"Only when away" while he's here. It holds back only what the PC tells
    itself: a result from the iPhone or Siri is never spoken on the PC (spec
    4.12), so the phone is its only way to monsieur."""
    if str(via or "pc") != "pc":
        return False
    return bool(config.NTFY_ONLY_AWAY) and at_pc()


def _first(key) -> bool:
    """True the first time this task status or confirmation is seen."""
    with _lock:
        if key in _seen:
            return False
        _seen[key] = None
        for old in list(_seen)[:max(0, len(_seen) - MAX_SEEN)]:
            del _seen[old]
        return True


def _reminder_body(data: dict) -> str:
    if not config.NTFY_REMINDER_TEXT:
        return REMINDER
    text = " ".join(_LINK.sub("(lien)", str(data.get("text") or data.get("title") or "")).split())
    if not text:
        return REMINDER
    if len(text) > REMINDER_TEXT_MAX:
        text = text[:REMINDER_TEXT_MAX - 1].rstrip() + "…"
    return REMINDER_WITH_TEXT.format(text=text)


def message_for(kind: str, data) -> tuple | None:
    """For an event worth a notification: ("reminder", body, priority), or
    ("task", "done" or "failed", via), or ("pending", "pending", via), seen
    for the first time. Else None."""
    if not isinstance(data, dict):
        return None
    if kind == "warning" and data.get("kind") == "remote":
        return None  # audit.alert's own warning: the alert hook tells it, once
    if kind == "task":
        status, task_id = data.get("status"), str(data.get("id") or "")
        outcome = "done" if status == "done" else "failed" if status in _FAILED else ""
        if not outcome or not task_id or not _first((task_id, status)):
            return None
        return ("task", outcome, str(data.get("via") or "pc"))
    if kind == "reminder":  # monsieur set it: quiet hours or not
        return ("reminder", _reminder_body(data), "high")
    if kind == "pending":
        p = data.get("pending")
        if not isinstance(p, dict) or p.get("state") != "pending" or not p.get("id"):
            return None
        if str(p.get("via") or "pc").startswith("app:"):
            return None  # raised on the phone: its card is on its screen already
        if not _first(("pending", str(p["id"]))):
            return None
        return ("pending", "pending", str(p.get("via") or "pc"))
    return None


def _body(kind: str, counts: Counter) -> str:
    """One fixed sentence for what was counted (failure wins over done)."""
    if kind == "task":
        n, failed = sum(counts.values()), counts["failed"]
        if n <= 1:
            return TASK_FAILED if failed else TASK_DONE
        return (TASKS_FAILED if failed else TASKS_DONE).format(n=n)
    n = counts["pending"]
    return PENDING if n <= 1 else PENDINGS.format(n=n)


def _tell(kind: str, outcome: str, via: str) -> None:
    """A task result or a new confirmation, by the rules: quiet hours hold a
    task result until they end (a confirmation expires long before), "only
    when away" leaves the PC's own to the PC, and the window counts the rest."""
    if _quiet():
        if kind == "task":
            _hold(outcome, via)
        return
    if _pc_tells_it(via):
        return
    counts = Counter({outcome: 1})
    if kind == "task":
        counts += _take_held()
    _count(kind, counts)

# ---------------------------------------------------------------- the hooks

def _on_event(kind: str, data) -> None:
    """events.HOOKS: never raises into events.publish."""
    if not config.NTFY:
        return
    try:
        found = message_for(kind, data)
        if not found:
            return
        if found[0] == "reminder":
            _queue(*found)
        else:
            _tell(*found)
    except Exception:  # noqa: BLE001 - a notification must never break a publish
        log.warning("JARVIS: notification ntfy ignorée (%s)", kind)


def _alert_text(kind: str, ntfy_text) -> str:
    """Only a fixed sentence of audit.ALERTS ever goes out: the one given when
    it is one of them, else the kind's own, else a generic one."""
    from . import audit  # inside a function (spec section 0)
    fixed = {a.get("ntfy_text") for a in audit.ALERTS.values() if a.get("ntfy") and a.get("ntfy_text")}
    if isinstance(ntfy_text, str) and ntfy_text in fixed:
        return ntfy_text
    own = (audit.ALERTS.get(kind) or {}).get("ntfy_text")
    return own if own in fixed else ALERT_UNKNOWN


def _on_alert(kind: str, ntfy_text: str) -> None:
    """audit.ALERT_HOOKS: always sent, never rate-limited."""
    if not config.NTFY:
        return
    try:
        _queue("alert", ALERT.format(text=_alert_text(kind, ntfy_text)), "high")
    except Exception:  # noqa: BLE001 - audit.alert carries on regardless
        log.warning("JARVIS: alerte ntfy non transmise (%s)", kind)


def siri_late(ok: bool) -> bool:
    """raccourci.py: Siri said « je vous préviens sur l'iPhone » and its answer
    came after the deadline. A fixed sentence (the answer itself stays in the
    conversation, never in a notification); monsieur just asked, so quiet
    hours and "only when away" do not hold it back. False when nothing went."""
    if not config.NTFY:
        return False
    try:  # never rate-limited: Siri itself allows a key 6 questions a minute
        return _queue("siri", SIRI_READY if ok else SIRI_FAILED, "default")
    except Exception:  # noqa: BLE001 - Siri's worker carries on regardless
        log.warning("JARVIS: notification ntfy ignorée (siri)")
        return False

# ---------------------------------------------------------------- sending

def _allowed(kind: str) -> bool:
    """One message per kind every RATE_S seconds (caller holds _lock)."""
    now = time.monotonic()
    last = _last.get(kind)
    if last is not None and now - last < RATE_S:
        return False
    _last[kind] = now
    return True


def _start(body: str, priority: str) -> None:
    """Send on a daemon thread (caller holds _lock: _drain never finds a
    thread not yet started)."""
    worker = threading.Thread(target=_deliver, args=(body, priority),
                              kwargs={"retries": tuple(RETRY_S)}, daemon=True, name="jarvis-ntfy")
    _workers[:] = [w for w in _workers if w.is_alive()]
    _workers.append(worker)
    worker.start()


def _queue(kind: str, body: str, priority: str) -> bool:
    """A reminder, Siri's late answer or a security alert: sent now, never
    rate-limited."""
    with _lock:
        _start(body, priority)
    return True


def _count(kind: str, counts: Counter) -> None:
    """Tasks and confirmations: one message per kind every RATE_S seconds.
    What the window refuses is counted and told in one message when it
    closes: never dropped."""
    with _lock:
        waiting = _later.get(kind)
        if waiting is not None:  # a message is already due: this joins it
            waiting.update(counts)
            return
        if _allowed(kind):
            _start(_body(kind, counts), "default")
            return
        _later[kind] = Counter(counts)
        delay = max(0.0, RATE_S - (time.monotonic() - _last.get(kind, 0.0)))
        timer = threading.Timer(delay, _tell_later, args=(kind,))
        timer.daemon = True
        _timers[kind] = timer
        timer.start()


def _tell_later(kind: str) -> None:
    """The window closed: what it refused goes out in one message."""
    with _lock:
        _timers.pop(kind, None)
        counts = _later.pop(kind, None)
        if not counts or not config.NTFY:
            return
        _last[kind] = time.monotonic()
        _start(_body(kind, counts), "default")


def _hold(outcome: str, via: str) -> None:
    """A task result during quiet hours: counted, told once they end."""
    with _lock:
        _held["pc" if via == "pc" else "other"][outcome] += 1
        if not _held_timer:
            _arm_held()


def _arm_held() -> None:
    """Caller holds _lock."""
    timer = threading.Timer(HELD_CHECK_S, _check_held)
    timer.daemon = True
    _held_timer[:] = [timer]
    timer.start()


def _take_held() -> Counter:
    """The task results quiet hours held back (the PC's own only when the PC
    would not tell them itself), forgotten here."""
    with _lock:
        pc, other = Counter(_held["pc"]), Counter(_held["other"])
        _held["pc"].clear()
        _held["other"].clear()
    if pc and _pc_tells_it("pc"):
        pc = Counter()
    return other + pc


def _check_held() -> None:
    """Every HELD_CHECK_S while something is held: once quiet hours end, it
    goes out in one message."""
    with _lock:
        _held_timer.clear()
    if not config.NTFY:
        _take_held()
        return
    if _quiet():
        with _lock:
            if (_held["pc"] or _held["other"]) and not _held_timer:
                _arm_held()
        return
    counts = _take_held()
    if counts:
        _count("task", counts)


def _url(create: bool) -> str:
    try:
        server = settings.validate(settings.BY_KEY["ntfy_server"], config.NTFY_SERVER)
    except settings.SettingError as exc:  # a hand-edited .env: refused here too
        raise _NotSent(str(exc), "server") from None
    value = topic(create=create)
    if not value:
        raise _NotSent(ERR_NO_TOPIC, "none")
    return f"{server.rstrip('/')}/{value}"


def _post(body: str, priority: str, create: bool) -> tuple:
    """One POST: (French error, reason code, worth trying again)."""
    try:
        url = _url(create)
        headers = {"Title": TITLE, "Priority": priority, "Tags": "robot",
                   "Content-Type": "text/plain; charset=utf-8"}
        with httpx.Client(timeout=TIMEOUT_S, transport=TRANSPORT, follow_redirects=False) as client:
            r = client.post(url, content=body.encode("utf-8"), headers=headers)
        if not 200 <= r.status_code < 300:  # a redirect included: never followed
            return ERR_STATUS.format(code=r.status_code), f"http {r.status_code}", r.status_code >= 500
    except _NotSent as exc:
        return (*exc.args, False)
    except httpx.TimeoutException:
        return ERR_TIMEOUT, "timeout", True
    except httpx.ConnectError:
        return ERR_CONNECT, "connect", True
    except Exception:  # noqa: BLE001 - whatever it was, the caller only learns that it failed
        return ERR_SEND, "send", False
    return "", "", False


def _deliver(body: str, priority: str, create: bool = False, retries: tuple = ()) -> tuple:
    """POST one message, again after each delay of retries while it fails on
    the way (a timeout, no connection, a 5xx; never a refusal); (ok, French
    error). Never raises, and no error text ever comes from the exception:
    httpx's may hold the URL."""
    error, reason, again = _post(body, priority, create)
    for delay in retries:
        if not (error and again):
            break
        log.warning("JARVIS: notification ntfy non envoyée (%s), nouvel essai", reason)
        time.sleep(delay)
        if not config.NTFY:
            break
        error, reason, again = _post(body, priority, create)
    with _lock:
        if error:
            _status["last_error"] = error
        else:
            _status.update(last_error="", last_sent=time.time())
    if error:
        log.warning("JARVIS: notification ntfy non envoyée (%s)", reason)
    return not error, error


def _drain(timeout: float = 5.0) -> None:
    """Tests: wait for the windows' counted messages and the sending threads."""
    end = time.monotonic() + timeout
    with _lock:
        timers = list(_timers.values())
    for timer in timers:
        timer.join(max(0.0, end - time.monotonic()))
    with _lock:
        workers = list(_workers)
    for worker in workers:
        worker.join(max(0.0, end - time.monotonic()))


def reset_memory() -> None:
    """Tests: forget the rate windows, what was told, counted or held, and the last result."""
    with _lock:
        for timer in [*_timers.values(), *_held_timer]:
            timer.cancel()
        _timers.clear()
        _held_timer.clear()
        _later.clear()
        _held["pc"].clear()
        _held["other"].clear()
        _last.clear()
        _seen.clear()
        _status.update(last_error="", last_sent=0.0)


def _install() -> None:
    from . import audit  # inside a function (spec section 0)
    if _on_event not in events.HOOKS:
        events.HOOKS.append(_on_event)
    if _on_alert not in audit.ALERT_HOOKS:
        audit.ALERT_HOOKS.append(_on_alert)
    httpx_log = logging.getLogger("httpx")
    if _HIDE_TOPIC not in httpx_log.filters:
        httpx_log.addFilter(_HIDE_TOPIC)


_install()

# ---------------------------------------------------------------- routes

@router.get("/api/notify")
def get_notify():
    """The PC's and the paired iPhone's view (the topic is made on first look)."""
    try:
        value = topic()
    except OSError:
        raise HTTPException(500, ERR_SAVE) from None
    with _lock:
        status = dict(_status)
    return {"enabled": bool(config.NTFY), "server": str(config.NTFY_SERVER or ""), "topic": value,
            "only_away": bool(config.NTFY_ONLY_AWAY), "reminder_text": bool(config.NTFY_REMINDER_TEXT),
            "last_error": status["last_error"], "last_sent": status["last_sent"]}


@router.post("/api/notify/test")
def post_test():
    """« JARVIS : notification de test. », sent now; {ok, error?}."""
    if not config.NTFY:
        return {"ok": False, "error": ERR_OFF}
    with _lock:
        if not _allowed("test"):
            return {"ok": False, "error": ERR_RATE}
    ok, error = _deliver(TEST, "default", create=True)
    return {"ok": True} if ok else {"ok": False, "error": error}


@router.post("/api/notify/topic")
def post_topic(request: Request):
    """A new topic (PC only: the remote table refuses it too)."""
    from . import remote  # inside a function (spec section 0)
    if remote.caller_of(request).remote:
        raise HTTPException(403, "Réservé au PC.")
    try:
        return {"topic": renew_topic()}
    except OSError:
        raise HTTPException(500, ERR_SAVE) from None
