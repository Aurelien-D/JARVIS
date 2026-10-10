"""The local morning briefing (WP17): built here from JARVIS's reminders, the
A.R.E.S agenda and the weather, without any Claude task; on the right days
only, queued during quiet hours, and the only web-enabled prompt (headlines,
when the feeds fail) holds nothing private."""
import subprocess
from datetime import datetime

import pytest

from jarvis import ares, briefing, config, desktop, events, health, inbox, info, memory, scheduler, store, tasks

REAL_ONBOARDED = health.onboarded
MONDAY_8 = datetime(2026, 10, 12, 8, 0)
SATURDAY_8 = datetime(2026, 10, 10, 8, 0)
AGENDA = ("• Appeler le labo — Aujourd'hui · 14:00\n"
          "• Réunion budget — Aujourd'hui · 16:30\n"
          "• Payer la facture EDF — En retard (2 j)\n"
          "• (rappel) Dentiste — Demain · 09:00")
WEATHER = "Aujourd'hui à Laon : pluie faible, de 8 à 14 °C, risque de pluie 80 %."


@pytest.fixture(autouse=True)
def onboarded(monkeypatch):
    """Monsieur has done the Mise en route (the briefing waits for it)."""
    monkeypatch.setattr(health, "onboarded", lambda: True)


@pytest.fixture
def sources(monkeypatch):
    """A.R.E.S and Open-Meteo as fakes; Claude must never be started."""
    calls = {"weather": [], "tasks": [], "popen": []}
    monkeypatch.setattr(config, "CITY", "Laon")
    monkeypatch.setattr(config, "BRIEFING_NEWS", False)
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: AGENDA)
    monkeypatch.setattr(info, "weather", lambda city=None, quand="maintenant":
                        calls["weather"].append((city, quand)) or {"ok": True, "text": WEATHER})
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: calls["tasks"].append((a, k)) or {"id": "t"})
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls["popen"].append(a))
    monkeypatch.setattr(tasks.subprocess, "Popen", lambda *a, **k: calls["popen"].append(a))
    return calls


@pytest.fixture
def no_toasts(monkeypatch):
    shown = []
    monkeypatch.setattr(desktop, "toast", lambda title, body: shown.append((title, body)) or True)
    monkeypatch.setattr(desktop, "attention_state", lambda: "ok")
    monkeypatch.setattr(inbox, "_toasted", {})
    return shown


def test_local_brief_has_the_weather_and_the_agenda_count_without_claude(published, sources):
    """Acceptance: with fake A.R.E.S and weather, the text holds the weather and
    the agenda count, and no claude process is spawned."""
    text = briefing.local_brief(MONDAY_8)
    assert text.startswith("Bonjour monsieur. Nous sommes lundi 12 octobre.")
    assert WEATHER in text
    assert "Dans A.R.E.S : 2 éléments aujourd'hui, 1 tâche en retard." in text
    assert "Appeler le labo" not in text  # counts only: the agenda's titles stay in A.R.E.S
    assert sources["weather"] == [("Laon", "aujourdhui")]
    payload = briefing.run(MONDAY_8)
    assert payload["text"] == text  # deterministic: the same facts, the same words
    assert sources["tasks"] == [] and sources["popen"] == []
    assert [e["type"] for e in published] == ["briefing"]


def test_brief_lists_the_days_reminders_and_routines(published, sources):
    store.save(scheduler.FILE, [
        {"id": "a", "kind": "reminder", "title": "Appeler le garage", "text": "x",
         "due": MONDAY_8.replace(hour=10, minute=30).timestamp()},
        {"id": "b", "kind": "task", "title": "Veille IA", "text": "y", "due": MONDAY_8.replace(hour=14).timestamp()},
        {"id": "c", "kind": "reminder", "title": "Hier", "text": "z", "due": MONDAY_8.replace(hour=7).timestamp()},
        {"id": "d", "kind": "reminder", "title": "Demain", "text": "w",
         "due": MONDAY_8.replace(day=13, hour=9).timestamp()},
        {"title": "sans id ni date"},
    ])
    text = briefing.local_brief(MONDAY_8)
    assert "Vos rappels du jour : 10 h 30, Appeler le garage ; 14 h, routine « Veille IA »." in text
    assert "Hier" not in text and "Demain," not in text
    store.save(scheduler.FILE, [])
    assert "Aucun rappel JARVIS aujourd'hui." in briefing.local_brief(MONDAY_8)


def test_brief_without_ares_or_city_says_what_it_knows(published, sources, monkeypatch):
    monkeypatch.setattr(config, "CITY", "")
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "")  # A.R.E.S off or away
    text = briefing.local_brief(MONDAY_8)
    assert "A.R.E.S" not in text and "°C" not in text and sources["weather"] == []
    assert text == "Bonjour monsieur. Nous sommes lundi 12 octobre. Aucun rappel JARVIS aujourd'hui. Bonne journée."
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "Rien de planifié sur les 14 prochains jours.")
    assert "Rien de prévu aujourd'hui dans A.R.E.S." in briefing.local_brief(MONDAY_8)

    def boom(*a, **k):
        raise RuntimeError("panne")
    monkeypatch.setattr(ares, "agenda_text", boom)
    monkeypatch.setattr(config, "CITY", "Laon")
    monkeypatch.setattr(info, "weather", boom)
    assert briefing.local_brief(MONDAY_8).startswith("Bonjour monsieur.")  # a broken source is left out


def test_agenda_counts():
    assert briefing.agenda_counts(AGENDA) == (2, 1)
    assert briefing.agenda_counts("• Rapport — Aujourd’hui (en retard)\n• Rien de planifié") == (0, 1)
    assert briefing.agenda_counts("") == (0, 0)


def test_no_briefing_on_a_day_left_out(published, sources, monkeypatch):
    """Acceptance: on Saturday with BRIEFING_DAYS='lun-ven' no briefing is produced."""
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:00")
    monkeypatch.setattr(config, "BRIEFING_DAYS", "lun-ven")
    monkeypatch.setattr(briefing, "_thread", None)
    scheduler.tick(SATURDAY_8.replace(minute=30).timestamp())
    assert briefing._thread is None and published == [] and inbox.pending() == []
    assert "briefing_date" not in store.load("state.json", {})
    scheduler.tick(MONDAY_8.replace(minute=30).timestamp())
    briefing._thread.join(5)
    assert [e["type"] for e in published] == ["briefing"]


def test_briefing_in_quiet_hours_is_queued_not_spoken(sources, no_toasts, monkeypatch):
    """Acceptance: during quiet hours the briefing goes to the inbox, marked
    queued (the page keeps it behind the badge), and nothing pops up."""
    seen = []
    real_publish = events.publish
    monkeypatch.setattr(events, "publish", lambda kind, data: seen.append((kind, data)) or real_publish(kind, data))
    monkeypatch.setattr(config, "QUIET_HOURS", "22:30-07:30")
    monkeypatch.setattr(config, "BRIEFING_TIME", "06:00")
    monkeypatch.setattr(config, "BRIEFING_DAYS", "tous")
    scheduler.tick(MONDAY_8.replace(hour=6, minute=10).timestamp())
    briefing._thread.join(5)
    (kind, data), = [(k, d) for k, d in seen if k == "briefing"]
    assert data["queued"] is True and data["inbox_id"]
    (item,) = inbox.pending()
    assert item["kind"] == "briefing" and item["payload"]["queued"] is True
    assert WEATHER in item["payload"]["text"]
    assert no_toasts == []  # quiet hours: no notification either
    # Outside quiet hours, with no page open: one notification, not queued.
    monkeypatch.setattr(config, "QUIET_HOURS", "")  # notify_offline reads the real clock
    out = briefing.deliver("Bonjour monsieur.", MONDAY_8.replace(day=13, hour=8))
    assert out["queued"] is False
    assert no_toasts == [("Briefing du matin", "Votre briefing du matin est prêt.")]


def test_do_not_disturb_queues_the_briefing_too(published, sources, monkeypatch):
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    inbox.set_dnd(None)
    assert briefing.deliver("x", MONDAY_8)["queued"] is False
    import time
    inbox.set_dnd(time.time() + 3600)
    assert briefing.deliver("x", MONDAY_8.replace(day=13))["queued"] is True


def test_headlines_come_from_the_feeds_without_a_task(published, sources, monkeypatch):
    monkeypatch.setattr(config, "BRIEFING_NEWS", True)
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": True, "headlines": [{"title": "Titre un"},
                                                                               {"title": "Titre deux"}]})
    payload = briefing.run(MONDAY_8)
    assert "Les titres : Titre un ; Titre deux." in payload["text"]
    assert sources["tasks"] == [] and sources["popen"] == []


def test_news_task_when_the_feeds_fail_has_nothing_private(published, sources, monkeypatch):
    """The only prompt with web access: the date, never the agenda, the
    reminders, the city or monsieur's memory."""
    monkeypatch.setattr(config, "BRIEFING_NEWS", True)
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": False, "error": "Actualités indisponibles"})
    memory.remember("Monsieur a rendez-vous chez le notaire")
    store.save(scheduler.FILE, [{"id": "a", "kind": "reminder", "title": "Appeler le garage", "text": "garage",
                                 "due": MONDAY_8.replace(hour=11).timestamp()}])
    payload = briefing.run(MONDAY_8)
    assert "Appeler le garage" in payload["text"]  # the local brief still has everything
    ((args, kwargs),) = sources["tasks"]
    title, prompt = args
    assert kwargs["profile"] == "recherche" and kwargs["origin"] == "routine"
    assert "lundi 12 octobre 2026" in prompt
    for private in ("garage", "Laon", "labo", "EDF", "budget", "Dentiste", "notaire", "rappel", "agenda"):
        assert private.lower() not in prompt.lower(), private
    assert briefing.news_prompt(MONDAY_8) == prompt


def test_briefing_task_refused_does_not_lose_the_brief(published, sources, monkeypatch):
    def refuse(*a, **k):
        raise ValueError("Plafond du jour atteint.")
    monkeypatch.setattr(config, "BRIEFING_NEWS", True)
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": False, "error": "x"})
    monkeypatch.setattr(tasks, "create_task", refuse)
    assert briefing.run(MONDAY_8)["text"].startswith("Bonjour monsieur.")
    assert [e["type"] for e in published] == ["briefing"]


def test_briefing_once_a_day_in_its_window(published, sources, monkeypatch):
    ran = []
    monkeypatch.setattr(briefing, "run", lambda now=None: ran.append(now))
    monkeypatch.setattr(config, "BRIEFING_DAYS", "tous")
    for spec in ("", "25:00", "midi"):
        monkeypatch.setattr(config, "BRIEFING_TIME", spec)
        briefing.maybe_run(MONDAY_8.timestamp())
    assert ran == []
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:00")
    briefing.maybe_run(MONDAY_8.replace(hour=12, minute=1).timestamp())  # past the 4 hours
    briefing.maybe_run(MONDAY_8.replace(minute=5).timestamp())
    briefing._thread.join(5)
    briefing.maybe_run(MONDAY_8.replace(minute=6).timestamp())
    assert ran == [MONDAY_8.replace(minute=5)]


def test_no_briefing_before_the_mise_en_route_then_it_follows_it(published, sources, monkeypatch):
    # A first launch on a weekday morning: no empty briefing on top of the
    # onboarding dialog; the day is not claimed, so it comes once that is done.
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:00")
    monkeypatch.setattr(config, "BRIEFING_DAYS", "tous")
    monkeypatch.setattr(briefing, "_thread", None)
    monkeypatch.setitem(briefing._done, "day", None)
    monkeypatch.setattr(health, "onboarded", REAL_ONBOARDED)
    assert health.onboarded() is False  # a fresh data folder
    scheduler.tick(MONDAY_8.replace(minute=10).timestamp())
    assert briefing._thread is None and published == [] and inbox.pending() == []
    assert "briefing_date" not in store.load("state.json", {})
    health.set_onboarded()
    scheduler.tick(MONDAY_8.replace(minute=40).timestamp())
    briefing._thread.join(5)
    assert [e["type"] for e in published] == ["briefing"]
    assert store.load("state.json", {})["briefing_date"] == MONDAY_8.date().isoformat()
