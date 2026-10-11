"""The web surface's protections, server side: the event stream needs the page
token and its replay carries no secret, data files keep fixed names under
DATA_DIR, logs never carry the OpenAI key or the page token, and the
ephemeral Realtime secret is short-lived and never kept."""
import ast
import asyncio
import json
import logging
import re
import sys
import time
import types
from collections import deque
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from test_tasks import FAKE_CLAUDE, wait

import server
from jarvis import (
    config,
    confirm,
    events,
    inbox,
    memory,
    realtime,
    remote,
    security,
    tasks,
)

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}
ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "jarvis").glob("*.py")) + [ROOT / "server.py"]

KEY = "sk-proj-SENTINEL0123456789abcdefKEY"
EPHEMERAL = "ek_SENTINEL0123456789abcdefEPHEMERAL"


@pytest.fixture
def client():
    return TestClient(server.app, base_url=BASE)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """No event, page, confirmation or task left over, before or after."""
    def clear():
        events._replay.clear()
        events._clients.clear()
        events._leader = None
        events._leader_live = False
        events._closing.clear()
        confirm.PENDING.clear()
        confirm.SESSIONS.clear()
        inbox._toasted.clear()
    clear()
    monkeypatch.setattr(config, "OPENAI_API_KEY", KEY)
    monkeypatch.setattr(config, "CONFIRM_COMPLET", True)
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: False)  # no native toast in tests
    yield
    for task in tasks.running():
        tasks.cancel(task["id"])
    end = time.time() + 10
    while (tasks._active or tasks._waiting) and time.time() < end:
        time.sleep(0.05)
    clear()


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)


def fake_openai(monkeypatch, status=200, body=None, raises=None):
    """OpenAI's client_secrets endpoint; returns what each call was sent."""
    sent = []

    def post(url, headers, json, timeout):
        sent.append({"url": url, "headers": headers, "json": json})
        if raises is not None:
            raise raises
        payload = {"value": EPHEMERAL, "expires_at": int(time.time()) + 120} if body is None else body
        if isinstance(payload, str):
            return httpx.Response(status, text=payload, request=httpx.Request("POST", url))
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(realtime.httpx, "post", post)
    return sent


def secrets_in(text: str, *extra) -> list:
    return [s for s in (KEY, EPHEMERAL, security.TOKEN, *extra) if s and s in text]

# ---------------------------------------------------------------- 4. the event stream


def test_event_stream_requires_the_token_holds(client):
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/events?token=").status_code == 401
    assert client.get("/api/events?token=nope&client=page-1").status_code == 401
    assert client.get("/api/events", headers={"X-Jarvis-Token": "nope"}).status_code == 401
    assert client.get("/api/events", headers={"Last-Event-ID": "0"}).status_code == 401
    good = f"/api/events?token={security.TOKEN}"
    assert client.get(good, headers={"Host": "evil.example:8788"}).status_code == 403
    assert client.get(good, headers={"Origin": "https://evil.example"}).status_code == 403
    assert not events.has_subscribers()
    # With the token the stream opens (closing: it ends at once instead of streaming forever).
    events._closing.set()
    r = client.get(good)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")


async def _replay_all(client_id: str) -> str:
    """Everything the route replays to a page that says it saw nothing (Last-Event-ID: 0)."""
    scope = {"type": "http", "method": "GET", "path": "/api/events", "query_string": b"",
             "headers": [(b"last-event-id", b"0")], "state": {"caller": remote.PC}}
    response = await server.stream_events(Request(scope), client=client_id)
    stream = response.body_iterator
    frames = []
    try:
        while True:
            frame = await asyncio.wait_for(anext(stream), 2)
            frames.append(frame)
            if frame.startswith("data: ") and '"type": "leader"' in frame:
                break  # the replay is over: 'who speaks' comes right after it
    finally:
        await stream.aclose()
    return "".join(frames)


def test_replayed_events_carry_no_secret_holds(client, fake_claude, monkeypatch):
    """A voice session is minted, then every kind of event a page can be sent is
    produced (confirmation cards, a task with denied tools and its approval
    card, memory, reminders, inbox, « Ne pas déranger », leader): the replay of
    all of it holds neither the OpenAI key, nor the ephemeral secret, nor the
    page token, nor the voice session id that confirmations are tied to."""
    fake_openai(monkeypatch)
    minted = client.post("/api/session", headers=AUTH, json={"recent": "monsieur : bonjour"}).json()
    assert minted["client_secret"] == EPHEMERAL  # the page itself does get it
    sid = minted["session_id"]
    api = TestClient(server.app, base_url=BASE, headers=AUTH)
    assert api.post("/api/voice/taint", json={"session_id": sid, "reason": "page web"}).json()["ok"]
    assert api.post("/api/voice/turn", json={"session_id": sid}).json()["ok"]

    def tool(name, **arguments):
        return api.post("/api/tool", json={"name": name, "arguments": arguments, "session_id": sid}).json()

    assert tool("delegate_to_claude", title="Complet", prompt="range le disque",
                profile="complet")["status"] == "needs_confirmation"
    started = tool("delegate_to_claude", title="Avec refus", prompt="REFUS lis le dossier", profile="lecture")
    wait(tasks.TASKS[started["task_id"]])
    # A tainted session parks remember (spec 4.11 rule c'): launch its card so a memory event exists.
    api.post(f"/api/pending/{tool('remember', fact='Le portail est vert')['pending_id']}/decide", json={"decision": "oui"})
    tool("schedule", kind="reminder", title="Arrosage", text="Arroser", delay_minutes=60)
    events.publish("reminder", {"id": "r1", "title": "Arrosage", "text": "Arroser"})
    api.post("/api/dnd", json={"minutes": 5})
    api.post("/api/presence", json={"client": "page-1", "focused": True})
    for item in inbox.pending():
        api.post(f"/api/inbox/{item['id']}/ack")

    kinds = {json.loads(message)["type"] for _, message in events._replay}
    assert {"pending", "task", "memory", "schedules", "reminder", "dnd", "inbox"} <= kinds
    assert any(confirm.PENDING[p]["kind"] == "task_approval" for p in confirm.PENDING)
    replayed = asyncio.run(_replay_all("page-replay"))
    assert "id: " in replayed and '"type": "pending"' in replayed
    assert secrets_in(replayed, sid) == []
    # Nor do the panels' own routes.
    for path in ("/api/tasks", f"/api/task/{started['task_id']}", f"/api/task/{started['task_id']}/log",
                 "/api/pending", "/api/inbox", "/api/schedules", "/api/memory", "/api/delivery", "/api/config"):
        assert secrets_in(api.get(path).text, sid) == [], path

# ---------------------------------------------------------------- 5. data files


def _module_strings(tree: ast.Module) -> dict:
    """Module-level NAME = "literal" assignments."""
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value.value
    return found


def _is_data_dir(node) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "DATA_DIR"


def test_data_files_have_fixed_names_under_data_dir_holds():
    """Every store.save/store.load names its file with a module constant (a bare
    file name), and every path built on DATA_DIR appends a literal: no file
    name is ever made from a request, a tool argument or the model."""
    names, joined = set(), set()
    for source in SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        consts = _module_strings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "store" \
                    and node.func.attr in ("save", "load"):
                where = f"{source.name}:{node.lineno}"
                arg = node.args[0]
                if isinstance(arg, ast.Constant):
                    name = arg.value
                else:
                    assert isinstance(arg, ast.Name) and arg.id in consts, f"nom de fichier variable : {where}"
                    name = consts[arg.id]
                assert isinstance(name, str) and re.fullmatch(r"[\w.-]+", name) and ".." not in name, where
                names.add(name)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and _is_data_dir(node.left) \
                    and source.name != "store.py":  # store.py joins the constant names checked above
                right = node.right
                assert isinstance(right, ast.Constant) and re.fullmatch(r"[\w.-]+", str(right.value)), \
                    f"chemin construit sur DATA_DIR : {source.name}:{node.lineno}"
                joined.add(right.value)
    assert {"inbox.json", "state.json", "memory.json", "schedules.json", "tasks.json"} <= names
    # Remote access: the switch and the devices through store, the audit trail and
    # its alerts as literal names under DATA_DIR (never a name from a request).
    assert {"remote.json", "devices.json"} <= names
    # The ntfy topic (B1): one fixed file through store, never .env.
    assert "ntfy.json" in names
    assert {"remote-audit.jsonl", "remote-audit.1.jsonl", "remote-alerts.jsonl", "remote-alerts.1.jsonl"} <= joined


def test_the_ntfy_topic_lives_in_its_data_file_only_holds():
    """B1: the topic is made into data/ntfy.json through store, under a module
    constant; notify.py never writes .env nor the environment."""
    from jarvis import notify
    assert notify.NTFY_FILE == "ntfy.json"
    tree = ast.parse((ROOT / "jarvis" / "notify.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
        {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"ENV_FILE", "write_key", "environ", "putenv", "open", "write_text", "write_bytes"}


HOSTILE = ["../../evil", "..%2F..%2Fevil", "/tmp/evil", "C:\\evil", "..\\..\\evil", "evil\x00.json", "%00"]


def test_hostile_ids_and_texts_never_name_a_file_holds(client, fake_claude, tmp_path):
    """Ids from the URL and texts from tools go through every writing path; the
    only files written are the fixed data files, all inside DATA_DIR."""
    api = TestClient(server.app, base_url=BASE, headers=AUTH)
    data = config.DATA_DIR
    for bad in HOSTILE:
        quoted = bad.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C").replace("\x00", "%00")
        api.delete(f"/api/schedules/{quoted}")
        api.patch(f"/api/schedules/{quoted}", json={"title": bad, "text": bad, "at": bad})  # WP17
        api.post(f"/api/schedules/{quoted}/snooze", json={"minutes": 10})
        api.post(f"/api/remarques/{quoted}/dismiss")
        api.post("/api/schedules", json={"title": bad, "text": bad, "delay_minutes": 60})
        api.delete(f"/api/memory/{quoted}")
        api.post(f"/api/inbox/{quoted}/ack")
        api.get(f"/api/task/{quoted}")
        api.get(f"/api/task/{quoted}/log")
        api.post(f"/api/task/{quoted}/cancel")
        api.post(f"/api/pending/{quoted}/decide", json={"decision": "oui"})
        assert api.post("/api/presence", json={"client": bad}).status_code == 400
        api.post("/api/tool", json={"name": "remember", "arguments": {"fact": bad}})
        api.post("/api/tool", json={"name": "schedule", "arguments": {
            "kind": "reminder", "title": bad, "text": bad, "delay_minutes": 60}})
        api.post("/api/tool", json={"name": "forget", "arguments": {"query": bad}})
        api.post("/api/tool", json={"name": "cancel_schedule", "arguments": {"query": bad}})
        inbox.add("reminder", {"id": bad, "title": bad, "text": bad})
    task = api.post("/api/tasks", json={"prompt": "lis " + HOSTILE[0], "title": HOSTILE[0]}).json()
    wait(tasks.TASKS[task["id"]])
    api.post("/api/dnd", json={"minutes": 5})

    # usage.json: the day's costs (WP18), the finished task's included.
    allowed = re.compile(r"(inbox|state|memory|schedules|tasks|trash|usage)\.json(\.bak|\.tmp|\.corrompu-[\d-]+)?")
    day_file = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl")  # the journal: one file per day, named by the server
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    inside = [p for p in written if data in p.parents]
    assert {p.name for p in inside} >= {"inbox.json", "memory.json", "schedules.json", "tasks.json", "state.json"}
    assert [p for p in inside if not ((p.parent == data and allowed.fullmatch(p.name))
                                      or (p.parent == data / "journal" and day_file.fullmatch(p.name)))] == []
    # Outside DATA_DIR: only the fake claude and what it writes in its working folder.
    outside = {p.relative_to(tmp_path).as_posix() for p in written if data not in p.parents}
    assert outside <= {"fake_claude.py", "travail/pids.txt"}, outside
    assert not any("evil" in p.name for p in tmp_path.rglob("*"))

# ---------------------------------------------------------------- 6. logs


# + remote access (A1): a device or pairing secret, a page token, a cookie. Names
# and substrings of string constants: a log text may not even say "cookie".
# + ntfy (B1): the topic is the address of monsieur's notifications.
_SECRET_NAMES = {"OPENAI_API_KEY", "TOKEN", "client_secret", "Authorization", "full_prompt",
                 "secret", "page_token", "pair_secret", "cookie", "topic"}


def _names(node) -> set:
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            found.update(n for n in _SECRET_NAMES if n in sub.value)
    return found


def test_no_logging_call_mentions_a_secret_holds():
    """Every logging.*/print call in JARVIS: none is handed the key, the token,
    the ephemeral secret or a prompt with memory in it."""
    calls = 0
    for source in SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_log = (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                      and func.value.id in ("logging", "logger", "log")) \
                or (isinstance(func, ast.Name) and func.id == "print")
            if not is_log:
                continue
            calls += 1
            used = set().union(*(_names(a) for a in node.args), *(_names(k.value) for k in node.keywords))
            assert not (used & _SECRET_NAMES), f"{source.name}:{node.lineno} {used & _SECRET_NAMES}"
    assert calls >= 10  # the scan really found JARVIS's logging calls


@pytest.mark.parametrize("answer", [
    {"status": 401, "body": {"error": {"message": f"Incorrect API key provided: {KEY}.", "code": "invalid_api_key"}}},
    {"status": 400, "body": {"error": {"message": f"Bad header Authorization: Bearer {KEY}", "type": "invalid_request"}}},
    {"status": 429, "body": {"error": {"message": f"quota for {KEY}", "code": "insufficient_quota"}}},
    {"status": 500, "body": f"<html>upstream {KEY}</html>"},
    {"status": 200, "body": f"not json {KEY}"},
    {"raises": httpx.ConnectError(f"connection refused (Authorization: Bearer {KEY})")},
    {"raises": httpx.ReadTimeout(f"timed out {KEY}")},
], ids=["401", "400", "429", "500-html", "200-not-json", "network", "timeout"])
def test_a_failed_mint_logs_no_key_holds(answer, monkeypatch, caplog, capfd):
    """Whatever OpenAI answers (even echoing the key), nothing JARVIS logs or
    prints carries it, and neither does the error the page shows (the page
    writes errors to its console)."""
    fake_openai(monkeypatch, status=answer.get("status", 200), body=answer.get("body"),
                raises=answer.get("raises"))
    caplog.set_level(logging.DEBUG)
    unsafe = TestClient(server.app, base_url=BASE, raise_server_exceptions=False)
    r = unsafe.post("/api/session", headers=AUTH, json={"recent": ""})
    assert r.status_code >= 400
    out, err = capfd.readouterr()
    logged = caplog.text + "".join(str(rec.args) for rec in caplog.records) + out + err
    assert secrets_in(logged) == []
    assert secrets_in(r.text) == []


def test_the_access_log_with_the_stream_token_is_off_holds(monkeypatch):
    """The page's event stream carries the token in its URL (?token=): the
    request log that would write that URL stays off."""
    import uvicorn
    captured = {}

    class FakeServer:
        def __init__(self, cfg):
            captured["config"] = cfg
            self.should_exit = False

        def run(self):
            pass

    monkeypatch.setattr(sys, "argv", ["server.py"])
    monkeypatch.setattr(server, "_already_running", lambda url: False)
    monkeypatch.setattr(server, "_log_to_file_if_windowless", lambda: None)
    monkeypatch.setattr(server.shell, "start", lambda url, quit: None)
    monkeypatch.setattr(server.shell, "stop", lambda: None)
    monkeypatch.setattr(server, "JarvisServer", FakeServer)
    access = logging.getLogger("uvicorn.access")
    saved = (access.level, access.handlers[:], access.propagate)
    try:
        server.main()
        cfg = captured["config"]
        assert isinstance(cfg, uvicorn.Config)
        level = cfg.log_level if isinstance(cfg.log_level, int) else logging.getLevelName(str(cfg.log_level).upper())
        assert cfg.access_log is False or level > logging.INFO
        assert not access.isEnabledFor(logging.INFO)
    finally:
        access.setLevel(saved[0])
        access.handlers[:] = saved[1]
        access.propagate = saved[2]
        server.SERVER = None

def test_the_serve_listener_never_logs_the_phone_token_holds(monkeypatch, caplog, capfd):
    """The iPhone's event stream carries its page token in its URL too, through
    the second listener (the one Tailscale Serve reaches): neither uvicorn's
    request log nor the remote audit ever writes that URL."""
    import socket

    from remote_helpers import LOGIN, REMOTE_HOST, remote_headers

    from jarvis import listener
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(config, "REMOTE_PORT", port)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    caplog.set_level(logging.DEBUG)
    # Every uvicorn logger listening (the listener's own Config then sets their level
    # when it starts: access_log=False and log_level="warning" both keep request lines out).
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        caplog.set_level(logging.DEBUG, logger=name)
    secret = "PAGETOKEN-SENTINEL-0123456789abcdef"
    remote.set_enabled(True, host=REMOTE_HOST, login=LOGIN)  # the real listener
    try:
        assert listener.state()["running"] is True
        with httpx.Client(trust_env=False, timeout=5) as c:
            r = c.get(f"http://127.0.0.1:{port}/api/events?token={secret}&client=page-1",
                      headers=remote_headers(origin=False))
            assert r.status_code == 401  # unpaired: refused, and its URL noted nowhere
            assert c.get(f"http://127.0.0.1:{port}/healthz?token={secret}",
                         headers=remote_headers(origin=False)).status_code == 200
    finally:
        remote.set_enabled(False)
    out, err = capfd.readouterr()
    # The server's own records (the test's httpx client logs its URL itself: not JARVIS's).
    logged = "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records
                       if not r.name.startswith(("httpx", "httpcore")))
    trail = "".join(p.read_text(encoding="utf-8") for p in config.DATA_DIR.glob("remote-*.jsonl"))
    assert "/api/events" in trail  # the refusal is audited, by its route
    for text in (logged, out, err, trail):
        assert secret not in text and "token=" not in text

# ---------------------------------------------------------------- 7. the ephemeral secret


def test_ephemeral_secret_is_short_lived_holds(client, monkeypatch):
    sent = fake_openai(monkeypatch)
    assert client.post("/api/session", headers=AUTH, json={}).status_code == 200
    expiry = sent[0]["json"]["expires_after"]
    assert expiry == {"anchor": "created_at", "seconds": config.SECRET_TTL}
    assert 10 <= expiry["seconds"] <= 600  # minutes, not hours, by default
    assert sent[0]["headers"]["Authorization"] == f"Bearer {KEY}"  # the key goes to OpenAI only
    for ttl, seconds in ((0, 10), (-5, 10), (10**9, 7200), (90, 90)):
        monkeypatch.setattr(config, "SECRET_TTL", ttl)
        assert realtime.session_payload()["expires_after"]["seconds"] == seconds


def _holds(value, needle: str, seen: set, depth: int = 0) -> bool:
    """Is needle somewhere in this value (containers and JARVIS objects walked)?"""
    if isinstance(value, (str, bytes)):
        return needle in (value if isinstance(value, str) else value.decode("utf-8", "replace"))
    if depth > 8 or id(value) in seen or isinstance(value, (types.ModuleType, types.FunctionType, type)):
        return False
    seen.add(id(value))
    if isinstance(value, dict):
        return any(_holds(k, needle, seen, depth + 1) or _holds(v, needle, seen, depth + 1)
                   for k, v in list(value.items()))
    if isinstance(value, (list, tuple, set, frozenset, deque)):
        return any(_holds(v, needle, seen, depth + 1) for v in list(value))
    if type(value).__module__.startswith(("jarvis", "server")) and hasattr(value, "__dict__"):
        return _holds(vars(value), needle, seen, depth + 1)
    return False


def test_ephemeral_secret_is_never_kept_server_side_holds(client, monkeypatch, caplog):
    """Once /api/session has answered, the ephemeral secret is nowhere on the
    server: not in any JARVIS module's state, not in the event replay, not in
    a data file, not in the logs."""
    fake_openai(monkeypatch)
    caplog.set_level(logging.DEBUG)
    r = client.post("/api/session", headers=AUTH, json={"recent": "monsieur : bonjour"})
    assert r.json()["client_secret"] == EPHEMERAL
    assert r.headers.get("cache-control", "no-store") in ("no-store", "no-cache")
    del r
    modules = [m for name, m in list(sys.modules.items())
               if m is not None and (name == "server" or name.startswith("jarvis"))]
    assert len(modules) > 10
    for module in modules:
        for name, value in list(vars(module).items()):
            assert not _holds(value, EPHEMERAL, set()), f"{module.__name__}.{name}"
    assert not any(EPHEMERAL in message for _, message in events._replay)
    for path in config.DATA_DIR.rglob("*") if config.DATA_DIR.exists() else []:
        if path.is_file():
            assert EPHEMERAL not in path.read_text(encoding="utf-8", errors="replace"), path
    assert EPHEMERAL not in caplog.text


def test_voice_session_and_full_prompt_stay_out_of_task_events_holds(fake_claude):
    """The full prompt (memory included) and the voice session id stay on the
    server: a task's events carry the prompt monsieur gave, not those."""
    memory.remember("Code du portail : 4321")
    task = tasks.create_task("Analyse", "lis le dossier", profile="lecture", voice_session="sid-secret")
    assert "4321" in task["full_prompt"]
    wait(task)
    sent = [json.loads(message) for _, message in events._replay if '"type": "task"' in message]
    assert sent and all("full_prompt" not in e and "voice_session" not in e for e in sent)
    assert not any("sid-secret" in message for _, message in events._replay)
