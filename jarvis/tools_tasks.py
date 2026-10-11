"""Tool family: Claude Code tasks (delegate real work, cancel it).

The family interface is described in tools.py.
"""
from . import config, tasks

PROFILE = {"type": "string", "enum": list(tasks.PROFILES),
           "description": ("recherche = web uniquement (aucun fichier) ; lecture = lit vos fichiers "
                           "sans rien modifier ni aller sur internet (par défaut) ; complet = "
                           "fichiers, commandes et web ; exige votre confirmation. "
                           "Pick the narrowest that does the job.")}
COMPLEXITY = {"type": "string", "enum": list(config.MODELS),
              "description": "simple = quick question, normale, complexe = heavy work."}

TOOLS = [{
    "type": "function",
    "name": "delegate_to_claude",
    "description": ("Delegate a real task to a Claude Code session running on "
                    "this machine (files, code, web research, mail and calendar "
                    "through connectors, automation). Returns immediately; the "
                    "result arrives later. Profile complet first returns "
                    "needs_confirmation: ask monsieur, then call confirm_action."),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Very short task label (3-5 words)"},
            "prompt": {"type": "string", "description": "Complete, self-contained task instruction for Claude Code"},
            "profile": PROFILE,
            "complexity": COMPLEXITY,
            "continue_task": {"type": "string",
                              "description": ("Continue an earlier task's Claude session: "
                                              "'latest' or a task id. Omit for a new task.")},
        },
        "required": ["title", "prompt", "profile"],
    },
}, {
    "type": "function",
    "name": "cancel_task",
    "description": ("Cancel a running Claude Code task. Omit task_id to cancel "
                    "the most recently started running task."),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task id to cancel (optional)"},
        },
    },
}]

CLIENT_TOOLS: set = set()


def _delegate(a: dict, ctx) -> dict:
    # The profile goes through as given: tasks.create_task decides what an
    # unknown or missing one becomes (lecture). The voice session goes along so
    # a later approval of denied tools can be confirmed in that conversation.
    task = tasks.create_task(a.get("title", ""), a.get("prompt", ""),
                             profile=a.get("profile"),
                             complexity=a.get("complexity") or "normale",
                             continue_task=a.get("continue_task") or None,
                             voice_session=getattr(ctx, "session_id", None),
                             via=getattr(ctx, "origin", "pc"))
    queued = task.get("status") == "en_file"  # MAX_CONCURRENT_TASKS already running
    out = {"status": "en_file" if queued else "started", "task_id": task["id"],
           "profile": task["profile"], "model": task["model"] or "défaut"}
    if task.get("resumed_from"):
        out["continues"] = task["resumed_from"]
    if task.get("note"):
        out["note"] = task["note"]
    return out


HANDLERS = {
    "delegate_to_claude": _delegate,
    "cancel_task": lambda a, ctx: tasks.cancel(a.get("task_id") or "latest"),
}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
