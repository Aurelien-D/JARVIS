"""The side panel's reminder routes (WP17): add, edit, snooze, delete by id,
and the remarques. A full-access routine never comes from here, and none of
these routes does anything without the page's token, from a foreign Host or
from another site's page."""
import re
import time

import pytest
from fastapi.testclient import TestClient

from jarvis import scheduler, security, store

BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}
NEW_ROUTES = {"/api/schedules": {"POST"}, "/api/schedules/{item_id}": {"PATCH", "DELETE"},
              "/api/schedules/{item_id}/snooze": {"POST"}, "/api/remarques": {"GET"},
              "/api/remarques/{key}/dismiss": {"POST"}}


@pytest.fixture
def client():
    import server
    return TestClient(server.app, base_url=BASE, headers=AUTH)


def test_add_a_reminder_from_the_page(client, published):
    r = client.post("/api/schedules", json={"title": "Pain", "text": "Sortir le pain", "at": "dans un quart d'heure"})
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["kind"] == "reminder" and item["due"] == pytest.approx(time.time() + 900, abs=5)
    assert [i["id"] for i in client.get("/api/schedules").json()] == [item["id"]]
    assert client.post("/api/schedules", json={"text": "x", "at": "25:00"}).json()["detail"].startswith("heure invalide")
    assert client.post("/api/schedules", json={"text": "x", "delay_minutes": "dix"}).json()["detail"] \
        == "délai invalide : donnez un nombre de minutes"
    assert client.post("/api/schedules", json={"text": "", "delay_minutes": 5}).status_code == 400
    routine = client.post("/api/schedules", json={"kind": "task", "title": "Veille", "text": "Actus IA",
                                                  "at": "08:00", "repeat": "weekdays", "profile": "recherche"})
    assert routine.status_code == 200 and routine.json()["item"]["profile"] == "recherche"


def test_a_full_access_routine_is_refused_from_the_page(client, published):
    for kind in ("task", "TASK", "Task"):
        r = client.post("/api/schedules", json={"kind": kind, "title": "Ménage", "text": "rm -rf",
                                                "at": "08:00", "repeat": "daily", "profile": "complet"})
        assert r.status_code == 403, kind
        assert r.json()["detail"] == scheduler.T.complet_api
    assert scheduler.items() == []


def test_edit_a_reminder(client, published):
    item = scheduler.add("reminder", "Pain", "Sortir le pain", delay_minutes=30)
    url = f"/api/schedules/{item['id']}"
    r = client.patch(url, json={"title": "Sortir la baguette"})
    assert r.status_code == 200 and r.json()["item"]["text"] == "Sortir la baguette"
    due = time.time() + 7200
    assert client.patch(url, json={"due": due}).json()["item"]["due"] == pytest.approx(due)
    assert client.patch(url, json={"at": "demain à 7 h"}).status_code == 200
    assert client.patch(url, json={"at": "25:00"}).status_code == 400
    assert client.patch(url, json={"due": time.time() - 3600}).json()["detail"] == "cette date est déjà passée"
    assert client.patch(url, json={}).json()["detail"] == "rien à modifier"
    assert client.patch("/api/schedules/zzz", json={"title": "x"}).status_code == 404
    assert published[-1]["type"] == "schedules"


def test_a_full_access_routine_keeps_its_instruction_when_edited(client, published):
    item = scheduler.add("task", "Ménage", "Vide la corbeille", at="08:00", repeat="daily", profile="complet",
                         allow_complet=True)
    url = f"/api/schedules/{item['id']}"
    for body in ({"text": "Formate le disque"}, {"repeat": "weekly"}, {"days": ["lun"]}):
        r = client.patch(url, json=body)
        assert r.status_code == 403 and "accès complet" in r.json()["detail"]
    assert client.patch(url, json={"title": "Grand ménage", "at": "09:30"}).status_code == 200
    (kept,) = scheduler.items()
    assert kept["text"] == "Vide la corbeille" and kept["repeat"] == "daily" and kept["profile"] == "complet"
    # Fields the route doesn't know are ignored: a routine never changes kind or profile here.
    client.patch(url, json={"profile": "lecture", "kind": "reminder", "title": "Ménage"})
    assert scheduler.items()[0]["profile"] == "complet" and scheduler.items()[0]["kind"] == "task"


def test_snooze_a_reminder_that_went_off(client, published):
    item = scheduler.add("reminder", "Pain", "Sortir le pain", delay_minutes=1)
    scheduler.tick(time.time() + 120)
    url = f"/api/schedules/{item['id']}/snooze"
    r = client.post(url, json={"minutes": 10})
    assert r.status_code == 200
    copy = r.json()["item"]
    assert copy["text"] == "Sortir le pain" and copy["due"] == pytest.approx(time.time() + 600, abs=5)
    assert client.post(url, json={"minutes": 60}).json()["item"]["id"] == copy["id"]  # moved, not doubled
    assert client.post(url).status_code == 200  # 10 minutes by default
    assert client.post(url, json={"minutes": "dix"}).status_code == 400
    assert client.post("/api/schedules/zzz/snooze", json={"minutes": 10}).status_code == 404
    assert client.post("/api/schedules/pain/snooze", json={"minutes": 10}).status_code == 404  # ids only
    assert len(scheduler.items()) == 1


def test_delete_takes_an_id_never_words(client, published):
    a = scheduler.add("reminder", "Garage", "Appeler le garage", delay_minutes=10)
    assert client.delete("/api/schedules/garage").json() == {"ok": True, "removed": 0}
    assert client.delete(f"/api/schedules/{a['id']}").json() == {"ok": True, "removed": 1}
    assert scheduler.items() == []


def test_remarques_routes(client, published, monkeypatch):
    from jarvis import ares
    assert client.get("/api/remarques").json() == []
    assert client.post("/api/remarques/ares-retard/dismiss").status_code == 404
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "• A — En retard (1 j)\n• B — En retard (3 j)")
    (r,) = client.get("/api/remarques").json()
    assert r["key"] == "ares-retard" and "voice" not in r
    out = client.post("/api/remarques/ares-retard/dismiss").json()
    assert out["ok"] and out["days"] == 1
    assert client.get("/api/remarques").json() == []


def test_config_says_reminders_can_be_edited(client):
    assert client.get("/api/config").json()["edit"]["schedules"] is True


def test_new_routes_do_nothing_without_the_token_or_from_elsewhere(published):
    import server
    raw = TestClient(server.app, base_url=BASE)
    item = scheduler.add("reminder", "Pain", "Sortir le pain", delay_minutes=30)
    before = store.load(scheduler.FILE, [])
    body = {"title": "x", "text": "x", "at": "08:00", "minutes": 10, "kind": "task", "profile": "complet"}
    from jarvis import api_schedules
    served = {}
    for route in api_schedules.router.routes:
        served.setdefault(route.path, set()).update(route.methods or ())
    for path, methods in NEW_ROUTES.items():
        assert methods <= served.get(path, set()), path
        url = re.sub(r"\{[^}]+\}", item["id"], path).replace(f"/remarques/{item['id']}", "/remarques/ares-retard")
        for method in methods:
            kw = {} if method in ("GET", "DELETE") else {"json": body}
            assert raw.request(method, url, **kw).status_code == 401, (method, path)
            assert raw.request(method, url, headers={"X-Jarvis-Token": "nope"}, **kw).status_code == 401
            r = raw.request(method, url, headers={**AUTH, "Host": "evil.example:8788"}, **kw)
            assert r.status_code == 403, (method, path)
            r = raw.request(method, url, headers={**AUTH, "Origin": "https://evil.example"}, **kw)
            assert r.status_code == 403, (method, path)
    assert store.load(scheduler.FILE, []) == before
    assert store.load("remarques.json", {}) == {}
