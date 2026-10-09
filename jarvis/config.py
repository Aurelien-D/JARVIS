"""Configuration: .env loading and the settings every module reads.

Modules read these as `config.NAME` at call time (not `from config import`),
so tests can monkeypatch them.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def load_env(path: Path = ROOT / ".env"):
    """Read KEY=VALUE lines into os.environ (real environment variables win).

    utf-8-sig: Notepad can save the file with a BOM, which would otherwise
    glue itself to the first key.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value not in ("0", "false", "non", "off", "no")


load_env()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime-2.1")
VOICE = os.environ.get("JARVIS_VOICE", "ballad")
LANGUAGE = os.environ.get("JARVIS_LANGUAGE", "français")
PORT = _int("JARVIS_PORT", 8788)

# Where Claude Code sessions run (their working directory).
WORKDIR = os.path.expanduser(os.environ.get("JARVIS_WORKDIR", "~/JARVIS-travail"))
TASK_TIMEOUT = _int("JARVIS_TASK_TIMEOUT", 600)

# Headless sessions have nobody to answer permission prompts: what would ask is
# denied. auto = a classifier lets safe actions through and refuses risky ones;
# bypassPermissions = no check at all (not recommended).
PERMISSION_MODE = os.environ.get("JARVIS_PERMISSION_MODE", "auto")

# Claude model per task complexity (Claude Code aliases; empty = account default).
MODELS = {
    "simple": os.environ.get("JARVIS_MODEL_SIMPLE", "haiku"),
    "normale": os.environ.get("JARVIS_MODEL_NORMAL", "sonnet"),
    "complexe": os.environ.get("JARVIS_MODEL_COMPLEX", "opus"),
}
# Extra MCP servers (mail, agenda, home automation...) for Claude Code tasks.
MCP_CONFIG = os.environ.get("JARVIS_MCP_CONFIG", "")

# Memory, reminders, task history, logs, the app window's browser profile.
DATA_DIR = Path(os.path.expanduser(os.environ.get("JARVIS_DATA_DIR", str(ROOT / "data"))))

WAKE_WORD = os.environ.get("JARVIS_WAKE_WORD", "1").strip().lower() not in ("0", "false", "non", "off")
IDLE_MINUTES = _int("JARVIS_IDLE_MINUTES", 3)
BRIEFING_TIME = os.environ.get("JARVIS_BRIEFING_TIME", "08:00").strip()
CITY = os.environ.get("JARVIS_CITY", "").strip()

# Browser speech recognition language, used for the wake word.
_SPEECH_LANGS = {"français": "fr-FR", "francais": "fr-FR", "french": "fr-FR",
                 "english": "en-US", "anglais": "en-US", "español": "es-ES",
                 "espagnol": "es-ES", "deutsch": "de-DE", "allemand": "de-DE",
                 "italiano": "it-IT", "italien": "it-IT"}
SPEECH_LANG = os.environ.get("JARVIS_SPEECH_LANG") or _SPEECH_LANGS.get(LANGUAGE.lower(), "fr-FR")

# ---------------------------------------------------------------- voice session (OpenAI Realtime)
# Transcription of what monsieur says (shown on screen and kept for reconnections),
# with a fallback model if OpenAI refuses the first one.
TRANSCRIBE_MODEL = os.environ.get("JARVIS_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
TRANSCRIBE_FALLBACK = os.environ.get("JARVIS_TRANSCRIBE_FALLBACK", "whisper-1")
REASONING_EFFORT = os.environ.get("JARVIS_REASONING", "low")  # gpt-realtime-2.x models only
NOISE_REDUCTION = os.environ.get("JARVIS_NOISE_REDUCTION", "far_field")  # near_field for a headset
EAGERNESS = os.environ.get("JARVIS_EAGERNESS", "auto")  # low = JARVIS cuts in less often
VOICE_SPEED = _float("JARVIS_VOICE_SPEED", 1.0)
SECRET_TTL = _int("JARVIS_SECRET_TTL", 120)  # seconds an ephemeral key stays usable
RETENTION_RATIO = _float("JARVIS_RETENTION_RATIO", 0.8)  # share of a long conversation kept

# ---------------------------------------------------------------- Claude Code tasks: safety and cost
TASK_BUDGET_USD = _float("JARVIS_TASK_BUDGET_USD", 2.0)
DAILY_BUDGET_USD = _float("JARVIS_DAILY_BUDGET_USD", 0)  # 0 = no daily cap
MAX_CONCURRENT_TASKS = _int("JARVIS_MAX_CONCURRENT_TASKS", 3)
CONFIRM_COMPLET = _bool("JARVIS_CONFIRM_COMPLET", True)  # full-access tasks wait for a "oui"
PENDING_TTL = _int("JARVIS_PENDING_TTL", 90)  # seconds a confirmation request stays open
OPEN_URL_ALLOW = os.environ.get("JARVIS_OPEN_URL_ALLOW", "")  # domains opened without asking
SAFE_MODE = os.environ.get("JARVIS_SAFE_MODE", "auto")
RESTRICTED = os.environ.get("JARVIS_RESTRICTED", "auto")

# ---------------------------------------------------------------- A.R.E.S (local MCP server)
ARES = os.environ.get("JARVIS_ARES", "auto")  # auto | on | off
ARES_URL = os.environ.get("JARVIS_ARES_URL", "http://127.0.0.1:6178/mcp")
ARES_MCP_NAME = os.environ.get("JARVIS_ARES_MCP_NAME", "ares")
ARES_TOKEN = os.environ.get("JARVIS_ARES_TOKEN", "")  # sent as a bearer token if A.R.E.S ever asks for one

# ---------------------------------------------------------------- proactivity
QUIET_HOURS = os.environ.get("JARVIS_QUIET_HOURS", "22:30-07:30")
BRIEFING_DAYS = os.environ.get("JARVIS_BRIEFING_DAYS", "lun-ven")
BRIEFING_NEWS = _bool("JARVIS_BRIEFING_NEWS", False)
NEWS_FEEDS = os.environ.get("JARVIS_NEWS_FEEDS", "https://www.lemonde.fr/rss/une.xml")
REOPEN_ON_REMINDER = _bool("JARVIS_REOPEN_ON_REMINDER", True)
JOURNAL_DAYS = _int("JARVIS_JOURNAL_DAYS", 30)

# ---------------------------------------------------------------- Windows integration
HOTKEY = os.environ.get("JARVIS_HOTKEY", "ctrl+alt+shift+j")
BROWSER = os.environ.get("JARVIS_BROWSER", "auto")  # auto | chrome | edge
TRAY = _bool("JARVIS_TRAY", True)
