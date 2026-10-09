"""HUD regressions found by the wave 1 review: links beside JARVIS, closing a
confirmation or lock card, the off-canvas side panel, the interim side panel
and report, the bottom sheet, the wake word switched off, the caption,
toasts, dead top actions, the error status, headings and a few details."""
import json
import re

import pytest

import voice_helpers
from test_layout import ORB_POINTS
from voice_helpers import emit, go_live

pytestmark = pytest.mark.e2e
clock_jarvis = voice_helpers.clock_jarvis  # the page whose clock the test controls (a fixture)

NNBSP = " "
COMPLET = {"title": "Ranger les téléchargements", "prompt": "Range ~/Downloads par type de fichier",
           "profile": "complet"}


@pytest.fixture
def nothing_real(monkeypatch):
    """No pending request from another test; nothing really opens or locks."""
    from jarvis import confirm, desktop
    confirm.PENDING.clear()
    done = []
    monkeypatch.setattr(desktop, "open_target", lambda **kw: done.append(kw) or {"ok": True})
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: done.append(action) or {"ok": True})
    yield done
    confirm.PENDING.clear()


def hud(page, call):
    return page.evaluate(f"async () => (await import('/static/js/hud.js')).{call}")


def call(name, call_id, args=None):
    return {"type": "response.done", "response": {"status": "completed", "output": [
        {"type": "function_call", "name": name, "call_id": call_id, "status": "completed",
         "arguments": json.dumps(args or {})}]}}


def output(page, call_id):
    page.wait_for_function(f"__sent.some(m => m.item && m.item.call_id === '{call_id}')")
    return json.loads(page.evaluate(f"__sent.find(m => m.item && m.item.call_id === '{call_id}').item.output"))


def status(page):
    return page.inner_text("#statusPill")


def box(page, selector):
    return page.evaluate(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                         " return {x: r.x, y: r.y, w: r.width, h: r.height, bottom: r.bottom, right: r.right}; })()")

# ---------------------------------------------------------------- links

def test_a_link_in_a_card_opens_beside_jarvis(jarvis):
    if not jarvis.evaluate("!!(window.marked && window.DOMPurify)"):
        pytest.skip("marked et DOMPurify (CDN) indisponibles")
    jarvis.context.route("https://exemple.fr/**", lambda route: route.fulfill(
        status=200, body="<title>Comparatif</title>", headers={"Content-Type": "text/html"}))
    go_live(jarvis)
    hud(jarvis, "addCard('Aspirateurs', 'Voir [le comparatif](https://exemple.fr/c).', 'result', {id: 'lien'})")
    link = jarvis.locator("#card-lien a")
    assert link.get_attribute("target") == "_blank"
    assert set(link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}
    assert link.evaluate("a => getComputedStyle(a).color") == "rgb(64, 220, 255)"  # --cyan
    with jarvis.context.expect_page() as opened:
        link.click()
    assert opened.value.url.startswith("https://exemple.fr/")
    assert jarvis.url.startswith("http://127.0.0.1")  # JARVIS stays, and so does its session
    assert jarvis.evaluate("__jarvis.state.mode") == "live"
    opened.value.close()

# ---------------------------------------------------------------- closing a card answers it

def test_closing_the_lock_countdown_card_cancels_the_lock(nothing_real, jarvis):
    go_live(jarvis)
    emit(jarvis, {"type": "response.created"})
    emit(jarvis, call("system_control", "lk", {"action": "lock_screen"}))
    x = jarvis.locator("#card-lock-countdown .x")
    x.wait_for()
    assert x.get_attribute("aria-label") == "Annuler le verrouillage"
    x.click()
    assert output(jarvis, "lk")["cancelled"] is True
    jarvis.wait_for_timeout(3500)
    assert "lock_screen" not in nothing_real


def test_closing_a_confirmation_card_answers_non(nothing_real, jarvis):
    from jarvis import confirm, tasks
    go_live(jarvis)
    emit(jarvis, call("delegate_to_claude", "d1", COMPLET))
    assert output(jarvis, "d1")["status"] == "needs_confirmation"
    card = jarvis.locator(".card.confirm[data-state='pending']")
    card.wait_for()
    x = card.locator(".x")
    assert x.get_attribute("aria-label") == "Refuser la demande et fermer la carte"
    with jarvis.expect_request("**/api/pending/*/decide") as req:
        x.click()
    assert req.value.post_data_json == {"decision": "non"}
    jarvis.wait_for_selector(".card[data-state='cancelled']:has-text('Annulé, rien n')")
    jarvis.wait_for_function("__jarvis.state.phase === 'listening'")
    assert status(jarvis) == "Je vous écoute…"
    assert not [p for p in confirm.PENDING.values() if p["state"] == "pending"]  # nothing left to say « oui » to
    assert not [t for t in tasks.TASKS.values() if t["profile"] == "complet"]

# ---------------------------------------------------------------- the off-canvas side panel

def test_the_off_canvas_panel_opens_under_the_top_bar_and_closes_by_mouse(jarvis):
    jarvis.set_viewport_size({"width": 1024, "height": 700})
    jarvis.click("#panelBtn")
    jarvis.wait_for_function("document.body.classList.contains('side-open')")
    jarvis.wait_for_timeout(300)  # the slide-in
    top = box(jarvis, "#topbar")
    assert abs(box(jarvis, "#side")["y"] - top["bottom"]) <= 1
    # Every top action stays reachable; what the panel covers is inert.
    for sel in ["#panelBtn", "#topActions > button:first-child"]:  # 'Panneau', 'Aide'
        b = box(jarvis, sel)
        hit = jarvis.evaluate(f"document.elementFromPoint({b['x'] + b['w'] / 2}, {b['y'] + b['h'] / 2})"
                              f".closest('button') === document.querySelector({json.dumps(sel)})")
        assert hit, sel
    assert jarvis.evaluate("document.getElementById('stage').inert && document.getElementById('cards').inert")
    # Its own ✕...
    close = jarvis.locator("#side .side-close")
    assert close.get_attribute("aria-label") == "Fermer le panneau"
    close.click()
    jarvis.wait_for_function("!document.body.classList.contains('side-open')")
    assert not jarvis.evaluate("document.getElementById('stage').inert")
    # ...the button again, and a click beside the panel.
    jarvis.click("#panelBtn")
    jarvis.wait_for_function("document.body.classList.contains('side-open')")
    jarvis.click("#panelBtn")
    jarvis.wait_for_function("!document.body.classList.contains('side-open')")
    jarvis.click("#panelBtn")
    jarvis.mouse.click(100, 400)
    jarvis.wait_for_function("!document.body.classList.contains('side-open')")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"  # that click only closed the panel

# ---------------------------------------------------------------- the side panel (WP10: tests/e2e/test_panels.py)

def test_side_panel_controls_are_labelled_buttons_with_french_words(jarvis):
    jarvis.evaluate("""__jarvis.bus.emit('server:task', {id: 'tk9', title: 'Comparatif', status: 'running',
      profile: 'recherche', started: Date.now() / 1000 - 65})""")
    cancel = jarvis.locator("#task-tk9 button.cancel")
    assert cancel.get_attribute("aria-label") == "Annuler la tâche « Comparatif »".replace(" »", f"{NNBSP}»").replace("« ", f"«{NNBSP}")
    assert re.fullmatch(r"1:0[5-7]", jarvis.inner_text("#task-tk9 .chrono"))
    assert "Web uniquement" in jarvis.inner_text("#task-tk9 .meta")
    jarvis.evaluate("""__jarvis.bus.emit('server:task', {id: 'tk9', title: 'Comparatif', status: 'done',
      profile: 'recherche', started: Date.now() / 1000 - 65, ended: Date.now() / 1000,
      output: '## Résultat\\n\\n| Modèle | Prix |\\n|---|---|\\n| A | 300 € |'})""")
    assert jarvis.inner_text("#task-tk9 .lbl") == "Terminée"
    assert jarvis.locator("#task-tk9 button.cancel").count() == 0
    out = jarvis.locator("#task-tk9 .out")
    if jarvis.evaluate("!!(window.marked && window.DOMPurify)"):
        assert out.locator("table td").first.inner_text() == "A"  # markdown, not '| Modèle |'
    assert out.get_attribute("tabindex") == "0"
    # Reminders and memory: buttons with what they delete, the 'Routine' tag, French times.
    due = "Date.now() / 1000 + 3600"
    jarvis.evaluate(f"""__jarvis.bus.emit('server:schedules', {{items: [
      {{id: 's1', kind: 'reminder', title: 'Thé', due: {due}}},
      {{id: 's2', kind: 'task', title: 'Point météo', due: {due}, repeat: 'daily'}}]}})""")
    rows = jarvis.locator("#scheduleList .item")
    assert rows.nth(0).locator("button.x").get_attribute("aria-label") == f"Supprimer le rappel «{NNBSP}Thé{NNBSP}»"
    assert re.fullmatch(r"\d{1,2} h( \d{2})?", rows.nth(0).locator(".when").inner_text()) \
        or rows.nth(0).locator(".when").inner_text().startswith("demain à")
    assert rows.nth(1).locator(".tag").inner_text() == "Routine"
    jarvis.evaluate("__jarvis.bus.emit('server:memory', {facts: [{id: 'm1', text: 'Préfère le thé'}]})")
    assert jarvis.get_attribute("#memoryList button.x", "aria-label") == f"Oublier «{NNBSP}Préfère le thé{NNBSP}»"
    jarvis.evaluate("__jarvis.bus.emit('server:schedules', {items: []}); __jarvis.bus.emit('server:memory', {facts: []})")
    assert jarvis.inner_text("#scheduleList").startswith("Aucun rappel.")
    assert jarvis.inner_text("#memoryList").startswith("Je ne sais encore rien de vous.")
    # No text under 12 px, no spaced-out section titles.
    small = jarvis.evaluate("""[...document.querySelectorAll('#side *')].filter(e => e.childNodes.length
      && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
      && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.className || e.tagName)""")
    assert small == []
    assert jarvis.evaluate("parseFloat(getComputedStyle(document.querySelector('#side section h2')).letterSpacing)") <= 12 * .18 + .01

# ---------------------------------------------------------------- the report (WP11: tests/e2e/test_report.py)

def test_the_report_is_centred_opaque_and_in_french(jarvis):
    jarvis.set_viewport_size({"width": 800, "height": 600})
    rows = [[f"Ligne {i}", 1000 + i * 37] for i in range(14)]
    jarvis.evaluate("""rows => import('/static/js/report.js').then(m => m.showReport({title: 'Ventes',
      table: {columns: ['Mois', 'CA'], rows}}))""", rows)
    jarvis.wait_for_selector("#report[open]")
    r = box(jarvis, "#report")
    assert abs(r["x"] - 24) <= 1 and abs(r["w"] - (800 - 48)) <= 1  # centred, whatever the width
    assert jarvis.evaluate("getComputedStyle(document.getElementById('report')).backgroundColor") == "rgb(7, 20, 30)"
    # The native table: French digit grouping, French pager (25 rows a page).
    jarvis.wait_for_selector("#rtable tbody td")
    assert f"1{NNBSP}037" in jarvis.inner_text("#rtable tbody")
    assert jarvis.locator("#rtable .rtable-pager").is_hidden()  # 14 rows: one page
    # Closed with its ✕ from the keyboard: the focus does not fall to <body>.
    jarvis.focus("#report .rhead .x")
    jarvis.keyboard.press("Enter")
    assert not jarvis.evaluate("document.getElementById('report').open")
    jarvis.wait_for_function("document.activeElement && document.activeElement !== document.body")

# ---------------------------------------------------------------- the bottom sheet (≤ 900 px)

LONG = "Une réponse longue. " * 30


def test_the_newest_card_in_the_sheet_scrolls_and_expands(jarvis):
    jarvis.set_viewport_size({"width": 800, "height": 600})
    hud(jarvis, f"addCard('Long', {json.dumps(LONG)}, 'info', {{id: 'long'}})")
    body = jarvis.locator("#card-long .body")
    assert body.evaluate("b => getComputedStyle(b).overflowY") == "auto"  # the wheel scrolls it
    assert body.get_attribute("tabindex") == "0"                         # and the keyboard
    more = jarvis.locator("#cards .cards-bar .more")
    assert more.is_visible() and more.get_attribute("aria-label") == "Afficher toute la carte"
    more.click()
    assert body.evaluate("b => b.scrollHeight <= b.clientHeight + 1")
    # 'Aide' shows whole: every example can be reached.
    hud(jarvis, "clearCards()")
    jarvis.keyboard.press("?")
    jarvis.wait_for_selector("#card-aide .aide-ex")
    aide = jarvis.locator("#card-aide .body")
    assert aide.evaluate("b => b.scrollHeight <= b.clientHeight + 1")
    assert not jarvis.locator("#card-aide time.meta").is_visible()


def test_a_confirmation_in_the_sheet_keeps_the_orb_and_its_buttons(nothing_real, jarvis):
    jarvis.set_viewport_size({"width": 800, "height": 600})
    go_live(jarvis)
    emit(jarvis, call("delegate_to_claude", "d1", COMPLET))
    jarvis.wait_for_selector(".card.confirm[data-state='pending'] .confirm-detail:has-text('Range')")
    jarvis.wait_for_timeout(300)
    assert box(jarvis, "#orbBtn")["w"] >= 150
    assert set(jarvis.evaluate(ORB_POINTS)) <= {"orbBtn", "orb"}  # nothing covers it
    lancer = box(jarvis, ".card.confirm .actions button.primary")
    assert lancer["bottom"] <= 600

# ---------------------------------------------------------------- status: the wake word off

def test_the_wake_word_switched_off_is_not_an_outage(jarvis, reload_jarvis):
    jarvis.click("#wakeBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'off'")
    assert status(jarvis) == "En veille · mot d'éveil désactivé · cliquez sur l'orbe"
    reload_jarvis()
    jarvis.wait_for_function("__jarvis.state.mode === 'off'")
    assert status(jarvis) == "En veille · mot d'éveil désactivé · cliquez sur l'orbe"
    jarvis.click("#wakeBtn")  # back on for the next tests (a per-browser setting)

# ---------------------------------------------------------------- the caption

def test_a_long_answer_shows_its_newest_words_and_fades_the_rest(jarvis):
    go_live(jarvis)
    text = "Voici les trois modèles retenus, avec leurs avantages et leurs défauts. " * 6 + "Quel est votre prix ?"
    emit(jarvis, {"type": "response.created"})
    emit(jarvis, {"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": text})
    t = jarvis.locator("#transcript")
    assert "clipped" in t.get_attribute("class")
    assert t.evaluate("b => b.scrollTop > 0 && b.scrollTop + b.clientHeight >= b.scrollHeight - 1")
    assert t.text_content().endswith(f"prix{NNBSP}?")  # the '?' never wraps alone
    jarvis.set_viewport_size({"width": 800, "height": 600})
    two_lines = 2 * 19 * 1.45
    assert abs(t.evaluate("b => parseFloat(getComputedStyle(b).maxHeight)") - two_lines) < 1

# ---------------------------------------------------------------- toasts

def test_toasts_stay_clear_of_the_controls_and_wait_while_pointed_at(jarvis):
    go_live(jarvis)
    jarvis.clock.install()
    hud(jarvis, "toast('Rappel supprimé', {actionLabel: 'Annuler'})")
    toast = jarvis.locator("#toasts .toast")
    t, controls, top = box(jarvis, "#toasts .toast"), box(jarvis, "#controls"), box(jarvis, "#topbar")
    assert t["y"] >= top["bottom"]
    assert t["bottom"] <= controls["y"] or t["y"] >= controls["bottom"]
    toast.hover()
    jarvis.clock.run_for(10_000)
    assert toast.count() == 1  # held while pointed at
    jarvis.mouse.move(5, 300)
    jarvis.clock.run_for(3500)
    assert toast.count() == 0

# ---------------------------------------------------------------- top actions, status, suffixes

def test_journal_and_reglages_wait_for_their_module(jarvis):
    for name in ["Journal", "Réglages"]:
        assert jarvis.is_hidden(f"#topActions button:has-text('{name}')")
    jarvis.evaluate("__jarvis.bus.emit('ui:ready', 'journal')")
    assert jarvis.is_visible("#topActions button:has-text('Journal')")


def test_an_error_reads_as_a_sentence_with_its_buttons_beside_it(jarvis):
    jarvis.evaluate("__jarvis.bus.emit('error', {kind: 'connect', message: 'Le micro est occupé par une autre"
                    " application ou bloqué par Windows (Confidentialité › Microphone).', retry: () => {}})")
    pill = box(jarvis, "#statusPill")
    retry = box(jarvis, "#statusActions button")
    assert abs((pill["y"] + pill["h"] / 2) - (retry["y"] + retry["h"] / 2)) < pill["h"] / 2  # one row
    style = jarvis.evaluate("""(() => { const s = getComputedStyle(document.getElementById('statusPill'));
      return {radius: s.borderTopLeftRadius, align: s.textAlign, live: document.getElementById('statusPill').getAttribute('aria-live')}; })()""")
    assert style == {"radius": "4px", "align": "left", "live": "off"}  # #srAlert says it once


def test_the_running_task_suffix_shows_at_once(jarvis):
    text = jarvis.evaluate("""async () => {
      __jarvis.bus.emit('server:task', {id: 'tk1', title: 'Analyse', status: 'running', started: Date.now() / 1000});
      await Promise.resolve();
      return document.getElementById('statusPill').textContent; }""")
    assert "1 tâche en cours" in text
    jarvis.evaluate("__jarvis.state.tasks.clear()")


def test_the_orb_is_centred_at_1024_without_cards(jarvis):
    jarvis.set_viewport_size({"width": 1024, "height": 700})
    orb = box(jarvis, "#orbBtn")
    assert abs(orb["x"] + orb["w"] / 2 - 512) <= 2

# ---------------------------------------------------------------- screen readers, headings, details

def test_a_confirmation_is_announced_once(nothing_real, jarvis):
    go_live(jarvis)
    jarvis.evaluate("document.getElementById('sr').textContent = ''")
    emit(jarvis, call("delegate_to_claude", "d1", COMPLET))
    jarvis.wait_for_selector(".card.confirm[data-state='pending']")
    assert jarvis.inner_text("#srAlert").startswith("Confirmation requise")
    assert "Confirmation requise" not in jarvis.text_content("#sr")


def test_one_h1_and_the_cards_heading_before_theirs(jarvis):
    hud(jarvis, "addCard('Météo', 'Beau', 'info')")
    heads = jarvis.evaluate("[...document.querySelectorAll('h1, h2, h3')].map(h => h.tagName + ':' + h.textContent.trim())")
    assert heads[0] == "H1:J.A.R.V.I.S." and [h for h in heads if h.startswith("H1")] == ["H1:J.A.R.V.I.S."]
    assert heads.index("H2:Affichages de JARVIS") < heads.index("H3:Météo")


def test_muted_keyword_and_badge_place(jarvis):
    go_live(jarvis)
    jarvis.keyboard.press("Control+m")
    assert jarvis.evaluate("getComputedStyle(document.querySelector('#statusPill b')).color") == "rgb(255, 107, 129)"
    jarvis.keyboard.press("Control+m")
    jarvis.evaluate("document.getElementById('badge').hidden = false; document.getElementById('badge').textContent = '2'")
    orb, badge = box(jarvis, "#orbBtn"), box(jarvis, "#badge")
    dist = ((badge["x"] + badge["w"] / 2 - orb["x"] - orb["w"] / 2) ** 2
            + (badge["y"] + badge["h"] / 2 - orb["y"] - orb["h"] / 2) ** 2) ** .5
    assert orb["w"] / 2 + 16 <= dist <= orb["w"] / 2 + 28  # on the ring's diagonal, just outside it
    jarvis.evaluate("document.getElementById('badge').hidden = true")


def test_the_animations_setting_stops_the_rotating_hint(clock_jarvis):
    page = clock_jarvis  # standby: the hint rotates every 8 s (test_composer.py)
    page.evaluate("__jarvis.settings.set('motion', 'reduced')")
    page.clock.fast_forward(1500)  # the 1 s tick applies body.reduce-motion
    hint = page.inner_text("#hint")
    page.clock.fast_forward(8000)
    page.clock.fast_forward(8000)
    assert page.inner_text("#hint") == hint  # auto-updating content stops too (WCAG 2.2.2)
    page.evaluate("__jarvis.settings.set('motion', 'auto')")
    page.clock.fast_forward(1500)
    page.clock.fast_forward(8000)
    assert page.inner_text("#hint") != hint


def test_toggles_say_their_state_in_their_label_only(jarvis):
    """A toggle's label must not change with aria-pressed (WAI-ARIA APG): the
    spec's 'Micro : activé / coupé' says the state, so no aria-pressed."""
    def label(sel):
        return jarvis.text_content(sel).replace(NNBSP, " ")
    for sel in ["#wakeBtn", "#dndToggle"]:
        assert jarvis.get_attribute(sel, "aria-pressed") is None
    assert label("#wakeBtn") == "Mot d'éveil : activé"
    go_live(jarvis)
    assert jarvis.get_attribute("#micBtn", "aria-pressed") is None
    assert label("#micBtn") == "Micro : activé"
    jarvis.keyboard.press("Control+m")
    assert label("#micBtn") == "Micro : coupé" and jarvis.get_attribute("#micBtn", "aria-pressed") is None
    assert jarvis.evaluate("getComputedStyle(document.getElementById('micBtn')).color") == "rgb(255, 107, 129)"
