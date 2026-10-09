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


load_env()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime")
VOICE = os.environ.get("JARVIS_VOICE", "ballad")
LANGUAGE = os.environ.get("JARVIS_LANGUAGE", "français")
PORT = _int("JARVIS_PORT", 8788)

# Where Claude Code sessions run (their working directory).
WORKDIR = os.path.expanduser(os.environ.get("JARVIS_WORKDIR", "~"))
TASK_TIMEOUT = _int("JARVIS_TASK_TIMEOUT", 600)

# Headless sessions have nobody to answer permission prompts: a task that asks
# would just hang until the timeout. Run them in a non-interactive mode instead.
# bypassPermissions = no prompt at all; acceptEdits = files yes, commands still ask.
PERMISSION_MODE = os.environ.get("JARVIS_PERMISSION_MODE", "bypassPermissions")

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
