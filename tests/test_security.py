import httpx
import pytest
from fastapi.testclient import TestClient

import server
from jarvis import config, security

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}


@pytest.fixture
def client():
    # Not used as a context manager: the scheduler thread stays off.
    return TestClient(server.app, base_url=BASE)


def test_page_carries_the_session_token(client):
    r = client.get("/")
    assert r.status_code == 200
    assert security.TOKEN in r.text
    assert "__JARVIS_TOKEN__" not in r.text


def test_foreign_host_is_refused(client):
    # DNS rebinding: an attacker's domain pointed at 127.0.0.1.
    assert client.get("/", headers={"Host": "evil.example:8788"}).status_code == 403
    assert client.get("/api/tasks", headers={**AUTH, "Host": "evil.example:8788"}).status_code == 403


def test_api_requires_the_token(client):
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"X-Jarvis-Token": "nope"}).status_code == 401
    assert client.get("/api/tasks", headers=AUTH).status_code == 200


def test_event_stream_checks_its_query_token(client):
    assert client.get("/api/events?token=nope").status_code == 401


def test_cross_site_request_is_refused(client):
    r = client.post("/api/tool", headers={**AUTH, "Origin": "https://evil.example"},
                    json={"name": "get_status"})
    assert r.status_code == 403


def test_same_origin_request_is_accepted(client):
    r = client.post("/api/tool", headers={**AUTH, "Origin": BASE}, json={"name": "get_status"})
    assert r.status_code == 200
    assert "now" in r.json()


def test_localhost_names_are_accepted(client):
    for host in ("localhost:8788", "[::1]:8788"):
        assert client.get("/healthz", headers={"Host": host}).status_code == 200


def _fake_openai(status, body, captured):
    def post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))
    return post


def test_session_hands_out_a_temporary_secret_only(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-secret")
    captured = {}
    monkeypatch.setattr(server.httpx, "post", _fake_openai(200, {"value": "ek_temp"}, captured))
    r = client.post("/api/session", headers=AUTH, json={"recent": "monsieur : bonjour"})
    assert r.status_code == 200
    assert r.json() == {"client_secret": "ek_temp", "model": config.REALTIME_MODEL}
    assert "sk-secret" not in r.text
    session = captured["json"]["session"]
    assert "monsieur : bonjour" in session["instructions"]
    assert "delegate_to_claude" in [t["name"] for t in session["tools"]]


def test_openai_key_error_is_not_mistaken_for_a_page_token_error(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-bad")
    monkeypatch.setattr(server.httpx, "post", _fake_openai(401, {"error": "bad key"}, {}))
    r = client.post("/api/session", headers=AUTH, json={})
    assert r.status_code == 502  # a 401 would make the page reload itself
    assert "OpenAI 401" in r.json()["detail"]


def test_client_side_tools_are_not_run_by_the_server(client):
    r = client.post("/api/tool", headers=AUTH, json={"name": "display_card", "arguments": {}})
    assert r.status_code == 400
