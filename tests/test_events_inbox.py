"""Reliable delivery on the server: the replayable event stream, the leader
page, the inbox of messages nobody has heard yet, quiet hours and « Ne pas
déranger »."""
import asyncio
import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import server
from jarvis import config, desktop, events, inbox, remote, security, store

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}


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


@pytest.fixture
def client():
    return TestClient(server.app, base_url=BASE)


@pytest.fixture
def windows(monkeypatch):
    """desktop calls recorded instead of made."""
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


async def opened(client_id: str, last_event_id=None):
    """A page's stream, registered (its first frame read)."""
    stream = events.stream(client_id, last_event_id)
    assert (await anext(stream)).startswith("retry:")
    return stream

# ---------------------------------------------------------------- the inbox

def test_a_message_published_with_no_page_waits_in_the_inbox_until_acked(client):
    assert not events.has_subscribers()
    reminder = {"id": "r1", "title": "Pain", "text": "Sortir le pain", "late_minutes": 0}
    item = inbox.add("reminder", reminder)  # the scheduler records it first...
    events.publish("reminder", reminder)    # ...then pushes it: still one message
    r = client.get("/api/inbox", headers=AUTH)
    assert r.status_code == 200
    assert [(i["id"], i["kind"], i["payload"]["text"]) for i in r.json()] == [(item["id"], "reminder", "Sortir le pain")]
    assert client.post(f"/api/inbox/{item['id']}/ack", headers=AUTH).json() == {"ok": True}
    assert client.get("/api/inbox", headers=AUTH).json() == []
    assert client.post("/api/inbox/inconnu/ack", headers=AUTH).status_code == 404
    acked = [json.loads(m) for _, m in events._replay if json.loads(m)["type"] == "inbox"]
    assert acked == [{"type": "inbox", "acked": [item["id"]]}]  # other pages drop it too


def test_publish_records_what_must_be_told_and_tells_the_page_its_inbox_id(windows):
    events.publish("task", {"id": "t1", "title": "Météo", "status": "running", "progress": "…"})
    events.publish("memory", {"facts": []})
    assert inbox.pending() == []  # progress and panels aren't messages
    events.publish("task", {"id": "t1", "title": "Météo", "status": "done", "output": "x" * 9000})
    events.publish("task", {"id": "t2", "title": "Annulée", "status": "cancelled", "output": ""})
    [item] = inbox.pending()
    assert item["kind"] == "task" and len(item["payload"]["output"]) == inbox.MAX_OUTPUT
    pushed = json.loads(events._replay[-2][1])
    assert pushed["inbox_id"] == item["id"] and pushed["status"] == "done"
    # No page open when it ended: one native notification about it.
    assert windows == [("toast", "Tâche « Météo »", "Terminée.")]


def test_a_publisher_that_recorded_the_message_itself_is_not_recorded_twice():
    item = inbox.add("briefing", {"text": "Beau temps."})
    events.publish("briefing", {"text": "Beau temps.", "inbox_id": item["id"]})
    events.publish("briefing", {"text": "Beau temps."})  # same text, moments later
    assert [i["id"] for i in inbox.pending()] == [item["id"]]


def test_inbox_keeps_a_week_and_replays_a_day(monkeypatch):
    now = time.time()
    old = inbox.add("reminder", {"id": "vieux", "text": "il y a 8 jours"})
    yesterday = inbox.add("reminder", {"id": "hier", "text": "il y a 30 heures"})
    with store.LOCK:
        items = store.load(inbox.FILE, [])
        for i in items:
            i["created"] = now - (8 * 86400 if i["id"] == old["id"] else 30 * 3600)
        store.save(inbox.FILE, items)
    fresh = inbox.add("reminder", {"id": "neuf", "text": "maintenant"})
    assert [i["id"] for i in inbox.pending()] == [fresh["id"]]  # the last 24 h only
    kept = [i["id"] for i in store.load(inbox.FILE, [])]
    assert kept == [yesterday["id"], fresh["id"]]  # pruned after 7 days


def test_a_repeating_reminder_is_a_new_message_each_time(monkeypatch):
    first = inbox.add("reminder", {"id": "quotidien", "text": "Médicaments"})
    inbox.ack(first["id"])
    clock = time.time() + 86400
    monkeypatch.setattr(inbox.time, "time", lambda: clock)
    second = inbox.add("reminder", {"id": "quotidien", "text": "Médicaments"})
    assert second["id"] != first["id"]

# ---------------------------------------------------------------- the replayable stream

def test_stream_lines_carry_ids_and_a_reconnection_replays_only_what_was_missed():
    async def scenario():
        ids = [events.publish("memory", {"n": n}) for n in range(5)]
        assert ids == sorted(ids) and len(set(ids)) == 5
        page = await opened("page-a", last_event_id=str(ids[1]))
        got = await frames(page, 4)
        assert [(i, d.get("n")) for i, d in got[:3]] == [(ids[2], 2), (ids[3], 3), (ids[4], 4)]
        assert got[3] == (None, {"type": "leader", "client": "page-a", "live": False})  # who speaks, no id
        new_id = events.publish("memory", {"n": 5})
        rest = await frames(page, 2)  # the election when it joined, then the new event
        assert rest[0][1] == {"type": "leader", "client": "page-a", "live": False} and rest[0][0] < new_id
        assert rest[1] == (new_id, {"type": "memory", "n": 5})
        await page.aclose()

        fresh = await opened("page-b")  # a new page: no replay, the inbox covers it
        assert (await frames(fresh, 1))[0] == (None, {"type": "leader", "client": "page-b", "live": False})
        await fresh.aclose()
    asyncio.run(scenario())


def test_an_unknown_or_future_last_event_id_replays_safely():
    async def scenario():
        published = events.publish("memory", {"n": 1})
        for bad in ("abc", str(published + 10**9)):
            page = await opened("page-x", last_event_id=bad)
            assert (await frames(page, 1))[0][1]["type"] == "leader"  # nothing replayed
            later = events.publish("memory", {"n": 2})
            got = await frames(page, 2)
            assert got[-1] == (later, {"type": "memory", "n": 2})  # and new events still flow
            await page.aclose()
    asyncio.run(scenario())


def test_the_http_route_passes_the_page_id_and_last_event_id():
    async def scenario():
        ids = [events.publish("memory", {"n": n}) for n in range(3)]
        scope = {"type": "http", "method": "GET", "path": "/api/events", "query_string": b"",
                 "headers": [(b"last-event-id", str(ids[0]).encode())], "state": {"caller": remote.PC}}
        response = await server.stream_events(Request(scope), client="page-http")
        stream = response.body_iterator
        assert (await anext(stream)).startswith("retry:")
        assert [i for i, _ in await frames(stream, 2)] == ids[1:]
        assert events.leader() == "page-http"
        await stream.aclose()
    asyncio.run(scenario())


def test_a_slow_page_keeps_at_most_500_events_and_loses_the_oldest():
    async def scenario():
        page = await opened("lente")
        ids = [events.publish("memory", {"n": n}) for n in range(600)]
        await asyncio.sleep(0)  # the hand-overs run on this loop
        [sub] = [s for s in events._subscribers if s.client == "lente"]
        assert sub.queue.qsize() == events.QUEUE_SIZE == 500
        got = await frames(page, 2)
        assert got[0][1]["type"] == "leader"
        assert got[1] == (ids[100], {"type": "memory", "n": 100})
        await page.aclose()
    asyncio.run(scenario())


def test_close_streams_ends_every_stream():
    async def scenario():
        page = await opened("page-a")
        assert events.has_subscribers()
        events.close_streams()
        rest = [chunk async for chunk in page]
        assert not any(c.startswith("id:") for c in rest[1:])
        assert not events.has_subscribers()
        late = events.stream("page-b")  # opened during shutdown: ends at once
        assert [chunk async for chunk in late] == []
    asyncio.run(scenario())

# ---------------------------------------------------------------- the leader page

def test_leader_is_the_live_page_else_the_last_focused_else_the_newest(client):
    async def scenario():
        a = await opened("page-a")
        assert events.leader() == "page-a"
        await asyncio.sleep(0.01)
        b = await opened("page-b")
        assert events.leader() == "page-b"  # nobody focused: the newest
        assert events.presence("page-a", focused=True) == "page-a"
        await asyncio.sleep(0.01)
        assert events.presence("page-b", focused=True) == "page-b"  # focused more recently
        assert events.presence("page-a", focused=True) == "page-b"  # already focused: no change
        assert events.presence("page-a", live=True) == "page-a"     # a voice session wins
        assert events.presence("page-b", focused=True) == "page-a"
        events.presence("page-a", live=False, focused=False)
        assert events.leader() == "page-b"
        await asyncio.sleep(0.01)
        events.presence("page-b", focused=False)  # monsieur went to another app
        assert events.leader() == "page-b"        # the one he left last
        assert events.presence("page-a", claim=True) == "page-a"  # 'Utiliser celle-ci'
        await a.aclose()                          # that page closes
        assert events.leader() == "page-b"
        await b.aclose()
        assert events.leader() is None
        changes = [json.loads(m)["client"] for _, m in events._replay if json.loads(m)["type"] == "leader"]
        assert changes == ["page-a", "page-b", "page-a", "page-b", "page-a", "page-b", "page-a", "page-b"]
        r = client.post("/api/presence", headers=AUTH, json={"client": "pas valide!", "focused": True})
        assert r.status_code == 400
    asyncio.run(scenario())


def test_presence_route_answers_with_the_leader(client):
    async def scenario():
        page = await opened("page-r")
        r = client.post("/api/presence", headers=AUTH, json={"client": "page-r", "focused": True, "live": False})
        assert r.json() == {"leader": "page-r", "live": False}
        await page.aclose()
    asyncio.run(scenario())


def test_the_leader_event_says_when_that_page_is_in_a_conversation(client):
    """A claim can't take over a conversation: the other pages are told why
    ('JARVIS est en conversation dans l'autre fenêtre')."""
    async def scenario():
        a = await opened("page-a")
        b = await opened("page-b")
        events.presence("page-a", focused=True)
        events._replay.clear()
        assert events.presence("page-a", live=True) == "page-a"
        r = client.post("/api/presence", headers=AUTH, json={"client": "page-b", "claim": True})
        assert r.json() == {"leader": "page-a", "live": True}  # the conversation keeps it
        events.presence("page-a", live=False)
        leaders = [json.loads(m) for _, m in events._replay if json.loads(m)["type"] == "leader"]
        # Same page, but its conversation started then ended: both are news.
        assert [(m["client"], m["live"]) for m in leaders] == [("page-a", True), ("page-b", False)]
        await a.aclose()
        await b.aclose()
    asyncio.run(scenario())


def test_presence_before_the_stream_is_kept_until_it_opens():
    async def scenario():
        other = await opened("page-a")
        events.presence("page-b", focused=True)  # its POST beat its stream
        assert events.leader() == "page-a"       # no stream yet: not a candidate
        page = await opened("page-b")
        assert events.leader() == "page-b"
        await page.aclose()
        await other.aclose()
    asyncio.run(scenario())

# ---------------------------------------------------------------- quiet hours, DND, no page open

@pytest.mark.parametrize("spec, hhmm, quiet", [
    ("22:30-07:30", "23:00", True), ("22:30-07:30", "07:29", True), ("22:30-07:30", "07:30", False),
    ("22:30-07:30", "12:00", False), ("22h-7h", "06:59", True), ("13:00-14:00", "13:30", True),
    ("13:00-14:00", "14:00", False), ("", "23:00", False), ("off", "23:00", False),
    ("n'importe quoi", "23:00", False), ("25:00-07:00", "23:00", False), ("08:00-08:00", "08:00", False),
])
def test_quiet_hours(spec, hhmm, quiet):
    h, m = map(int, hhmm.split(":"))
    assert inbox.quiet_hours(datetime(2026, 10, 9, h, m), spec) is quiet


def test_do_not_disturb_is_stored_for_the_scheduler_and_the_page(client, monkeypatch):
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    # Not the real machine's state: a Windows CI runner reports 'quiet_time'.
    monkeypatch.setattr(desktop, "attention_state", lambda: "ok")
    until = time.time() + 3600
    assert client.post("/api/dnd", headers=AUTH, json={"until": until}).json() == {"until": until}
    assert store.load("state.json", {})["dnd_until"] == until
    assert inbox.dnd_until() == until and inbox.is_quiet()
    state = client.get("/api/delivery", headers=AUTH).json()
    assert state["dnd_until"] == until and state["quiet_hours"] == "" and state["attention"] == "ok"
    pushed = [json.loads(m) for _, m in events._replay if json.loads(m)["type"] == "dnd"]
    assert pushed == [{"type": "dnd", "until": until}]
    assert client.post("/api/dnd", headers=AUTH, json={"until": None}).json() == {"until": None}
    assert inbox.dnd_until() is None and not inbox.is_quiet()
    r = client.post("/api/dnd", headers=AUTH, json={"minutes": 60}).json()
    assert 3590 < r["until"] - time.time() <= 3600
    assert client.post("/api/dnd", headers=AUTH, json={"until": time.time() + 30 * 86400}).json()["until"] \
        <= time.time() + 7 * 86400  # at most a week


def test_with_no_page_open_one_native_notification_and_the_window_back_for_a_reminder(windows, monkeypatch):
    assert inbox.notify_offline("Rappel", "Sortir le pain") is True
    assert windows == [("toast", "Rappel", "Sortir le pain"), ("show", f"http://127.0.0.1:{config.PORT}")]
    assert inbox.notify_offline("Rappel", "Sortir le pain") is False  # the same message: once
    windows.clear()
    monkeypatch.setattr(config, "QUIET_HOURS", "00:00-24:00")
    assert inbox.notify_offline("Rappel", "Arroser")  # quiet hours: the toast, not the window
    assert inbox.notify_offline("Tâche", "Terminée.", kind="task") is False  # waits for a page
    assert windows == [("toast", "Rappel", "Arroser")]
    windows.clear()
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    monkeypatch.setattr(desktop, "attention_state", lambda: "fullscreen")  # a game, a film
    assert inbox.notify_offline("Rappel", "Appeler le garage")
    assert inbox.notify_offline("Tâche", "Finie.", kind="task") is False
    assert windows == [("toast", "Rappel", "Appeler le garage")]
    monkeypatch.setattr(config, "REOPEN_ON_REMINDER", False)
    monkeypatch.setattr(desktop, "attention_state", lambda: "ok")
    windows.clear()
    assert inbox.notify_offline("Rappel", "Thé")
    assert windows == [("toast", "Rappel", "Thé")]


def test_with_a_page_open_the_page_tells_it(windows):
    async def scenario():
        page = await opened("page-a")
        assert inbox.notify_offline("Rappel", "Sortir le pain") is False
        events.publish("task", {"id": "t9", "title": "X", "status": "error", "output": "boom"})
        await page.aclose()
    asyncio.run(scenario())
    assert windows == []
    assert [i["payload"]["id"] for i in inbox.pending()] == ["t9"]  # recorded all the same


def test_a_broken_inbox_file_is_set_aside_and_recording_goes_on():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / inbox.FILE).write_text('[{"id": "a",}]', encoding="utf-8")
    item = inbox.add("reminder", {"id": "r", "text": "Toujours là"})
    kinds = [i["kind"] for i in inbox.pending()]
    assert item["id"] in [i["id"] for i in inbox.pending()] and "warning" in kinds
    assert list(config.DATA_DIR.glob("inbox.json.corrompu-*"))
