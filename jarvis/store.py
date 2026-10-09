"""Small JSON files in the data folder (memory, reminders, task history)."""
import json
import threading

from . import config

LOCK = threading.RLock()


def load(name: str, default):
    try:
        return json.loads((config.DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(name: str, data):
    """Write atomically: a crash mid-write never leaves a half-written file."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / name
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
