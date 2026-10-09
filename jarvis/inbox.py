"""Messages for monsieur that no page has told him yet: the source of truth
for delivery, plus the rules every way out respects (quiet hours, « Ne pas
déranger », a busy screen).

- Reminders, task results, briefings and warnings are recorded in
  data/inbox.json as events.publish() pushes them (publishers may also call
  add() first: the same message is never recorded twice).
- The leader page acknowledges an item once monsieur has really been told.
  A page that opens gets the unacknowledged items of the last 24 hours and
  tells them as « Pendant votre absence : … ». Items are kept 7 days.
- With no page open at all, notify_offline() sends one native notification
  and, for a reminder, may bring the JARVIS window back.
"""
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, desktop, events, store

router = APIRouter()

FILE = "inbox.json"
STATE_FILE = "state.json"  # shared with the scheduler: dnd_until lives there
KEEP_DAYS = 7
REPLAY_HOURS = 24
DEDUPE_SECONDS = 600  # a publisher's add() and its publish() are the same message
MAX_ITEMS = 500
MAX_OUTPUT = 4000  # a task's output is kept up to what delivery reads out
# tasks.py statuses; a cancelled task was monsieur's own doing. 'interrompue':
# JARVIS was closed while it ran ('interrupted' kept for older history files).
FINAL_TASK = ("done", "error", "interrompue", "interrupted")
_RANGE = re.compile(r"^\s*(\d{1,2})(?:\s*[:hH]\s*(\d{2})?)?\s*(?:-|–|—|à)\s*"
                    r"(\d{1,2})(?:\s*[:hH]\s*(\d{2})?)?\s*$")
_toasted: dict = {}  # (title, body) -> when: one native notification per message


# ---------------------------------------------------------------- the inbox

def deliverable(kind: str, data: dict) -> bool:
    if kind == "task":
        return data.get("status") in FINAL_TASK
    return kind in ("reminder", "briefing", "warning")


def _ref(kind: str, payload: dict) -> str:
    key = payload.get("id")
    if key:
        return f"{kind}:{key}:{payload.get('status', '')}" if kind == "task" else f"{kind}:{key}"
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return f"{kind}:{hashlib.sha1(blob.encode('utf-8')).hexdigest()[:16]}"


def _slim(kind: str, payload: dict) -> dict:
    payload = {k: v for k, v in payload.items() if k != "inbox_id"}
    if kind == "task" and isinstance(payload.get("output"), str):
        payload["output"] = payload["output"][:MAX_OUTPUT]
    return payload


def add(kind: str, payload: dict) -> dict:
    """Record a message for monsieur (or return the same one, already recorded)."""
    payload = _slim(kind, dict(payload or {}))
    ref = _ref(kind, payload)
    now = time.time()
    with store.LOCK:
        items = _prune(store.load(FILE, []), now)
        for item in items:
            if item.get("ref") == ref and now - item.get("created", 0) < DEDUPE_SECONDS:
                return item
        item = {"id": uuid.uuid4().hex[:12], "kind": kind, "payload": payload,
                "created": now, "acked": None, "ref": ref}
        items.append(item)
        store.save(FILE, items[-MAX_ITEMS:])
    return item


def _prune(items: list, now: float) -> list:
    return [i for i in items if isinstance(i, dict) and i.get("id")
            and now - i.get("created", 0) < KEEP_DAYS * 86400]


def pending(now: float | None = None) -> list:
    """Not yet told, from the last 24 hours, oldest first."""
    now = time.time() if now is None else now
    items = [i for i in store.load(FILE, []) if isinstance(i, dict) and i.get("id")
             and not i.get("acked") and now - i.get("created", 0) < REPLAY_HOURS * 3600]
    return sorted(items, key=lambda i: i.get("created", 0))


def ack(item_id: str) -> bool:
    with store.LOCK:
        items = store.load(FILE, [])
        for item in items:
            if isinstance(item, dict) and item.get("id") == item_id:
                if not item.get("acked"):
                    item["acked"] = time.time()
                    store.save(FILE, items)
                break
        else:
            return False
    events.publish("inbox", {"acked": [item_id]})
    return True


def on_publish(kind: str, data: dict) -> dict:
    """events.publish() hook: record what monsieur must hear, and give the page
    the inbox id it acknowledges once he has heard it."""
    if not deliverable(kind, data):
        return data
    if data.get("inbox_id"):  # the publisher recorded it itself
        return data
    item = add(kind, data)
    if kind == "task":  # nobody else raises the alarm for a task that ends with no page open
        notify_offline(f"Tâche « {data.get('title', '')} »",
                       "Terminée." if data.get("status") == "done" else "Elle n'a pas abouti.",
                       kind="task")
    return {**data, "inbox_id": item["id"]}

# ---------------------------------------------------------------- quiet hours & « Ne pas déranger »

def _minutes(h, m) -> int | None:
    h, m = int(h), int(m or 0)
    return h * 60 + m if 0 <= h <= 24 and 0 <= m <= 59 and h * 60 + m <= 1440 else None


def quiet_hours(now: datetime | None = None, spec: str | None = None) -> bool:
    """Inside config.QUIET_HOURS ('22:30-07:30'; empty or 'off' = never)?"""
    spec = (config.QUIET_HOURS if spec is None else spec) or ""
    if spec.strip().lower() in ("", "0", "off", "non", "aucune", "none"):
        return False
    match = _RANGE.match(spec)
    start = _minutes(match.group(1), match.group(2)) if match else None
    end = _minutes(match.group(3), match.group(4)) if match else None
    if start is None or end is None:
        logging.warning("JARVIS: heures calmes illisibles (%r) : ignorées", spec)
        return False
    now = now or datetime.now()
    current = now.hour * 60 + now.minute
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end  # across midnight


def dnd_until(now: float | None = None) -> float | None:
    """End of « Ne pas déranger » (epoch seconds), or None when it is off."""
    now = time.time() if now is None else now
    until = store.load(STATE_FILE, {}).get("dnd_until")
    return until if isinstance(until, (int, float)) and until > now else None


def set_dnd(until: float | None) -> float | None:
    now = time.time()
    if until is not None and not (now < until <= now + 7 * 86400):
        until = None if until <= now else now + 7 * 86400
    with store.LOCK:
        state = store.load(STATE_FILE, {})
        if until:
            state["dnd_until"] = until
        else:
            state.pop("dnd_until", None)
        store.save(STATE_FILE, state)
    events.publish("dnd", {"until": until})
    return until


def is_quiet(now: datetime | None = None) -> bool:
    """Quiet hours or « Ne pas déranger »: nothing is said aloud."""
    return quiet_hours(now) or dnd_until() is not None


def _attention() -> str:
    try:
        return desktop.attention_state()
    except Exception:  # noqa: BLE001 - unknown means don't hold anything back
        return "ok"


def notify_offline(title: str, body: str, kind: str = "reminder") -> bool:
    """No JARVIS page open: one native notification instead (the message
    stays in the inbox for the next page). A reminder monsieur set always
    notifies; anything else waits out quiet hours and a busy screen. Returns
    whether something was shown."""
    if events.leader() is not None:
        return False  # a page will tell him
    now = time.time()
    for key in [k for k, t in _toasted.items() if now - t > DEDUPE_SECONDS]:
        del _toasted[key]
    if (title, body) in _toasted:
        return False
    quiet, attention = is_quiet(), _attention()
    if kind != "reminder" and (quiet or attention != "ok"):
        return False
    _toasted[(title, body)] = now
    try:
        desktop.toast(title, body)
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: notification Windows impossible")
    if kind == "reminder" and config.REOPEN_ON_REMINDER and attention == "ok" and not quiet:
        try:
            desktop.show_app_window(f"http://127.0.0.1:{config.PORT}")
        except Exception:  # noqa: BLE001
            logging.exception("JARVIS: impossible de rouvrir la fenêtre")
    return True

# ---------------------------------------------------------------- routes

@router.get("/api/inbox")
def get_inbox():
    return pending()


@router.post("/api/inbox/{item_id}/ack")
def post_ack(item_id: str):
    if not ack(item_id):
        raise HTTPException(404, "Message introuvable.")
    return {"ok": True}


class PresenceIn(BaseModel):
    client: str
    focused: bool | None = None
    live: bool | None = None
    claim: bool = False


@router.post("/api/presence")
def post_presence(body: PresenceIn):
    try:
        return {"leader": events.presence(body.client, body.focused, body.live, body.claim)}
    except ValueError as exc:
        raise HTTPException(400, f"Présence refusée : {exc}.") from None


@router.get("/api/delivery")
def get_delivery():
    """What the leader page needs to decide how to tell something."""
    return {"leader": events.leader(), "quiet_hours": config.QUIET_HOURS,
            "quiet": quiet_hours(), "dnd_until": dnd_until(), "attention": _attention()}


class DndIn(BaseModel):
    until: float | None = None    # epoch seconds; empty or past = off
    minutes: float | None = None  # or a duration from now


@router.post("/api/dnd")
def post_dnd(body: DndIn):
    until = body.until
    if body.minutes is not None:
        until = time.time() + body.minutes * 60 if body.minutes > 0 else None
    return {"until": set_dnd(until)}
