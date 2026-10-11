"""The morning briefing, built here in a blink, without any Claude task:
JARVIS's reminders and routines of the day, how many things monsieur has in
A.R.E.S today and how many are overdue, the weather in his city, and the
headlines if he asked for them (Réglages › Proactivité). The same facts
always give the same French text.

- Only on the days of BRIEFING_DAYS ('lun-ven' by default), once, between
  BRIEFING_TIME and four hours later (switched on in the afternoon, JARVIS
  doesn't brief about the morning), and never before the Mise en route is done.
- The text goes to the inbox first, then to the pages ('briefing'). During
  quiet hours or « Ne pas déranger » it is marked queued: the page keeps it
  behind the badge instead of saying it. With no page open, one native
  notification (which itself waits out quiet hours and a busy screen).
- Headlines come from the RSS feeds. If the feeds can't be read, one Claude
  task (profile recherche: the web, no files) looks them up; its prompt holds
  the date and nothing else: no agenda, no reminder, no memory, no city.
  Past the daily cap (Réglages › Coûts, usage.py) that task is not started:
  the briefing itself costs nothing, it is still built and delivered, and the
  payload says capped so the page reads it with the browser's own voice
  rather than inviting monsieur into a paid conversation.
"""
import logging
import re
import threading
from datetime import datetime, timedelta

from . import ares, config, events, health, inbox, info, scheduler, store, tasks, usage

STATE_FILE = "state.json"  # 'briefing_date' is ours
WINDOW = timedelta(hours=4)
NEWS_COUNT = 3
_CLOCK = re.compile(r"(\d{1,2})\s*[:hH]\s*(\d{2})?")

_thread = None  # the last briefing thread (tests wait for it)
_done = {"day": None}  # (data folder, day) already claimed: no state.json read every second


class T:
    title = "Briefing du matin"
    ready = "Votre briefing du matin est prêt."
    hello = "Bonjour monsieur. Nous sommes {day}."
    agenda_none = "Rien de prévu aujourd'hui dans A.R.E.S."
    agenda_today = "Dans A.R.E.S : {today} aujourd'hui"
    agenda_late = "{late} en retard"
    reminders = "Vos rappels du jour : {items}."
    no_reminders = "Aucun rappel JARVIS aujourd'hui."
    news = "Les titres : {items}."
    bye = "Bonne journée."
    news_title = "Actualités du matin"


def briefing_days() -> set:
    """config.BRIEFING_DAYS ('lun-ven', 'tous', 'lun,mer,ven') as weekday numbers;
    every day when it can't be read (a hand edit), rather than no briefing at all."""
    spec = str(config.BRIEFING_DAYS or "").strip().lower().replace(" ", "")
    if spec in ("", "tous", "*"):
        return set(range(7))
    try:
        days = set(scheduler.parse_days(spec))
    except ValueError:
        logging.warning("JARVIS: jours du briefing illisibles (%s) : tous les jours", spec[:40])
        return set(range(7))
    return days or set(range(7))


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def agenda_counts(text: str) -> tuple:
    """(today, overdue) from the A.R.E.S agenda: '• Titre — Aujourd'hui · 14:00',
    '… (en retard)', 'En retard (2 j)'."""
    today = late = 0
    for line in ares.lines(text):
        low = line.lower().replace("’", "'")
        if "en retard" in low:
            late += 1
        elif "aujourd'hui" in low or "aujourd hui" in low:
            today += 1
    return today, late


def todays_items(now: datetime) -> list:
    """JARVIS's reminders and routines still to come today, soonest first."""
    end = datetime(now.year, now.month, now.day) + timedelta(days=1)
    return [i for i in scheduler.items() if now.timestamp() <= i["due"] < end.timestamp()]


def _agenda_sentence() -> str:
    try:
        text = ares.agenda_text(1500)
    except Exception:  # noqa: BLE001 - A.R.E.S is optional
        logging.exception("JARVIS: agenda A.R.E.S du briefing")
        return ""
    if not text:
        return ""  # A.R.E.S off or away: nothing said rather than 'nothing planned'
    today, late = agenda_counts(text)
    if not today and not late:
        return T.agenda_none
    parts = [T.agenda_today.format(today=_count(today, "élément", "éléments"))]
    if late:
        parts.append(T.agenda_late.format(late=_count(late, "tâche", "tâches")))
    return ", ".join(parts) + "."


def _weather_sentence() -> str:
    if not config.CITY:
        return ""
    try:
        out = info.weather(config.CITY, "aujourdhui")
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: météo du briefing")
        return ""
    return str(out.get("text") or "") if out.get("ok") else ""


def _reminders_sentence(now: datetime) -> str:
    todays = todays_items(now)
    if not todays:
        return T.no_reminders
    said = []
    for item in todays[:6]:
        label = scheduler.label(item) or item.get("text") or ""  # never words from outside content
        if item.get("kind") == "task":
            label = f"routine « {label} »"
        said.append(f"{scheduler.fr_time(datetime.fromtimestamp(item['due']))}, {label}")
    more = len(todays) - len(said)
    return T.reminders.format(items=" ; ".join(said) + (f" ; et {more} de plus" if more > 0 else ""))


def headlines() -> list | None:
    """The day's headlines from the RSS feeds (None when they can't be read)."""
    try:
        out = info.news(NEWS_COUNT)
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: actualités du briefing")
        return None
    if not out.get("ok"):
        return None
    return [h["title"] for h in out.get("headlines") or [] if h.get("title")][:NEWS_COUNT]


def local_brief(now: datetime | None = None, news: list | None = None) -> str:
    """The briefing's French text. news: headlines to add (None: none)."""
    now = now or datetime.now()
    day = f"{scheduler.DAYS[now.weekday()]} {now.day} {scheduler.MONTHS[now.month - 1]}"
    parts = [T.hello.format(day=day), _weather_sentence(), _agenda_sentence(), _reminders_sentence(now)]
    if news:
        parts.append(T.news.format(items=" ; ".join(news)))
    parts.append(T.bye)
    return " ".join(p for p in parts if p)


def news_prompt(now: datetime) -> str:
    """The only prompt that goes to the web: the date, nothing of monsieur's."""
    return (f"Nous sommes le {scheduler.fr_date(now)}. Donne en français les deux ou trois "
            "actualités marquantes du jour, en une phrase chacune, pour une lecture à voix haute "
            "(pas de tableau, pas de lien).")


def capped() -> bool:
    """The daily cap reached (voice and tasks together); a broken counter never blocks."""
    try:
        return bool(usage.over_daily_cap())
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: dépense du jour illisible pour le briefing")
        return False


def deliver(text: str, now: datetime) -> dict:
    """Inbox first, then the pages; queued during quiet hours or « Ne pas déranger ».
    capped: the page says the text itself (free) instead of offering a session."""
    quiet = inbox.is_quiet(now)
    payload = {"id": f"briefing-{now.date().isoformat()}", "title": T.title, "text": text,
               "date": now.date().isoformat(), "queued": quiet, "capped": capped()}
    try:
        payload["inbox_id"] = inbox.add("briefing", payload)["id"]
    except Exception:  # noqa: BLE001 - events.publish records it as a fallback
        logging.exception("JARVIS: boîte de réception indisponible pour le briefing")
    events.publish("briefing", payload)
    if not quiet and not events.has_subscribers():
        inbox.notify_offline(T.title, T.ready, kind="briefing")
    return payload


def run(now: datetime | None = None) -> dict:
    """Build and deliver today's briefing (and, if needed, start the news task)."""
    now = now or datetime.now()
    news = None
    need_task = False
    if config.BRIEFING_NEWS:
        news = headlines()
        need_task = news is None
    payload = deliver(local_brief(now, news), now)
    if need_task and payload.get("capped"):
        logging.info("JARVIS: plafond du jour atteint : pas de tâche Claude pour les actualités du briefing")
        need_task = False
    if need_task:
        try:
            tasks.create_task(T.news_title, news_prompt(now), profile="recherche", complexity="simple",
                              origin="routine")
        except Exception:  # noqa: BLE001 - the daily cap reached, Claude missing...
            logging.exception("JARVIS: tâche des actualités non lancée")
    return payload


def maybe_run(now: float):
    """Called by the scheduler every second: today's briefing, once, on time."""
    global _thread
    clock = _CLOCK.fullmatch(config.BRIEFING_TIME or "")
    if not clock:
        return
    current = datetime.fromtimestamp(now)
    try:
        target = current.replace(hour=int(clock.group(1)), minute=int(clock.group(2) or 0),
                                 second=0, microsecond=0)
    except ValueError:  # 25:00 and the like
        return
    if not target <= current < target + WINDOW:
        return
    if current.weekday() not in briefing_days():  # Réglages › Proactivité › Jours du briefing
        return
    today = current.date().isoformat()
    claimed = (str(config.DATA_DIR), today)
    if _done["day"] == claimed:
        return
    # A first launch: the Mise en route comes first, not an empty briefing on
    # top of it. The day is not claimed: the briefing follows once it is done.
    if not health.onboarded():
        return
    with store.LOCK:
        state = store.load(STATE_FILE, {})
        if state.get("briefing_date") == today:
            _done["day"] = claimed
            return
        state["briefing_date"] = today
        store.save(STATE_FILE, state)
    _done["day"] = claimed
    # The weather and A.R.E.S take a second or two: not on the clock's thread,
    # where a reminder due meanwhile would wait.
    _thread = threading.Thread(target=_run_safely, args=(current,), daemon=True, name="jarvis-briefing")
    _thread.start()


def _run_safely(now: datetime):
    try:
        run(now)
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: briefing du matin non préparé")
