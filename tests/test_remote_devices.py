"""The paired devices' store (spec 3.5): hashed secrets, tombstones kept
forever, revoked records pruned after 30 days, clean labels, Siri key records."""
import hashlib
import json

import pytest

from jarvis import config, devices, store

IP, IP6, LOGIN = "100.101.102.103", "fd7a:115c:a1e0::1234", "monsieur@example.com"


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_760_000_000.0}
    monkeypatch.setattr(devices, "_now", lambda: now["t"])
    return now


def raw() -> dict:
    return json.loads((config.DATA_DIR / devices.DEVICES_FILE).read_text(encoding="utf-8"))


def new(name="iPhone de test", **kw):
    return devices.add(name, ip=kw.pop("ip", IP), login=kw.pop("login", LOGIN), **kw)

# ---------------------------------------------------------------- labels


@pytest.mark.parametrize("text, default, limit, expected", [
    ("iPhone de test", "", 24, "iPhone de test"),
    ("<img src=x onerror=alert(1)>", "", 24, "img srcx onerroralert1"),
    ("[x](https://ev.il) **gras**", "", 40, "xhttpsev.il gras"),
    ("  Écran   d'entrée_2-b.c  ", "", 24, "Écran d'entrée_2-b.c"),
    ("a" * 50, "", 24, "a" * 24),
    ("<>&\"`", "iPhone", 24, "iPhone"),
    (None, "iPhone", 24, "iPhone"),
    ("ligne\nsuivante\ttab", "", 40, "lignesuivantetab"),  # control characters go, they are no space
])
def test_clean_label(text, default, limit, expected):
    assert devices.clean_label(text, default, limit) == expected

# ---------------------------------------------------------------- devices


def test_add_stores_only_the_hash_and_returns_the_secret_once(clock):
    device, secret = new(os="iOS <b>", host_name="iphone-de-test", ua="Mozilla/5.0 " + "x" * 300, paired_seq=42,
                         ips=(IP6,))
    assert device["id"].startswith("d_") and len(device["id"]) == 18
    assert len(secret) >= 40
    text = (config.DATA_DIR / devices.DEVICES_FILE).read_text(encoding="utf-8")
    assert secret not in text
    assert hashlib.sha256(secret.encode()).hexdigest() in text
    stored = raw()["devices"][0]
    assert stored["os"] == "iOS b" and stored["ips"] == [IP, IP6] and stored["paired_seq"] == 42
    assert len(stored["ua"]) == 160 and stored["revoked"] is False and stored["last_seen"] is None
    assert stored["paired_at"] == clock["t"]
    public = devices.public(device)
    assert "secret_sha256" not in json.dumps(public) and "ua" not in public
    assert public["name"] == "iPhone de test" and public["ips"] == [IP, IP6]


def test_verify_needs_the_right_secret_of_an_active_device():
    device, secret = new()
    assert devices.verify(device["id"], secret)["id"] == device["id"]
    assert devices.verify(device["id"], secret + "x") is None
    assert devices.verify(device["id"], "") is None
    assert devices.verify("d_0000000000000000", secret) is None
    devices.revoke(device["id"])
    assert devices.verify(device["id"], secret) is None


def test_names_are_cleaned_and_default_to_iphone():
    device, _ = new(name="<script>alert(1)</script>")
    assert device["name"] == "scriptalert1script"
    assert new(name="  ")[0]["name"] == "iPhone"
    renamed = devices.rename(device["id"], "[Mon](javascript:x) iPhone")
    assert renamed["name"] == "Monjavascriptx iPhone" and "secret_sha256" not in renamed
    with pytest.raises(KeyError):
        devices.rename("d_0000000000000000", "x")


def test_at_most_five_active_devices():
    made = [new(name=f"iPhone {n}")[0] for n in range(devices.MAX_DEVICES)]
    with pytest.raises(ValueError):
        new(name="Sixième")
    devices.revoke(made[0]["id"])
    assert new(name="Remplaçant")[0]["name"] == "Remplaçant"
    assert len(devices.active()) == devices.MAX_DEVICES


def test_touch_writes_at_most_once_a_minute_and_add_ip_learns_the_other_address(clock):
    device, _ = new()
    devices.touch(device["id"], IP)
    assert devices.get(device["id"])["last_seen"] == clock["t"]
    first = clock["t"]
    clock["t"] += 30
    devices.touch(device["id"], IP)
    assert devices.get(device["id"])["last_seen"] == first
    clock["t"] += 31
    devices.touch(device["id"], IP)
    assert devices.get(device["id"])["last_seen"] == clock["t"]
    devices.add_ip(device["id"], IP6)
    devices.add_ip(device["id"], IP6)
    assert devices.get(device["id"])["ips"] == [IP, IP6]


def test_revoke_revokes_the_keys_and_tombstones_every_id(clock):
    device, _ = new()
    key_id, _ = devices.add_siri_key(device["id"])
    assert devices.revoke(device["id"]) is True
    assert devices.revoke(device["id"]) is False  # already
    assert devices.revoke("d_0000000000000000") is False
    assert devices.is_revoked(device["id"]) and devices.is_known(device["id"])
    assert devices.siri_key_revoked(key_id)
    assert set(raw()["revoked_ids"]) == {device["id"], key_id}
    assert devices.active() == []


def test_revoked_records_are_pruned_after_30_days_but_stay_tombstoned(clock):
    device, _ = new()
    key_id, _ = devices.add_siri_key(device["id"])
    kept, _ = new(name="Gardé")
    devices.revoke(device["id"])
    clock["t"] += 29 * 86400
    assert devices.is_known(device["id"])
    clock["t"] += 2 * 86400
    assert devices.get(device["id"]) is None and not devices.is_known(device["id"])
    assert devices.is_revoked(device["id"]) and devices.siri_key_revoked(key_id)
    assert not devices.siri_key_known(key_id)
    new(name="Nouveau")  # the next write prunes the file itself
    assert [d["id"] for d in raw()["devices"]] == [kept["id"], devices.active()[1]["id"]]
    assert device["id"] in raw()["revoked_ids"] and key_id in raw()["revoked_ids"]


def test_tombstones_keep_the_last_thousand():
    ids = [f"d_{n:016x}" for n in range(1200)]
    store.save(devices.DEVICES_FILE, {"version": 1, "revoked_ids": ids, "devices": []})
    new()
    tombs = raw()["revoked_ids"]
    assert len(tombs) == 1000 and tombs[-1] == ids[-1] and ids[0] not in tombs
    assert devices.is_revoked(ids[-1]) and not devices.is_revoked(ids[0])


def test_a_hand_edited_file_never_breaks_the_gate():
    store.save(devices.DEVICES_FILE, {"version": 1, "revoked_ids": ["pas-un-id", 3, "k_0123456789abcdef"],
                                      "devices": [{"id": "../../evil"}, "x", {"id": "d_0123456789abcdef",
                                                                              "name": "<b>Vieux</b>",
                                                                              "ips": "100.1.1.1", "siri_keys": 4}]})
    (device,) = devices.active()
    assert device["name"] == "bVieuxb" and device["ips"] == [] and device["siri_keys"] == []
    assert devices.verify("d_0123456789abcdef", "x" * 43) is None  # no hash: never verifies
    assert devices.is_revoked("k_0123456789abcdef") and not devices.is_revoked("pas-un-id")
    (config.DATA_DIR / devices.DEVICES_FILE).write_text("{pas du json", encoding="utf-8")
    assert isinstance(devices.active(), list)

# ---------------------------------------------------------------- Siri keys


def test_siri_keys_are_hashed_capped_and_verified(clock):
    device, _ = new()
    key_id, secret = devices.add_siri_key(device["id"])
    assert key_id.startswith("k_") and len(key_id) == 18
    assert secret not in (config.DATA_DIR / devices.DEVICES_FILE).read_text(encoding="utf-8")
    found = devices.verify_siri_key(key_id, secret)
    assert found and found[0]["id"] == device["id"] and found[1]["id"] == key_id
    assert devices.verify_siri_key(key_id, "faux" * 11) is None
    assert devices.siri_key_known(key_id) and not devices.siri_key_revoked(key_id)
    second, _ = devices.add_siri_key(device["id"])
    with pytest.raises(ValueError):
        devices.add_siri_key(device["id"])
    assert devices.revoke_siri_key(second) is True and devices.revoke_siri_key(second) is False
    assert devices.siri_key_revoked(second) and second in raw()["revoked_ids"]
    devices.add_siri_key(device["id"])  # a revoked key frees its place
    public = devices.public(devices.get(device["id"]))
    assert len(public["siri_keys"]) == 3 and "secret_sha256" not in json.dumps(public)


def test_siri_keys_need_an_active_device_and_touch_once_a_minute(clock):
    device, _ = new()
    key_id, secret = devices.add_siri_key(device["id"])
    devices.touch_siri_key(key_id)
    assert devices.get(device["id"])["siri_keys"][0]["last_used"] == clock["t"]
    clock["t"] += 10
    devices.touch_siri_key(key_id)
    assert devices.get(device["id"])["siri_keys"][0]["last_used"] == clock["t"] - 10
    assert devices.siri_key_device(key_id)["id"] == device["id"]
    devices.revoke(device["id"])
    assert devices.verify_siri_key(key_id, secret) is None
    with pytest.raises(ValueError):
        devices.add_siri_key(device["id"])
    with pytest.raises(ValueError):
        devices.add_siri_key("d_0000000000000000")
