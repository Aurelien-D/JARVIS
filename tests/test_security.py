import re
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

import server
from jarvis import config, realtime, security

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
    monkeypatch.setattr(realtime.httpx, "post", _fake_openai(200, {"value": "ek_temp"}, captured))
    r = client.post("/api/session", headers=AUTH, json={"recent": "monsieur : bonjour"})
    assert r.status_code == 200
    body = r.json()
    assert body.pop("client_secret") == "ek_temp"
    assert body.pop("model") == config.REALTIME_MODEL
    assert len(body.pop("session_id")) == 32  # ties later confirmations to this session
    assert not body
    assert "sk-secret" not in r.text
    session = captured["json"]["session"]
    assert "monsieur : bonjour" in session["instructions"]
    assert "delegate_to_claude" in [t["name"] for t in session["tools"]]


def test_openai_key_error_is_not_mistaken_for_a_page_token_error(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-bad")
    monkeypatch.setattr(realtime.httpx, "post", _fake_openai(401, {"error": "bad key"}, {}))
    r = client.post("/api/session", headers=AUTH, json={})
    assert r.status_code == 502  # a 401 would make the page reload itself
    assert "OpenAI 401" in r.json()["detail"]


def test_client_side_tools_are_not_run_by_the_server(client):
    r = client.post("/api/tool", headers=AUTH, json={"name": "display_card", "arguments": {}})
    assert r.status_code == 400


def test_missing_openai_key_is_explained(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    r = client.post("/api/session", headers=AUTH, json={})
    assert r.status_code == 500
    assert "OPENAI_API_KEY" in r.json()["detail"]


def test_tool_call_carries_its_voice_session(client, monkeypatch):
    seen = []
    monkeypatch.setattr(server.tools, "run_tool", lambda name, args, ctx: seen.append(ctx) or {"ok": True})
    r = client.post("/api/tool", headers=AUTH, json={"name": "get_status", "session_id": "abc"})
    assert r.status_code == 200
    assert seen[0].session_id == "abc"


def test_modules_are_served_as_javascript(client):
    # Browsers refuse a module script served as text/plain (a Windows registry quirk).
    r = client.get("/static/js/main.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert client.get("/static/css/tokens.css").headers["content-type"].startswith("text/css")
    # Revalidated every time, so an update never mixes old and new modules.
    assert r.headers["cache-control"] == "no-cache"


def test_page_loads_only_the_module_entry_point(client):
    html = client.get("/").text
    local_scripts = re.findall(r'<script[^>]*src="(/[^"]+)"[^>]*>', html)
    assert local_scripts == ["/static/js/main.js"]
    assert '<script type="module" src="/static/js/main.js">' in html
    assert not (config.ROOT / "static" / "jarvis.js").exists()


def test_shutdown_needs_the_token(client, monkeypatch):
    stopped = []
    monkeypatch.setattr(server.tasks, "shutdown", lambda: stopped.append(True))
    monkeypatch.setattr(server, "SERVER", SimpleNamespace(should_exit=False))
    assert client.post("/api/shutdown").status_code == 401
    assert client.post("/api/shutdown", headers={**AUTH, "Origin": "https://evil.example"}).status_code == 403
    assert not stopped and not server.SERVER.should_exit
    assert client.post("/api/shutdown", headers=AUTH).json() == {"ok": True}
    assert stopped == [True]
    assert server.SERVER.should_exit


def served_routes(routes=None, prefix=""):
    """{path: methods} of everything the app serves, routers included, whether
    this FastAPI flattens included routers or keeps them as one route each.
    A mount is {"MOUNT"}; a route with no HTTP method (a websocket) is {"WEBSOCKET"}."""
    found: dict = {}
    for route in server.app.routes if routes is None else routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            context = getattr(route, "include_context", None)
            for path, methods in served_routes(inner.routes, prefix + (getattr(context, "prefix", "") or "")).items():
                found.setdefault(path, set()).update(methods)
        elif isinstance(route, Mount):
            found.setdefault(prefix + route.path, set()).add("MOUNT")
        else:
            found.setdefault(prefix + route.path, set()).update(getattr(route, "methods", None) or {"WEBSOCKET"})
    return found


# Every /api route this version has, the new ones included: one that disappears
# from the sweep (renamed, moved under another prefix) must be noticed here.
KNOWN_API = {
    "/api/config", "/api/session", "/api/tool", "/api/events", "/api/shutdown",
    "/api/tasks", "/api/task/{task_id}", "/api/task/{task_id}/log", "/api/task/{task_id}/cancel",
    "/api/schedules", "/api/schedules/{item_id}", "/api/memory", "/api/memory/{fact_id}",
    "/api/inbox", "/api/inbox/{item_id}/ack", "/api/presence", "/api/delivery", "/api/dnd",
    "/api/voice/turn", "/api/voice/taint", "/api/pending", "/api/pending/{pending_id}/decide",
    "/api/journal", "/api/undo/{fact_id}", "/api/ares",  # WP14, WP15
}
FOREIGN_ORIGINS = ("https://evil.example", "http://127.0.0.1:9999", "null", "http://localhost.evil.example:8788")


def test_every_api_route_is_guarded(client):
    # The guard is app-wide, so routes moved to routers (and those added later) keep it.
    paths = server.app.openapi()["paths"]
    assert {"/api/config", "/api/session", "/api/tool", "/api/events", "/api/tasks",
            "/api/task/{task_id}", "/api/task/{task_id}/cancel", "/api/schedules",
            "/api/schedules/{item_id}", "/api/memory", "/api/memory/{fact_id}",
            "/api/shutdown"} <= set(paths)
    for path, methods in paths.items():
        if not path.startswith("/api/"):
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in methods:
            assert client.request(method.upper(), url).status_code == 401, (method, path)
            r = client.request(method.upper(), url, headers={**AUTH, "Host": "evil.example:8788"})
            assert r.status_code == 403, (method, path)


def test_token_host_and_origin_guard_holds_on_every_api_route(client, monkeypatch):
    """The sweep walks the app's own routing table (not only the OpenAPI schema),
    so a route added later, even hidden from the schema, is checked too."""
    stopped = []
    monkeypatch.setattr(server.tasks, "shutdown", lambda: stopped.append(True))
    served = served_routes()
    assert KNOWN_API <= set(served), sorted(KNOWN_API - set(served))
    assert {p for p in server.app.openapi()["paths"] if p.startswith("/api/")} <= set(served)
    checked = 0
    for path, methods in sorted(served.items()):
        if not path.startswith("/api/"):
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            where = (method, path)
            # No token, a wrong one, an empty one: 401 (the page reloads for a new one).
            assert client.request(method, url).status_code == 401, where
            assert client.request(method, url, headers={"X-Jarvis-Token": "nope"}).status_code == 401, where
            assert client.request(method, url, headers={"X-Jarvis-Token": ""}).status_code == 401, where
            assert client.request(method, f"{url}?token=nope").status_code == 401, where
            # The right token from a foreign Host (DNS rebinding): 403.
            for host in ("evil.example:8788", "evil.example", "127.0.0.1.evil.example:8788"):
                r = client.request(method, url, headers={**AUTH, "Host": host})
                assert r.status_code == 403, (*where, host)
            # The right token sent by another site's page: 403.
            for origin in FOREIGN_ORIGINS:
                r = client.request(method, url, headers={**AUTH, "Origin": origin})
                assert r.status_code == 403, (*where, origin)
                assert client.request(method, url, headers={"Origin": origin}).status_code == 403, (*where, origin)
            checked += 1
    assert checked >= len(KNOWN_API)
    assert not stopped  # nothing got through to /api/shutdown


def test_only_the_page_and_the_api_are_served_holds():
    """Outside /api/ there is no token check: only the page, the health probe and
    the static files may live there. FastAPI's /docs and /redoc would run
    unpinned CDN scripts in JARVIS's origin, where they could read the token."""
    served = served_routes()
    assert all("WEBSOCKET" not in m for m in served.values()), "the guard is an HTTP middleware only"
    others = {path: methods for path, methods in served.items() if not path.startswith("/api/")}
    assert others == {"/": {"GET"}, "/healthz": {"GET"}, "/static": {"MOUNT"}}


def test_page_is_refused_to_a_foreign_host_or_origin_holds(client):
    for host in ("evil.example:8788", "127.0.0.1.evil.example"):
        r = client.get("/", headers={"Host": host})
        assert r.status_code == 403 and security.TOKEN not in r.text
    for origin in FOREIGN_ORIGINS:
        r = client.get("/", headers={"Origin": origin})
        assert r.status_code == 403 and security.TOKEN not in r.text
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


def test_static_files_never_reach_outside_their_folder_holds(client):
    # Only the status and a yes/no are asserted: a failure never prints what was served.
    for url in ("/static/../.env", "/static/%2e%2e/.env", "/static/..%2f.env", "/static/js/../../.env",
                "/static/..%5c.env", "/static/../index.html", "/static/../jarvis/config.py"):
        r = client.get(url)
        leaked = "OPENAI_API_KEY" in r.text or "__JARVIS_TOKEN__" in r.text
        assert r.status_code in (400, 404), (url, r.status_code)
        assert not leaked, url
