"""ntfy notifications on the iPhone (B1, spec 6): minimal fixed texts, security
alerts never dropped, the topic kept out of every log, error and audit line,
failures isolated from events.publish, the server https (or http to a local
address) with no redirect. A fake ntfy server (httpx.MockTransport) records
what would have been sent: nothing here touches the network."""
import logging
import re
import threading
import time
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from remote_helpers import IP, LOGIN, paired_client

import server
from jarvis import audit, config, desktop, events, inbox, notify, remote, security, settings, store

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}
DEVICE = "d_3f9a1c2b4d5e6f70"
PHONE = remote.Caller(kind="app", device_id=DEVICE, ip=IP, login=LOGIN, name="iPhone de test")
# What must never leave the PC in a notification.
TITLE = "Rapport confidentiel sur la banque de Monsieur"
OUTPUT = "Solde du compte : 1 234,56 €. Mot de passe noté dans le carnet."
PROMPT = "Lis mes mails de la banque et résume-les"


class FakeNtfy:
    """A ntfy server that records each request; status or raises decide the answer."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.headers = {}
        self.raises = None

    def __call__(self, request):
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises(request)
        return httpx.Response(self.status, headers=self.headers, json={"id": "abc", "event": "message"})

    @property
    def bodies(self) -> list:
        return [r.content.decode("utf-8") for r in self.requests]


@pytest.fixture
def client():
    return TestClient(server.app, base_url=BASE, headers=AUTH)


@pytest.fixture
def ntfy(monkeypatch):
    """Notifications on, monsieur away from the PC, no quiet time: every rule
    that holds a message back is switched on by the test itself."""
    fake = FakeNtfy()
    monkeypatch.setattr(notify, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(config, "NTFY", True)
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    monkeypatch.setattr(events, "leader", lambda: None)
    monkeypatch.setattr(desktop, "attention_state", lambda: "ok")
    monkeypatch.setattr(desktop, "idle_seconds", lambda: None)
    monkeypatch.setattr(desktop, "toast", lambda title, body: False)  # audit.alert's PC toast
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: False)
    notify.topic()  # monsieur has seen it in Réglages: there is one to send to
    yield fake
    notify._drain()


def sent(fake) -> list:
    notify._drain()
    return fake.bodies


def push(kind, data) -> int:
    """events.publish, then wait for the message it may send: the order sent is the order published."""
    event_id = events.publish(kind, data)
    notify._drain()
    return event_id


def alert(kind, text="Alerte de test.", **fields):
    audit.alert(kind, text, caller=PHONE, **fields)
    notify._drain()


def task(status, task_id="t1", **extra) -> dict:
    return {"id": task_id, "status": status, "title": TITLE, "output": OUTPUT, "prompt": PROMPT,
            "via": f"app:{DEVICE}", **extra}


def pending(pid="p1", state="pending", via="pc") -> dict:
    return {"pending": {"id": pid, "name": "delegate_to_claude", "kind": "tool", "state": state, "via": via,
                        "summary": f"Lancer « {TITLE} » depuis « iPhone de test »",
                        "detail": f"{PROMPT} ({IP}, {LOGIN})", "button_only": False}}


def reminder(text="Appeler le notaire au sujet de la maison", title="Notaire") -> dict:
    return {"id": "r1", "title": title, "text": text, "via": "pc"}


def quiet_range() -> str:
    now = datetime.now()
    return f"{(now - timedelta(hours=1)):%H:%M}-{(now + timedelta(hours=1)):%H:%M}"


def alert_bodies() -> dict:
    return {k: f"JARVIS · sécurité : {a['ntfy_text']}" for k, a in audit.ALERTS.items() if a["ntfy"]}

# ---------------------------------------------------------------- P0 32: the proofs


def test_notifications_carry_minimal_text_only_holds(ntfy, monkeypatch):
    monkeypatch.setattr(notify, "RATE_S", 0)  # every message of this test goes out
    push("task", task("done", "t1"))
    push("task", task("error", "t2"))
    push("task", task("interrompue", "t3"))
    push("reminder", reminder())
    push("pending", pending("p1", via="siri:k_9b8a7c6d5e4f3a21"))
    tasks_and_co = sent(ntfy)
    assert tasks_and_co == ["JARVIS : tâche terminée.", "JARVIS : une tâche n'a pas abouti.",
                            "JARVIS : une tâche n'a pas abouti.", "JARVIS : un rappel.",
                            "JARVIS : une confirmation vous attend."]
    # Every security alert sent to ntfy: the PC text names the device, its IP
    # and login; the phone gets « JARVIS · sécurité : » + the kind's fixed sentence.
    expected = alert_bodies()
    assert {"new_device", "funnel", "auth_failures", "remote_complet", "login_change", "ip_change",
            "secret_copied", "remote_on", "remote_off", "complet_optin", "serve_misconfig",
            "pc_login_change"} == set(expected)
    for kind in expected:
        alert(kind, f"Alerte {kind} : « iPhone de test » ({IP}, {LOGIN}) « {TITLE} ».", title=TITLE)
    # A hook handed anything else still sends only a fixed sentence.
    notify._on_alert("new_device", f"« iPhone de test » {IP} {LOGIN}")
    notify._on_alert("inconnue", f"{IP} {LOGIN}")
    bodies = sent(ntfy)
    alerts = bodies[len(tasks_and_co):]
    assert alerts[:len(expected)] == list(expected.values())
    assert sorted(alerts[len(expected):]) == sorted([expected["new_device"],
                                                     f"JARVIS · sécurité : {notify.ALERT_UNKNOWN}"])
    for body in bodies:
        for secret in (TITLE, OUTPUT, PROMPT, "iPhone de test", IP, LOGIN, DEVICE, "Notaire", "notaire", "t1"):
            assert secret not in body, (secret, body)
        assert "http" not in body and "www." not in body  # never a link
    # The request itself: POST to {server}/{topic}, the fixed headers, no click URL.
    value = store.load(notify.NTFY_FILE, {})["topic"]
    for r in ntfy.requests:
        assert r.method == "POST" and str(r.url) == f"https://ntfy.sh/{value}"
        assert r.headers["Title"] == "JARVIS" and r.headers["Tags"] == "robot"
        assert r.headers["Priority"] in ("default", "high")
        assert not {"click", "actions", "attach", "icon", "x-click", "markdown"} & {k.lower() for k in r.headers}
    priority = {r.content.decode("utf-8"): r.headers["Priority"] for r in ntfy.requests}
    assert {b for b, p in priority.items() if p == "high"} == {"JARVIS : un rappel.", *alerts}


def test_topic_never_reaches_logs_or_errors_holds(ntfy, client, caplog, capfd, monkeypatch):
    caplog.set_level(logging.DEBUG)
    first = client.get("/api/notify").json()["topic"]
    second = client.post("/api/notify/topic").json()["topic"]
    assert first != second
    errors = []

    def fail_with(raises=None, status=200, headers=None):
        notify.reset_memory()  # a fresh rate window for each failure
        ntfy.raises, ntfy.status, ntfy.headers = raises, status, headers or {}
        r = client.post("/api/notify/test").json()
        assert r["ok"] is False, r
        errors.append(r["error"])
        errors.append(client.get("/api/notify").json()["last_error"])
        push("task", task("done", f"t-{len(errors)}"))  # the background path too
        errors.append(notify._status["last_error"])

    # httpx's own messages name the URL, the topic in it: none of them is passed on.
    fail_with(lambda rq: httpx.ConnectError(f"connexion refusée vers {rq.url}", request=rq))
    fail_with(lambda rq: httpx.ReadTimeout(f"délai dépassé pour {rq.url}", request=rq))
    fail_with(lambda rq: httpx.RemoteProtocolError(f"réponse illisible de {rq.url}", request=rq))
    fail_with(lambda rq: RuntimeError(f"panne inattendue {rq.url}"))
    fail_with(status=500)
    fail_with(status=302, headers={"Location": f"https://ailleurs.example/{second}"})
    monkeypatch.setattr(config, "NTFY_SERVER", f"http://ntfy.example/{second}")  # refused before sending
    fail_with()
    assert len(set(errors)) >= 5 and all(errors)
    # The security alerts: written to the audit trail, sent with the topic in the URL only.
    ntfy.raises, ntfy.status = None, 200
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.sh")
    alert("new_device", "Nouvel appareil associé : « iPhone de test ».")
    out, err = capfd.readouterr()
    for value in (first, second):
        for where, text in (("logs", caplog.text), ("stdout", out), ("stderr", err), *(("error", e) for e in errors)):
            assert value not in text, where
        for path in config.DATA_DIR.rglob("*"):  # the audit trail, its alerts, the inbox...
            if path.is_file() and not path.name.startswith(notify.NTFY_FILE):  # its own file (and backup)
                assert value not in path.read_text(encoding="utf-8", errors="replace"), path.name
    assert "notification ntfy non envoyée" in caplog.text  # the failures were logged, by reason only
    assert "ntfy.sh/jarvis-…" in caplog.text  # httpx's own request line, the topic cut out


def test_a_failing_ntfy_never_breaks_publish_holds(ntfy, monkeypatch, caplog):
    crashed = []
    monkeypatch.setattr(threading, "excepthook", lambda args: crashed.append(args.exc_type))
    seen_after = []
    monkeypatch.setattr(events, "HOOKS", [*events.HOOKS, lambda kind, data: seen_after.append(kind)])
    monkeypatch.setattr(notify, "RATE_S", 0)
    # The server down, then answering garbage, then the code itself failing.
    ntfy.raises = lambda rq: httpx.ConnectError("refusé", request=rq)
    assert isinstance(push("task", task("done", "a")), int)
    assert notify._status["last_error"] == notify.ERR_CONNECT
    ntfy.raises = lambda rq: ValueError("réponse illisible")
    assert isinstance(push("reminder", reminder()), int)
    assert notify._status["last_error"] == notify.ERR_SEND
    ntfy.raises = None
    ntfy.status = 503
    assert isinstance(push("pending", pending("p9")), int)
    assert notify._status["last_error"] == notify.ERR_STATUS.format(code=503)

    def broken(*args, **kwargs):
        raise RuntimeError("bogue")
    monkeypatch.setattr(notify, "message_for", broken)
    assert isinstance(events.publish("task", task("done", "b")), int)
    monkeypatch.setattr(notify, "_queue", broken)
    ntfy.status = 200
    # An alert still gets its audit line, its PC card and the other hooks.
    others = []
    monkeypatch.setattr(audit, "ALERT_HOOKS", [*audit.ALERT_HOOKS, lambda kind, text: others.append(kind)])
    alert("remote_complet", "Tâche avec accès complet lancée depuis l'iPhone.")
    assert seen_after == ["task", "reminder", "pending", "task", "warning"]
    assert others == ["remote_complet"]
    # notify.py caught its own failures: events.py's last-resort catch never had to.
    assert "écouteur d'événements en échec" not in caplog.text and "relais d'alerte en échec" not in caplog.text
    assert [line["alert"] for line in audit.tail(10) if line.get("kind") == "alert"] == ["remote_complet"]
    assert crashed == []


def test_ntfy_server_must_be_https_or_local_holds(ntfy, client, monkeypatch):
    for bad in ("http://ntfy.sh", "http://8.8.8.8", "http://127.0.0.1:8080", "http://ts.net",
                "https://monsieur:motdepasse@ntfy.sh", "https://ntfy.sh/?redirect=evil",
                "https://ntfy.sh/#fragment", "ftp://ntfy.example", "javascript:alert(1)",
                "http://172.32.0.1", "http://[::ffff:10.0.0.1]", "https://ntfy.sh:99999", "https://ntfy .sh"):
        r = client.put("/api/settings", json={"ntfy_server": bad})
        assert r.status_code == 400 and r.json()["detail"] == settings.URL_REFUSED, (bad, r.text)
        assert config.NTFY_SERVER == "https://ntfy.sh"
    assert client.put("/api/settings", json={"ntfy_server": "https://" + "a" * 200}).status_code == 400
    assert client.put("/api/settings", json={"ntfy_server": ""}).status_code == 400
    for good in ("https://ntfy.example.org/base", "http://100.101.102.103:8080",
                 "http://[fd7a:115c:a1e0::1234]:80", "http://ntfy.tail0000.ts.net", "http://192.168.1.20",
                 "http://10.1.2.3", "http://172.16.5.4", "http://[fd00::1]", "https://ntfy.sh"):
        r = client.put("/api/settings", json={"ntfy_server": good})
        assert r.status_code == 200 and config.NTFY_SERVER == good, (good, r.text)
    # A server from .env is checked again before each message: nothing goes out.
    monkeypatch.setattr(config, "NTFY_SERVER", "http://ntfy.example")
    r = client.post("/api/notify/test").json()
    assert r == {"ok": False, "error": settings.URL_REFUSED} and ntfy.requests == []
    # A redirect is never followed: one request, and the test says it failed.
    notify.reset_memory()
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.sh")
    ntfy.status, ntfy.headers = 301, {"Location": "http://ailleurs.example/vol"}
    r = client.post("/api/notify/test").json()
    assert r["ok"] is False and "301" in r["error"] and len(ntfy.requests) == 1
    # Writing it stays on the PC: the phone cannot change any ntfy setting.
    phone, _device, _token = paired_client(monkeypatch)
    for key, value in (("ntfy_server", "https://ntfy.example.org"), ("ntfy", False), ("ntfy_only_away", True),
                       ("ntfy_reminder_text", True)):
        assert phone.put("/api/settings", json={key: value}).status_code == 403, key
    assert config.NTFY_SERVER == "https://ntfy.sh" and config.NTFY is True

# ---------------------------------------------------------------- when a message goes out


def test_quiet_hours_and_dnd_hold_tasks_and_pendings_but_not_reminders_and_alerts(ntfy, monkeypatch):
    monkeypatch.setattr(notify, "RATE_S", 0)
    for quiet in ("hours", "dnd"):
        notify.reset_memory()
        ntfy.requests.clear()
        if quiet == "hours":
            monkeypatch.setattr(config, "QUIET_HOURS", quiet_range())
        else:
            monkeypatch.setattr(config, "QUIET_HOURS", "")
            inbox.set_dnd(time.time() + 3600)
        assert inbox.is_quiet()
        push("task", task("done", f"q-{quiet}"))
        push("pending", pending(f"p-{quiet}"))
        push("reminder", reminder())
        alert("remote_complet")
        assert sent(ntfy) == ["JARVIS : un rappel.", alert_bodies()["remote_complet"]], quiet
    inbox.set_dnd(None)
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    # The task held back (not the confirmation: it has expired by then) is told
    # with the first one after: never lost, never told twice.
    push("task", task("done", "after"))
    assert sent(ntfy)[-1] == "JARVIS : 2 tâches terminées."
    push("task", task("done", "next"))
    assert sent(ntfy)[-1] == "JARVIS : tâche terminée."


def test_task_results_held_by_quiet_hours_are_told_once_they_end_holds(ntfy, monkeypatch):
    """A Siri research that ends at 22:40 (quiet hours from 22:30): Siri said
    « je vous préviens », so the phone hears it once quiet hours are over."""
    monkeypatch.setattr(notify, "RATE_S", 0)
    monkeypatch.setattr(notify, "HELD_CHECK_S", 0.05)
    monkeypatch.setattr(config, "QUIET_HOURS", quiet_range())
    push("task", task("done", "nuit-1", via="siri:k_0123456789abcdef"))
    push("task", task("error", "nuit-2", via=f"app:{DEVICE}"))
    push("task", task("done", "nuit-1", via="siri:k_0123456789abcdef"))  # the same status again: once
    time.sleep(0.2)  # the check runs, finds quiet hours, waits again
    assert sent(ntfy) == []
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    end = time.time() + 5
    while not ntfy.requests and time.time() < end:
        time.sleep(0.02)
    assert sent(ntfy) == ["JARVIS : 2 tâches finies, au moins une n'a pas abouti."]
    time.sleep(0.2)
    assert sent(ntfy) == ["JARVIS : 2 tâches finies, au moins une n'a pas abouti."]  # told once
    # Switched off meanwhile: what was held is forgotten, nothing goes out.
    monkeypatch.setattr(config, "QUIET_HOURS", quiet_range())
    push("task", task("done", "nuit-3", via="siri:k_0123456789abcdef"))
    monkeypatch.setattr(config, "NTFY", False)
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    time.sleep(0.3)
    monkeypatch.setattr(config, "NTFY", True)
    push("task", task("done", "jour", via="siri:k_0123456789abcdef"))
    assert sent(ntfy)[-1] == "JARVIS : tâche terminée." and len(ntfy.requests) == 2


def test_only_away_follows_the_leader_page_the_attention_and_idle_time(ntfy, monkeypatch):
    monkeypatch.setattr(notify, "RATE_S", 0)
    monkeypatch.setattr(config, "NTFY_ONLY_AWAY", True)
    state = {"leader": "page-1", "attention": "ok", "idle": 30.0}
    monkeypatch.setattr(events, "leader", lambda: state["leader"])
    monkeypatch.setattr(desktop, "attention_state", lambda: state["attention"])
    monkeypatch.setattr(desktop, "idle_seconds", lambda: state["idle"])
    cases = [  # (leader, attention, idle) -> at the PC?
        (("page-1", "ok", 30.0), True), (("page-1", "ok", None), True), (("page-1", "ok", 599.0), True),
        ((None, "ok", 30.0), False), (("page-1", "not_present", 30.0), False),
        (("page-1", "busy", 30.0), False), (("page-1", "ok", 601.0), False),
    ]
    for i, ((leader, attention, idle), here) in enumerate(cases):
        state.update(leader=leader, attention=attention, idle=idle)
        assert notify.at_pc() is here, (leader, attention, idle)
        before = len(sent(ntfy))
        push("task", task("done", f"away-{i}", via="pc"))
        push("pending", pending(f"away-{i}"))
        push("reminder", reminder())  # always
        bodies = sent(ntfy)[before:]
        expected = ["JARVIS : un rappel."] if here else \
            ["JARVIS : tâche terminée.", "JARVIS : une confirmation vous attend.", "JARVIS : un rappel."]
        assert bodies == expected, (leader, attention, idle)
    # A result from the iPhone or Siri is never spoken on the PC: monsieur at
    # the PC or not, the phone is told.
    state.update(leader="page-1", attention="ok", idle=1.0)
    assert notify.at_pc()
    for i, via in enumerate((f"app:{DEVICE}", "siri:k_0123456789abcdef")):
        before = len(sent(ntfy))
        push("task", task("done", f"remote-{i}", via=via))
        assert sent(ntfy)[before:] == ["JARVIS : tâche terminée."], via
    # Off: tasks are told even with monsieur at the PC.
    monkeypatch.setattr(config, "NTFY_ONLY_AWAY", False)
    state.update(leader="page-1", attention="ok", idle=1.0)
    push("task", task("done", "present"))
    assert sent(ntfy)[-1] == "JARVIS : tâche terminée."


def test_each_task_status_and_confirmation_is_told_once(ntfy, monkeypatch):
    monkeypatch.setattr(notify, "RATE_S", 0)
    for status in ("running", "en_file", "done", "done", "cancelled"):
        push("task", task(status, "t1"))
    push("task", task("error", "t1"))  # the same task, another final status
    push("task", task("error", "t1"))
    push("task", task("done", ""))  # no id: never told
    push("pending", pending("p1"))
    push("pending", pending("p1"))  # asked again
    push("pending", pending("p1", state="done"))
    push("pending", pending("p2", state="expired"))
    assert sent(ntfy) == ["JARVIS : tâche terminée.", "JARVIS : une tâche n'a pas abouti.",
                          "JARVIS : une confirmation vous attend."]


def close_windows():
    """Ten seconds later: each window's counted message goes out now."""
    with notify._lock:
        due = list(notify._timers.items())
    for kind, timer in due:
        timer.cancel()
        notify._tell_later(kind)
    notify._drain()


def test_one_message_per_kind_every_ten_seconds_but_alerts_are_never_rate_limited(ntfy, monkeypatch):
    for i in range(3):  # (push would wait for the window to close)
        events.publish("task", task("done", f"r{i}"))
        events.publish("reminder", reminder())
        events.publish("pending", pending(f"r{i}"))
    for _ in range(5):  # never deduplicated by audit: each one reaches the phone
        audit.alert("remote_complet", "Alerte de test.", caller=PHONE)
    audit.alert("new_device", "Alerte de test.", caller=PHONE)
    with notify._lock:
        workers = list(notify._workers)
    for worker in workers:
        worker.join(5)
    first = list(ntfy.bodies)
    assert first.count("JARVIS : tâche terminée.") == 1 and first.count("JARVIS : une confirmation vous attend.") == 1
    # Reminders are never rate-limited: monsieur set each one.
    assert first.count("JARVIS : un rappel.") == 3
    assert first.count(alert_bodies()["remote_complet"]) == 5 and first.count(alert_bodies()["new_device"]) == 1
    assert set(notify._timers) == {"task", "pending"}
    # What the window refused is counted, never dropped: one message when it closes.
    close_windows()
    assert sorted(ntfy.bodies[len(first):]) == ["JARVIS : 2 confirmations vous attendent.",
                                                "JARVIS : 2 tâches terminées."]
    # A failure counted with the rest is never hidden by a success.
    for kind in list(notify._last):
        notify._last[kind] -= notify.RATE_S + 0.1
    for task_id, status in (("a", "done"), ("b", "error"), ("c", "done")):
        events.publish("task", task(status, task_id))
    close_windows()
    assert ntfy.bodies[-2:] == ["JARVIS : tâche terminée.", "JARVIS : 2 tâches finies, au moins une n'a pas abouti."]


def test_the_window_sends_what_it_counted_by_itself(ntfy, monkeypatch):
    monkeypatch.setattr(notify, "RATE_S", 0.3)
    started = time.monotonic()
    push("task", task("done", "w1"))
    events.publish("task", task("error", "w2"))
    assert sent(ntfy) == ["JARVIS : tâche terminée.", "JARVIS : une tâche n'a pas abouti."]
    assert time.monotonic() - started >= 0.25 and notify._timers == {} and notify._later == {}
    # Notifications switched off before the window closed: nothing more goes out.
    events.publish("task", task("done", "w3"))
    monkeypatch.setattr(config, "NTFY", False)
    assert sent(ntfy) == ["JARVIS : tâche terminée.", "JARVIS : une tâche n'a pas abouti."]


def test_a_message_failing_on_the_way_is_tried_again(ntfy, monkeypatch, caplog):
    monkeypatch.setattr(notify, "RETRY_S", (0, 0))
    answers = [httpx.ConnectError, httpx.ReadTimeout]

    def flaky(request):
        ntfy.requests.append(request)
        if answers:
            raise answers.pop(0)("panne", request=request)
        return httpx.Response(200, json={"id": "x"})
    monkeypatch.setattr(notify, "TRANSPORT", httpx.MockTransport(flaky))
    alert("new_device")
    assert ntfy.bodies == [alert_bodies()["new_device"]] * 3 and notify._status["last_error"] == ""
    # A 5xx too, but never a refusal (4xx), and at most len(RETRY_S) more tries.
    ntfy.requests.clear()
    monkeypatch.setattr(notify, "TRANSPORT", httpx.MockTransport(ntfy))
    ntfy.status = 503
    push("reminder", reminder())
    assert len(ntfy.requests) == 3 and notify._status["last_error"] == notify.ERR_STATUS.format(code=503)
    ntfy.requests.clear()
    ntfy.status = 403
    push("reminder", reminder())
    assert len(ntfy.requests) == 1
    assert "nouvel essai" in caplog.text and "jarvis-" not in caplog.text.replace("jarvis-…", "")


def test_remote_warnings_phone_pendings_and_other_events_are_not_told_by_on_event(ntfy, monkeypatch):
    monkeypatch.setattr(notify, "RATE_S", 0)
    events.publish_pc("warning", {"kind": "remote", "text": f"« iPhone de test » {IP}", "plain": True})
    assert notify.message_for("warning", {"kind": "remote", "text": "x"}) is None
    push("pending", pending("p1", via=f"app:{DEVICE}"))  # its card is on the phone already
    for kind, data in (("warning", {"text": "Le fichier des tâches était abîmé."}), ("briefing", {"text": "Bonjour"}),
                       ("config", {"keys": ["voice"]})):
        push(kind, data)
    notify._on_event("task", "pas un dict")
    assert sent(ntfy) == []


def test_nothing_goes_out_while_notifications_are_off(ntfy, client, monkeypatch):
    monkeypatch.setattr(config, "NTFY", False)
    push("task", task("done"))
    push("reminder", reminder())
    alert("new_device")
    assert client.post("/api/notify/test").json() == {"ok": False, "error": notify.ERR_OFF}
    assert sent(ntfy) == []


def test_the_reminder_text_only_when_asked_cut_and_without_a_link(ntfy, monkeypatch):
    monkeypatch.setattr(notify, "RATE_S", 0)
    monkeypatch.setattr(config, "NTFY_REMINDER_TEXT", True)
    push("reminder", reminder("Arroser les plantes"))
    push("reminder", reminder("x" * 80))
    push("reminder", reminder("Voir https://evil.example/vol et www.piege.example\nvite"))
    push("reminder", reminder("", title="Pharmacie"))
    bodies = sent(ntfy)
    assert bodies[0] == "JARVIS · rappel : Arroser les plantes"
    assert bodies[1] == "JARVIS · rappel : " + "x" * 59 + "…"
    assert bodies[2] == "JARVIS · rappel : Voir (lien) et (lien) vite"
    assert bodies[3] == "JARVIS · rappel : Pharmacie"

# ---------------------------------------------------------------- the routes


def test_the_topic_is_made_on_first_look_and_renewed_only_from_the_pc(ntfy, client, monkeypatch):
    (config.DATA_DIR / notify.NTFY_FILE).unlink()
    assert notify.topic(create=False) == ""
    push("task", task("done"))  # nobody can have subscribed yet: nothing is sent, nothing made
    assert ntfy.requests == [] and notify._status["last_error"] == notify.ERR_NO_TOPIC
    assert not (config.DATA_DIR / notify.NTFY_FILE).exists()
    body = client.get("/api/notify").json()
    assert set(body) == {"enabled", "server", "topic", "only_away", "reminder_text", "last_error", "last_sent"}
    assert body["enabled"] is True and body["server"] == "https://ntfy.sh"
    assert re.fullmatch(r"jarvis-[A-Za-z0-9_-]{32}", body["topic"])
    saved = store.load(notify.NTFY_FILE, {})
    assert saved["topic"] == body["topic"] and isinstance(saved["created"], float)
    assert client.get("/api/notify").json()["topic"] == body["topic"]  # made once
    # A hand-edited topic that could leave the server's path is replaced.
    for odd in ("jarvis-../../admin", "jarvis-" + "a" * 32 + "\n", "jarvis-" + "a" * 32 + "/x", 42):
        store.save(notify.NTFY_FILE, {"topic": odd, "created": 1.0})
        assert re.fullmatch(r"jarvis-[A-Za-z0-9_-]{32}", client.get("/api/notify").json()["topic"]), odd
    renewed = client.post("/api/notify/topic").json()["topic"]
    assert renewed != body["topic"] and client.get("/api/notify").json()["topic"] == renewed
    # The paired iPhone reads it (to subscribe) and sends a test, never renews it.
    phone, _device, _token = paired_client(monkeypatch)
    assert phone.get("/api/notify").json()["topic"] == renewed
    assert phone.post("/api/notify/topic").status_code == 403
    assert phone.post("/api/notify/test").json() == {"ok": True}
    assert client.get("/api/notify").json()["topic"] == renewed
    assert sent(ntfy) == ["JARVIS : notification de test."]


def test_the_test_message_is_synchronous_and_says_how_it_went(ntfy, client):
    r = client.post("/api/notify/test").json()
    assert r == {"ok": True} and ntfy.bodies == ["JARVIS : notification de test."]  # already sent
    state = client.get("/api/notify").json()
    assert state["last_error"] == "" and state["last_sent"] > time.time() - 60
    assert client.post("/api/notify/test").json() == {"ok": False, "error": notify.ERR_RATE}
    notify.reset_memory()
    ntfy.raises = lambda rq: httpx.ConnectError("refusé", request=rq)
    assert client.post("/api/notify/test").json() == {"ok": False, "error": notify.ERR_CONNECT}
    assert client.get("/api/notify").json()["last_error"] == notify.ERR_CONNECT
    notify.reset_memory()
    assert client.get("/api/notify").json()["last_error"] == ""


def test_the_hooks_are_installed_once():
    assert events.HOOKS.count(notify._on_event) == 1
    assert audit.ALERT_HOOKS.count(notify._on_alert) == 1
    notify._install()
    assert events.HOOKS.count(notify._on_event) == 1 and audit.ALERT_HOOKS.count(notify._on_alert) == 1
