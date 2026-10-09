"""Tool family: reminders, timers and routines (JARVIS's own agenda).

The family interface is described in tools.py.
"""
from . import scheduler
from .tools_tasks import COMPLEXITY, PROFILE

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
                   "description": "Local time 'HH:MM' (next occurrence) or 'YYYY-MM-DDTHH:MM'"},
            "repeat": {"type": "string", "enum": list(scheduler.REPEATS)},
            "profile": PROFILE,
            "complexity": COMPLEXITY,
        },
        "required": ["kind", "title", "text"],
    },
}, {
    "type": "function",
    "name": "cancel_schedule",
    "description": "Cancel reminders or routines by id or by words from their title.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}]

CLIENT_TOOLS: set = set()


def _schedule(a: dict, ctx) -> dict:
    item = scheduler.add(a.get("kind", "reminder"), a.get("title", ""), a.get("text", ""),
                         at=a.get("at"), delay_minutes=a.get("delay_minutes"),
                         repeat=a.get("repeat") or "none",
                         profile=a.get("profile") or "recherche",
                         complexity=a.get("complexity") or "normale")
    return {"ok": True, "scheduled": scheduler.describe(item)}


def _cancel_schedule(a: dict, ctx) -> dict:
    gone = scheduler.cancel(a.get("query", ""))
    if not gone:
        return {"ok": False, "error": "Aucun rappel ne correspond."}
    return {"ok": True, "cancelled": [i["title"] for i in gone]}


HANDLERS = {"schedule": _schedule, "cancel_schedule": _cancel_schedule}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
