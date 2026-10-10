"""« JARVIS a remarqué » : at most three one-line suggestions at the top of
the side panel, found here with simple rules (no Claude task, no network
beyond the A.R.E.S agenda snapshot JARVIS already keeps):

- at least two A.R.E.S tasks overdue → « Replanifier ? » (prefills the composer);
- a Claude task that failed in the last 24 hours and was not tried again
  → « Relancer ? » (the panel's own retry route: full access still asks first);
- reminders that went off late because JARVIS was closed at the time.

The ✕ silences a remark: one day the first time, then twice as long each
time (120 days at most), kept in data/remarques.json. Only the keys of the
remarks currently shown can be dismissed, so the file stays small.

The voice hears at most one remark, at the opening of a session (through the
voice instructions, tools_agenda.instructions_block), and never the same one
twice within 12 hours. Its wording never quotes a title: task titles and
agenda lines are not JARVIS's own words.
"""
import logging
import re
import threading
import time

from . import ares, events, scheduler, store, tasks

FILE = "remarques.json"
MAX = 3
BASE_DAYS = 1
MAX_DAYS = 120
RECENT_S = 24 * 3600
VOICE_AGAIN_S = 12 * 3600
KEEP_ENTRIES = 100
FAILED = ("error", "interrompue", "interrupted")
_KEY = re.compile(r"^[\w-]{1,64}$")

_lock = threading.Lock()
_last: dict = {"items": None}  # what the pages were last told
_voiced: dict = {}  # key -> when it was offered to a voice session


class T:
    overdue = "{n} tâches A.R.E.S sont en retard."
    overdue_why = "A.R.E.S les marque « en retard » dans son agenda."
    overdue_action = "Replanifier ?"
    overdue_prompt = "Aide-moi à replanifier mes tâches A.R.E.S en retard."
    overdue_voice = "{n} tâches de l'agenda A.R.E.S sont en retard : proposez de les replanifier."
    failed = "La tâche « {title} » a échoué."
    failed_why = "Échec il y a moins de 24 heures, sans nouvel essai depuis."
    failed_action = "Relancer ?"
    failed_voice = "Une tâche Claude a échoué ces dernières 24 heures : proposez de la relancer."
    missed_one = "Un rappel est arrivé en retard : JARVIS était fermé à l'heure prévue."
    missed_many = "{n} rappels sont arrivés en retard : JARVIS était fermé à l'heure prévue."
    missed_why = "Les rappels prévus pendant que JARVIS était fermé sont dits à son retour."
    missed_voice = ("Des rappels sont arrivés en retard parce que JARVIS était fermé : suggérez de "
                    "laisser JARVIS ouvert en arrière-plan.")
    unknown = "Remarque introuvable : elle a peut-être déjà disparu."


def _agenda_overdue() -> int:
    try:
        text = ares.agenda_text(1500, read_timeout=1.0)
    except Exception:  # noqa: BLE001 - A.R.E.S is optional
        logging.exception("JARVIS: agenda A.R.E.S illisible pour les remarques")
        return 0
    return sum(1 for line in ares.lines(text) if "en retard" in line.lower())


def _failed_task(now: float) -> dict | None:
    """The latest failed task of the last 24 h with no follow-up and no retry."""
    all_tasks = tasks.list_tasks()
    for task in all_tasks:  # newest first
        ended = task.get("ended") or task.get("started") or 0
        if task.get("status") not in FAILED or now - ended > RECENT_S:
            continue
        if task.get("origin") == "approbation" or not str(task.get("title") or "").strip():
            continue
        followed = any(t.get("resumed_from") == task["id"]
                       or (t.get("id") != task["id"] and (t.get("started") or 0) >= (task.get("started") or 0)
                           and t.get("title") == task.get("title"))
                       for t in all_tasks)
        if not followed:
            return task
    return None


def detect(now: float | None = None) -> list:
    """Every remark that applies now, most useful first (before dismissals)."""
    now = time.time() if now is None else now
    out = []
    late = _agenda_overdue()
    if late >= 2:
        out.append({"key": "ares-retard", "kind": "review", "text": T.overdue.format(n=late),
                    "why": T.overdue_why, "voice": T.overdue_voice.format(n=late),
                    "action": {"type": "compose", "label": T.overdue_action, "text": T.overdue_prompt}})
    task = _failed_task(now)
    if task:
        out.append({"key": f"tache-{task['id']}"[:64], "kind": "question",
                    "text": T.failed.format(title=str(task.get("title"))[:80]),
                    "why": T.failed_why, "voice": T.failed_voice,
                    "action": {"type": "retry", "label": T.failed_action, "task": task["id"],
                               "title": str(task.get("title"))[:80]}})
    missed = [r for r in scheduler.recent_fired(RECENT_S / 3600, now) if r.get("late_minutes")]
    if missed:
        n = len(missed)
        out.append({"key": f"rappels-retard-{missed[0]['id']}"[:64], "kind": "notify",
                    "text": T.missed_one if n == 1 else T.missed_many.format(n=n),
                    "why": T.missed_why, "voice": T.missed_voice, "action": None})
    return out


def _dismissed(now: float) -> dict:
    """{key: {count, until, at}} (a hand-edited entry of another shape is dropped)."""
    data = store.load(FILE, {})
    if not isinstance(data, dict):
        return {}
    ok = (int, float)
    return {k: v for k, v in data.items() if isinstance(v, dict) and isinstance(v.get("until"), ok)
            and not isinstance(v.get("until"), bool) and isinstance(v.get("at", 0), ok)}


def items(now: float | None = None) -> list:
    """What the panel shows: at most three remarks not silenced."""
    now = time.time() if now is None else now
    silenced = _dismissed(now)
    shown = [r for r in detect(now) if not (silenced.get(r["key"], {}).get("until", 0) > now)]
    return shown[:MAX]


def public(items_list: list) -> list:
    return [{k: v for k, v in r.items() if k != "voice"} for r in items_list]


def dismiss(key: str, now: float | None = None) -> dict:
    """✕: silence this remark, twice as long as last time. KeyError if it isn't shown."""
    now = time.time() if now is None else now
    if not isinstance(key, str) or not _KEY.match(key):
        raise KeyError(T.unknown)
    if key not in {r["key"] for r in items(now)}:
        raise KeyError(T.unknown)
    with store.LOCK:
        data = _dismissed(now)
        entry = data.get(key) or {}
        count = int(entry.get("count", 0)) + 1 if isinstance(entry.get("count", 0), int) else 1
        days = min(MAX_DAYS, BASE_DAYS * 2 ** (count - 1))
        data[key] = {"count": count, "until": now + days * 86400, "at": now}
        # Old silences go once they would have ended long ago; never more than KEEP_ENTRIES.
        data = {k: v for k, v in data.items() if v.get("until", 0) > now - MAX_DAYS * 86400}
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-KEEP_ENTRIES:])
        store.save(FILE, data)
    refresh(force=True)
    return {"key": key, "days": days, "until": data[key]["until"]}


def refresh(force: bool = False) -> list:
    """Tell the pages when the remarks changed (the scheduler asks every 5 minutes)."""
    shown = public(items())
    with _lock:
        changed = shown != _last["items"]
        _last["items"] = shown
    if changed or force:
        events.publish("remarques", {"items": shown})
    return shown


def for_voice(now: float | None = None) -> str:
    """One remark for the opening of a voice session ('' for none)."""
    now = time.time() if now is None else now
    for remark in items(now):
        key = remark["key"]
        if now - _voiced.get(key, 0) < VOICE_AGAIN_S:
            continue
        _voiced[key] = now
        if len(_voiced) > 50:
            for old in sorted(_voiced, key=_voiced.get)[:25]:
                del _voiced[old]
        return remark["voice"]
    return ""
