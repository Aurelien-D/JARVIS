"""What JARVIS costs, day by day (WP18), and the daily cap.

data/usage.json holds one entry per local date (the PC's clock), 90 days kept:

    {"2026-10-10": {"realtime": {"text_in", "text_cached", "text_out",
                                 "audio_in", "audio_cached", "audio_out",
                                 "image_in", "image_cached", "transcribe_in",
                                 "transcribe_out", "transcribe_seconds", "usd"},
                    "claude": {"usd", "tasks"},
                    "text": {"in", "cached", "cache_write", "out", "calls", "usd"}}}

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
- Siri's text model (raccourci.py, OpenAI Responses): each call's usage is
  priced here (add_text, TEXT_PRICES) under "text", present only once Siri
  spoke that day. It counts in spent_today and the cap like the rest.
- A paired iPhone (remote.py) reports its voice usage the same way, but a page
  away from home could lie: its reports are bounded per post and by the time
  elapsed since each voice session it opened today (REMOTE_*), and what it was
  granted is kept in the day entry, under "remote", so a restart grants nothing
  new: {"remote": {"d_<16 hex>": {"usd": 0.42, "mints": [epoch, ...]}}}.
"""
import logging
import math
import re
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
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

# Siri's text models (raccourci.MODELS), USD per 1M tokens, read on
# developers.openai.com/api/docs/pricing and the model pages on 2026-10-10.
# 'cached' and 'cache_write' are parts of the input. gpt-6-luna lists cache
# writes at 0.125 $; the others list none, so a write costs their input rate.
# Reasoning tokens are billed as output.
TEXT_PRICES = {
    "gpt-6-luna": {"in": 0.10, "cached": 0.01, "cache_write": 0.125, "out": 0.50},
    "gpt-5.6-luna": {"in": 0.20, "cached": 0.02, "cache_write": 0.20, "out": 1.20},
    "gpt-5.4-nano": {"in": 0.20, "cached": 0.02, "cache_write": 0.20, "out": 1.25},
    "gpt-4.1-nano": {"in": 0.10, "cached": 0.025, "cache_write": 0.10, "out": 0.40},
}
TEXT_CLASSES = ("in", "cached", "cache_write", "out")
# A model missing from the table (JARVIS_SIRI_MODEL) is priced as the dearest
# entry: the cap errs on the safe side.
TEXT_DEFAULT_MODEL = max(TEXT_PRICES, key=lambda m: (TEXT_PRICES[m]["out"], TEXT_PRICES[m]["in"]))

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

# A paired iPhone's reports (spec 4.8): at most this much per post, and in all
# at most the elapsed time of each voice session it opened today at this rate,
# capped per session. A page can then raise the ledger by 0.30 $ a minute per
# session at worst, and the mint limits stop a page that opens many.
REMOTE_POST_MAX_USD = 1.0
REMOTE_SESSION_ALLOWANCE_USD = 6.0
REMOTE_MAX_USD_PER_S = 0.005
REMOTE_MAX_DEVICES = 10
REMOTE_MAX_MINTS = 100
_DEVICE_ID = re.compile(r"d_[0-9a-f]{16}")


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


def resolve_text_model(model: str) -> str:
    """The TEXT_PRICES entry for a model name: itself, its dated snapshot's
    model, else the dearest entry."""
    model = str(model or "")
    snapshot = _SNAPSHOT.fullmatch(model)
    model = snapshot.group(1) if snapshot else model
    return model if model in TEXT_PRICES else TEXT_DEFAULT_MODEL


def text_classes(usage: dict) -> dict:
    """A Responses API usage -> token counts per billing class. Cached and
    cache-write tokens are parts of input_tokens (never more than it)."""
    if not isinstance(usage, dict):
        raise ValueError("objet attendu")
    details = _part(usage, "input_tokens_details")
    total_in = _count(usage.get("input_tokens"))
    cached = min(_count(details.get("cached_tokens")), total_in)
    written = min(_count(details.get("cache_write_tokens")), total_in - cached)
    return {"in": total_in - cached - written, "cached": cached, "cache_write": written,
            "out": _count(usage.get("output_tokens"))}


def text_cost(classes: dict, model: str) -> float:
    prices = TEXT_PRICES[resolve_text_model(model)]
    return sum(classes.get(k, 0) * prices[k] for k in TEXT_CLASSES) / 1e6


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


def _now() -> float:
    return time.time()


def _num(value) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0


def _remote(raw) -> dict:
    """The paired devices' part of a day: known id shapes, bounded, numbers finite."""
    out = {}
    for device_id, item in (raw.items() if isinstance(raw, dict) else ()):
        if len(out) >= REMOTE_MAX_DEVICES:
            break
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id) or not isinstance(item, dict):
            continue
        mints = [float(m) for m in item.get("mints") or [] if _num(m) > 0] if isinstance(item.get("mints"), list) \
            else []
        out[device_id] = {"usd": max(0.0, float(_num(item.get("usd")))), "mints": mints[-REMOTE_MAX_MINTS:]}
    return out


def _entry(raw) -> dict:
    """One day, every field present and numeric (the file may have been edited by hand)."""
    raw = raw if isinstance(raw, dict) else {}
    rt = raw.get("realtime") if isinstance(raw.get("realtime"), dict) else {}
    cl = raw.get("claude") if isinstance(raw.get("claude"), dict) else {}
    realtime = {k: _num(rt.get(k)) for k in (*TOKEN_CLASSES, "transcribe_in", "transcribe_out",
                                             "transcribe_seconds", "usd")}
    entry = {"realtime": realtime, "claude": {"usd": _num(cl.get("usd")), "tasks": int(_num(cl.get("tasks")))}}
    tx = raw.get("text") if isinstance(raw.get("text"), dict) else {}
    text = {k: _num(tx.get(k)) for k in (*TEXT_CLASSES, "usd")}
    text["calls"] = int(_num(tx.get("calls")))
    if text["usd"] or text["calls"]:  # only once Siri spoke that day: the other days keep their shape
        entry["text"] = text
    remote = _remote(raw.get("remote"))
    if remote:  # only once a paired device used the voice: the PC's days keep their shape
        entry["remote"] = remote
    return entry


def _load() -> dict:
    data = store.load(FILE, {})
    return data if isinstance(data, dict) else {}


def _change(fn, publish: bool = True) -> dict:
    """Apply fn to today's entry and save; returns that entry."""
    today = _today()
    with store.LOCK:
        data = _load()
        key = today.isoformat()
        entry = _entry(data.get(key))
        fn(entry)
        entry["realtime"]["usd"] = round(entry["realtime"]["usd"], 6)
        entry["claude"]["usd"] = round(entry["claude"]["usd"], 6)
        if "text" in entry:
            entry["text"]["usd"] = round(entry["text"]["usd"], 6)
        data[key] = entry
        oldest = (today - timedelta(days=KEEP_DAYS - 1)).isoformat()
        data = {k: v for k, v in data.items() if _DAY.fullmatch(str(k)) and k >= oldest}
        store.save(FILE, data)
    if publish:
        _publish()
    return entry


def _day(key: str) -> dict:
    return _entry(_load().get(key))


def add_realtime(usage: dict, model: str = "", at_least: str = "") -> float:
    """A response.done usage (or the sum of several) of one voice model; returns its cost in USD.
    at_least: never priced below this model (the page's report: see post_usage)."""
    classes = realtime_classes(usage)
    usd = realtime_cost(classes, model or config.REALTIME_MODEL)
    if at_least:
        usd = max(usd, realtime_cost(classes, at_least))

    def apply(entry):
        for k, v in classes.items():
            entry["realtime"][k] += v
        entry["realtime"]["usd"] += usd
    _change(apply)
    return usd


def add_transcription(usage: dict, model: str = "", at_least: str = "") -> float:
    """at_least: never priced below this model's rate (the page's report: see post_usage)."""
    if not isinstance(usage, dict):
        raise ValueError("objet attendu")
    if model not in TRANSCRIBE_PER_MINUTE:
        from . import realtime  # late: realtime -> tools -> tasks imports this module
        model = realtime.transcribe_model()
    parts = transcription_parts(usage, model)
    if at_least and at_least != model:
        floor = transcription_parts(usage, at_least)
        if floor["usd"] > parts["usd"]:
            parts = floor

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


def add_text(model: str, usage) -> float:
    """One call of Siri's text model (raccourci.py): its Responses usage,
    priced at TEXT_PRICES (an unknown model as the dearest); returns the USD.
    A missing or unreadable usage still counts the call, at 0 tokens."""
    try:
        classes = text_classes(usage if usage is not None else {})
    except ValueError:
        classes = dict.fromkeys(TEXT_CLASSES, 0)
    usd = text_cost(classes, model)

    def apply(entry):
        text = entry.setdefault("text", {**dict.fromkeys(TEXT_CLASSES, 0), "calls": 0, "usd": 0})
        for k, v in classes.items():
            text[k] += v
        text["calls"] += 1
        text["usd"] += usd
    _change(apply)
    return usd


# ---------------------------------------------------------------- a paired iPhone's voice

def note_remote_mint(device_id: str, now: float) -> None:
    """A voice session a paired device opened (remote.note_mint): kept in today's
    entry, so the day's mint limit and the report allowance survive a restart."""
    if not _DEVICE_ID.fullmatch(str(device_id or "")):
        return

    def apply(entry):
        remote = entry.setdefault("remote", {})
        item = remote.setdefault(device_id, {"usd": 0.0, "mints": []})
        item["mints"] = [*item["mints"], float(now)][-REMOTE_MAX_MINTS:]
        while len(remote) > REMOTE_MAX_DEVICES:  # the oldest device of the day goes
            remote.pop(next(iter(remote)))
    _change(apply, publish=False)


def remote_mints(device_id: str) -> list[float]:
    """Today's voice sessions of this device (epoch seconds)."""
    item = _day(_today().isoformat()).get("remote", {}).get(str(device_id or ""))
    return list(item["mints"]) if item else []


def _allowance(mints: list, now: float) -> float:
    return sum(min(REMOTE_SESSION_ALLOWANCE_USD, REMOTE_MAX_USD_PER_S * max(0.0, now - m)) for m in mints)


def _remote_report(caller, usage: dict, model: str) -> None:
    """A paired device's report: priced like the PC's, refused above
    REMOTE_POST_MAX_USD, and recorded only up to what the time elapsed since its
    sessions allows (the token classes scaled alike, so the ledger stays whole)."""
    from . import (  # late: realtime -> tools -> tasks imports this module
        audit,
        realtime,
    )
    device_id = caller.device_id
    if "type" in usage:
        asr = model if model in TRANSCRIBE_PER_MINUTE else realtime.transcribe_model()
        parts = transcription_parts(usage, asr)
        floor = realtime.transcribe_model()
        if floor != asr:
            low = transcription_parts(usage, floor)
            if low["usd"] > parts["usd"]:
                parts = low
        usd, classes = parts["usd"], {"transcribe_in": parts["in"], "transcribe_out": parts["out"],
                                      "transcribe_seconds": parts["seconds"]}
    else:
        classes = realtime_classes(usage)
        usd = max(realtime_cost(classes, model or config.REALTIME_MODEL),
                  realtime_cost(classes, config.REALTIME_MODEL))
    if usd > REMOTE_POST_MAX_USD:
        raise ValueError("relevé trop élevé")
    clamped = {"done": False}

    def apply(entry):
        # No voice session opened today: no allowance, nothing recorded (and no entry made).
        item = entry.get("remote", {}).get(device_id) or {"usd": 0.0, "mints": []}
        room = max(0.0, _allowance(item["mints"], _now()) - item["usd"])
        ratio = 1.0
        if usd > room:
            ratio = room / usd if usd > 0 else 0.0
            clamped["done"] = True
        for k, v in classes.items():
            entry["realtime"][k] += v * ratio
        recorded = usd * ratio
        entry["realtime"]["usd"] += recorded
        item["usd"] = round(item["usd"] + recorded, 6)
    _change(apply)
    if clamped["done"]:
        audit.event(caller, "usage", reason="clamped")


def realtime_spent_today() -> float:
    return _day(_today().isoformat())["realtime"]["usd"]


def claude_spent_today() -> float:
    return _day(_today().isoformat())["claude"]["usd"]


def text_spent_today() -> float:
    """Siri's text model today."""
    return _day(_today().isoformat()).get("text", {}).get("usd", 0.0)


def spent_today() -> float:
    return realtime_spent_today() + claude_spent_today() + text_spent_today()


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


def _text_usd(entry: dict) -> float:
    return entry.get("text", {}).get("usd", 0.0)


def _figures(key: str, entry: dict) -> dict:
    """One day as the page shows it. Siri's text part, once Siri spoke that day,
    is apart (text_usd) and inside the total."""
    voice, claude, text = entry["realtime"]["usd"], entry["claude"]["usd"], _text_usd(entry)
    out = {"date": key, "realtime_usd": round(voice, 6), "claude_usd": round(claude, 6),
           "claude_tasks": entry["claude"]["tasks"], "total_usd": round(voice + claude + text, 6)}
    if "text" in entry:
        out["text_usd"] = round(text, 6)
    return out


def _capped(entry: dict, cap: float) -> bool:
    return cap > 0 and entry["realtime"]["usd"] + entry["claude"]["usd"] + _text_usd(entry) >= cap


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
def post_usage(body: UsageIn, request: Request):
    # The page names its session's model, but a cheaper name than Réglages
    # set never makes the cap look further away: the dearer of the two counts
    # (a session opened before a switch to a dearer model is then over-counted,
    # the safe side).
    from . import (  # late: realtime -> tools -> tasks imports this module
        realtime,
        remote,
    )
    model = body.model[:80]
    caller = remote.caller_of(request)
    try:
        if caller.remote:  # a paired iPhone: bounded (4.8)
            _remote_report(caller, body.usage, model)
        elif "type" in body.usage:  # a transcription: {type: 'tokens'|'duration', ...}
            add_transcription(body.usage, model, at_least=realtime.transcribe_model())
        else:
            add_realtime(body.usage, model, at_least=config.REALTIME_MODEL)
    except ValueError:
        raise HTTPException(400, "Relevé de consommation invalide.") from None
    return summary(1)


@router.get("/api/usage")
def get_usage(days: int = Query(30, ge=1, le=KEEP_DAYS)):
    return summary(days)
