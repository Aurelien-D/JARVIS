"""Long-term memory: short facts JARVIS keeps about monsieur (data/memory.json).

Forgetting is safe by construction: words that match several facts delete
nothing (the model must name one by its id), and a forgotten fact waits
TRASH_DAYS in data/trash.json, where POST /api/undo/{id} can bring it back.
"""
import time
import uuid

from . import events, store

FILE = "memory.json"
TRASH_FILE = "trash.json"
MAX_FACTS = 200
MAX_TEXT = 500
TRASH_DAYS = 7


def facts() -> list:
    return store.load(FILE, [])


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _is_pc(via) -> bool:
    return str(via or "pc") == "pc"


def remember(text: str, via: str = "pc") -> dict:
    """A new fact; one said from a phone keeps its origin (via): it never
    reaches a full-access task's prompt (as_text(pc_only=True))."""
    text = _clean(text)[:MAX_TEXT]
    if not text:
        raise ValueError("rien à retenir")
    with store.LOCK:
        items = facts()
        for fact in items:
            if fact["text"].lower() == text.lower():
                return fact
        fact = {"id": uuid.uuid4().hex[:6], "text": text, "created": time.time()}
        if not _is_pc(via):
            fact["via"] = str(via)
        items = (items + [fact])[-MAX_FACTS:]
        store.save(FILE, items)
    events.publish("memory", {"facts": items})
    return fact


def forget(query: str) -> dict:
    """Drop the fact with this id, or the one fact containing the words.

    {ok: True, forgotten: [facts]} | {ok: False, ambiguous: [{id, text}]}
    | {ok: False, error}. Several facts matching the words is ambiguous: none
    is deleted, so a vague « oublie ce qui concerne monsieur » can't wipe the
    memory; the model asks which one and calls again with its id.
    """
    q = _clean(query).lower()
    if not q:
        raise ValueError("précise ce qu'il faut oublier")
    with store.LOCK:
        items = facts()
        gone = ([f for f in items if f["id"] == q]
                or [f for f in items if f["text"].lower() == q])  # the exact wording names one
        if not gone:
            matches = [f for f in items if q in f["text"].lower()]
            if len(matches) > 1:
                return {"ok": False, "ambiguous": [{"id": f["id"], "text": f["text"]} for f in matches[:10]],
                        "error": "Plusieurs souvenirs correspondent : précise lequel par son id."}
            gone = matches
        left = _drop(items, gone) if gone else None
    if not gone:
        return {"ok": False, "error": "Rien de tel dans ma mémoire."}
    events.publish("memory", {"facts": left})
    return {"ok": True, "forgotten": gone}


def forget_id(fact_id: str) -> list:
    """The panel's ✕: that id only, never a word match."""
    with store.LOCK:
        items = facts()
        gone = [f for f in items if f["id"] == str(fact_id or "")]
        left = _drop(items, gone) if gone else None
    if gone:
        events.publish("memory", {"facts": left})
    return gone


def _drop(items: list, gone: list) -> list:
    """Remove these facts, keeping them in the trash; returns what is left
    (caller holds store.LOCK and publishes once it is released)."""
    now = time.time()
    trash = (_trash(now) + [{"id": f["id"], "fact": f, "deleted": now} for f in gone])[-MAX_FACTS:]
    store.save(TRASH_FILE, trash)
    left = [f for f in items if f not in gone]
    store.save(FILE, left)
    return left


def _trash(now: float) -> list:
    """What was forgotten in the last TRASH_DAYS days (older entries are dropped)."""
    return [t for t in store.load(TRASH_FILE, []) if isinstance(t, dict) and isinstance(t.get("id"), str)
            and isinstance(t.get("fact"), dict) and isinstance(t["fact"].get("text"), str)
            and isinstance(t.get("deleted"), (int, float)) and now - t["deleted"] < TRASH_DAYS * 86400]


def undo(fact_id: str) -> dict | None:
    """Bring a forgotten fact back from the trash (None if it isn't there any more)."""
    now = time.time()
    with store.LOCK:
        trash = _trash(now)
        entry = next((t for t in reversed(trash) if t["id"] == str(fact_id or "")), None)
        if entry is None:
            return None
        trash.remove(entry)
        store.save(TRASH_FILE, trash)
        items = facts()
        fact = entry["fact"]
        if not any(f["id"] == fact["id"] or f["text"].lower() == str(fact.get("text", "")).lower()
                   for f in items):
            items = (items + [fact])[-MAX_FACTS:]
            store.save(FILE, items)
    events.publish("memory", {"facts": items})
    return fact


def edit(fact_id: str, text: str, via: str = "pc") -> dict | None:
    """Correct a fact's wording (None: no such fact). ValueError on an empty or too long text.
    The fact takes the origin of whoever wrote these words (via)."""
    text = _clean(text)
    if not text:
        raise ValueError("Le souvenir ne peut pas être vide.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"Souvenir trop long : {MAX_TEXT} caractères au plus.")
    with store.LOCK:
        items = facts()
        fact = next((f for f in items if f["id"] == str(fact_id or "")), None)
        if fact is None:
            return None
        fact["text"] = text
        fact["edited"] = time.time()
        if _is_pc(via):
            fact.pop("via", None)
        else:
            fact["via"] = str(via)
        store.save(FILE, items)
    events.publish("memory", {"facts": items})
    return fact


def as_text(limit_chars: int = 3000, pc_only: bool = False) -> str:
    """Newest facts first, as a bullet list that fits the budget. pc_only: only
    the facts written on the PC (a full-access task's prompt: a phone in other
    hands must not leave it standing orders that outlive the phone's removal)."""
    lines, used = [], 0
    for fact in reversed(facts()):
        if pc_only and not _is_pc(fact.get("via")):
            continue
        line = f"- {fact['text']}"
        if used + len(line) > limit_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
