"""Confirmation of risky actions: one server-side store of pending actions.

Every tool call goes through gate() before it runs. A risky call is parked
here and the model gets needs_confirmation instead:
- a full-access Claude task, or a full-access routine;
- once untrusted content entered the voice session (screen, clipboard, web
  news, notes, journal, a task result), a link to an unknown site or a
  clipboard write;
- a finished task's denied tools, which monsieur may approve (tasks.approve).

A parked action then runs with the arguments stored here, never new ones, and
only on a UI button (POST /api/pending/{id}/decide) or through confirm_action,
which counts only if monsieur spoke or typed in that voice session AFTER the
action was last asked: the model cannot confirm its own request. Pending actions
live in memory and expire after PENDING_TTL seconds.

Voice sessions are identified by an id minted with each ephemeral key
(new_session), so a confirmation is tied to the session that asked for it.
"""
import copy
import threading
import time
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, events, tasks

router = APIRouter()

MAX_SESSIONS = 20
DETAIL_MAX = 4000  # characters of the action shown on its card
KEEP_DECIDED_S = 600  # a decided request still answers "déjà traitée" this long

SESSIONS: dict = {}  # sid -> {created, last_turn, tainted, reasons}
# id -> {id, sid, name, args, summary, detail, created, expires, kind, task_id, state, result}
PENDING: dict = {}
_lock = threading.RLock()


class T:
    """French wording (the same as T.confirm in static/js/strings-fr.js)."""
    complet = "Confier à Claude, avec accès complet à vos fichiers et commandes : « {title} »."
    link = "Ouvrir {domain} ?"
    clipboard = "Remplacer le contenu du presse-papiers ?"
    complet_routine = "Programmer une routine {freq} avec accès complet : « {title} »."
    task_approval = "Claude demande l'autorisation d'utiliser {tool} pour « {title} »."
    refused = "Confirmation refusée : attendez la réponse de monsieur."
    expired = "Demande expirée : rien n'a été lancé."
    unknown = "Demande inconnue : rien n'a été lancé."
    decided = "Demande déjà traitée."
    cancelled = "Annulé, rien n'a été fait."
    bad_decision = "Décision invalide : répondez « oui » ou « non »."
    ask = ("Demande à monsieur, en une phrase, s'il confirme cette action et sa conséquence ; "
           "n'appelle confirm_action qu'après sa réponse.")


FREQ = {"none": "ponctuelle", "daily": "quotidienne", "weekdays": "en semaine",
        "weekly": "hebdomadaire"}


def _now() -> float:
    return time.time()  # tests move this clock


# ---------------------------------------------------------------- tool family (see tools.py)

TOOLS = [{
    "type": "function",
    "name": "confirm_action",
    "description": ("Give monsieur's answer to an action that returned needs_confirmation. "
                    "Call it only after he answered, never in the same response as the "
                    "question. 'oui' runs exactly the action that was asked; 'non' cancels it."),
    "parameters": {
        "type": "object",
        "properties": {
            "pending_id": {"type": "string",
                           "description": "The pending_id returned with needs_confirmation"},
            "decision": {"type": "string", "enum": ["oui", "non"]},
        },
        "required": ["pending_id", "decision"],
    },
}]
CLIENT_TOOLS: set = set()


def _confirm_action(a: dict, ctx) -> dict:
    out = decide(str(a.get("pending_id") or ""), a.get("decision"),
                 voice_session=getattr(ctx, "session_id", None), by_voice=True)
    if out.get("state") == "done":
        return out["result"]  # what the action itself answered
    return out


HANDLERS = {"confirm_action": _confirm_action}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""


# ---------------------------------------------------------------- voice sessions

def _session(sid: str) -> dict:
    """The session's record, created on first sight (caller holds _lock)."""
    record = SESSIONS.get(sid)
    if record is None:
        record = SESSIONS[sid] = {"created": _now(), "last_turn": 0.0, "tainted": False,
                                  "reasons": []}
        while len(SESSIONS) > MAX_SESSIONS:
            SESSIONS.pop(next(iter(SESSIONS)))
    return record


def new_session(continues: bool = False) -> str:
    """continues: the new session carries the last exchanges of the previous one
    (a reconnection, the 55-minute refresh): what JARVIS said there about
    outside content comes along, so its taint does too."""
    sid = uuid.uuid4().hex
    with _lock:
        previous = max(SESSIONS.values(), key=lambda r: r["created"], default=None)
        record = _session(sid)
        if continues and previous and previous["tainted"]:
            record["tainted"] = True
            record["reasons"] = list(previous["reasons"])
    return sid


def mark_turn(sid: str | None):
    """Monsieur spoke or typed in this session (a confirmation needs a fresh turn)."""
    if not sid:
        return
    with _lock:
        _session(sid)["last_turn"] = _now()


def mark_tainted(sid: str | None, reason: str):
    """Untrusted data (web page, screen, notes) entered this session."""
    if not sid:
        return
    with _lock:
        record = _session(sid)
        record["tainted"] = True
        reason = str(reason or "")[:80]
        if reason and reason not in record["reasons"]:
            record["reasons"] = (record["reasons"] + [reason])[-10:]


def is_tainted(sid: str | None) -> bool:
    with _lock:
        return bool(sid) and bool(SESSIONS.get(sid, {}).get("tainted"))


def after_tool(name: str, args: dict, ctx, out):
    """A tool brought outside content into the session: taint it (run_tool calls this)."""
    sid = getattr(ctx, "session_id", None)
    if not sid or not isinstance(out, dict) or out.get("ok") is False:
        return
    if name in ("look_at_screen", "recall"):
        mark_tainted(sid, name)
    elif name == "system_control" and args.get("action") == "read_clipboard":
        mark_tainted(sid, "presse-papiers")  # often copied from a web page
    elif name == "info" and "actu" in str(args.get("type") or args.get("kind") or "").lower():
        mark_tainted(sid, "actualités")
    elif name == "ares_lire" and _mentions_notes(args, out):
        mark_tainted(sid, "notes A.R.E.S")


def _mentions_notes(args: dict, out: dict) -> bool:
    return (any("note" in str(v).lower() for v in args.values())
            or any(k in ("note", "notes") for k in out))


# ---------------------------------------------------------------- the gate

def gate(name: str, args: dict, ctx) -> dict | None:
    """None lets the call run; a dict is returned to the model instead.

    Several calls in one response are gated one by one: each gets its own id.
    """
    sid = getattr(ctx, "session_id", None)
    rule = _rule(name, args or {}, sid)
    if rule is None:
        return None
    summary, detail = rule
    pending = _park(name, args or {}, sid, summary, detail)
    return {"status": "needs_confirmation", "pending_id": pending["id"], "summary": summary,
            "expires_in": max(0, round(pending["expires"] - _now())), "consigne": T.ask}


def _complet(profile) -> bool:
    # Loose on purpose: "Complet" is parked too (tasks would run it as lecture).
    return str(profile or "").strip().lower() == "complet"


def _rule(name: str, args: dict, sid):
    """(summary, detail) when this call needs monsieur's "oui"."""
    # The detail is what will really run: the whole prompt (scrollable on the
    # card), so nothing can hide behind a harmless title.
    if name == "delegate_to_claude" and config.CONFIRM_COMPLET and _complet(args.get("profile")):
        return (T.complet.format(title=_title(args.get("title"), args.get("prompt"))),
                _clip(args.get("prompt"), DETAIL_MAX))
    if (name == "schedule" and str(args.get("kind") or "").lower() == "task"
            and _complet(args.get("profile"))):
        freq = FREQ.get(str(args.get("repeat") or "none"), "ponctuelle")
        return (T.complet_routine.format(freq=freq, title=_title(args.get("title"), args.get("text"))),
                _clip(args.get("text"), DETAIL_MAX))
    if not is_tainted(sid):
        return None
    if name == "open_url":
        url = str(args.get("url") or "")
        domain = _domain(url)
        if not _allowed(domain):
            return T.link.format(domain=domain or "ce lien"), _clip(url, 300)
    if name == "system_control" and args.get("action") == "write_clipboard":
        return T.clipboard, _clip(args.get("value"), 300)
    return None


def _title(title, fallback) -> str:
    text = " ".join(str(title or "").split()) or " ".join(str(fallback or "").split())[:60]
    return text[:80] or "sans titre"


def _clip(text, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _domain(url: str) -> str:
    # A browser reads a backslash as '/' in a web address: evil.example\@youtube.com
    # goes to evil.example, where urlsplit alone would answer youtube.com.
    text = url.strip().replace("\\", "/")
    if "://" not in text and ":" not in text.split("/", 1)[0]:
        text = "https://" + text  # 'youtube.com/x' is opened as https
    try:
        return (urlsplit(text).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _allowed(domain: str) -> bool:
    """Domains in OPEN_URL_ALLOW (and their subdomains) open without asking."""
    allow = [d.strip().lower().lstrip(".") for d in (config.OPEN_URL_ALLOW or "").split(",") if d.strip()]
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in allow)


# ---------------------------------------------------------------- the store

def public(p: dict) -> dict:
    """What the page sees (never the session id; the arguments only as the detail)."""
    keep = ("id", "name", "kind", "summary", "detail", "created", "expires", "task_id", "state", "result")
    out = {k: p.get(k) for k in keep}
    out["expires_in"] = max(0, round(p["expires"] - _now())) if p["state"] == "pending" else 0
    return out


def _park(name: str, args: dict, sid, summary: str, detail: str, kind: str = "tool",
          task_id: str | None = None) -> dict:
    now = _now()
    with _lock:
        expired = _sweep(now)
        same = next((p for p in PENDING.values() if p["state"] == "pending" and p["sid"] == sid
                     and p["name"] == name and p["args"] == args), None)
        if same is None:  # the same request asked twice gets one card
            same = {"id": uuid.uuid4().hex[:10], "sid": sid, "name": name, "args": copy.deepcopy(args),
                    "summary": summary, "detail": detail, "created": now, "asked": now,
                    "expires": now + max(5, config.PENDING_TTL), "kind": kind, "task_id": task_id,
                    "state": "pending", "result": None}
            PENDING[same["id"]] = same
            fresh = True
        else:  # asked again: the "oui" must come after this latest question
            same["asked"] = now
            fresh = False
    _announce(expired)
    if fresh:
        _publish(same)
        timer = threading.Timer(max(0.0, same["expires"] - _now()) + 0.25, expire_due)
        timer.daemon = True
        timer.start()
    return same


def _sweep(now: float) -> list:
    """Expire what is due and forget old decisions (caller holds _lock)."""
    expired = []
    for pid, p in list(PENDING.items()):
        if p["state"] == "pending" and now >= p["expires"]:
            p["state"] = "expired"
            p["decided"] = now
            expired.append(p)
        elif p["state"] != "pending" and now - p.get("decided", now) > KEEP_DECIDED_S:
            PENDING.pop(pid, None)
    return expired


def expire_due():
    """Publish the requests that just expired (their cards say so)."""
    with _lock:
        expired = _sweep(_now())
    _announce(expired)


def _announce(items: list):
    for p in items:
        _publish(p)


def _publish(p: dict):
    events.publish("pending", {"pending": public(p)})
    if p["kind"] == "task_approval":
        _show_on_task(p)


def open_items() -> list:
    with _lock:
        expired = _sweep(_now())
        items = [public(p) for p in PENDING.values() if p["state"] == "pending"]
    _announce(expired)
    return sorted(items, key=lambda p: p["created"])


def decide(pending_id: str, decision, voice_session: str | None = None, by_voice: bool = False) -> dict:
    """Run ('oui') or drop ('non') a parked action.

    From a UI button any open request can be decided. By voice, the request
    must come from the same session and, for 'oui', monsieur must have spoken
    or typed after it was last asked (asking again needs a new answer).
    """
    decision = str(decision or "").strip().lower()
    if decision not in ("oui", "non"):
        return {"ok": False, "error": T.bad_decision}
    with _lock:
        expired = _sweep(_now())
        p = PENDING.get(pending_id)
        error, state = None, None
        if p is None:
            error = T.unknown
        elif p["state"] == "expired":
            error, state = T.expired, "expired"
        elif p["state"] != "pending":
            error, state = T.decided, p["state"]
        elif by_voice and (not voice_session or p["sid"] != voice_session):
            error = T.refused
        elif (by_voice and decision == "oui"
              and not SESSIONS.get(voice_session, {}).get("last_turn", 0) > p.get("asked", p["created"])):
            error = T.refused
        else:  # claim it now: a second click or call can't run it twice
            p["state"] = "running" if decision == "oui" else "cancelled"
            p["decided"] = _now()
    _announce(expired)
    if error:
        out = {"ok": False, "error": error}
        if state:
            out["state"] = state
        return out
    if decision == "non":
        p["result"] = {"ok": True, "message": T.cancelled}
        _publish(p)
        return {"ok": True, "state": "cancelled", "message": T.cancelled}
    result = _execute(p)
    with _lock:
        p["state"] = "error" if result.get("ok") is False else "done"
        p["result"] = result
        p["decided"] = _now()
    _publish(p)
    return {"ok": p["state"] == "done", "state": p["state"], "result": result}


def _execute(p: dict) -> dict:
    """The stored action with its stored arguments, past the gate."""
    from . import tools  # tools imports this module

    try:
        if p["kind"] == "task_approval":
            task = tasks.approve(p["task_id"])
            return {"status": "started", "task_id": task["id"], "profile": task["profile"]}
        handler = tools.handlers().get(p["name"])
        if handler is None:
            return {"ok": False, "error": f"Outil indisponible : {p['name']}"}
        out = handler(copy.deepcopy(p["args"]), tools.ToolCtx(session_id=p["sid"]))
        return out if isinstance(out, dict) else {"ok": True}
    except Exception as exc:  # noqa: BLE001 - said to monsieur, like any tool error
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------- denied tools of a task

def _on_task_finished(task: dict):
    """Claude was refused some tools: offer monsieur to approve them and resume."""
    denials = task.get("permission_denials") or []
    if (not denials or not task.get("session_id") or task.get("sandbox_violation")
            or task["status"] not in ("done", "error")):
        return
    names = sorted({str(d.get("tool")) for d in denials if d.get("tool")})
    summary = T.task_approval.format(tool=", ".join(names), title=task["title"])
    detail = "\n".join(f"- {d.get('detail') or d.get('tool')}" for d in denials[:5])
    _park("approve_task", {"task_id": task["id"]}, task.get("voice_session"), summary, detail,
          kind="task_approval", task_id=task["id"])


def _show_on_task(p: dict):
    """The task card shows its approval request while it is open."""
    task = tasks.TASKS.get(p.get("task_id") or "")
    if not task:
        return
    if p["state"] == "pending":
        task["approval"] = p["id"]
    elif task.get("approval") == p["id"]:
        task.pop("approval", None)
    else:
        return
    events.publish("task", tasks.public(task))


tasks.ON_FINISH.append(_on_task_finished)


# ---------------------------------------------------------------- routes

class TurnIn(BaseModel):
    session_id: str | None = None


class TaintIn(BaseModel):
    session_id: str | None = None
    reason: str = ""


class DecideIn(BaseModel):
    decision: str = ""


@router.post("/api/voice/turn")
def voice_turn(body: TurnIn):
    """The page heard monsieur (speech_stopped) or he typed: a fresh turn."""
    mark_turn(body.session_id)
    return {"ok": bool(body.session_id)}


@router.post("/api/voice/taint")
def voice_taint(body: TaintIn):
    """The page handed outside text to the model (voice.sendData)."""
    mark_tainted(body.session_id, body.reason or "données")
    return {"ok": bool(body.session_id)}


@router.get("/api/pending")
def list_pending():
    return open_items()


@router.post("/api/pending/{pending_id}/decide")
def decide_route(pending_id: str, body: DecideIn):
    """[Lancer] / [Annuler] on a confirmation card."""
    out = decide(pending_id, body.decision)
    if out.get("error") == T.unknown:
        raise HTTPException(404, T.unknown)
    return out
