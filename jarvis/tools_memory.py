"""Tool family: long-term memory (facts JARVIS keeps about monsieur).

The family interface is described in tools.py.
"""
from . import memory

TOOLS = [{
    "type": "function",
    "name": "remember",
    "description": ("Store a lasting fact about monsieur (preferences, people, projects, "
                    "city, habits). It is given back to you at every conversation."),
    "parameters": {
        "type": "object",
        "properties": {"fact": {"type": "string", "description": "One short, self-contained fact"}},
        "required": ["fact"],
    },
}, {
    "type": "function",
    "name": "forget",
    "description": "Delete remembered facts matching an id or words.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}]

CLIENT_TOOLS: set = set()


def _forget(a: dict, ctx) -> dict:
    gone = memory.forget(a.get("query", ""))
    if not gone:
        return {"ok": False, "error": "Rien de tel dans ma mémoire."}
    return {"ok": True, "forgotten": [f["text"] for f in gone]}


HANDLERS = {
    "remember": lambda a, ctx: {"ok": True, "remembered": memory.remember(a.get("fact", ""))["text"]},
    "forget": _forget,
}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
