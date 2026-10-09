"""Wave 2 working together, server side: a setting changed in Réglages reaches
the module it configures (A.R.E.S, quiet hours, briefing days, the global
hotkey, the journal), the health check reads A.R.E.S and the Windows shell,
and the tray's Quitter stops JARVIS the way POST /api/shutdown does."""
import types
from datetime import datetime

import pytest
from fake_ares import FakeAres
from fastapi.testclient import TestClient
from test_desktop import FakeHotkeyApi, start_hotkey

import server
from jarvis import ares, config, desktop, health, inbox, scheduler, security, settings, shell, tasks, tools

AUTH = {"X-Jarvis-Token": security.TOKEN}


@pytest.fixture
def client(monkeypatch):
    """The API as the page calls it, every setting put back afterwards."""
    for s in settings.SCHEMA:
        if not s.attr.startswith("MODELS."):
            monkeypatch.setattr(config, s.attr, getattr(config, s.attr))
    monkeypatch.setattr(config, "MODELS", dict(config.MODELS))
    monkeypatch.setattr(settings, "_BOOT", dict(settings._BOOT))
    with TestClient(server.app, base_url="http://127.0.0.1:8788", headers=AUTH) as c:
        yield c


def forget_ares():
    ares._state.update(protocol=None, session=None, version=None, tools=None,
                       down_until=0.0, probed=0.0, up=False)
    ares._agenda.update(text="", at=0.0, ok=False)
    ares._last_published.update(available=None, lines=None)


@pytest.fixture
def fake_ares(monkeypatch):
    fake = FakeAres()
    forget_ares()
    monkeypatch.setattr(config, "ARES_URL", fake.url)
    yield fake
    if ares._refresher:
        ares._refresher.join(5)
    fake.stop()
    forget_ares()


@pytest.fixture
def fresh_shell(monkeypatch):
    yield
    shell.stop()
    monkeypatch.setattr(shell, "_hotkey", None)
    monkeypatch.setattr(shell, "_tray", None)


# ---------------------------------------------------------------- Réglages -> A.R.E.S

def test_ares_switched_in_reglages_is_asked_again_at_once(client, fake_ares, monkeypatch):
    """A.R.E.S was down a moment ago (30 s pause): switching it on in Réglages
    forgets that, so the tools and the agenda come back without waiting."""
    monkeypatch.setattr(config, "ARES", "off")
    ares._down("tombé tout à l'heure")
    assert client.put("/api/settings", json={"ares": "auto"}).status_code == 200
    assert ares.available() is True
    assert {"ares_lire", "ares_ajouter", "ares_modifier"} <= {t["name"] for t in tools.session_tools()}
    assert client.get("/api/ares").json()["available"] is True
    assert client.put("/api/settings", json={"ares": "off"}).status_code == 200
    asked = len(fake_ares.requests)
    assert client.get("/api/ares").json() == {"available": False, "mode": "off", "lines": [], "at": 0}
    assert "ares_lire" not in {t["name"] for t in tools.session_tools()}
    assert len(fake_ares.requests) == asked  # off means never called


# ---------------------------------------------------------------- Réglages -> quiet hours, briefing, journal

def test_quiet_hours_saved_by_reglages_are_read_the_same_by_the_inbox(client):
    assert client.put("/api/settings", json={"quiet_hours": "23h-6h30"}).json()["values"]["quiet_hours"] \
        == "23:00-06:30"
    assert inbox.quiet_hours(datetime(2026, 10, 9, 23, 30)) is True
    assert inbox.quiet_hours(datetime(2026, 10, 9, 7, 0)) is False
    assert client.get("/api/config").json()["quiet_hours"] == "23:00-06:30"


def test_briefing_days_saved_by_reglages_drive_the_scheduler(client):
    client.put("/api/settings", json={"briefing_days": ["lun", "mar", "mer", "jeu", "ven"]})
    assert config.BRIEFING_DAYS == "lun-ven" and scheduler.briefing_days() == {0, 1, 2, 3, 4}
    client.put("/api/settings", json={"briefing_days": "lun,mer,ven"})
    assert scheduler.briefing_days() == {0, 2, 4}
    client.put("/api/settings", json={"briefing_days": "lun,mar,mer,jeu,ven,sam,dim"})
    assert config.BRIEFING_DAYS == "tous" and scheduler.briefing_days() == set(range(7))


def test_no_journal_in_reglages_hides_recall(client):
    assert "recall" in {t["name"] for t in tools.session_tools()}
    client.put("/api/settings", json={"journal_days": 0})
    assert "recall" not in {t["name"] for t in tools.session_tools()}


# ---------------------------------------------------------------- Réglages -> the global hotkey

def test_a_hotkey_changed_in_reglages_is_swapped_on_the_hotkey_thread(client, monkeypatch, fresh_shell):
    published = []
    monkeypatch.setattr(shell.events, "publish", lambda kind, data: published.append({"type": kind, **data}))
    api = FakeHotkeyApi(taken={(shell.MOD_CONTROL | shell.MOD_ALT | shell.MOD_SHIFT, ord("P"))})
    monkeypatch.setattr(shell, "_hotkey", start_hotkey(api))
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(shell, "_state", {"url": "", "on_quit": None, "running": True})

    r = client.put("/api/settings", json={"hotkey": "Ctrl + Alt + Maj + H"})
    assert r.status_code == 200 and r.json()["applied"] == {"hotkey": "now"} and r.json()["restart"] == []
    assert api.registered == {(shell.MOD_CONTROL | shell.MOD_ALT | shell.MOD_SHIFT, ord("H"))}
    assert client.get("/api/config").json()["hotkey"] == {"combo": "Ctrl+Alt+Maj+H", "active": True}

    # Another program holds Ctrl+Alt+Maj+P: refused in French, the old one still works.
    r = client.put("/api/settings", json={"hotkey": "ctrl+alt+shift+p"})
    assert r.status_code == 400 and "indisponible" in r.json()["detail"]
    assert config.HOTKEY == "ctrl+alt+shift+h"
    assert api.registered == {(shell.MOD_CONTROL | shell.MOD_ALT | shell.MOD_SHIFT, ord("H"))}
    assert shell.hotkey_state() == {"active": True, "combo": "Ctrl+Alt+Maj+H"}


def test_the_page_learns_the_hotkey_even_without_windows(client):
    body = client.get("/api/config").json()
    assert body["hotkey"]["combo"] == shell.hotkey_label() and body["hotkey"]["active"] in (None, True, False)


# ---------------------------------------------------------------- health: A.R.E.S and Windows

def test_health_reads_ares_for_real_even_when_forced_on(fake_ares, monkeypatch):
    monkeypatch.setattr(config, "ARES", "on")
    assert health.check_ares()[0]["message_fr"].startswith("A.R.E.S répond")
    fake_ares.stop()
    # 'on' offers the tools without asking; the health check asks, after Revérifier.
    assert ares.available() is True
    out = health.check_ares(refresh=True)[0]
    assert out["level"] == "warning" and "pas joignable" in out["message_fr"]


def test_health_shows_the_windows_integration(monkeypatch, fresh_shell):
    monkeypatch.setattr(health, "_windows", lambda: True)
    assert health.check_windows() == []  # JARVIS not started (tests, another launcher)
    api = FakeHotkeyApi()
    monkeypatch.setattr(shell, "_hotkey", start_hotkey(api))
    monkeypatch.setattr(shell, "_state", {"url": "", "on_quit": None, "running": True})
    monkeypatch.setattr(config, "TRAY", True)
    monkeypatch.setattr(desktop, "autostart_enabled", lambda: False, raising=False)
    rows = {r["id"]: r for r in health.check_windows()}
    assert rows["hotkey"]["level"] == "ok" and "Ctrl+Alt+Maj+J" in rows["hotkey"]["message_fr"]
    assert rows["tray"]["level"] == "info" and "pip install" in rows["tray"]["fix_fr"]
    assert rows["autostart"]["ok"] and "ne démarre pas" in rows["autostart"]["message_fr"]
    # Windows refused it at start (another program holds it): a warning that says where to change it.
    shell._hotkey.ok = False
    rows = {r["id"]: r for r in health.check_windows()}
    assert rows["hotkey"]["level"] == "warning" and "Réglages › Système" in rows["hotkey"]["fix_fr"]
    assert "windows" in [fn.__name__.replace("check_", "") for fn in health.CHECKS]


# ---------------------------------------------------------------- the tray's Quitter

def test_tray_quit_stops_tasks_the_server_and_the_shell(monkeypatch, fresh_shell):
    stopped = []
    monkeypatch.setattr(tasks, "shutdown", lambda: stopped.append("tasks"))
    monkeypatch.setattr(server, "SERVER", types.SimpleNamespace(should_exit=False))
    api = FakeHotkeyApi()
    monkeypatch.setattr(shell, "_hotkey", start_hotkey(api))
    monkeypatch.setattr(shell, "_state", {"url": "", "on_quit": server._request_shutdown, "running": True})
    icon = types.SimpleNamespace(stop=lambda: stopped.append("icon"))
    shell._quit(icon)
    assert stopped == ["tasks", "icon"] and server.SERVER.should_exit is True
    shell.stop()  # what server.main does once uvicorn has stopped
    assert shell._hotkey is None and not shell.running()
    assert ("unregister", None, shell.HOTKEY_ID) in api.calls
