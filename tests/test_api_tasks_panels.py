"""The side panel's task routes (WP10): 'Réessayer' and 'Afficher dans
l'explorateur'. Full access never starts from a button without its
confirmation card, and the explorer only ever shows a file the task wrote."""
import sys
import time

import pytest
from fastapi.testclient import TestClient

from jarvis import config, confirm, desktop, security, tasks
from test_tasks import FAKE_CLAUDE, wait

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}


@pytest.fixture
def client():
    import server
    return TestClient(server.app, base_url=BASE, headers=AUTH)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    confirm.PENDING.clear()
    monkeypatch.setattr(config, "CONFIRM_COMPLET", True)
    monkeypatch.setattr(config, "PENDING_TTL", 90)
    yield
    for task in tasks.running():
        tasks.cancel(task["id"])
    end = time.time() + 10
    while (tasks._active or tasks._waiting) and time.time() < end:
        time.sleep(0.05)
    confirm.PENDING.clear()


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)


def finished(title="Rangement", prompt="Range le dossier", profile="lecture", **extra) -> dict:
    """A task already over, as the history keeps it."""
    task = {"id": f"t{len(tasks.TASKS) + 1}", "title": title, "prompt": prompt, "profile": profile,
            "complexity": "simple", "model": "haiku", "origin": "voix", "status": "error",
            "output": "", "progress": "", "steps": 2, "started": time.time() - 60, "ended": time.time(),
            "session_id": "s-old", "resumed_from": None, "files": [], "permission_denials": [], **extra}
    tasks.TASKS[task["id"]] = task
    return task


def complet_tasks():
    return [t for t in tasks.TASKS.values() if t["profile"] == "complet" and t["status"] in tasks.ACTIVE]


# ---------------------------------------------------------------- Réessayer

def test_retry_runs_the_stored_prompt_and_profile(client, fake_claude):
    old = finished(profile="recherche", prompt="cherche la météo")
    r = client.post(f"/api/task/{old['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "started"
    fresh = tasks.TASKS[r.json()["task"]["id"]]
    assert (fresh["prompt"], fresh["profile"], fresh["complexity"], fresh["origin"]) == \
        ("cherche la météo", "recherche", "simple", "clavier")
    wait(fresh)


def test_retry_of_a_follow_up_continues_the_same_earlier_session(client, fake_claude):
    first = finished(status="done", session_id="sess-1")
    follow = finished(prompt="et ensuite ?", resumed_from=first["id"])
    fresh = tasks.TASKS[client.post(f"/api/task/{follow['id']}/retry").json()["task"]["id"]]
    assert fresh["resumed_from"] == first["id"] and fresh["resume"] == "sess-1"
    wait(fresh)


def test_retry_of_a_complet_task_only_parks_a_confirmation_holds(client, fake_claude):
    old = finished(profile="complet", prompt="Supprime les doublons", title="Doublons")
    out = client.post(f"/api/task/{old['id']}/retry").json()
    assert out["status"] == "needs_confirmation"
    pending = confirm.PENDING[out["pending_id"]]
    assert pending["sid"] is None and pending["name"] == "delegate_to_claude"
    assert pending["args"]["prompt"] == "Supprime les doublons" and pending["args"]["profile"] == "complet"
    assert "Doublons" in pending["summary"] and pending["detail"] == "Supprime les doublons"
    assert not complet_tasks()
    # Asked twice (a double click): one request, one card.
    assert client.post(f"/api/task/{old['id']}/retry").json()["pending_id"] == out["pending_id"]
    # A voice "oui" can't take it: it belongs to no voice session.
    sid = confirm.new_session()
    confirm.mark_turn(sid)
    assert confirm.decide(out["pending_id"], "oui", voice_session=sid, by_voice=True)["ok"] is False
    assert not complet_tasks()
    # [Lancer] on its card: the stored prompt starts, once.
    done = client.post(f"/api/pending/{out['pending_id']}/decide", json={"decision": "oui"}).json()
    assert done["state"] == "done"
    [task] = complet_tasks()
    assert task["prompt"] == "Supprime les doublons"
    wait(task)


def test_retry_of_a_complet_task_without_confirmations_is_refused_holds(client, fake_claude, monkeypatch):
    monkeypatch.setattr(config, "CONFIRM_COMPLET", False)
    old = finished(profile="complet")
    r = client.post(f"/api/task/{old['id']}/retry")
    assert r.status_code == 400 and "confirmation" in r.json()["detail"]
    assert not complet_tasks() and not confirm.PENDING


def test_retry_refuses_unknown_running_and_approval_tasks(client, fake_claude):
    assert client.post("/api/task/nope/retry").status_code == 404
    assert client.post(f"/api/task/{finished(status='running')['id']}/retry").status_code == 409
    approval = finished(origin="approbation", prompt="Reprends avec les outils autorisés")
    assert client.post(f"/api/task/{approval['id']}/retry").status_code == 400
    assert client.post(f"/api/task/{finished(prompt='')['id']}/retry").status_code == 400
    assert len(tasks.TASKS) == 3  # nothing new started


# ---------------------------------------------------------------- Afficher dans l'explorateur

@pytest.fixture
def shown(monkeypatch):
    seen = []
    monkeypatch.setattr(desktop, "reveal_in_explorer", lambda path: seen.append(path))
    return seen


def test_reveal_shows_only_a_file_the_task_wrote_holds(client, shown, tmp_path):
    report = tmp_path / "rapport.md"
    report.write_text("ok", encoding="utf-8")
    task = finished(status="done", files=[str(report)])
    r = client.post(f"/api/task/{task['id']}/reveal", json={"path": str(report)})
    assert r.status_code == 200 and shown == [str(report)]
    for path in (str(tmp_path / "autre.bat"), str(report) + " ", str(tmp_path), "", "../rapport.md", "rapport.md"):
        assert client.post(f"/api/task/{task['id']}/reveal", json={"path": path}).status_code == 403, path
    assert client.post("/api/task/nope/reveal", json={"path": str(report)}).status_code == 404
    other = finished(status="done", files=[])
    assert client.post(f"/api/task/{other['id']}/reveal", json={"path": str(report)}).status_code == 403
    assert shown == [str(report)]


def test_reveal_of_a_relative_path_is_in_claudes_folder_and_a_gone_file_says_so(client, shown, tmp_path,
                                                                                monkeypatch):
    work = tmp_path / "travail"
    work.mkdir()
    (work / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(config, "WORKDIR", str(work))
    task = finished(status="done", files=["notes.txt", "parti.txt"])
    assert client.post(f"/api/task/{task['id']}/reveal", json={"path": "notes.txt"}).status_code == 200
    assert shown == [str(work / "notes.txt")]
    r = client.post(f"/api/task/{task['id']}/reveal", json={"path": "parti.txt"})
    assert r.status_code == 410 and "parti.txt" in r.json()["detail"]


def test_reveal_not_available_yet_answers_501(client, tmp_path, monkeypatch):
    def stub(path):
        raise NotImplementedError("Afficher dans l'explorateur : pas encore disponible.")
    monkeypatch.setattr(desktop, "reveal_in_explorer", stub)
    report = tmp_path / "r.md"
    report.write_text("x", encoding="utf-8")
    task = finished(status="done", files=[str(report)])
    r = client.post(f"/api/task/{task['id']}/reveal", json={"path": str(report)})
    assert r.status_code == 501 and "explorateur" in r.json()["detail"]


# ---------------------------------------------------------------- the guard

@pytest.mark.parametrize("route", ["/api/task/x/retry", "/api/task/x/reveal"])
def test_new_routes_need_the_token_and_the_page_origin_holds(route, shown, fake_claude):
    import server
    bare = TestClient(server.app, base_url=BASE)
    finished(status="done", files=["/tmp/x"])
    assert bare.post(route, json={"path": "/tmp/x"}).status_code == 401
    assert bare.post(route, headers={**AUTH, "Origin": "https://evil.example"},
                     json={"path": "/tmp/x"}).status_code == 403
    assert bare.post(route, headers={**AUTH, "Host": "evil.example:8788"},
                     json={"path": "/tmp/x"}).status_code == 403
    assert shown == [] and len(tasks.TASKS) == 1
