"""Confirmation of risky actions: one server-side store of pending actions.

Every tool call goes through gate() before it runs. A risky call is parked
here and the model gets needs_confirmation instead:
- a full-access Claude task, or a full-access routine;
- once untrusted content entered the voice session (screen, clipboard, web
  news, notes, journal, a task result), a link to an unknown site, a
  clipboard write, a write into A.R.E.S (a hostile note must not fill
  monsieur's organiser), a web research (its prompt leaves the PC) or a fact
  to remember (it would steer every later task);
- from the iPhone, an action on the PC (volume, music, lock) or an opted-in
  full-access task: those wait for the [Lancer] button of the phone that asked;
- a finished task's denied tools, which monsieur may approve (tasks.approve).

Some calls are refused outright, never parked: full access from a phone
without the PC's opt-in or after outside content, a full-access routine from
anything but the PC, and anything that would need a card from Siri (no
screen to show it on).

A parked action then runs with the arguments stored here, never new ones, and
only on a UI button (POST /api/pending/{id}/decide) or through confirm_action,
which counts only if monsieur spoke or typed in that voice session AFTER the
action was last asked: the model cannot confirm its own request. Pending actions
live in memory and expire after PENDING_TTL seconds.

Voice sessions are identified by an id minted with each ephemeral key
(new_session), so a confirmation is tied to the session that asked for it.
Each session belongs to the origin that opened it (remote.Caller.origin) and
is evicted only among that origin's sessions; an evicted session that was
tainted stays tainted (FORGOTTEN).
"""
import copy
import threading
import time
import uuid
from collections import OrderedDict
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config, events, tasks

router = APIRouter()

MAX_SESSIONS = 50          # the PC's voice sessions
MAX_REMOTE_SESSIONS = 8    # per remote origin (one phone, one Siri key)
MAX_FORGOTTEN = 1000       # evicted tainted sessions remembered as tainted
DETAIL_MAX = 4000  # characters of the action shown on its card
KEEP_DECIDED_S = 600  # a decided request still answers "déjà traitée" this long

SESSIONS: dict = {}  # sid -> {created, last_turn, tainted, reasons, origin}
# sid -> reasons, for sessions evicted while tainted (oldest first).
FORGOTTEN: OrderedDict = OrderedDict()
# id -> {id, sid, name, args, summary, detail, created, asked, expires, kind, task_id, state, result,
#        via, button_only, remote_kind, tainted}
PENDING: dict = {}
_lock = threading.RLock()


class T:
    """French wording (the same as T.confirm in static/js/strings-fr.js)."""
    complet = "Confier à Claude, avec accès complet à vos fichiers et commandes : « {title} »."
    link = "Ouvrir {domain} ?"
    clipboard = "Remplacer le contenu du presse-papiers ?"
    complet_routine = "Programmer une routine {freq} avec accès complet : « {title} »."
    task_approval = "Claude demande l'autorisation d'utiliser {tool} pour « {title} »."
    ares_write = "Écrire dans A.R.E.S : {what} ?"
    refused = "Confirmation refusée : attendez la réponse de monsieur."
    expired = "Demande expirée : rien n'a été lancé."
    unknown = "Demande inconnue : rien n'a été lancé."
    decided = "Demande déjà traitée."
    cancelled = "Annulé, rien n'a été fait."
    bad_decision = "Décision invalide : répondez « oui » ou « non »."
    ask = ("Demande à monsieur, en une phrase, s'il confirme cette action et sa conséquence ; "
           "n'appelle confirm_action qu'après sa réponse.")
    # From another device than the PC (spec 4.11).
    complet_remote = "Depuis l'iPhone, confier à Claude avec accès complet : « {title} »."
    complet_closed = ("Accès complet fermé depuis l'iPhone : autorisez-le sur le PC "
                      "(Réglages › Accès à distance).")
    complet_tainted = ("Accès complet refusé : cette conversation contient des données externes. "
                       "Recommencez dans une nouvelle conversation.")
    complet_device = "Accès complet refusé depuis cet appareil."
    complet_routine_pc = "Une routine avec accès complet se programme sur le PC."
    complet_alert = "Accès complet lancé depuis l'iPhone : « {title} »."
    alert_failed = "Alerte du PC impossible : rien n'a été lancé."
    pc_action = "Agir sur le PC à distance : {what} ?"
    research = ("Recherche web après des données externes : « {title} ». "
                "Vérifiez la consigne, elle part sur internet.")
    remember = "Retenir une information après des données externes : « {fact} » ?"
    siri = "Cette action demande une confirmation : ouvrez JARVIS sur l'iPhone."
    button_only = "Cette action se lance avec le bouton Lancer, sur l'iPhone qui l'a demandée."
    other_device = "Cette demande vient d'un autre appareil : elle se lance là-bas."
    from_phone = "Cette demande se lance depuis l'iPhone qui l'a faite."
    session_unknown = "Session inconnue : rouvrez la conversation."
    session_expired = "Session expirée : rouvrez la conversation."
    session_foreign = "Session d'un autre appareil."


FREQ = {"none": "ponctuelle", "daily": "quotidienne", "weekdays": "en semaine",
        "weekly": "hebdomadaire", "monthly": "mensuelle", "days": "certains jours"}

# What a remote PC action does, in the card's words (keys: tools.REMOTE_PC_ACTIONS).
ACTION_FR = {
    "volume_up": "monter le son du PC",
    "volume_down": "baisser le son du PC",
    "set_volume": "régler le volume du PC à {value} %",
    "mute": "couper ou remettre le son du PC",
    "play_pause": "lecture ou pause sur le PC",
    "next_track": "piste suivante sur le PC",
    "previous_track": "piste précédente sur le PC",
    "lock_screen": "verrouiller la session du PC",
}


def _now() -> float:
    return time.time()  # tests move this clock


def _kind(origin) -> str:
    """'pc', 'app', 'siri' or 'unknown' (remote.kind_of; remote is imported late)."""
    from . import remote
    return remote.kind_of(origin)


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
    # The ctx's origin goes along: a Siri session is refused even when this
    # handler is reached directly, and a phone's voice never launches a button-only card.
    out = decide(str(a.get("pending_id") or ""), a.get("decision"),
                 voice_session=getattr(ctx, "session_id", None), by_voice=True,
                 origin=getattr(ctx, "origin", "pc"))
    if out.get("state") == "done":
        return out["result"]  # what the action itself answered
    return out


HANDLERS = {"confirm_action": _confirm_action}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""


# ---------------------------------------------------------------- voice sessions

def _origin(record: dict) -> str:
    return record.get("origin") or "pc"  # older records: the PC


def _session(sid: str, origin: str = "pc") -> dict:
    """The session's record, created on first sight (caller holds _lock). A
    session evicted while tainted comes back tainted, with its reasons."""
    record = SESSIONS.get(sid)
    if record is None:
        reasons = FORGOTTEN.pop(sid, None)
        record = SESSIONS[sid] = {"created": _now(), "last_turn": 0.0, "tainted": reasons is not None,
                                  "reasons": list(reasons or []), "origin": str(origin or "pc")}
        _evict(record["origin"], keep=sid)
    return record


def _evict(origin: str, keep: str):
    """Keep each origin within its bucket (caller holds _lock): the least
    recently used session of that origin goes, one with an open request last.
    A phone can never push a PC session out, nor wash out its taint."""
    limit = MAX_SESSIONS if origin == "pc" else MAX_REMOTE_SESSIONS
    mine = [sid for sid, record in SESSIONS.items() if _origin(record) == origin and sid != keep]
    while mine and len(mine) + 1 > limit:
        busy = {p["sid"] for p in PENDING.values() if p["state"] == "pending"}
        pool = [sid for sid in mine if sid not in busy] or mine
        victim = min(pool, key=lambda sid: max(SESSIONS[sid]["created"], SESSIONS[sid]["last_turn"]))
        mine.remove(victim)
        record = SESSIONS.pop(victim)
        if record["tainted"]:
            FORGOTTEN[victim] = list(record["reasons"])
            FORGOTTEN.move_to_end(victim)
            while len(FORGOTTEN) > MAX_FORGOTTEN:
                FORGOTTEN.popitem(last=False)


def new_session(continues: bool = False, sources=None, origin: str = "pc") -> str:
    """continues: the new session carries the last exchanges of earlier ones
    (a reconnection, the 55-minute refresh, a page refilled from the journal):
    what JARVIS said there about outside content comes along, so its taint
    does too. sources: the sessions JARVIS's carried lines were said in, as the
    page tracks them ("" for a line refilled from the journal); any of them
    tainted, unknown here (JARVIS restarted since, or forgot it) or opened by
    another origin, taints the new one. Without sources, the previous session
    of the same origin stands for them (none known: JARVIS restarted, the
    lines' taint is unknown). origin: who opened it ("pc", "app:d_…",
    "siri:k_…"), the origin string of remote.Caller."""
    sid = uuid.uuid4().hex
    origin = str(origin or "pc")
    with _lock:
        if sources is None:
            mine = [record for record in SESSIONS.values() if _origin(record) == origin]
            # The newest, and the latest made among equal clock readings.
            newest = max(enumerate(mine), key=lambda item: (item[1]["created"], item[0]), default=None)
            carried = [newest[1] if newest else None]
        else:
            carried = []
            for source in sources:
                prior = SESSIONS.get(str(source or "")[:64])
                carried.append(prior if prior is not None and _origin(prior) == origin else None)
        record = _session(sid, origin)
        if continues:
            reasons = []
            for prior in carried:
                if prior is None:
                    reasons.append("conversation reprise")
                elif prior["tainted"]:
                    reasons += prior["reasons"] or ["conversation reprise"]
            if reasons:
                record["tainted"] = True
                record["reasons"] = list(dict.fromkeys(reasons))[-10:]
    return sid


def session_origin(sid: str | None) -> str | None:
    """The origin that opened this session, or None when it is not known here."""
    if not sid:
        return None
    with _lock:
        record = SESSIONS.get(sid)
        return _origin(record) if record is not None else None


def check_session(sid: str | None, origin: str) -> str | None:
    """Why this origin may not use that voice session (French), or None when it may."""
    origin = str(origin or "pc")
    with _lock:
        record = SESSIONS.get(sid) if sid else None
        if record is None:
            if _kind(origin) == "pc":
                return None  # the PC page after a restart: its session is simply new here
            return T.session_expired if sid and sid in FORGOTTEN else T.session_unknown
        if _origin(record) != origin:
            return T.session_foreign
    return None


def cancel_for_origin(origin: str) -> int:
    """Cancel the open requests of a revoked origin; returns how many. Like an
    expiry, never through decide(): nothing runs."""
    origin = str(origin or "pc")
    now = _now()
    with _lock:
        expired = _sweep(now)
        mine = [p for p in PENDING.values() if p["state"] == "pending" and (p.get("via") or "pc") == origin]
        for p in mine:
            p["state"] = "cancelled"
            p["decided"] = now
            p["result"] = {"ok": True, "message": T.cancelled}
    _announce(expired + mine)
    return len(mine)


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
        return bool(sid) and (bool(SESSIONS.get(sid, {}).get("tainted")) or sid in FORGOTTEN)


def _tainted_or_unknown(sid: str | None) -> bool:
    """For full access from a phone: a session no longer known here may have
    been tainted, so it counts as tainted (no session at all, a panel retry, does not)."""
    with _lock:
        return is_tainted(sid) or (sid is not None and sid not in SESSIONS)


def after_tool(name: str, args: dict, ctx, out):
    """A tool brought outside content into the session: taint it (run_tool calls this)."""
    sid = getattr(ctx, "session_id", None)
    if not sid or not isinstance(out, dict) or out.get("ok") is False:
        return
    if name in ("look_at_screen", "recall"):
        mark_tainted(sid, name)
    elif name == "system_control" and args.get("action") == "read_clipboard":
        mark_tainted(sid, "presse-papiers")  # often copied from a web page
    elif name == "info" and ("headlines" in out
                             or "actu" in str(args.get("type") or args.get("kind") or "").lower()):
        # By what came back: info also answers 'news' or 'Actus' with headlines.
        mark_tainted(sid, "actualités")
    elif name == "ares_lire" and _mentions_notes(args, out):
        mark_tainted(sid, "notes A.R.E.S")


def _mentions_notes(args: dict, out: dict) -> bool:
    return (any("note" in str(v).lower() for v in args.values())
            or any(k in ("note", "notes") for k in out))


# ---------------------------------------------------------------- the gate

def gate(name: str, args: dict, ctx) -> dict | None:
    """None lets the call run; a dict is returned to the model instead: the
    parked request (needs_confirmation) or a refusal ({"ok": False, "error"}).

    Several calls in one response are gated one by one: each gets its own id.
    """
    sid = getattr(ctx, "session_id", None)
    origin = str(getattr(ctx, "origin", "pc") or "pc")
    rule = _decision(name, args or {}, sid, origin)
    if rule is None:
        return None
    if "error" in rule:
        return {"ok": False, "error": rule["error"]}
    pending = _park(name, args or {}, sid, rule["summary"], rule["detail"], via=origin,
                    button_only=rule["button_only"], remote_kind=rule["remote_kind"])
    return {"status": "needs_confirmation", "pending_id": pending["id"], "summary": rule["summary"],
            "expires_in": max(0, round(pending["expires"] - _now())), "consigne": T.ask}


def needs_confirmation(name: str, args: dict, sid=None, origin: str = "pc") -> bool:
    """Would gate() park this call? Never true for a refusal (a handler reached
    anyway came through decide())."""
    rule = _decision(name, args or {}, sid, str(origin or "pc"))
    return rule is not None and "error" not in rule


def _decision(name: str, args: dict, sid, origin: str):
    """_rule, then rule s: Siri (or any origin that is neither the PC nor a
    phone) has no screen, so what would wait for a card is refused before
    anything is parked or published."""
    rule = _rule(name, args, sid, origin)
    if rule is not None and "error" not in rule and _kind(origin) not in ("pc", "app"):
        return {"error": T.siri}
    return rule


def _parks(summary: str, detail: str, button_only: bool = False, remote_kind: str | None = None) -> dict:
    return {"summary": summary, "detail": detail, "button_only": button_only, "remote_kind": remote_kind}


def _complet(profile) -> bool:
    # Loose on purpose: "Complet" is parked too (tasks would run it as lecture).
    return str(profile or "").strip().lower() == "complet"


def _complet_open() -> bool:
    """The PC's opt-in for full access from the phone (closed if it can't be read)."""
    from . import remote
    try:
        return bool(remote.complet_allowed())
    except Exception:  # noqa: BLE001 - fail closed
        return False


def _remote_actions() -> frozenset:
    from . import tools  # tools imports this module
    return tools.REMOTE_PC_ACTIONS


def _rule(name: str, args: dict, sid, origin: str = "pc"):
    """What this call needs (spec 4.11, rules a to d): None to run, a park
    (_parks: the card's summary and detail) or a refusal ({"error": text})."""
    # The detail is what will really run: the whole prompt (scrollable on the
    # card), so nothing can hide behind a harmless title.
    kind = _kind(origin)
    if name == "delegate_to_claude" and _complet(args.get("profile")):
        title, detail = _title(args.get("title"), args.get("prompt")), _clip(args.get("prompt"), DETAIL_MAX)
        if kind == "pc":
            return _parks(T.complet.format(title=title), detail) if config.CONFIRM_COMPLET else None
        if kind != "app":
            return {"error": T.complet_device}
        # From the phone: only while the PC's opt-in is open, never after
        # outside content, and always on the phone's own button (CONFIRM_COMPLET
        # is the PC's own setting).
        if not _complet_open():
            return {"error": T.complet_closed}
        if _tainted_or_unknown(sid):
            return {"error": T.complet_tainted}
        return _parks(T.complet_remote.format(title=title), detail, button_only=True, remote_kind="complet")
    if (name == "schedule" and str(args.get("kind") or "").lower() == "task"
            and _complet(args.get("profile"))):
        if kind != "pc":
            # Once approved it would run with full access every time, after the
            # opt-in expired and after the phone was removed.
            return {"error": T.complet_routine_pc}
        # Read as scheduler.add reads it ('Daily', or days with no repeat): the
        # card names the frequency that will run, never a harmless 'ponctuelle'.
        repeat = str(args.get("repeat") or "none").strip().lower()
        if repeat in ("none", "") and args.get("days"):
            repeat = "days"
        freq = FREQ.get(repeat, "ponctuelle")
        return _parks(T.complet_routine.format(freq=freq, title=_title(args.get("title"), args.get("text"))),
                      _clip(args.get("text"), DETAIL_MAX))
    action = args.get("action")
    if kind != "pc" and name == "system_control" and isinstance(action, str) and action in _remote_actions():
        # Monsieur may not be in front of the PC: only the phone's button acts on it.
        return _parks(T.pc_action.format(what=_action_fr(action, args.get("value"))), "",
                      button_only=True, remote_kind="pc_action")
    if not is_tainted(sid):
        return None
    research = _research(name, args)
    if research is not None:
        # The prompt leaves the PC: a hostile page could have written it.
        title, text = research
        return _parks(T.research.format(title=title), _clip(text, DETAIL_MAX))
    if name == "remember":
        # A remembered fact reaches every later prompt, full-access ones included.
        fact = " ".join(str(args.get("fact") or "").split())
        return _parks(T.remember.format(fact=_clip(fact, 80)), _clip(args.get("fact"), DETAIL_MAX))
    if name == "open_url" and kind == "pc":  # from a phone nothing opens on the PC
        url = str(args.get("url") or "")
        domain = _domain(url)
        if not _allowed(domain):
            return _parks(T.link.format(domain=domain or "ce lien"), _clip(url, 300))
    if name == "system_control" and action == "write_clipboard":
        return _parks(T.clipboard, _clip(args.get("value"), 300))
    if name in ("ares_ajouter", "ares_modifier"):
        return _parks(T.ares_write.format(what=_ares_what(name, args)), _clip(
            "\n".join(f"{k} : {v}" for k, v in args.items() if v not in (None, "")), DETAIL_MAX))
    return None


def _research(name: str, args: dict):
    """(title, prompt) of a task that will run with the web profile, as tasks
    and scheduler.add read the profile; None otherwise."""
    if name == "delegate_to_claude" and args.get("profile") == "recherche":
        return _title(args.get("title"), args.get("prompt")), args.get("prompt")
    if name == "schedule" and str(args.get("kind") or "").lower() == "task":
        profile = args.get("profile") or "recherche"  # what tools_agenda passes on
        if not isinstance(profile, str) or profile not in tasks.PROFILES or profile == "recherche":
            return _title(args.get("title"), args.get("text")), args.get("text")
    return None


def _action_fr(action: str, value) -> str:
    what = ACTION_FR.get(action, action)
    if action == "set_volume":
        what = what.format(value=" ".join(str(value or "").split())[:8] or "?")
    return what


_ARES_KINDS = {"tache": "nouvelle tâche", "tâche": "nouvelle tâche", "rappel": "nouveau rappel",
               "note": "nouvelle note"}


def _ares_what(name: str, args: dict) -> str:
    if name == "ares_ajouter":
        kind = _ARES_KINDS.get(str(args.get("type") or "").strip().lower(), "ajout")
        return f"{kind} « {_title(args.get('titre'), args.get('texte'))} »"
    done = str(args.get("action") or "").strip().lower() == "terminer"
    return f"tâche « {_title(args.get('titre_attendu'), args.get('id'))} » {'terminée' if done else 'reportée'}"


def _title(title, fallback) -> str:
    text = " ".join(str(title or "").split()) or " ".join(str(fallback or "").split())[:60]
    return text[:80] or "sans titre"


def _clip(text, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def web_address(url: str) -> str:
    """The address as a browser would open it: backslashes read as '/', and
    https:// added when no scheme is given ('youtube.com/x')."""
    text = str(url or "").strip().replace("\\", "/")
    if "://" not in text and ":" not in text.split("/", 1)[0]:
        text = "https://" + text
    return text


def _domain(url: str) -> str:
    # A browser reads a backslash as '/' in a web address: evil.example\@youtube.com
    # goes to evil.example, where urlsplit alone would answer youtube.com.
    try:
        return (urlsplit(web_address(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _allowed(domain: str) -> bool:
    """Domains in OPEN_URL_ALLOW (and their subdomains) open without asking."""
    allow = [d.strip().lower().lstrip(".") for d in (config.OPEN_URL_ALLOW or "").split(",") if d.strip()]
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in allow)


# ---------------------------------------------------------------- the store

def public(p: dict) -> dict:
    """What the page sees (never the session id nor the taint flag; the
    arguments only as the detail)."""
    keep = ("id", "name", "kind", "summary", "detail", "created", "expires", "task_id", "state", "result")
    out = {k: p.get(k) for k in keep}
    out["expires_in"] = max(0, round(p["expires"] - _now())) if p["state"] == "pending" else 0
    # Who raised it, and the one device whose button may launch it.
    out["via"] = p.get("via") or "pc"
    out["button_only"] = bool(p.get("button_only", False))
    out["launch_from"] = out["via"] if out["button_only"] else None
    out["remote_kind"] = p.get("remote_kind")
    return out


def _park(name: str, args: dict, sid, summary: str, detail: str, kind: str = "tool",
          task_id: str | None = None, *, via: str = "pc", button_only: bool = False,
          remote_kind: str | None = None) -> dict:
    now = _now()
    via = str(via or "pc")
    with _lock:
        expired = _sweep(now)
        same = next((p for p in PENDING.values() if p["state"] == "pending" and p["sid"] == sid
                     and p["name"] == name and p["args"] == args and p.get("via") == via), None)
        if same is None:  # the same request asked twice gets one card
            same = {"id": uuid.uuid4().hex[:10], "sid": sid, "name": name, "args": copy.deepcopy(args),
                    "summary": summary, "detail": detail, "created": now, "asked": now,
                    "expires": now + max(5, config.PENDING_TTL), "kind": kind, "task_id": task_id,
                    "state": "pending", "result": None, "via": via, "button_only": bool(button_only),
                    "remote_kind": remote_kind, "tainted": is_tainted(sid)}
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


def decide(pending_id: str, decision, voice_session: str | None = None, by_voice: bool = False,
           origin: str = "pc") -> dict:
    """Run ('oui') or drop ('non') a parked action.

    From a UI button any open request can be decided, with two exceptions: a
    phone launches only its own requests, and a button-only request (full
    access or a PC action asked from a phone) only from that phone. Siri never
    decides. By voice, the request must come from the same session, must not
    be button-only and, for 'oui', monsieur must have spoken or typed after it
    was last asked (asking again needs a new answer). A phone's full access is
    checked once more at launch: the PC's opt-in still open, the conversation
    still clean and still known here.
    """
    decision = str(decision or "").strip().lower()
    if decision not in ("oui", "non"):
        return {"ok": False, "error": T.bad_decision}
    origin = str(origin or "pc")
    kind = _kind(origin)
    peek = PENDING.get(pending_id)
    # Read before taking the lock: the remote state has locks of its own.
    opted_in = decision == "oui" and bool(peek) and peek.get("remote_kind") == "complet" and _complet_open()
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
        elif kind not in ("pc", "app"):  # Siri (or an origin not recognised) never decides
            error = T.refused
        elif by_voice and p.get("button_only"):
            error = T.button_only
        elif by_voice and not _voice_may_decide(p, voice_session, decision):
            error = T.refused
        elif decision == "oui":
            error = _launch_refusal(p, origin, kind, opted_in)
        if error is None:  # claim it now: a second click or call can't run it twice
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
    if p.get("remote_kind") == "complet" and not _alert_complet(p):
        result = {"ok": False, "error": T.alert_failed}  # never a launch the PC was not told of
    else:
        result = _execute(p)
    with _lock:
        p["state"] = "error" if result.get("ok") is False else "done"
        p["result"] = result
        p["decided"] = _now()
    _publish(p)
    return {"ok": p["state"] == "done", "state": p["state"], "result": result}


def _voice_may_decide(p: dict, voice_session, decision: str) -> bool:
    """By voice: only in the session that asked and, for 'oui', after a turn
    of monsieur's that came after the question (caller holds _lock)."""
    if not voice_session or p["sid"] != voice_session:
        return False
    return decision != "oui" or SESSIONS.get(voice_session, {}).get("last_turn", 0) > p.get("asked", p["created"])


def _launch_refusal(p: dict, origin: str, kind: str, opted_in: bool) -> str | None:
    """Why this origin may not say 'oui' to p (caller holds _lock), or None."""
    via = p.get("via") or "pc"
    if kind == "app" and via != origin:
        return T.other_device
    if kind == "pc" and p.get("button_only") and via != "pc":
        return T.from_phone
    if p.get("remote_kind") == "complet":
        if not opted_in:
            return T.complet_closed
        if p.get("tainted") or _tainted_or_unknown(p["sid"]):
            return T.complet_tainted
    return None


def _alert_complet(p: dict) -> bool:
    """Every full-access launch from a phone: an audit line, a PC toast and
    warning card, the phone alert (audit.alert, never deduplicated). False if
    it could not be raised: then nothing is launched."""
    from . import audit, remote
    via = p.get("via") or ""
    args = p.get("args") or {}
    task = tasks.TASKS.get(p.get("task_id") or "") or {}
    title = _title(args.get("title") or task.get("title"), args.get("prompt"))
    caller = (remote.Caller(kind="app", device_id=via.partition(":")[2])
              if remote.kind_of(via) == "app" else None)
    try:
        audit.alert("remote_complet", T.complet_alert.format(title=title), caller,
                    pending=p["id"], tool=p["name"], profile="complet", title=title)
    except Exception:  # noqa: BLE001 - audit never raises; if it did, fail closed
        return False
    return True


def _execute(p: dict) -> dict:
    """The stored action with its stored arguments, past the gate, for the
    origin that asked (its task, routine or link goes back there)."""
    from . import tools  # tools imports this module

    try:
        if p["kind"] == "task_approval":
            task = tasks.approve(p["task_id"])
            return {"status": "started", "task_id": task["id"], "profile": task["profile"]}
        handler = tools.handlers().get(p["name"])
        if handler is None:
            return {"ok": False, "error": f"Outil indisponible : {p['name']}"}
        out = handler(copy.deepcopy(p["args"]), tools.ToolCtx(session_id=p["sid"], origin=p.get("via") or "pc"))
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
    via = str(task.get("via") or "pc")
    kind = _kind(via)
    if kind == "siri":
        return  # Siri never gets a card: it has no screen to show it on
    # A phone's full-access task resumes with more tools only from that phone's button.
    remote_complet = kind != "pc" and tasks.normalize_profile(task.get("profile")) == "complet"
    names = sorted({str(d.get("tool")) for d in denials if d.get("tool")})
    summary = T.task_approval.format(tool=", ".join(names), title=task["title"])
    detail = "\n".join(f"- {d.get('detail') or d.get('tool')}" for d in denials[:5])
    _park("approve_task", {"task_id": task["id"]}, task.get("voice_session"), summary, detail,
          kind="task_approval", task_id=task["id"], via=via, button_only=remote_complet,
          remote_kind="complet" if remote_complet else None)


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


def _own_session(sid: str | None, request: Request):
    """403 when the caller may not use that voice session (another device's, unknown from a phone)."""
    from . import remote
    error = check_session(sid, remote.caller_of(request).origin)
    if error:
        raise HTTPException(403, error)


@router.post("/api/voice/turn")
def voice_turn(body: TurnIn, request: Request):
    """The page heard monsieur (speech_stopped) or he typed: a fresh turn."""
    _own_session(body.session_id, request)
    mark_turn(body.session_id)
    return {"ok": bool(body.session_id)}


@router.post("/api/voice/taint")
def voice_taint(body: TaintIn, request: Request):
    """The page handed outside text to the model (voice.sendData)."""
    _own_session(body.session_id, request)
    mark_tainted(body.session_id, body.reason or "données")
    return {"ok": bool(body.session_id)}


@router.get("/api/pending")
def list_pending():
    return open_items()


@router.post("/api/pending/{pending_id}/decide")
def decide_route(pending_id: str, body: DecideIn, request: Request):
    """[Lancer] / [Annuler] on a confirmation card. Who decides is the caller
    the guard stamped, never anything in the body."""
    from . import remote
    caller = remote.caller_of(request)
    out = decide(pending_id, body.decision, origin=caller.origin)
    if caller.remote:
        from . import audit
        audit.event(caller, "decide", pending=pending_id[:16], decision=str(body.decision or "")[:8])
    if out.get("error") == T.unknown:
        raise HTTPException(404, T.unknown)
    return out
