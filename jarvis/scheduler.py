"""Reminders, timers and routines.

Items live in data/schedules.json so they survive a restart; anything that
came due while JARVIS was off fires once at the next start, except routines
(Claude tasks) more than 2 hours late: those are skipped, rescheduled, and
monsieur is told.

- compute_due() reads what the voice model or the page sends: a delay in
  minutes, 'HH:MM', an ISO date, or plain French ('à midi', 'dans un quart
  d'heure', 'mardi prochain à 9 h', 'demain soir à 8 h', 'le 12 octobre').
  Every refusal is a French sentence the model can repeat.
- Repeats: daily, weekdays, weekly, monthly (same day each month, the last
  day when a month is shorter) and days (a list of weekdays).
- A reminder that fires is recorded in the inbox before any page hears of it;
  with no page open, a native notification. The last ten fired reminders are
  kept in state.json (recent_fired) so they can be snoozed: snooze() puts a
  copy back, due a few minutes later.
- A full-access routine is only ever created past monsieur's "oui"
  (confirm.gate in front of the voice tool); the page's own route refuses it
  and an existing one's instruction or frequency can't be edited.
- A hand-edited entry without an id or a due date is ignored (logged once an
  hour), never a crash of the clock.
- The morning briefing is built locally by briefing.py; the clock loop also
  keeps the PC awake while tasks run and refreshes the A.R.E.S agenda.
"""
import calendar
import logging
import math
import re
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta

from . import config, desktop, events, inbox, store, tasks

FILE = "schedules.json"
STATE_FILE = "state.json"  # shared: recent_fired and briefing_date are ours
REPEATS = ("none", "daily", "weekdays", "weekly", "monthly", "days")
REPEAT_LABELS = {"daily": "chaque jour", "weekdays": "en semaine", "weekly": "chaque semaine",
                 "monthly": "chaque mois"}
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]
DAY_KEYS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
_CLOCK = re.compile(r"(\d{1,2})\s*[:hH]\s*(\d{2})?")

MISSED_ROUTINE_S = 120 * 60   # a routine later than this is skipped, not run
LATE_S = 120                  # a reminder later than this says so
RECENT_FIRED = 10             # fired reminders kept for snooze
RECENT_HOURS = 24             # snooze('last') only looks this far back
MAX_DELAY_MIN = 366 * 1440
MAX_SNOOZE_MIN = 7 * 1440
MAX_AHEAD = timedelta(days=5 * 366)
DEFAULT_HOUR = 9              # a day without a time: 09:00 (A.R.E.S's own convention)
KEEP_AWAKE_TICKS = 30         # the loop ticks once a second
REFRESH_TICKS = 300           # A.R.E.S agenda and remarques every 5 minutes
LOG_EVERY_S = 3600

_stop = threading.Event()
_thread = None
_refresher = None  # the last background refresh (tests wait for it)
_logged: dict = {}  # message key -> when it was last logged


class T:
    """French wording returned to the voice model and shown by the page."""
    bad_delay = "délai invalide : donnez un nombre de minutes"
    nonpositive = "délai invalide : donnez un nombre de minutes positif"
    too_long = "délai invalide : un an au plus"
    need_when = "précisez un délai (delay_minutes) ou une heure (at)"
    bad_hour = "heure invalide : {text} (les heures vont de 0 à 23, les minutes de 0 à 59)"
    not_understood = ("heure non comprise : {text} (par exemple 14:30, à midi, demain à 9 h, "
                      "mardi prochain à 9 h, dans un quart d'heure ou 2026-10-12T09:00)")
    past = "cette date est déjà passée"
    past_today = "cette heure est déjà passée aujourd'hui"
    too_far = "cette date est trop lointaine (cinq ans au plus)"
    bad_day = "jour invalide : {text}"
    bad_repeat = "répétition inconnue : {text} (none, daily, weekdays, weekly, monthly ou days)"
    need_days = "précisez les jours de la semaine (par exemple lun, mer, ven)"
    empty = "texte du rappel ou consigne de la tâche manquant"
    empty_title = "titre vide"
    # A reminder set from a conversation that read outside content: its words
    # are never put back into a model's prompt (the side panel shows them).
    outside = "(texte venu de données externes, lisible dans la liste des rappels)"
    which = "précisez le rappel à annuler"
    ambiguous = "Plusieurs rappels correspondent : précisez lequel (son id ou d'autres mots)."
    not_found = "Rappel introuvable : il a peut-être déjà été supprimé."
    no_recent = "Aucun rappel n'est arrivé récemment : rien à reporter."
    not_recent = "Ce rappel n'est pas arrivé récemment : modifiez plutôt son heure."
    snooze_ambiguous = "Plusieurs rappels récents correspondent : précisez lequel."
    complet_api = "Une routine avec accès complet se programme à la voix, avec votre confirmation."
    complet_locked = ("La consigne et la fréquence d'une routine avec accès complet ne se modifient pas : "
                      "supprimez-la et redemandez-la à JARVIS.")
    nothing = "rien à modifier"
    skipped = "Routine « {title} » non lancée : JARVIS était éteint à l'heure prévue ({when})."
    skipped_next = " Prochaine fois : {when}."
    failed = "Routine « {title} » non lancée : {why}"
    remote_refused = ("Routine « {title} » non lancée : elle venait d'un appareil retiré ou demandait "
                      "l'accès complet.")


class Ambiguous(ValueError):
    """Several items match: nothing was done, monsieur must say which."""

    def __init__(self, message: str, matches: list):
        super().__init__(message)
        self.matches = matches


def fr_date(dt: datetime) -> str:
    return f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def fr_time(dt: datetime) -> str:
    """'9 h', '14 h 30': as JARVIS says it."""
    return f"{dt.hour} h" if dt.minute == 0 else f"{dt.hour} h {dt.minute:02d}"


def _log_once(key: str, message: str, *args, level=logging.WARNING, exc_info=False):
    """The same problem logged at most once an hour (a broken entry, a loop error)."""
    now = time.monotonic()
    last = _logged.get(key)
    if last is not None and now - last < LOG_EVERY_S:
        return
    if len(_logged) > 200:
        _logged.clear()
    _logged[key] = now
    logging.log(level, message, *args, exc_info=exc_info)

# ---------------------------------------------------------------- reading French

def _fold(text) -> str:
    """Lower case, no accents, plain apostrophes and single spaces."""
    text = unicodedata.normalize("NFD", str(text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("’", "'").replace("‘", "'").replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip(" .!?;")


_UNITS = {"zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
          "sept": 7, "huit": 8, "neuf": 9}
_TEENS = {"dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
          "seize": 16, "dix-sept": 17, "dix-huit": 18, "dix-neuf": 19}
_TENS = {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50}


def _number_words(limit: int) -> dict:
    """French words for 0..limit (with and without hyphens): 'vingt et une' -> 21."""
    out = dict(_UNITS)
    out.update(_TEENS)
    out.update({k.replace("-", " "): v for k, v in _TEENS.items()})
    for word, tens in _TENS.items():
        out[word] = tens
        for unit_word, unit in _UNITS.items():
            if unit == 0:
                continue
            glue = ["et"] if unit == 1 else [""]
            for g in glue:
                for sep in (" ", "-"):
                    parts = [word, g, unit_word] if g else [word, unit_word]
                    out[sep.join(parts)] = tens + unit
    return {k: v for k, v in out.items() if v <= limit}


_WORDS = _number_words(59)
_WORDS_RE = "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True))


def _number(text) -> float | None:
    """'15', '1,5', 'quinze', 'vingt-cinq', 'une' -> a number (None if not one)."""
    text = _fold(text)
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return float(text.replace(",", "."))
    return float(_WORDS[text]) if text in _WORDS else None


def parse_delay(value) -> float:
    """A delay in minutes, from a number or a string ('10', '7,5', '10 min')."""
    if isinstance(value, bool):
        raise ValueError(T.bad_delay)
    if isinstance(value, (int, float)):
        minutes = float(value)
    else:
        text = _fold(value)
        text = re.sub(r"\s*(?:minutes?|mn|min)$", "", text)
        try:
            minutes = float(text.replace(",", "."))
        except ValueError:
            raise ValueError(T.bad_delay) from None
    if not math.isfinite(minutes):
        raise ValueError(T.bad_delay)
    if minutes <= 0:
        raise ValueError(T.nonpositive)
    if minutes > MAX_DELAY_MIN:
        raise ValueError(T.too_long)
    return minutes


_UNIT_MIN = {"minute": 1, "minutes": 1, "min": 1, "mn": 1, "heure": 60, "heures": 60, "h": 60,
             "jour": 1440, "jours": 1440, "semaine": 10080, "semaines": 10080}
_SPECIAL_DURATIONS = {"un quart d'heure": 15, "quart d'heure": 15, "1/4 d'heure": 15,
                      "une demi-heure": 30, "une demi heure": 30, "demi-heure": 30,
                      "trois quarts d'heure": 45, "3/4 d'heure": 45, "3 quarts d'heure": 45}


def _duration(text: str) -> tuple | None:
    """'un quart d'heure', '2 heures et demie', '1 h 30', 'dix minutes', '3 jours'
    -> (minutes, counted in days?) or None."""
    text = text.strip()
    if text in _SPECIAL_DURATIONS:
        return _SPECIAL_DURATIONS[text], False
    m = re.fullmatch(r"(.+?) ?(?:heures?|h) et (demie?|quart)", text)
    if m and (n := _number(m.group(1))) is not None:
        return n * 60 + (30 if m.group(2).startswith("demi") else 15), False
    m = re.fullmatch(r"(.+?) ?(?:heures?|h) ?(\d{1,2}|" + _WORDS_RE + r")(?: ?(?:minutes?|min|mn))?", text)
    if m and (n := _number(m.group(1))) is not None and (mins := _number(m.group(2))) is not None:
        return n * 60 + mins, False
    m = re.fullmatch(r"(.+?) ?(minutes?|min|mn|heures?|h|jours?|semaines?)", text)
    if m and (n := _number(m.group(1))) is not None:
        unit = _UNIT_MIN[m.group(2)]
        return n * unit, unit >= 1440
    return None


_HOUR_WORDS = "|".join(sorted((re.escape(w) for w, v in _WORDS.items() if v <= 23), key=len, reverse=True))
_TIME = re.compile(
    r"\b(?P<h>\d{1,2}|" + _HOUR_WORDS + r") ?(?:heures?|h|:) ?(?P<m>\d{1,2}|" + _WORDS_RE + r")?"
    r"(?: ?(?:minutes?|min|mn))?"
    r"(?: (?P<frac>et demie?|et quart|moins le quart|moins (?P<less>\d{1,2}|" + _WORDS_RE + r")))?"
    r"(?: (?P<part>du matin|du soir|de l'apres-midi|de l'apres midi|de la nuit))?(?![\w/])")
# Not the 'midi' of 'apres-midi' / 'apres midi' (an afternoon, not noon).
_NOON = re.compile(r"\b(?P<w>(?<!apres[- ])midi|minuit)"
                   r"(?: (?P<frac>et demie?|et quart|moins le quart|moins (?P<less>\d{1,2}|"
                   + _WORDS_RE + r")))?\b")
_MONTHS_FOLDED = [_fold(m) for m in MONTHS]
_WEEKDAY = r"(?P<wd>lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
_PART = r"(?: (?P<part>matin|apres-midi|apres midi|soir|soiree|nuit))?"
_DAY_PATTERNS = [
    ("today", re.compile(r"(?:aujourd'hui|aujourdhui|aujourd hui)" + _PART)),
    ("today", re.compile(r"(?:ce |cet |cette )(?P<part>matin|apres-midi|apres midi|soir|soiree|nuit)")),
    ("tomorrow", re.compile(r"demain" + _PART)),
    ("after", re.compile(r"(?:apres-demain|apres demain)" + _PART)),
    ("weekday", re.compile(_WEEKDAY + r"(?P<next> prochain)?" + _PART)),
    ("date", re.compile(r"(?:" + _WEEKDAY + r" )?(?P<d>\d{1,2}|1er|premier) (?P<mon>" + "|".join(_MONTHS_FOLDED)
                        + r")(?: (?P<y>\d{4}))?" + _PART)),
    ("slash", re.compile(r"(?:" + _WEEKDAY + r" )?(?P<d>\d{1,2})/(?P<mon>\d{1,2})(?:/(?P<y>\d{2}|\d{4}))?" + _PART)),
    ("dom", re.compile(r"(?P<d>\d{1,2}|1er|premier)" + _PART)),
]
_CONNECTORS = {"a", "vers", "le", "pour", "au", "de", "et", "des", ","}
_PART_HOURS = {"matin": 9, "apres-midi": 14, "apres midi": 14, "soir": 19, "soiree": 19, "nuit": 22}


def _clock_of(match, now_text: str) -> tuple:
    """(hour, minute, part) from a _TIME or _NOON match; raises on 25:00."""
    groups = match.groupdict()
    if "w" in groups and groups.get("w"):
        h, m = (12 if groups["w"] == "midi" else 0), 0
    else:
        h = _number(groups["h"])
        m = _number(groups["m"]) if groups.get("m") else 0
        if h is None or m is None:
            raise ValueError(T.not_understood.format(text=now_text))
        h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(T.bad_hour.format(text=now_text))
    frac = groups.get("frac") or ""
    if frac.startswith("et demi"):
        m += 30
    elif frac == "et quart":
        m += 15
    elif frac == "moins le quart":
        h, m = (h - 1) % 24, m + 45
    elif frac.startswith("moins") and groups.get("less"):
        less = _number(groups["less"])
        if less is None or not 0 < less < 60:
            raise ValueError(T.bad_hour.format(text=now_text))
        total = (h * 60 + m - int(less)) % 1440
        h, m = divmod(total, 60)
    if m >= 60:
        h, m = (h + m // 60) % 24, m % 60
    part = (groups.get("part") or "").replace("du ", "").replace("de l'", "").replace("de la ", "")
    return h, m, part


def _evening(h: int, part: str) -> int:
    """'8 h du soir', 'demain soir à 8 h' -> 20 h."""
    if part in ("soir", "soiree", "apres-midi", "apres midi") and h < 12:
        return h + 12
    if part == "nuit" and 6 <= h < 12:
        return h + 12
    return h


def _day_of(text: str, now: datetime, original: str):
    """(date, part, rule) for the day words left once the time is taken out
    (False: not a day we know). rule 'weekday': a plain weekday that is today,
    which means next week once today's time is past."""
    split = text.replace(",", " ").split()
    words = [w for w in split if w not in _CONNECTORS]
    rest = " ".join(words)
    if not rest:
        return None, "", "none"
    for kind, pattern in _DAY_PATTERNS:
        m = pattern.fullmatch(rest)
        if not m or (kind == "dom" and "le" not in split):  # '9' alone is no date: 'le 9' is
            continue
        part = (m.groupdict().get("part") or "")
        today = now.date()
        if kind == "today":
            return today, part, "fixed"
        if kind == "tomorrow":
            return today + timedelta(days=1), part, "fixed"
        if kind == "after":
            return today + timedelta(days=2), part, "fixed"
        if kind == "weekday":
            ahead = (DAYS.index(m.group("wd")) - today.weekday()) % 7
            if m.group("next"):
                return today + timedelta(days=ahead or 7), part, "fixed"
            return today + timedelta(days=ahead), part, "weekday" if ahead == 0 else "fixed"
        day_word = m.group("d")
        d = 1 if day_word in ("1er", "premier") else int(day_word)
        if kind == "dom":
            year, month = today.year, today.month
            try:
                when = date(year, month, d)
            except ValueError:
                raise ValueError(T.bad_day.format(text=original)) from None
            if when < today:
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
                try:
                    when = date(year, month, d)
                except ValueError:
                    raise ValueError(T.bad_day.format(text=original)) from None
            return when, part, "fixed"
        month = (_MONTHS_FOLDED.index(m.group("mon")) + 1) if kind == "date" else int(m.group("mon"))
        year_text = m.group("y")
        year = int(year_text) + (2000 if year_text and len(year_text) == 2 else 0) if year_text else today.year
        try:
            when = date(year, month, d)
        except ValueError:
            raise ValueError(T.bad_day.format(text=original)) from None
        if when < today:
            if year_text:
                raise ValueError(T.past)
            try:
                when = date(year + 1, month, d)
            except ValueError:  # 29 February
                raise ValueError(T.bad_day.format(text=original)) from None
        return when, part, "fixed"
    return False, "", "none"


def _natural(text: str, now: datetime) -> datetime | None:
    """Plain French -> when (None: not French we know)."""
    t = _fold(text)
    if t.startswith("a ") or t.startswith("vers "):
        t = t.split(" ", 1)[1]
    m = re.fullmatch(r"(?:dans|d'ici) (?P<dur>.+?)(?: (?:a|vers) (?P<time>.+))?", t)
    if m:
        found = _duration(m.group("dur"))
        if found is None:
            return None
        minutes, in_days = found
        if minutes <= 0 or minutes > MAX_DELAY_MIN:
            raise ValueError(T.too_long if minutes > 0 else T.nonpositive)
        if m.group("time"):
            if not in_days:
                return None
            clock = _TIME.fullmatch(m.group("time")) or _NOON.fullmatch(m.group("time"))
            if not clock:
                return None
            h, mi, part = _clock_of(clock, text)
            day = now.date() + timedelta(days=int(minutes // 1440))
            return datetime(day.year, day.month, day.day, _evening(h, part), mi)
        return now + timedelta(minutes=minutes)
    clock = _NOON.search(t) or _TIME.search(t)
    rest = t
    h = mi = None
    time_part = ""
    if clock:
        h, mi, time_part = _clock_of(clock, text)
        rest = (t[:clock.start()] + " " + t[clock.end():]).strip()
    day, day_part, rule = _day_of(rest, now, text)
    if day is False:
        return None
    if h is None:
        if day is None:
            return None
        h, mi = _PART_HOURS.get(day_part, DEFAULT_HOUR), 0
    else:
        h = _evening(h, time_part or day_part)
    if day is None:  # a time alone: its next occurrence
        due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return due if due > now else due + timedelta(days=1)
    due = datetime(day.year, day.month, day.day, h, mi)
    if due <= now:
        if rule == "weekday":
            return due + timedelta(days=7)
        if day == now.date():
            raise ValueError(T.past_today)
        raise ValueError(T.past)
    return due


def compute_due(at: str | None = None, delay_minutes=None, now: datetime | None = None) -> datetime:
    """When something is due: in N minutes, at HH:MM (next occurrence), at an
    ISO date, or plain French. Raises ValueError with a French reason."""
    now = now or datetime.now()
    if delay_minutes not in (None, ""):
        return now + timedelta(minutes=parse_delay(delay_minutes))
    at = str(at or "").strip()
    if not at:
        raise ValueError(T.need_when)
    if len(at) > 120:
        raise ValueError(T.not_understood.format(text=at[:60] + "…"))
    clock = _CLOCK.fullmatch(at)
    if clock:
        h, m = int(clock.group(1)), int(clock.group(2) or 0)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(T.bad_hour.format(text=at))
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return due if due > now else due + timedelta(days=1)
    due = None
    if re.match(r"\d{4}-\d{2}-\d{2}", at):
        # The tool contract is local time: a trailing 'Z' is read as local, the
        # same on Python 3.10 (which refuses it) as on 3.11+ (which reads UTC).
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ][\d:.]+[Zz]", at):
            at = at[:-1]
        try:
            due = datetime.fromisoformat(at)
        except ValueError:
            raise ValueError(T.not_understood.format(text=at)) from None
        if due.tzinfo:
            due = due.astimezone().replace(tzinfo=None)
        if due < now - timedelta(minutes=1):
            raise ValueError(T.past)
    else:
        due = _natural(at, now)
        if due is None:
            raise ValueError(T.not_understood.format(text=at))
    if due > now + MAX_AHEAD:
        raise ValueError(T.too_far)
    return due

# ---------------------------------------------------------------- repeats

def parse_days(value) -> list:
    """['lun', 'mercredi', 4], 'lun,mer,ven' or 'lun-ven' -> sorted weekday numbers."""
    if value in (None, "", []):
        return []
    tokens = value if isinstance(value, (list, tuple)) else re.split(r"[,;\s]+", str(value))
    out = set()
    for token in tokens:
        if isinstance(token, bool):
            raise ValueError(T.bad_day.format(text=token))
        if isinstance(token, int):
            if not 0 <= token <= 6:
                raise ValueError(T.bad_day.format(text=token))
            out.add(token)
            continue
        word = _fold(token)
        if not word:
            continue
        if "-" in word and not word.startswith("-"):
            a, b = (_day_index(w, token) for w in word.split("-", 1))
            out.update(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])
        else:
            out.add(_day_index(word, token))
    return sorted(out)


def _day_index(word: str, original) -> int:
    key = word[:3]
    if key not in DAY_KEYS:
        raise ValueError(T.bad_day.format(text=original))
    return DAY_KEYS.index(key)


def next_due(due: float, repeat: str, after: float, days=None, month_day=None) -> float:
    """Next occurrence of a repeating item strictly after `after` (naive local
    time: 08:00 stays 08:00 across DST changes)."""
    dt = datetime.fromtimestamp(due)
    if repeat == "monthly":
        anchor = int(month_day or dt.day)
        while dt.timestamp() <= after:
            year, month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
            dt = dt.replace(year=year, month=month, day=min(anchor, calendar.monthrange(year, month)[1]))
        return dt.timestamp()
    allowed = set(days or []) if repeat == "days" else None
    if allowed is not None and not allowed:
        allowed = None  # no day left: every day rather than never
    step = timedelta(days=7 if repeat == "weekly" else 1)
    behind = (after - dt.timestamp()) / 86400
    if behind > 14:  # long off: jump close instead of walking day by day
        jump = int(behind) - 8
        dt += timedelta(days=jump - jump % 7 if repeat == "weekly" else jump)
    while (dt.timestamp() <= after or (repeat == "weekdays" and dt.weekday() >= 5)
           or (allowed is not None and dt.weekday() not in allowed)):
        dt += step
    return dt.timestamp()


def _repeat(item: dict) -> str:
    repeat = item.get("repeat") or "none"
    return repeat if repeat in REPEATS else "none"


def _next(item: dict, after: float) -> float:
    return next_due(item["due"], _repeat(item), after, item.get("days"), item.get("month_day"))

# ---------------------------------------------------------------- items

def _valid(item) -> bool:
    if not isinstance(item, dict):
        return False
    due, key = item.get("due"), item.get("id")
    return (isinstance(key, str) and bool(key.strip()) and isinstance(due, (int, float))
            and not isinstance(due, bool) and math.isfinite(due) and 0 < due < 32503680000)


def _note_invalid(raw: list):
    bad = [i for i in raw if not _valid(i)]
    if bad:
        _log_once("schedules-invalid", "JARVIS: %d entrée(s) de %s sans id ou sans date valable : ignorée(s)",
                  len(bad), FILE)


def _load() -> list:
    raw = store.load(FILE, [])
    return raw if isinstance(raw, list) else []


def items() -> list:
    """The valid items, soonest first (a broken hand edit is skipped, not fatal)."""
    raw = _load()
    _note_invalid(raw)
    return sorted((i for i in raw if _valid(i)), key=lambda i: i["due"])


def add(kind: str, title: str, text: str, at: str | None = None, delay_minutes=None,
        repeat: str = "none", profile: str = "recherche", complexity: str = "normale",
        days=None, allow_complet: bool = False, *, via: str = "pc", tainted: bool = False) -> dict:
    """A new reminder or routine. allow_complet: only past confirm.gate (the
    voice tool, once monsieur said "oui"); every other caller is refused.
    via: who asked, the origin string of remote.Caller ("pc", "app:d_…"); a
    full-access routine is the PC's alone. tainted: asked from a conversation
    that read outside content (label() keeps its words out of every prompt)."""
    text = (text or "").strip()
    if not text:
        raise ValueError(T.empty)
    # Exactly confirm.gate's reading: only 'task' (any case, no padding) is a routine.
    is_task = str(kind or "").lower() == "task"
    repeat = str(repeat or "none").strip().lower()
    day_list = parse_days(days)
    if day_list and repeat in ("none", ""):
        repeat = "days"
    if repeat not in REPEATS:
        raise ValueError(T.bad_repeat.format(text=repeat[:20]))
    if repeat == "days" and not day_list:
        raise ValueError(T.need_days)
    profile = profile if is_task and profile in tasks.PROFILES else "recherche"
    if profile == "complet" and (not allow_complet or _kind(via) != "pc"):
        # From a phone or Siri never, whatever allow_complet says: an approved
        # routine would run with full access after the opt-in or the device is gone.
        raise PermissionError(T.complet_api)
    due = compute_due(at, delay_minutes).timestamp()
    item = {
        "id": uuid.uuid4().hex[:6], "kind": "task" if is_task else "reminder",
        "title": (title or text).strip()[:80], "text": text[:4000], "due": due, "repeat": repeat,
        "profile": profile,
        "complexity": complexity if complexity in config.MODELS else "normale",
        "created": time.time(), "via": str(via or "pc"),
    }
    if tainted:
        item["tainted"] = True
    _shape_repeat(item, day_list)
    with store.LOCK:
        all_items = _load() + [item]
        store.save(FILE, all_items)
    _publish(all_items)
    return item


def _shape_repeat(item: dict, day_list: list):
    """Weekday routines set on a Saturday start Monday; monthly keeps its day."""
    item.pop("days", None)
    item.pop("month_day", None)
    if item["repeat"] == "days":
        item["days"] = day_list
    if item["repeat"] == "monthly":
        item["month_day"] = datetime.fromtimestamp(item["due"]).day
    if item["repeat"] in ("weekdays", "days"):
        item["due"] = next_due(item["due"], item["repeat"], item["due"] - 1, item.get("days"))


def update(item_id: str, *, title=None, text=None, at=None, delay_minutes=None, due=None,
           repeat=None, days=None, now: float | None = None) -> dict:
    """Change a reminder or a routine (the side panel's ✎). A full-access
    routine keeps its instruction and its frequency: those were what monsieur
    said "oui" to. Raises KeyError (unknown), PermissionError, ValueError."""
    now = time.time() if now is None else now
    changes = {}
    if title is not None:
        title = " ".join(str(title).split())[:80]
        if not title:
            raise ValueError(T.empty_title)
        changes["title"] = title
    if text is not None:
        text = str(text).strip()[:4000]
        if not text:
            raise ValueError(T.empty)
        changes["text"] = text
    if at not in (None, "") or delay_minutes not in (None, ""):
        changes["due"] = compute_due(at, delay_minutes, datetime.fromtimestamp(now)).timestamp()
    elif due is not None:
        if isinstance(due, bool) or not isinstance(due, (int, float)) or not math.isfinite(due):
            raise ValueError(T.not_understood.format(text=due))
        if due < now - 60:
            raise ValueError(T.past)
        if datetime.fromtimestamp(due) > datetime.fromtimestamp(now) + MAX_AHEAD:
            raise ValueError(T.too_far)
        changes["due"] = float(due)
    day_list = parse_days(days) if days is not None else None
    if repeat is not None or day_list:
        repeat = str(repeat or ("days" if day_list else "none")).strip().lower()
        if repeat not in REPEATS:
            raise ValueError(T.bad_repeat.format(text=repeat[:20]))
        changes["repeat"] = repeat
    if not changes:
        raise ValueError(T.nothing)
    with store.LOCK:
        all_items = _load()
        item = next((i for i in all_items if _valid(i) and i["id"] == item_id), None)
        if item is None:
            raise KeyError(T.not_found)
        locked = item.get("kind") == "task" and item.get("profile") == "complet"
        if locked and (("text" in changes and changes["text"] != item.get("text"))
                       or ("repeat" in changes and changes["repeat"] != _repeat(item))
                       or (day_list is not None and day_list != item.get("days"))):
            raise PermissionError(T.complet_locked)
        # The panel shows a reminder's label only: renamed there, it says the new words.
        if "title" in changes and "text" not in changes and item.get("kind") != "task":
            changes["text"] = changes["title"]
        new_days = day_list if day_list is not None else list(item.get("days") or [])
        if changes.get("repeat", _repeat(item)) == "days" and not new_days:
            raise ValueError(T.need_days)
        item.update(changes)
        if "title" in changes:  # monsieur's own words now (the side panel's ✎)
            item.pop("tainted", None)
        item["repeat"] = _repeat(item)
        if "due" in changes or "repeat" in changes or day_list is not None:
            _shape_repeat(item, new_days)
        item["edited"] = now
        store.save(FILE, all_items)
    _publish(all_items)
    return dict(item)


def _matching(q: str, pool: list) -> list:
    """By id first, then by words in the title or the text; an exact title wins."""
    q = q.strip().casefold()
    by_id = [i for i in pool if str(i.get("id", "")).casefold() == q]
    if by_id:
        return by_id
    words = [i for i in pool if q in str(i.get("title", "")).casefold() or q in str(i.get("text", "")).casefold()]
    exact = [i for i in words if str(i.get("title", "")).casefold() == q]
    return exact if len(exact) == 1 else words


def cancel(query: str, all_matches: bool = False) -> list:
    """Remove the item with this id, or the one whose title/text holds these
    words. Several matches: Ambiguous, and nothing is removed (unless
    all_matches: monsieur said 'tous')."""
    q = (query or "").strip()
    if not q:
        raise ValueError(T.which)
    with store.LOCK:
        all_items = _load()
        gone = _matching(q, [i for i in all_items if _valid(i)])
        if len(gone) > 1 and not all_matches:
            raise Ambiguous(T.ambiguous, sorted(gone, key=lambda i: i["due"]))
        if gone:
            ids = {i["id"] for i in gone}
            all_items = [i for i in all_items if not (_valid(i) and i["id"] in ids)]
            store.save(FILE, all_items)
    if gone:
        _publish(all_items)
    return gone


def remove(item_id: str) -> list:
    """By id only (the side panel's ✕): words in a URL never remove several items."""
    with store.LOCK:
        all_items = _load()
        gone = [i for i in all_items if _valid(i) and i["id"] == item_id]
        if gone:
            all_items = [i for i in all_items if not (_valid(i) and i["id"] == item_id)]
            store.save(FILE, all_items)
    if gone:
        _publish(all_items)
    return gone


def _repeat_label(item: dict) -> str:
    repeat = _repeat(item)
    if repeat == "days":
        names = [DAYS[d] for d in item.get("days") or [] if isinstance(d, int) and 0 <= d <= 6]
        if not names:
            return ""
        return "le " + (", ".join(names[:-1]) + " et " + names[-1] if len(names) > 1 else names[0])
    return REPEAT_LABELS.get(repeat, "")


def when_text(ts: float, now: datetime | None = None) -> str:
    dt = datetime.fromtimestamp(ts)
    today = (now or datetime.now()).date()
    if dt.date() == today:
        day = "aujourd'hui"
    elif dt.date() == today + timedelta(days=1):
        day = "demain"
    else:
        day = f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]}"
    return f"{day} à {dt:%H:%M}"


def label(item: dict) -> str:
    """The title a model may read: never the words of an item set from a
    conversation that read outside content (they would reach every later
    prompt, the PC's untainted ones included)."""
    return T.outside if item.get("tainted") else str(item.get("title", ""))


def describe(item: dict, now: datetime | None = None) -> str:
    what = "Tâche" if item.get("kind") == "task" else "Rappel"
    repeat = _repeat_label(item)
    return (f"[{item.get('id', '?')}] {what} {when_text(item['due'], now)} : {label(item)}"
            + (f" ({repeat})" if repeat else ""))


def _publish(all_items: list):
    events.publish("schedules", {"items": sorted((i for i in all_items if _valid(i)), key=lambda i: i["due"])})

# ---------------------------------------------------------------- snooze

def _recent(state: dict) -> list:
    recent = state.get("recent_fired")
    return [r for r in recent if _valid(r)] if isinstance(recent, list) else []


def recent_fired(hours: float | None = None, now: float | None = None) -> list:
    """The last fired reminders, newest first (state.json)."""
    now = time.time() if now is None else now
    out = _recent(store.load(STATE_FILE, {}))
    if hours is not None:
        out = [r for r in out if now - r.get("fired", 0) <= hours * 3600]
    # Stored oldest first: reversed, a stable sort keeps the newest first on equal times.
    return sorted(reversed(out), key=lambda r: r.get("fired", 0), reverse=True)


def _remember_fired(payload: dict, now: float, tainted: bool = False):
    entry = {"id": payload["id"], "title": payload.get("title", ""), "text": payload.get("text", ""),
             "due": now, "fired": now, "late_minutes": payload.get("late_minutes", 0),
             "via": str(payload.get("via") or "pc")}  # a snoozed copy goes back to whoever set it
    if tainted:  # and keeps its words out of the prompts
        entry["tainted"] = True
    with store.LOCK:
        state = store.load(STATE_FILE, {})
        recent = [r for r in _recent(state) if r["id"] != entry["id"]] + [entry]
        state["recent_fired"] = recent[-RECENT_FIRED:]
        store.save(STATE_FILE, state)


def find_recent(query: str = "", now: float | None = None, exact: bool = False) -> dict:
    """The fired reminder monsieur means: the last one, or the one matching
    these words or this id (exact: the id only). Raises LookupError or Ambiguous."""
    q = (query or "").strip()
    if not q or q.casefold() in ("last", "dernier", "le dernier"):
        recent = recent_fired(RECENT_HOURS, now)
        if not recent:
            raise LookupError(T.no_recent)
        return recent[0]
    pool = recent_fired(None, now)
    matches = [r for r in pool if r["id"] == q] if exact else _matching(q, pool)
    if not matches:
        raise LookupError(T.not_recent)
    if len({m["id"] for m in matches}) > 1:
        raise Ambiguous(T.snooze_ambiguous, matches)
    return matches[0]


def snooze(ref: str = "last", minutes=10, now: float | None = None, exact: bool = True) -> dict:
    """Bring a fired reminder back in `minutes` (a one-off copy). ref: 'last'
    or its id (exact=False: words too). Snoozing the same reminder again moves
    that copy instead of making another."""
    now = time.time() if now is None else now
    minutes = parse_delay(minutes)
    if minutes > MAX_SNOOZE_MIN:
        raise ValueError(T.too_long)
    entry = find_recent(ref, now, exact=exact)
    due = now + minutes * 60
    with store.LOCK:
        state = store.load(STATE_FILE, {})
        recent = _recent(state)
        stored = next((r for r in recent if r["id"] == entry["id"]), None)
        all_items = _load()
        copy_id = (stored or {}).get("snoozed_to")
        item = next((i for i in all_items if _valid(i) and copy_id and i["id"] == copy_id), None)
        if item is not None:
            item["due"] = due
        else:
            item = {"id": uuid.uuid4().hex[:6], "kind": "reminder",
                    "title": (entry.get("title") or entry.get("text") or "Rappel")[:80],
                    "text": (entry.get("text") or entry.get("title") or "")[:4000],
                    "due": due, "repeat": "none", "profile": "recherche", "complexity": "normale",
                    "created": now, "snoozed_from": entry["id"],
                    # Still that device's reminder: the PC never tells a phone's, nor the phone the PC's.
                    "via": str(entry.get("via") or "pc")}
            if entry.get("tainted"):
                item["tainted"] = True
            all_items.append(item)
            if stored is not None:
                stored["snoozed_to"] = item["id"]
                state["recent_fired"] = recent
                store.save(STATE_FILE, state)
        store.save(FILE, all_items)
    _publish(all_items)
    return dict(item)

# ---------------------------------------------------------------- clock

def tick(now: float | None = None):
    now = time.time() if now is None else now
    fired, skipped, keep = [], [], []
    changed = False
    with store.LOCK:
        raw = _load()
        for item in raw:
            if not _valid(item):
                keep.append(item)  # left as it is for monsieur to fix; never fired
                continue
            if item["due"] > now:
                keep.append(item)
                continue
            changed = True
            entry = dict(item)  # as it was due (late is counted from this)
            missed = item.get("kind") == "task" and now - item["due"] > MISSED_ROUTINE_S
            (skipped if missed else fired).append(entry)
            if _repeat(item) != "none":
                item["due"] = entry["next"] = _next(item, now)
                keep.append(item)
        if changed:
            store.save(FILE, keep)
    _note_invalid(raw)
    for item in fired:
        try:
            _fire(item, late=now - item["due"], now=now)
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the others
            if isinstance(exc, ValueError) and item.get("kind") == "task":
                # A refusal (the daily cap, say), not a bug: one line, no traceback
                # in data/jarvis.log at every routine of the day.
                logging.info("JARVIS: routine %s non lancée : %s", item.get("id"), exc)
            else:
                logging.exception("JARVIS: échec du déclenchement de %s", item.get("id"))
            if item.get("kind") == "task":
                _warn(T.failed.format(title=item.get("title", ""), why=exc), f"routine-{item['id']}-{int(now)}")
    for item in skipped:
        _skip(item)
    if changed:
        _publish(keep)
    _maybe_briefing(now)


def _kind(via) -> str:
    from . import remote  # remote is imported late (spec: no import cycle)
    return remote.kind_of(via)


def _may_run(item: dict, via: str) -> bool:
    """A routine set from a phone or Siri runs only while that device is still
    allowed, and never with full access (old data or any path that slipped by)."""
    from . import remote
    if _kind(via) == "pc":
        return True
    return item.get("profile") != "complet" and remote.origin_active(via)


def _fire(item: dict, late: float, now: float | None = None):
    now = time.time() if now is None else now
    via = item.get("via") or "pc"  # who set it (older items: the PC)
    if item.get("kind") == "task":
        if not _may_run(item, via):
            _warn(T.remote_refused.format(title=item.get("title", "")), f"routine-refusee-{item['id']}-{int(now)}")
            return
        tasks.create_task(item.get("title", ""), item.get("text", ""), profile=item.get("profile", "recherche"),
                          complexity=item.get("complexity", "normale"), origin="routine", via=via)
        return
    payload = {"id": item["id"], "title": item.get("title", ""), "text": item.get("text", ""),
               "late_minutes": int(late // 60) if late > LATE_S else 0, "via": via}
    try:
        _remember_fired(payload, now, tainted=bool(item.get("tainted")))
    except Exception:  # noqa: BLE001 - snooze is a convenience: the reminder still goes out
        logging.exception("JARVIS: rappel non noté pour le report")
    # The inbox first: with no page open, nothing is lost.
    try:
        payload["inbox_id"] = inbox.add("reminder", payload)["id"]
    except Exception:  # noqa: BLE001 - events.publish records it as a fallback
        logging.exception("JARVIS: boîte de réception indisponible pour un rappel")
    events.publish("reminder", payload)
    if not events.has_subscribers():
        inbox.notify_offline("Rappel", payload["text"] or payload["title"], kind="reminder", via=via)


def _skip(item: dict):
    """A routine due while JARVIS was off: not run hours late; monsieur is told."""
    text = T.skipped.format(title=item.get("title", ""), when=when_text(item["due"]))
    if item.get("next"):
        text += T.skipped_next.format(when=when_text(item["next"]))
    logging.info("JARVIS: routine %s manquée de plus de 2 h : non lancée", item.get("id"))
    _warn(text, f"routine-manquee-{item['id']}-{int(item['due'])}")


def _warn(text: str, ref: str):
    try:
        events.publish("warning", {"id": ref, "text": text})
    except Exception:  # noqa: BLE001
        logging.exception("JARVIS: avertissement non publié")


def _maybe_briefing(now: float):
    from . import briefing  # late: briefing reads the items of this module
    briefing.maybe_run(now)


def briefing_days() -> set:
    """Réglages › Proactivité › Jours du briefing (see briefing.briefing_days)."""
    from . import briefing
    return briefing.briefing_days()


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
    n = 0
    while not _stop.is_set():
        try:
            tick()
        except Exception as exc:  # noqa: BLE001
            _log_once(f"loop:{type(exc).__name__}:{str(exc)[:200]}", "JARVIS: erreur du planificateur",
                      level=logging.ERROR, exc_info=True)
        n += 1
        try:
            periodic(n)
        except Exception as exc:  # noqa: BLE001
            _log_once(f"periodic:{type(exc).__name__}:{str(exc)[:200]}", "JARVIS: tâche de fond du planificateur",
                      level=logging.ERROR, exc_info=True)
        _stop.wait(1.0)


def periodic(n: int):
    """Every tick n of the loop: keep the PC awake while tasks run (every 30
    ticks), refresh the A.R.E.S agenda and the remarques (every 5 minutes)."""
    global _refresher
    if n % KEEP_AWAKE_TICKS == 0 and tasks.running():
        desktop.keep_awake()
    if n % REFRESH_TICKS == 0 and not (_refresher and _refresher.is_alive()):
        # A.R.E.S may take a second or two to answer: never on the clock's thread.
        _refresher = threading.Thread(target=_refresh, daemon=True, name="jarvis-scheduler-refresh")
        _refresher.start()


def _refresh():
    from . import ares, remarques  # late: optional neighbours
    try:
        if ares.available():
            ares.refresh()
    except Exception:  # noqa: BLE001
        _log_once("ares-refresh", "JARVIS: agenda A.R.E.S non rafraîchi", exc_info=True)
    try:
        remarques.refresh()
    except Exception:  # noqa: BLE001
        _log_once("remarques-refresh", "JARVIS: remarques non rafraîchies", exc_info=True)
