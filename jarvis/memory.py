"""Long-term memory: short facts JARVIS keeps about monsieur (data/memory.json)."""
import time
import uuid

from . import events, store

FILE = "memory.json"
MAX_FACTS = 200


def facts() -> list:
    return store.load(FILE, [])


def remember(text: str) -> dict:
    text = " ".join((text or "").split())[:500]
    if not text:
        raise ValueError("rien à retenir")
    with store.LOCK:
        items = facts()
        for fact in items:
            if fact["text"].lower() == text.lower():
                return fact
        fact = {"id": uuid.uuid4().hex[:6], "text": text, "created": time.time()}
        items = (items + [fact])[-MAX_FACTS:]
        store.save(FILE, items)
    events.publish("memory", {"facts": items})
    return fact


def forget(query: str) -> list:
    """Drop the fact with this id, or every fact containing the words."""
    q = (query or "").strip().lower()
    if not q:
        raise ValueError("précise ce qu'il faut oublier")
    with store.LOCK:
        items = facts()
        gone = ([f for f in items if f["id"] == q]
                or [f for f in items if q in f["text"].lower()])
        if gone:
            items = [f for f in items if f not in gone]
            store.save(FILE, items)
    if gone:
        events.publish("memory", {"facts": items})
    return gone


def as_text(limit_chars: int = 3000) -> str:
    """Newest facts first, as a bullet list that fits the budget."""
    lines, used = [], 0
    for fact in reversed(facts()):
        line = f"- {fact['text']}"
        if used + len(line) > limit_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
