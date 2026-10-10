"""Wave 3's protections in Chromium, against the real server (fakes only for
what would leave this PC: OpenAI, Claude Code, A.R.E.S, Open-Meteo).

4. Untrusted text stays inert on the new screens: reminders (the side panel,
   its edit form with the date field, a reminder pushed with markup in every
   field, the card of a reminder that went off and its snooze buttons), the
   local briefing card with a hostile A.R.E.S agenda, weather place name and
   reminder title (in a session it reaches the model framed as outside data,
   and taints the session), « JARVIS a remarqué » whatever the server puts in
   each field (and its actions open nothing), the cost chip, its warning card
   and Réglages › Coûts (chart and table) even when the server's figures are
   words with markup. Nothing runs (window.__pwned stays undefined), no
   inline handler, frame or javascript: link is left (test_untrusted_text's sweep).
5. Past the daily cap no paid voice session opens without monsieur's click:
   the wake word is refused with a French explanation, the orb, the composer
   and the global hotkey ask first and 'Annuler' opens nothing; a cap reached
   while the page is open (a Claude task's cost, a lower cap in Réglages) is
   obeyed at once, without a reload.
"""
import json
import time
from datetime import datetime

import pytest
from test_integration_wave3 import clean, restore_settings, spend  # noqa: F401 - fixtures
from test_reminders_ui import clean_schedules, fire_now, schedule_items  # noqa: F401 - fixture
from test_untrusted_text import assert_inert, compact, evil, go_live, shown

pytestmark = pytest.mark.e2e

SESSION_ID = "async () => (await import('/static/js/voice.js')).sessionId()"
# A day far ahead whatever the real clock says: the running scheduler never fires it.
FAR = datetime(2099, 3, 2, 8, 0)


def until(condition, timeout=10.0):
    end = time.time() + timeout
    while not condition():
        if time.time() > end:
            raise AssertionError("condition jamais remplie")
        time.sleep(0.05)


def api(page, url, method="GET", body=None):
    return page.evaluate("([u, m, b]) => __jarvis.api(u, b === null ? {method: m} : {method: m, body: b})",
                         [url, method, body])


def requests_to(page, fragment):
    seen = []
    page.on("request", lambda r: seen.append((r.method, r.url)) if fragment in r.url else None)
    return seen

# ---------------------------------------------------------------- 4. reminders


def test_reminders_stay_inert_in_the_panel_its_edit_form_and_the_card_holds(jarvis, clean_schedules):  # noqa: F811
    from jarvis import events
    out = api(jarvis, "/api/schedules", "POST",
              {"title": compact("rappel-titre"), "text": evil("rappel-texte"), "delay_minutes": 600})
    rid = out["item"]["id"]
    api(jarvis, "/api/schedules", "POST", {"kind": "task", "profile": "lecture", "title": compact("routine-titre"),
                                           "text": evil("routine-consigne"), "delay_minutes": 700})
    shown(jarvis, "#scheduleList", "rappel-titre")
    shown(jarvis, "#scheduleList", "routine-titre")
    assert_inert(jarvis)
    # The edit form: the hostile words in the field, the date beside them.
    row = jarvis.locator(f'#scheduleList [data-key="s:{rid}"]')
    row.locator(".edit").click()
    form = row.locator("form.edit-form")
    form.wait_for()
    assert "rappel-titre<img" in form.get_by_role("textbox", name="Nouveau texte").input_value()
    assert form.locator("input.edit-when").count() == 1
    assert_inert(jarvis)
    form.get_by_role("button", name="Annuler la modification").click()
    form.wait_for(state="detached")

    # A list pushed with markup in every field (id, kind, repeat, days included).
    hostile_id = "x\"><img src=x onerror=\"window.__pwned='id'\">"
    events.publish("schedules", {"items": [
        {"id": hostile_id, "kind": "<b>task</b>", "title": compact("pousse-titre"), "text": evil("pousse-texte"),
         "due": time.time() + 3600, "repeat": "<img src=x onerror=\"window.__pwned='repeat'\">",
         "days": ["<img src=x onerror=\"window.__pwned='jour'\">", 2, "lun"]},
        {"id": "p2", "kind": "task", "title": evil("pousse-jours"), "due": time.time() + 7200, "repeat": "days",
         "days": ["<img src=x onerror=\"window.__pwned='jours'\">", 9, -1, 1.5]}]})
    shown(jarvis, "#scheduleList", "pousse-titre")
    shown(jarvis, "#scheduleList", "pousse-jours")
    hostile_row = jarvis.locator("#scheduleList .item", has_text="pousse-titre")
    hostile_row.locator(".edit").click()
    hostile_row.locator("form.edit-form").wait_for()
    assert_inert(jarvis)
    jarvis.keyboard.press("Escape")

    # A reminder that went off: its card and its snooze buttons.
    fire_now("xss-fire", evil("echeance"))
    card = jarvis.locator("#cards .card.warning", has_text="echeance")
    card.wait_for()
    assert card.locator(".actions button").all_inner_texts() == ["+10 min", "+1 h", "Demain", "Fait"]
    assert_inert(jarvis)
    card.get_by_role("button", name="+10 min").click()
    card.wait_for(state="detached")
    jarvis.locator("#toasts .toast", has_text="Rappel reporté").wait_for()
    until(lambda: any(i.get("snoozed_from") == "xss-fire" for i in schedule_items()))
    copy = next(i for i in schedule_items() if i.get("snoozed_from") == "xss-fire")
    assert (copy["kind"], copy["profile"], copy["repeat"]) == ("reminder", "recherche", "none")
    shown(jarvis, "#scheduleList", "echeance")
    assert_inert(jarvis)

# ---------------------------------------------------------------- 4. the local briefing


def test_briefing_with_hostile_agenda_weather_and_reminder_stays_inert_and_taints_holds(
        jarvis, clean_schedules, monkeypatch):  # noqa: F811
    from jarvis import ares, briefing, config, confirm, info, store
    place = "meteo-lieu<img src=x onerror=\"window.__pwned='meteo'\"><b>gras</b>"
    agenda = "\n".join(f"• {compact('agenda-titre')} — {when}" for when in ("Aujourd'hui · 14:00", "En retard (2 j)"))
    monkeypatch.setattr(config, "CITY", "Hostileville")
    monkeypatch.setattr(config, "BRIEFING_NEWS", False)
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: agenda)
    monkeypatch.setattr(info, "weather", lambda city=None, quand="maintenant":
                        {"ok": True, "text": f"Aujourd'hui à {place} : pluie faible, 12 °C.", "city": place})
    with store.LOCK:
        store.save("schedules.json", [{"id": "brief1", "kind": "reminder", "title": compact("rappel-brief"),
                                       "text": "x", "due": FAR.replace(hour=10).timestamp(), "repeat": "none",
                                       "profile": "recherche", "complexity": "normale", "created": time.time()}])
    go_live(jarvis)
    sid = jarvis.evaluate(SESSION_ID)
    assert not confirm.is_tainted(sid)
    payload = briefing.run(FAR)
    assert place in payload["text"] and "rappel-brief" in payload["text"]
    assert "agenda-titre" not in payload["text"]  # A.R.E.S's titles stay there: counts only
    # The card's body goes through core.md (sanitised markdown): the markup is dropped, never run.
    shown(jarvis, "#cards .card", "meteo-lieu")
    shown(jarvis, "#cards .card", "rappel-brief")
    shown(jarvis, "#cards .card", "1 tâche en retard")
    assert jarvis.locator("#cards .card iframe, #cards .card svg, #cards .card script").count() == 0
    assert jarvis.evaluate("[...document.querySelectorAll('#cards .card img')]"
                           ".every(i => [...i.attributes].every(a => ['src', 'alt', 'title'].includes(a.name)))")
    assert_inert(jarvis)
    # In the session: framed as outside data, and the session is tainted.
    jarvis.wait_for_function("__texts().some(t => t.includes('Données non fiables (Briefing du matin)')"
                             " && t.includes('meteo-lieu<img'))")
    frame = next(t for t in jarvis.evaluate("__texts()") if "Briefing du matin)" in t)
    assert frame.count("<donnees>") == 1 and frame.rstrip().endswith("</donnees>")
    until(lambda: confirm.is_tainted(sid))
    assert_inert(jarvis)

# ---------------------------------------------------------------- 4. « JARVIS a remarqué »


def test_remarques_stay_inert_whatever_the_server_sends_holds(jarvis, clean_schedules):  # noqa: F811
    from jarvis import events, remarques, tasks
    sent = requests_to(jarvis, "/api/")
    hostile_key = "k1\"]<img src=x onerror=\"window.__pwned='cle'\">"
    events.publish("remarques", {"items": [
        {"key": hostile_key, "kind": "<img src=x onerror=\"window.__pwned='kind'\">", "text": evil("remarque-texte"),
         "why": evil("remarque-pourquoi"),
         "action": {"type": "compose", "label": compact("remarque-action"), "text": evil("remarque-compose")}},
        {"key": "k2", "kind": "question", "text": compact("deuxieme"), "why": "",
         "action": {"type": "retry", "label": compact("relance"), "task": "../../x?<img src=x>",
                    "title": evil("titre-relance")}},
        {"key": "k3", "text": "troisieme", "action": {"type": "javascript:alert(1)", "label": compact("drole")}}]})
    section = jarvis.locator("#remarques")
    shown(jarvis, "#remarques li.remarque .txt", "remarque-texte")
    shown(jarvis, "#remarques li.remarque", "troisieme")
    first = section.locator("li.remarque").first
    first.locator(".why summary").click()
    shown(jarvis, "#remarques .why p", "remarque-pourquoi")
    assert_inert(jarvis)
    # « compose »: the words only go into the field, nothing is sent or opened.
    first.locator(".acts button.ctl").click()
    jarvis.wait_for_function("document.getElementById('askInput').value.includes('remarque-compose')")
    assert jarvis.evaluate("__jarvis.state.mode") in ("off", "standby")
    assert jarvis.evaluate("window.__pcs || 0") == 0
    # An unknown action does nothing; « retry » on a task id with markup: refused, said as text.
    section.locator("li.remarque", has_text="troisieme").locator(".acts button.ctl").click()
    section.locator("li.remarque", has_text="deuxieme").locator(".acts button.ctl").click()
    jarvis.locator("#toasts .toast").first.wait_for()
    assert_inert(jarvis)
    # ✕ on the hostile key: the server knows no such remark (French, as text).
    first.locator("button.x").click()
    jarvis.locator("#toasts .toast", has_text="Remarque introuvable").wait_for()
    assert_inert(jarvis)
    posts = [(m, u) for m, u in sent if m != "GET" and "/api/presence" not in u and "/api/journal" not in u]
    assert all("/retry" in u or "/dismiss" in u for _, u in posts), posts
    assert not tasks.running()
    # The real server, a failed task with a hostile title.
    now = time.time()
    tasks.TASKS["w3xss"] = {"id": "w3xss", "title": compact("tache-echec"), "status": "error", "prompt": "p",
                            "profile": "lecture", "complexity": "simple", "origin": "voix", "started": now - 700,
                            "ended": now - 600, "output": "", "log": []}
    try:
        remarques.refresh(force=True)
        shown(jarvis, "#remarques li.remarque .txt", "tache-echec<img")
        assert_inert(jarvis)
    finally:
        tasks.TASKS.pop("w3xss", None)

# ---------------------------------------------------------------- 4. costs


HOSTILE_USAGE = {
    "today": {"date": evil("jour"), "realtime_usd": "<img src=x onerror=\"window.__pwned='usd'\">",
              "claude_usd": 0.5, "claude_tasks": "<b>x</b>", "total_usd": "<img src=x>"},
    "days": [{"date": compact("date-a"), "realtime_usd": 0.2, "claude_usd": "<img src=x>", "total_usd": 0.3},
             {"date": "2026-10-09<img src=x onerror=\"window.__pwned='date'\">", "realtime_usd": 0.1,
              "claude_usd": 0.1, "total_usd": 0.2},
             {"date": evil("jour"), "realtime_usd": 0.0, "claude_usd": 0.5, "total_usd": 0.5}],
    "budget": 0.6, "warn_ratio": "<img src=x>", "capped": False, "note": evil("note"),
    "model": evil("modele"), "default_model": "<img src=x>",
    "prices": {"<img src=x onerror=\"window.__pwned='prix'\">": {"audio_out": "<b>"}},
    "transcribe": {"model": evil("transcription"), "per_minute": "<img src=x>"},
}


def test_cost_chip_warning_and_reglages_couts_stay_inert_holds(jarvis, reload_jarvis):
    from jarvis import events

    def hostile(route):
        if route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(HOSTILE_USAGE))
        else:
            route.continue_()
    jarvis.route("**/api/usage**", hostile)
    reload_jarvis()
    jarvis.wait_for_function("document.querySelector('#costChip button')?.textContent.startsWith(\"Aujourd'hui\")")
    assert "img" not in jarvis.text_content("#costChip button")
    jarvis.locator("#cards .card", has_text="du plafond du jour").first.wait_for()  # 0,50 $ of 0,60 $: 80 %
    assert_inert(jarvis)
    jarvis.click("#topActions button:has-text('Réglages')")
    jarvis.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    jarvis.click("#settingsDialog .set-tab:has-text('Coûts')")
    jarvis.wait_for_selector("#usageChart[data-state='ready'], #usageChart[data-state='unavailable']",
                             timeout=20_000)
    shown(jarvis, "#settingsUsage .usage-table", "date-a<img")
    assert jarvis.evaluate("document.querySelectorAll('#settingsUsage img, #settingsUsage script, "
                           "#usageChart foreignObject img, #usageChart foreignObject b').length") == 0
    if jarvis.evaluate("document.getElementById('usageChart').dataset.state") == "ready":
        labels = jarvis.evaluate("[...document.querySelectorAll('#usageChart text, #usageChart tspan')]"
                                 ".map(e => e.textContent).join(' ')")
        assert "<" not in labels and ">" not in labels  # neutralised before ApexCharts writes them
    assert_inert(jarvis)
    # A pushed figure with markup: the chip stays a number.
    events.publish("usage", {"today": {"date": evil("pousse"), "realtime_usd": "<img src=x>", "claude_usd": 0.1,
                                       "total_usd": evil("total")}, "budget": "<b>", "capped": "<img src=x>"})
    jarvis.wait_for_timeout(300)
    assert_inert(jarvis)
    jarvis.keyboard.press("Escape")
    jarvis.unroute("**/api/usage**")

# ---------------------------------------------------------------- 5. paid sessions past the cap


def refused_wake(page, app_server):
    """The wake word heard: no conversation, a French explanation."""
    minted, pcs = len(app_server.sessions), page.evaluate("window.__pcs || 0")
    page.wait_for_function("window.__rec && __rec.running && __jarvis.state.mode === 'standby'")
    page.evaluate("document.querySelectorAll('#toasts .toast').forEach(t => t.remove())")
    # Heard a minute 'later' each time (wake.js says it at most every 30 s), then the real clock again.
    page.evaluate("""() => { const real = Date.now, shift = (window.__shift || 0) + 60000;
                             window.__shift = shift; Date.now = () => real() + shift;
                             try { __say('Jarvis, ouvre Spotify', true); } finally { Date.now = real; } }""")
    page.wait_for_selector(".toast:has-text('Plafond de dépenses du jour atteint')")
    page.wait_for_timeout(300)
    assert page.evaluate("__jarvis.state.mode") == "standby"
    assert page.evaluate("window.__pcs || 0") == pcs and len(app_server.sessions) == minted


def test_past_the_cap_no_paid_session_opens_without_a_click_holds(jarvis, app_server, clean,  # noqa: F811
                                                                    restore_settings):  # noqa: F811
    from jarvis import config, usage
    spend(0.30, 0.12)
    jarvis.reload()
    jarvis.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === false")
    # The cap reached while the page is open: a Claude task's cost (no reload).
    config.DAILY_BUDGET_USD = 0.50
    usage.add_claude(0.10)
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    refused_wake(jarvis, app_server)
    minted = len(app_server.sessions)
    # Every other way in asks first; 'Annuler' (or Échap) opens nothing.
    jarvis.click("#orbBtn")
    jarvis.wait_for_selector("#capDialog[open]")
    assert "Cette conversation sera payante" in jarvis.text_content("#capText")
    jarvis.click("#capDialog button[value='cancel']")
    jarvis.wait_for_selector("#capDialog", state="hidden")
    jarvis.fill("#askInput", "Quel temps fait-il ?")
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_selector("#capDialog[open]")
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_selector("#capDialog", state="hidden")
    jarvis.wait_for_function("document.getElementById('askInput').value === 'Quel temps fait-il ?'")
    jarvis.evaluate("__jarvis.bus.emit('server:hotkey', {type: 'hotkey', action: 'talk', at: Date.now() / 1000})")
    jarvis.wait_for_selector("#capDialog[open]")
    jarvis.click("#capDialog button[value='cancel']")
    jarvis.wait_for_selector("#capDialog", state="hidden")
    jarvis.wait_for_timeout(300)
    assert jarvis.evaluate("window.__pcs || 0") == 0 and len(app_server.sessions) == minted
    assert jarvis.evaluate("__jarvis.state.mode") in ("off", "standby")

    # Raised in Réglages: the wake word opens a conversation again (not vacuous).
    assert api(jarvis, "/api/settings", "PUT", {"daily_budget_usd": 5})["ok"] is True
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === false")
    jarvis.wait_for_function("window.__rec && __rec.running && __jarvis.state.mode === 'standby'")
    jarvis.evaluate("__say('Jarvis, quelle heure est-il ?', true)")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    assert len(app_server.sessions) == minted + 1
    jarvis.evaluate("__jarvis.voice.sleep()")
    jarvis.wait_for_function("__jarvis.state.mode !== 'live'", timeout=10_000)
    # Lowered below today's spending in Réglages: obeyed at once, without a reload.
    assert api(jarvis, "/api/settings", "PUT", {"daily_budget_usd": 0.4})["ok"] is True
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    refused_wake(jarvis, app_server)
