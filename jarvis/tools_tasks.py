"""Tool family: Claude Code tasks (delegate real work, cancel it).

The family interface is described in tools.py.
"""
from . import config, tasks

PROFILE = {"type": "string", "enum": list(tasks.PROFILES),
           "description": ("recherche = web only; lecture = read local files, no changes, "
                           "no web; complet = files, commands and web. Pick the narrowest.")}
COMPLEXITY = {"type": "string", "enum": list(config.MODELS),
              "description": "simple = quick question, normale, complexe = heavy work."}

TOOLS = [{
    "type": "function",
    "name": "delegate_to_claude",
    "description": ("Delegate a real task to a Claude Code session running on "
                    "this machine (files, code, web research, mail and calendar "
                    "through connectors, automation). Returns immediately; the "
                    "result arrives later as a system message."),
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
    # unknown or missing one becomes.
    task = tasks.create_task(a.get("title", ""), a.get("prompt", ""),
                             profile=a.get("profile"),
                             complexity=a.get("complexity") or "normale",
                             continue_task=a.get("continue_task") or None)
    out = {"status": "started", "task_id": task["id"], "profile": task["profile"],
           "model": task["model"] or "défaut"}
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
