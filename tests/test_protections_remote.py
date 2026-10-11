"""Remote access protections (spec section 8).

Scaffold (A0):
- P0-1: a proxy header or the Serve port makes a request remote, whatever its
  Host; the remote path never hands out nor accepts the PC's page token.
- P0-2: every listener binds the literal 127.0.0.1 and trusts no forwarded header.
- P0-2b: remote access cannot be switched on before remote.READY.
- P0-25: a phone's event stream never takes part in the election, never hears
  the PC's controls and never silences the PC's toasts; P1: at most 4 streams
  per device.

Remote core (A1), through the real gate (remote_client arrives on the Serve
port, so __Host- cookies and the classifier work as with Tailscale Serve):
- P0-1 and 1b: the PC's token never authenticates remotely; a proxy header on
  the PC port is refused with the antivirus diagnosis.
- P0-3 to 18c: exact Serve headers, the switch, the pause, reverse pairing,
  hashed secrets and __Host- cookies, node binding, cookie plus page token,
  revocation, the route table, harmless settings, lockouts per credential and
  address, remote voice cost, the audit trail, alerts, safe headers, the opt-in;
  P1: at most 5 devices and 2 Siri keys each.
"""
import ast
import asyncio
import hashlib
import inspect
import json
import logging
import re
import socket
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from remote_helpers import (
    IP,
    LOGIN,
    REMOTE_HOST,
    enable_remote,
    page_token,
    paired_client,
    remote_client,
    remote_headers,
)
from test_security import served_routes

import server
from jarvis import (
    audit,
    config,
    confirm,
    desktop,
    devices,
    events,
    inbox,
    listener,
    raccourci,
    realtime,
    remote,
    scheduler,
    security,
    settings,
    store,
    tailscale,
    tasks,
    usage,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "jarvis").glob("*.py")) + [ROOT / "server.py"]
AUTH = {"X-Jarvis-Token": security.TOKEN}
PHONE = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip=IP, login=LOGIN, name="iPhone de test")
OTHER_PHONE = remote.Caller(kind="app", device_id="d_fedcba9876543210", ip=IP, login=LOGIN, name="iPhone 2")
MARKERS = {"X-Forwarded-For": IP, "X-Forwarded-Proto": "https", "X-Forwarded-Host": REMOTE_HOST,
           "Forwarded": f"for={IP};proto=https", "Via": "1.1 proxy", "X-Real-IP": IP,
           "Tailscale-User-Login": LOGIN,
           # any x-forwarded-* and any tailscale-* count, not only the usual ones
           "X-Forwarded-Port": "443", "Tailscale-Funnel-Request": "?1"}


@pytest.fixture(autouse=True)
def fresh_events():
    """No page and no event left over from another test."""
    events._replay.clear()
    events._clients.clear()
    events._leader = None
    events._leader_live = False
    events._closing.clear()
    inbox._toasted.clear()
    yield
    events._closing.clear()
    events._clients.clear()
    events._leader = None
    events._leader_live = False


def parse(chunk: str):
    """One SSE frame -> (id or None, data or None)."""
    event_id, data = None, None
    for line in chunk.splitlines():
        if line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return event_id, data


async def frames(stream, count: int, timeout: float = 2.0) -> list:
    """The next `count` frames that carry data, as (id, data)."""
    out = []
    while len(out) < count:
        event_id, data = parse(await asyncio.wait_for(anext(stream), timeout))
        if data is not None:
            out.append((event_id, data))
    return out


async def until(stream, event_id: int, timeout: float = 2.0) -> list:
    """Every frame that carries data, up to the event with this id, as (id, data)."""
    out = []
    while not out or out[-1][0] != event_id:
        got_id, data = parse(await asyncio.wait_for(anext(stream), timeout))
        if data is not None:
            out.append((got_id, data))
    return out


async def opened(client_id: str, caller=None, last_event_id=None):
    """A stream, registered (its first frame read)."""
    stream = events.stream(client_id, last_event_id, caller)
    assert (await anext(stream)).startswith("retry:")
    return stream


def _remote_subs(device_id: str) -> list:
    return [s for s in events._subscribers if s.caller is not None and s.caller.device_id == device_id]

# ---------------------------------------------------------------- P0-1: classification, never the token

def test_serve_request_with_a_spoofed_local_host_is_remote_holds():
    client = TestClient(server.app)
    for host in ("127.0.0.1:8788", "localhost", "[::1]:8788", REMOTE_HOST, "evil.example"):
        for name, value in MARKERS.items():
            for path in ("/", "/api/config"):
                r = client.get(path, headers={**AUTH, "Host": host, name: value})
                assert r.status_code == 403, (host, name, path, r.status_code)
                assert security.TOKEN not in r.text, (host, name, path)
    # The same requests without the marker are this PC's own page: only the marker made them remote.
    local = TestClient(server.app, base_url="http://127.0.0.1:8788")
    assert security.TOKEN in local.get("/").text
    assert local.get("/api/config", headers=AUTH).json()["remote"] is False


def test_requests_on_the_serve_port_are_remote_holds():
    assert config.REMOTE_PORT == 8789
    bare = TestClient(server.app, base_url="http://127.0.0.1:8789")  # no proxy header at all
    for path in ("/", "/api/config", "/healthz"):
        r = bare.get(path, headers=AUTH)
        assert r.status_code == 403 and security.TOKEN not in r.text, path
    # What Tailscale Serve sends, the PC's token added: refused on every served route.
    client = remote_client(token=security.TOKEN)
    checked = 0
    for path, methods in sorted(served_routes().items()):
        if "MOUNT" in methods:
            path = "/static/js/main.js"
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted((methods - {"HEAD", "OPTIONS", "MOUNT"}) or {"GET"}):
            r = client.request(method, url)
            assert r.status_code == 403, (method, path, r.status_code)
            assert security.TOKEN not in r.text, (method, path)
            checked += 1
    assert checked > 50


def test_the_pc_page_still_gets_its_token_and_the_pc_caller():
    client = TestClient(server.app, base_url="http://127.0.0.1:8788")
    r = client.get("/")
    assert r.status_code == 200 and security.TOKEN in r.text
    assert '<meta name="jarvis-remote" content="0">' in r.text
    assert '<meta name="jarvis-origin" content="pc">' in r.text
    body = client.get("/api/config", headers=AUTH).json()
    assert body["remote"] is False and body["origin"] == "pc" and body["wake_word"] == config.WAKE_WORD

# ---------------------------------------------------------------- P0-2: loopback only

def test_server_binds_loopback_only_holds():
    configs, binds = [], []
    for source in SOURCES:
        text = source.read_text(encoding="utf-8")
        assert "0.0.0.0" not in text, source.name
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            where = f"{source.name}:{node.lineno}"
            if node.func.attr == "Config" and getattr(node.func.value, "id", "") == "uvicorn":
                kw = {k.arg: k.value for k in node.keywords}
                assert None not in kw, f"**kwargs in uvicorn.Config: {where}"
                for name, expected in (("host", "127.0.0.1"), ("proxy_headers", False), ("forwarded_allow_ips", "")):
                    value = kw.get(name)
                    assert isinstance(value, ast.Constant) and value.value is not None \
                        and value.value == expected and type(value.value) is type(expected), (name, where)
                configs.append(where)
            elif node.func.attr == "bind":
                address = node.args[0] if node.args else None
                assert isinstance(address, ast.Tuple), f"bind without a literal address: {where}"
                host = address.elts[0]
                assert isinstance(host, ast.Constant) and host.value == "127.0.0.1", where
                binds.append(where)
    assert any(where.startswith("server.py:") for where in configs), configs
    # No setting can change the listening address (JARVIS_REMOTE_HOST is the Serve name, not a bind).
    config_src = (ROOT / "jarvis" / "config.py").read_text(encoding="utf-8")
    assert not re.search(r"^(HOST|BIND\w*|LISTEN\w*)\s*=", config_src, re.MULTILINE)
    assert '"JARVIS_HOST"' not in config_src

# ---------------------------------------------------------------- P0-2b: the release gate

def test_remote_access_cannot_be_enabled_before_ready_holds():
    assert remote.READY is False
    store.save("remote.json", {"enabled": True, "host": REMOTE_HOST, "logins": [LOGIN], "paused_until": 0,
                               "complet_until": 0, "published": True, "changed_at": 1, "changed_by": "pc"})
    assert remote.is_enabled() is False
    with pytest.raises(remote.RemoteError):
        remote.set_enabled(True, host=REMOTE_HOST, login=LOGIN)
    assert remote.is_enabled() is False
    listener.start_if_enabled()
    assert listener.state()["running"] is False
    # From the PC page: refused too, and nothing was switched on.
    pc = TestClient(server.app, base_url="http://127.0.0.1:8788")
    r = pc.post("/api/remote/state", headers=AUTH, json={"enabled": True, "host": REMOTE_HOST, "login": LOGIN})
    assert r.status_code >= 400 and remote.is_enabled() is False
    # And the phone still meets a closed door.
    assert remote_client().get("/").status_code == 403

# ---------------------------------------------------------------- P0-25: the phone's event stream

def test_remote_stream_never_becomes_leader_holds():
    async def scenario():
        assert not events._subscribers
        pc = await opened("page-pc", remote.PC)
        assert events.leader() == "page-pc"
        # The phone asks for the PC page's own id: it gets an id of its own namespace.
        phone = await opened("page-pc", PHONE)
        (sub,) = _remote_subs(PHONE.device_id)
        assert sub.client.startswith(f"r-{PHONE.device_id}-") and sub.client != "page-pc"
        assert set(events._clients) == {"page-pc"} and events._clients["page-pc"].streams == 1
        assert events.leader() == "page-pc"
        # No leader frame for the phone: the first thing it hears is the next event.
        marker = events.publish("memory", {"n": 1})
        assert await frames(phone, 1) == [(marker, {"type": "memory", "n": 1})]
        await phone.aclose()  # the PC page's presence is untouched
        assert events._clients["page-pc"].streams == 1 and events.leader() == "page-pc"
        await pc.aclose()
        assert events.leader() is None
        # A phone alone: still nobody speaks, and nothing of it joined the election.
        alone = await opened("", PHONE)
        assert events.leader() is None and not events._clients
        await alone.aclose()
        assert events.leader() is None
    asyncio.run(scenario())


def test_remote_stream_drops_pc_control_events_holds(monkeypatch):
    # PHONE is no paired device here: A1's real floor would replay nothing to it,
    # and this proof is about what a replay may hold, so it starts from zero.
    monkeypatch.setattr(remote, "replay_floor", lambda caller: 0)

    async def scenario():
        pc = await opened("page-pc")
        phone = await opened("", PHONE)
        hotkey = events.publish("hotkey", {"action": "toggle", "at": 1})
        other = await opened("page-b")  # a new PC page: the leader changes, 'leader' goes out
        before = events.current_id()
        private = events.publish_pc("remote", {"kind": "devices"})
        assert private == before + 1 == events.current_id()
        marker = events.publish("memory", {"n": 1})
        # The phone hears the shared event only.
        assert await frames(phone, 1) == [(marker, {"type": "memory", "n": 1})]
        # The PC page hears everything, the PC-only event included.
        heard = await until(pc, marker)
        kinds = [data["type"] for _, data in heard]
        assert {"leader", "hotkey", "remote", "memory"} <= set(kinds) and kinds.count("leader") >= 2
        assert (private, {"type": "remote", "kind": "devices"}) in heard
        # The PC-only event is never replayed, and a phone's replay holds no PC control.
        assert private not in [i for i, _ in events._replay]
        assert hotkey in [i for i, _ in events._replay]
        again = await opened("", PHONE, last_event_id="0")
        replayed = [data["type"] for _, data in await frames(again, 1)]
        assert replayed == ["memory"]
        for stream in (again, phone, other, pc):
            await stream.aclose()
    asyncio.run(scenario())


def test_a_phone_stream_never_silences_pc_toasts_holds(monkeypatch):
    notified = []
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: notified.append((a, k)) or True)

    async def scenario():
        phone = await opened("", PHONE)
        assert events.has_subscribers() is False  # no PC page: the PC must say it another way
        assert events.has_subscribers(lambda c: True) is True
        assert events.has_subscribers(lambda c: c is not None and c.remote) is True
        scheduler._fire({"id": "r1", "kind": "reminder", "title": "Pain", "text": "Sortir le pain", "due": 1}, late=0)
        await phone.aclose()
    asyncio.run(scenario())
    assert notified == [(("Rappel", "Sortir le pain"), {"kind": "reminder", "via": "pc"})]


def test_closing_the_phone_streams_never_ends_the_pc_holds():
    """A revocation closes the phone's streams only; the PC keeps its stream and
    can still open new ones (only a shutdown ends them for good)."""
    async def scenario():
        pc = await opened("page-pc")
        phone = await opened("", PHONE)
        assert events.close_streams(lambda c: c is not None and c.remote) == 1
        assert await asyncio.wait_for(_drain(phone), 2) == []
        assert not events._closing.is_set()
        marker = events.publish("memory", {"n": 2})
        assert (await until(pc, marker))[-1] == (marker, {"type": "memory", "n": 2})
        later = await opened("page-b")
        await later.aclose()
        await pc.aclose()
    asyncio.run(scenario())


async def _drain(stream) -> list:
    return [parse(chunk)[1] for chunk in [c async for c in stream] if parse(chunk)[1] is not None]

# ---------------------------------------------------------------- P1: streams per device

def test_streams_per_device_are_capped_holds():
    async def scenario():
        pc = await opened("page-pc")
        other = await opened("", OTHER_PHONE)
        streams = [await opened("", PHONE) for _ in range(events.MAX_REMOTE_STREAMS)]
        assert len(_remote_subs(PHONE.device_id)) == events.MAX_REMOTE_STREAMS == 4
        fifth = await opened("", PHONE)
        # The oldest of that device ends; the others, the other device and the PC stay.
        assert await asyncio.wait_for(_drain(streams[0]), 2) == []
        assert len(_remote_subs(PHONE.device_id)) == 4
        assert len(_remote_subs(OTHER_PHONE.device_id)) == 1
        marker = events.publish("memory", {"n": 3})
        for stream in (*streams[1:], fifth, other):
            assert await frames(stream, 1) == [(marker, {"type": "memory", "n": 3})]
        assert (await until(pc, marker))[-1] == (marker, {"type": "memory", "n": 3})
        for stream in (*streams[1:], fifth, other, pc):
            await stream.aclose()
        assert not events._subscribers
    asyncio.run(scenario())

# ================================================================ remote core (A1)

IP_B = "100.64.0.8"
TODAY = date(2026, 10, 10)
ONE_DOLLAR = {"output_tokens": 15_000, "output_token_details": {"audio_tokens": 15_000, "text_tokens": 0}}  # 0.96 $


@pytest.fixture
def pc():
    return TestClient(server.app, base_url="http://127.0.0.1:8788", headers=AUTH)


@pytest.fixture
def minted(monkeypatch):
    """A fake OpenAI: the scope of every voice session minted."""
    scopes = []

    def mint(recent="", scope="pc"):
        scopes.append(scope)
        return {"value": "ek_fake"}
    monkeypatch.setattr(realtime, "mint", mint)
    return scopes


@pytest.fixture
def clock(monkeypatch):
    """One clock for remote access, the usage ledger and the audit windows."""
    now = {"t": time.time()}
    for mod in (remote, usage, audit):
        monkeypatch.setattr(mod, "_now", lambda: now["t"])
    return now


@pytest.fixture
def alerts(monkeypatch):
    """What reached the PC (toasts, warning cards) and the alert hooks (ntfy)."""
    out = SimpleNamespace(toasts=[], warnings=[], hooks=[])
    monkeypatch.setattr(desktop, "toast", lambda title, body: out.toasts.append(body) or True)
    real = events.publish_pc

    def publish_pc(kind, data):
        if kind == "warning":
            out.warnings.append(data)
        return real(kind, data)
    monkeypatch.setattr(events, "publish_pc", publish_pc)
    monkeypatch.setattr(audit, "ALERT_HOOKS", [lambda kind, text: out.hooks.append((kind, text))])
    out.kinds = lambda: [line["alert"] for line in reversed(audit.tail(200)) if line.get("kind") == "alert"]
    return out


def with_cookie(value: str, **kw) -> TestClient:
    """A browser on the Serve port holding this device cookie."""
    client = remote_client(**kw)
    client.cookies.set(remote.COOKIE, value, domain="127.0.0.1", path="/")
    return client


def siri_client(key_id: str, secret: str, ip: str = IP, **kw) -> TestClient:
    """The Shortcut: no Origin, no cookie, the Siri key as a bearer."""
    return remote_client(ip=ip, origin=False, extra={"Authorization": f"Bearer jv_siri_{key_id}.{secret}"}, **kw)


def bare(headers: dict, cookie: str = "") -> TestClient:
    """A client on the Serve port with exactly these headers."""
    client = TestClient(server.app, base_url=f"https://127.0.0.1:{config.REMOTE_PORT}", headers=headers)
    if cookie:
        client.cookies.set(remote.COOKIE, cookie, domain="127.0.0.1", path="/")
    return client


def last_request_reason() -> str:
    return next(line.get("reason", "") for line in audit.tail(50) if line.get("kind") == "request")


def pair_phone(pc, *, ip=IP, name="iPhone de test"):
    """The real reverse pairing; returns (phone, device, device cookie value, pairing cookie value)."""
    if not remote.pairing_until():
        assert pc.post("/api/remote/pairing", json={"open": True}).status_code == 200
    phone = remote_client(ip=ip)
    asked = phone.post("/api/remote/pair-request", json={"name": name})
    assert asked.status_code == 200, asked.text
    pair_cookie = phone.cookies.get(remote.PAIR_COOKIE)
    assert pc.post(f"/api/remote/pair-requests/{asked.json()['request_id']}/allow",
                   json={"code": asked.json()["code"]}).status_code == 200
    delivered = phone.get("/api/remote/pair-status")
    assert delivered.json()["status"] == "approved", delivered.text
    value = phone.cookies.get(remote.COOKIE)
    device = devices.get(value.split(".")[0])
    return phone, device, value, pair_cookie


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

# ---------------------------------------------------------------- P0-1 and 1b: the PC's token, the PC's port


def test_pc_token_is_never_accepted_on_the_remote_path_holds(monkeypatch, minted):
    enable_remote(monkeypatch)
    events._closing.set()  # a stream that did open would end at once
    client = remote_client(token=security.TOKEN)
    checked = 0
    for path, methods in sorted(served_routes().items()):
        static = "MOUNT" in methods
        url = "/static/js/main.js" if static else re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted((methods - {"HEAD", "OPTIONS", "MOUNT"}) or {"GET"}):
            for target in (url, f"{url}?token={security.TOKEN}"):
                r = client.request(method, target)
                assert security.TOKEN not in r.text, (method, target)
                if path in ("/", "/healthz") or static:  # open to anyone on the tailnet: nothing in them
                    assert r.status_code == 200, (method, path)
                else:
                    assert r.status_code in (401, 403, 409), (method, path, r.status_code)
                checked += 1
    assert checked > 100 and minted == []
    # A paired iPhone presenting the PC's token instead of its own: refused too.
    phone, _, _ = paired_client(monkeypatch)
    del phone.headers["X-Jarvis-Token"]
    for target, headers in (("/api/config", {"X-Jarvis-Token": security.TOKEN}),
                            (f"/api/config?token={security.TOKEN}", {}),
                            (f"/api/events?token={security.TOKEN}", {}),
                            ("/api/events", {"X-Jarvis-Token": security.TOKEN})):
        r = phone.get(target, headers=headers)
        assert r.status_code == 401 and r.json()["detail"] == remote.T_PAGE_KEY, target
    assert security.TOKEN not in phone.get("/").text


def test_proxy_headers_on_the_pc_port_are_refused_holds(monkeypatch):
    phone, _, token = paired_client(monkeypatch)
    cookie = phone.cookies.get(remote.COOKIE)
    on_pc_port = TestClient(server.app, base_url="http://127.0.0.1:8788")
    serve = {**remote_headers(token=token), "Cookie": f"{remote.COOKIE}={cookie}"}
    for headers in (serve, {**AUTH, "Via": "1.1 antivirus"}, {**AUTH, "X-Forwarded-For": "127.0.0.1"},
                    {**AUTH, "Forwarded": "for=127.0.0.1"}, {**AUTH, "X-Real-IP": "127.0.0.1"},
                    {**AUTH, "Tailscale-User-Login": LOGIN}):
        for path in ("/", "/api/config"):
            r = on_pc_port.get(path, headers=headers)
            assert r.status_code == 403, (headers, path)
            assert security.TOKEN not in r.text and token not in r.text
            if path == "/api/config":
                assert r.json()["detail"] == remote.T_PROXY
    assert last_request_reason() == "proxy_on_pc_port"
    # The very same request on the Serve port is the paired iPhone.
    assert phone.get("/api/config").status_code == 200

# ---------------------------------------------------------------- P0-3: exactly what this PC's Serve sends


def test_remote_host_is_exact_and_never_a_wildcard_holds(monkeypatch):
    phone, _, _ = paired_client(monkeypatch)
    for host in ("evil.example", f"{REMOTE_HOST}:8443", f"{REMOTE_HOST}:80", "nas.tail0000.ts.net",
                 f"x.{REMOTE_HOST}", f"{REMOTE_HOST}.evil.example", "tail0000.ts.net", "127.0.0.1:8789",
                 "localhost", f"{REMOTE_HOST}:443:443", "*.tail0000.ts.net"):
        r = phone.get("/api/config", headers={"Host": host})
        assert r.status_code == 403 and r.json()["detail"] == remote.T_REFUSED, host
        assert last_request_reason() == "host", host
        audit.reset_memory()
    for host in (REMOTE_HOST, f"{REMOTE_HOST}:443", REMOTE_HOST.upper()):
        assert phone.get("/api/config", headers={"Host": host}).status_code == 200, host
    # No Serve name at all: nothing matches.
    store.save(remote.REMOTE_FILE, {**store.load(remote.REMOTE_FILE, {}), "host": ""})
    assert phone.get("/api/config").status_code == 403


def test_remote_origin_must_be_the_exact_https_origin_holds(monkeypatch):
    phone, _, _ = paired_client(monkeypatch)
    for origin in ("https://nas.tail0000.ts.net", f"http://{REMOTE_HOST}", f"https://{REMOTE_HOST}:443",
                   f"https://{REMOTE_HOST}.evil.example", "null", f"https://{REMOTE_HOST}/", "https://evil.example",
                   f"https://{REMOTE_HOST.upper()}"):
        r = phone.post("/api/presence", headers={"Origin": origin}, json={"client": "page-1"})
        assert r.status_code == 403 and r.json()["detail"] == remote.T_REFUSED, origin
        assert last_request_reason() == "origin"
        audit.reset_memory()
    # No Origin (a plain GET) is fine, and so is the exact one.
    del phone.headers["Origin"]
    assert phone.get("/api/config").status_code == 200
    assert phone.get("/api/config", headers={"Origin": f"https://{REMOTE_HOST}"}).status_code == 200
    # Sec-Fetch-Site: another site, even a sibling *.ts.net page, is refused; the page mints nothing for it.
    before = len(remote._tokens)
    for site in ("cross-site", "same-site"):
        r = phone.get("/api/config", headers={"Sec-Fetch-Site": site})
        assert r.status_code == 403 and r.json()["detail"] == remote.T_REFUSED
        assert last_request_reason() == "site"
        page = phone.get("/", headers={"Sec-Fetch-Site": site, "Sec-Fetch-Dest": "document"})
        assert page.status_code == 403 and page_token(page.text) == "" and 'content="refused"' in page.text
    assert len(remote._tokens) == before
    for site in ("same-origin", "none"):
        assert phone.get("/api/config", headers={"Sec-Fetch-Site": site}).status_code == 200


def test_forwarded_proto_and_tailnet_address_are_required_holds(monkeypatch):
    # This PC's own tailnet addresses (Serve's WhoIs could answer for them).
    monkeypatch.setattr(tailscale, "self_info", lambda: {"installed": True, "running": True,
                                                         "ips": ["100.100.100.100", "fd7a:115c:a1e0::1"]})
    phone, _, token = paired_client(monkeypatch)
    cookie = phone.cookies.get(remote.COOKIE)
    for proto in ("http", "", "HTTPS", "https,https", "wss"):
        audit.reset_memory()
        r = phone.get("/api/config", headers={"X-Forwarded-Proto": proto})
        assert r.status_code == 403 and r.json()["detail"] == remote.T_REFUSED, proto
        assert last_request_reason() == "proto"
    for xff, reason in ((None, "xff"), ("", "xff"), ("8.8.8.8", "xff"), ("192.168.1.10", "xff"),
                        (f"{IP}, 100.64.0.2", "xff"), (f"{IP},{IP}", "xff"), ("pas-une-ip", "xff"),
                        ("100.128.0.1", "xff"), ("fd7a:115c:a1e1::1", "xff"), ("100.63.255.255", "xff"),
                        ("127.0.0.1", "self"), ("::1", "self"),
                        ("100.100.100.100", "self"), ("fd7a:115c:a1e0::1", "self"), ("fd7a:115c:a1e0:0:0::1", "self")):
        headers = remote_headers(token=token)
        if xff is None:
            headers.pop("X-Forwarded-For")
        else:
            headers["X-Forwarded-For"] = xff
        audit.reset_memory()
        r = bare(headers, cookie).get("/api/config")
        assert r.status_code == 403 and r.json()["detail"] == remote.T_REFUSED, xff
        assert last_request_reason() == reason, xff
    assert phone.get("/api/config").status_code == 200
    # A device on the tailnet's IPv6 range is as good as one on IPv4.
    six, _, _ = paired_client(monkeypatch, name="IPv6", ip="fd7a:115c:a1e0::1234")
    assert six.get("/api/config").status_code == 200


def test_funnel_and_anonymous_tailnet_requests_are_refused_holds(monkeypatch, alerts):
    # Off (READY is False): a Funnel request is still refused, and the PC hears of it.
    r = remote_client(extra={"Tailscale-Funnel-Request": "?1"}).get("/api/config")
    assert r.status_code == 403 and r.json()["detail"] == remote.T_FUNNEL
    assert alerts.kinds() == ["funnel"] and alerts.hooks == [("funnel", audit.ALERTS["funnel"]["ntfy_text"])]
    phone, _, token = paired_client(monkeypatch)
    r = phone.get("/api/config", headers={"Tailscale-Funnel-Request": "1"})
    assert r.status_code == 403 and r.json()["detail"] == remote.T_FUNNEL
    page = phone.get("/", headers={"Tailscale-Funnel-Request": "1"})
    assert page.status_code == 403 and page_token(page.text) == ""
    cookie = phone.cookies.get(remote.COOKIE)
    # A tailnet node with no user (a tagged or shared node), or another account: refused.
    anonymous = remote_headers(token=token)
    anonymous.pop("Tailscale-User-Login")
    r = bare(anonymous, cookie).get("/api/config")
    assert r.status_code == 403 and r.json()["detail"] == remote.T_LOGIN
    for login in ("tagged-devices", "intrus@example.com", "", "monsieur@example.com.evil"):
        r = bare(remote_headers(token=token, login=login)).get("/api/config")
        assert r.status_code == 403 and r.json()["detail"] == remote.T_LOGIN, login
    # Another account presenting a paired device's cookie: the PC is told.
    r = bare(remote_headers(token=token, login="intrus@example.com"), cookie).get("/api/config")
    assert r.status_code == 403 and alerts.kinds()[-1] == "login_change"
    # The allowed login, RFC 2047-encoded or in capitals, is the same account.
    for login in ("=?utf-8?q?monsieur=40example.com?=", "MONSIEUR@EXAMPLE.COM", " monsieur@example.com "):
        assert bare(remote_headers(token=token, login=login), cookie).get("/api/config").status_code == 200, login

# ---------------------------------------------------------------- P0-4 and 4b: the switch


def test_remote_access_is_off_by_default_and_closed_while_off_holds(monkeypatch):
    assert remote.READY is False and remote.is_enabled() is False
    assert store.load(remote.REMOTE_FILE, None) is None
    assert remote.state_for(remote.PC)["enabled"] is False
    # No variable of .env can open it: none exists.
    config_src = (ROOT / "jarvis" / "config.py").read_text(encoding="utf-8")
    assert re.findall(r'"(JARVIS_REMOTE\w*)"', config_src) == ["JARVIS_REMOTE_HOST", "JARVIS_REMOTE_LOGINS",
                                                               "JARVIS_REMOTE_PORT"]
    for name in ("JARVIS_REMOTE", "JARVIS_REMOTE_ENABLED", "JARVIS_REMOTE_ACCESS"):
        monkeypatch.setenv(name, "1")
    assert remote.is_enabled() is False
    # READY alone opens nothing: the switch stays off until the PC turns it on.
    monkeypatch.setattr(remote, "READY", True)
    page = remote_client().get("/")
    assert page.status_code == 403 and 'content="off"' in page.text
    # Off is a 403 even with a valid cookie and its token.
    phone, _, token = paired_client(monkeypatch)
    assert phone.get("/api/config").status_code == 200
    remote.set_enabled(False)
    for path in ("/api/config", "/api/remote/state", f"/api/events?token={token}", "/healthz", "/static/js/main.js"):
        r = phone.get(path)
        assert r.status_code == 403 and r.json()["detail"] == remote.T_OFF, path
    page = phone.get("/")
    assert page.status_code == 403 and 'content="off"' in page.text and page_token(page.text) == ""
    # READY False counts as off, whatever remote.json says.
    store.save(remote.REMOTE_FILE, {**store.load(remote.REMOTE_FILE, {}), "enabled": True})
    assert phone.get("/api/config").status_code == 401  # on again: but switching off dropped its token
    monkeypatch.setattr(remote, "READY", False)
    assert phone.get("/api/config").json()["detail"] == remote.T_OFF


def test_remote_access_turns_on_only_from_the_pc_with_a_cap_holds(monkeypatch, pc):
    calls = []
    monkeypatch.setattr(remote, "READY", True)
    monkeypatch.setattr(listener, "start", lambda: calls.append("start") or
                        {"running": True, "port": config.REMOTE_PORT, "error": ""})
    monkeypatch.setattr(listener, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    phone = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip=IP, login=LOGIN)

    def refused(text, **kw):
        with pytest.raises(remote.RemoteError, match=re.escape(text)):
            remote.set_enabled(True, **kw)
        assert remote.is_enabled() is False

    refused("Seul le PC peut activer l'accès à distance.", host=REMOTE_HOST, login=LOGIN, by=phone)
    refused("Fixez d'abord un plafond de dépense par jour", host=REMOTE_HOST, login=LOGIN)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 2.0)
    for host in ("evil.example", f"{REMOTE_HOST}.evil.example", "*.tail0000.ts.net", "", "127.0.0.1",
                 "-pc.tail0000.ts.net"):
        refused("Adresse Tailscale invalide (exemple : jarvis-pc.tail0000.ts.net).", host=host, login=LOGIN)
    for login in ("", "monsieur", "a@b@c", "mon sieur@example.com", "@example.com"):
        refused("Compte Tailscale inconnu : connectez Tailscale sur ce PC.", host=REMOTE_HOST, login=login)
    monkeypatch.setattr(config, "REMOTE_PORT", config.PORT)
    refused("JARVIS_REMOTE_PORT doit différer de JARVIS_PORT.", host=REMOTE_HOST, login=LOGIN)
    monkeypatch.setattr(config, "REMOTE_PORT", 8789)
    start = listener.start
    monkeypatch.setattr(listener, "start", lambda: {"running": False, "port": 8789, "error": ""})
    refused("Port 8789 déjà utilisé : choisissez un autre JARVIS_REMOTE_PORT.", host=REMOTE_HOST, login=LOGIN)
    assert store.load(remote.REMOTE_FILE, None) is None  # no refusal saved anything
    monkeypatch.setattr(listener, "start", start)
    # From the PC page, with a cap: on (a ...@github or ...@passkey login is an account too).
    r = pc.post("/api/remote/state", json={"enabled": True, "host": REMOTE_HOST, "login": "monsieur@github"})
    assert r.status_code == 200 and remote.is_enabled() and remote.logins() == ["monsieur@github"]
    assert calls == ["start"]
    r = pc.post("/api/remote/state", json={"enabled": True, "host": REMOTE_HOST, "login": LOGIN})
    assert r.status_code == 200 and remote.logins() == [LOGIN]
    # A paired phone can neither switch it on nor off.
    phone_client, _, _ = paired_client(monkeypatch)
    for enabled in (True, False):
        r = phone_client.post("/api/remote/state", json={"enabled": enabled, "host": REMOTE_HOST, "login": LOGIN})
        assert r.status_code == 403 and r.json()["detail"] == "Réservé au PC."
    with pytest.raises(remote.RemoteError, match="Seul le PC"):
        remote.set_enabled(False, by=phone)
    assert remote.is_enabled()


def test_switching_off_cuts_remote_streams_and_the_listener_holds(monkeypatch):
    port = free_port()
    monkeypatch.setattr(config, "REMOTE_PORT", port)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    monkeypatch.setattr(remote, "READY", True)
    remote.set_enabled(True, host=REMOTE_HOST, login=LOGIN)  # the real listener
    assert listener.state() == {"running": True, "port": port, "error": ""}
    with httpx.Client(trust_env=False, timeout=5) as c:  # through the listener itself: the gate's door
        assert c.get(f"http://127.0.0.1:{port}/healthz", headers=remote_headers(origin=False)).status_code == 200
    phone_client, device, token = paired_client(monkeypatch)
    phone = remote.Caller(kind="app", device_id=device["id"], ip=IP, login=LOGIN, name="iPhone de test")

    async def scenario():
        pc_stream = await opened("page-pc")
        phone_stream = await opened("", phone)
        remote.set_enabled(False)
        assert await asyncio.wait_for(_drain(phone_stream), 2) == []
        assert not events._closing.is_set()
        marker = events.publish("memory", {"n": 1})
        assert (await until(pc_stream, marker))[-1] == (marker, {"type": "memory", "n": 1})
        await pc_stream.aclose()
    asyncio.run(scenario())
    assert listener.state()["running"] is False
    with pytest.raises(httpx.ConnectError), httpx.Client(trust_env=False, timeout=5) as c:
        c.get(f"http://127.0.0.1:{port}/healthz")
    assert remote._tokens == {}  # every page token dropped
    # On again: the phone's old token is dead, it reloads the page.
    remote.set_enabled(True)
    assert phone_client.get("/api/config").status_code == 401
    assert page_token(phone_client.get("/").text) != token
    remote.set_enabled(False)


def test_switching_off_withdraws_the_serve_configuration_holds(monkeypatch):
    enable_remote(monkeypatch)
    seen, serve = [], {"state": "ready"}
    monkeypatch.setattr(tailscale, "serve_status", lambda: {"state": serve["state"], "detail": "", "url": ""})
    monkeypatch.setattr(tailscale, "unpublish", lambda: seen.append("unpublish") or
                        {"ok": True, "state": "absent", "error": ""})
    monkeypatch.setattr(tailscale, "publish", lambda: seen.append("publish") or
                        {"ok": True, "state": "ready", "consent_url": "", "error": ""})
    remote.note_published(True)
    remote.set_enabled(False)
    assert seen == ["unpublish"]
    assert store.load(remote.REMOTE_FILE, {})["published"] is True  # kept: on restores it
    remote.set_enabled(True)
    assert seen == ["unpublish", "publish"]
    # Serve aimed elsewhere, Funnel or a TCP forward: not exactly JARVIS's target, never touched.
    for other in ("wrong_target", "funnel", "tcp", "absent", "stopped", "no_tailscale", "unknown"):
        serve["state"] = other
        remote.set_enabled(False)
        remote.set_enabled(True)
    assert seen.count("unpublish") == 1
    # Never published: never published on.
    remote.note_published(False)
    seen.clear()
    remote.set_enabled(False)
    remote.set_enabled(True)
    assert seen == []
    # A broken Tailscale never stops the switch.
    monkeypatch.setattr(tailscale, "serve_status", lambda: (_ for _ in ()).throw(OSError("tailscale")))
    remote.set_enabled(False)
    assert remote.is_enabled() is False

# ---------------------------------------------------------------- P0-5: pause


def test_the_phone_can_pause_but_never_resume_or_enable_holds(monkeypatch, pc, alerts):
    phone, device, _ = paired_client(monkeypatch)
    key_id, secret = devices.add_siri_key(device["id"])
    siri = siri_client(key_id, secret)
    for hours in (0, 2, 12, 48, -1):
        assert phone.post("/api/remote/pause", json={"hours": hours}).status_code == 400, hours
    for enabled in (True, False):
        assert phone.post("/api/remote/state", json={"enabled": enabled}).status_code == 403
    caller = remote.Caller(kind="app", device_id=device["id"], ip=IP, login=LOGIN)
    with pytest.raises(remote.RemoteError):
        remote.pause(0, by=caller)
    r = phone.post("/api/remote/pause", json={"hours": 1})
    assert r.status_code == 200 and r.json()["paused_until"] == pytest.approx(time.time() + 3600, abs=10)
    assert alerts.kinds()[-1] == "remote_paused"
    assert alerts.toasts[-1].startswith("Accès à distance mis en pause depuis l'iPhone jusqu'à ")
    # Everything remote is refused, Siri included, and the phone cannot lift it.
    r = phone.get("/api/config")
    assert r.status_code == 403 and r.json()["detail"].startswith("Accès à distance en pause jusqu'à ")
    assert phone.post("/api/remote/pause", json={"hours": 1}).status_code == 403
    r = siri.post("/api/raccourci", json={"text": "bonjour"})
    assert r.status_code == 403 and r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("Accès à distance en pause")
    page = phone.get("/")
    assert page.status_code == 403 and 'content="paused"' in page.text
    # Only the PC resumes it.
    assert pc.post("/api/remote/pause", json={"hours": 0}).json() == {"paused_until": 0}
    assert phone.get("/api/config").status_code == 200
    assert phone.post("/api/remote/pause", json={"hours": 24}).status_code == 200
    assert remote.paused_until() == pytest.approx(time.time() + 86400, abs=10)

# ---------------------------------------------------------------- P0-6: reverse pairing


def test_pairing_window_and_approval_are_pc_only_holds(monkeypatch, pc):
    assert pc.post("/api/remote/pairing", json={"open": True}).status_code == 409  # off: no window
    with pytest.raises(remote.RemoteError):
        remote.open_pairing()
    enable_remote(monkeypatch)
    unpaired = remote_client()
    r = unpaired.post("/api/remote/pair-request", json={"name": "iPhone"})
    assert r.status_code == 409 and r.json()["detail"] == remote.T_CLOSED  # the window is closed
    phone, _, _ = paired_client(monkeypatch, ip="100.64.0.7")
    for client in (unpaired, phone):
        assert client.post("/api/remote/pairing", json={"open": True}).status_code == 403
    assert remote.pairing_until() == 0
    assert pc.post("/api/remote/pairing", json={"open": True}).status_code == 200
    rid = unpaired.post("/api/remote/pair-request", json={"name": "iPhone"}).json()["request_id"]
    for client in (unpaired, phone):
        assert client.get("/api/remote/pair-requests").status_code == 403
        assert client.post(f"/api/remote/pair-requests/{rid}/allow", json={}).status_code == 403
        assert client.post(f"/api/remote/pair-requests/{rid}/deny").status_code == 403
    assert unpaired.get("/api/remote/pair-status").json()["status"] == "waiting"
    # A paired phone does not ask again.
    r = phone.post("/api/remote/pair-request", json={})
    assert r.status_code == 409 and r.json()["detail"] == remote.T_PAIRED
    assert pc.post(f"/api/remote/pair-requests/{rid}/allow").status_code == 200
    assert unpaired.get("/api/remote/pair-status").json()["status"] == "approved"


def test_device_secret_is_delivered_once_to_the_requesting_browser_holds(monkeypatch, pc):
    enable_remote(monkeypatch)
    pc.post("/api/remote/pairing", json={"open": True})
    phone = remote_client()
    asked = phone.post("/api/remote/pair-request", json={"name": "iPhone de test"}).json()
    rid = asked["request_id"]
    pair_cookie = phone.cookies.get(remote.PAIR_COOKIE)
    assert pair_cookie and pair_cookie.startswith(rid + ".")
    assert pc.post(f"/api/remote/pair-requests/{rid}/allow").status_code == 200
    assert devices.active() == []  # approved, yet no device: nobody holds its secret
    # Another browser at the same address, without the pairing cookie: nothing.
    assert remote_client().get("/api/remote/pair-status").status_code == 401
    # The right request with a wrong secret: nothing.
    forged = remote_client()
    forged.cookies.set(remote.PAIR_COOKIE, f"{rid}.{'x' * 43}", domain="127.0.0.1", path="/")
    assert forged.get("/api/remote/pair-status").status_code == 401
    # The right pairing cookie from another address: nothing.
    elsewhere = remote_client(ip="100.64.0.9")
    elsewhere.cookies.set(remote.PAIR_COOKIE, pair_cookie, domain="127.0.0.1", path="/")
    assert elsewhere.get("/api/remote/pair-status").status_code == 403
    assert devices.active() == []
    # The requesting browser: the device is created now, its cookie set, never cached.
    r = phone.get("/api/remote/pair-status")
    assert r.json() == {"status": "approved", "code": asked["code"]} and r.headers["cache-control"] == "no-store"
    cookies = r.headers.get_list("set-cookie")
    (device_cookie,) = [c for c in cookies if c.startswith(f"{remote.COOKIE}=")]
    assert f"{remote.PAIR_COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict" in cookies
    (device,) = devices.active()
    assert device_cookie.split(";")[0].split("=", 1)[1].startswith(device["id"] + ".")
    # Never again: the same pairing cookie, or the same browser, gets no secret.
    replay = remote_client()
    replay.cookies.set(remote.PAIR_COOKIE, pair_cookie, domain="127.0.0.1", path="/")
    again = replay.get("/api/remote/pair-status")
    assert again.json() == {"status": "done", "code": asked["code"]} and "set-cookie" not in again.headers
    later = phone.get("/api/remote/pair-status")
    assert later.json() == {"status": "done"} and "set-cookie" not in later.headers
    assert len(devices.active()) == 1
    assert page_token(phone.get("/").text)  # and it is a working device


def test_pairing_requests_are_rate_limited_and_expire_holds(monkeypatch, pc, clock):
    enable_remote(monkeypatch)
    pc.post("/api/remote/pairing", json={"open": True})
    phone = remote_client()
    for n in range(5):
        r = phone.post("/api/remote/pair-request", json={"name": f"iPhone {n}"})
        assert r.status_code == 200
    rid = r.json()["request_id"]
    # One waiting request per address: each one replaced the previous one.
    assert [x["name"] for x in remote.pairing_requests() if x["status"] == "waiting"] == ["iPhone 4"]
    r = phone.post("/api/remote/pair-request")
    assert r.status_code == 429 and r.json()["detail"] == remote.T_RATE
    # Three waiting in all.
    for n in (1, 2):
        assert remote_client(ip=f"100.64.0.{n}").post("/api/remote/pair-request").status_code == 200
    r = remote_client(ip="100.64.0.3").post("/api/remote/pair-request")
    assert r.status_code == 409 and r.json()["detail"] == remote.T_TOO_MANY
    # The window ends: what waits expires and leaves nothing behind.
    clock["t"] += 601
    assert phone.get("/api/remote/pair-status").json()["status"] == "expired"
    assert {x["status"] for x in remote.pairing_requests()} == {"expired"}
    assert pc.post(f"/api/remote/pair-requests/{rid}/allow").status_code == 404
    assert devices.active() == []
    # Refused requests count: the fifth locks that address out of pairing for 15 minutes.
    late = remote_client(ip="100.64.0.4")
    for _ in range(5):
        r = late.post("/api/remote/pair-request")
        assert r.status_code == 409 and r.json()["detail"] == remote.T_CLOSED
    pc.post("/api/remote/pairing", json={"open": True})
    r = late.post("/api/remote/pair-request")
    assert r.status_code == 429 and r.json()["detail"] == remote.T_LOCKED
    assert remote_client(ip="100.64.0.5").post("/api/remote/pair-request").status_code == 200  # only that one
    clock["t"] += 901
    pc.post("/api/remote/pairing", json={"open": True})
    assert late.post("/api/remote/pair-request").status_code == 200


def test_pairing_poll_for_ten_minutes_is_not_rate_limited_holds(monkeypatch, pc, clock):
    enable_remote(monkeypatch)
    pc.post("/api/remote/pairing", json={"open": True})
    phone = remote_client()
    assert phone.post("/api/remote/pair-request").status_code == 200
    statuses = []
    for _ in range(300):  # every 2 s, the whole window long
        r = phone.get("/api/remote/pair-status")
        assert r.status_code == 200, r.text
        statuses.append(r.json()["status"])
        clock["t"] += 2
    assert statuses[0] == "waiting" and statuses.count("waiting") >= 299 and set(statuses) <= {"waiting", "expired"}
    assert not [line for line in audit.tail(200) if line.get("route") == "/api/remote/pair-status"]  # not logged
    # The page and its files are never counted either.
    for _ in range(100):
        assert phone.get("/").status_code == 200
        assert phone.get("/static/js/main.js").status_code == 200

# ---------------------------------------------------------------- P0-7: the secret and its cookie


def test_device_secret_is_stored_hashed_and_never_returned_or_logged_holds(monkeypatch, pc, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    enable_remote(monkeypatch)
    phone, device, value, pair_cookie = pair_phone(pc)
    secret, pair_secret = value.split(".", 1)[1], pair_cookie.split(".", 1)[1]
    digest = hashlib.sha256(secret.encode()).hexdigest()
    token = page_token(phone.get("/").text)
    phone.headers["X-Jarvis-Token"] = token
    stored = (config.DATA_DIR / devices.DEVICES_FILE).read_text(encoding="utf-8")
    assert secret not in stored and digest in stored and pair_secret not in stored
    bodies = [pc.get(path).text for path in ("/api/remote/devices", "/api/remote/state", "/api/remote/pair-requests",
                                              "/api/remote/audit?limit=200")]
    bodies.append(pc.patch(f"/api/remote/devices/{device['id']}", json={"name": "Cuisine"}).text)
    bodies += [phone.get(path).text for path in ("/api/remote/state", "/api/config", "/")]
    bodies.append(phone.get("/api/remote/pair-status").text)
    files = [p.read_text(encoding="utf-8", errors="replace") for p in config.DATA_DIR.rglob("*")
             if p.is_file() and p.name != devices.DEVICES_FILE and not p.name.startswith(devices.DEVICES_FILE)]
    out, err = capfd.readouterr()
    logged = caplog.text + "".join(str(rec.args) for rec in caplog.records) + out + err
    for text in (*bodies, *files, logged):
        for needle in (secret, digest, pair_secret, value):
            assert needle not in text
    audit_text = (config.DATA_DIR / "remote-audit.jsonl").read_text(encoding="utf-8")
    assert token not in audit_text and token not in logged


def test_device_cookie_is_host_prefixed_httponly_secure_strict_holds(monkeypatch, pc):
    enable_remote(monkeypatch)
    pc.post("/api/remote/pairing", json={"open": True})
    phone = remote_client()
    asked = phone.post("/api/remote/pair-request")
    pc.post(f"/api/remote/pair-requests/{asked.json()['request_id']}/allow")
    delivered = phone.get("/api/remote/pair-status")
    refreshed = phone.get("/")

    def parts(header):
        name_value, *attrs = [p.strip() for p in header.split(";")]
        return name_value, set(attrs)

    (pair_header,) = asked.headers.get_list("set-cookie")
    name_value, attrs = parts(pair_header)
    assert name_value.startswith(f"{remote.PAIR_COOKIE}=r_")
    assert attrs == {"Path=/", "Secure", "HttpOnly", "SameSite=Strict", "Max-Age=600"}
    for response in (delivered, refreshed):
        (header,) = [c for c in response.headers.get_list("set-cookie") if c.startswith(f"{remote.COOKIE}=d_")]
        name_value, attrs = parts(header)
        assert attrs == {"Path=/", "Secure", "HttpOnly", "SameSite=Strict", "Max-Age=34560000"}
        assert not any(a.lower().startswith("domain") for a in attrs)
    assert remote.COOKIE == "__Host-jarvis" and remote.COOKIE_SAMESITE == "Strict"
    stale = with_cookie("d_0000000000000000." + "x" * 43)
    cleared = stale.get("/").headers.get_list("set-cookie")
    assert cleared == ["__Host-jarvis=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict"]

# ---------------------------------------------------------------- P0-8: bound to its node and login


def test_device_cookie_from_another_address_or_login_is_refused_holds(monkeypatch, alerts):
    enable_remote(monkeypatch, logins=(LOGIN, "autre@example.com"))
    device, secret = devices.add("iPhone de test", ip=IP, login=LOGIN, node_id="nNODE1", ips=(IP,))
    nodes = {"fd7a:115c:a1e0::1234": {"node_id": "nNODE1", "addresses": [IP, "fd7a:115c:a1e0::1234"]},
             "100.64.0.77": {"node_id": "nOTHER", "addresses": ["100.64.0.77"]}}
    monkeypatch.setattr(tailscale, "whois", lambda ip: nodes.get(ip, {}))
    value = f"{device['id']}.{secret}"
    home = with_cookie(value)
    token = page_token(home.get("/").text)
    auth = {"X-Jarvis-Token": token}
    assert home.get("/api/config", headers=auth).status_code == 200
    # Another Tailscale account at the same address: refused, alerted.
    r = with_cookie(value, login="autre@example.com").get("/api/config", headers=auth)
    assert r.status_code == 403 and r.json()["detail"] == remote.T_MOVED
    assert alerts.kinds()[-1] == "login_change"
    # An address whois cannot place: refused, alerted, the device kept.
    r = with_cookie(value, ip="100.64.0.9").get("/api/config", headers=auth)
    assert r.status_code == 403 and r.json()["detail"] == remote.T_MOVED
    assert alerts.kinds()[-1] == "ip_change" and not devices.is_revoked(device["id"])
    # The node's other address (whois: the same node): learnt and admitted.
    assert with_cookie(value, ip="fd7a:115c:a1e0::1234").get("/api/config", headers=auth).status_code == 200
    assert devices.get(device["id"])["ips"] == [IP, "fd7a:115c:a1e0::1234"]
    # Another machine holding the secret: the device is revoked at once.
    r = with_cookie(value, ip="100.64.0.77").get("/api/config", headers=auth)
    assert r.status_code == 403 and r.json()["detail"] == remote.T_MOVED
    assert alerts.kinds()[-1] == "secret_copied" and devices.is_revoked(device["id"])
    r = home.get("/api/config", headers=auth)
    assert r.status_code == 401 and r.json()["detail"] == remote.T_REVOKED
    # The PC heard every one, through the hooks with their fixed sentences only.
    assert [k for k, _ in alerts.hooks] == ["login_change", "ip_change", "secret_copied"]
    assert all(text == audit.ALERTS[k]["ntfy_text"] for k, text in alerts.hooks)

# ---------------------------------------------------------------- P0-9 and 10: cookie plus its page token


def test_remote_api_needs_both_cookie_and_its_own_page_token_holds(monkeypatch, clock):
    a, _, token_a = paired_client(monkeypatch, name="A")
    _, _, token_b = paired_client(monkeypatch, name="B", ip=IP_B)
    del a.headers["X-Jarvis-Token"]
    r = a.get("/api/config")
    assert r.status_code == 401 and r.json()["detail"] == remote.T_PAGE_KEY  # the cookie alone
    assert a.get("/api/config", headers={"X-Jarvis-Token": token_b}).status_code == 401  # another device's
    assert a.get("/api/config", headers={"X-Jarvis-Token": token_a + "x"}).status_code == 401
    assert a.get(f"/api/config?token={token_a}").status_code == 401  # a query token only for the stream
    no_cookie = remote_client(token=token_a)
    r = no_cookie.get("/api/config")
    assert r.status_code == 401 and r.json()["detail"] == remote.T_UNPAIRED  # the token alone
    assert a.get("/api/config", headers={"X-Jarvis-Token": token_a}).status_code == 200
    events._closing.set()  # an accepted stream ends at once instead of streaming forever
    assert a.get(f"/api/events?token={token_a}").status_code == 200
    assert a.get(f"/api/events?token={token_b}").status_code == 401
    assert no_cookie.get(f"/api/events?token={token_a}").status_code == 401
    # Twelve hours idle and a token is gone; in use, it slides.
    clock["t"] += 11 * 3600
    assert a.get("/api/config", headers={"X-Jarvis-Token": token_a}).status_code == 200
    clock["t"] += 11 * 3600
    assert a.get("/api/config", headers={"X-Jarvis-Token": token_a}).status_code == 200
    remote.keepalive(remote.Caller(kind="app", device_id=a.cookies.get(remote.COOKIE).split(".")[0]))
    clock["t"] += 12 * 3600 + 1
    assert a.get("/api/config", headers={"X-Jarvis-Token": token_a}).status_code == 401
    # Each page load mints one, at most 8 per device: the oldest goes.
    tokens = [page_token(a.get("/").text) for _ in range(9)]
    assert a.get("/api/config", headers={"X-Jarvis-Token": tokens[-1]}).status_code == 200
    assert a.get("/api/config", headers={"X-Jarvis-Token": tokens[0]}).status_code == 401


def test_unpaired_remote_page_holds_no_token_holds(monkeypatch, pc):
    enable_remote(monkeypatch)
    unpaired = remote_client()
    page = unpaired.get("/")
    assert page.status_code == 200 and 'content="closed"' in page.text
    assert security.TOKEN not in page.text and page_token(page.text) == "" and remote._tokens == {}
    pc.post("/api/remote/pairing", json={"open": True})
    page = unpaired.get("/")
    assert 'content="pair"' in page.text and security.TOKEN not in page.text and remote._tokens == {}
    # A paired device's page loaded by another site, or embedded: refused, nothing minted.
    phone, _, token = paired_client(monkeypatch)
    count = len(remote._tokens)
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                    {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-origin"},
                    {"Sec-Fetch-Dest": "image"}, {"Sec-Fetch-Dest": "script", "Sec-Fetch-Site": "none"},
                    {"Sec-Fetch-Dest": "embed"}):
        r = phone.get("/", headers=headers)
        assert r.status_code == 403 and page_token(r.text) == "" and token not in r.text, headers
        assert 'content="refused"' in r.text and security.TOKEN not in r.text
    assert len(remote._tokens) == count
    # A top-level load (what Safari sends, or nothing at all) does get one.
    for headers in ({"Sec-Fetch-Site": "none", "Sec-Fetch-Dest": "document"}, {"Sec-Fetch-Site": "same-origin"}, {}):
        assert page_token(phone.get("/", headers=headers).text)

# ---------------------------------------------------------------- P0-11: revocation


def test_revoked_device_is_refused_at_once_and_its_streams_and_requests_end_holds(monkeypatch, pc):
    phone, device, _ = paired_client(monkeypatch)
    value = phone.cookies.get(remote.COOKIE)
    keys = [devices.add_siri_key(device["id"]) for _ in range(2)]
    origins, forgotten, cancelled = [], [], []
    monkeypatch.setattr(confirm, "cancel_for_origin", lambda origin: origins.append(origin) or 1)
    monkeypatch.setattr(raccourci, "forget_device", lambda device_id: forgotten.append(device_id))
    running = [{"id": "t-pc", "via": "pc"}, {"id": "t-app", "via": f"app:{device['id']}"},
               {"id": "t-siri", "via": f"siri:{keys[1][0]}"}, {"id": "t-autre", "via": "app:d_fedcba9876543210"}]
    monkeypatch.setattr(tasks, "running", lambda: running)
    monkeypatch.setattr(tasks, "cancel", lambda task_id: cancelled.append(task_id) or {"ok": True})
    caller = remote.Caller(kind="app", device_id=device["id"], ip=IP, login=LOGIN, name="iPhone de test")

    async def scenario():
        pc_stream = await opened("page-pc")
        phone_stream = await opened("", caller)
        r = pc.delete(f"/api/remote/devices/{device['id']}")
        assert r.json() == {"ok": True, "cancelled_tasks": 2, "topic_renewed": False}  # no topic yet
        assert await asyncio.wait_for(_drain(phone_stream), 2) == []
        marker = events.publish("memory", {"n": 1})
        assert (await until(pc_stream, marker))[-1] == (marker, {"type": "memory", "n": 1})
        await pc_stream.aclose()
    asyncio.run(scenario())
    assert origins == [f"app:{device['id']}", f"siri:{keys[0][0]}", f"siri:{keys[1][0]}"]
    assert forgotten == [device["id"]]
    assert cancelled == ["t-app", "t-siri"]
    assert not [t for t in remote._tokens.values() if t["device"] == device["id"]]
    assert all(devices.siri_key_revoked(k) for k, _ in keys)
    assert {device["id"], keys[0][0], keys[1][0]} <= set(store.load(devices.DEVICES_FILE, {})["revoked_ids"])
    r = phone.get("/api/config")
    assert r.status_code == 401 and r.json()["detail"] == remote.T_REVOKED
    assert "__Host-jarvis=; Max-Age=0" in r.headers["set-cookie"]
    page = with_cookie(value).get("/")
    assert page.status_code == 401 and 'content="revoked"' in page.text
    r = siri_client(*keys[0]).post("/api/raccourci", json={"text": "bonjour"})
    assert r.status_code == 401 and r.text == remote.T_SIRI_REFUSED
    assert not remote.origin_active(f"app:{device['id']}") and not remote.origin_active(f"siri:{keys[0][0]}")
    # Never counted toward a lock, and tombstoned for good, even once the record is pruned.
    for _ in range(10):
        assert with_cookie(value).get("/api/config").status_code == 401
    monkeypatch.setattr(devices, "_now", lambda: time.time() + 31 * 86400)
    assert devices.get(device["id"]) is None
    r = with_cookie(value).get("/api/config")
    assert r.status_code == 401 and r.json()["detail"] == remote.T_REVOKED

def test_a_removed_device_loses_the_ntfy_topic_too_holds(monkeypatch, pc, alerts):
    """The ntfy topic is the only key to the notifications and every paired
    app reads it to subscribe: removing a device from the PC (lost, stolen)
    or a copied secret renews it. A phone forgetting itself never does (only
    the PC renews the topic: it would cut the other phones off)."""
    from jarvis import notify
    phone, device, _ = paired_client(monkeypatch)
    first = phone.get("/api/notify").json()["topic"]
    assert first and notify.topic(create=False) == first
    r = pc.delete(f"/api/remote/devices/{device['id']}")
    assert r.json() == {"ok": True, "cancelled_tasks": 0, "topic_renewed": True}
    second = notify.topic(create=False)
    assert second and second != first
    # Its own forgetting: the topic stays (the other phones keep hearing it).
    other, _device2, _ = paired_client(monkeypatch, name="iPad de test")
    assert other.get("/api/notify").json()["topic"] == second
    assert other.post("/api/remote/forget").json() == {"ok": True}
    assert notify.topic(create=False) == second
    # A secret used from another machine: the device goes, and the topic with it.
    nodes = {"100.64.0.77": {"node_id": "nOTHER", "addresses": ["100.64.0.77"]}}
    monkeypatch.setattr(tailscale, "whois", lambda ip: nodes.get(ip, {}))
    copied, secret = devices.add("iPhone 2", ip=IP, login=LOGIN, node_id="nNODE1", ips=(IP,))
    r = with_cookie(f"{copied['id']}.{secret}", ip="100.64.0.77").get("/api/config")
    assert r.status_code == 403 and alerts.kinds()[-1] == "secret_copied" and devices.is_revoked(copied["id"])
    third = notify.topic(create=False)
    assert third and third not in (first, second)
    # Never created by a removal: no topic, nothing renewed.
    (config.DATA_DIR / notify.NTFY_FILE).unlink()
    _phone4, device4, _ = paired_client(monkeypatch, name="iPhone 4")
    assert pc.delete(f"/api/remote/devices/{device4['id']}").json()["topic_renewed"] is False
    assert notify.topic(create=False) == ""

# ---------------------------------------------------------------- P0-12: the route table


def test_every_route_is_pc_only_unless_listed_for_remote_holds(monkeypatch, minted):
    routes = served_routes()
    # The table names only real routes, with their real methods.
    for methods, template, _ in remote.REMOTE_ALLOW:
        served = routes.get("/static" if template == "/static/{path:path}" else template)
        assert served is not None, template
        assert "MOUNT" in served or set(methods) <= served, template
    allowed = {(m, t) for methods, t, scopes in remote.REMOTE_ALLOW if "app" in scopes for m in methods}
    phone, device, _ = paired_client(monkeypatch)
    unpaired = remote_client()
    events._closing.set()  # GET /api/events would otherwise stream forever
    checked = 0
    for path, methods in sorted(routes.items()):
        if "MOUNT" in methods:
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            if (method, path) == ("POST", "/api/remote/forget"):
                continue  # it would forget this very phone (test_remote_api covers it)
            r = phone.request(method, url)
            if path == "/api/raccourci":  # Siri's: the phone's cookie and token do not open it
                assert r.status_code == 401 and r.text == remote.T_SIRI_UNKNOWN
                continue
            pc_only = r.status_code == 403 and r.json() == {"detail": "Réservé au PC."}
            assert pc_only == ((method, path) not in allowed), (method, path, r.status_code)
            if (method, path) in allowed and path not in ("/", "/healthz") and "open" not in \
                    next(s for m, t, s in remote.REMOTE_ALLOW if t == path and method in m):
                assert unpaired.request(method, url).json()["detail"] == remote.T_UNPAIRED, (method, path)
            checked += 1
    assert checked > 60
    for method, url in (("GET", "/api/remote/state/"), ("GET", "/api/%72emote/devices"), ("HEAD", "/api/shutdown"),
                        ("POST", "/api/shutdown"), ("GET", "/api/health"), ("POST", "/api/settings/openai-key"),
                        ("POST", "/api/task/x/reveal"), ("DELETE", "/api/journal"), ("POST", "/api/onboarding"),
                        ("POST", "/api/notify/topic"), ("POST", "/api/remote/devices/x/siri-key"),
                        ("DELETE", "/api/remote/siri-keys/x"), ("GET", "//api/config"), ("GET", "/api/config/"),
                        ("OPTIONS", "/api/config"), ("GET", "/docs"), ("GET", "/api/nope")):
        assert phone.request(method, url).status_code == 403, (method, url)
    assert devices.get(device["id"])["revoked"] is False


def test_siri_key_reaches_only_the_raccourci_endpoint_holds(monkeypatch, clock):
    phone, device, _ = paired_client(monkeypatch)
    key_id, secret = devices.add_siri_key(device["id"])
    siri = siri_client(key_id, secret)
    events._closing.set()
    for path, methods in sorted(served_routes().items()):
        if path in ("/api/raccourci", "/", "/healthz") or "MOUNT" in methods:
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            r = siri.request(method, url)
            assert r.status_code in (401, 403, 409), (method, path, r.status_code)
    assert page_token(siri.get("/").text) == ""
    # The one route a key opens; the gate let it through (whatever the endpoint answers).
    r = siri.post("/api/raccourci", json={"text": "bonjour"})
    assert r.status_code not in (401, 403, 429), r.text
    # The iPhone's page credentials do not open it.
    r = phone.post("/api/raccourci", json={"text": "bonjour"})
    assert r.status_code == 401 and r.text == remote.T_SIRI_UNKNOWN
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    # Unknown, malformed, revoked, wrong: refused in plain French.
    for header, text in ((f"Bearer jv_siri_k_{'0' * 16}.{secret}", remote.T_SIRI_UNKNOWN),
                         (f"Bearer {secret}", remote.T_SIRI_UNKNOWN), ("Basic eDp5", remote.T_SIRI_UNKNOWN),
                         (f"Bearer jv_siri_{key_id}.{'x' * 43}", remote.T_SIRI_REFUSED)):
        r = remote_client(origin=False, extra={"Authorization": header}).post("/api/raccourci", json={"text": "x"})
        assert r.status_code == 401 and r.text == text, header
    # Its device's node and login: another address or account is refused.
    assert siri_client(key_id, secret, ip="100.64.0.9").post("/api/raccourci", json={}).status_code == 403
    assert siri_client(key_id, secret, login="autre@example.com").post("/api/raccourci", json={}).status_code == 403
    # Six a minute.
    remote.reset_memory()
    for _ in range(6):
        assert siri.post("/api/raccourci", json={"text": "x"}).status_code not in (401, 403, 429)
    r = siri.post("/api/raccourci", json={"text": "x"})
    assert r.status_code == 429 and r.text == remote.T_RATE
    # Sixty a day.
    for _ in range(9):
        clock["t"] += 61
        for _ in range(6):
            assert siri.post("/api/raccourci", json={"text": "x"}).status_code != 429
    clock["t"] += 61
    assert siri.post("/api/raccourci", json={"text": "x"}).status_code == 429
    second, other_secret = devices.add_siri_key(device["id"])
    devices.revoke_siri_key(second)
    r = siri_client(second, other_secret).post("/api/raccourci", json={})
    assert r.status_code == 401 and r.text == remote.T_SIRI_REFUSED

# ---------------------------------------------------------------- P0-13: settings from the phone


def test_remote_only_harmless_settings_change_from_the_phone_holds(monkeypatch):
    phone, _, _ = paired_client(monkeypatch)
    watched = ("VOICE", "CITY", "PERMISSION_MODE", "WORKDIR", "MCP_CONFIG", "DAILY_BUDGET_USD", "HOTKEY",
               "REALTIME_MODEL", "TASK_BUDGET_USD", "WAKE_WORD")
    for attr in watched:
        monkeypatch.setattr(config, attr, getattr(config, attr))
    before = {attr: getattr(config, attr) for attr in watched}
    body = phone.get("/api/settings").json()
    assert set(body["values"]) <= remote.REMOTE_SETTINGS and {e["key"] for e in body["schema"]} <= remote.REMOTE_SETTINGS
    assert {s["id"] for s in body["sections"]} <= set(remote.REMOTE_SECTIONS)
    assert body["key"] == {"present": True, "masked": ""} and body["data_dir"] == "" and body["autostart"] is None
    for change in ({"permission_mode": "bypassPermissions", "confirm": True}, {"workdir": "C:\\", "confirm": True},
                   {"mcp_config": "", "confirm": True}, {"daily_budget_usd": 1000, "confirm": True},
                   {"daily_budget_usd": 1000}, {"permission_mode": "auto"}, {"hotkey": "ctrl+j"},
                   {"realtime_model": "gpt-realtime-mini"}, {"task_budget_usd": 50}, {"wake_word": False},
                   {"voice": "cedar", "confirm": True}, {"voice": "cedar", "workdir": "C:\\"}, {"inconnu": 1}):
        r = phone.put("/api/settings", json=change)
        assert r.status_code == 403 and r.json()["detail"] == "Réglage modifiable sur le PC seulement.", change
    assert {attr: getattr(config, attr) for attr in watched} == before
    assert store.load(settings.SETTINGS_FILE, {}) == {}
    r = phone.put("/api/settings", json={"voice": "cedar", "city": "Lyon"})
    assert r.status_code == 200 and config.VOICE == "cedar" and config.CITY == "Lyon"
    assert set(r.json()["values"]) <= remote.REMOTE_SETTINGS
    for path, body in (("/api/settings/openai-key", {"key": "sk-" + "x" * 20, "confirm": True}),
                       ("/api/autostart", {"on": True}), ("/api/settings/open-data", {})):
        assert phone.post(path, json=body).status_code == 403

# ---------------------------------------------------------------- P0-14: lockouts


def test_auth_failures_lock_only_the_failing_credential_holds(monkeypatch, alerts):
    phone, device, _ = paired_client(monkeypatch)
    unknown = "d_0000000000000000." + "x" * 43
    # An unknown cookie (a pairing the PC no longer knows): cleared, never counted.
    r = with_cookie(unknown).get("/")
    assert r.status_code == 200 and 'content="closed"' in r.text
    assert r.headers.get_list("set-cookie") == [remote.cookie_header("")]
    for _ in range(30):
        assert with_cookie(unknown).get("/static/js/main.js").status_code == 200
    for _ in range(10):
        assert with_cookie(unknown).get("/").status_code == 200
        assert with_cookie(unknown).get("/api/config").status_code == 401
    assert remote._locks == {} and phone.get("/api/config").status_code == 200
    # Ten unknown Siri keys from the iPhone's own address: only the Siri route closes.
    for n in range(10):
        r = siri_client(f"k_{n:016x}", "x" * 43).post("/api/raccourci", json={"text": "x"})
    assert r.status_code == 429 and r.text == remote.T_LOCKED
    assert phone.get("/api/config").status_code == 200 and page_token(phone.get("/").text)
    # Five wrong secrets for device A from device B's address: A is not locked.
    b, _, _ = paired_client(monkeypatch, name="B", ip=IP_B)
    for _ in range(5):
        assert with_cookie(f"{device['id']}.{'y' * 43}", ip=IP_B).get("/api/config").status_code == 401
    assert phone.get("/api/config").status_code == 200
    assert alerts.kinds()[-1] == "auth_failures"
    assert b.get("/api/config").status_code == 429  # the address that tried is the one shut out
    # Five wrong secrets for A from A's own address lock A there, and nothing else.
    c, _, _ = paired_client(monkeypatch, name="C")
    for _ in range(5):
        with_cookie(f"{device['id']}.{'z' * 43}").get("/api/config")
    r = phone.get("/api/config")
    assert r.status_code == 429 and r.json()["detail"] == remote.T_LOCKED
    page = phone.get("/")
    assert page.status_code == 429 and 'content="locked"' in page.text
    assert c.get("/api/config").status_code == 200  # another device at the same address
    # A revoked device never counts.
    pc = TestClient(server.app, base_url="http://127.0.0.1:8788", headers=AUTH)
    value = c.cookies.get(remote.COOKIE)
    pc.delete(f"/api/remote/devices/{value.split('.')[0]}")
    for _ in range(10):
        assert with_cookie(value).get("/api/config").status_code == 401
    assert with_cookie(value).get("/api/config").json()["detail"] == remote.T_REVOKED

# ---------------------------------------------------------------- P0-15 and 16: what the phone's voice costs


def test_remote_voice_needs_a_daily_cap_and_respects_it_holds(monkeypatch, minted, pc):
    phone, _, _ = paired_client(monkeypatch)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    r = phone.post("/api/session", json={})
    assert r.status_code == 403 and r.json()["detail"] == remote.T_NO_CAP and minted == []
    assert pc.post("/api/session", json={}).status_code == 200  # the PC needs no cap
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 2.0)
    r = phone.post("/api/session", json={})
    assert r.status_code == 200 and minted == ["pc", "app"]
    assert set(r.json()) == {"client_secret", "model", "session_id"}
    usage.add_claude(2.0)
    r = phone.post("/api/session", json={})
    assert r.status_code == 403 and r.json()["detail"] == remote.T_CAPPED and minted == ["pc", "app"]


def test_remote_voice_minting_is_rate_limited_per_device_holds(monkeypatch, minted, clock):
    monkeypatch.setattr(usage, "_today", lambda: TODAY)
    phone, device, _ = paired_client(monkeypatch)
    other, _, _ = paired_client(monkeypatch, name="Autre", ip=IP_B)
    for _ in range(12):
        assert phone.post("/api/session", json={}).status_code == 200
    r = phone.post("/api/session", json={})
    assert r.status_code == 429 and r.json()["detail"] == remote.T_MINTS
    assert other.post("/api/session", json={}).status_code == 200  # per device
    # A failed mint (OpenAI down) is not counted.
    clock["t"] += 601
    working = realtime.mint

    def down(recent="", scope="pc"):
        raise realtime.MintError(502, "OpenAI injoignable.")
    monkeypatch.setattr(realtime, "mint", down)
    for _ in range(15):
        assert phone.post("/api/session", json={}).status_code == 502
    monkeypatch.setattr(realtime, "mint", working)
    assert remote.mints_today(device["id"]) == 12
    # 40 a day, kept in usage.json: a restart forgets the 10-minute window, not the day.
    for batch in (12, 12, 4):
        for _ in range(batch):
            assert phone.post("/api/session", json={}).status_code == 200
        clock["t"] += 601
    remote.reset_memory()
    phone.headers["X-Jarvis-Token"] = page_token(phone.get("/").text)
    r = phone.post("/api/session", json={})
    assert r.status_code == 429 and r.json()["detail"] == remote.T_MINTS
    assert len(store.load(usage.FILE, {})[TODAY.isoformat()]["remote"][device["id"]]["mints"]) == 40
    assert remote.check_voice(remote.PC) is None


def test_remote_usage_reports_are_bounded_holds(monkeypatch, minted, clock, pc):
    monkeypatch.setattr(usage, "_today", lambda: TODAY)
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1")
    phone, device, _ = paired_client(monkeypatch)
    assert phone.post("/api/session", json={}).status_code == 200  # one voice session
    for _ in range(10):  # ten posts of about 1 $ within one minute
        clock["t"] += 6
        r = phone.post("/api/usage", json={"usage": ONE_DOLLAR, "model": "gpt-realtime-2.1"})
        assert r.status_code == 200
    spent = usage.realtime_spent_today()
    assert 0 < spent <= 0.30 + 1e-6
    entry = store.load(usage.FILE, {})[TODAY.isoformat()]["remote"][device["id"]]
    assert entry["usd"] == pytest.approx(spent, abs=1e-6) and len(entry["mints"]) == 1
    assert any(line.get("kind") == "usage" and line.get("reason") == "clamped" for line in audit.tail(50))
    # A restart (memory cleared) grants nothing new: it is all in usage.json.
    remote.reset_memory()
    phone.headers["X-Jarvis-Token"] = page_token(phone.get("/").text)
    assert phone.post("/api/usage", json={"usage": ONE_DOLLAR, "model": "gpt-realtime-2.1"}).status_code == 200
    assert usage.realtime_spent_today() <= 0.30 + 1e-6
    # More than 1 $ in one post is refused outright.
    too_much = {"output_tokens": 20_000, "output_token_details": {"audio_tokens": 20_000, "text_tokens": 0}}
    r = phone.post("/api/usage", json={"usage": too_much, "model": "gpt-realtime-2.1"})
    assert r.status_code == 400 and r.json()["detail"] == "Relevé de consommation invalide."
    # Time grants more, 0.30 $ a minute at most per session.
    clock["t"] += 60
    phone.post("/api/usage", json={"usage": ONE_DOLLAR, "model": "gpt-realtime-2.1"})
    assert usage.realtime_spent_today() <= 0.60 + 1e-6
    # The PC's own reports are recorded in full, as before.
    before = usage.realtime_spent_today()
    pc.post("/api/usage", json={"usage": ONE_DOLLAR, "model": "gpt-realtime-2.1"})
    assert usage.realtime_spent_today() == pytest.approx(before + 0.96, abs=1e-6)

# ---------------------------------------------------------------- P0-17, 18, 18b: audit, alerts, headers


def test_every_remote_request_is_audited_without_secrets_holds(monkeypatch, pc):
    enable_remote(monkeypatch)
    phone, device, value, pair_cookie = pair_phone(pc)
    token = page_token(phone.get("/").text)
    phone.headers["X-Jarvis-Token"] = token
    assert phone.get("/api/config?secret=VALEUR-SECRETE&token=" + token).status_code == 200
    assert phone.get("/api/shutdown").status_code == 403
    assert phone.get("/api/nope/123").status_code == 403
    assert phone.get("/static/js/main.js").status_code == 200
    assert phone.get("/favicon.ico").status_code == 404
    assert phone.get("/apple-touch-icon.png").status_code == 404
    requests = [line for line in audit.tail(200) if line["kind"] == "request"]
    config_line = next(line for line in requests if line["route"] == "/api/config")
    assert config_line == {**config_line, "caller": "app", "device": device["id"], "ip": IP, "login": LOGIN,
                           "method": "GET", "status": 200}
    assert any(line["route"] == "/api/shutdown" and line["status"] == 403 and line["reason"] == "scope"
               for line in requests)
    assert any(line["route"] == "?" and line["status"] == 403 for line in requests)
    assert not [line for line in requests if line["route"] in ("/static/{path:path}", "/api/remote/pair-status")]
    assert not [line for line in requests if line.get("status") == 404]  # the iOS icon probes
    raw = "".join((config.DATA_DIR / name).read_text(encoding="utf-8")
                  for name in ("remote-audit.jsonl", "remote-alerts.jsonl"))
    secret = value.split(".", 1)[1]
    for needle in (secret, hashlib.sha256(secret.encode()).hexdigest(), pair_cookie.split(".", 1)[1], token,
                   "VALEUR-SECRETE", "?", security.TOKEN, "__Host"):
        if needle == "?":
            assert not re.search(r'"route": "[^"?]*\?[^"]+"', raw)  # never a query string
        else:
            assert needle not in raw, needle
    # Alerts are kept apart, whatever floods the trail.
    alerts_file = [json.loads(t) for t in (config.DATA_DIR / "remote-alerts.jsonl").read_text("utf-8").splitlines()]
    assert {"pair_request", "new_device"} <= {a["alert"] for a in alerts_file}
    # Refusals are throttled per address: 20 lines, then a count.
    noisy = remote_client(ip="100.64.0.66")
    for _ in range(30):
        assert noisy.get("/api/config").status_code == 401
    assert len([line for line in audit.tail(200) if line.get("ip") == "100.64.0.66"]) == 20


def test_pc_is_alerted_of_sensitive_remote_events_holds(monkeypatch, pc, alerts):
    monkeypatch.setattr(remote, "READY", True)
    monkeypatch.setattr(listener, "start", lambda: {"running": True, "port": config.REMOTE_PORT, "error": ""})
    monkeypatch.setattr(listener, "stop", lambda: None)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    nodes = {IP: {"node_id": "nNODE1", "addresses": [IP], "os": "iOS"},
             "100.64.0.77": {"node_id": "nOTHER", "addresses": ["100.64.0.77"]}}
    monkeypatch.setattr(tailscale, "whois", lambda ip: nodes.get(ip, {}))
    assert pc.post("/api/remote/state", json={"enabled": True, "host": REMOTE_HOST, "login": LOGIN}).status_code == 200
    _, device, value, _ = pair_phone(pc)
    remote_client(extra={"Tailscale-Funnel-Request": "?1"}).get("/")
    for _ in range(5):
        with_cookie(f"{device['id']}.{'w' * 43}", ip=IP).get("/api/config")
    for _ in range(2):  # every full-access launch from the phone, never deduplicated (A2 raises it)
        audit.alert("remote_complet", "Tâche avec accès complet lancée depuis l'iPhone : « Ranger ».",
                    remote.Caller(kind="app", device_id=device["id"], ip=IP, login=LOGIN))
    with_cookie(value, login="intrus@example.com").get("/api/config")
    with_cookie(value, ip="100.64.0.9").get("/")
    with_cookie(value, ip="100.64.0.77").get("/")
    pc.post("/api/remote/complet", json={"duration": "7d"})
    pc.post("/api/remote/state", json={"enabled": False})
    kinds = alerts.kinds()
    for kind in ("remote_on", "pair_request", "new_device", "funnel", "auth_failures", "login_change", "ip_change",
                 "secret_copied", "complet_optin", "remote_off"):
        assert kind in kinds, kind
    assert kinds.count("remote_complet") == 2
    # Each one reached the PC: a toast and a warning card rendered as text.
    assert len(alerts.toasts) == len(alerts.warnings) == len(kinds)
    assert all(w["kind"] == "remote" and w["plain"] is True for w in alerts.warnings)
    assert any("100.101.102.103" in t for t in alerts.toasts)
    # The hooks (ntfy) got the fixed sentences only: no name, no address, no account.
    hooked = [k for k, _ in alerts.hooks]
    assert hooked.count("remote_complet") == 2 and "pair_request" not in hooked
    assert set(hooked) == {k for k in kinds if audit.ALERTS[k]["ntfy"]}
    for kind, text in alerts.hooks:
        assert text == audit.ALERTS[kind]["ntfy_text"]
        assert "iPhone de test" not in text and IP not in text and "@" not in text


def test_remote_responses_carry_csp_and_safe_headers_holds(monkeypatch):
    assert remote.CSP == (
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self' https://api.openai.com; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'; object-src 'none'; manifest-src 'self'")

    def check(r, no_store):
        assert r.headers["content-security-policy"] == remote.CSP, r.url
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
        if no_store:
            assert r.headers["cache-control"] == "no-store", r.url

    off = remote_client()
    check(off.get("/"), True)
    check(off.get("/api/config"), True)
    check(off.get("/favicon.ico"), False)
    check(TestClient(server.app).get("/api/config", headers={"Via": "1.1 proxy"}), True)  # the PC port
    phone, device, _ = paired_client(monkeypatch)
    check(phone.get("/"), True)
    check(phone.get("/api/config"), True)
    check(phone.get("/api/shutdown"), True)
    check(phone.post("/api/remote/pause", json={"hours": 5}), True)
    check(remote_client().get("/api/config"), True)
    static = phone.get("/static/js/main.js")
    check(static, False)
    assert static.headers["cache-control"] == "no-cache"
    check(phone.get("/healthz"), False)
    quiet = phone.get("/apple-touch-icon.png")
    check(quiet, False)
    assert quiet.status_code == 404
    key_id, _ = devices.add_siri_key(device["id"])
    check(siri_client(key_id, "x" * 43).post("/api/raccourci", json={}), True)
    # The PC's own page is untouched: no CSP added on the local path.
    assert "content-security-policy" not in TestClient(server.app, base_url="http://127.0.0.1:8788").get("/").headers

# ---------------------------------------------------------------- P0-18c: the full-access opt-in


def test_complet_optin_is_pc_only_holds(monkeypatch, pc, alerts):
    phone, _, _ = paired_client(monkeypatch)
    for duration in ("24h", "7d"):
        r = phone.post("/api/remote/complet", json={"duration": duration})
        assert r.status_code == 403 and r.json()["detail"] == "Réservé au PC."
    assert remote.complet_allowed() is False and remote.complet_until() == 0
    assert "complet_optin" not in alerts.kinds()
    assert list(inspect.signature(remote.set_complet).parameters) == ["duration"]  # no caller can be passed
    r = pc.post("/api/remote/complet", json={"duration": "24h"})
    assert r.status_code == 200 and remote.complet_allowed()
    assert alerts.kinds()[-1] == "complet_optin"
    assert alerts.hooks[-1] == ("complet_optin", "accès complet depuis l'iPhone autorisé sur le PC.")
    assert alerts.toasts[-1].startswith("Accès complet depuis l'iPhone autorisé jusqu'au ")
    assert phone.get("/api/remote/state").json()["complet_until"] == pytest.approx(time.time() + 86400, abs=10)
    assert pc.post("/api/remote/complet", json={"duration": "never"}).json() == {"complet_until": 0}
    assert remote.complet_allowed() is False and alerts.kinds().count("complet_optin") == 1


def test_complet_optin_expires_by_itself_holds(monkeypatch, clock):
    enable_remote(monkeypatch)
    start = clock["t"]
    assert remote.set_complet("24h") == start + 86400 and remote.complet_allowed()
    clock["t"] = start + 86400 - 1
    assert remote.complet_allowed()
    clock["t"] = start + 86400
    assert not remote.complet_allowed() and remote.complet_until() == 0
    assert remote.complet_allowed(now=start + 10)
    clock["t"] = start
    remote.set_complet("7d")
    clock["t"] = start + 7 * 86400 - 60
    assert remote.complet_allowed()
    clock["t"] = start + 7 * 86400 + 1
    assert not remote.complet_allowed()
    remote.set_complet("never")
    assert not remote.complet_allowed(now=start)
    with pytest.raises(remote.RemoteError):
        remote.set_complet("toujours")

# ---------------------------------------------------------------- P1: counts


def test_device_and_key_counts_are_capped_holds(monkeypatch, pc):
    enable_remote(monkeypatch)
    assert devices.MAX_DEVICES == 5 and devices.MAX_SIRI_KEYS == 2
    made = [devices.add(f"iPhone {n}", ip=f"100.64.0.{n + 1}", login=LOGIN)[0] for n in range(5)]
    with pytest.raises(ValueError):
        devices.add("Sixième", ip="100.64.0.40", login=LOGIN)
    # A sixth phone may ask, but the PC cannot allow it.
    pc.post("/api/remote/pairing", json={"open": True})
    rid = remote_client(ip="100.64.0.50").post("/api/remote/pair-request").json()["request_id"]
    r = pc.post(f"/api/remote/pair-requests/{rid}/allow")
    assert r.status_code == 409 and r.json()["detail"] == remote.T_LIMIT
    # An approved request holds its place: four devices and one approved make five.
    pc.delete(f"/api/remote/devices/{made[0]['id']}")
    assert pc.post(f"/api/remote/pair-requests/{rid}/allow").status_code == 200
    rid2 = remote_client(ip="100.64.0.51").post("/api/remote/pair-request").json()["request_id"]
    assert pc.post(f"/api/remote/pair-requests/{rid2}/allow").status_code == 409
    # Two Siri keys per device.
    devices.add_siri_key(made[1]["id"])
    devices.add_siri_key(made[1]["id"])
    with pytest.raises(ValueError):
        devices.add_siri_key(made[1]["id"])

# ---------------------------------------------------------------- wave A verification


def _alert_file_lines() -> list:
    out = []
    for name in ("remote-alerts.1.jsonl", "remote-alerts.jsonl"):
        path = config.DATA_DIR / name
        if path.exists():
            out += [json.loads(t) for t in path.read_text("utf-8").splitlines()]
    return out


def test_forged_funnel_requests_never_flood_the_pc_with_alerts_holds(monkeypatch, alerts, clock):
    """A DNS-rebinding page in the PC's browser may add Tailscale-Funnel-Request and
    any X-Forwarded-For, never a *.ts.net Host: on either port it is refused without
    an alert. What Serve does bring from Funnel alerts once per window, whatever
    address it claims; no table grows and the trail keeps a count, not a line per address."""
    enable_remote(monkeypatch)
    for base_url, host in (("http://127.0.0.1:8788", "evil.example:8788"),
                           (f"http://127.0.0.1:{config.REMOTE_PORT}", f"evil.example:{config.REMOTE_PORT}")):
        local = TestClient(server.app, base_url=base_url)
        for n in range(50):
            r = local.get("/", headers={"Host": host, "Tailscale-Funnel-Request": "1",
                                        "X-Forwarded-For": f"203.0.113.{n}"})
            assert r.status_code == 403 and remote.T_FUNNEL in r.text
    # The PC's own port never alerts, not even with this PC's Serve name.
    TestClient(server.app, base_url="http://127.0.0.1:8788").get("/", headers={
        "Host": REMOTE_HOST, "Tailscale-Funnel-Request": "1"})
    assert alerts.toasts == [] and alerts.hooks == [] and alerts.warnings == []
    for n in range(50):  # Funnel misconfigured: internet clients rotating their addresses
        r = remote_client(ip=f"2001:db8::{n + 1:x}", extra={"Tailscale-Funnel-Request": "?1"}).get("/")
        assert r.status_code == 403
    assert alerts.hooks == [("funnel", audit.ALERTS["funnel"]["ntfy_text"])] and len(alerts.toasts) == 1
    clock["t"] += 601
    remote_client(ip="198.51.100.1", extra={"Tailscale-Funnel-Request": "?1"}).get("/api/config")
    assert len(alerts.hooks) == len(alerts.toasts) == 2
    assert len(audit._last_alert) == 1 and len(audit._refusals) <= 2
    requests = [line for line in audit.tail(500) if line["kind"] == "request"]
    assert len(requests) <= 2 * audit.THROTTLE_MAX + 2


def test_a_flood_of_repeated_alerts_never_pushes_an_earlier_one_out_holds(monkeypatch, alerts, clock):
    """Deduplicated repeats are counted, not written: 4000 of them leave the pairing
    alert in remote-alerts.jsonl, then one count line before the next alert of a kind."""
    phone = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip=IP, login=LOGIN, name="iPhone de test")
    audit.alert("new_device", "Nouvel appareil associé : « iPhone de test » (100.101.102.103).", phone)
    for n in range(2000):
        audit.alert("funnel", "Requête refusée venant d'internet (Funnel) : vérifiez Tailscale.",
                    remote.Caller(kind="unpaired", ip=f"2001:db8::{n + 1:x}"))
        audit.alert("login_change", "Compte Tailscale inattendu pour « iPhone de test » : refusé.",
                    remote.Caller(kind="unpaired", ip=IP, login="intrus@example.com"), device=phone.device_id)
    assert [a["alert"] for a in _alert_file_lines()] == ["new_device", "funnel", "login_change"]
    assert len(alerts.toasts) == len(alerts.warnings) == 3
    clock["t"] += 601
    audit.alert("funnel", "Requête refusée venant d'internet (Funnel) : vérifiez Tailscale.")
    kept = _alert_file_lines()
    assert kept[-2] == {"t": kept[-2]["t"], "kind": "alert", "alert": "funnel", "reason": "throttled", "count": 1999,
                        "text": audit.REPEATS_TEXT}
    assert kept[-1]["alert"] == "funnel" and kept[0]["alert"] == "new_device"
    # Through the real gate: a refused account presenting a paired cookie, over and over.
    enable_remote(monkeypatch)
    device, secret = devices.add("iPhone de test", ip=IP, login=LOGIN, ips=(IP,))
    intruder = with_cookie(f"{device['id']}.{secret}", login="intrus@example.com")
    for _ in range(600):
        assert intruder.get("/api/config").status_code == 403
    assert [a["alert"] for a in _alert_file_lines()].count("login_change") == 2
    assert _alert_file_lines()[0]["alert"] == "new_device"


def test_concurrent_voice_mints_respect_the_per_device_limit_holds(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    phone, device, _ = paired_client(monkeypatch)
    minted = []

    def slow(recent="", scope="pc"):
        time.sleep(0.3)  # OpenAI takes its time: every request is in flight at once
        minted.append(scope)
        return {"value": "ek_fake"}
    monkeypatch.setattr(realtime, "mint", slow)

    def burst(n):
        with ThreadPoolExecutor(n) as pool:
            return list(pool.map(lambda _: phone.post("/api/session", json={}).status_code, range(n)))
    codes = burst(30)
    assert codes.count(200) == len(minted) == remote.MINTS_PER_WINDOW and codes.count(429) == 30 - 12
    assert remote.mints_today(device["id"]) == remote.MINTS_PER_WINDOW
    # 40 a day: a burst at 39 gets one more, not one per request in flight.
    remote.reset_memory()  # the 10-minute window forgotten (a restart), the day kept
    for _ in range(27):
        usage.note_remote_mint(device["id"], time.time() - 3600)
    phone.headers["X-Jarvis-Token"] = page_token(phone.get("/").text)
    codes = burst(10)
    assert codes.count(200) == 1 and remote.mints_today(device["id"]) == remote.MINTS_PER_DAY
    # A failed mint frees its slot at once.
    remote.reset_memory()
    store.save(usage.FILE, {})
    phone.headers["X-Jarvis-Token"] = page_token(phone.get("/").text)

    def down(recent="", scope="pc"):
        raise realtime.MintError(502, "OpenAI injoignable.")
    monkeypatch.setattr(realtime, "mint", down)
    assert [phone.post("/api/session", json={}).status_code for _ in range(20)] == [502] * 20
    assert remote._reserved == {}


def test_a_snoozed_phone_reminder_stays_the_phones_holds(monkeypatch):
    shown = []
    monkeypatch.setattr(desktop, "toast", lambda title, body: shown.append((title, body)))
    monkeypatch.setattr(desktop, "show_app_window", lambda url: shown.append(("show", url)))
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    phone, device, _ = paired_client(monkeypatch)
    origin = f"app:{device['id']}"
    r = phone.post("/api/schedules", json={"kind": "reminder", "title": "Sortir le pain", "text": "Sortir le pain",
                                           "delay_minutes": 1})
    item = r.json()["item"]
    assert r.status_code == 200 and item["via"] == origin
    scheduler.tick(now=item["due"] + 1)
    assert [i["via"] for i in inbox.pending(via=None)] == [origin] and shown == []
    # « +10 min » on the phone: the copy is still the phone's...
    r = phone.post(f"/api/schedules/{item['id']}/snooze", json={"minutes": 10})
    copy = r.json()["item"]
    assert r.status_code == 200 and copy["via"] == origin
    # ...so when it goes off, the PC neither toasts it nor keeps it for its own page.
    scheduler.tick(now=copy["due"] + 1)
    assert [i["via"] for i in inbox.pending(via=None)] == [origin, origin]
    assert inbox.pending(via="pc") == [] and shown == []
    # Snoozing it again moves that same copy, still the phone's.
    r = phone.post(f"/api/schedules/{copy['id']}/snooze", json={"minutes": 10})
    assert r.json()["item"]["via"] == origin


def test_one_waiting_pairing_request_per_address_even_when_concurrent_holds(monkeypatch, pc):
    from concurrent.futures import ThreadPoolExecutor
    enable_remote(monkeypatch)
    monkeypatch.setattr(tailscale, "whois", lambda ip: time.sleep(0.5) or {})  # whois runs outside the lock
    assert pc.post("/api/remote/pairing", json={"open": True}).status_code == 200
    node = remote_client(ip="100.64.0.42")
    with ThreadPoolExecutor(3) as pool:
        codes = list(pool.map(lambda n: node.post("/api/remote/pair-request", json={"name": f"Faux {n}"}).status_code,
                              range(3)))
    assert codes == [200, 200, 200]
    assert [r["ip"] for r in remote.pairing_requests() if r["status"] == "waiting"] == ["100.64.0.42"]
    # The real iPhone still finds a free place.
    assert remote_client().post("/api/remote/pair-request", json={"name": "iPhone"}).status_code == 200


def test_changing_the_allowed_account_or_name_ends_the_old_sessions_holds(monkeypatch):
    phone_client, device, _ = paired_client(monkeypatch)
    phone = remote.Caller(kind="app", device_id=device["id"], ip=IP, login=LOGIN, name="iPhone de test")

    async def scenario(change, cut):
        pc_stream = await opened("page-pc")
        phone_stream = await opened("", phone)
        change()
        marker = events.publish("task", {"id": "t1", "title": "Secret", "prompt": "Données privées"})
        if cut:
            assert await asyncio.wait_for(_drain(phone_stream), 2) == []
        else:
            assert (await until(phone_stream, marker))[-1][0] == marker
            await phone_stream.aclose()
        assert (await until(pc_stream, marker))[-1][0] == marker
        await pc_stream.aclose()
    # Switched on again with the same name and account: nothing ends.
    asyncio.run(scenario(lambda: remote.set_enabled(True, host=REMOTE_HOST, login=LOGIN), cut=False))
    assert phone_client.get("/api/config").status_code == 200
    # Another account: the old one's stream ends at once and its page tokens die.
    asyncio.run(scenario(lambda: remote.set_enabled(True, host=REMOTE_HOST, login="autre@example.com"), cut=True))
    assert remote._tokens == {}
    # Back to this account, then another Serve name: the same.
    remote.set_enabled(True, host=REMOTE_HOST, login=LOGIN)
    phone_client.headers["X-Jarvis-Token"] = page_token(phone_client.get("/").text)
    asyncio.run(scenario(lambda: remote.set_enabled(True, host="autre-pc.tail0000.ts.net"), cut=True))
    assert remote._tokens == {}


def test_equal_pc_and_serve_ports_never_make_the_pc_page_remote_holds(monkeypatch):
    """JARVIS_PORT=8789 left over in .env: the PC's page stays the PC's, a proxy
    header there is still refused, and the Serve listener never takes that port."""
    monkeypatch.setattr(config, "PORT", 8789)
    monkeypatch.setattr(config, "REMOTE_PORT", 8789)
    out = listener.start()
    assert out == {"running": False, "port": 8789, "error": "JARVIS_REMOTE_PORT doit différer de JARVIS_PORT."}
    local = TestClient(server.app, base_url="http://127.0.0.1:8789")
    page = local.get("/")
    assert page.status_code == 200 and page_token(page.text) == security.TOKEN
    assert local.get("/api/config", headers=AUTH).json()["remote"] is False
    enable_remote(monkeypatch)
    r = remote_client(token="x").get("/api/config")
    assert r.status_code == 403 and r.json()["detail"] == remote.T_PROXY


def test_an_impossible_serve_port_is_refused_in_french_holds(monkeypatch):
    monkeypatch.setattr(remote, "READY", True)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    for port in (87890, 65536, 0, -1):
        monkeypatch.setattr(config, "REMOTE_PORT", port)
        out = listener.start()
        assert out["running"] is False and out["error"] == remote.T_BAD_PORT, port
        with pytest.raises(remote.RemoteError, match=re.escape(remote.T_BAD_PORT)):
            remote.set_enabled(True, host=REMOTE_HOST, login=LOGIN)
        assert remote.is_enabled() is False
        # A switch left on with such a port never stops JARVIS from starting.
        store.save(remote.REMOTE_FILE, {"enabled": True, "host": REMOTE_HOST, "logins": [LOGIN]})
        listener.start_if_enabled()
        assert listener.state()["running"] is False
        store.save(remote.REMOTE_FILE, {})


def test_a_refused_page_explains_itself_without_its_files_holds(monkeypatch):
    """Steps 1, 2 and 6 to 8 refuse pair.html's own script and styles too: the page
    they answer with carries its sentence itself, with nothing to load."""
    def plain(r, text):
        assert r.status_code == 403 and r.headers["content-type"].startswith("text/html"), r.text[:200]
        assert text in r.text and "<script" not in r.text and "<link" not in r.text
        assert r.headers["content-security-policy"] == remote.CSP
    on_pc_port = TestClient(server.app, base_url="http://127.0.0.1:8788")
    for headers in ({"Via": "1.1 antivirus"}, {"X-Forwarded-For": "127.0.0.1"}, {"Forwarded": "for=127.0.0.1"}):
        plain(on_pc_port.get("/", headers={**AUTH, **headers}), remote.T_PROXY)
        assert on_pc_port.get("/static/js/pair.js", headers=headers).status_code == 403
    plain(remote_client(extra={"Tailscale-Funnel-Request": "?1"}).get("/"), remote.T_FUNNEL)
    enable_remote(monkeypatch)
    for kw in ({"host": "ancien-pc.tail0000.ts.net"}, {"ip": "8.8.8.8"}, {"extra": {"X-Forwarded-Proto": "http"}}):
        plain(remote_client(**kw).get("/"), remote.T_ADDRESS)
        r = remote_client(**kw).get("/api/config")
        assert r.status_code == 403 and r.json()["detail"] == remote.T_REFUSED
