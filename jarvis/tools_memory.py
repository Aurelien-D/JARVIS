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
    "description": ("Delete one remembered fact, by its id or by words found in it. If several "
                    "facts match, nothing is deleted and they come back as 'ambiguous': ask "
                    "monsieur which one, then call again with its id."),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "The fact's id, or words from it"}},
        "required": ["query"],
    },
}]

CLIENT_TOOLS: set = set()


def _forget(a: dict, ctx) -> dict:
    out = memory.forget(a.get("query", ""))
    if not out.get("ok"):
        return out
    # The ids let the page offer 'Annuler' (POST /api/undo/{id}).
    return {"ok": True, "forgotten": [f["text"] for f in out["forgotten"]],
            "ids": [f["id"] for f in out["forgotten"]]}


HANDLERS = {
    "remember": lambda a, ctx: {"ok": True, "remembered": memory.remember(
        a.get("fact", ""), via=getattr(ctx, "origin", None) or "pc")["text"]},
    "forget": _forget,
}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
