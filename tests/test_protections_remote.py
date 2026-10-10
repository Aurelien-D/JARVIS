"""Remote access protections (spec section 8), as far as the scaffold goes:

- P0-1: a proxy header or the Serve port makes a request remote, whatever its
  Host; the remote path never hands out nor accepts the PC's page token (the
  stub gate refuses every remote request).
- P0-2: every listener binds the literal 127.0.0.1 and trusts no forwarded header.
- P0-2b: remote access cannot be switched on before remote.READY.
- P0-25: a phone's event stream never takes part in the election, never hears
  the PC's controls and never silences the PC's toasts; P1: at most 4 streams
  per device.
"""
import ast
import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from remote_helpers import IP, LOGIN, REMOTE_HOST, remote_client
from test_security import served_routes

import server
from jarvis import config, events, inbox, listener, remote, scheduler, security, store

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


def test_remote_stream_drops_pc_control_events_holds():
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
