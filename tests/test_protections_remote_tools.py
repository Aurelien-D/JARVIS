"""Protections of the iPhone's tools, confirmations and voice sessions (spec
4.11, section 8 rows 19 to 24b): what a paired phone or Siri may do is decided
on the server, with the effective origin carried into every handler; full
access from the phone needs the PC's opt-in, a clean conversation and the
phone's own [Lancer]; PC actions wait for that button; a phone never sets up a
full-access routine; sessions stay with their origin; Siri never gets a card.

Until the remote core lands, the phone is a stamped caller (remote_helpers.as_caller)
and remote.complet_allowed, remote.origin_active and audit.alert are faked here.
Fictitious names only: the repository is public."""
import sys
import time

import pytest
from fastapi.testclient import TestClient
from remote_helpers import IP, LOGIN, as_caller, remote_client
from test_tasks import FAKE_CLAUDE, wait

from jarvis import (
    audit,
    config,
    confirm,
    desktop,
    realtime,
    remote,
    scheduler,
    security,
    store,
    tasks,
    tools,
)

PHONE = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip=IP, login=LOGIN, name="iPhone de test")
OTHER = remote.Caller(kind="app", device_id="d_fedcba9876543210", ip=IP, login=LOGIN, name="iPad de test")
SIRI = remote.Caller(kind="siri", device_id=PHONE.device_id, key_id="k_9b8a7c6d5e4f3a21", ip=IP, login=LOGIN)
APP = PHONE.origin

COMPLET = {"title": "Ranger les téléchargements", "prompt": "Range ~/Downloads par type", "profile": "complet"}
ROUTINE = {"kind": "task", "title": "Ménage", "text": "Vide la corbeille chaque matin", "at": "08:00",
           "repeat": "daily", "profile": "complet"}
RESEARCH = {"title": "Veille", "prompt": "Cherche les nouveautés du jour", "profile": "recherche"}

T = confirm.T


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    for store_ in (confirm.PENDING, confirm.SESSIONS, confirm.FORGOTTEN):
        store_.clear()
    monkeypatch.setattr(config, "CONFIRM_COMPLET", True)
    monkeypatch.setattr(config, "PENDING_TTL", 90)
    monkeypatch.setattr(config, "OPEN_URL_ALLOW", "")
    yield
    for store_ in (confirm.PENDING, confirm.SESSIONS, confirm.FORGOTTEN):
        store_.clear()


@pytest.fixture
def optin(monkeypatch):
    """The PC's « Accès complet depuis l'iPhone » opt-in (A1's state), flipped by hand."""
    state = {"open": True}
    monkeypatch.setattr(remote, "complet_allowed", lambda now=None: state["open"])
    return state


@pytest.fixture
def alerts(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "alert", lambda kind, text, caller=None, **fields: seen.append(
        {"kind": kind, "text": text, "caller": caller, **fields}))
    return seen


@pytest.fixture
def audited(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "event", lambda caller, kind, **fields: seen.append((caller, kind, fields)))
    return seen


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)


@pytest.fixture
def nothing_real(monkeypatch):
    """Nothing opens, locks or grabs anything on this PC: the list says what would have run."""
    done = []
    monkeypatch.setattr(desktop, "open_target", lambda **kw: done.append(("open", kw.get("url") or kw.get("name")))
                        or {"ok": True})
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: done.append((action, value))
                        or {"ok": True})
    monkeypatch.setattr(desktop, "screenshot_jpeg", lambda monitor=None: done.append(("screen", monitor))
                        or b"\xff\xd8jpeg")
    return done


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(confirm, "_now", lambda: now[0])
    return now


@pytest.fixture
def pc():
    import server
    return TestClient(server.app, base_url="http://127.0.0.1:8788", headers={"X-Jarvis-Token": security.TOKEN})


@pytest.fixture
def as_device(monkeypatch):
    """A client arriving on the Serve port as this caller (the stamped gate)."""
    def client(caller=PHONE):
        as_caller(monkeypatch, caller)
        return remote_client()
    return client


def tool_call(client, name, args, sid):
    return client.post("/api/tool", json={"name": name, "arguments": args, "session_id": sid})


def decide(client, pending_id, decision="oui"):
    return client.post(f"/api/pending/{pending_id}/decide", json={"decision": decision}).json()


def complet_tasks():
    """Full-access tasks started during the test (not the finished one a retry starts from)."""
    return [t for t in tasks.TASKS.values() if t["profile"] == "complet" and t["id"] != "t-old"]


# ---------------------------------------------------------------- 19. the allowlist

def test_remote_tool_allowlist_is_enforced_by_the_server_holds(as_device, nothing_real, monkeypatch):
    sid = confirm.new_session(origin=APP)
    phone = as_device()
    refused = [("open_app", {"name": "notepad"}), ("look_at_screen", {}),
               ("system_control", {"action": "read_clipboard"}),
               ("system_control", {"action": "write_clipboard", "value": "x"}),
               ("system_control", {"action": "save_screenshot"}), ("system_control", {"action": "Volume_up"}),
               ("system_control", {"action": ["mute"]}), ("teleport", {})]
    for name, args in refused:
        r = tool_call(phone, name, args, sid)
        assert r.status_code == 200 and r.json()["ok"] is False, (name, args)
        assert "PC" in r.json()["error"], (name, args)
    assert tool_call(phone, "open_app", {"name": "notepad"}, sid).json()["error"] == \
        "Depuis l'iPhone, « open_app » n'est pas disponible : faites-le depuis le PC."
    assert tool_call(phone, "look_at_screen", {}, sid).json()["error"] == \
        "Depuis l'iPhone, « look_at_screen » n'est pas disponible : faites-le depuis le PC."
    assert tool_call(phone, "system_control", {"action": "read_clipboard"}, sid).json()["error"] == \
        "Presse-papiers et captures d'écran restent réservés au PC."
    # The effective origin: a caller that claims to be the PC, in the phone's
    # session, is the phone (the gate, the handler and after_tool see it).
    for name, args in refused[:5]:
        assert tools.run_tool(name, args, tools.ToolCtx(sid))["ok"] is False, name
    assert nothing_real == [] and not confirm.is_tainted(sid)
    seen = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **kw: seen.append(kw)
                        or {"id": "t1", "status": "running", "profile": "lecture", "model": ""})
    tools.run_tool("delegate_to_claude", {"title": "x", "prompt": "lis", "profile": "lecture"}, tools.ToolCtx(sid))
    assert seen[-1]["via"] == APP and seen[-1]["voice_session"] == sid
    # Siri: even less.
    siri_sid = confirm.new_session(origin=SIRI.origin)
    for name, args in [("open_app", {"name": "notepad"}), ("system_control", {"action": "mute"}),
                       ("remember", {"fact": "x"}), ("open_url", {"url": "https://example.org"})]:
        assert tools.run_tool(name, args, tools.ToolCtx(siri_sid, origin=SIRI.origin))["ok"] is False
    assert nothing_real == []
    # The phone's voice session is never offered them.
    payload = realtime.session_payload(scope="app")["session"]
    names = {t["name"] for t in payload["tools"]}
    assert not names & {"open_app", "look_at_screen"}
    assert names <= tools.APP_TOOLS | tools.client_tools()
    assert {"display_card", "display_report", "end_conversation", "wait_for_user"} <= names
    [system] = [t for t in payload["tools"] if t["name"] == "system_control"]
    assert system["parameters"]["properties"]["action"]["enum"] == sorted(tools.REMOTE_PC_ACTIONS)
    assert "[Lancer]" in system["description"]
    [link] = [t for t in payload["tools"] if t["name"] == "open_url"]
    assert link["description"] == "Send a link to monsieur's iPhone as a tappable card; nothing opens on the PC."
    # ...while the PC keeps every one of them, its schemas untouched.
    pc_tools = realtime.session_payload()["session"]["tools"]
    assert {"open_app", "look_at_screen"} <= {t["name"] for t in pc_tools}
    [pc_system] = [t for t in pc_tools if t["name"] == "system_control"]
    assert "write_clipboard" in pc_system["parameters"]["properties"]["action"]["enum"]
    assert tools.run_tool("open_app", {"name": "notepad"}, tools.ToolCtx(confirm.new_session()))["ok"]


# ---------------------------------------------------------------- 20. full access from the phone

def test_complet_from_the_phone_is_refused_unless_opted_in_on_the_pc_holds(as_device, optin, fake_claude,
                                                                             monkeypatch, published):
    sid = confirm.new_session(origin=APP)
    phone = as_device()
    optin["open"] = False
    for confirm_complet in (True, False):  # the PC's own setting changes nothing here
        monkeypatch.setattr(config, "CONFIRM_COMPLET", confirm_complet)
        out = tool_call(phone, "delegate_to_claude", COMPLET, sid).json()
        assert out == {"ok": False, "error": T.complet_closed}
    assert phone.post("/api/tasks", json={"prompt": "Range", "profile": "complet"}).status_code == 400
    old = {"id": "t-old", "title": "Doublons", "prompt": "Supprime les doublons", "profile": "complet",
           "complexity": "simple", "origin": "voix", "status": "error", "output": "", "started": time.time() - 60,
           "ended": time.time(), "session_id": "s-old", "resumed_from": None, "files": [], "permission_denials": []}
    tasks.TASKS[old["id"]] = old
    r = phone.post(f"/api/task/{old['id']}/retry")
    assert r.status_code == 403 and r.json()["detail"] == T.complet_closed
    assert not confirm.PENDING and not complet_tasks()
    assert not [e for e in published if e["type"] == "pending"]
    # Opted in, but the conversation read outside content: refused, nothing parked.
    optin["open"] = True
    tainted = confirm.new_session(origin=APP)
    confirm.mark_tainted(tainted, "résultat de tâche")
    assert tool_call(phone, "delegate_to_claude", COMPLET, tainted).json() == {"ok": False, "error": T.complet_tainted}
    assert not confirm.PENDING
    # Opted in and clean: a card, launched only by this phone's button, even with CONFIRM_COMPLET=0.
    monkeypatch.setattr(config, "CONFIRM_COMPLET", False)
    out = tool_call(phone, "delegate_to_claude", COMPLET, sid).json()
    assert out["status"] == "needs_confirmation"
    assert out["summary"] == "Depuis l'iPhone, confier à Claude avec accès complet : « Ranger les téléchargements »."
    [card] = phone.get("/api/pending").json()
    assert (card["via"], card["button_only"], card["launch_from"], card["remote_kind"]) == \
        (APP, True, APP, "complet")
    assert "tainted" not in card and "sid" not in card
    retried = phone.post(f"/api/task/{old['id']}/retry").json()
    assert retried["status"] == "needs_confirmation" and confirm.PENDING[retried["pending_id"]]["button_only"]
    assert not complet_tasks()
    # Siri: never, opted in or not; the PC with CONFIRM_COMPLET=0 runs at once (its own choice).
    siri_sid = confirm.new_session(origin=SIRI.origin)
    assert tools.run_tool("delegate_to_claude", COMPLET, tools.ToolCtx(siri_sid, origin=SIRI.origin))["ok"] is False
    assert confirm.gate("delegate_to_claude", COMPLET, tools.ToolCtx(siri_sid, origin=SIRI.origin))["ok"] is False
    assert len(confirm.PENDING) == 2
    started = tools.run_tool("delegate_to_claude", COMPLET, tools.ToolCtx(confirm.new_session()))
    assert started["status"] == "started" and started["profile"] == "complet"
    wait(tasks.TASKS[started["task_id"]])


def test_opted_in_complet_runs_only_on_the_phone_button_never_by_voice_holds(as_device, optin, alerts, audited,
                                                                               fake_claude, pc, clock):
    sid = confirm.new_session(origin=APP)
    phone = as_device()
    clock[0] += 1
    assert phone.post("/api/voice/turn", json={"session_id": sid}).json()["ok"]  # monsieur's request
    clock[0] += 1
    pending = tool_call(phone, "delegate_to_claude", COMPLET, sid).json()["pending_id"]
    clock[0] += 1
    assert phone.post("/api/voice/turn", json={"session_id": sid}).json()["ok"]  # « oui »
    clock[0] += 1
    # By voice, even a fresh "oui" in the very conversation that asked: no.
    out = tools.run_tool("confirm_action", {"pending_id": pending, "decision": "oui"}, tools.ToolCtx(sid, origin=APP))
    assert out == {"ok": False, "error": T.button_only}
    # Neither the PC's button, nor another phone's, nor Siri.
    assert decide(pc, pending) == {"ok": False, "error": T.from_phone}
    assert decide(as_device(OTHER), pending) == {"ok": False, "error": T.other_device}
    assert confirm.decide(pending, "oui", origin=SIRI.origin) == {"ok": False, "error": T.refused}
    assert not complet_tasks() and confirm.PENDING[pending]["state"] == "pending" and alerts == []
    # The phone's own button: it starts, for the phone, and the PC is alerted.
    body = decide(as_device(), pending)
    assert body["state"] == "done"
    [task] = complet_tasks()
    assert task["prompt"] == COMPLET["prompt"] and task["via"] == APP and task["voice_session"] == sid
    assert [a["kind"] for a in alerts] == ["remote_complet"]
    assert "Ranger les téléchargements" in alerts[0]["text"] and alerts[0]["caller"].device_id == PHONE.device_id
    assert ("decide", {"pending": pending, "decision": "oui"}) in [(k, f) for _, k, f in audited]
    wait(task)
    # Every launch is alerted (never deduplicated).
    again = tool_call(as_device(), "delegate_to_claude", {**COMPLET, "title": "Encore"}, sid).json()["pending_id"]
    assert decide(as_device(), again)["state"] == "done"
    assert [a["kind"] for a in alerts] == ["remote_complet", "remote_complet"]
    for t in complet_tasks():
        wait(t)


def test_decide_rechecks_the_optin_and_the_taint_holds(as_device, optin, alerts, fake_claude, clock):
    sid = confirm.new_session(origin=APP)
    phone = as_device()
    pending = tool_call(phone, "delegate_to_claude", COMPLET, sid).json()["pending_id"]
    # The opt-in closed (it expired, or the PC said Jamais) between the card and the click.
    optin["open"] = False
    assert decide(phone, pending) == {"ok": False, "error": T.complet_closed}
    optin["open"] = True
    # Outside content entered the conversation after the card was raised.
    confirm.mark_tainted(sid, "résultat de tâche")
    assert decide(phone, pending) == {"ok": False, "error": T.complet_tainted}
    assert not complet_tasks() and alerts == [] and confirm.PENDING[pending]["state"] == "pending"
    # A session evicted before the click counts as tainted: its taint is no longer known.
    clock[0] += 1
    first = confirm.new_session(origin=APP)
    asked = tool_call(phone, "delegate_to_claude", {**COMPLET, "title": "Premier"}, first).json()["pending_id"]
    for i in range(confirm.MAX_REMOTE_SESSIONS):  # every one busy with a card: the oldest goes
        clock[0] += 1
        busy = confirm.new_session(origin=APP)
        assert tool_call(phone, "system_control", {"action": "mute"}, busy).json()["status"] == "needs_confirmation"
    assert first not in confirm.SESSIONS and not confirm.is_tainted(first)  # clean when it went
    assert decide(phone, asked) == {"ok": False, "error": T.complet_tainted}
    # An unknown session is refused at park time too.
    assert tools.run_tool("delegate_to_claude", COMPLET, tools.ToolCtx("0" * 32, origin=APP)) == \
        {"ok": False, "error": T.complet_tainted}
    assert not complet_tasks() and alerts == []


# ---------------------------------------------------------------- 21. PC actions and links

def test_pc_actions_from_the_phone_wait_for_the_phone_button_holds(as_device, nothing_real, pc, clock):
    sid = confirm.new_session(origin=APP)
    phone = as_device()
    asked = {}
    for args, what in [({"action": "volume_up"}, "monter le son du PC"),
                       ({"action": "set_volume", "value": "30"}, "régler le volume du PC à 30 %"),
                       ({"action": "lock_screen"}, "verrouiller la session du PC"),
                       ({"action": "play_pause"}, "lecture ou pause sur le PC")]:
        out = tool_call(phone, "system_control", args, sid).json()
        assert out["status"] == "needs_confirmation", args
        assert out["summary"] == f"Agir sur le PC à distance : {what} ?"
        p = confirm.PENDING[out["pending_id"]]
        assert (p["button_only"], p["remote_kind"], p["via"]) == (True, "pc_action", APP)
        asked[args["action"]] = out["pending_id"]
    assert nothing_real == []  # nothing ran yet, not even in a clean conversation
    clock[0] += 1
    phone.post("/api/voice/turn", json={"session_id": sid})
    clock[0] += 1
    assert tools.run_tool("confirm_action", {"pending_id": asked["lock_screen"], "decision": "oui"},
                          tools.ToolCtx(sid, origin=APP)) == {"ok": False, "error": T.button_only}
    assert decide(pc, asked["lock_screen"]) == {"ok": False, "error": T.from_phone}
    assert nothing_real == []
    assert decide(phone, asked["set_volume"])["state"] == "done"
    assert decide(phone, asked["lock_screen"])["state"] == "done"
    assert nothing_real == [("set_volume", "30"), ("lock_screen", None)]
    # The PC itself: at once, as before.
    assert tools.run_tool("system_control", {"action": "volume_up"}, tools.ToolCtx(confirm.new_session()))["ok"]
    assert nothing_real[-1] == ("volume_up", None)


def test_open_url_from_the_phone_never_opens_on_the_pc_holds(as_device, nothing_real, monkeypatch):
    sid = confirm.new_session(origin=APP)
    phone = as_device()
    out = tool_call(phone, "open_url", {"url": "https://example.org/page?x=1"}, sid).json()
    assert out == {"ok": True, "opened": False, "link": "https://example.org/page?x=1", "domain": "example.org",
                   "tainted": False, "message": "Lien envoyé sur l'iPhone : touchez la carte pour l'ouvrir."}
    assert tool_call(phone, "open_url", {"url": "youtube.com/watch"}, sid).json()["link"] == "https://youtube.com/watch"
    for bad in ("javascript:alert(1)", "file:///C:/Windows/System32/calc.exe", "", "data:text/html,x"):
        assert tool_call(phone, "open_url", {"url": bad}, sid).json() == \
            {"ok": False, "error": "Lien refusé : seules les adresses web s'envoient."}, bad
    # After outside content: not parked (nothing opens here), and the card is told so.
    confirm.mark_tainted(sid, "Page web")
    out = tool_call(phone, "open_url", {"url": "https://evil.example/x"}, sid).json()
    assert out["ok"] is True and out["tainted"] is True and not confirm.PENDING
    # A caller claiming to be the PC in the phone's session: still the phone.
    assert tools.run_tool("open_url", {"url": "https://example.org"}, tools.ToolCtx(sid))["opened"] is False
    assert nothing_real == []
    # The PC opens it, as before.
    assert tools.run_tool("open_url", {"url": "https://example.org"}, tools.ToolCtx(confirm.new_session()))["ok"]
    assert nothing_real == [("open", "https://example.org")]


# ---------------------------------------------------------------- 21b. full-access routines

def test_phone_can_never_create_or_retime_a_complet_routine_holds(as_device, optin, pc, published, monkeypatch):
    sid = confirm.new_session(origin=APP)
    phone = as_device()
    out = tool_call(phone, "schedule", ROUTINE, sid).json()
    assert out == {"ok": False, "error": "Une routine avec accès complet se programme sur le PC."}
    siri_sid = confirm.new_session(origin=SIRI.origin)
    assert tools.run_tool("schedule", ROUTINE, tools.ToolCtx(siri_sid, origin=SIRI.origin))["ok"] is False
    assert phone.post("/api/schedules", json=ROUTINE).status_code == 403
    assert not confirm.PENDING and not [e for e in published if e["type"] == "pending"]
    # Even reached past the gate (a decided card), the handler and scheduler.add refuse it.
    with pytest.raises(PermissionError):
        tools.handlers()["schedule"](dict(ROUTINE), tools.ToolCtx(sid, origin=APP))
    with pytest.raises(PermissionError):
        scheduler.add("task", "Ménage", "Vide la corbeille", at="08:00", profile="complet", allow_complet=True,
                      via=APP)
    assert scheduler.items() == []
    # A phone's reminder or web routine is fine, and carries its origin.
    added = phone.post("/api/schedules", json={"kind": "task", "title": "Veille", "text": "Actus", "at": "08:00",
                                               "profile": "recherche"}).json()["item"]
    assert added["via"] == APP
    # A seeded phone routine with full access (old data, any path that slipped by): never run.
    started = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **kw: started.append(kw) or {"id": "t1"})
    due = time.time() - 30
    seeded = [{"id": "c0ffee", "kind": "task", "title": "Ménage", "text": "rm -rf", "due": due, "repeat": "daily",
               "profile": "complet", "complexity": "normale", "created": due, "via": APP},
              {"id": "bead01", "kind": "task", "title": "Veille retirée", "text": "Actus", "due": due,
               "repeat": "none", "profile": "recherche", "complexity": "normale", "created": due, "via": OTHER.origin}]
    store.save(scheduler.FILE, seeded)
    monkeypatch.setattr(remote, "origin_active", lambda origin: origin in ("pc", APP))
    scheduler.tick()
    assert started == []
    warned = [e["text"] for e in published if e["type"] == "warning"]
    assert warned == [f"Routine « {title} » non lancée : elle venait d'un appareil retiré ou demandait l'accès "
                      "complet." for title in ("Ménage", "Veille retirée")]
    # Control: an active phone's web routine does run, for that phone.
    store.save(scheduler.FILE, [{**seeded[1], "id": "bead02", "title": "Veille", "via": APP}])
    scheduler.tick()
    assert [kw["via"] for kw in started] == [APP]
    # A PC full-access routine: the phone may delete it, never move nor snooze it.
    item = scheduler.add("task", "Ménage", "Vide la corbeille", at="08:00", repeat="daily", profile="complet",
                         allow_complet=True)
    url = f"/api/schedules/{item['id']}"
    for r in (phone.patch(url, json={"due": time.time() + 7200}), phone.patch(url, json={"title": "Autre"}),
              phone.post(f"{url}/snooze", json={"minutes": 10})):
        assert r.status_code == 403 and r.json()["detail"] == "Routine avec accès complet : modifiable sur le PC seulement."
    assert next(i for i in scheduler.items() if i["id"] == item["id"])["due"] == item["due"]
    assert pc.patch(url, json={"due": time.time() + 7200}).status_code == 200  # the PC may
    assert phone.delete(url).json()["removed"] == 1


# ---------------------------------------------------------------- 22. who decides

def test_phone_can_cancel_any_request_but_approve_only_its_own_holds(as_device, optin, audited, fake_claude, pc,
                                                                       nothing_real):
    pc_request = tools.run_tool("delegate_to_claude", COMPLET, tools.ToolCtx(confirm.new_session()))["pending_id"]
    mine = confirm.new_session(origin=APP)
    phone = as_device()
    phone_request = tool_call(phone, "system_control", {"action": "mute"}, mine).json()["pending_id"]
    theirs = confirm.new_session(origin=OTHER.origin)
    other_request = tool_call(as_device(OTHER), "system_control", {"action": "mute"}, theirs).json()["pending_id"]
    phone = as_device()
    # Launch: the phone only its own.
    assert decide(phone, pc_request) == {"ok": False, "error": T.other_device}
    assert decide(phone, other_request) == {"ok": False, "error": T.other_device}
    assert not complet_tasks() and nothing_real == []
    assert decide(phone, phone_request)["state"] == "done" and nothing_real == [("mute", None)]
    # Cancel: any request, from the phone as from the PC.
    assert decide(phone, pc_request, "non")["state"] == "cancelled"
    assert decide(pc, other_request, "non")["state"] == "cancelled"
    # Siri never decides, not even "non", nor through its own confirm_action.
    siri_sid = confirm.new_session(origin=SIRI.origin)
    last = tool_call(as_device(), "system_control", {"action": "mute"}, mine).json()["pending_id"]
    for decision in ("oui", "non"):
        assert confirm.decide(last, decision, origin=SIRI.origin) == {"ok": False, "error": T.refused}
    handler = tools.handlers()["confirm_action"]
    assert handler({"pending_id": last, "decision": "oui"}, tools.ToolCtx(siri_sid, origin=SIRI.origin)) == \
        {"ok": False, "error": T.refused}
    assert confirm.PENDING[last]["state"] == "pending" and nothing_real == [("mute", None)]
    # Every decision of a remote caller is in the audit; the PC's are not.
    assert [f["pending"] for _, kind, f in audited if kind == "decide"] == \
        [pc_request, other_request, phone_request, pc_request]


# ---------------------------------------------------------------- 23. sessions

def test_sessions_are_bound_to_their_origin_holds(as_device, pc, monkeypatch):
    monkeypatch.setattr(realtime, "mint", lambda recent="", scope="pc": {"value": "ek_fake", "scope": scope})
    sid = as_device().post("/api/session", json={"recent": ""}).json()["session_id"]
    assert confirm.SESSIONS[sid]["origin"] == APP
    pc_sid = pc.post("/api/session", json={"recent": ""}).json()["session_id"]
    assert confirm.SESSIONS[pc_sid]["origin"] == "pc"
    foreign = "Session d'un autre appareil."
    # Another device (the PC included) can't use the phone's session: tool, turn or taint.
    for client in (pc, as_device(OTHER)):
        assert tool_call(client, "get_status", {}, sid).status_code == 403
        for route in ("/api/voice/turn", "/api/voice/taint"):
            r = client.post(route, json={"session_id": sid, "reason": "x"})
            assert r.status_code == 403 and r.json()["detail"] == foreign, route
    assert confirm.SESSIONS[sid]["last_turn"] == 0.0 and not confirm.is_tainted(sid)
    # Nor the phone the PC's; and a phone needs a session this PC knows.
    phone = as_device()
    assert phone.post("/api/voice/turn", json={"session_id": pc_sid}).json()["detail"] == foreign
    for unknown in (None, "", "0" * 32):
        r = tool_call(phone, "get_status", {}, unknown)
        assert r.status_code == 403 and r.json()["detail"] == "Session inconnue : rouvrez la conversation."
    assert phone.post("/api/voice/turn", json={}).status_code == 403
    confirm.FORGOTTEN["f" * 32] = ["Page web"]
    assert tool_call(phone, "get_status", {}, "f" * 32).json()["detail"] == "Session expirée : rouvrez la conversation."
    # Its own session works; the PC with a session JARVIS forgot works as before.
    assert tool_call(phone, "get_status", {}, sid).status_code == 200
    assert phone.post("/api/voice/turn", json={"session_id": sid}).json() == {"ok": True}
    assert pc.post("/api/voice/turn", json={"session_id": "1" * 32}).json() == {"ok": True}
    # Inside run_tool too: two remote origins never share a session.
    assert tools.run_tool("get_status", {}, tools.ToolCtx(sid, origin=OTHER.origin)) == \
        {"ok": False, "error": foreign}
    assert confirm.check_session(sid, SIRI.origin) == foreign


def test_taint_is_inherited_only_within_one_origin_holds():
    phone = confirm.new_session(origin=APP)  # clean
    pc_sid = confirm.new_session()
    confirm.mark_tainted(pc_sid, "Page web")
    # The phone reconnects: its own previous session stands for the carried lines, never the PC's.
    resumed = confirm.new_session(continues=True, origin=APP)
    assert not confirm.is_tainted(resumed)
    assert confirm.is_tainted(confirm.new_session(continues=True))  # the PC's own, tainted
    confirm.mark_tainted(resumed, "résultat de tâche")
    clean_pc = confirm.new_session()
    assert not confirm.is_tainted(confirm.new_session(continues=True))  # the phone's taint stays there
    # Named sources from another origin count as unknown: tainted, but never with their reasons.
    from_pc = confirm.new_session(continues=True, sources=[pc_sid], origin=APP)
    assert confirm.SESSIONS[from_pc]["reasons"] == ["conversation reprise"]
    from_phone = confirm.new_session(continues=True, sources=[resumed, clean_pc])
    assert confirm.SESSIONS[from_phone]["reasons"] == ["conversation reprise"]
    # Its own origin's sources carry their reasons, as on the PC today.
    own = confirm.new_session(continues=True, sources=[resumed, phone], origin=APP)
    assert confirm.SESSIONS[own]["reasons"] == ["résultat de tâche"]


def test_remote_sessions_never_evict_or_launder_a_pc_session_holds(clock, nothing_real):
    pc_sid = confirm.new_session()
    confirm.mark_tainted(pc_sid, "Page web")
    phone_tainted = confirm.new_session(origin=APP)
    confirm.mark_tainted(phone_tainted, "résultat de tâche")
    for i in range(40):
        clock[0] += 1
        confirm.new_session(origin=APP)
        confirm.new_session(origin=SIRI.origin)
    assert pc_sid in confirm.SESSIONS and confirm.is_tainted(pc_sid)
    out = tools.run_tool("delegate_to_claude", RESEARCH, tools.ToolCtx(pc_sid))
    assert out["status"] == "needs_confirmation"  # the tainted recherche is still parked
    assert confirm.decide(out["pending_id"], "non")["state"] == "cancelled"  # an open card would keep it
    by_origin = {}
    for record in confirm.SESSIONS.values():
        by_origin[record["origin"]] = by_origin.get(record["origin"], 0) + 1
    assert by_origin == {"pc": 1, APP: confirm.MAX_REMOTE_SESSIONS, SIRI.origin: confirm.MAX_REMOTE_SESSIONS}
    # The phone's own evicted tainted session stays tainted (and expired for the phone).
    assert phone_tainted not in confirm.SESSIONS and confirm.is_tainted(phone_tainted)
    assert confirm.check_session(phone_tainted, APP) == "Session expirée : rouvrez la conversation."
    # The PC's bucket: a tainted session pushed out by newer PC sessions stays tainted too,
    # and comes back tainted when the page uses it again.
    for i in range(confirm.MAX_SESSIONS):
        clock[0] += 1
        confirm.new_session()
    assert pc_sid not in confirm.SESSIONS and confirm.is_tainted(pc_sid)
    assert tools.run_tool("open_url", {"url": "https://evil.example/"}, tools.ToolCtx(pc_sid))["status"] == \
        "needs_confirmation"
    confirm.mark_turn(pc_sid)
    assert confirm.SESSIONS[pc_sid]["tainted"] and confirm.SESSIONS[pc_sid]["reasons"] == ["Page web"]
    assert nothing_real == []


# ---------------------------------------------------------------- 24b. Siri never gets a card

def test_siri_needing_confirmation_creates_no_pending_holds(published, nothing_real, fake_claude, monkeypatch):
    origin = SIRI.origin
    sid = confirm.new_session(origin=origin)
    ctx = tools.ToolCtx(sid, origin=origin)
    reminder = {"kind": "reminder", "title": "Pain", "text": "Sortir le pain", "delay_minutes": 30}
    assert tools.run_tool("schedule", reminder, ctx)["ok"]  # what Siri is for
    confirm.mark_tainted(sid, "résultat de tâche")
    assert tools.run_tool("schedule", {**reminder, "repeat": "daily"}, ctx) == \
        {"ok": False, "error": "Rappel répété refusé après la lecture d'un résultat."}
    assert tools.run_tool("schedule", {**reminder, "days": ["lun"]}, ctx)["ok"] is False
    assert tools.run_tool("delegate_to_claude", RESEARCH, ctx) == \
        {"ok": False, "error": "Recherche refusée après la lecture d'un résultat : ouvrez JARVIS sur l'iPhone."}
    assert tools.run_tool("schedule", {**reminder, "kind": "task", "profile": "recherche"}, ctx)["ok"] is False
    # Whatever would wait for a card is refused before anything is parked.
    would_park = [("delegate_to_claude", COMPLET), ("delegate_to_claude", RESEARCH), ("remember", {"fact": "x"}),
                  ("system_control", {"action": "lock_screen"}), ("schedule", ROUTINE),
                  ("ares_ajouter", {"type": "note", "titre": "x"}),
                  ("system_control", {"action": "write_clipboard", "value": "x"})]
    for name, args in would_park:
        out = confirm.gate(name, args, ctx)
        assert out and out["ok"] is False, name
        assert not confirm.needs_confirmation(name, args, sid, origin=origin), name
    assert confirm.gate("remember", {"fact": "x"}, ctx)["error"] == \
        "Cette action demande une confirmation : ouvrez JARVIS sur l'iPhone."
    assert not confirm.PENDING and not [e for e in published if e["type"] == "pending"]
    # cancel_task: only a task this key started, named by its id.
    monkeypatch.setattr(confirm, "is_tainted", lambda s: False)
    own = tools.run_tool("delegate_to_claude", RESEARCH, ctx)
    assert own["status"] == "started" and tasks.TASKS[own["task_id"]]["via"] == origin
    pc_task = tools.run_tool("delegate_to_claude", {**RESEARCH, "title": "PC"}, tools.ToolCtx(confirm.new_session()))
    for args in ({}, {"task_id": ""}, {"task_id": "latest"}, {"task_id": pc_task["task_id"]}, {"task_id": "nope"}):
        assert tools.run_tool("cancel_task", args, ctx) == \
            {"ok": False, "error": "Seules les tâches lancées depuis Siri s'annulent ici."}, args
    assert tasks.TASKS[pc_task["task_id"]]["status"] in tasks.ACTIVE
    assert tools.run_tool("cancel_task", {"task_id": own["task_id"]}, ctx)["ok"]
    # A Siri-origin confirm_action, even reached through the handler map, decides nothing.
    parked = confirm._park("delegate_to_claude", COMPLET, sid, "x", "y")
    confirm.mark_turn(sid)
    out = tools.handlers()["confirm_action"]({"pending_id": parked["id"], "decision": "oui"}, ctx)
    assert out == {"ok": False, "error": T.refused} and parked["state"] == "pending"
    assert tools.run_tool("confirm_action", {"pending_id": parked["id"], "decision": "oui"}, ctx)["ok"] is False
    assert not complet_tasks() and nothing_real == []
    for task in tasks.running():
        tasks.cancel(task["id"])
