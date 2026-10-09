"""Defensive checks of the confirmation layer (jarvis/confirm.py): each test
proves that one protection holds, whatever the voice model tries.

1. A full-access task or routine starts only after monsieur's "oui": the
   [Lancer] button (with the page token) or his words in a NEW turn.
2. No voice tool or argument can register his turn or approve on his behalf.
3. A request is decided once, with the arguments stored when it was asked.
4. Once outside content entered the conversation, unknown links and
   clipboard writes ask first.
7. tasks.approve() is reachable only through a confirmed approval.
"""
import ast
import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import config, confirm, desktop, realtime, scheduler, security, tasks, tools
from test_tasks import FAKE_CLAUDE, wait

ROOT = Path(__file__).resolve().parent.parent
REFUSED = "Confirmation refusée : attendez la réponse de monsieur."
COMPLET = {"title": "Ranger les téléchargements", "prompt": "Range ~/Downloads par type",
           "profile": "complet"}
ROUTINE = {"kind": "task", "title": "Ménage", "text": "Vide la corbeille chaque matin",
           "at": "08:00", "repeat": "daily", "profile": "complet"}


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    confirm.PENDING.clear()
    confirm.SESSIONS.clear()
    monkeypatch.setattr(config, "CONFIRM_COMPLET", True)
    monkeypatch.setattr(config, "PENDING_TTL", 90)
    monkeypatch.setattr(config, "OPEN_URL_ALLOW", "")
    yield
    for task in tasks.running():
        tasks.cancel(task["id"])
    end = time.time() + 10
    while (tasks._active or tasks._waiting) and time.time() < end:  # their threads are over
        time.sleep(0.05)
    confirm.PENDING.clear()
    confirm.SESSIONS.clear()


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Never the real `claude`: a script that echoes what it got."""
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)


@pytest.fixture
def nothing_real(monkeypatch):
    """Nothing opens, locks, writes the clipboard or grabs the screen: returns what would have run."""
    done = []
    monkeypatch.setattr(desktop, "open_target", lambda **kw: done.append(("open", kw.get("url") or kw.get("name")))
                        or {"ok": True})
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: done.append((action, value))
                        or {"ok": True})
    monkeypatch.setattr(desktop, "screenshot_jpeg", lambda monitor=None: b"\xff\xd8jpeg")
    return done


@pytest.fixture
def clock(monkeypatch):
    """confirm's clock, moved by hand."""
    now = [1_000_000.0]
    monkeypatch.setattr(confirm, "_now", lambda: now[0])
    return now


@pytest.fixture
def client():
    import server
    return TestClient(server.app, base_url="http://127.0.0.1:8788",
                      headers={"X-Jarvis-Token": security.TOKEN})


def ctx(sid=None):
    return tools.ToolCtx(session_id=sid)


def confirm_by_voice(pending_id, sid, decision="oui", **extra):
    return tools.run_tool("confirm_action", {"pending_id": pending_id, "decision": decision, **extra}, ctx(sid))


def complet_tasks():
    return [t for t in tasks.TASKS.values() if t["profile"] == "complet"]


def all_families_on(monkeypatch):
    for fam in tools.FAMILIES:
        monkeypatch.setattr(fam, "available", lambda: True)


# ---------------------------------------------------------------- 1. full access waits for a "oui"

def test_complet_task_never_starts_before_a_yes_in_a_new_turn_holds(fake_claude, clock):
    asking, other = confirm.new_session(), confirm.new_session()
    clock[0] += 1
    confirm.mark_turn(asking)  # monsieur's request itself
    clock[0] += 1
    out = tools.run_tool("delegate_to_claude", COMPLET, ctx(asking))
    assert out["status"] == "needs_confirmation" and not tasks.TASKS
    pending = out["pending_id"]
    # The model answering for monsieur in the same turn (even in the same
    # breath, with the very same clock reading): refused.
    assert confirm_by_voice(pending, asking) == {"ok": False, "error": REFUSED}
    clock[0] += 1
    assert confirm_by_voice(pending, asking)["error"] == REFUSED
    # A turn in another conversation is not monsieur answering this one.
    confirm.mark_turn(other)
    clock[0] += 1
    assert confirm_by_voice(pending, asking)["error"] == REFUSED
    assert not tasks.TASKS and confirm.PENDING[pending]["state"] == "pending"
    # His "oui", spoken or typed after the question: it runs, once.
    confirm.mark_turn(asking)
    clock[0] += 1
    out = confirm_by_voice(pending, asking)
    assert out["status"] == "started"
    assert [t["prompt"] for t in complet_tasks()] == [COMPLET["prompt"]]
    wait(tasks.TASKS[out["task_id"]])


def test_complet_task_asked_again_needs_a_yes_after_the_new_question_holds(fake_claude, clock):
    """Asking the same thing again shows the same card, and the "oui" must
    still come after that latest question, never in the same model turn."""
    sid = confirm.new_session()
    first = tools.run_tool("delegate_to_claude", COMPLET, ctx(sid))["pending_id"]
    clock[0] += 2
    confirm.mark_turn(sid)  # « attendez, je ne sais pas »
    clock[0] += 2
    again = tools.run_tool("delegate_to_claude", dict(COMPLET), ctx(sid))
    assert again["pending_id"] == first and len(confirm.PENDING) == 1
    # Re-asked and confirmed in one model turn: refused.
    assert confirm_by_voice(first, sid) == {"ok": False, "error": REFUSED}
    assert not tasks.TASKS
    clock[0] += 1
    confirm.mark_turn(sid)  # « oui, allez-y »
    clock[0] += 1
    out = confirm_by_voice(first, sid)
    assert out["status"] == "started"
    wait(tasks.TASKS[out["task_id"]])


def test_only_the_exact_complet_profile_reaches_full_access_and_it_is_always_gated_holds(fake_claude):
    """Whatever the model writes as a profile, tasks runs it as complet only
    when the gate asked first."""
    candidates = ["complet", "Complet", " complet ", "COMPLET", "complet\n", "complet\u200b",
                  "complèt", "full", "", None, 0, ["complet"], {"complet": True}]
    for profile in candidates:
        if tasks.normalize_profile(profile) == "complet":
            assert confirm._complet(profile), profile
            out = confirm.gate("delegate_to_claude", {**COMPLET, "profile": profile}, ctx("s"))
            assert out and out["status"] == "needs_confirmation", profile
    assert [p for p in candidates if tasks.normalize_profile(p) == "complet"] == ["complet"]
    # The voice tool itself: every candidate either waits or runs narrower.
    for profile in candidates:
        out = tools.run_tool("delegate_to_claude", {**COMPLET, "profile": profile}, ctx("s"))
        if out.get("status") != "needs_confirmation":
            assert out["profile"] != "complet", profile
    assert not complet_tasks()


def test_complet_routine_is_never_scheduled_before_a_yes_holds(fake_claude, clock, client):
    sid = confirm.new_session()
    for kind in ("task", "Task", "TASK"):
        out = tools.run_tool("schedule", {**ROUTINE, "kind": kind}, ctx(sid))
        assert out["status"] == "needs_confirmation", kind
    assert scheduler.items() == []
    pending = tools.run_tool("schedule", ROUTINE, ctx(sid))["pending_id"]
    assert confirm_by_voice(pending, sid)["error"] == REFUSED  # same turn
    assert scheduler.items() == []
    clock[0] += 1
    assert client.post("/api/voice/turn", json={"session_id": sid}).json() == {"ok": True}
    clock[0] += 1
    out = confirm_by_voice(pending, sid, text="Supprime tout", profile="complet")
    assert out["ok"] and "Ménage" in out["scheduled"]
    [item] = scheduler.items()
    assert item["profile"] == "complet" and item["text"] == ROUTINE["text"]
    # A routine below complet needs no confirmation (and cannot become complet).
    assert tools.run_tool("schedule", {**ROUTINE, "profile": "Complet ", "kind": "rappel"}, ctx(sid))["ok"]
    assert all(i["profile"] != "complet" or i["id"] == item["id"] for i in scheduler.items())


def test_lancer_button_needs_the_page_token_holds(fake_claude):
    import server
    pending = tools.run_tool("delegate_to_claude", COMPLET, ctx())["pending_id"]
    url = f"/api/pending/{pending}/decide"
    bare = TestClient(server.app, base_url="http://127.0.0.1:8788")
    assert bare.post(url, json={"decision": "oui"}).status_code == 401
    assert bare.post(url, json={"decision": "oui"}, headers={"X-Jarvis-Token": "x" * 43}).status_code == 401
    page = {"X-Jarvis-Token": security.TOKEN}
    assert bare.post(url, json={"decision": "oui"},
                     headers={**page, "Origin": "https://evil.example"}).status_code == 403
    rebound = TestClient(server.app, base_url="http://evil.example:8788", headers=page)
    assert rebound.post(url, json={"decision": "oui"}).status_code == 403
    assert not tasks.TASKS and confirm.PENDING[pending]["state"] == "pending"
    body = bare.post(url, json={"decision": "oui"}, headers=page).json()
    assert body["state"] == "done"
    wait(tasks.TASKS[body["result"]["task_id"]])


# ---------------------------------------------------------------- 2. the model cannot say "oui" for monsieur

FORBIDDEN_PARAMS = {"session_id", "session", "sid", "voice_session", "last_turn", "turn", "by_voice",
                    "approve", "approved", "approval", "allowed_tools", "allowedtools", "confirmed",
                    "force", "skip_confirmation", "bypass"}


def _param_names(schema) -> set:
    names = set()
    if isinstance(schema, dict):
        for key, value in (schema.get("properties") or {}).items():
            names.add(key.lower())
            names |= _param_names(value)
        names |= _param_names(schema.get("items"))
    return names


def _calls(func_name: str):
    """(file, enclosing function, call node) for every call of func_name in the product code."""
    found = []
    for path in [*sorted((ROOT / "jarvis").glob("*.py")), ROOT / "server.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if callee == func_name:
                        found.append((path.name, getattr(fn, "name", "<lambda>"), node))
    # A call inside a nested function is seen from every enclosing one: keep the innermost.
    unique = {}
    for file, fn, node in found:
        unique[(file, node.lineno, node.col_offset)] = (file, fn, node)
    return list(unique.values())


def test_no_tool_schema_can_name_a_session_or_an_approval_holds(monkeypatch):
    all_families_on(monkeypatch)
    for tool in tools.session_tools():
        params = _param_names(tool.get("parameters"))
        assert not params & FORBIDDEN_PARAMS, (tool["name"], params & FORBIDDEN_PARAMS)
        assert "approv" not in tool["name"] and "turn" not in tool["name"], tool["name"]
    [confirm_tool] = [t for t in tools.session_tools() if t["name"] == "confirm_action"]
    assert set(confirm_tool["parameters"]["properties"]) == {"pending_id", "decision"}


def test_only_the_page_routes_register_a_turn_or_decide_holds():
    """Read the product code: who may mark a turn, decide, or approve a task."""
    turns = {(f, fn) for f, fn, _ in _calls("mark_turn")}
    assert turns == {("confirm.py", "voice_turn")}  # POST /api/voice/turn, called by the page
    decides = {(f, fn): node for f, fn, node in _calls("decide")}
    assert set(decides) == {("confirm.py", "_confirm_action"), ("confirm.py", "decide_route")}
    by_voice = {k.arg: k.value for k in decides[("confirm.py", "_confirm_action")].keywords}
    assert isinstance(by_voice.get("by_voice"), ast.Constant) and by_voice["by_voice"].value is True
    assert not decides[("confirm.py", "decide_route")].keywords  # the button: no session to fake
    # last_turn is written in one place only.
    writers = set()
    for path in sorted((ROOT / "jarvis").glob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(fn, ast.FunctionDef):
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Assign) and any(
                            isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                            and t.slice.value == "last_turn" for t in node.targets)):
                        writers.add((path.name, fn.name))
    assert writers == {("confirm.py", "mark_turn")}


def test_the_page_reports_a_turn_only_for_speech_typing_or_push_to_talk_holds():
    """Read the page's code: one module posts /api/voice/turn, and only from
    monsieur's speech ending, his typed text and the push-to-talk release."""
    js = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "static" / "js").glob("*.js")}
    assert [name for name, src in js.items() if "/api/voice/turn" in src] == ["voice.js"]
    src = js["voice.js"]
    assert src.count("/api/voice/turn") == 1
    where = set()
    scope = re.compile(r'(?:function\s+(\w+)\s*\(|case\s+"([\w.]+)"\s*:)')
    for call in re.finditer(r"\breportTurn\(\)", src):
        if src[max(0, call.start() - 9):call.start()] == "function ":
            continue  # its definition
        enclosing = list(scope.finditer(src, 0, call.start()))[-1]
        where.add(enclosing.group(1) or enclosing.group(2))
    assert where == {"sendText", "input_audio_buffer.speech_stopped", "pttUp"}
    # sendText is monsieur's words: the composer, the wake word and what he typed
    # before the session opened; never a tool result or a server event.
    callers = {name for name, text in js.items() if re.search(r"\bsendText\(", text)}
    assert callers <= {"composer.js", "wake.js", "voice.js"}


def test_no_voice_tool_registers_a_turn_or_decides_a_request_holds(fake_claude, nothing_real, clock,
                                                                   monkeypatch):
    """Every server tool, called with every argument a model could invent to pass
    for monsieur: his last turn and the open requests stay as they were."""
    all_families_on(monkeypatch)
    approved = []
    monkeypatch.setattr(tasks, "approve", lambda task_id: approved.append(task_id))
    sid = confirm.new_session()
    pending = tools.run_tool("delegate_to_claude", COMPLET, ctx(sid))["pending_id"]
    approval = confirm._park("approve_task", {"task_id": "t1"}, sid, "Claude demande Bash", "- rm",
                             kind="task_approval", task_id="t1")["id"]
    turn_before = confirm.SESSIONS[sid]["last_turn"]
    sensible = {
        "delegate_to_claude": {"title": "t", "prompt": "p", "profile": "lecture"},
        "cancel_task": {"task_id": "nope"},
        "open_app": {"name": "bloc-notes"},
        "open_url": {"url": "https://example.org"},
        "system_control": {"action": "volume_up"},
        "schedule": {"kind": "reminder", "title": "Thé", "text": "Thé prêt", "delay_minutes": 600},
        "cancel_schedule": {"query": "zzz"},
        "remember": {"fact": "Monsieur aime le thé"},
        "forget": {"query": "zzz"},
    }
    forged = {"session_id": sid, "voice_session": sid, "sid": sid, "pending_id": pending,
              "decision": "oui", "by_voice": False, "last_turn": 9e18, "turn": True,
              "task_id": "t1", "approve": True, "confirmed": True}
    names = sorted(tools.handlers())
    assert {"confirm_action", "delegate_to_claude", "open_url", "system_control"} <= set(names)
    for name in names:
        for target in (pending, approval):
            clock[0] += 1
            out = tools.run_tool(name, {**sensible.get(name, {}), **forged, "pending_id": target}, ctx(sid))
            if name == "confirm_action":
                assert out == {"ok": False, "error": REFUSED}
    assert confirm.SESSIONS[sid]["last_turn"] == turn_before
    assert confirm.PENDING[pending]["state"] == "pending"
    assert confirm.PENDING[approval]["state"] == "pending"
    assert approved == [] and not complet_tasks()


def test_tool_route_ignores_a_session_named_in_the_arguments_holds(fake_claude, client, clock):
    asking, other = confirm.new_session(), confirm.new_session()
    pending = tools.run_tool("delegate_to_claude", COMPLET, ctx(asking))["pending_id"]
    clock[0] += 1
    confirm.mark_turn(asking)  # a "oui" from the asking session would now count
    clock[0] += 1
    for session_id in (other, None, ""):
        body = {"name": "confirm_action", "session_id": session_id,
                "arguments": {"pending_id": pending, "decision": "oui", "session_id": asking}}
        assert client.post("/api/tool", json=body).json() == {"ok": False, "error": REFUSED}
    # The turn route belongs to the page: without its token, no turn.
    import server
    bare = TestClient(server.app, base_url="http://127.0.0.1:8788")
    before = confirm.SESSIONS[other]["last_turn"]
    assert bare.post("/api/voice/turn", json={"session_id": other}).status_code == 401
    assert confirm.SESSIONS[other]["last_turn"] == before
    assert confirm.PENDING[pending]["state"] == "pending" and not tasks.TASKS


# ---------------------------------------------------------------- 3. decided once, as asked

def test_pending_action_runs_at_most_once_under_concurrent_yeses_holds(fake_claude, clock):
    sid = confirm.new_session()
    pending = tools.run_tool("delegate_to_claude", COMPLET, ctx(sid))["pending_id"]
    clock[0] += 1
    confirm.mark_turn(sid)
    clock[0] += 1
    start = threading.Barrier(8)
    results = []

    def yes(by_voice):
        start.wait()
        results.append(confirm.decide(pending, "oui", voice_session=sid if by_voice else None,
                                      by_voice=by_voice))

    threads = [threading.Thread(target=yes, args=(i % 2 == 0,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == 8
    winners = [r for r in results if r.get("ok")]
    assert len(winners) == 1 and winners[0]["state"] == "done"
    assert all(r == {"ok": False, "error": "Demande déjà traitée.", "state": r["state"]}
               for r in results if r is not winners[0])
    assert len(tasks.TASKS) == 1
    wait(next(iter(tasks.TASKS.values())))


def test_decided_or_expired_request_never_runs_again_holds(fake_claude, clock, client):
    sid = confirm.new_session()
    # Cancelled: a later "oui" (button or voice after a fresh turn) does nothing.
    dropped = tools.run_tool("delegate_to_claude", COMPLET, ctx(sid))["pending_id"]
    assert confirm_by_voice(dropped, sid, "non")["state"] == "cancelled"
    clock[0] += 1
    confirm.mark_turn(sid)
    clock[0] += 1
    assert confirm_by_voice(dropped, sid)["error"] == "Demande déjà traitée."
    assert client.post(f"/api/pending/{dropped}/decide", json={"decision": "oui"}).json()["state"] == "cancelled"
    # Expired: neither way runs it.
    late = tools.run_tool("delegate_to_claude", {**COMPLET, "title": "Autre"}, ctx(sid))["pending_id"]
    clock[0] += config.PENDING_TTL + 1
    confirm.mark_turn(sid)
    clock[0] += 1
    assert confirm_by_voice(late, sid)["state"] == "expired"
    assert client.post(f"/api/pending/{late}/decide", json={"decision": "oui"}).json()["state"] == "expired"
    assert client.post(f"/api/pending/{late}/decide", json={"decision": "non"}).json()["state"] == "expired"
    assert not tasks.TASKS
    # Done: once, then "déjà traitée", then forgotten (unknown): never twice.
    done = tools.run_tool("delegate_to_claude", {**COMPLET, "title": "Encore"}, ctx(sid))["pending_id"]
    assert client.post(f"/api/pending/{done}/decide", json={"decision": "oui"}).json()["state"] == "done"
    assert client.post(f"/api/pending/{done}/decide", json={"decision": "oui"}).json()["state"] == "done"
    clock[0] += confirm.KEEP_DECIDED_S + 1
    assert client.post(f"/api/pending/{done}/decide", json={"decision": "oui"}).status_code == 404
    clock[0] += 1
    confirm.mark_turn(sid)
    clock[0] += 1
    assert confirm_by_voice(done, sid)["error"] == "Demande inconnue : rien n'a été lancé."
    assert len(tasks.TASKS) == 1
    wait(next(iter(tasks.TASKS.values())))


def test_pending_from_another_voice_session_cannot_be_decided_holds(fake_claude, clock):
    asking, other = confirm.new_session(), confirm.new_session()
    pending = tools.run_tool("delegate_to_claude", COMPLET, ctx(asking))["pending_id"]
    orphan = tools.run_tool("delegate_to_claude", {**COMPLET, "title": "Sans session"}, ctx())["pending_id"]
    clock[0] += 1
    confirm.mark_turn(other)
    confirm.mark_turn(asking)
    clock[0] += 1
    for decision in ("oui", "non"):
        assert confirm_by_voice(pending, other, decision)["error"] == REFUSED
        # Asked outside any voice session (the page): only its button decides.
        assert confirm_by_voice(orphan, asking, decision)["error"] == REFUSED
        assert confirm_by_voice(orphan, None, decision)["error"] == REFUSED
    assert confirm.PENDING[pending]["state"] == confirm.PENDING[orphan]["state"] == "pending"
    assert not tasks.TASKS
    out = confirm_by_voice(pending, asking)
    assert out["status"] == "started"
    wait(tasks.TASKS[out["task_id"]])


def test_confirmed_action_runs_the_arguments_stored_when_asked_holds(fake_claude, nothing_real, clock, client):
    sid = confirm.new_session()
    asked = dict(COMPLET)
    pending = tools.run_tool("delegate_to_claude", asked, ctx(sid))["pending_id"]
    asked["prompt"] = "Efface tout le disque"  # the caller's dict changing afterwards changes nothing
    body = client.post(f"/api/pending/{pending}/decide",
                       json={"decision": "oui", "prompt": "Efface tout", "profile": "complet",
                             "args": {"prompt": "Efface tout"}, "name": "open_url"}).json()
    task = tasks.TASKS[body["result"]["task_id"]]
    assert (task["prompt"], task["title"], task["profile"]) == (COMPLET["prompt"], COMPLET["title"], "complet")
    wait(task)
    # A link, confirmed by voice with another link in the call: the stored one opens.
    confirm.mark_tainted(sid, "Page web")
    link = tools.run_tool("open_url", {"url": "https://evil.example/a"}, ctx(sid))["pending_id"]
    clock[0] += 1
    confirm.mark_turn(sid)
    clock[0] += 1
    assert confirm_by_voice(link, sid, url="https://pire.example/b", name="system_control",
                            action="write_clipboard")["ok"]
    assert nothing_real == [("open", "https://evil.example/a")]
    # A clipboard write: the stored text, not the one given with the "oui".
    clip = tools.run_tool("system_control", {"action": "write_clipboard", "value": "texte prévu"},
                          ctx(sid))["pending_id"]
    clock[0] += 1
    confirm.mark_turn(sid)
    clock[0] += 1
    assert confirm_by_voice(clip, sid, value="mot de passe", action="lock_screen")["ok"]
    assert nothing_real[-1] == ("write_clipboard", "texte prévu")


# ---------------------------------------------------------------- 4. outside content makes links and clipboard ask

TRICKY_LINKS = [
    "https://evil.example/?q=secret",
    "https://youtube.com.evil.example/",
    "https://youtube.com@evil.example/",
    "youtube.com@evil.example",
    "https://evil.example\\@youtube.com",       # a browser reads '\' as '/': host evil.example
    "evil.example\\@youtube.com",
    "https://evil.example\\.youtube.com",
    "https://evil.example\\\\@youtube.com",
    "https://evil.example#@youtube.com",
    "https://evil.example?.youtube.com",
    "https://evil.example/@youtube.com",
    "HTTPS://EVIL.EXAMPLE/watch",
    "javascript:alert(1)",
    "file:///C:/Windows/System32/calc.exe",
    "",
]


def test_outside_content_makes_unknown_links_and_clipboard_writes_ask_holds(nothing_real, client, monkeypatch):
    monkeypatch.setattr(config, "OPEN_URL_ALLOW", "youtube.com, mail.google.com")
    sid, bystander = confirm.new_session(), confirm.new_session()
    clip = {"action": "write_clipboard", "value": "texte"}
    # Before any outside content: they run.
    assert tools.run_tool("open_url", {"url": "https://evil.example/?q=secret"}, ctx(sid))["ok"]
    assert tools.run_tool("system_control", clip, ctx(sid))["ok"]
    assert len(nothing_real) == 2
    # voice.sendData reports outside text: from then on they ask first.
    assert client.post("/api/voice/taint", json={"session_id": sid, "reason": "Page web"}).json()["ok"]
    for url in TRICKY_LINKS:
        out = tools.run_tool("open_url", {"url": url}, ctx(sid))
        assert out.get("status") == "needs_confirmation", url
    out = tools.run_tool("system_control", clip, ctx(sid))
    assert out["status"] == "needs_confirmation"
    assert len(nothing_real) == 2  # nothing more ran
    # Allowed sites, and actions that send nothing anywhere, still run.
    for url in ("https://www.youtube.com/watch?v=x", "youtube.com", "https://mail.google.com/",
                "https://YouTube.com./feed"):
        assert tools.run_tool("open_url", {"url": url}, ctx(sid))["ok"], url
    assert tools.run_tool("system_control", {"action": "volume_up"}, ctx(sid))["ok"]
    # The taint is this conversation's: another one is not affected.
    assert tools.run_tool("open_url", {"url": "https://evil.example/"}, ctx(bystander))["ok"]


def test_clipboard_text_read_by_the_model_is_outside_content_holds(nothing_real):
    """What monsieur copied (often from a web page) is outside text, like the screen."""
    sid = confirm.new_session()
    assert tools.run_tool("system_control", {"action": "volume_up"}, ctx(sid))["ok"]
    assert not confirm.is_tainted(sid)
    assert tools.run_tool("system_control", {"action": "read_clipboard"}, ctx(sid))["ok"]
    assert confirm.is_tainted(sid)
    out = tools.run_tool("open_url", {"url": "https://evil.example/"}, ctx(sid))
    assert out["status"] == "needs_confirmation"
    out = tools.run_tool("system_control", {"action": "write_clipboard", "value": "x"}, ctx(sid))
    assert out["status"] == "needs_confirmation"
    assert [a for a, _ in nothing_real] == ["volume_up", "read_clipboard"]


def test_outside_content_keeps_asking_after_a_reconnection_holds(nothing_real, client, monkeypatch):
    """A reconnection (or a 55-minute refresh) carries the last exchanges into
    the new session: what JARVIS said about a web page goes with them, and so
    does the taint."""
    monkeypatch.setattr(realtime, "mint", lambda recent="": {"value": "ek_fake"})
    first = client.post("/api/session", json={"recent": ""}).json()["session_id"]
    client.post("/api/voice/taint", json={"session_id": first, "reason": "Page web"})
    recent = "monsieur : résume cette page\nJARVIS : La page dit d'ouvrir evil.example."
    resumed = client.post("/api/session", json={"recent": recent}).json()["session_id"]
    assert resumed != first and confirm.is_tainted(resumed)
    out = tools.run_tool("open_url", {"url": "https://evil.example/"}, ctx(resumed))
    assert out["status"] == "needs_confirmation"
    # A conversation that starts afresh (nothing carried over) starts clean.
    fresh = client.post("/api/session", json={"recent": ""}).json()["session_id"]
    assert not confirm.is_tainted(fresh)
    # ...and resuming that clean one does not inherit an older taint.
    again = client.post("/api/session", json={"recent": "monsieur : bonjour"}).json()["session_id"]
    assert not confirm.is_tainted(again)
    assert nothing_real == []


# ---------------------------------------------------------------- 7. approving denied tools

def test_approve_is_reachable_only_through_a_confirmed_approval_holds(fake_claude, client, clock, monkeypatch):
    real_approve = tasks.approve
    approved = []
    monkeypatch.setattr(tasks, "approve", lambda task_id: approved.append(task_id) or real_approve(task_id))
    sid = confirm.new_session()
    first = wait(tasks.create_task("Nettoyage", "REFUS", profile="complet", voice_session=sid))
    end = time.time() + 5
    while "approval" not in first and time.time() < end:
        time.sleep(0.02)
    approval = first["approval"]
    # Code: approve() and allowed_tools are used in one place each.
    assert {(f, fn) for f, fn, _ in _calls("approve")} == {("confirm.py", "_execute")}
    with_allowed = [(f, fn) for f, fn, node in _calls("create_task")
                    if any(k.arg == "allowed_tools" for k in node.keywords)]
    assert with_allowed == [("tasks.py", "approve")]
    # No tool, route or argument reaches it.
    all_families_on(monkeypatch)
    assert not [n for n in tools.handlers() if "approv" in n]
    import server
    assert not [r.path for r in server.app.routes if "approv" in getattr(r, "path", "")]
    for name in ("approve_task", "approve"):
        assert tools.run_tool(name, {"task_id": first["id"]}, ctx(sid))["ok"] is False
        r = client.post("/api/tool", json={"name": name, "arguments": {"task_id": first["id"]}, "session_id": sid})
        assert r.json()["ok"] is False
    keyed = client.post("/api/tasks", json={"prompt": "Continue", "continue_task": first["id"],
                                            "allowed_tools": ["Bash"], "allowedTools": "Bash"}).json()
    voiced = tools.run_tool("delegate_to_claude", {"title": "Suite", "prompt": "Continue", "profile": "lecture",
                                                   "continue_task": first["id"], "allowed_tools": ["Bash"]},
                            ctx(sid))
    for task_id in (keyed["id"], voiced["task_id"]):
        task = wait(tasks.TASKS[task_id])
        assert "allowed_tools" not in task
        assert "--allowedTools" not in json.loads(task["output"])["argv"]
    # By voice: the same rules as any request (a new turn of that conversation).
    assert confirm_by_voice(approval, sid)["error"] == REFUSED
    clock[0] += 1
    confirm.mark_turn(confirm.new_session())
    clock[0] += 1
    assert confirm_by_voice(approval, sid)["error"] == REFUSED
    assert approved == []
    # The button: approve() runs, once.
    body = client.post(f"/api/pending/{approval}/decide", json={"decision": "oui"}).json()
    assert body["state"] == "done" and approved == [first["id"]]
    assert client.post(f"/api/pending/{approval}/decide", json={"decision": "oui"}).json()["state"] == "done"
    assert approved == [first["id"]]
    wait(tasks.TASKS[body["result"]["task_id"]])
