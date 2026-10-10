"""What JARVIS costs, day by day (WP18), and the daily cap.

data/usage.json holds one entry per local date (the PC's clock), 90 days kept:

    {"2026-10-10": {"realtime": {"text_in", "text_cached", "text_out",
                                 "audio_in", "audio_cached", "audio_out",
                                 "image_in", "image_cached", "transcribe_in",
                                 "transcribe_out", "transcribe_seconds", "usd"},
                    "claude": {"usd", "tasks"}}}

- The voice (OpenAI Realtime): the page adds up the usage of every
  response.done and of every transcription of what monsieur said, and posts
  it every 30 s and when the conversation ends (POST /api/usage). It is priced
  here as it arrives, at OpenAI's list prices (PRICES).
- Claude Code: the total_cost_usd each task reports (tasks._finish calls
  add_claude). Claude Code computes it itself, at API prices: with a claude.ai
  subscription nothing is billed per task. The page calls it an estimate.
- The cap (DAILY_BUDGET_USD, Réglages › Coûts) covers the voice and the tasks
  together. Once reached, /api/config says usage_capped: the wake word no
  longer opens a paid conversation (a click still can, after a confirmation)
  and no new Claude task starts (tasks._check_budget).
"""
import logging
import math
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import config, events, store

router = APIRouter()

FILE = "usage.json"
KEEP_DAYS = 90
WARN_RATIO = 0.8  # the page warns at 80 % of the daily cap
NOTE_FR = "Claude : estimation (équivalent API)"

# ---------------------------------------------------------------- prices
# OpenAI list prices in USD per 1M tokens (developers.openai.com/api/docs/pricing,
# read on 2026-10-09). 'cached' is cached input. Reasoning tokens (gpt-realtime-2.x)
# are billed as text output.
_FULL = {"audio_in": 32.0, "audio_cached": 0.40, "audio_out": 64.0,
         "text_in": 4.0, "text_cached": 0.40, "image_in": 5.0, "image_cached": 0.50}
_MINI = {"audio_in": 10.0, "audio_cached": 0.30, "audio_out": 20.0,
         "text_in": 0.60, "text_cached": 0.06, "text_out": 2.40, "image_in": 0.80, "image_cached": 0.08}
PRICES = {
    "gpt-realtime-2.1": {**_FULL, "text_out": 24.0},
    "gpt-realtime-2.1-mini": dict(_MINI),
    "gpt-realtime-2": {**_FULL, "text_out": 24.0},
    "gpt-realtime": {**_FULL, "text_out": 16.0},
    "gpt-realtime-mini": dict(_MINI),
}
# A model missing from the table is priced as the dearest one: the cap errs on the safe side.
DEFAULT_MODEL = "gpt-realtime-2.1"
TOKEN_CLASSES = ("text_in", "text_cached", "text_out", "audio_in", "audio_cached", "audio_out",
                 "image_in", "image_cached")

# Transcription of what monsieur says, billed apart at the ASR model's rate:
# per minute, or per 1M tokens for the models that report tokens.
TRANSCRIBE_PER_MINUTE = {"gpt-4o-mini-transcribe": 0.003, "gpt-4o-transcribe": 0.006, "whisper-1": 0.006,
                         "gpt-transcribe": 0.0045, "gpt-live-transcribe": 0.017}
TRANSCRIBE_PER_1M = {"gpt-4o-mini-transcribe": {"in": 1.25, "out": 5.00},
                     "gpt-4o-transcribe": {"in": 2.50, "out": 10.00}}
USER_AUDIO_TOKENS_PER_SECOND = 10  # 1 token per 100 ms of monsieur's voice
_SNAPSHOT = re.compile(r"(.+)-\d{4}-\d{2}-\d{2}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

# One post covers 30 s of conversation: far below these, whatever happens.
MAX_TOKENS = 50_000_000
MAX_SECONDS = 86_400


def resolve_model(model: str) -> str:
    """The PRICES entry for a model name: itself, or the model of a dated
    snapshot ('gpt-realtime-mini-2025-10-06' -> 'gpt-realtime-mini'), else
    DEFAULT_MODEL."""
    model = str(model or "")
    snapshot = _SNAPSHOT.fullmatch(model)
    model = snapshot.group(1) if snapshot else model
    return model if model in PRICES else DEFAULT_MODEL


# ---------------------------------------------------------------- reading a usage object

def _count(value, limit=MAX_TOKENS) -> float:
    """A token count (or seconds) from the page: a finite number >= 0, else ValueError."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("nombre attendu")
    if value < 0 or value > limit:
        raise ValueError("nombre hors limites")
    return value


def _part(obj, key) -> dict:
    sub = obj.get(key) if isinstance(obj, dict) else None
    if sub is None:
        return {}
    if not isinstance(sub, dict):
        raise ValueError(f"{key} : objet attendu")
    return sub


def realtime_classes(usage: dict) -> dict:
    """response.done usage -> token counts per billing class. Cached tokens are
    a subset of the input ones; input or output the details leave unexplained
    (reasoning) count as text."""
    if not isinstance(usage, dict):
        raise ValueError("objet attendu")
    din, dout = _part(usage, "input_token_details"), _part(usage, "output_token_details")
    dcached = _part(din, "cached_tokens_details")
    out = {}
    total_in = _count(usage.get("input_tokens"))
    explained = 0
    for kind in ("text", "audio", "image"):
        seen = _count(din.get(f"{kind}_tokens"))
        cached = min(_count(dcached.get(f"{kind}_tokens")), seen)
        out[f"{kind}_in"], out[f"{kind}_cached"] = seen - cached, cached
        explained += seen
    out["text_in"] += max(0, total_in - explained)
    total_out = _count(usage.get("output_tokens"))
    out["audio_out"] = _count(dout.get("audio_tokens"))
    text_out = _count(dout.get("text_tokens"))
    out["text_out"] = text_out + max(0, total_out - out["audio_out"] - text_out)
    return out


def realtime_cost(classes: dict, model: str) -> float:
    prices = PRICES[resolve_model(model)]
    return sum(classes.get(k, 0) * prices.get(k, 0) for k in TOKEN_CLASSES) / 1e6


def _per_minute(model: str) -> float:
    """A model missing from the table costs the dearest rate (the cap errs on the safe side)."""
    return TRANSCRIBE_PER_MINUTE.get(model, max(TRANSCRIBE_PER_MINUTE.values()))


def transcription_parts(usage: dict, model: str) -> dict:
    """A transcription's usage ({type: 'tokens'|'duration', ...}) -> {in, out, seconds, usd}."""
    kind = usage.get("type")
    rate_min = _per_minute(model)
    if kind == "duration":
        seconds = _count(usage.get("seconds"), MAX_SECONDS)
        return {"in": 0, "out": 0, "seconds": seconds, "usd": seconds / 60 * rate_min}
    if kind != "tokens":
        raise ValueError("type d'usage inconnu")
    details = _part(usage, "input_token_details")
    tin, tout = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
    rates = TRANSCRIBE_PER_1M.get(model)
    if rates:
        usd = (tin * rates["in"] + tout * rates["out"]) / 1e6
    else:  # priced per minute only: its audio tokens say how long monsieur spoke
        audio = _count(details.get("audio_tokens")) or tin
        usd = audio / USER_AUDIO_TOKENS_PER_SECOND / 60 * rate_min
    return {"in": tin, "out": tout, "seconds": 0, "usd": usd}


# ---------------------------------------------------------------- the ledger

def _today() -> date:
    return datetime.now().date()


def _num(value) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0


def _entry(raw) -> dict:
    """One day, every field present and numeric (the file may have been edited by hand)."""
    raw = raw if isinstance(raw, dict) else {}
    rt = raw.get("realtime") if isinstance(raw.get("realtime"), dict) else {}
    cl = raw.get("claude") if isinstance(raw.get("claude"), dict) else {}
    realtime = {k: _num(rt.get(k)) for k in (*TOKEN_CLASSES, "transcribe_in", "transcribe_out",
                                             "transcribe_seconds", "usd")}
    return {"realtime": realtime, "claude": {"usd": _num(cl.get("usd")), "tasks": int(_num(cl.get("tasks")))}}


def _load() -> dict:
    data = store.load(FILE, {})
    return data if isinstance(data, dict) else {}


def _change(fn) -> dict:
    """Apply fn to today's entry and save; returns that entry."""
    today = _today()
    with store.LOCK:
        data = _load()
        key = today.isoformat()
        entry = _entry(data.get(key))
        fn(entry)
        entry["realtime"]["usd"] = round(entry["realtime"]["usd"], 6)
        entry["claude"]["usd"] = round(entry["claude"]["usd"], 6)
        data[key] = entry
        oldest = (today - timedelta(days=KEEP_DAYS - 1)).isoformat()
        data = {k: v for k, v in data.items() if _DAY.fullmatch(str(k)) and k >= oldest}
        store.save(FILE, data)
    _publish()
    return entry


def _day(key: str) -> dict:
    return _entry(_load().get(key))


def add_realtime(usage: dict, model: str = "") -> float:
    """A response.done usage (or the sum of several) of one voice model; returns its cost in USD."""
    classes = realtime_classes(usage)
    usd = realtime_cost(classes, model or config.REALTIME_MODEL)

    def apply(entry):
        for k, v in classes.items():
            entry["realtime"][k] += v
        entry["realtime"]["usd"] += usd
    _change(apply)
    return usd


def add_transcription(usage: dict, model: str = "") -> float:
    if not isinstance(usage, dict):
        raise ValueError("objet attendu")
    if model not in TRANSCRIBE_PER_MINUTE:
        from . import realtime  # late: realtime -> tools -> tasks imports this module
        model = realtime.transcribe_model()
    parts = transcription_parts(usage, model)

    def apply(entry):
        entry["realtime"]["transcribe_in"] += parts["in"]
        entry["realtime"]["transcribe_out"] += parts["out"]
        entry["realtime"]["transcribe_seconds"] += parts["seconds"]
        entry["realtime"]["usd"] += parts["usd"]
    _change(apply)
    return parts["usd"]


def add_claude(usd) -> None:
    """A finished task's cost (tasks._finish): Claude Code's own estimate."""
    usd = _count(usd, 10_000)
    if not usd:
        return

    def apply(entry):
        entry["claude"]["usd"] += usd
        entry["claude"]["tasks"] += 1
    _change(apply)


def realtime_spent_today() -> float:
    return _day(_today().isoformat())["realtime"]["usd"]


def claude_spent_today() -> float:
    return _day(_today().isoformat())["claude"]["usd"]


def spent_today() -> float:
    return realtime_spent_today() + claude_spent_today()


def daily_cap() -> float:
    """DAILY_BUDGET_USD when it is a usable amount, else 0 (no cap)."""
    cap = config.DAILY_BUDGET_USD
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap <= 0:
        return 0.0
    return float(cap)


def over_daily_cap() -> bool:
    """Voice and tasks together reached DAILY_BUDGET_USD (never when it is 0)."""
    cap = daily_cap()
    return cap > 0 and spent_today() >= cap


def _figures(key: str, entry: dict) -> dict:
    voice, claude = entry["realtime"]["usd"], entry["claude"]["usd"]
    return {"date": key, "realtime_usd": round(voice, 6), "claude_usd": round(claude, 6),
            "claude_tasks": entry["claude"]["tasks"], "total_usd": round(voice + claude, 6)}


def _capped(entry: dict, cap: float) -> bool:
    return cap > 0 and entry["realtime"]["usd"] + entry["claude"]["usd"] >= cap


def summary(days: int = 30) -> dict:
    """What the page shows: today, the last `days` days (oldest first, empty
    days included), the cap, and the prices it estimates the running conversation with."""
    from . import realtime  # late: realtime -> tools -> tasks imports this module
    data, today = _load(), _today()
    out = []
    for back in range(days - 1, -1, -1):
        key = (today - timedelta(days=back)).isoformat()
        out.append(_figures(key, _entry(data.get(key))))
    cap = daily_cap()
    transcribe = realtime.transcribe_model()
    return {"today": out[-1], "days": out, "budget": cap, "warn_ratio": WARN_RATIO,
            "capped": _capped(_entry(data.get(today.isoformat())), cap), "note": NOTE_FR,
            "model": config.REALTIME_MODEL, "default_model": DEFAULT_MODEL, "prices": PRICES,
            "transcribe": {"model": transcribe, "per_minute": _per_minute(transcribe),
                           "per_1m": TRANSCRIBE_PER_1M.get(transcribe)}}


def _publish():
    """Every page's chip follows, and the wake word learns about the cap at once."""
    try:
        key = _today().isoformat()
        cap, entry = daily_cap(), _day(key)
        events.publish("usage", {"today": _figures(key, entry), "budget": cap, "capped": _capped(entry, cap)})
    except Exception:  # noqa: BLE001 - counting must never fail because the push did
        logging.exception("JARVIS: dépense du jour non publiée")


# ---------------------------------------------------------------- routes

class UsageIn(BaseModel):
    usage: dict
    model: str = ""


@router.post("/api/usage")
def post_usage(body: UsageIn):
    model = body.model[:80]
    try:
        if "type" in body.usage:  # a transcription: {type: 'tokens'|'duration', ...}
            add_transcription(body.usage, model)
        else:
            add_realtime(body.usage, model)
    except ValueError:
        raise HTTPException(400, "Relevé de consommation invalide.") from None
    return summary(1)


@router.get("/api/usage")
def get_usage(days: int = Query(30, ge=1, le=KEEP_DAYS)):
    return summary(days)
