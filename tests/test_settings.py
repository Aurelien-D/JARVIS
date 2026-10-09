"""Réglages (WP12): the override layer on top of config, its routes, the OpenAI
key written into .env without ever going back to the browser, and the
sensitive settings that only the page can change, with a confirmation."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import server
from jarvis import config, desktop, events, security, settings, store, tools

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}
KEY = "sk-proj-SETTINGS0123456789abcdefWXYZ"


@pytest.fixture(autouse=True)
def restore_config(tmp_path, monkeypatch):
    """Every setting and the key come back after each test; .env is a copy in tmp."""
    saved = {s.attr: getattr(config, s.attr) for s in settings.SCHEMA if not s.attr.startswith("MODELS.")}
    models = dict(config.MODELS)
    boot = dict(settings._BOOT)
    monkeypatch.setattr(settings, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-old")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    yield
    for attr, value in saved.items():
        setattr(config, attr, value)
    config.MODELS.clear()
    config.MODELS.update(models)
    settings._BOOT.clear()
    settings._BOOT.update(boot)


@pytest.fixture
def client():
    return TestClient(server.app, base_url=BASE, headers=AUTH)


def saved_file() -> dict:
    return store.load(settings.SETTINGS_FILE, {})

# ---------------------------------------------------------------- acceptance


def test_put_voice_changes_config_and_persists(client, published):
    r = client.put("/api/settings", json={"voice": "cedar"})
    assert r.status_code == 200, r.text
    assert config.VOICE == "cedar"
    assert saved_file() == {"voice": "cedar"}
    body = r.json()
    assert body["applied"] == {"voice": "session"}  # 'appliqué à la prochaine conversation'
    assert body["values"]["voice"] == "cedar"
    assert published[-1] == {"type": "config", "keys": ["voice"], "restart": []}


def test_an_invalid_value_gives_400_in_french_and_changes_nothing(client):
    before = config.VOICE
    r = client.put("/api/settings", json={"voice": "robot"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Voix de JARVIS : valeur non reconnue (robot)."
    # All or nothing: one bad value and the good one is not applied either.
    r = client.put("/api/settings", json={"voice": "cedar", "idle_minutes": 999})
    assert r.status_code == 400
    assert r.json()["detail"] == "Mise en veille après : choisissez une valeur entre 0 min et 120 min."
    assert config.VOICE == before
    assert saved_file() == {}
    assert client.put("/api/settings", json={"no_such_thing": 1}).json()["detail"].startswith("Réglage inconnu")
    assert client.put("/api/settings", json={}).status_code == 400


def test_bypass_needs_an_explicit_confirmation(client):
    r = client.put("/api/settings", json={"permission_mode": "bypassPermissions"})
    assert r.status_code == 400
    assert "sensible" in r.json()["detail"] and "jamais à la voix" in r.json()["detail"]
    assert config.PERMISSION_MODE != "bypassPermissions"
    # Only a real true counts.
    for confirm in ("true", 1, "oui"):
        r = client.put("/api/settings", json={"permission_mode": "bypassPermissions", "confirm": confirm})
        assert r.status_code == 400, confirm
    r = client.put("/api/settings", json={"permission_mode": "bypassPermissions", "confirm": True})
    assert r.status_code == 200
    assert config.PERMISSION_MODE == "bypassPermissions"
    assert r.json()["applied"] == {"permission_mode": "task"}


def test_every_sensitive_setting_needs_the_confirmation(client, tmp_path):
    assert settings.SENSITIVE == {"permission_mode", "workdir", "mcp_config"}
    folder = tmp_path / "travail"
    for key, value in (("workdir", str(folder)), ("mcp_config", ""), ("permission_mode", "auto")):
        assert client.put("/api/settings", json={key: value}).status_code == 400, key
        # mixed with a harmless one, the confirmation is still needed
        assert client.put("/api/settings", json={key: value, "city": "Laon"}).status_code == 400, key
    assert config.CITY != "Laon"
    assert client.put("/api/settings", json={"workdir": str(folder), "confirm": True}).status_code == 200
    assert config.WORKDIR == str(folder)


def test_the_key_route_writes_env_and_never_returns_the_key(client, tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("# JARVIS\r\n# OPENAI_API_KEY=commentée\r\nOPENAI_API_KEY=sk-old\r\nJARVIS_VOICE=ballad\r\n"
                    .encode("utf-8"))
    r = client.post("/api/settings/openai-key", json={"key": KEY, "confirm": True})
    assert r.status_code == 200
    assert KEY not in r.text and KEY[3:] not in r.text
    assert r.json() == {"ok": True, "masked": "sk-…WXYZ"}
    # The line is replaced in place; the rest of the file, its comments and CRLF stay.
    assert env.read_bytes().decode("utf-8") == (
        f"# JARVIS\r\n# OPENAI_API_KEY=commentée\r\nOPENAI_API_KEY={KEY}\r\nJARVIS_VOICE=ballad\r\n")
    assert not (tmp_path / ".env.tmp").exists()
    assert config.OPENAI_API_KEY == KEY  # used at once by the next session
    # Nothing the page reads afterwards carries it either.
    for path in ("/api/settings", "/api/config", "/api/onboarding"):
        assert KEY not in client.get(path).text, path
    assert client.get("/api/settings").json()["key"] == {"present": True, "masked": "sk-…WXYZ"}


def test_the_key_needs_the_confirmation_too(client, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-old\n", encoding="utf-8")
    for body in ({"key": KEY}, {"key": KEY, "confirm": False}):
        r = client.post("/api/settings/openai-key", json=body)
        assert r.status_code == 400 and "sensible" in r.json()["detail"]
        assert KEY not in r.text
    assert env.read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-old\n"
    assert config.OPENAI_API_KEY == "sk-old"


def test_no_voice_tool_can_change_settings_or_permissions():
    names = set()
    for family in tools.FAMILIES:
        names |= {t["name"] for t in family.TOOLS} | set(family.HANDLERS) | set(family.CLIENT_TOOLS)
    assert names, "the registry lists the voice tools"
    for name in names:
        assert "settings" not in name.lower() and "permission" not in name.lower(), name
        assert "setting" not in name.lower() and "config" not in name.lower(), name
    assert settings not in tools.FAMILIES
    assert not hasattr(settings, "TOOLS") and not hasattr(settings, "HANDLERS")
    # Nor does any tool description offer it to the model.
    described = json.dumps(tools.session_tools(), ensure_ascii=False).lower()
    for word in ("permission_mode", "bypasspermissions", "openai_api_key", "settings.json"):
        assert word not in described

# ---------------------------------------------------------------- the key, in detail


@pytest.mark.parametrize("bad", ["", "sk-short", "pk-" + "a" * 30, KEY + "\nJARVIS_PORT=1",
                                 KEY + " #", KEY + "\rX=1", '"' + KEY + '"', "sk-é" + "a" * 30])
def test_a_malformed_key_is_refused_and_env_untouched(client, tmp_path, bad):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-old\n", encoding="utf-8")
    r = client.post("/api/settings/openai-key", json={"key": bad, "confirm": True})
    assert r.status_code == 400
    assert "Clé OpenAI invalide" in r.json()["detail"]
    assert env.read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-old\n"
    assert config.OPENAI_API_KEY == "sk-old"


def test_key_is_added_when_env_has_none_and_ansi_files_stay_ansi(tmp_path):
    env = tmp_path / ".env"
    settings.write_key(KEY)  # no .env yet: created with the key alone
    assert env.read_text(encoding="utf-8").splitlines() == [f"OPENAI_API_KEY={KEY}"]
    env.write_bytes("# Clé à coller ci-dessous\nJARVIS_CITY=Orléans".encode("cp1252"))  # Notepad's ANSI
    settings.write_key(KEY)
    assert env.read_bytes() == f"# Clé à coller ci-dessous\nJARVIS_CITY=Orléans\nOPENAI_API_KEY={KEY}\n".encode("cp1252")
    env.write_bytes(b"\xef\xbb\xbfOPENAI_API_KEY=\nJARVIS_CITY=Laon\n")  # a BOM stays a BOM
    settings.write_key(KEY)
    assert env.read_bytes() == f"\ufeffOPENAI_API_KEY={KEY}\nJARVIS_CITY=Laon\n".encode("utf-8")


def test_mask():
    assert settings.mask(KEY) == "sk-…WXYZ"
    assert settings.mask("") == ""
    assert settings.mask("sk-abc") == "sk-…"

# ---------------------------------------------------------------- overrides


def test_saved_settings_win_over_env_at_startup_and_bad_ones_are_skipped(caplog):
    store.save(settings.SETTINGS_FILE, {"voice": "marin", "idle_minutes": 7, "model_simple": "sonnet",
                                        "briefing_time": "25:00", "unknown": 1, "quiet_hours": "23-6"})
    config.BRIEFING_TIME = "08:00"
    settings.apply_overrides()
    assert (config.VOICE, config.IDLE_MINUTES, config.MODELS["simple"]) == ("marin", 7, "sonnet")
    assert config.QUIET_HOURS == "23:00-06:00"
    assert config.BRIEFING_TIME == "08:00"  # .env's value stays
    assert "briefing_time" in caplog.text
    # What is applied at startup is not 'waiting for a restart'.
    assert settings.restart_pending() == []


def test_null_goes_back_to_the_env_value(client):
    env_voice = settings._ENV["voice"]
    client.put("/api/settings", json={"voice": "sage" if env_voice != "sage" else "ash"})
    assert saved_file()
    r = client.put("/api/settings", json={"voice": None})
    assert r.status_code == 200
    assert config.VOICE == env_voice
    assert "voice" not in saved_file()


def test_restart_settings_are_reported_until_the_next_start(client):
    r = client.put("/api/settings", json={"hotkey": "Ctrl + Alt + Maj + K", "tray": not config.TRAY})
    assert r.status_code == 200
    assert config.HOTKEY == "ctrl+alt+shift+k"
    assert r.json()["applied"] == {"hotkey": "restart", "tray": "restart"}
    assert sorted(r.json()["restart"]) == ["hotkey", "tray"]
    assert sorted(client.get("/api/settings").json()["restart"]) == ["hotkey", "tray"]
    # Put back as it was: nothing waits any more.
    client.put("/api/settings", json={"hotkey": settings._BOOT["hotkey"], "tray": settings._BOOT["tray"]})
    assert client.get("/api/settings").json()["restart"] == []


def test_a_hotkey_wp13_can_swap_live_is_applied_at_once(client, monkeypatch):
    calls = []
    monkeypatch.setattr(settings.shell, "set_hotkey", calls.append, raising=False)
    r = client.put("/api/settings", json={"hotkey": "ctrl+alt+shift+h"})
    assert r.json()["applied"] == {"hotkey": "now"} and r.json()["restart"] == []
    assert calls == ["ctrl+alt+shift+h"]

    def refuse(combo):
        raise OSError("déjà pris")
    monkeypatch.setattr(settings.shell, "set_hotkey", refuse, raising=False)
    r = client.put("/api/settings", json={"hotkey": "ctrl+alt+shift+g"})
    assert r.json()["applied"] == {"hotkey": "restart"} and r.json()["restart"] == ["hotkey"]


def test_a_full_disk_changes_nothing(client, monkeypatch):
    before = config.VOICE

    def full(name, data):
        raise OSError("No space left on device")
    monkeypatch.setattr(settings.store, "save", full)
    r = client.put("/api/settings", json={"voice": "sage" if before != "sage" else "ash"})
    assert r.status_code == 500 and "non enregistrés" in r.json()["detail"]
    assert config.VOICE == before


def test_models_are_entries_of_config_models(client):
    r = client.put("/api/settings", json={"model_complex": "", "model_simple": "claude-haiku-5-5"})
    assert r.status_code == 200
    assert config.MODELS["complexe"] == "" and config.MODELS["simple"] == "claude-haiku-5-5"
    assert client.put("/api/settings", json={"model_normal": "sonnet; rm -rf /"}).status_code == 400

# ---------------------------------------------------------------- validation


S = settings.BY_KEY


@pytest.mark.parametrize("value,expected", [("8:00", "08:00"), ("08h30", "08:30"), ("7h", "07:00"),
                                            ("", ""), ("  6:05 ", "06:05")])
def test_times(value, expected):
    assert settings.validate(S["briefing_time"], value) == expected


@pytest.mark.parametrize("value", ["24:00", "8:60", "huit heures", "8:5", 8])
def test_bad_times(value):
    with pytest.raises(settings.SettingError, match="Briefing du matin"):
        settings.validate(S["briefing_time"], value)


@pytest.mark.parametrize("value,expected", [("22:30-07:30", "22:30-07:30"), ("22h-7h", "22:00-07:00"),
                                            ("22 h 30 à 6 h", "22:30-06:00"), ("", ""), ("off", "")])
def test_quiet_hours(value, expected):
    assert settings.validate(S["quiet_hours"], value) == expected


@pytest.mark.parametrize("value", ["22:30", "22-22", "minuit-midi", "25-7"])
def test_bad_quiet_hours(value):
    with pytest.raises(settings.SettingError):
        settings.validate(S["quiet_hours"], value)


@pytest.mark.parametrize("value,expected", [
    ("lun-ven", "lun-ven"), ("tous", "tous"), ("lun,mar,mer,jeu,ven", "lun-ven"),
    ("Lundi, Mercredi, vendredi", "lun,mer,ven"), (["sam", "dim"], "sam-dim"),
    ("lun-dim", "tous"), ("ven-lun", "lun,ven,sam,dim"), ("mar", "mar")])
def test_briefing_days(value, expected):
    assert settings.validate(S["briefing_days"], value) == expected


@pytest.mark.parametrize("value", ["", "lun-funday", "weekend"])
def test_bad_briefing_days(value):
    with pytest.raises(settings.SettingError):
        settings.validate(S["briefing_days"], value)


@pytest.mark.parametrize("value", ["ctrl+alt+j", "j", "ctrl+", "ctrl+ctrl+j", "hyper+j", "ctrl+alt+é",
                                   "ctrl+alt+f25"])
def test_bad_hotkeys(value):
    with pytest.raises(settings.SettingError):
        settings.normalize_hotkey(value)


def test_hotkeys():
    assert settings.normalize_hotkey("Maj+Ctrl+Alt+J") == "ctrl+alt+shift+j"
    assert settings.normalize_hotkey("win+F12") == "win+f12"
    with pytest.raises(settings.SettingError, match="A.R.E.S"):
        settings.normalize_hotkey("alt+ctrl+j")


def test_numbers_and_bools():
    assert settings.validate(S["voice_speed"], "1,1") == 1.1
    assert settings.validate(S["task_timeout"], 900) == 900
    for bad in (True, "vite", float("nan"), 0.1, 2):
        with pytest.raises(settings.SettingError):
            settings.validate(S["voice_speed"], bad)
    with pytest.raises(settings.SettingError, match="entier"):
        settings.validate(S["idle_minutes"], 2.5)
    with pytest.raises(settings.SettingError, match="1 min et 120 min"):
        settings.validate(S["task_timeout"], 30)  # shown in minutes, like the dialog
    assert settings.validate(S["wake_word"], False) is False
    with pytest.raises(settings.SettingError):
        settings.validate(S["wake_word"], "non")


def test_texts_refuse_control_characters_and_length():
    assert settings.validate(S["city"], "  Laon ") == "Laon"
    for bad in ("Laon\nJARVIS_PORT=1", "x" * 81, 12):
        with pytest.raises(settings.SettingError):
            settings.validate(S["city"], bad)


def test_workdir_must_be_absolute_and_outside_jarvis(tmp_path):
    assert settings.validate(S["workdir"], str(tmp_path / "travail")) == str(tmp_path / "travail")
    for bad in ("travail", "", str(config.ROOT), str(config.ROOT / "static"), str(config.DATA_DIR / "x")):
        with pytest.raises(settings.SettingError):
            settings.validate(S["workdir"], bad)


def test_mcp_config_must_be_a_claude_code_file(tmp_path):
    good = tmp_path / "mcp.json"
    good.write_text(json.dumps({"mcpServers": {"mail": {"command": "x"}}}), encoding="utf-8")
    assert settings.validate(S["mcp_config"], str(good)) == str(good)
    assert settings.validate(S["mcp_config"], "") == ""
    bad = tmp_path / "bad.json"
    bad.write_text("{pas du json", encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text("[1, 2]", encoding="utf-8")
    for path, why in ((bad, "JSON valide"), (other, "mcpServers"), (tmp_path / "absent.json", "introuvable")):
        with pytest.raises(settings.SettingError, match=why):
            settings.validate(S["mcp_config"], str(path))


def test_schema_is_complete_and_french():
    keys = [s.key for s in settings.SCHEMA]
    assert len(keys) == len(set(keys))
    sections = {i for i, _ in settings.SECTIONS}
    for s in settings.SCHEMA:
        assert s.section in sections, s.key
        assert s.live in ("now", "session", "task", "restart"), s.key
        attr = s.attr.split(".")[0]
        assert hasattr(config, attr), s.key
        assert s.label and s.label[0].isupper(), s.key
        for text in (s.label, s.help, *(label for _, label in s.choices)):
            words = set(text.lower().replace("'", " ").split())
            assert not words & {"tu", "toi", "ton", "ta", "tes", "te"}, text
        if s.choices:
            assert s.kind == "choice"
        # the current value is a valid one, so the dialog never starts in error
        if s.kind not in ("path", "file", "hotkey"):
            settings.validate(s, settings._get(s))
    for wanted in ("voice", "realtime_model", "wake_word", "idle_minutes", "quiet_hours", "briefing_time",
                   "briefing_days", "city", "task_budget_usd", "daily_budget_usd", "ares", "hotkey",
                   "permission_mode", "noise_reduction", "eagerness"):
        assert wanted in keys
    voices = [c[0] for c in settings.BY_KEY["voice"].choices]
    assert voices == ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]
    assert settings.BY_KEY["permission_mode"].danger[0] == "bypassPermissions"

# ---------------------------------------------------------------- the other routes


def test_get_settings_shape(client):
    body = client.get("/api/settings").json()
    assert [s["id"] for s in body["sections"]][:2] == ["connexion", "voix"]
    entry = next(e for e in body["schema"] if e["key"] == "permission_mode")
    assert entry["sensitive"] is True and entry["danger"]["value"] == "bypassPermissions"
    timeout = next(e for e in body["schema"] if e["key"] == "task_timeout")
    assert timeout["scale"] == 60 and timeout["unit"] == "min"
    assert body["values"]["voice"] == config.VOICE
    assert body["key"] == {"present": True, "masked": "sk-…"}
    assert set(body["versions"]) >= {"jarvis", "claude_code", "python", "realtime_model"}
    assert "sk-old" not in json.dumps(body)


def test_config_adds_quiet_hours_cap_and_versions(client, monkeypatch):
    monkeypatch.setattr(config, "QUIET_HOURS", "22:30-07:30")
    monkeypatch.setattr(settings, "usage_capped", lambda: True)
    body = client.get("/api/config").json()
    assert body["quiet_hours"] == "22:30-07:30"
    assert body["usage_capped"] is True
    assert body["versions"]["realtime_model"] == config.REALTIME_MODEL
    assert {"wake_word", "speech_lang", "idle_minutes"} <= set(body)


def test_autostart_route(client, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "set_autostart", lambda on: calls.append(on) or "JARVIS se lancera au démarrage.")
    r = client.post("/api/autostart", json={"on": True})
    assert r.status_code == 200 and r.json()["message"] == "JARVIS se lancera au démarrage."
    assert calls == [True]

    def refuse(on):
        raise RuntimeError("Le démarrage automatique n'est géré que sous Windows.")
    monkeypatch.setattr(desktop, "set_autostart", refuse)
    r = client.post("/api/autostart", json={"on": False})
    assert r.status_code == 400 and "Windows" in r.json()["detail"]


def test_open_data_folder_opens_only_the_data_folder(client, monkeypatch):
    opened = []
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    r = client.post("/api/settings/open-data", json={"path": "C:/Windows"})
    assert r.status_code == 200
    assert opened == [str(config.DATA_DIR)]


def test_new_routes_keep_the_guard():
    raw = TestClient(server.app, base_url=BASE)
    for method, path in (("GET", "/api/settings"), ("PUT", "/api/settings"),
                         ("POST", "/api/settings/openai-key"), ("POST", "/api/autostart"),
                         ("POST", "/api/settings/open-data"), ("GET", "/api/health"),
                         ("GET", "/api/onboarding"), ("POST", "/api/onboarding")):
        assert raw.request(method, path).status_code == 401, path
        r = raw.request(method, path, headers={**AUTH, "Origin": "https://evil.example"})
        assert r.status_code == 403, path


def test_config_event_reaches_the_pages(client, monkeypatch):
    seen = []
    monkeypatch.setattr(events, "publish", lambda kind, data: seen.append((kind, data)))
    client.put("/api/settings", json={"idle_minutes": 5})
    assert seen == [("config", {"keys": ["idle_minutes"], "restart": []})]
    assert client.get("/api/config").json()["idle_minutes"] == 5
