"""Reminders, timers and routines, plus the morning briefing.

Items live in data/schedules.json so they survive a restart; anything that
came due while JARVIS was off fires once at the next start.
"""
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import config, events, store, tasks

FILE = "schedules.json"
STATE_FILE = "state.json"
REPEATS = ("none", "daily", "weekdays", "weekly")
REPEAT_LABELS = {"daily": "chaque jour", "weekdays": "en semaine", "weekly": "chaque semaine"}
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]
_CLOCK = re.compile(r"(\d{1,2})\s*[:hH]\s*(\d{2})?")

_stop = threading.Event()
_thread = None


def fr_date(dt: datetime) -> str:
    return f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def compute_due(at: str | None = None, delay_minutes=None, now: datetime | None = None) -> datetime:
    """When something is due: in N minutes, at HH:MM (next occurrence) or at an ISO date."""
    now = now or datetime.now()
    if delay_minutes not in (None, ""):
        minutes = float(delay_minutes)
        if minutes <= 0:
            raise ValueError("le délai doit être positif")
        return now + timedelta(minutes=minutes)
    at = (at or "").strip()
    if not at:
        raise ValueError("précise un délai (delay_minutes) ou une heure (at)")
    clock = _CLOCK.fullmatch(at)
    if clock:
        due = now.replace(hour=int(clock.group(1)), minute=int(clock.group(2) or 0),
                          second=0, microsecond=0)
        return due if due > now else due + timedelta(days=1)
    try:
        due = datetime.fromisoformat(at)
    except ValueError:
        raise ValueError(f"heure non comprise : {at} (attendu HH:MM ou AAAA-MM-JJTHH:MM)") from None
    if due.tzinfo:
        due = due.astimezone().replace(tzinfo=None)
    if due < now - timedelta(minutes=1):
        raise ValueError("cette date est déjà passée")
    return due


def next_due(due: float, repeat: str, after: float) -> float:
    """Next occurrence of a repeating item strictly after `after`."""
    dt = datetime.fromtimestamp(due)
    step = timedelta(days=7 if repeat == "weekly" else 1)
    while dt.timestamp() <= after or (repeat == "weekdays" and dt.weekday() >= 5):
        dt += step  # naive local time: 08:00 stays 08:00 across DST changes
    return dt.timestamp()


# ---------------------------------------------------------------- items

def items() -> list:
    return sorted(store.load(FILE, []), key=lambda i: i["due"])


def add(kind: str, title: str, text: str, at: str | None = None, delay_minutes=None,
        repeat: str = "none", profile: str = "recherche", complexity: str = "normale") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("texte du rappel ou consigne de la tâche manquant")
    repeat = repeat if repeat in REPEATS else "none"
    due = compute_due(at, delay_minutes).timestamp()
    if repeat == "weekdays":
        due = next_due(due, repeat, due - 1)  # a weekday routine set on a Saturday starts Monday
    item = {
        "id": uuid.uuid4().hex[:6], "kind": "task" if kind == "task" else "reminder",
        "title": (title or text).strip()[:80], "text": text[:4000], "due": due, "repeat": repeat,
        "profile": profile if profile in tasks.PROFILES else "recherche",
        "complexity": complexity if complexity in config.MODELS else "normale",
        "created": time.time(),
    }
    with store.LOCK:
        all_items = store.load(FILE, []) + [item]
        store.save(FILE, all_items)
    _publish(all_items)
    return item


def cancel(query: str) -> list:
    """Remove the item with this id, or every item whose title/text contains the words."""
    q = (query or "").strip().lower()
    if not q:
        raise ValueError("précise le rappel à annuler")
    with store.LOCK:
        all_items = store.load(FILE, [])
        gone = ([i for i in all_items if i["id"] == q]
                or [i for i in all_items if q in i["title"].lower() or q in i["text"].lower()])
        if gone:
            all_items = [i for i in all_items if i not in gone]
            store.save(FILE, all_items)
    if gone:
        _publish(all_items)
    return gone


def describe(item: dict, now: datetime | None = None) -> str:
    dt = datetime.fromtimestamp(item["due"])
    today = (now or datetime.now()).date()
    if dt.date() == today:
        day = "aujourd'hui"
    elif dt.date() == today + timedelta(days=1):
        day = "demain"
    else:
        day = f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]}"
    what = "Tâche" if item["kind"] == "task" else "Rappel"
    repeat = REPEAT_LABELS.get(item.get("repeat"), "")
    return (f"[{item['id']}] {what} {day} à {dt:%H:%M} : {item['title']}"
            + (f" ({repeat})" if repeat else ""))


def _publish(all_items: list):
    events.publish("schedules", {"items": sorted(all_items, key=lambda i: i["due"])})


# ---------------------------------------------------------------- clock

def tick(now: float | None = None):
    now = time.time() if now is None else now
    fired, keep = [], []
    with store.LOCK:
        for item in store.load(FILE, []):
            if item["due"] > now:
                keep.append(item)
                continue
            fired.append(dict(item))
            if item.get("repeat", "none") != "none":
                item["due"] = next_due(item["due"], item["repeat"], now)
                keep.append(item)
        if fired:
            store.save(FILE, keep)
    for item in fired:
        try:
            _fire(item, late=now - item["due"])
        except Exception:  # noqa: BLE001 - one bad item must not stop the others
            logging.exception("JARVIS: échec du déclenchement de %s", item.get("id"))
    if fired:
        _publish(keep)
    _maybe_briefing(now)


def _fire(item: dict, late: float):
    if item["kind"] == "task":
        tasks.create_task(item["title"], item["text"], profile=item.get("profile", "recherche"),
                          complexity=item.get("complexity", "normale"), origin="routine")
    else:
        events.publish("reminder", {"id": item["id"], "title": item["title"], "text": item["text"],
                                    "late_minutes": int(late // 60) if late > 120 else 0})


def _maybe_briefing(now: float):
    clock = _CLOCK.fullmatch(config.BRIEFING_TIME or "")
    if not clock:
        return
    current = datetime.fromtimestamp(now)
    try:
        target = current.replace(hour=int(clock.group(1)), minute=int(clock.group(2) or 0),
                                 second=0, microsecond=0)
    except ValueError:  # 25:00 and the like
        return
    # Only that morning: switched on at 15:00, JARVIS doesn't brief about the morning.
    if not target <= current < target + timedelta(hours=4):
        return
    today = current.date().isoformat()
    with store.LOCK:
        state = store.load(STATE_FILE, {})
        if state.get("briefing_date") == today:
            return
        state["briefing_date"] = today
        store.save(STATE_FILE, state)
    tasks.create_task("Briefing du matin", briefing_prompt(current),
                      profile="recherche", complexity="simple", origin="briefing")


def briefing_prompt(now: datetime) -> str:
    todays = [describe(i, now) for i in items()
              if datetime.fromtimestamp(i["due"]).date() == now.date()]
    city = (f" à {config.CITY}" if config.CITY
            else " (dans la ville de l'utilisateur si le contexte l'indique ; sinon passe ce point)")
    reminders = "\n".join(todays) or "aucun"
    return (f"Prépare le briefing du matin de l'utilisateur pour aujourd'hui, {fr_date(now)}.\n"
            f"1. La météo du jour{city}.\n"
            "2. Son agenda du jour et ses e-mails importants non lus, uniquement si tu disposes "
            "de connecteurs pour y accéder (sinon, ignore ce point sans le mentionner).\n"
            f"3. Ses rappels du jour :\n{reminders}\n"
            "4. Deux ou trois actualités marquantes du jour.\n"
            "Réponds en français, de façon courte et structurée, pour une lecture à voix haute "
            "(pas de tableau, pas de liens).")


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="jarvis-scheduler")
    _thread.start()


def stop():
    _stop.set()


def _loop():
    while not _stop.is_set():
        try:
            tick()
        except Exception:  # noqa: BLE001
            logging.exception("JARVIS: erreur du planificateur")
        _stop.wait(1.0)
