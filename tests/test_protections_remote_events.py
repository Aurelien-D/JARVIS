"""Remote access protections for the event stream and the inbox (spec 4.12,
section 8 rows 25-27 and the P1 replay row):

- P0-25: a phone's presence and claims never touch the PC's election.
- P0-26: a fresh phone stream replays nothing; a reconnecting one never
  replays from before its pairing (remote.replay_floor); PC-only events
  (publish_pc, leader, hotkey) never reach a phone's stream, live or replayed.
- P0-27: the inbox is split by origin: each device reads and acknowledges only
  its own messages (the PC may acknowledge any), and the PC never toasts a
  phone's results.
- P1: a phone that reconnects never hears an event older than ten minutes.

Fictitious devices only: the repository is public.
"""
import asyncio
import contextlib
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from remote_helpers import IP, LOGIN, as_caller, remote_client
from starlette.requests import Request

import server
from jarvis import config, desktop, events, inbox, remote, security, store

AUTH = {"X-Jarvis-Token": security.TOKEN}
PHONE = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip=IP, login=LOGIN, name="iPhone de test")
OTHER_PHONE = remote.Caller(kind="app", device_id="d_fedcba9876543210", ip=IP, login=LOGIN, name="iPhone 2")
SIRI = remote.Caller(kind="siri", device_id=PHONE.device_id, key_id="k_0123456789abcdef", ip=IP, login=LOGIN)


@pytest.fixture(autouse=True)
def fresh_events():
    """No page and no event left over from another test."""
    def clear():
        events._replay.clear()
        events._replay_at.clear()
        events._clients.clear()
        events._leader = None
        events._leader_live = False
        events._closing.clear()
        inbox._toasted.clear()
    clear()
    yield
    clear()


@pytest.fixture
def windows(monkeypatch):
    """desktop calls recorded instead of made; no quiet hours, nothing full screen."""
    calls = []
    monkeypatch.setattr(desktop, "toast", lambda title, body: calls.append(("toast", title, body)))
    monkeypatch.setattr(desktop, "show_app_window", lambda url: calls.append(("show", url)))
    monkeypatch.setattr(desktop, "attention_state", lambda: "ok")
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    return calls


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


@contextlib.contextmanager
def published_ago(monkeypatch, seconds: float):
    """Events published inside this block are stamped `seconds` in the past."""
    real = time.monotonic
    with monkeypatch.context() as m:
        m.setattr(events, "time", SimpleNamespace(time=time.time, monotonic=lambda: real() - seconds))
        yield

# ---------------------------------------------------------------- P0-25: presence and claims

def test_remote_presence_and_claims_are_ignored_holds(monkeypatch):
    """The phone posts presence, focus, a live session and a claim, under the PC
    page's own id or its own: the election never hears of it, and the phone is
    told it leads nothing."""
    calls = []
    real_presence = events.presence
    monkeypatch.setattr(events, "presence", lambda *a, **k: calls.append((a, k)) or real_presence(*a, **k))
    monkeypatch.setattr(desktop, "attention_state", lambda: "fullscreen")  # the PC is busy, not the phone

    async def scenario():
        pc = await opened("page-pc")
        assert events.leader() == "page-pc"
        before = {cid: p.rank() for cid, p in events._clients.items()}
        marker = events.current_id()
        as_caller(monkeypatch, PHONE)
        phone = remote_client()
        for body in ({"client": "page-pc", "claim": True, "focused": True, "live": True},
                     {"client": "page-iphone", "claim": True, "focused": True, "live": True},
                     {"client": "page-pc", "focused": False, "live": False}):
            r = phone.post("/api/presence", json=body)
            assert r.status_code == 200 and r.json() == {"leader": None, "live": False, "remote": True}, body
        # Nothing reached the election: same pages, same ranks, same leader, no 'leader' event.
        assert calls == []
        assert {cid: p.rank() for cid, p in events._clients.items()} == before
        assert events.leader_info() == {"client": "page-pc", "live": False}
        assert events.current_id() == marker
        # /api/delivery: the phone leads nothing and the PC's full screen is not its business.
        state = phone.get("/api/delivery").json()
        assert state["leader"] is None and state["attention"] == "ok"
        # The PC page's own presence still counts, and sees its busy screen.
        pc_client = TestClient(server.app, base_url="http://127.0.0.1:8788")
        r = pc_client.post("/api/presence", headers=AUTH, json={"client": "page-pc", "focused": True})
        assert r.json() == {"leader": "page-pc", "live": False} and len(calls) == 1
        state = pc_client.get("/api/delivery", headers=AUTH).json()
        assert state["leader"] == "page-pc" and state["attention"] == "fullscreen"
        await pc.aclose()
    asyncio.run(scenario())

# ---------------------------------------------------------------- P0-26: replay

def test_remote_fresh_stream_replays_nothing_holds(monkeypatch):
    """A phone that opens its page (no Last-Event-ID) hears only what comes next,
    exactly like a fresh PC page: not everything since its pairing."""
    asked = []
    monkeypatch.setattr(remote, "replay_floor", lambda caller: asked.append(caller) or 0)  # paired long ago

    async def scenario():
        old = [events.publish("task", {"id": f"t{n}", "title": "Météo", "status": "done", "via": PHONE.origin})
               for n in range(3)]
        assert old == sorted(old)
        for last in (None, ""):
            phone = await opened("", PHONE, last_event_id=last)
            marker = events.publish("memory", {"n": last})
            assert await frames(phone, 1) == [(marker, {"type": "memory", "n": last})]
            await phone.aclose()
        assert asked == []  # nothing to replay: the floor isn't even needed
        # Through the HTTP route, as the iPhone opens it.
        scope = {"type": "http", "method": "GET", "path": "/api/events", "query_string": b"", "headers": [],
                 "state": {"caller": PHONE}}
        response = await server.stream_events(Request(scope), client="page-pc")
        stream = response.body_iterator
        assert (await anext(stream)).startswith("retry:")
        marker = events.publish("memory", {"n": "http"})
        assert await frames(stream, 1) == [(marker, {"type": "memory", "n": "http"})]
        await stream.aclose()
        # A fresh PC page: the same (only who speaks, then what comes next).
        pc = await opened("page-pc")
        assert (await frames(pc, 1))[0] == (None, {"type": "leader", "client": "page-pc", "live": False})
        await pc.aclose()
    asyncio.run(scenario())


def test_remote_replay_never_goes_before_pairing_holds(monkeypatch):
    """A reconnecting phone replays from its pairing at the earliest, whatever
    Last-Event-ID it sends; an unknown or broken floor replays nothing; the PC's
    replay is untouched."""
    async def scenario():
        before = [events.publish("memory", {"n": n}) for n in range(3)]  # before the pairing
        paired = events.current_id()
        after = [events.publish("memory", {"n": n}) for n in range(3, 5)]
        floors = {PHONE.device_id: paired}
        asked = []

        def floor(caller):
            asked.append(caller)
            if caller.device_id not in floors:
                raise RuntimeError("appareil inconnu")
            return floors[caller.device_id]

        monkeypatch.setattr(remote, "replay_floor", floor)
        for last in ("0", str(before[0]), str(before[-1]), str(paired)):
            phone = await opened("", PHONE, last_event_id=last)
            assert [i for i, _ in await frames(phone, 2)] == after, last
            await phone.aclose()
        assert asked == [PHONE] * 4
        # Sent after the pairing: replayed from there as usual.
        phone = await opened("", PHONE, last_event_id=str(after[0]))
        assert [i for i, _ in await frames(phone, 1)] == after[1:]
        await phone.aclose()
        # A floor that can't be known (here it raises): nothing replayed, new events still flow.
        other = await opened("", OTHER_PHONE, last_event_id="0")
        marker = events.publish("memory", {"n": "new"})
        assert await frames(other, 1) == [(marker, {"type": "memory", "n": "new"})]
        await other.aclose()
        # A floor from the future (a clock set back): nothing replayed, and nothing new is dropped.
        floors[PHONE.device_id] = events.current_id() + 10**9
        phone = await opened("", PHONE, last_event_id="0")
        marker = events.publish("memory", {"n": "après"})
        assert await frames(phone, 1) == [(marker, {"type": "memory", "n": "après"})]
        await phone.aclose()
        # The PC page replays what it missed, before any pairing included.
        asked.clear()
        pc = await opened("page-pc", remote.PC, last_event_id=str(before[0]))
        replayed = [i for i, _ in await frames(pc, 4)]
        assert replayed == before[1:] + after[:2] and asked == []
        await pc.aclose()
    asyncio.run(scenario())


def test_pc_only_events_never_reach_a_remote_stream_holds(monkeypatch):
    """Pairing news, remote alerts, the election and the hotkey stay on the PC:
    never live on a phone stream, never in its replay, never in its inbox."""
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: False)
    monkeypatch.setattr(remote, "replay_floor", lambda caller: 0)  # paired before all of it

    async def scenario():
        start = events.current_id()
        pc = await opened("page-pc")
        phone = await opened("", PHONE)
        private = [
            events.publish_pc("remote", {"kind": "pair_request", "request": {"code": "4821"}}),
            events.publish_pc("warning", {"kind": "remote", "plain": True,
                                          "text": "Demande d'association : code 4821."}),
        ]
        hotkey = events.publish("hotkey", {"action": "toggle", "at": 1})
        second = await opened("page-b")  # a new PC page: 'leader' goes out
        marker = events.publish("memory", {"n": 1})
        # Live: the phone hears the shared event only.
        assert await frames(phone, 1) == [(marker, {"type": "memory", "n": 1})]
        heard = {data["type"] for _, data in await until(pc, marker)}
        assert {"remote", "warning", "hotkey", "leader", "memory"} <= heard
        # Replayed: the PC-only events were never buffered, the PC's controls are skipped.
        buffered = [i for i, _ in events._replay]
        assert not set(private) & set(buffered) and hotkey in buffered
        again = await opened("", PHONE, last_event_id=str(start))
        later = events.publish("memory", {"n": 2})
        got = await until(again, later)
        assert [data["type"] for _, data in got] == ["memory", "memory"]
        assert [i for i, _ in got] == [marker, later]
        # The inbox keeps the alert for the PC, never for the phone.
        assert [i["payload"]["text"] for i in inbox.pending()] == ["Demande d'association : code 4821."]
        assert inbox.pending(via=PHONE.origin) == []
        for stream in (again, second, phone, pc):
            await stream.aclose()
    asyncio.run(scenario())

# ---------------------------------------------------------------- P0-27: the inbox by origin

def test_inbox_is_split_by_origin_holds(monkeypatch, windows):
    old_pc = inbox.add("reminder", {"id": "r-ancien", "text": "Sortir le pain"})  # an older file: no via
    with store.LOCK:
        items = store.load(inbox.FILE, [])
        for item in items:
            item.pop("via", None)
        store.save(inbox.FILE, items)
    pc_item = inbox.add("reminder", {"id": "r-pc", "text": "Arroser", "via": "pc"})
    mine = inbox.add("task", {"id": "t-tel", "title": "Météo", "status": "done", "via": PHONE.origin})
    other = inbox.add("task", {"id": "t-autre", "title": "Trajet", "status": "done", "via": OTHER_PHONE.origin})
    siri = inbox.add("task", {"id": "t-siri", "title": "Recherche", "status": "done", "via": SIRI.origin})
    def ids(items):
        return [i["id"] for i in items]

    assert ids(inbox.pending()) == [old_pc["id"], pc_item["id"]]  # missing via = the PC's
    assert ids(inbox.pending(via="pc")) == ids(inbox.pending(via=""))
    assert ids(inbox.pending(via=PHONE.origin)) == [mine["id"]]
    assert ids(inbox.pending(via=SIRI.origin)) == [siri["id"]]
    assert ids(inbox.pending(via=None)) == [old_pc["id"], pc_item["id"], mine["id"], other["id"], siri["id"]]

    # Over HTTP: each device reads only its own messages.
    pc = TestClient(server.app, base_url="http://127.0.0.1:8788")
    assert ids(pc.get("/api/inbox", headers=AUTH).json()) == [old_pc["id"], pc_item["id"]]
    as_caller(monkeypatch, PHONE)
    phone = remote_client()
    assert ids(phone.get("/api/inbox").json()) == [mine["id"]]
    # The phone acknowledges its own only: another origin's message is not found.
    for item in (old_pc, pc_item, other, siri):
        assert phone.post(f"/api/inbox/{item['id']}/ack").status_code == 404
    assert len(inbox.pending(via=None)) == 5
    assert phone.post(f"/api/inbox/{mine['id']}/ack").json() == {"ok": True}
    assert inbox.pending(via=PHONE.origin) == []
    # The PC may acknowledge any message (it sees them all in its task panel).
    assert pc.post(f"/api/inbox/{other['id']}/ack", headers=AUTH).json() == {"ok": True}
    assert ids(inbox.pending(via=None)) == [old_pc["id"], pc_item["id"], siri["id"]]
    # An unpaired caller (a stamp the guard never gave) reads nothing at all.
    as_caller(monkeypatch, remote.Caller(kind="unpaired", ip=IP, login=LOGIN))
    assert remote_client().get("/api/inbox").json() == []

    # The PC never toasts a phone's or Siri's result, even with no page open...
    assert not events.has_subscribers()
    for origin in (PHONE.origin, SIRI.origin, "unknown:x"):
        assert inbox.notify_offline("Rappel", f"Rappel de {origin}", kind="reminder", via=origin) is False
    events.publish("task", {"id": "t-tel2", "title": "Météo", "status": "done", "via": PHONE.origin})
    events.publish("task", {"id": "t-siri2", "title": "Trajet", "status": "error", "via": SIRI.origin})
    assert windows == []
    assert {i["payload"]["id"]: i["via"] for i in inbox.pending(via=None) if i["kind"] == "task"} == {
        "t-siri": SIRI.origin, "t-tel2": PHONE.origin, "t-siri2": SIRI.origin}
    # ...while its own still do.
    events.publish("task", {"id": "t-pc", "title": "Rapport", "status": "done"})
    assert windows == [("toast", "Tâche « Rapport »", "Terminée.")]

# ---------------------------------------------------------------- P1: ten minutes at most

def test_remote_replay_is_limited_to_ten_minutes_holds(monkeypatch):
    """A phone back after a while replays the last ten minutes at most, whatever
    its Last-Event-ID; the PC replays its whole buffer as before."""
    monkeypatch.setattr(remote, "replay_floor", lambda caller: 0)  # paired before all of it

    async def scenario():
        with published_ago(monkeypatch, events.REMOTE_REPLAY_SECONDS + 1):
            stale = [events.publish("memory", {"n": n}) for n in range(3)]
        with published_ago(monkeypatch, events.REMOTE_REPLAY_SECONDS - 60):
            recent = [events.publish("memory", {"n": n}) for n in range(3, 5)]
        now = events.publish("memory", {"n": 5})
        for last in ("0", str(stale[0])):
            phone = await opened("", PHONE, last_event_id=last)
            assert [i for i, _ in await frames(phone, 3)] == recent + [now], last
            await phone.aclose()
        phone = await opened("", PHONE, last_event_id="0")
        marker = events.publish("memory", {"n": "suite"})
        assert [i for i, _ in await until(phone, marker)] == recent + [now, marker]  # and the stream goes on
        await phone.aclose()
        pc = await opened("page-pc", remote.PC, last_event_id=str(stale[0]))
        assert [i for i, _ in await frames(pc, 5)] == stale[1:] + recent + [now]
        await pc.aclose()
    asyncio.run(scenario())
