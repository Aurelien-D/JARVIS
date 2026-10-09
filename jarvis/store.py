"""Small JSON files in the data folder (memory, reminders, task history, inbox).

Writes are atomic (tmp file + replace), and every save first keeps the
previous good version as name.bak. A file that no longer reads back (a power
cut on a disk without write-through, a hand edit, a wrong shape) is never
silently replaced by an empty one: it is set aside as name.corrompu-<date>,
the .bak copy is restored, and the page is told.
"""
import json
import logging
import os
import threading
import time

from . import config

_MISSING = object()
_held: list = []  # warnings raised while LOCK is held (see _StoreLock)


class _StoreLock:
    """Re-entrant lock around the data files. Warnings about a broken file are
    held back until the outermost holder lets go: publishing one records it in
    the inbox, itself a file of this store, which must not be rewritten in the
    middle of another operation on it."""

    def __init__(self):
        self._lock = threading.RLock()
        self._depth = 0  # changed only by the thread holding _lock

    def acquire(self, blocking=True, timeout=-1):
        got = self._lock.acquire(blocking, timeout)
        if got:
            self._depth += 1
        return got

    def release(self):
        self._depth -= 1
        held = []
        if self._depth == 0:
            held, _held[:] = list(_held), []
        self._lock.release()
        for warning in held:
            _publish_warning(*warning)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


LOCK = _StoreLock()


def _parse(raw: bytes, default):
    """The file's data, or _MISSING when it doesn't read back as JSON of the
    expected kind (a list file holding a dict breaks callers just the same)."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError is a ValueError too
        return _MISSING
    if isinstance(default, (list, dict)) and not isinstance(data, type(default)):
        return _MISSING
    return data


def load(name: str, default):
    path = config.DATA_DIR / name
    with LOCK:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return default  # never written, or deleted on purpose: no restore
        except OSError:
            logging.exception("JARVIS: lecture impossible de %s", path)
            return default
        data = _parse(raw, default)
        if data is not _MISSING:
            return data
        return _recover(name, default)


def _recover(name: str, default):
    """Set the broken file aside, then restore the last good copy if there is one."""
    path = config.DATA_DIR / name
    kept = path.with_name(f"{name}.corrompu-{time.strftime('%Y%m%d-%H%M%S')}")
    n = 1
    while kept.exists():  # two breakages within the same second
        n += 1
        kept = kept.with_name(f"{name}.corrompu-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
    try:
        path.replace(kept)
    except OSError:
        logging.exception("JARVIS: impossible de mettre %s de côté", path)
        return default
    restored = _MISSING
    bak = path.with_name(name + ".bak")
    try:
        raw = bak.read_bytes()
        restored = _parse(raw, default)
        if restored is not _MISSING:
            _write(path, raw)  # back in place, so the next load and save start from it
    except FileNotFoundError:
        pass
    except OSError:
        logging.exception("JARVIS: restauration impossible de %s", bak)
        restored = _MISSING
    logging.error("JARVIS: %s était illisible ; copie gardée dans %s%s", name, kept.name,
                  " ; sauvegarde restaurée" if restored is not _MISSING else "")
    _held.append((name, kept.name, restored is not _MISSING))  # published once LOCK is free
    return default if restored is _MISSING else restored


def _publish_warning(name: str, kept: str, restored: bool):
    from . import events  # late: events records warnings in the inbox, which uses this module
    text = (f"Le fichier {name} était abîmé : une copie a été gardée et la sauvegarde restaurée."
            if restored else
            f"Le fichier {name} était abîmé : une copie a été gardée ({kept}), il repart de zéro.")
    try:
        events.publish("warning", {"kind": "corrupt", "file": name, "kept": kept, "text": text})
    except Exception:  # noqa: BLE001 - telling the page must never break a load
        logging.exception("JARVIS: avertissement non publié")


def _write(path, raw: bytes):
    """Atomic: a crash mid-write never leaves a half-written file behind."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())  # on disk before it replaces the good file
    tmp.replace(path)


def save(name: str, data):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / name
    raw = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    with LOCK:
        try:
            current = path.read_bytes()
        except OSError:
            current = None
        # Only a version that still reads back may become the backup: a broken
        # file must not overwrite the last good copy.
        if current is not None and _parse(current, None) is not _MISSING:
            try:
                _write(path.with_name(name + ".bak"), current)
            except OSError:
                logging.exception("JARVIS: sauvegarde .bak impossible pour %s", name)
        _write(path, raw)
