"""What the voice model can do: the tool registry.

Tools come in families, one module each. A family exposes:
  TOOLS                 the JSON schemas given to the voice model
  HANDLERS              name -> fn(args, ctx), run here on the server
  CLIENT_TOOLS          names that run in the page instead (display, camera, standby)
  available()           False hides the whole family (not configured, unreachable)
  instructions_block()  extra text for the voice instructions ('' for none)

This module is itself the core family (status, display, camera, standby).
Every server-side call goes through run_tool(), which asks confirm.gate()
first: that is where risky actions wait for monsieur's "oui". After a tool
that brought outside content in (screen, notes, news), confirm.after_tool()
marks the voice session as tainted.
"""
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from . import (ares, confirm, info, journal, scheduler, tasks, tools_agenda, tools_memory,
               tools_pc, tools_tasks, usage)


@dataclass
class ToolCtx:
    """Who is calling: the voice session that asked (None for internal calls)."""
    session_id: str | None = None


# ---------------------------------------------------------------- core family

TOOLS = [{
    "type": "function",
    "name": "get_status",
    "description": ("Current date and time, running and recent Claude tasks, "
                    "upcoming reminders and routines, today's spending and the daily cap."),
    "parameters": {"type": "object", "properties": {}},
}, {
    "type": "function",
    "name": "look_at_camera",
    "description": "Take a photo with the webcam and look at it.",
    "parameters": {"type": "object", "properties": {}},
}, {
    "type": "function",
    "name": "display_card",
    "description": ("Show a visual card on the JARVIS screen: results, "
                    "numbers, lists, code, comparisons. Use markdown-lite: "
                    "**bold**, `code`, lines starting with '- ' for bullets. "
                    "Use whenever a visual helps; keep the spoken reply short."),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short card title"},
            "content": {"type": "string", "description": "Card body (markdown-lite)"},
            "kind": {"type": "string", "enum": ["info", "result", "code", "warning"],
                      "description": "Visual style of the card"},
        },
        "required": ["title", "content"],
    },
}, {
    "type": "function",
    "name": "display_report",
    "description": ("Show a full data report dashboard on screen: KPI tiles, "
                    "an interactive chart, a sortable table, and markdown notes. "
                    "Use for data analysis results (spreadsheets, stats, "
                    "comparisons). All sections are optional except title."),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Report title"},
            "kpis": {"type": "array", "description": "Headline numbers (max 4)",
                     "items": {"type": "object", "properties": {
                         "label": {"type": "string"},
                         "value": {"type": "string", "description": "e.g. '12 480 €'"},
                         "delta": {"type": "string", "description": "e.g. '+12%' (optional)"},
                     }, "required": ["label", "value"]}},
            "chart": {"type": "object", "description": "One chart", "properties": {
                "type": {"type": "string", "enum": ["line", "bar", "area", "donut"]},
                "categories": {"type": "array", "items": {"type": "string"},
                               "description": "X axis labels (or slice labels for donut)"},
                "series": {"type": "array", "description": "1-3 series",
                           "items": {"type": "object", "properties": {
                               "name": {"type": "string"},
                               "data": {"type": "array", "items": {"type": "number"}},
                           }, "required": ["name", "data"]}},
            }},
            "table": {"type": "object", "properties": {
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array",
                         "items": {"type": ["string", "number"]}}},
            }},
            "markdown": {"type": "string", "description": "Notes / conclusions in markdown"},
        },
        "required": ["title"],
    },
}, {
    "type": "function",
    "name": "end_conversation",
    "description": ("Go back to standby once your goodbye has been said. JARVIS then "
                    "waits for its wake word again."),
    "parameters": {"type": "object", "properties": {}},
}, {
    "type": "function",
    "name": "wait_for_user",
    "description": ("Call when the latest audio needs no spoken reply: silence, noise, TV, "
                    "side conversation, speech not addressed to JARVIS."),
    "parameters": {"type": "object", "properties": {}},
}]

CLIENT_TOOLS = {"display_card", "display_report", "look_at_camera", "end_conversation",
                "wait_for_user"}


def _money(usd: float) -> str:
    return f"{usd:.2f} $".replace(".", ",")


def _spending() -> dict:
    """Today's spending as the top bar's chip says it (usage.py): the voice at
    OpenAI's prices, Claude as Claude Code's own estimate, and the daily cap."""
    try:
        voice, claude, cap = usage.realtime_spent_today(), usage.claude_spent_today(), usage.daily_cap()
    except Exception:  # noqa: BLE001 - get_status never fails for a counter
        return {"today": "inconnue"}
    return {"today": f"≈ {_money(voice + claude)} (voix ≈ {_money(voice)}, Claude ≈ {_money(claude)}, "
                     "estimation)",
            "daily_cap": _money(cap) if cap else "aucun (Réglages › Coûts)",
            "cap_reached": bool(cap and voice + claude >= cap)}


def _status(a: dict, ctx) -> dict:
    now = datetime.now()
    all_tasks = tasks.list_tasks()
    return {
        "now": f"{scheduler.fr_date(now)}, {now:%H:%M}",
        "running_tasks": [{"id": t["id"], "title": t["title"], "progress": t["progress"],
                           "status": t["status"], "elapsed_s": round(time.time() - t["started"])}
                          for t in all_tasks if t["status"] in tasks.ACTIVE],  # queued ones too
        "recent_tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]}
                         for t in all_tasks if t["status"] not in tasks.ACTIVE][:5],
        "upcoming": [scheduler.describe(i, now) for i in scheduler.items()[:8]],
        "spending": _spending(),
    }


HANDLERS = {"get_status": _status}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""


# ---------------------------------------------------------------- registry

FAMILIES = [sys.modules[__name__], tools_tasks, tools_pc, tools_agenda, tools_memory,
            confirm, ares, info, journal]


def families() -> list:
    """The families offered right now (an unreachable service hides its tools)."""
    return [fam for fam in FAMILIES if fam.available()]


def session_tools() -> list:
    """Every tool schema for a new voice session."""
    return [tool for fam in families() for tool in fam.TOOLS]


def client_tools() -> set:
    return {name for fam in families() for name in fam.CLIENT_TOOLS}


def handlers() -> dict:
    return {name: fn for fam in families() for name, fn in fam.HANDLERS.items()}


def run_tool(name: str, args: dict, ctx: ToolCtx | None = None) -> dict:
    args, ctx = args or {}, ctx or ToolCtx()
    try:
        gated = confirm.gate(name, args, ctx)
        if gated is not None:
            return gated
        handler = handlers().get(name)
        if not handler:
            # A family hidden since the session began (A.R.E.S closed meanwhile)
            # says why in its own words; its handler never runs.
            hidden = next((fam for fam in FAMILIES if name in fam.HANDLERS), None)
            if hidden is not None:
                return {"ok": False, "error": getattr(hidden, "UNAVAILABLE", f"Outil indisponible : {name}")}
            return {"ok": False, "error": f"Outil inconnu : {name}"}
        out = handler(args, ctx)
        confirm.after_tool(name, args, ctx, out)
        return out
    except Exception as exc:  # noqa: BLE001 - the model gets the reason and can say it
        return {"ok": False, "error": str(exc)}
