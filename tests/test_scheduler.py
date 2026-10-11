import logging
import threading
import time
from datetime import datetime, timedelta

import pytest

from jarvis import config, desktop, events, inbox, scheduler, store, tasks, tools

FRIDAY = datetime(2026, 10, 9, 10, 0)  # a Friday


@pytest.fixture(autouse=True)
def fresh_logs(monkeypatch):
    """_log_once remembers what it logged: each test starts with a clean slate."""
    monkeypatch.setattr(scheduler, "_logged", {})


def write(items):
    store.save(scheduler.FILE, items)


# ---------------------------------------------------------------- compute_due

def test_clock_times_mean_the_next_occurrence():
    assert scheduler.compute_due("10:30", now=FRIDAY) == datetime(2026, 10, 9, 10, 30)
    assert scheduler.compute_due("20h15", now=FRIDAY) == datetime(2026, 10, 9, 20, 15)
    assert scheduler.compute_due("8h", now=FRIDAY) == datetime(2026, 10, 10, 8, 0)


def test_delays_and_dates():
    assert scheduler.compute_due(delay_minutes=5, now=FRIDAY) == FRIDAY + timedelta(minutes=5)
    assert scheduler.compute_due(delay_minutes="7,5", now=FRIDAY) == FRIDAY + timedelta(minutes=7.5)
    assert scheduler.compute_due(delay_minutes="10 min", now=FRIDAY) == FRIDAY + timedelta(minutes=10)
    assert scheduler.compute_due("2026-10-12T09:00", now=FRIDAY) == datetime(2026, 10, 12, 9, 0)


@pytest.mark.parametrize("kwargs", [{"at": "bientôt"}, {}, {"delay_minutes": -3},
                                    {"at": "2020-01-01T09:00"}, {"at": "25:00"}, {"at": "9"},
                                    {"delay_minutes": float("nan")}, {"delay_minutes": True},
                                    {"delay_minutes": 10 ** 9}, {"at": "le 31 février"},
                                    {"at": "aujourd'hui à 8 h"}, {"at": "x" * 500}])
def test_nonsense_is_refused(kwargs):
    with pytest.raises(ValueError):
        scheduler.compute_due(now=FRIDAY, **kwargs)


def test_hour_and_delay_errors_are_french():
    """Acceptance: '25:00' → 'heure invalide…'; a delay 'dix' → 'délai invalide…'."""
    for at in ("25:00", "12:60", "24h", "demain à 25 h"):
        with pytest.raises(ValueError, match=r"^heure invalide : "):
            scheduler.compute_due(at, now=FRIDAY)
    with pytest.raises(ValueError, match=r"^délai invalide : donnez un nombre de minutes"):
        scheduler.compute_due(delay_minutes="dix", now=FRIDAY)
    with pytest.raises(ValueError, match=r"^heure non comprise : bientôt \(par exemple"):
        scheduler.compute_due("bientôt", now=FRIDAY)


@pytest.mark.parametrize("at, expected", [
    ("dans un quart d'heure", FRIDAY + timedelta(minutes=15)),
    ("dans une demi-heure", FRIDAY + timedelta(minutes=30)),
    ("dans trois quarts d'heure", FRIDAY + timedelta(minutes=45)),
    ("dans une heure et demie", FRIDAY + timedelta(minutes=90)),
    ("dans 1 h 30", FRIDAY + timedelta(minutes=90)),
    ("dans dix minutes", FRIDAY + timedelta(minutes=10)),
    ("dans 2 heures", FRIDAY + timedelta(hours=2)),
    ("mardi prochain à 9 h", datetime(2026, 10, 13, 9, 0)),
    ("Mardi prochain à 9h", datetime(2026, 10, 13, 9, 0)),
    ("à midi", datetime(2026, 10, 9, 12, 0)),
    ("à minuit", datetime(2026, 10, 10, 0, 0)),
    ("midi et demi", datetime(2026, 10, 9, 12, 30)),
    ("minuit moins dix", datetime(2026, 10, 9, 23, 50)),
    ("mardi", datetime(2026, 10, 13, 9, 0)),                 # a day alone: 09:00
    ("vendredi à 11 h", datetime(2026, 10, 9, 11, 0)),       # today, still ahead
    ("vendredi à 9 h", datetime(2026, 10, 16, 9, 0)),        # today, past: next week
    ("vendredi prochain", datetime(2026, 10, 16, 9, 0)),     # 'prochain' is never today
    ("demain", datetime(2026, 10, 10, 9, 0)),
    ("demain soir à 8 h", datetime(2026, 10, 10, 20, 0)),
    ("ce soir à 20 h", datetime(2026, 10, 9, 20, 0)),
    ("8 h du soir", datetime(2026, 10, 9, 20, 0)),
    ("après-demain à 7 h 30", datetime(2026, 10, 11, 7, 30)),
    ("à neuf heures et quart", datetime(2026, 10, 10, 9, 15)),
    ("à 9 h moins le quart", datetime(2026, 10, 10, 8, 45)),
    ("le 12 octobre à 9 h", datetime(2026, 10, 12, 9, 0)),
    ("lundi 12 octobre à 18 h", datetime(2026, 10, 12, 18, 0)),
    ("le 1er novembre", datetime(2026, 11, 1, 9, 0)),
    ("le 5", datetime(2026, 11, 5, 9, 0)),                   # the 5th has passed: next month
    ("12/10 à 9h30", datetime(2026, 10, 12, 9, 30)),
    ("dans 3 jours à 9 h", datetime(2026, 10, 12, 9, 0)),
    # 'après-midi' is an afternoon: its 'midi' is not noon.
    ("cet après-midi", datetime(2026, 10, 9, 14, 0)),
    ("cet après-midi à 4 h", datetime(2026, 10, 9, 16, 0)),
    ("à 15 h cet après-midi", datetime(2026, 10, 9, 15, 0)),
    ("à 4 h de l'après-midi", datetime(2026, 10, 9, 16, 0)),
    ("demain après-midi", datetime(2026, 10, 10, 14, 0)),
    ("demain après-midi à 3 h", datetime(2026, 10, 10, 15, 0)),
    ("demain apres midi a 15h", datetime(2026, 10, 10, 15, 0)),
    ("samedi après-midi à 15 h", datetime(2026, 10, 10, 15, 0)),
    ("mardi après-midi", datetime(2026, 10, 13, 14, 0)),
    ("demain à midi", datetime(2026, 10, 10, 12, 0)),
])
def test_plain_french_is_understood(at, expected):
    """Acceptance: 'dans un quart d'heure' → now + 15 min; 'mardi prochain à
    9 h' → the next Tuesday at 09:00 (frozen clock: Friday 9 October 10:00)."""
    assert scheduler.compute_due(at, now=FRIDAY) == expected


def test_an_iso_time_ending_in_z_is_local_time_on_every_python():
    # Python 3.10's fromisoformat refuses 'Z'; 3.11+ reads it as UTC (2 h off
    # on a French PC in summer). The tool contract is local time: both read it so.
    assert scheduler.compute_due("2026-10-12T09:00:00Z", now=FRIDAY) == datetime(2026, 10, 12, 9, 0)
    assert scheduler.compute_due("2026-10-12 09:00z", now=FRIDAY) == datetime(2026, 10, 12, 9, 0)
    with pytest.raises(ValueError):
        scheduler.compute_due("2026-10-12Z", now=FRIDAY)


def test_repeats_skip_ahead():
    eight = FRIDAY.replace(hour=8).timestamp()
    assert datetime.fromtimestamp(scheduler.next_due(eight, "daily", eight)) == datetime(2026, 10, 10, 8)
    assert datetime.fromtimestamp(scheduler.next_due(eight, "weekdays", eight)) == datetime(2026, 10, 12, 8)
    assert datetime.fromtimestamp(scheduler.next_due(eight, "weekly", eight)) == datetime(2026, 10, 16, 8)


def test_monthly_keeps_its_day_even_after_a_short_month():
    jan31 = datetime(2027, 1, 31, 9, 0).timestamp()
    feb = scheduler.next_due(jan31, "monthly", jan31, month_day=31)
    assert datetime.fromtimestamp(feb) == datetime(2027, 2, 28, 9, 0)
    assert datetime.fromtimestamp(scheduler.next_due(feb, "monthly", feb, month_day=31)) == datetime(2027, 3, 31, 9)
    # Off for months: the next one after now, not every missed one.
    later = datetime(2027, 6, 2).timestamp()
    assert datetime.fromtimestamp(scheduler.next_due(jan31, "monthly", later, month_day=31)) == datetime(2027, 6, 30, 9)


def test_days_repeat_follows_the_chosen_weekdays():
    eight = FRIDAY.replace(hour=8).timestamp()  # Friday
    nxt = scheduler.next_due(eight, "days", eight, days=[0, 3])  # Monday, Thursday
    assert datetime.fromtimestamp(nxt) == datetime(2026, 10, 12, 8)
    assert datetime.fromtimestamp(scheduler.next_due(nxt, "days", nxt, days=[0, 3])) == datetime(2026, 10, 15, 8)
    assert scheduler.parse_days(["lundi", "jeu", 4]) == [0, 3, 4]
    assert scheduler.parse_days("lun-mer") == [0, 1, 2]
    assert scheduler.parse_days("sam-lun") == [0, 5, 6]
    with pytest.raises(ValueError, match="jour invalide"):
        scheduler.parse_days(["funday"])


def test_long_absence_jumps_ahead_quickly():
    old = datetime(2020, 1, 1, 8).timestamp()
    now = datetime(2026, 10, 9, 10).timestamp()
    t0 = time.perf_counter()
    nxt = scheduler.next_due(old, "weekly", now)
    assert time.perf_counter() - t0 < 0.5
    assert datetime.fromtimestamp(nxt) == datetime(2026, 10, 14, 8)  # 1 Jan 2020 was a Wednesday


def test_monthly_and_days_items_are_described(published):
    item = scheduler.add("reminder", "Loyer", "Payer le loyer", at="le 5",
                         repeat="monthly")
    assert item["month_day"] == datetime.fromtimestamp(item["due"]).day
    assert "(chaque mois)" in scheduler.describe(item)
    gym = scheduler.add("reminder", "Sport", "Sport", at="18:00", days=["lun", "jeu"])
    assert gym["repeat"] == "days" and gym["days"] == [0, 3]
    assert datetime.fromtimestamp(gym["due"]).weekday() in (0, 3)
    assert "(le lundi et jeudi)" in scheduler.describe(gym)
    with pytest.raises(ValueError, match="jours"):
        scheduler.add("reminder", "x", "x", at="18:00", repeat="days")
    with pytest.raises(ValueError, match="répétition inconnue"):
        scheduler.add("reminder", "x", "x", at="18:00", repeat="dayly")

# ---------------------------------------------------------------- firing


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


def test_fire_with_no_page_goes_to_the_inbox_first_then_notifies(monkeypatch):
    """Acceptance: _fire with 0 subscribers creates an inbox item and calls
    inbox.notify_offline (and the inbox has it before any page could)."""
    seen, notified = [], []

    def publish(kind, data):
        seen.append((kind, data, [i["payload"].get("text") for i in inbox.pending()]))
    monkeypatch.setattr(events, "publish", publish)
    monkeypatch.setattr(events, "has_subscribers", lambda: False)
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: notified.append((a, k)) or True)
    scheduler._fire({"id": "r1", "kind": "reminder", "title": "Pain", "text": "Sortir le pain", "due": 1}, late=0)
    ((kind, data, inbox_then),) = seen
    assert kind == "reminder" and inbox_then == ["Sortir le pain"]
    (item,) = inbox.pending()
    assert item["kind"] == "reminder" and data["inbox_id"] == item["id"]
    assert notified == [(("Rappel", "Sortir le pain"), {"kind": "reminder", "via": "pc"})]


def test_fire_with_a_page_open_does_not_notify(published, monkeypatch):
    notified = []
    monkeypatch.setattr(events, "has_subscribers", lambda: True)
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: notified.append(a))
    scheduler._fire({"id": "r2", "kind": "reminder", "title": "Thé", "text": "Thé prêt", "due": 1}, late=0)
    assert notified == [] and [e["type"] for e in published] == ["reminder"]


def test_routine_missed_by_more_than_two_hours_is_skipped_and_rescheduled(published, monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: started.append(a))
    now = time.time()
    write([{"id": "daily1", "kind": "task", "title": "Veille", "text": "veille", "due": now - 3 * 3600,
            "repeat": "daily", "profile": "recherche"},
           {"id": "once1", "kind": "task", "title": "Ponctuelle", "text": "x", "due": now - 5 * 3600,
            "repeat": "none", "profile": "recherche"},
           {"id": "soon1", "kind": "task", "title": "Récente", "text": "y", "due": now - 3600,
            "repeat": "none", "profile": "recherche"}])
    scheduler.tick(now)
    assert started == [("Récente", "y")]  # an hour late still runs
    (kept,) = scheduler.items()
    assert kept["id"] == "daily1" and kept["due"] > now
    warnings = [e["text"] for e in published if e["type"] == "warning"]
    assert len(warnings) == 2
    assert any("Routine « Veille » non lancée" in w and "Prochaine fois" in w for w in warnings)
    assert any("Routine « Ponctuelle » non lancée" in w and "Prochaine fois" not in w for w in warnings)
    # Reminders always fire, however late (an alarm monsieur set).
    write([{"id": "r9", "kind": "reminder", "title": "Vieux", "text": "Vieux", "due": now - 10 * 3600}])
    scheduler.tick(now)
    assert [e["text"] for e in published if e["type"] == "reminder"] == ["Vieux"]


def test_routine_that_cannot_start_says_why(published, monkeypatch, caplog):
    def refuse(*a, **k):
        raise ValueError("Plafond du jour atteint (2,00 $).")
    monkeypatch.setattr(tasks, "create_task", refuse)
    scheduler.add("task", "Veille", "veille", delay_minutes=1)
    caplog.set_level(logging.INFO)
    scheduler.tick(time.time() + 120)
    (warning,) = [e for e in published if e["type"] == "warning"]
    assert warning["text"] == "Routine « Veille » non lancée : Plafond du jour atteint (2,00 $)."
    # An expected refusal: one INFO line, no ERROR with a traceback (data/jarvis.log all day long).
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("non lancée : Plafond du jour atteint" in r.getMessage() and not r.exc_info for r in caplog.records)


def test_a_routine_that_breaks_unexpectedly_is_still_logged_with_its_traceback(published, monkeypatch, caplog):
    def broken(*a, **k):
        raise KeyError("profil")
    monkeypatch.setattr(tasks, "create_task", broken)
    scheduler.add("task", "Veille", "veille", delay_minutes=1)
    scheduler.tick(time.time() + 120)
    assert [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert [e for e in published if e["type"] == "warning"]

# ---------------------------------------------------------------- resilience


def test_entries_without_id_or_due_are_skipped_and_logged_once(published, caplog):
    """Acceptance: an entry without 'due' in schedules.json does not crash
    tick() and is logged once."""
    now = time.time()
    write([{"id": "nodue", "title": "Sans date", "text": "x"}, {"due": now - 10, "text": "sans id"},
           "pas un objet", {"id": "bad", "due": "demain"}, {"id": "nan", "due": float("inf")},
           {"id": "ok1", "kind": "reminder", "title": "Valide", "text": "Valide", "due": now - 5}])
    with caplog.at_level(logging.WARNING):
        scheduler.tick(now)
        scheduler.tick(now + 1)
        assert [i["id"] for i in scheduler.items()] == []
    assert [e["text"] for e in published if e["type"] == "reminder"] == ["Valide"]
    logged = [r for r in caplog.records if "sans id ou sans date" in r.getMessage()]
    assert len(logged) == 1 and "5 entrée(s)" in logged[0].getMessage()
    # The broken entries are left for monsieur to fix, never thrown away.
    assert len(store.load(scheduler.FILE, [])) == 5
    # The page's list and the voice instructions read the valid ones only.
    assert scheduler.items() == [] and tools.run_tool("get_status", {})["upcoming"] == []


def test_loop_logs_the_same_error_at_most_once_an_hour(monkeypatch, caplog):
    class Stop:
        def __init__(self, rounds):
            self.rounds = rounds

        def is_set(self):
            self.rounds -= 1
            return self.rounds < 0

        def wait(self, _):
            return False

        def clear(self):
            pass

    def boom(now=None):
        raise RuntimeError("disque plein")
    monkeypatch.setattr(scheduler, "tick", boom)
    monkeypatch.setattr(scheduler, "periodic", lambda n: None)
    monkeypatch.setattr(scheduler, "_stop", Stop(50))
    with caplog.at_level(logging.ERROR):
        scheduler._loop()
    assert len([r for r in caplog.records if "erreur du planificateur" in r.getMessage()]) == 1
    # An hour later, the same error is logged again.
    for key in scheduler._logged:
        scheduler._logged[key] -= 3700
    monkeypatch.setattr(scheduler, "_stop", Stop(5))
    with caplog.at_level(logging.ERROR):
        scheduler._loop()
    assert len([r for r in caplog.records if "erreur du planificateur" in r.getMessage()]) == 2


def test_periodic_keeps_the_pc_awake_while_tasks_run_and_refreshes_ares(monkeypatch):
    from jarvis import ares, remarques
    awake, refreshed = [], []
    monkeypatch.setattr(desktop, "keep_awake", lambda: awake.append(1) or True)
    monkeypatch.setattr(tasks, "running", lambda: [{"id": "t"}])
    monkeypatch.setattr(ares, "available", lambda: True)
    monkeypatch.setattr(ares, "refresh", lambda action="": refreshed.append("ares"))
    monkeypatch.setattr(remarques, "refresh", lambda force=False: refreshed.append("remarques"))
    for n in range(1, 61):
        scheduler.periodic(n)
    assert len(awake) == 2  # every 30 ticks
    monkeypatch.setattr(tasks, "running", lambda: [])
    for n in range(61, 91):
        scheduler.periodic(n)
    assert len(awake) == 2  # nothing runs: the PC may sleep
    assert refreshed == []
    scheduler.periodic(300)
    scheduler._refresher.join(5)
    assert refreshed == ["ares", "remarques"]
    monkeypatch.setattr(ares, "available", lambda: False)
    scheduler.periodic(600)
    scheduler._refresher.join(5)
    assert refreshed == ["ares", "remarques", "remarques"]  # A.R.E.S away: not asked

# ---------------------------------------------------------------- cancel, update, snooze


def test_cancel_by_id_or_words(published):
    a = scheduler.add("reminder", "Appeler maman", "Appeler maman", delay_minutes=10)
    scheduler.add("reminder", "Sortir le chien", "Sortir le chien", delay_minutes=10)
    assert [i["id"] for i in scheduler.cancel(a["id"])] == [a["id"]]
    assert [i["title"] for i in scheduler.cancel("chien")] == ["Sortir le chien"]
    assert scheduler.cancel("rien") == []


def test_cancel_with_several_matches_is_ambiguous(published):
    scheduler.add("reminder", "Garage : devis", "Appeler le garage pour le devis", delay_minutes=10)
    scheduler.add("reminder", "Garage : pneus", "Garage, changer les pneus", delay_minutes=20)
    out = tools.run_tool("cancel_schedule", {"query": "garage"})
    assert out["ok"] is False and out["ambiguous"] is True and len(out["choices"]) == 2
    assert "Plusieurs rappels correspondent" in out["error"]
    assert len(scheduler.items()) == 2  # nothing removed
    # An exact title wins; 'all' when monsieur said all of them.
    assert tools.run_tool("cancel_schedule", {"query": "Garage : pneus"})["cancelled"] == ["Garage : pneus"]
    scheduler.add("reminder", "Garage : rdv", "Garage rdv", delay_minutes=30)
    assert len(tools.run_tool("cancel_schedule", {"query": "garage", "all": True})["cancelled"]) == 2
    assert scheduler.items() == []


def test_update_changes_label_time_and_repeat(published):
    item = scheduler.add("reminder", "Pain", "Sortir le pain", delay_minutes=30)
    now = time.time()
    out = scheduler.update(item["id"], title="Sortir la baguette", now=now)
    assert out["title"] == out["text"] == "Sortir la baguette"  # the panel shows the label: it says it
    out = scheduler.update(item["id"], delay_minutes=90, now=now)
    assert out["due"] == pytest.approx(now + 5400, abs=1)
    out = scheduler.update(item["id"], at="demain à 7 h", repeat="weekly")
    assert datetime.fromtimestamp(out["due"]).hour == 7 and out["repeat"] == "weekly"
    out = scheduler.update(item["id"], due=now + 3600, repeat="days", days=["mar"])
    assert out["days"] == [1] and datetime.fromtimestamp(out["due"]).weekday() == 1
    assert published[-1]["type"] == "schedules"
    with pytest.raises(KeyError):
        scheduler.update("zzz", title="x")
    for bad in ({"title": "  "}, {"text": ""}, {"at": "25:00"}, {"due": now - 3600}, {"repeat": "souvent"}, {}):
        with pytest.raises(ValueError):
            scheduler.update(item["id"], **bad)


def test_snooze_last_brings_the_reminder_back_in_ten_minutes(published):
    """Acceptance: snooze('last', 10) after a fired reminder creates a new item
    due now + 10 min."""
    scheduler.add("reminder", "Pain", "Sortir le pain", delay_minutes=1)
    fired_at = time.time() + 120
    scheduler.tick(fired_at)
    assert scheduler.items() == []
    now = time.time()
    item = scheduler.snooze("last", 10, now=now)
    assert item["due"] == pytest.approx(now + 600, abs=1)
    assert item["text"] == "Sortir le pain" and item["repeat"] == "none"
    assert [i["id"] for i in scheduler.items()] == [item["id"]]
    # Snoozed again (a double click): the same copy moves, no second one.
    again = scheduler.snooze("last", 60, now=now)
    assert again["id"] == item["id"] and again["due"] == pytest.approx(now + 3600, abs=1)
    assert len(scheduler.items()) == 1


def test_snooze_keeps_the_last_ten_and_refuses_what_never_fired(published):
    now = time.time()
    write([{"id": f"r{i}", "kind": "reminder", "title": f"Rappel {i}", "text": f"Rappel {i}",
            "due": now - 100 + i} for i in range(12)])
    scheduler.tick(now)
    recent = scheduler.recent_fired()
    assert len(recent) == 10 and recent[0]["id"] == "r11"
    with pytest.raises(LookupError):
        scheduler.snooze("r0", 10)  # pushed out of the last ten
    with pytest.raises(LookupError):
        scheduler.snooze("Rappel 5", 10)  # the route takes an id only
    with pytest.raises(ValueError):
        scheduler.snooze("r5", "dix")
    with pytest.raises(ValueError):
        scheduler.snooze("r5", 8 * 1440)


def test_snooze_with_nothing_recent_says_so():
    with pytest.raises(LookupError, match="Aucun rappel n'est arrivé récemment"):
        scheduler.snooze("last", 10)


def test_snooze_tool_by_words_and_ambiguity(published):
    now = time.time()
    write([{"id": "a1", "kind": "reminder", "title": "Garage", "text": "Appeler le garage", "due": now - 5},
           {"id": "a2", "kind": "reminder", "title": "Pain", "text": "Sortir le pain", "due": now - 4},
           {"id": "a3", "kind": "reminder", "title": "Pain bis", "text": "Encore le pain", "due": now - 3}])
    scheduler.tick(now)
    out = tools.run_tool("snooze_reminder", {"query": "garage", "minutes": 15})
    assert out["ok"] and "Garage" in out["snoozed"]
    out = tools.run_tool("snooze_reminder", {"query": "le pain"})
    assert out["ok"] is False and out["ambiguous"] and len(out["choices"]) == 2
    out = tools.run_tool("snooze_reminder", {"query": "pain", "minutes": 5})  # the exact title wins
    assert out["ok"] and "Pain" in out["snoozed"] and "Pain bis" not in out["snoozed"]
    out = tools.run_tool("snooze_reminder", {})
    assert out["ok"] and "Pain bis" in out["snoozed"]  # the last one, 10 minutes by default
    assert tools.run_tool("snooze_reminder", {"query": "dentiste"})["ok"] is False

# ---------------------------------------------------------------- full-access routines


def test_full_access_routine_never_from_add_without_the_gate(published):
    with pytest.raises(PermissionError):
        scheduler.add("task", "Ménage", "Vide la corbeille", at="08:00", profile="complet")
    assert scheduler.items() == []
    # Only 'task', as confirm.gate reads it, is a routine: anything else is a reminder.
    for kind in (" task", "tâche", "routine", "Task "):
        item = scheduler.add(kind, "Ménage", "Vide la corbeille", at="08:00", profile="complet")
        assert item["kind"] == "reminder" and item["profile"] == "recherche", kind


def test_padded_kind_cannot_slip_a_full_access_routine_past_the_gate(published):
    from jarvis import confirm
    sid = confirm.new_session()
    out = tools.run_tool("schedule", {"kind": " task", "title": "Ménage", "text": "rm", "at": "08:00",
                                      "repeat": "daily", "profile": "complet"}, tools.ToolCtx(session_id=sid))
    assert out["ok"]
    assert all(i["kind"] != "task" and i["profile"] != "complet" for i in scheduler.items())


def test_full_access_routine_keeps_its_instruction_and_frequency(published):
    item = scheduler.add("task", "Ménage", "Vide la corbeille", at="08:00", repeat="daily",
                         profile="complet", allow_complet=True)
    for change in ({"text": "Formate le disque"}, {"repeat": "weekly"}, {"days": ["lun"]}):
        with pytest.raises(PermissionError):
            scheduler.update(item["id"], **change)
    out = scheduler.update(item["id"], title="Grand ménage", at="09:00")
    assert out["title"] == "Grand ménage" and out["text"] == "Vide la corbeille" and out["profile"] == "complet"
    assert datetime.fromtimestamp(out["due"]).hour == 9



PHONE = "app:d_0123456789abcdef"


def test_a_full_access_routine_is_the_pcs_alone(published):
    """Whatever allow_complet says: from a phone or Siri (or an origin not
    recognised) it would run with full access after the opt-in or the device is gone."""
    for via in (PHONE, "siri:k_9b8a7c6d5e4f3a21", "unpaired", "x:y"):
        with pytest.raises(PermissionError):
            scheduler.add("task", "Ménage", "Vide la corbeille", at="08:00", profile="complet", allow_complet=True,
                          via=via)
    assert scheduler.items() == []
    item = scheduler.add("task", "Veille", "Actus", at="08:00", profile="recherche", via=PHONE)
    assert item["via"] == PHONE and item["profile"] == "recherche"
    assert scheduler.add("task", "Ménage", "Vide", at="08:00", profile="complet", allow_complet=True)["via"] == "pc"


def test_the_agenda_tool_never_allows_full_access_for_a_phone(published):
    from jarvis import confirm
    routine = {"kind": "task", "title": "Ménage", "text": "Vide la corbeille", "at": "08:00", "profile": "complet"}
    # Its handler reached directly (as a decided card would): only the PC's origin may.
    with pytest.raises(PermissionError):
        tools.handlers()["schedule"](dict(routine), tools.ToolCtx(confirm.new_session(origin=PHONE), origin=PHONE))
    assert tools.handlers()["schedule"](dict(routine), tools.ToolCtx(confirm.new_session()))["ok"]
    [item] = scheduler.items()
    assert item["via"] == "pc" and item["profile"] == "complet"


def test_the_agenda_tool_asks_full_access_only_for_the_pc(monkeypatch):
    """The handler's own reading, before scheduler.add's: allow_complet only for the PC's origin."""
    from jarvis import confirm
    asked = []
    monkeypatch.setattr(scheduler, "add", lambda *a, **k: asked.append((k["allow_complet"], k["via"])) or
                        {"id": "x", "kind": "task", "title": "t", "due": time.time() + 60, "repeat": "none"})
    monkeypatch.setattr(scheduler, "describe", lambda item, now=None: "t")
    routine = {"kind": "task", "title": "Ménage", "text": "Vide la corbeille", "at": "08:00", "profile": "complet"}
    for origin in (PHONE, "siri:k_9b8a7c6d5e4f3a21", "x:y"):
        tools.handlers()["schedule"](dict(routine), tools.ToolCtx(None, origin=origin))
    tools.handlers()["schedule"](dict(routine), tools.ToolCtx(confirm.new_session()))
    assert asked == [(False, PHONE), (False, "siri:k_9b8a7c6d5e4f3a21"), (False, "x:y"), (True, "pc")]


def test_a_phone_routine_runs_only_while_its_device_is_allowed_and_never_with_full_access(published, monkeypatch):
    from jarvis import remote
    started = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: started.append(k))
    active = {"pc", PHONE}
    monkeypatch.setattr(remote, "origin_active", lambda origin: origin in active)
    due = time.time() - 10
    base = {"kind": "task", "text": "Actus", "due": due, "repeat": "daily", "profile": "recherche",
            "complexity": "normale", "created": due}
    write([{**base, "id": "a1", "title": "Veille", "via": PHONE},
           {**base, "id": "a2", "title": "Ménage", "profile": "complet", "via": PHONE},
           {**base, "id": "a3", "title": "Retirée", "via": "app:d_fedcba9876543210"},
           {**base, "id": "a4", "title": "Du PC", "profile": "complet"}])
    scheduler.tick()
    assert sorted((k["via"], k["profile"]) for k in started) == [("app:d_0123456789abcdef", "recherche"),
                                                                 ("pc", "complet")]
    warnings = sorted(e["text"] for e in published if e["type"] == "warning")
    assert warnings == [f"Routine « {t} » non lancée : elle venait d'un appareil retiré ou demandait l'accès complet."
                        for t in ("Ménage", "Retirée")]
    assert {i["id"] for i in scheduler.items()} == {"a1", "a2", "a3", "a4"}  # still listed, rescheduled
    # The phone removed: its routine stops firing too.
    active.discard(PHONE)
    started.clear()
    scheduler.tick(max(i["due"] for i in scheduler.items()) + 10)  # the next morning, on time
    assert [k["via"] for k in started] == ["pc"]

# ---------------------------------------------------------------- briefing hook


def test_briefing_runs_once_in_the_morning(published, monkeypatch):
    from jarvis import briefing, health
    monkeypatch.setattr(health, "onboarded", lambda: True)  # the briefing waits for the Mise en route
    ran = []
    monkeypatch.setattr(briefing, "run", lambda now=None: ran.append(now))
    monkeypatch.setattr(config, "BRIEFING_TIME", "08:00")
    monkeypatch.setattr(config, "BRIEFING_DAYS", "tous")
    scheduler.tick(datetime(2026, 10, 9, 7, 59).timestamp())
    assert ran == []
    scheduler.tick(datetime(2026, 10, 9, 8, 30).timestamp())
    scheduler.tick(datetime(2026, 10, 9, 9, 0).timestamp())
    briefing._thread.join(5)  # gathered off the scheduler's thread
    assert ran == [datetime(2026, 10, 9, 8, 30)]
    scheduler.tick(datetime(2026, 10, 10, 15, 0).timestamp())  # switched on in the afternoon
    assert len(ran) == 1


@pytest.mark.parametrize("spec, days", [("lun-ven", {0, 1, 2, 3, 4}), ("tous", set(range(7))),
                                        ("lun,mer,ven", {0, 2, 4}), ("sam-lun", {5, 6, 0}),
                                        ("", set(range(7))), ("n'importe quoi", set(range(7)))])
def test_briefing_days_follow_the_settings(spec, days, monkeypatch):
    """Réglages › Proactivité › Jours du briefing (settings.py normalises them)."""
    monkeypatch.setattr(config, "BRIEFING_DAYS", spec)
    assert scheduler.briefing_days() == days


def test_scheduler_thread_starts_and_stops(monkeypatch):
    ticked = threading.Event()
    monkeypatch.setattr(scheduler, "tick", lambda now=None: ticked.set())
    monkeypatch.setattr(scheduler, "periodic", lambda n: None)
    scheduler.start()
    try:
        assert ticked.wait(5)
    finally:
        scheduler.stop()
        scheduler._thread.join(5)
    assert not scheduler._thread.is_alive()
