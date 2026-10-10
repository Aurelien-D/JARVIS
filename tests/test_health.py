"""Bilan de santé (WP12): each check against fakes (OpenAI, the claude command,
the Windows microphone switch), the cache, the deadlines, and the first-run
state. Nothing here reaches the network or the real Claude Code."""
import json
import sys
import time
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

import server
from jarvis import config, health, security, store, tasks

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}
KEY = "sk-proj-HEALTH0123456789abcdefWXYZ"

FAKE_CLAUDE = r'''
import json, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("{version} (Claude Code)")
elif args[:2] == ["auth", "status"]:
    method = "{method}"
    if method == "none":
        print(json.dumps({{"loggedIn": False, "authMethod": "none"}}))
        sys.exit(1)
    print(json.dumps({{"loggedIn": True, "authMethod": method, "apiProvider": "firstParty"}}))
'''


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    health.forget()
    monkeypatch.setattr(config, "OPENAI_API_KEY", KEY)
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    yield
    health.forget()


@pytest.fixture
def claude(tmp_path, monkeypatch):
    """claude_command() runs a fake: claude(version, method) sets what it says."""
    def make(version="2.1.295", method="claude.ai"):
        script = tmp_path / f"claude_{len(list(tmp_path.glob('claude_*')))}.py"
        script.write_text(FAKE_CLAUDE.format(version=version, method=method), encoding="utf-8")
        monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
        return script
    return make


def fake_openai(monkeypatch, status=200, body=None, raises=None):
    calls = []

    def get(url, headers, timeout):
        calls.append({"url": url, "headers": headers})
        if raises is not None:
            raise raises
        return httpx.Response(status, json=body if body is not None else {}, request=httpx.Request("GET", url))
    monkeypatch.setattr(health.httpx, "get", get)
    return calls

# ---------------------------------------------------------------- acceptance


def test_a_refused_key_is_an_error_with_a_french_fix(monkeypatch):
    calls = fake_openai(monkeypatch, 401, {"error": {"message": f"Incorrect API key provided: {KEY}",
                                                     "code": "invalid_api_key"}})
    result = health.check_openai()
    assert result["id"] == "openai" and result["level"] == "error" and result["ok"] is False
    assert result["message_fr"] == "Clé OpenAI refusée."
    assert "Réglages › Connexion" in result["fix_fr"] and "platform.openai.com" in result["fix_fr"]
    assert KEY not in json.dumps(result)  # OpenAI echoes the key: never repeated
    # A free read of the model's description, with the key sent to OpenAI only.
    assert calls == [{"url": "https://api.openai.com/v1/models/gpt-realtime-2.1",
                      "headers": {"Authorization": f"Bearer {KEY}"}}]


def test_claude_logged_out_says_so(claude):
    claude(method="none")  # `claude auth status` exits with 1
    [result] = health.check_claude(refresh=True)
    assert result["id"] == "claude" and result["level"] == "error"
    assert "n'est pas connecté" in result["message_fr"]
    assert "claude" in result["fix_fr"] and "connectez-vous" in result["fix_fr"]

# ---------------------------------------------------------------- OpenAI


@pytest.mark.parametrize("status,body,level,words", [
    (200, {"id": "gpt-realtime-2.1"}, "ok", "Clé OpenAI valide"),
    (403, {}, "error", "n'a pas accès"),
    (404, {"error": {"code": "model_not_found"}}, "error", "indisponible pour votre compte"),
    (429, {"error": {"code": "insufficient_quota"}}, "error", "Crédit OpenAI épuisé"),
    (429, {"error": {"code": "rate_limit_exceeded"}}, "warning", "limite les demandes"),
    (503, {}, "warning", "rencontre un problème"),
])
def test_openai_answers_are_mapped(monkeypatch, status, body, level, words):
    fake_openai(monkeypatch, status, body)
    result = health.check_openai()
    assert result["level"] == level and words in result["message_fr"]


def test_openai_unreachable_is_a_warning_not_a_bad_key(monkeypatch):
    fake_openai(monkeypatch, raises=httpx.ConnectError("refused"))
    assert health.check_openai()["level"] == "warning"
    health.forget()
    fake_openai(monkeypatch, raises=httpx.ReadTimeout("slow"))
    assert "ne répond pas" in health.check_openai()["message_fr"]


def test_missing_or_malformed_key_needs_no_network(monkeypatch):
    calls = fake_openai(monkeypatch)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    assert health.check_openai()["message_fr"].startswith("Clé OpenAI absente")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "pk-123")
    assert "bonne forme" in health.check_openai()["message_fr"]
    assert calls == []


def test_the_verdict_is_cached_until_the_key_changes(monkeypatch):
    calls = fake_openai(monkeypatch, 200)
    health.check_openai()
    health.check_openai()
    assert len(calls) == 1
    health.check_openai(refresh=True)  # 'Revérifier'
    assert len(calls) == 2
    monkeypatch.setattr(config, "OPENAI_API_KEY", KEY + "2")
    health.check_openai()
    assert len(calls) == 3
    # The cache keeps a digest, never the key.
    assert KEY not in repr(health._cache)

# ---------------------------------------------------------------- Claude Code


def test_claude_missing(monkeypatch):
    def missing():
        raise FileNotFoundError("claude")
    monkeypatch.setattr(tasks, "claude_command", missing)
    [result] = health.check_claude()
    assert result["level"] == "error" and result["message_fr"] == "Claude Code est introuvable."
    assert "install" in result["fix_fr"]


def test_claude_ready_with_its_account(claude):
    claude("2.1.300", "claude.ai")
    results = health.check_claude(refresh=True)
    assert [r["id"] for r in results] == ["claude"]
    assert results[0]["level"] == "ok"
    assert results[0]["message_fr"] == "Claude Code 2.1.300 installé et connecté (compte claude.ai)."


def test_claude_too_old(claude):
    claude("2.1.200", "claude.ai")
    [result] = health.check_claude(refresh=True)
    assert result["level"] == "warning" and "trop ancien" in result["message_fr"]
    assert "claude update" in result["fix_fr"]


def test_connectors_need_a_claude_ai_login(claude, monkeypatch):
    claude("2.1.300", "oauth_token")
    results = health.check_claude(refresh=True)
    assert [r["level"] for r in results] == ["ok", "info"]
    assert "jeton OAuth" in results[1]["message_fr"]
    health.forget()
    claude("2.1.301", "claude.ai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    results = health.check_claude(refresh=True)
    assert results[1]["id"] == "claude_connectors" and "ANTHROPIC_API_KEY est défini" in results[1]["message_fr"]


def test_auth_status_is_cached(claude, monkeypatch):
    claude("2.1.300", "claude.ai")
    runs = []
    real = health.subprocess.run
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **k: runs.append(a) or real(*a, **k))
    health.auth_status()
    health.auth_status()
    assert len(runs) == 1
    health.auth_status(refresh=True)
    assert len(runs) == 2


def test_lost_protections_are_reported(claude):
    claude("2.1.300", "claude.ai")
    assert health.check_hardening() == []
    store.save("state.json", {"safe_mode_broken": "2.1.300", "dnd_until": 1})
    [result] = health.check_hardening()
    assert result["level"] == "warning" and "--safe-mode" in result["message_fr"]
    # Another version: the flag is tried again, nothing to report.
    store.save("state.json", {"safe_mode_broken": "2.1.100"})
    assert health.check_hardening() == []


def test_bypass_is_flagged(monkeypatch):
    monkeypatch.setattr(config, "PERMISSION_MODE", "bypassPermissions")
    [result] = health.check_permission()
    assert result["level"] == "warning" and "sans garde-fou" in result["message_fr"]
    monkeypatch.setattr(config, "PERMISSION_MODE", "auto")
    assert health.check_permission() == []

# ---------------------------------------------------------------- this PC


def test_windows_microphone_switch(monkeypatch):
    real = health.mic_consent
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(health, "mic_consent", lambda: "deny")
    [result] = health.check_microphone()
    assert result["level"] == "error"
    assert "Autoriser les applications de bureau à accéder au microphone" in result["fix_fr"]
    monkeypatch.setattr(health, "mic_consent", lambda: "allow")
    assert health.check_microphone() == []
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    assert health.check_microphone() == [] and real() == ""  # unknown off Windows


def test_workdir_and_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    [result] = health.check_workdir()
    assert result["level"] == "info" and "sera créé" in result["message_fr"]
    (tmp_path / "travail").mkdir()
    assert health.check_workdir()[0]["level"] == "ok"
    assert health.check_data()[0]["level"] == "ok"
    assert list(config.DATA_DIR.iterdir()) == []  # the write test leaves nothing behind
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: type("U", (), {"free": 10 * 1024 * 1024})())
    assert "presque plein" in health.check_data()[0]["message_fr"]


def test_browser(monkeypatch):
    monkeypatch.setattr(health.desktop, "_app_browser", lambda: None)
    assert health.check_browser()[0]["level"] == "warning"
    monkeypatch.setattr(health.desktop, "_app_browser", lambda: r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    assert health.check_browser()[0]["message_fr"] == "Fenêtre JARVIS : Google Chrome."
    monkeypatch.setattr(health.desktop, "_app_browser", lambda: r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    result = health.check_browser()[0]
    assert result["level"] == "info" and "Chrome est conseillé" in result["message_fr"]


def test_mcp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFIG", "")
    assert health.check_mcp() == []
    path = tmp_path / "mcp.json"
    path.write_text("{oups", encoding="utf-8")
    monkeypatch.setattr(config, "MCP_CONFIG", str(path))
    assert health.check_mcp()[0]["level"] == "error"
    path.write_text(json.dumps({"mcpServers": {"a": {}, "b": {}}}), encoding="utf-8")
    assert health.check_mcp()[0]["message_fr"] == "Connecteurs MCP en plus : 2 serveurs."


def test_ares_is_optional(monkeypatch):
    monkeypatch.setattr(config, "ARES", "auto")
    from jarvis import ares
    monkeypatch.setattr(ares, "reachable", lambda: False)
    assert health.check_ares()[0]["level"] == "info"
    monkeypatch.setattr(config, "ARES", "on")
    assert health.check_ares()[0]["level"] == "warning"
    monkeypatch.setattr(ares, "reachable", lambda: True)
    assert health.check_ares()[0]["level"] == "info"
    monkeypatch.setattr(config, "ARES", "jamais")  # read as ares.py reads it
    assert health.check_ares() == []


def test_deadlines(monkeypatch):
    monkeypatch.setattr(health, "_today", lambda: date(2026, 10, 9))
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime")
    monkeypatch.setattr(config, "TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
    monkeypatch.setattr(config, "TRANSCRIBE_FALLBACK", "whisper-1")
    realtime_item, transcribe_item = health.check_deadlines()
    assert realtime_item["level"] == "warning" and "20 janvier 2027 (dans 103 jours)" in realtime_item["message_fr"]
    assert transcribe_item["level"] == "info" and "26 février 2027" in transcribe_item["message_fr"]
    monkeypatch.setattr(health, "_today", lambda: date(2027, 3, 1))
    realtime_item, transcribe_item = health.check_deadlines()
    assert realtime_item["level"] == "error" and transcribe_item["level"] == "warning"
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1")
    monkeypatch.setattr(config, "TRANSCRIBE_MODEL", "gpt-transcribe")
    monkeypatch.setattr(config, "TRANSCRIBE_FALLBACK", "")
    assert health.check_deadlines() == []

# ---------------------------------------------------------------- remote access (Tailscale Serve)


def test_remote_check_reports_serve_state(monkeypatch):
    """Nothing without Tailscale; Funnel, TCP and a wrong target are errors even
    while remote access is off; with it on, ready is ok, absent and stopped warn."""
    from jarvis import remote, tailscale
    states = {"value": "ready"}
    monkeypatch.setattr(tailscale, "serve_status", lambda: {"state": states["value"], "detail": f"Serve {states['value']}.",
                                                            "url": ""})
    monkeypatch.setattr(tailscale, "exe_path", lambda: None)
    assert health.check_remote() == []
    monkeypatch.setattr(tailscale, "exe_path", lambda: r"C:\Program Files\Tailscale\tailscale.exe")
    enabled = {"value": False}
    monkeypatch.setattr(remote, "is_enabled", lambda: enabled["value"])

    def level(state):
        states["value"] = state
        out = health.check_remote()
        assert all(set(i) == {"id", "ok", "level", "title_fr", "message_fr", "fix_fr"} for i in out)
        assert all(i["id"] == "remote" and i["title_fr"] == "Accès à distance" for i in out)
        return out[0]["level"] if out else None

    for state in ("ready", "absent", "stopped", "no_tailscale", "unknown"):
        assert level(state) is None, state  # off: nothing to say
    for state in ("funnel", "tcp", "wrong_target"):
        assert level(state) == "error", state
        assert "Publier sur Tailscale" in health.check_remote()[0]["fix_fr"]
    enabled["value"] = True
    assert level("ready") == "ok"
    assert level("absent") == "warning" and "Réglages › Accès à distance › Publier sur Tailscale" in \
        health.check_remote()[0]["fix_fr"]
    assert level("stopped") == "warning"
    for state in ("funnel", "tcp", "wrong_target"):
        assert level(state) == "error", state
    assert level("unknown") is None
    assert health.check_remote in health.CHECKS


# ---------------------------------------------------------------- all together


def test_route_runs_every_check_and_survives_a_broken_one(monkeypatch, claude):
    claude("2.1.300", "claude.ai")
    fake_openai(monkeypatch, 200)

    def broken(refresh=False):
        raise RuntimeError("boom")

    def slow(refresh=False):
        time.sleep(4)
        return []
    health.check_claude()  # warm: the fake claude starts slowly on a busy Windows runner
    monkeypatch.setattr(health, "CHECKS", [health.check_openai, health.check_claude, broken, slow,
                                           health.check_data])
    monkeypatch.setattr(health, "CHECK_TIMEOUT", 2.5)
    r = TestClient(server.app, base_url=BASE, headers=AUTH).get("/api/health")
    assert r.status_code == 200
    items = r.json()
    assert [i["id"] for i in items] == ["openai", "claude", "broken", "slow", "data"]
    for i in items:
        assert set(i) == {"id", "ok", "level", "title_fr", "message_fr", "fix_fr"}
        assert i["level"] in ("ok", "info", "warning", "error")
        assert i["ok"] is (i["level"] in ("ok", "info"))
    assert items[2]["level"] == "warning" and "impossible" in items[2]["message_fr"]
    assert "trop longue" in items[3]["message_fr"]
    assert KEY not in r.text


def test_every_message_is_french_and_says_vous(monkeypatch, claude):
    claude("2.1.200", "oauth_token")
    fake_openai(monkeypatch, 401)
    monkeypatch.setattr(config, "PERMISSION_MODE", "bypassPermissions")
    monkeypatch.setattr(health, "_today", lambda: date(2026, 10, 9))
    import re
    for i in health.run_checks(refresh=True):
        for text in (i["title_fr"], i["message_fr"], i["fix_fr"]):
            own = re.sub(r"«[^»]*»", "", text)
            assert not re.search(r"\b(tu|toi|ton|ta|tes|te)\b", own, re.I), text

# ---------------------------------------------------------------- first run


def test_onboarding_state_lives_in_state_json():
    api = TestClient(server.app, base_url=BASE, headers=AUTH)
    store.save("state.json", {"dnd_until": 123})
    assert api.get("/api/onboarding").json() == {"onboarded": False}
    assert api.post("/api/onboarding", json={"done": True}).json() == {"onboarded": True}
    assert api.get("/api/onboarding").json() == {"onboarded": True}
    state = store.load("state.json", {})
    assert state["dnd_until"] == 123 and state["onboarded"] > 0  # the other keys stay
    api.post("/api/onboarding", json={"done": False})
    assert health.onboarded() is False
