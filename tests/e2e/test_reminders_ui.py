"""Reminders, remarques and the local briefing in a real browser (WP17):
a reminder that went off can be put off ('+10 min', '+1 h', 'Demain') or
closed ('Fait'); the side panel edits a reminder's text and its date and
time; « JARVIS a remarqué » shows its suggestions, acts and hides them; the
briefing shows as a card and, marked queued, waits behind the badge instead
of being said. The server is the real one, in this process.

Set JARVIS_E2E_SHOTS to a folder to also save screenshots."""
import os
import time
from datetime import datetime, timedelta

import pytest

from test_delivery import SPIES, open_page, record_deliveries, until  # noqa: F401 - fixtures

pytestmark = pytest.mark.e2e

SHOTS = os.environ.get("JARVIS_E2E_SHOTS", "")
NNBSP, NBSP = " ", " "


def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"{name}.png"))


@pytest.fixture
def clean_schedules():
    from jarvis import scheduler, store, tasks
    with store.LOCK:
        store.save(scheduler.FILE, [])
        store.save("remarques.json", {})
        state = store.load("state.json", {})
        state.pop("recent_fired", None)
        store.save("state.json", state)
    for task_id in [t["id"] for t in tasks.list_tasks() if t["status"] not in tasks.ACTIVE]:
        tasks.TASKS.pop(task_id, None)
    yield
    with store.LOCK:
        store.save(scheduler.FILE, [])
        store.save("remarques.json", {})


def fire_now(rid, text):
    """A reminder due a second ago: the running scheduler fires it within a second."""
    from jarvis import scheduler, store
    with store.LOCK:
        items = store.load(scheduler.FILE, [])
        items.append({"id": rid, "kind": "reminder", "title": text, "text": text, "due": time.time() - 1,
                      "repeat": "none", "profile": "recherche", "complexity": "normale", "created": time.time()})
        store.save(scheduler.FILE, items)


def schedule_items():
    from jarvis import scheduler
    return scheduler.items()

# ---------------------------------------------------------------- a reminder that went off


def test_a_reminder_that_went_off_can_be_put_off_or_closed(jarvis, clean_schedules):
    fire_now("pain1", "Sortir le pain du four")
    card = jarvis.locator("#cards .card.warning", has_text="Sortir le pain du four")
    card.wait_for()
    buttons = card.locator(".actions button")
    assert buttons.all_inner_texts() == ["+10 min", "+1 h", "Demain", "Fait"]
    shot(jarvis, "rappel-carte")
    before = time.time()
    card.get_by_role("button", name="+10 min").click()
    card.wait_for(state="detached")
    toast = jarvis.locator("#toasts .toast", has_text="Rappel reporté")
    toast.wait_for()
    until(lambda: len(schedule_items()) == 1)
    (item,) = schedule_items()
    assert item["text"] == "Sortir le pain du four" and item["repeat"] == "none"
    assert before + 590 <= item["due"] <= time.time() + 610
    # The panel shows it again, today.
    row = jarvis.locator("#scheduleList .item", has_text="Sortir le pain du four")
    row.wait_for()
    assert jarvis.locator("#scheduleList .grp").first.inner_text() == "Aujourd'hui"
    shot(jarvis, "rappel-reporte")

    # 'Demain' on another one: tomorrow, same time.
    fire_now("the1", "Le thé est prêt")
    card = jarvis.locator("#cards .card.warning", has_text="Le thé est prêt")
    card.wait_for()
    card.get_by_role("button", name="Demain").click()
    card.wait_for(state="detached")
    until(lambda: len(schedule_items()) == 2)
    tea = next(i for i in schedule_items() if i["text"] == "Le thé est prêt")
    assert tea["due"] == pytest.approx(time.time() + 86400, abs=30)
    assert "demain" in jarvis.locator("#toasts .toast", has_text="Rappel reporté").last.inner_text()

    # 'Fait' only closes it: nothing comes back.
    fire_now("eau1", "Arroser les plantes")
    card = jarvis.locator("#cards .card.warning", has_text="Arroser les plantes")
    card.wait_for()
    card.get_by_role("button", name="Fait").click()
    card.wait_for(state="detached")
    time.sleep(0.3)
    assert [i["text"] for i in schedule_items() if i["text"] == "Arroser les plantes"] == []


def test_snooze_of_a_reminder_that_never_went_off_says_why(jarvis, clean_schedules):
    jarvis.evaluate("""() => __jarvis.bus.emit('server:reminder',
        {id: 'inconnu', title: 'Fantôme', text: 'Fantôme', late_minutes: 0})""")
    card = jarvis.locator("#cards .card.warning", has_text="Fantôme")
    card.wait_for()
    card.get_by_role("button", name="+1 h").click()
    jarvis.locator("#toasts .toast", has_text="Report impossible").wait_for()
    assert card.is_visible()  # still there: nothing was put off

# ---------------------------------------------------------------- editing in the side panel


def test_the_side_panel_edits_a_reminders_text_and_time(jarvis, clean_schedules):
    out = jarvis.evaluate("""() => __jarvis.api('/api/schedules', {method: 'POST',
        body: {title: 'Appeler le garage', text: 'Appeler le garage', delay_minutes: 60}})""")
    rid = out["item"]["id"]
    row = jarvis.locator(f'#scheduleList [data-key="s:{rid}"]')
    row.wait_for()
    row.locator(".edit").click()
    form = row.locator("form.edit-form")
    text = form.get_by_role("textbox", name="Nouveau texte")
    when = form.locator("input.edit-when")
    assert when.get_attribute("aria-label") == "Date et heure"
    due = datetime.fromtimestamp(out["item"]["due"])
    assert when.input_value() == due.strftime("%Y-%m-%dT%H:%M")
    text.fill("Appeler le garage Martin")
    new = (datetime.now() + timedelta(days=2)).replace(hour=9, minute=30, second=0, microsecond=0)
    when.fill(new.strftime("%Y-%m-%dT%H:%M"))
    shot(jarvis, "rappel-edition")
    form.get_by_role("button", name="Enregistrer").click()
    form.wait_for(state="detached")
    until(lambda: schedule_items() and schedule_items()[0]["title"] == "Appeler le garage Martin")
    (item,) = schedule_items()
    assert item["text"] == "Appeler le garage Martin"  # what the panel shows is what JARVIS says
    assert item["due"] == pytest.approx(new.timestamp(), abs=1)
    row = jarvis.locator(f'#scheduleList [data-key="s:{rid}"]')
    row.locator(".txt", has_text="Appeler le garage Martin").wait_for()
    assert f"9{NBSP}h{NBSP}30" in row.locator(".when").inner_text()
    # Only the text this time: the time stays.
    row.locator(".edit").click()
    form = row.locator("form.edit-form")
    form.get_by_role("textbox", name="Nouveau texte").fill("Garage Martin")
    form.get_by_role("textbox", name="Nouveau texte").press("Enter")
    until(lambda: schedule_items()[0]["title"] == "Garage Martin")
    assert schedule_items()[0]["due"] == pytest.approx(new.timestamp(), abs=1)


def test_a_wrong_date_is_refused_in_french(jarvis, clean_schedules):
    out = jarvis.evaluate("""() => __jarvis.api('/api/schedules', {method: 'POST',
        body: {title: 'Dentiste', text: 'Dentiste', delay_minutes: 60}})""")
    rid = out["item"]["id"]
    row = jarvis.locator(f'#scheduleList [data-key="s:{rid}"]')
    row.wait_for()
    row.locator(".edit").click()
    past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    row.locator("input.edit-when").fill(past)
    row.get_by_role("button", name="Enregistrer").click()
    jarvis.locator("#toasts .toast", has_text="cette date est déjà passée").wait_for()
    assert schedule_items()[0]["due"] == pytest.approx(out["item"]["due"], abs=1)

# ---------------------------------------------------------------- « JARVIS a remarqué »


@pytest.fixture
def remarks(monkeypatch, clean_schedules):
    """Two overdue A.R.E.S tasks and a Claude task that failed an hour ago, with
    a title that tries to be markup."""
    from jarvis import ares, remarques, tasks
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "• Payer EDF — En retard (2 j)\n"
                                                                  "• Rendre le dossier — En retard (5 j)")
    monkeypatch.setattr(remarques, "_last", {"items": None})
    now = time.time()
    tasks.TASKS["rq1"] = {"id": "rq1", "title": "Comparer <img src=x onerror=alert(1)> les prix",
                          "status": "error", "prompt": "Compare les prix", "profile": "lecture",
                          "complexity": "simple", "origin": "voix", "started": now - 3700, "ended": now - 3600,
                          "output": "", "log": []}
    yield
    tasks.TASKS.pop("rq1", None)


def test_remarques_show_act_and_hide(remarks, jarvis):
    from jarvis import store
    section = jarvis.locator("#remarques")
    section.locator("li.remarque").nth(1).wait_for()
    assert section.is_visible()
    assert section.locator("summary h2").text_content() == "JARVIS a remarqué (2)"
    texts = [t.strip() for t in section.locator("li.remarque .txt").all_text_contents()]
    assert texts == ["2 tâches A.R.E.S sont en retard.",
                     "La tâche « Comparer <img src=x onerror=alert(1)> les prix » a échoué."]
    assert section.locator("img").count() == 0  # a title is text, never markup
    # « Pourquoi ? » unfolds the reason.
    first = section.locator("li.remarque").first
    first.locator(".why summary").click()
    assert first.locator(".why p").inner_text() == "A.R.E.S les marque « en retard » dans son agenda."
    shot(jarvis, "remarques")
    # « Replanifier ? » prefills the composer: no session opened for it.
    first.get_by_role("button", name="Replanifier ?").click()
    assert jarvis.input_value("#askInput") == "Aide-moi à replanifier mes tâches A.R.E.S en retard."
    assert jarvis.evaluate("__jarvis.state.mode") in ("off", "standby")
    # ✕ hides it for a day; the focus goes to the next one's ✕.
    x = first.locator("button.x")
    assert x.get_attribute("aria-label") == f"Masquer la remarque «{NNBSP}2 tâches A.R.E.S sont en retard.{NNBSP}»"
    x.click()
    jarvis.locator("#toasts .toast", has_text="Remarque masquée pendant 1 jour.").wait_for()
    until(lambda: section.locator("li.remarque").count() == 1)
    assert jarvis.evaluate("document.activeElement.closest('li')?.dataset.key") == "tache-rq1"
    assert store.load("remarques.json", {})["ares-retard"]["count"] == 1
    # « Relancer ? » retries the task (read-only profile: it starts at once).
    section.get_by_role("button", name="Relancer ?").click()
    jarvis.locator("#toasts .toast", has_text="Nouvel essai de").wait_for()
    until(lambda: section.is_hidden())  # retried: nothing left to remark
    from jarvis import tasks
    until(lambda: not tasks._active and not tasks._waiting, timeout=20)  # over before the next test


def test_remarques_section_is_hidden_when_empty(jarvis, clean_schedules):
    assert jarvis.locator("#remarques").is_hidden()

# ---------------------------------------------------------------- the local briefing


def briefing(text, queued, key):
    """As briefing.deliver() sends it (the inbox records it on the way)."""
    from jarvis import events
    return events.publish("briefing", {"id": f"briefing-{key}", "title": "Briefing du matin", "text": text,
                                       "date": "2026-10-12", "queued": queued})


def test_briefing_shows_a_card_and_is_announced(open_page):  # noqa: F811 - the fixture
    page = open_page(SPIES)
    page.evaluate("window.__notifPerm = 'granted'; 0")
    record_deliveries(page)
    briefing("Bonjour monsieur. Nous sommes lundi 12 octobre. Aucun rappel JARVIS aujourd'hui. Bonne journée.",
             False, "brief-ok")
    card = page.locator("#cards .card", has_text="Aucun rappel JARVIS aujourd'hui")
    card.wait_for()
    assert card.locator("h3 .t").inner_text() == "Briefing du matin"
    page.wait_for_function("window.__spoken.length > 0")
    assert page.evaluate("window.__spoken")[0].startswith("Bonjour monsieur. Votre briefing du matin est prêt")
    shot(page, "briefing")


def test_briefing_marked_queued_waits_behind_the_badge_and_is_not_said(open_page):  # noqa: F811
    page = open_page(SPIES)
    record_deliveries(page)
    briefing("Bonjour monsieur. Nous sommes lundi 12 octobre. Bonne journée.", True, "brief-quiet")
    page.locator("#cards .card", has_text="Nous sommes lundi 12 octobre").wait_for()
    page.wait_for_function("window.__delivered.some(d => d.kind === 'briefing')")
    assert page.evaluate("window.__delivered.filter(d => d.kind === 'briefing').map(d => d.how)") == ["queued"]
    badge = page.locator("#badge")
    badge.wait_for()
    assert badge.inner_text() == "1"
    time.sleep(0.3)
    assert page.evaluate("window.__spoken") == []  # nothing said aloud
    assert page.evaluate("window.__tones") == []   # not even a chime
