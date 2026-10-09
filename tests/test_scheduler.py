import time
from datetime import datetime, timedelta

import pytest

from jarvis import config, scheduler, tasks

FRIDAY = datetime(2026, 10, 9, 10, 0)  # a Friday


def test_clock_times_mean_the_next_occurrence():
    assert scheduler.compute_due("10:30", now=FRIDAY) == datetime(2026, 10, 9, 10, 30)
    assert scheduler.compute_due("20h15", now=FRIDAY) == datetime(2026, 10, 9, 20, 15)
    assert scheduler.compute_due("8h", now=FRIDAY) == datetime(2026, 10, 10, 8, 0)


def test_delays_and_dates():
    assert scheduler.compute_due(delay_minutes=5, now=FRIDAY) == FRIDAY + timedelta(minutes=5)
    assert scheduler.compute_due("2026-10-12T09:00", now=FRIDAY) == datetime(2026, 10, 12, 9, 0)


@pytest.mark.parametrize("kwargs", [{"at": "bientôt"}, {}, {"delay_minutes": -3},
                                    {"at": "2020-01-01T09:00"}, {"at": "25:00"}])
def test_nonsense_is_refused(kwargs):
    with pytest.raises(ValueError):
        scheduler.compute_due(now=FRIDAY, **kwargs)


def test_repeats_skip_ahead():
    eight = FRIDAY.replace(hour=8).timestamp()
    assert datetime.fromtimestamp(scheduler.next_due(eight, "daily", eight)) == datetime(2026, 10, 10, 8)
    assert datetime.fromtimestamp(scheduler.next_due(eight, "weekdays", eight)) == datetime(2026, 10, 12, 8)
    assert datetime.fromtimestamp(scheduler.next_due(eight, "weekly", eight)) == datetime(2026, 10, 16, 8)


def test_due_reminder_is_announced_once(published):
    scheduler.add("reminder", "Garage", "Appeler le garage", delay_minutes=1)
    scheduler.tick(time.time() + 120)
    reminders = [e for e in published if e["type"] == "reminder"]
    assert [r["text"] for r in reminders] == ["Appeler le garage"]
    assert scheduler.items() == []
    scheduler.tick(time.time() + 240)
    assert len([e for e in published if e["type"] == "reminder"]) == 1


def test_repeating_item_is_rescheduled(published):
    item = scheduler.add("reminder", "Pilule", "Prendre la pilule", delay_minutes=1, repeat="daily")
    scheduler.tick(time.time() + 120)
    (kept,) = scheduler.items()
    assert kept["id"] == item["id"]
    assert kept["due"] - item["due"] == pytest.approx(86400, abs=3700)  # a day, give or take DST


def test_late_reminders_say_so(published):
    scheduler.add("reminder", "Réunion", "Réunion", delay_minutes=1)
    scheduler.tick(time.time() + 3600)
    (reminder,) = [e for e in published if e["type"] == "reminder"]
    assert reminder["late_minutes"] >= 55


def test_routine_starts_a_claude_task(published, monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: started.append((a, k)))
    scheduler.add("task", "Veille IA", "Résume les actus IA", delay_minutes=1, profile="recherche")
    scheduler.tick(time.time() + 120)
    ((args, kwargs),) = started
    assert args == ("Veille IA", "Résume les actus IA")
    assert kwargs["origin"] == "routine" and kwargs["profile"] == "recherche"


def test_cancel_by_id_or_words(published):
    a = scheduler.add("reminder", "Appeler maman", "Appeler maman", delay_minutes=10)
    scheduler.add("reminder", "Sortir le chien", "Sortir le chien", delay_minutes=10)
    assert [i["id"] for i in scheduler.cancel(a["id"])] == [a["id"]]
    assert [i["title"] for i in scheduler.cancel("chien")] == ["Sortir le chien"]
    assert scheduler.cancel("rien") == []


def test_briefing_runs_once_in_the_morning(published, monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: started.append(k))
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:00")
    monkeypatch.setattr(config, "CITY", "Lyon")
    scheduler.tick(datetime(2026, 10, 9, 7, 59).timestamp())
    assert started == []
    scheduler.tick(datetime(2026, 10, 9, 8, 30).timestamp())
    scheduler.tick(datetime(2026, 10, 9, 9, 0).timestamp())
    assert [k["origin"] for k in started] == ["briefing"]
    scheduler.tick(datetime(2026, 10, 10, 15, 0).timestamp())  # switched on in the afternoon
    assert len(started) == 1


def test_briefing_prompt_lists_todays_reminders(published):
    scheduler.add("reminder", "Dentiste", "Dentiste", at="23:59")
    prompt = scheduler.briefing_prompt(datetime.now())
    assert "Dentiste" in prompt
    assert "météo" in prompt
