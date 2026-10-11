"""Wave 3 working together, in Chromium: reminders and the local briefing
(WP17) meet the costs and the daily cap (WP18), and both meet the modules of
waves 1 and 2.

- a reminder that went off is put off from its card and by voice, and both
  land in the side panel;
- Réglages › Coûts sets the cap: the chip turns red, the health check in
  Réglages › Connexion says so, the briefing is read by the browser's own
  voice instead of inviting a paid conversation, the remarques stop offering
  « Relancer ? », and all of it comes back once the cap is raised;
- quiet hours still hold the briefing back, cap or not;
- the help card and the voice instructions name the new abilities, and
  « Combien j'ai dépensé aujourd'hui ? » finds its answer in get_status.

The server runs in this process: every setting changed is put back, the
ledger is seeded on today's date (never a fixed clock time), and quiet hours
are a window around the current time.

Set JARVIS_E2E_SHOTS to a folder to also save screenshots at 1440x900 and 1024x700."""
import json
import os
import time
from datetime import datetime, timedelta

import pytest
from test_delivery import SPIES, open_page, record_deliveries, until  # noqa: F401 - fixtures
from test_untrusted_text import call, go_live

from jarvis import briefing, config, health, inbox, remarques, scheduler, settings, store, tasks, usage

pytestmark = pytest.mark.e2e

SHOTS = os.environ.get("JARVIS_E2E_SHOTS", "")
NBSP, NNBSP = " ", " "
REAL_RUN_CHECKS = health.run_checks  # the harness swaps it for an empty list when it starts
BRIEF = ("Bonjour monsieur. Nous sommes samedi 10 octobre. Aujourd'hui à Nantes : éclaircies, de 9 à 16 °C. "
         "Dans A.R.E.S : 2 éléments aujourd'hui. Vos rappels du jour : 18 h, Appeler maman. Bonne journée.")


def shot(page, name, sizes=((1440, 900), (1024, 700)), settle=250):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for w, h in sizes:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(settle)  # the layout settles at the new width (a chart redraws)
        page.screenshot(path=os.path.join(SHOTS, f"{name}-{w}.png"))
    page.set_viewport_size({"width": 1440, "height": 900})


@pytest.fixture
def restore_settings(app_server, monkeypatch):
    """Every setting as it was, and no settings.json left for the next test."""
    for s in settings.SCHEMA:
        if not s.attr.startswith("MODELS."):
            monkeypatch.setattr(config, s.attr, getattr(config, s.attr))
    monkeypatch.setattr(config, "MODELS", dict(config.MODELS))
    monkeypatch.setattr(settings, "_BOOT", dict(settings._BOOT))
    yield
    for name in ("settings.json", "settings.json.bak"):
        (config.DATA_DIR / name).unlink(missing_ok=True)


@pytest.fixture
def clean(app_server, monkeypatch):
    """No reminder, remark, silenced remark, cost or finished task left from
    another test; no cap; A.R.E.S has nothing overdue."""
    from jarvis import ares
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "")
    monkeypatch.setattr(remarques, "_last", {"items": None})
    monkeypatch.setattr(remarques, "_voiced", {})

    def wipe():
        with store.LOCK:
            store.save(scheduler.FILE, [])
            store.save("remarques.json", {})
            state = store.load("state.json", {})
            state.pop("recent_fired", None)
            store.save("state.json", state)
        for name in ("usage.json", "usage.json.bak"):
            (config.DATA_DIR / name).unlink(missing_ok=True)
        for task_id in [t["id"] for t in tasks.list_tasks() if t["status"] not in tasks.ACTIVE]:
            tasks.TASKS.pop(task_id, None)
    wipe()
    yield
    wipe()


def spend(voice, claude):
    """Today's ledger (usage.py's own date, whatever the clock says)."""
    store.save(usage.FILE, {usage._today().isoformat(): {"realtime": {"usd": voice},
                                                         "claude": {"usd": claude, "tasks": 1 if claude else 0}}})


def fire_now(rid, text):
    """A reminder due a second ago: the running scheduler fires it within a second."""
    with store.LOCK:
        items = store.load(scheduler.FILE, [])
        items.append({"id": rid, "kind": "reminder", "title": text, "text": text, "due": time.time() - 1,
                      "repeat": "none", "profile": "recherche", "complexity": "normale", "created": time.time()})
        store.save(scheduler.FILE, items)


def open_settings(page, section):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    page.click(f"#settingsDialog .set-tab:has-text('{section}')")
    page.wait_for_selector(f"#settingsDialog .set-tab[aria-current='true']:has-text('{section}')")


def close_settings(page):
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('settingsDialog').open")


def health_row(page, check_id):
    sel = f"#setHealth .ob-row[data-check='{check_id}']"
    page.wait_for_selector(sel)
    return page.evaluate("s => { const r = document.querySelector(s); "
                         "return {state: r.dataset.state, text: r.textContent}; }", sel)


def chip(page):
    return page.text_content("#costChip button") or ""


def add_failed_task():
    now = time.time()
    tasks.TASKS["w3fail"] = {"id": "w3fail", "title": "Comparer les aspirateurs", "status": "error",
                             "prompt": "Compare les aspirateurs", "profile": "lecture", "complexity": "simple",
                             "origin": "voix", "started": now - 700, "ended": now - 600, "output": "", "log": []}

# ---------------------------------------------------------------- snooze: card and voice


def test_a_reminder_put_off_from_its_card_and_by_voice_lands_in_the_side_panel(clean, jarvis, app_server):
    fire_now("w3pain", "Sortir le pain du four")
    card = jarvis.locator("#cards .card.warning", has_text="Sortir le pain du four")
    card.wait_for()
    assert card.locator(".actions button").all_inner_texts() == ["+10 min", "+1 h", "Demain", "Fait"]
    shot(jarvis, "w3-rappel-carte")
    card.get_by_role("button", name="+1 h").click()
    card.wait_for(state="detached")
    jarvis.locator("#toasts .toast", has_text="Rappel reporté").wait_for()
    jarvis.locator("#scheduleList .item", has_text="Sortir le pain du four").wait_for()

    # The next one, put off by voice in a conversation: « redis-le-moi dans 10 minutes ».
    go_live(jarvis)
    session = app_server.sessions[-1]["session"]
    assert "snooze_reminder" in {t["name"] for t in session["tools"]}
    assert "snooze_reminder" in session["instructions"] and "Plafond du jour atteint" in session["instructions"]
    fire_now("w3the", "Le thé est prêt")
    jarvis.locator("#cards .card.warning", has_text="Le thé est prêt").wait_for()
    # The voice session heard about it (a notice, at the next pause).
    jarvis.wait_for_function("""__sent.some(m => m.type === 'conversation.item.create'
        && JSON.stringify(m.item).includes('Le thé est prêt'))""")
    call(jarvis, "snooze_reminder", {"query": "thé", "minutes": 10}, "w3-snooze")
    out = jarvis.evaluate("""JSON.parse(__sent.find(m => m.item && m.item.call_id === 'w3-snooze').item.output)""")
    assert out["ok"] is True and "Le thé est prêt" in out["snoozed"]
    jarvis.locator("#scheduleList .item", has_text="Le thé est prêt").wait_for()
    due = {i["text"]: i["due"] for i in scheduler.items()}
    assert due["Le thé est prêt"] == pytest.approx(time.time() + 600, abs=30)
    assert due["Sortir le pain du four"] == pytest.approx(time.time() + 3600, abs=30)

# ---------------------------------------------------------------- the cap, from Réglages to everything


def test_the_cap_set_in_reglages_reaches_chip_health_briefing_and_remarques(
        restore_settings, clean, open_page, monkeypatch):  # noqa: F811 - the fixture
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    monkeypatch.setattr(health, "run_checks", REAL_RUN_CHECKS)
    monkeypatch.setattr(health, "CHECKS", [health.check_costs])
    spend(0.30, 0.12)
    add_failed_task()
    page = open_page(SPIES)
    record_deliveries(page)
    page.wait_for_function(f"document.querySelector('#costChip button')?.textContent === \"Aujourd'hui ≈ 0,42{NBSP}$\"")
    remark = page.locator("#remarques li.remarque", has_text="Comparer les aspirateurs")
    remark.wait_for()
    assert remark.get_by_role("button", name="Relancer ?").is_visible()

    # Réglages › Connexion: the day's spending, no cap yet.
    open_settings(page, "Connexion")
    row = health_row(page, "costs")
    assert row["state"] == "info" and f"Aujourd'hui ≈ 0,42{NBSP}$" in row["text"] and "Aucun plafond" in row["text"]

    # Réglages › Coûts: a cap of 0,40 $, below what is spent.
    page.click("#settingsDialog .set-tab:has-text('Coûts')")
    field = page.locator("#set-daily_budget_usd")
    field.fill("0.4")
    field.press("Tab")
    page.wait_for_function("""(() => { const s = document.querySelector(
        "#settingsDialog [data-key='daily_budget_usd'] > .set-status"); return s && s.textContent.trim(); })()""")
    assert config.DAILY_BUDGET_USD == 0.4
    page.wait_for_selector("#settingsUsage .usage-cap")
    page.wait_for_function("document.querySelector('#settingsUsage .usage-cap').textContent.includes('0,40')")
    # Every page follows at once: the chip turns red, the wake word knows.
    page.wait_for_function("document.getElementById('costChip').classList.contains('cap')")
    assert chip(page).endswith(" · plafond atteint")
    page.wait_for_function("__jarvis.state.config.usage_capped === true")
    if SHOTS:  # the chart drawn and its bars grown (or the offline note), not mid-animation
        page.wait_for_selector("#usageChart[data-state='ready'], #usageChart[data-state='unavailable']")
    shot(page, "w3-reglages-couts", settle=1500)
    page.click("#settingsDialog .set-tab:has-text('Connexion')")
    page.click("#settingsDialog button:has-text('Revérifier')")
    page.wait_for_function("""(() => { const r = document.querySelector("#setHealth .ob-row[data-check='costs']");
        return r && r.dataset.state === 'fix'; })()""")
    row = health_row(page, "costs")
    assert "Plafond du jour atteint" in row["text"] and "Réglages › Coûts" in row["text"]
    shot(page, "w3-sante-couts", sizes=((1440, 900),))
    close_settings(page)
    page.locator(".card", has_text="Plafond du jour atteint").first.wait_for()  # usage.js's own card
    shot(page, "w3-cout-plafond")

    # The remarques stop offering a retry that would be refused (the cap's push reloads them).
    page.wait_for_function("""(() => { const li = [...document.querySelectorAll('#remarques li.remarque')]
        .find(l => l.textContent.includes('Comparer les aspirateurs'));
        return li && ![...li.querySelectorAll('button')].some(b => b.textContent === 'Relancer ?'); })()""",
                           timeout=15_000)
    remark.locator(".why summary").click()
    assert "Plafond du jour atteint" in remark.locator(".why p").inner_text()

    # The briefing: no paid conversation offered, the browser's voice reads it all.
    payload = briefing.deliver(BRIEF, datetime.now())
    assert payload["capped"] is True and payload["queued"] is False
    card = page.locator("#cards .card", has_text="Appeler maman")
    card.wait_for()
    page.wait_for_function("window.__spoken.some(t => t.includes('Appeler maman'))")
    spoken = page.evaluate("window.__spoken")
    assert not any("appelez-moi" in t for t in spoken)
    assert page.evaluate("__jarvis.state.pending") == 0  # nothing left behind the badge
    until(lambda: payload["inbox_id"] not in [i["id"] for i in inbox.pending()])
    assert page.evaluate("window.__pcs || 0") == 0  # no conversation was opened
    shot(page, "w3-briefing-plafond")

    # The cap raised: the retry comes back, the chip loses its colour.
    open_settings(page, "Coûts")
    field = page.locator("#set-daily_budget_usd")
    field.fill("5")
    field.press("Tab")
    page.wait_for_function("!document.getElementById('costChip').classList.contains('cap')")
    page.wait_for_function("__jarvis.state.config.usage_capped === false")
    close_settings(page)
    remark.get_by_role("button", name="Relancer ?").wait_for(timeout=15_000)


def test_the_briefing_spoken_under_the_cap_invites_a_session(clean, open_page, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    page = open_page(SPIES)
    record_deliveries(page)
    payload = briefing.deliver(BRIEF, datetime.now())
    assert payload["capped"] is False
    card = page.locator("#cards .card", has_text="Appeler maman")
    card.wait_for()
    assert card.locator("h3 .t").inner_text() == "Briefing du matin"
    page.wait_for_function("window.__spoken.length > 0")
    assert page.evaluate("window.__spoken")[0].startswith("Bonjour monsieur. Votre briefing du matin est prêt")
    page.wait_for_function("__jarvis.state.pending === 1")  # the details wait for the next conversation
    shot(page, "w3-briefing")


def test_quiet_hours_hold_the_briefing_back_even_over_the_cap(clean, open_page, monkeypatch):  # noqa: F811
    now = datetime.now()
    monkeypatch.setattr(config, "QUIET_HOURS", f"{now - timedelta(hours=1):%H:%M}-{now + timedelta(hours=1):%H:%M}")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.4)
    spend(0.5, 0)
    page = open_page(SPIES)
    record_deliveries(page)
    payload = briefing.deliver(BRIEF, now)
    assert payload["queued"] is True and payload["capped"] is True
    page.locator("#cards .card", has_text="Appeler maman").wait_for()
    page.wait_for_function("window.__delivered.some(d => d.kind === 'briefing')")
    assert page.evaluate("window.__delivered.filter(d => d.kind === 'briefing').map(d => d.how)") == ["queued"]
    page.wait_for_function("__jarvis.state.pending === 1")
    time.sleep(0.3)
    assert page.evaluate("window.__spoken") == [] and page.evaluate("window.__tones") == []

# ---------------------------------------------------------------- what JARVIS says it can do


def test_help_card_and_voice_know_the_new_abilities(clean, jarvis, app_server):
    jarvis.keyboard.press("?")
    card = jarvis.locator("#cards .card:has-text('Ce que je sais faire')")
    card.wait_for()
    reminders = card.locator(".aide-cat", has=jarvis.locator("h4", has_text="Rappels et routines"))
    assert "Reporte le rappel de 10 minutes" in reminders.locator(".aide-ex").all_inner_texts()
    today = card.locator(".aide-cat", has=jarvis.locator("h4", has_text="Point du jour"))
    ask = today.get_by_role("button", name="Combien j'ai dépensé aujourd'hui ?")
    assert ask.is_visible()
    spend(0.30, 0.12)
    ask.click()  # opens a conversation with the question as its first words
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    jarvis.wait_for_function("""__sent.some(m => m.type === 'conversation.item.create'
        && JSON.stringify(m.item).includes("Combien j'ai dépensé aujourd'hui"))""")
    call(jarvis, "get_status", {}, "w3-status")
    out = json.loads(jarvis.evaluate("__sent.find(m => m.item && m.item.call_id === 'w3-status').item.output"))
    assert out["spending"]["today"].startswith("≈ 0,42 $") and out["spending"]["daily_cap"].startswith("aucun")
    session = app_server.sessions[-1]["session"]
    assert "# Briefing du matin" in session["instructions"] and "dépense du jour" in session["instructions"]
