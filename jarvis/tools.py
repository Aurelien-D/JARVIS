"""What the voice model can do: the tool registry.

Tools come in families, one module each. A family exposes:
  TOOLS                 the JSON schemas given to the voice model
  HANDLERS              name -> fn(args, ctx), run here on the server
  CLIENT_TOOLS          names that run in the page instead (display, camera, standby)
  available()           False hides the whole family (not configured, unreachable)
  instructions_block()  extra text for the voice instructions ('' for none)

This module is itself the core family (status, display, camera, standby).
Every server-side call goes through run_tool(), which first settles who is
asking (the effective origin: the PC, a paired iPhone, a Siri key), refuses
what that origin may not do (APP_TOOLS, SIRI_TOOLS), then asks confirm.gate():
that is where risky actions wait for monsieur's "oui". After a tool that
brought outside content in (screen, notes, news), confirm.after_tool() marks
the voice session as tainted.
"""
import copy
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from . import (ares, confirm, info, journal, scheduler, tasks, tools_agenda, tools_memory,
               tools_pc, tools_tasks, usage)


@dataclass
class ToolCtx:
    """Who is calling: the voice session that asked (None for internal calls) and
    the origin string of the caller ("pc", "app:d_…", "siri:k_…", see remote.Caller)."""
    session_id: str | None = None
    origin: str = "pc"


# What a paired iPhone may do (spec 4.11). PC actions from the phone wait for
# its [Lancer] button (confirm.py); clipboard, screenshots, the screen and
# opening applications stay on the PC.
REMOTE_PC_ACTIONS = frozenset({"volume_up", "volume_down", "set_volume", "mute", "play_pause",
                               "next_track", "previous_track", "lock_screen"})
APP_TOOLS = frozenset({"get_status", "delegate_to_claude", "cancel_task", "schedule", "cancel_schedule",
                       "snooze_reminder", "remember", "forget", "recall", "info", "ares_lire",
                       "ares_ajouter", "ares_modifier", "confirm_action", "system_control", "open_url"})
# Siri: a reminder, a web research, cancelling its own tasks; nothing else.
SIRI_TOOLS = frozenset({"schedule", "delegate_to_claude", "cancel_task"})

REMOTE_SYSTEM_CONTROL = ("Volume, music keys and lock of the PC at home; monsieur is away: every action "
                         "waits for his [Lancer] on the iPhone.")
REMOTE_OPEN_URL = "Send a link to monsieur's iPhone as a tappable card; nothing opens on the PC."


class T:
    """French refusals returned to the model (it says them to monsieur)."""
    foreign_session = "Session d'un autre appareil."
    app_tool = "Depuis l'iPhone, « {name} » n'est pas disponible : faites-le depuis le PC."
    app_pc_only = "Presse-papiers et captures d'écran restent réservés au PC."
    siri_elsewhere = "Pour cela, ouvrez JARVIS sur l'iPhone."
    siri_repeat = "Rappel répété refusé après la lecture d'un résultat."
    siri_research = "Recherche refusée après la lecture d'un résultat : ouvrez JARVIS sur l'iPhone."
    siri_cancel = "Seules les tâches lancées depuis Siri s'annulent ici."


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
    OpenAI's prices, Claude as Claude Code's own estimate, Siri's text model on
    the days it spoke, and the daily cap."""
    try:
        voice, claude, cap = usage.realtime_spent_today(), usage.claude_spent_today(), usage.daily_cap()
        siri = usage.text_spent_today()
    except Exception:  # noqa: BLE001 - get_status never fails for a counter
        return {"today": "inconnue"}
    total = voice + claude + siri
    parts = f"voix ≈ {_money(voice)}, Claude ≈ {_money(claude)}" + (f", Siri ≈ {_money(siri)}" if siri else "")
    return {"today": f"≈ {_money(total)} ({parts}, estimation)",
            "daily_cap": _money(cap) if cap else "aucun (Réglages › Coûts)",
            "cap_reached": bool(cap and total >= cap)}


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


def session_tools(scope: str = "pc") -> list:
    """Every tool schema for a new voice session. scope: the caller's kind;
    anything but "pc" gets the iPhone's list (APP_TOOLS and the page's own
    tools), with PC actions and links described for a monsieur who is away."""
    offered = [tool for fam in families() for tool in fam.TOOLS]
    if scope == "pc":
        return offered
    client = client_tools()
    remote_list = []
    for tool in offered:
        name = tool.get("name")
        if name not in APP_TOOLS and name not in client:
            continue  # open_app, look_at_screen: PC only
        if name == "system_control":
            tool = copy.deepcopy(tool)
            tool["description"] = REMOTE_SYSTEM_CONTROL
            tool["parameters"]["properties"]["action"]["enum"] = sorted(REMOTE_PC_ACTIONS)
        elif name == "open_url":
            tool = {**tool, "description": REMOTE_OPEN_URL}
        remote_list.append(tool)
    return remote_list


def client_tools() -> set:
    return {name for fam in families() for name in fam.CLIENT_TOOLS}


def handlers() -> dict:
    return {name: fn for fam in families() for name, fn in fam.HANDLERS.items()}


def _effective_origin(ctx) -> str | None:
    """Who is really asking: a known session's own origin when the caller says
    "pc" (the more restrictive one); None when two remote origins disagree."""
    from . import remote
    asked = str(getattr(ctx, "origin", "pc") or "pc")
    owner = confirm.session_origin(getattr(ctx, "session_id", None))
    if owner is None:
        return asked
    if remote.kind_of(asked) == "pc":
        return owner
    if remote.kind_of(owner) != "pc" and owner != asked:
        return None
    return asked


def _refusal(name: str, args: dict, ctx: ToolCtx) -> str | None:
    """What this origin may never do, whatever confirm would say (spec 4.11)."""
    from . import remote
    kind = remote.kind_of(ctx.origin)
    if kind == "pc":
        return None
    if kind == "siri":
        return _siri_refusal(name, args, ctx)
    # A paired iPhone (and, failing closed, any origin not recognised).
    if name not in APP_TOOLS:
        return T.app_tool.format(name=" ".join(str(name).split())[:60])
    action = args.get("action")
    if name == "system_control" and not (isinstance(action, str) and action in REMOTE_PC_ACTIONS):
        return T.app_pc_only
    return None


def _siri_refusal(name: str, args: dict, ctx: ToolCtx) -> str | None:
    tainted = confirm.is_tainted(ctx.session_id)
    if name not in SIRI_TOOLS:
        return T.siri_elsewhere
    if name == "schedule":
        if args.get("kind", "reminder") != "reminder":
            return T.siri_elsewhere
        repeat = str(args.get("repeat") or "none").strip().lower()
        if tainted and (repeat != "none" or args.get("days")):
            return T.siri_repeat
    if name == "delegate_to_claude":
        if args.get("profile") != "recherche" or args.get("continue_task"):
            return T.siri_elsewhere
        if tainted:
            return T.siri_research
    if name == "cancel_task":
        # Never "the latest task": only one this Siri key started, named by its id.
        task_id = str(args.get("task_id") or "").strip()
        task = tasks.TASKS.get(task_id) if task_id not in ("", "latest", "last", "-") else None
        if task is None or (task.get("via") or "pc") != ctx.origin:
            return T.siri_cancel
    return None


def run_tool(name: str, args: dict, ctx: ToolCtx | None = None) -> dict:
    args, ctx = args or {}, ctx or ToolCtx()
    try:
        # 1. The effective origin, carried into the gate, the handler and after_tool.
        origin = _effective_origin(ctx)
        if origin is None:
            return {"ok": False, "error": T.foreign_session}
        ctx = ToolCtx(session_id=getattr(ctx, "session_id", None), origin=origin)
        # 2-3. What a phone or Siri may never do.
        refused = _refusal(name, args, ctx)
        if refused:
            return {"ok": False, "error": refused}
        # 4. Risky calls wait for a "oui", or are refused for this origin.
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
