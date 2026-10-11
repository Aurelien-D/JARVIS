"""Paired devices (iPhones) and their Siri keys, in data/devices.json.

- A device holds a 256-bit secret, sent back by its browser in the
  __Host-jarvis cookie. Only its sha256 is stored, compared in constant time:
  a copy of this file opens nothing.
- A revoked record is kept 30 days (so the PC can still name it), then pruned.
  Its id stays forever in revoked_ids (the last 1000), so a stale cookie or Siri
  key is always recognised as revoked, never as unknown.
- Everything a device sends (its name, the whois data) goes through clean_label
  before it is stored or shown: no markup can ever reach the PC page.

Writes are atomic through store and serialised by store.LOCK, so two requests
never lose each other's change.
"""
import hashlib
import re
import secrets
import time

from . import store

DEVICES_FILE = "devices.json"
MAX_DEVICES = 5
MAX_SIRI_KEYS = 2
KEEP_REVOKED_S = 30 * 86400  # a revoked record is pruned after this
MAX_TOMBSTONES = 1000
TOUCH_EVERY_S = 60           # last_seen is written at most once a minute
MAX_IPS = 8                  # the node's addresses (IPv4 and IPv6 are two)

DEVICE_ID = re.compile(r"^d_[0-9a-f]{16}$")
KEY_ID = re.compile(r"^k_[0-9a-f]{16}$")
_LABEL = re.compile(r"[^\w '\-.]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")


def _now() -> float:
    return time.time()


def clean_label(text, default: str = "", limit: int = 24) -> str:
    """Letters, digits, spaces and ' - . _ only, spaces collapsed, cut to limit;
    empty -> default. Used for every string a device or whois supplies."""
    text = _LABEL.sub("", str(text or ""))
    text = _SPACES.sub(" ", text).strip()[:limit].strip()
    return text or default


def _hash(secret: str) -> str:
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


def _matches(stored: str, secret: str) -> bool:
    return bool(stored) and secrets.compare_digest(str(stored), _hash(secret))

# ---------------------------------------------------------------- the file


def _clean_key(raw) -> dict | None:
    if not isinstance(raw, dict) or not KEY_ID.fullmatch(str(raw.get("id", ""))):
        return None
    return {"id": raw["id"], "secret_sha256": str(raw.get("secret_sha256") or ""),
            "created": _num(raw.get("created")), "last_used": _opt(raw.get("last_used")),
            "revoked": bool(raw.get("revoked")), "revoked_at": _opt(raw.get("revoked_at"))}


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _opt(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _clean_device(raw) -> dict | None:
    """One record as the file holds it, every field present and typed (a hand
    edit must not break the gate)."""
    if not isinstance(raw, dict) or not DEVICE_ID.fullmatch(str(raw.get("id", ""))):
        return None
    ips = [ip for ip in _list(raw.get("ips")) if isinstance(ip, str) and ip][:MAX_IPS]
    ip = raw.get("ip") if isinstance(raw.get("ip"), str) else ""
    if ip and ip not in ips:
        ips.insert(0, ip)
    return {"id": raw["id"], "name": clean_label(raw.get("name"), "iPhone"), "kind": "app",
            "secret_sha256": str(raw.get("secret_sha256") or ""), "login": str(raw.get("login") or "").lower(),
            "ip": ip, "ips": ips, "node_id": str(raw.get("node_id") or ""),
            "os": clean_label(raw.get("os"), "", 40), "host_name": clean_label(raw.get("host_name"), "", 40),
            "ua": str(raw.get("ua") or "")[:160], "paired_at": _num(raw.get("paired_at")),
            "paired_seq": int(_num(raw.get("paired_seq"))), "last_seen": _opt(raw.get("last_seen")),
            "revoked": bool(raw.get("revoked")), "revoked_at": _opt(raw.get("revoked_at")),
            "siri_keys": [k for k in (_clean_key(x) for x in _list(raw.get("siri_keys"))) if k]}


def _load() -> dict:
    data = store.load(DEVICES_FILE, {})
    data = data if isinstance(data, dict) else {}
    tombs = [i for i in _list(data.get("revoked_ids")) if isinstance(i, str) and (DEVICE_ID.fullmatch(i) or KEY_ID.fullmatch(i))]
    devices = [d for d in (_clean_device(x) for x in _list(data.get("devices"))) if d]
    # Pruned once 30 days revoked: its ids live on in the tombstones.
    cutoff = _now() - KEEP_REVOKED_S
    kept = []
    for d in devices:
        if d["revoked"] and (d["revoked_at"] or 0) < cutoff:
            tombs += [d["id"], *(k["id"] for k in d["siri_keys"])]
        else:
            kept.append(d)
    return {"version": 1, "revoked_ids": _bounded(tombs), "devices": kept}


def _bounded(ids: list) -> list:
    return list(dict.fromkeys(ids))[-MAX_TOMBSTONES:]


def _save(data: dict) -> None:
    data["revoked_ids"] = _bounded(data["revoked_ids"])
    store.save(DEVICES_FILE, data)


def _find(data: dict, device_id: str) -> dict | None:
    return next((d for d in data["devices"] if d["id"] == device_id), None)


def _find_key(data: dict, key_id: str):
    for d in data["devices"]:
        for k in d["siri_keys"]:
            if k["id"] == key_id:
                return d, k
    return None, None


def _copy(device: dict | None) -> dict | None:
    if device is None:
        return None
    return {**device, "ips": list(device["ips"]), "siri_keys": [dict(k) for k in device["siri_keys"]]}

# ---------------------------------------------------------------- devices


def add(name, *, ip, login, os="", host_name="", node_id="", ips=(), ua="", paired_seq=0) -> tuple[dict, str]:
    """A new paired device; returns (device, plain secret). The secret is
    returned once, here, and never stored in clear. ValueError past MAX_DEVICES."""
    secret = secrets.token_urlsafe(32)
    addresses = [str(a) for a in (ip, *ips) if a]
    with store.LOCK:
        data = _load()
        if len([d for d in data["devices"] if not d["revoked"]]) >= MAX_DEVICES:
            raise ValueError(f"{MAX_DEVICES} appareils au plus.")
        taken = {d["id"] for d in data["devices"]} | set(data["revoked_ids"])
        device_id = "d_" + secrets.token_hex(8)
        while device_id in taken:
            device_id = "d_" + secrets.token_hex(8)
        device = {"id": device_id, "name": clean_label(name, "iPhone"), "kind": "app",
                  "secret_sha256": _hash(secret), "login": str(login or "").strip().lower(),
                  "ip": str(ip or ""), "ips": list(dict.fromkeys(addresses))[:MAX_IPS],
                  "node_id": str(node_id or ""), "os": clean_label(os, "", 40),
                  "host_name": clean_label(host_name, "", 40), "ua": str(ua or "")[:160],
                  "paired_at": _now(), "paired_seq": int(paired_seq or 0), "last_seen": None,
                  "revoked": False, "revoked_at": None, "siri_keys": []}
        data["devices"].append(device)
        _save(data)
    return _copy(device), secret


def get(device_id) -> dict | None:
    """The record (active or revoked, not pruned), with its hashes: server side only."""
    return _copy(_find(_load(), str(device_id or "")))


def active() -> list[dict]:
    return [_copy(d) for d in _load()["devices"] if not d["revoked"]]


def verify(device_id, secret) -> dict | None:
    """The active device whose secret this is, else None."""
    device = _find(_load(), str(device_id or ""))
    if device is None or device["revoked"] or not _matches(device["secret_sha256"], secret):
        return None
    return _copy(device)


def is_known(device_id) -> bool:
    return _find(_load(), str(device_id or "")) is not None


def is_revoked(device_id) -> bool:
    data = _load()
    device = _find(data, str(device_id or ""))
    return (device is not None and device["revoked"]) or str(device_id or "") in data["revoked_ids"]


def _update(device_id: str, fn) -> dict | None:
    with store.LOCK:
        data = _load()
        device = _find(data, str(device_id or ""))
        if device is None:
            return None
        if fn(device) is not False:
            _save(data)
        return _copy(device)


def touch(device_id, ip) -> None:
    """Seen just now (written at most once a minute)."""
    now = _now()

    def apply(device):
        if device["revoked"] or (device["last_seen"] and now - device["last_seen"] < TOUCH_EVERY_S):
            return False
        device["last_seen"] = now
        if ip and ip in device["ips"]:
            device["ip"] = str(ip)
    _update(device_id, apply)


def add_ip(device_id, ip) -> None:
    """The same tailnet node seen on its other address (whois said so)."""
    def apply(device):
        if not ip or ip in device["ips"]:
            return False
        device["ips"] = [*device["ips"], str(ip)][-MAX_IPS:]
    _update(device_id, apply)


def rename(device_id, name) -> dict:
    """The public record, renamed; KeyError for an unknown or revoked device."""
    def apply(device):
        if device["revoked"]:
            return False
        device["name"] = clean_label(name, "iPhone")
    device = _update(device_id, apply)
    if device is None or device["revoked"]:
        raise KeyError(device_id)
    return public(device)


def revoke(device_id) -> bool:
    """Revoke a device and its Siri keys; every id is tombstoned. False when it
    was unknown or already revoked."""
    with store.LOCK:
        data = _load()
        device = _find(data, str(device_id or ""))
        if device is None or device["revoked"]:
            return False
        now = _now()
        device.update(revoked=True, revoked_at=now)
        for key in device["siri_keys"]:
            if not key["revoked"]:
                key.update(revoked=True, revoked_at=now)
        data["revoked_ids"] += [device["id"], *(k["id"] for k in device["siri_keys"])]
        _save(data)
    return True


def public(device) -> dict:
    """What the PC page sees: never a hash, never the user agent."""
    if not device:
        return {}
    keys = [{"id": k["id"], "created": k["created"], "last_used": k["last_used"], "revoked": k["revoked"],
             "revoked_at": k["revoked_at"]} for k in device.get("siri_keys") or []]
    return {"id": device["id"], "name": device["name"], "kind": "app", "login": device["login"],
            "ip": device["ip"], "ips": list(device["ips"]), "os": device["os"], "host_name": device["host_name"],
            "paired_at": device["paired_at"], "last_seen": device["last_seen"], "revoked": device["revoked"],
            "revoked_at": device["revoked_at"], "siri_keys": keys}

# ---------------------------------------------------------------- Siri keys


def add_siri_key(device_id) -> tuple[str, str]:
    """(key_id, plain secret) for an active device; ValueError past MAX_SIRI_KEYS
    or for a device that is not active."""
    secret = secrets.token_urlsafe(32)
    with store.LOCK:
        data = _load()
        device = _find(data, str(device_id or ""))
        if device is None or device["revoked"]:
            raise ValueError("Appareil inconnu ou retiré.")
        if len([k for k in device["siri_keys"] if not k["revoked"]]) >= MAX_SIRI_KEYS:
            raise ValueError(f"{MAX_SIRI_KEYS} clés Siri au plus par appareil.")
        taken = {k["id"] for d in data["devices"] for k in d["siri_keys"]} | set(data["revoked_ids"])
        key_id = "k_" + secrets.token_hex(8)
        while key_id in taken:
            key_id = "k_" + secrets.token_hex(8)
        device["siri_keys"].append({"id": key_id, "secret_sha256": _hash(secret), "created": _now(),
                                    "last_used": None, "revoked": False, "revoked_at": None})
        _save(data)
    return key_id, secret


def verify_siri_key(key_id, secret) -> tuple[dict, dict] | None:
    """(device, key), both active and the secret right; else None."""
    device, key = _find_key(_load(), str(key_id or ""))
    if key is None or key["revoked"] or device["revoked"] or not _matches(key["secret_sha256"], secret):
        return None
    return _copy(device), dict(key)


def siri_key_known(key_id) -> bool:
    return _find_key(_load(), str(key_id or ""))[1] is not None


def siri_key_revoked(key_id) -> bool:
    data = _load()
    device, key = _find_key(data, str(key_id or ""))
    return (key is not None and (key["revoked"] or device["revoked"])) or str(key_id or "") in data["revoked_ids"]


def siri_key_device(key_id) -> dict | None:
    """The device a key belongs to (active or not), or None."""
    device, _ = _find_key(_load(), str(key_id or ""))
    return _copy(device)


def revoke_siri_key(key_id) -> bool:
    with store.LOCK:
        data = _load()
        _, key = _find_key(data, str(key_id or ""))
        if key is None or key["revoked"]:
            return False
        key.update(revoked=True, revoked_at=_now())
        data["revoked_ids"].append(key["id"])
        _save(data)
    return True


def touch_siri_key(key_id) -> None:
    now = _now()
    with store.LOCK:
        data = _load()
        _, key = _find_key(data, str(key_id or ""))
        if key is None or key["revoked"] or (key["last_used"] and now - key["last_used"] < TOUCH_EVERY_S):
            return
        key["last_used"] = now
        _save(data)
