"""Tool family: reminders, timers and routines (JARVIS's own agenda).

The family interface is described in tools.py.
"""
from . import confirm, scheduler
from .tools_tasks import COMPLEXITY, PROFILE

DAYS_SCHEMA = {"type": "array", "items": {"type": "string", "enum": scheduler.DAY_KEYS},
               "description": "repeat='days': the weekdays, e.g. ['lun', 'jeu']"}

TOOLS = [{
    "type": "function",
    "name": "schedule",
    "description": ("Schedule a reminder (spoken when due) or a Claude task to run "
                    "later, once or repeatedly. Give delay_minutes OR at."),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["reminder", "task"]},
            "title": {"type": "string", "description": "Short label"},
            "text": {"type": "string",
                     "description": "reminder: what to tell monsieur; task: full instruction for Claude Code"},
            "delay_minutes": {"type": "number", "description": "Due in N minutes (timers)"},
            "at": {"type": "string",
                   "description": ("Local time 'HH:MM' (next occurrence), 'YYYY-MM-DDTHH:MM', or monsieur's "
                                   "French words: 'à midi', 'demain à 9 h', 'mardi prochain à 9 h', "
                                   "'dans un quart d'heure', 'le 12 octobre à 18 h'")},
            "repeat": {"type": "string", "enum": list(scheduler.REPEATS),
                       "description": "monthly = same day each month; days = the weekdays in 'days'"},
            "days": DAYS_SCHEMA,
            "profile": PROFILE,
            "complexity": COMPLEXITY,
        },
        "required": ["kind", "title", "text"],
    },
}, {
    "type": "function",
    "name": "cancel_schedule",
    "description": ("Cancel a reminder or routine by id or by words from its title. Several "
                    "matches return ambiguous with the choices: ask monsieur which one, then "
                    "call again with its id (all=true only if he said all of them)."),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"},
                       "all": {"type": "boolean", "description": "Cancel every match"}},
        "required": ["query"],
    },
}, {
    "type": "function",
    "name": "snooze_reminder",
    "description": ("Bring back a reminder that just went off, a few minutes later ('reporte-le "
                    "de 10 minutes', 'rappelle-le-moi dans une heure'). Without query: the last one."),
    "parameters": {
        "type": "object",
        "properties": {"minutes": {"type": "number", "description": "Default 10"},
                       "query": {"type": "string", "description": "Words of the reminder, or its id"}},
    },
}]

CLIENT_TOOLS: set = set()


def _schedule(a: dict, ctx) -> dict:
    # A full-access routine reaches this handler only once monsieur said "oui"
    # (confirm.decide); straight from run_tool the gate would have parked it.
    # Whatever confirm wouldn't park is never allowed full access here.
    allowed = confirm.needs_confirmation("schedule", a, getattr(ctx, "session_id", None))
    item = scheduler.add(a.get("kind", "reminder"), a.get("title", ""), a.get("text", ""),
                         at=a.get("at"), delay_minutes=a.get("delay_minutes"),
                         repeat=a.get("repeat") or "none",
                         profile=a.get("profile") or "recherche",
                         complexity=a.get("complexity") or "normale",
                         days=a.get("days"), allow_complet=allowed)
    return {"ok": True, "scheduled": scheduler.describe(item)}


def _choices(matches: list) -> list:
    return [scheduler.describe(i) for i in matches[:6]]


def _cancel_schedule(a: dict, ctx) -> dict:
    try:
        gone = scheduler.cancel(a.get("query", ""), all_matches=a.get("all") is True)
    except scheduler.Ambiguous as exc:
        return {"ok": False, "ambiguous": True, "error": str(exc), "choices": _choices(exc.matches)}
    if not gone:
        return {"ok": False, "error": "Aucun rappel ne correspond."}
    return {"ok": True, "cancelled": [i["title"] for i in gone]}


def _snooze(a: dict, ctx) -> dict:
    minutes = a.get("minutes")
    try:
        entry = scheduler.find_recent(a.get("query") or "")
    except scheduler.Ambiguous as exc:
        return {"ok": False, "ambiguous": True, "error": str(exc),
                "choices": [f"[{m['id']}] {m.get('title', '')}" for m in exc.matches[:6]]}
    except LookupError as exc:
        return {"ok": False, "error": str(exc)}
    item = scheduler.snooze(entry["id"], 10 if minutes in (None, "") else minutes)
    return {"ok": True, "snoozed": scheduler.describe(item)}


HANDLERS = {"schedule": _schedule, "cancel_schedule": _cancel_schedule, "snooze_reminder": _snooze}


def available() -> bool:
    return True


def instructions_block() -> str:
    """At most one remark per session, said at the opening (remarques.py)."""
    from . import remarques  # late: remarques reads the tasks and A.R.E.S
    try:
        text = remarques.for_voice()
    except Exception:  # noqa: BLE001 - an optional nicety never blocks a session
        return ""
    if not text:
        return ""
    return ("Une remarque de JARVIS, facultative : si monsieur ouvre la conversation sans demande "
            f"urgente, mentionne-la une seule fois, en une phrase, puis n'y reviens plus : {text}")
