"""Wave 3 working together (WP17 reminders and briefing, WP18 costs), server side:

- the daily cap reaches the briefing (no paid news task; the page is told to
  read it itself), the remarques (no « Relancer ? » that would be refused),
  the Claude routines (not started, monsieur told why), the health check and
  get_status (« combien j'ai dépensé aujourd'hui ? »);
- quiet hours still hold the briefing back when the cap is reached too;
- a reminder that went off can be put off by voice (snooze_reminder) as well
  as from its card (the route), and both land in the same schedule;
- the voice instructions name the new abilities;
- keep-awake has a single owner: the scheduler (test_desktop checks the shell)."""
import time
from datetime import datetime

import pytest

from jarvis import (ares, briefing, config, health, inbox, info, instructions, remarques, scheduler,
                    store, tasks, tools, usage)

MONDAY_8 = datetime(2026, 10, 12, 8, 0)
NBSP = " "


@pytest.fixture
def capped(monkeypatch):
    """A 1 $ cap, already spent today: 0,70 $ of voice and 0,40 $ of Claude."""
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    usage.add_claude(0.40)
    data = store.load(usage.FILE, {})
    data[usage._today().isoformat()]["realtime"]["usd"] = 0.70
    store.save(usage.FILE, data)
    assert usage.over_daily_cap()


@pytest.fixture
def no_claude(monkeypatch):
    monkeypatch.setattr(tasks, "claude_command", lambda: pytest.fail("Claude lancé malgré le plafond"))


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(config, "CITY", "")
    monkeypatch.setattr(config, "BRIEFING_NEWS", True)
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "")
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": False, "error": "flux illisibles"})
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: None)

# ---------------------------------------------------------------- the briefing


def test_briefing_under_the_cap_may_start_the_news_task(published, sources, monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: started.append(k) or {"id": "t"})
    payload = briefing.run(MONDAY_8)
    assert payload["capped"] is False
    assert [k["profile"] for k in started] == ["recherche"]


def test_briefing_over_the_cap_starts_no_task_and_tells_the_page(published, sources, capped, no_claude,
                                                                monkeypatch):
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: pytest.fail("tâche Claude malgré le plafond"))
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    payload = briefing.run(MONDAY_8)
    assert payload["capped"] is True and payload["queued"] is False
    assert payload["text"].startswith("Bonjour monsieur.")  # the local brief costs nothing: still there
    (event,) = [e for e in published if e["type"] == "briefing"]
    assert event["capped"] is True
    assert inbox.pending()[0]["payload"]["capped"] is True  # a page opened later knows it too


def test_briefing_over_the_cap_still_waits_out_the_quiet_hours(published, sources, capped, monkeypatch):
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: pytest.fail("tâche Claude malgré le plafond"))
    monkeypatch.setattr(config, "QUIET_HOURS", "07:00-09:00")
    payload = briefing.run(MONDAY_8)
    assert payload["queued"] is True and payload["capped"] is True

# ---------------------------------------------------------------- the remarques


def add_failed_task():
    now = time.time()
    tasks.TASKS["f1"] = {"id": "f1", "title": "Comparer les aspirateurs", "status": "error", "prompt": "p",
                         "profile": "lecture", "origin": "voix", "started": now - 700, "ended": now - 600}


def test_remarques_over_the_cap_offer_no_retry(monkeypatch, capped):
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "")
    monkeypatch.setattr(remarques, "_voiced", {})
    add_failed_task()
    (r,) = remarques.items()
    assert r["key"] == "tache-f1" and r["action"] is None
    assert "Plafond du jour atteint" in r["why"] and "Réglages › Coûts" in r["why"]
    voice = remarques.for_voice()
    assert "ne proposez pas de la relancer" in voice and "Comparer" not in voice
    # The cap raised: the retry is offered again.
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    (r,) = remarques.items()
    assert r["action"]["type"] == "retry" and r["action"]["label"] == "Relancer ?"

# ---------------------------------------------------------------- Claude routines


def test_a_claude_routine_over_the_cap_is_not_run_and_monsieur_is_told(published, capped, no_claude):
    now = time.time()
    store.save(scheduler.FILE, [{"id": "r1", "kind": "task", "title": "Veille IA", "text": "Cherche…",
                                 "due": now - 5, "repeat": "daily", "profile": "recherche",
                                 "complexity": "simple", "created": now - 86400}])
    scheduler.tick(now)
    assert tasks.list_tasks() == []
    (warning,) = [e for e in published if e["type"] == "warning"]
    assert warning["text"].startswith("Routine « Veille IA » non lancée : Plafond du jour atteint (1,00 $)")
    (item,) = scheduler.items()
    assert item["due"] > now  # a daily routine: tomorrow, as usual

# ---------------------------------------------------------------- health and get_status


def test_health_says_todays_spending_and_the_cap(monkeypatch):
    assert health.check_costs() == []  # nothing spent, no cap: nothing to say
    usage.add_claude(0.42)
    (row,) = health.check_costs()
    assert row["id"] == "costs" and row["level"] == "info" and row["ok"] is True
    assert f"Aujourd'hui ≈ 0,42{NBSP}$" in row["message_fr"] and "Aucun plafond" in row["message_fr"]
    assert "Réglages › Coûts" in row["fix_fr"]
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 2.0)
    (row,) = health.check_costs()
    assert row["level"] == "ok" and f"Plafond du jour : 2,00{NBSP}$." in row["message_fr"]
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.5)
    (row,) = health.check_costs()
    assert row["level"] == "info" and f"84{NBSP}% du plafond" in row["message_fr"]
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.4)
    (row,) = health.check_costs()
    assert row["level"] == "warning" and row["ok"] is False
    assert "Plafond du jour atteint" in row["message_fr"] and "attendez demain" in row["fix_fr"]
    assert health.check_costs in health.CHECKS
    assert [c["id"] for c in health._run_one(health.check_costs, False)] == ["costs"]


def test_get_status_says_todays_spending(monkeypatch):
    out = tools.run_tool("get_status", {})
    assert out["spending"] == {"today": "≈ 0,00 $ (voix ≈ 0,00 $, Claude ≈ 0,00 $, estimation)",
                               "daily_cap": "aucun (Réglages › Coûts)", "cap_reached": False}
    usage.add_claude(1.5)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    spending = tools.run_tool("get_status", {})["spending"]
    assert spending["daily_cap"] == "1,00 $" and spending["cap_reached"] is True
    assert "Claude ≈ 1,50 $" in spending["today"]

# ---------------------------------------------------------------- snooze: card and voice


def test_a_reminder_put_off_by_voice_and_from_its_card_lands_in_one_schedule(published):
    from fastapi.testclient import TestClient

    import server
    from jarvis import security
    now = time.time()
    store.save(scheduler.FILE, [
        {"id": "pain", "kind": "reminder", "title": "Sortir le pain", "text": "Sortir le pain", "due": now - 1},
        {"id": "the", "kind": "reminder", "title": "Le thé", "text": "Le thé est prêt", "due": now - 1}])
    scheduler.tick(now)
    assert scheduler.items() == [] and [e["id"] for e in published if e["type"] == "reminder"] == ["pain", "the"]
    # By voice: « reporte le pain de 10 minutes ».
    out = tools.run_tool("snooze_reminder", {"query": "pain", "minutes": 10})
    assert out["ok"] is True and "Sortir le pain" in out["snoozed"]
    # From the card: '+1 h' on the tea.
    client = TestClient(server.app, base_url="http://127.0.0.1:8788", headers={"X-Jarvis-Token": security.TOKEN})
    r = client.post("/api/schedules/the/snooze", json={"minutes": 60})
    assert r.status_code == 200, r.text
    items = {i["text"]: i["due"] for i in scheduler.items()}
    assert items["Sortir le pain"] == pytest.approx(time.time() + 600, abs=30)
    assert items["Le thé est prêt"] == pytest.approx(time.time() + 3600, abs=30)
    # The voice knows what is coming (get_status and the instructions).
    upcoming = tools.run_tool("get_status", {})["upcoming"]
    assert len(upcoming) == 2 and any("Sortir le pain" in u for u in upcoming)
    assert "Sortir le pain" in instructions.build_instructions()

# ---------------------------------------------------------------- what the voice is told


def test_the_instructions_name_the_new_abilities():
    text = instructions.INSTRUCTIONS
    assert "snooze_reminder" in text and "1440 minutes" in text
    assert "dépense du jour" in text and "estimation" in text
    assert "Plafond du jour atteint" in text and "Réglages › Coûts" in text
    assert "# Briefing du matin\n" in text and "ne le refais pas" in text
    names = {t["name"] for t in tools.session_tools()}
    assert {"snooze_reminder", "get_status", "schedule", "cancel_schedule"} <= names
    status = next(t for t in tools.TOOLS if t["name"] == "get_status")
    assert "spending" in status["description"] and "daily cap" in status["description"]
