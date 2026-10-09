"""Layout, status and cards of the HUD (design spec §2-4, WP04): the grid and
its breakpoints, the orb that nothing covers, the status pill in every state,
the caption, the live controls and the display cards."""
import re

import pytest

pytestmark = pytest.mark.e2e

SIZES = [(800, 600), (1024, 700), (1280, 720), (1440, 900), (1920, 1080)]

SIX_CARDS = """async () => {
  const hud = await import('/static/js/hud.js');
  hud.addCard('Météo Laon', '**14 °C**, averses éparses, vent 25 km/h', 'result');
  hud.addCard('Définition', "**Agroécologie** : un ensemble de pratiques agricoles qui s'appuient sur les écosystèmes.", 'info');
  hud.addCard('Commande PowerShell', 'Get-ChildItem -Recurse -Filter *.xlsx | Sort-Object LastWriteTime -Descending', 'code');
  hud.addCard('Rappel', 'Appeler le garage pour le contrôle technique de la Clio', 'warning');
  hud.addCard('Conversion', '250 € = 268,40 $ (taux 1,0736)', 'result');
  hud.addCard('Étapes pour configurer le NAS', '1. Ouvrir DSM\\n2. Panneau de configuration\\n3. Activer SMB 3', 'info');
}"""

# The orb's centre and four points at 30 % of its width around it.
ORB_POINTS = """() => {
  const r = document.getElementById('orbBtn').getBoundingClientRect();
  const cx = r.x + r.width / 2, cy = r.y + r.height / 2, d = r.width * 0.3;
  return [[cx, cy], [cx - d, cy], [cx + d, cy], [cx, cy - d], [cx, cy + d]].map(([x, y]) => {
    const el = document.elementFromPoint(x, y);
    return el ? el.id || el.className || el.tagName : null;
  });
}"""

# Every point of a 15x15 grid inside the orb's disc must land on the orb.
ORB_DISC = """() => {
  const r = document.getElementById('orbBtn').getBoundingClientRect();
  const cx = r.x + r.width / 2, cy = r.y + r.height / 2, rad = r.width / 2 - 2, out = [];
  for (let i = 0; i < 15; i++) for (let j = 0; j < 15; j++) {
    const x = cx - rad + (2 * rad * i) / 14, y = cy - rad + (2 * rad * j) / 14;
    if (Math.hypot(x - cx, y - cy) > rad) continue;
    const el = document.elementFromPoint(x, y);
    if (!el || (el.id !== 'orbBtn' && el.id !== 'orb')) out.push([Math.round(x), Math.round(y), el && (el.id || el.className || el.tagName)]);
  }
  return out;
}"""

# Elements of #stage (other than the orb and its ancestors) that take the
# pointer and whose box reaches into the orb's disc.
STAGE_OVERLAPS = """() => {
  const btn = document.getElementById('orbBtn'), r = btn.getBoundingClientRect();
  const cx = r.x + r.width / 2, cy = r.y + r.height / 2, rad = r.width / 2;
  const hits = [];
  for (const el of document.querySelectorAll('#stage *')) {
    if (el === btn || btn.contains(el) || el.contains(btn)) continue;
    const cs = getComputedStyle(el);
    if (cs.pointerEvents === 'none' || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height) continue;
    const nx = Math.max(b.left, Math.min(cx, b.right)), ny = Math.max(b.top, Math.min(cy, b.bottom));
    if (Math.hypot(nx - cx, ny - cy) < rad - 1) hits.push(el.id || el.className || el.tagName);
  }
  return hits;
}"""


def hud(page, call):
    """Call hud.js (e.g. "addCard('a', 'b')") and return the result."""
    return page.evaluate(f"async () => {{ const hud = await import('/static/js/hud.js'); return hud.{call}; }}")


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def set_phase(page, phase, label=""):
    """What voice.js (WP02) does on each Realtime event: state.phase and the bus."""
    page.evaluate("([p, l]) => { __jarvis.state.phase = p; __jarvis.bus.emit('phase', {phase: p, label: l}); }",
                  [phase, label])


def caption(page):
    page.evaluate("""() => {
      __jarvis.bus.emit('caption:user', {itemId: 'u1', text: 'Jarvis, analyse les ventes du trimestre', final: true});
      __jarvis.bus.emit('caption:jarvis', {itemId: 'a1', text: 'Très bien, monsieur. Le cidre recule de trois pour cent, principalement en grandes surfaces.', final: true});
    }""")


def status(page):
    return page.inner_text("#statusPill").replace("\u202f", " ").replace("\u00a0", " ")


def assert_orb_free(page):
    points = page.evaluate(ORB_POINTS)
    assert all(p in ("orb", "orbBtn") for p in points), points
    assert page.evaluate(ORB_DISC) == []
    assert page.evaluate(STAGE_OVERLAPS) == []


# ---------------------------------------------------------------- the orb is never covered

@pytest.mark.parametrize("size", SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_nothing_covers_the_orb(jarvis, size):
    jarvis.set_viewport_size({"width": size[0], "height": size[1]})
    jarvis.evaluate(SIX_CARDS)
    jarvis.wait_for_timeout(300)  # card entrance and the bottom sheet measure
    assert jarvis.locator("#cards .card").count() == 6
    assert_orb_free(jarvis)
    go_live(jarvis)
    caption(jarvis)
    set_phase(jarvis, "speaking")
    jarvis.wait_for_timeout(200)
    assert_orb_free(jarvis)
    # the orb stays a usable size, and round
    box = jarvis.locator("#orbBtn").bounding_box()
    assert box["width"] >= 120 and abs(box["width"] - box["height"]) < 1


def test_caption_and_cards_do_not_intersect(jarvis):
    jarvis.set_viewport_size({"width": 1440, "height": 900})
    jarvis.evaluate(SIX_CARDS)
    go_live(jarvis)
    caption(jarvis)
    a, b = jarvis.locator("#caption").bounding_box(), jarvis.locator("#cards").bounding_box()
    assert a["height"] > 0
    assert a["x"] >= b["x"] + b["width"] or b["x"] >= a["x"] + a["width"] \
        or a["y"] >= b["y"] + b["height"] or b["y"] >= a["y"] + a["height"], (a, b)


@pytest.mark.parametrize("width,height", [(800, 600), (360, 640)])
def test_no_horizontal_scroll(jarvis, width, height):
    jarvis.set_viewport_size({"width": width, "height": height})
    jarvis.evaluate(SIX_CARDS)
    go_live(jarvis)
    caption(jarvis)
    jarvis.evaluate("__jarvis.bus.emit('error', {kind: 'connect', message: 'OpenAI 401: invalid_api_key'})")
    assert jarvis.evaluate("document.scrollingElement.scrollWidth <= innerWidth")
    # and nothing in the stage pokes out of the window either
    assert jarvis.evaluate("""[...document.querySelectorAll('#stage *, #topbar *')]
      .filter(e => getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width)
      .every(e => e.getBoundingClientRect().right <= innerWidth + 1)""")


def test_grid_columns_follow_the_breakpoints(jarvis):
    def widths():
        return jarvis.evaluate("""[document.getElementById('cards'), document.getElementById('side')]
          .map(e => Math.round(e.getBoundingClientRect().width))""")
    jarvis.set_viewport_size({"width": 1440, "height": 900})
    assert widths() == [340, 360]
    jarvis.set_viewport_size({"width": 1280, "height": 720})
    assert widths() == [300, 300]
    jarvis.set_viewport_size({"width": 1920, "height": 1080})
    assert widths() == [340, 360]
    # no fixed pixel offsets left: the top bar spans the window
    assert jarvis.evaluate("Math.round(document.getElementById('topbar').getBoundingClientRect().width)") == 1920


def test_side_panel_goes_off_canvas_below_1100(jarvis):
    jarvis.set_viewport_size({"width": 1440, "height": 900})
    assert not jarvis.is_visible("#panelBtn")
    assert jarvis.evaluate("getComputedStyle(document.getElementById('side')).backdropFilter") == "blur(6px)"

    jarvis.set_viewport_size({"width": 1024, "height": 700})
    side = "document.getElementById('side')"
    # after its slide-out: off the window, and hidden (out of the tab order) while closed
    jarvis.wait_for_function(f"{side}.getBoundingClientRect().left >= innerWidth"
                             f" && getComputedStyle({side}).visibility === 'hidden'")
    assert jarvis.is_visible("#panelBtn")
    assert jarvis.get_attribute("#panelBtn", "aria-expanded") == "false"
    assert jarvis.get_attribute("#panelBtn", "aria-controls") == "side"
    jarvis.click("#panelBtn")
    jarvis.wait_for_function(f"Math.abs({side}.getBoundingClientRect().right - innerWidth) < 1")
    assert jarvis.evaluate("document.body.classList.contains('side-open')")
    assert jarvis.get_attribute("#panelBtn", "aria-expanded") == "true"
    assert jarvis.evaluate(f"{side}.contains(document.activeElement)")
    jarvis.keyboard.press("Escape")
    assert not jarvis.evaluate("document.body.classList.contains('side-open')")
    assert jarvis.get_attribute("#panelBtn", "aria-expanded") == "false"
    assert jarvis.evaluate("document.activeElement.id") == "panelBtn"
    # widening the window closes it for good
    jarvis.click("#panelBtn")
    jarvis.set_viewport_size({"width": 1440, "height": 900})
    jarvis.wait_for_function("!document.body.classList.contains('side-open')")


def test_bottom_sheet_below_900(jarvis):
    jarvis.set_viewport_size({"width": 800, "height": 600})
    assert not jarvis.is_visible("#cards")  # no cards: no sheet
    hud(jarvis, "addCard('Un', 'premier', 'info')")
    hud(jarvis, "addCard('Deux', 'deuxième', 'info')")
    hud(jarvis, "addCard('Trois', 'troisième', 'result')")
    visible = jarvis.evaluate("[...document.querySelectorAll('#cards .card')].filter(c => c.checkVisibility()).map(c => c.querySelector('h3').textContent)")
    assert visible == ["Trois"]  # the newest only
    more = jarvis.locator("#cards .more")
    assert more.is_visible() and more.inner_text() == "+2"
    assert more.get_attribute("aria-expanded") == "false"
    assert more.get_attribute("aria-label") == "Afficher 2 autres cartes"
    # the stage keeps clear of the sheet
    sheet = jarvis.locator("#cards").bounding_box()
    pill = jarvis.locator("#statusPill").bounding_box()
    assert pill["y"] + pill["height"] <= sheet["y"]
    more.click()
    assert jarvis.evaluate("[...document.querySelectorAll('#cards .card')].every(c => c.checkVisibility())")
    assert more.get_attribute("aria-expanded") == "true"
    assert jarvis.evaluate("document.getElementById('cards').getBoundingClientRect().height <= innerHeight * 0.38 + 1")
    assert_orb_free(jarvis)


# ---------------------------------------------------------------- status pill

def test_status_pill_in_every_state(jarvis):
    assert jarvis.evaluate("getComputedStyle(document.getElementById('statusPill')).textTransform") == "none"
    assert status(jarvis).startswith("En veille · dites « Jarvis » ou cliquez sur l'orbe")
    assert jarvis.inner_text("#statusPill b") == "En veille"  # only the keyword is bold
    assert jarvis.get_attribute("#orbBtn", "aria-label") == "Parler à JARVIS"

    # where the wake word is heard (wake.js tells, WP07)
    jarvis.evaluate("__jarvis.state.wakeEngine = 'local'")
    hud(jarvis, "renderStatus()")
    assert status(jarvis).endswith("ou cliquez sur l'orbe · écoute locale")
    jarvis.evaluate("__jarvis.state.wakeEngine = 'google'")
    hud(jarvis, "renderStatus()")
    assert status(jarvis).endswith(" · écoute via Google")
    jarvis.evaluate("delete __jarvis.state.wakeEngine")

    jarvis.click("#wakeBtn")  # wake word off: offline
    assert status(jarvis) == "Hors ligne · cliquez sur l'orbe ou appuyez sur Espace pour parler"
    assert jarvis.evaluate("document.body.dataset.state") == "off-idle"
    jarvis.click("#wakeBtn")

    jarvis.evaluate("__jarvis.state.mode = 'connecting'; __jarvis.bus.emit('mode', {mode: 'connecting'})")
    assert status(jarvis) == "Connexion…"
    jarvis.evaluate("__jarvis.state.retries = 2; __jarvis.bus.emit('mode', {mode: 'connecting', reason: 'retry'})")
    assert status(jarvis) == "Reconnexion (2/5)…"
    jarvis.evaluate("__jarvis.state.retries = 0; __jarvis.state.mode = 'standby'; __jarvis.bus.emit('mode', {mode: 'standby'})")

    go_live(jarvis)
    assert status(jarvis) == "Je vous écoute…"
    assert jarvis.get_attribute("#orbBtn", "aria-label") == "Mettre JARVIS en veille"
    for phase, text in [("user", "Je vous entends…"), ("thinking", "Réflexion…"),
                        ("speaking", "JARVIS répond · parlez ou appuyez sur Échap pour l'interrompre"),
                        ("confirm", "En attente de votre confirmation"), ("listening", "Je vous écoute…")]:
        set_phase(jarvis, phase)
        assert status(jarvis) == text
        assert jarvis.evaluate("document.body.dataset.state") == f"live-{phase}"
    set_phase(jarvis, "tool", "Je confie la tâche à Claude…")
    assert status(jarvis) == "Je confie la tâche à Claude…"
    # after 10 s a running tool shows its time
    hud(jarvis, "renderStatus()")
    jarvis.evaluate("""async () => { const hud = await import('/static/js/hud.js'); const real = Date.now;
      Date.now = () => real() + 12500; try { hud.renderStatus(); } finally { Date.now = real; } }""")
    assert status(jarvis) == "Je confie la tâche à Claude… (12 s)"
    # orb label for Windows contrast themes: the keyword
    assert jarvis.get_attribute("#orbBtn", "data-label") == "Je confie la tâche à Claude…"


def test_status_suffixes_tasks_countdown_and_muted(jarvis):
    go_live(jarvis)
    # one running task: its time ticks, without being re-announced
    jarvis.evaluate("__jarvis.state.tasks.set('t1', {id: 't1', status: 'running', started: Date.now() / 1000 - 42})")
    hud(jarvis, "renderStatus()")
    assert re.fullmatch(r"Je vous écoute… · 1 tâche en cours \(0:4[23]\)", status(jarvis))
    assert jarvis.get_attribute("#statusPill", "aria-live") == "polite"
    jarvis.wait_for_function("document.getElementById('statusPill').textContent.includes('(0:44)')")
    assert jarvis.get_attribute("#statusPill", "aria-live") == "off"  # a tick, not news
    jarvis.evaluate("__jarvis.state.tasks.set('t2', {id: 't2', status: 'running', started: Date.now() / 1000})")
    hud(jarvis, "renderStatus()")
    assert status(jarvis) == "Je vous écoute… · 2 tâches en cours"
    assert jarvis.get_attribute("#statusPill", "aria-live") == "polite"
    jarvis.evaluate("__jarvis.state.tasks.clear()")

    # the idle countdown, in the last 15 s
    jarvis.evaluate("__jarvis.state.config.idle_minutes = 1; __jarvis.state.lastActivity = Date.now() - 50000")
    hud(jarvis, "renderStatus()")
    assert re.fullmatch(r"Veille dans 1[01] s", status(jarvis))
    jarvis.evaluate("__jarvis.state.lastActivity = Date.now(); __jarvis.state.config.idle_minutes = 3")

    jarvis.evaluate("__jarvis.voice.setMuted(true)")
    assert status(jarvis) == "Micro coupé · Ctrl+M pour le réactiver"
    assert jarvis.evaluate("document.body.classList.contains('muted')")
    jarvis.evaluate("__jarvis.voice.setMuted(false)")
    assert status(jarvis) == "Je vous écoute…"


def test_error_variant_until_success_or_dismissed(jarvis):
    jarvis.evaluate("__jarvis.bus.emit('error', {kind: 'connect', message: 'OpenAI 401: invalid_api_key'})")
    pill = jarvis.locator("#statusPill")
    assert "err" in pill.get_attribute("class").split()
    assert status(jarvis) == "Erreur · Clé OpenAI refusée : vérifiez-la sur platform.openai.com/api-keys."
    assert jarvis.evaluate("getComputedStyle(document.getElementById('statusPill')).color") == "rgb(255, 107, 129)"
    assert jarvis.text_content("#srAlert").startswith("Clé OpenAI refusée")
    assert jarvis.get_attribute("#srAlert", "role") == "alert"
    # dismissing it
    x = jarvis.locator("#statusActions .x")
    assert x.evaluate("e => e.tagName") == "BUTTON" and x.get_attribute("aria-label") == "Effacer le message d'erreur"
    x.click()
    assert "err" not in (pill.get_attribute("class") or "").split()
    assert status(jarvis).startswith("En veille")
    # a connection error offers to retry; going live clears it
    jarvis.evaluate("__jarvis.bus.emit('error', {kind: 'connect', message: 'Failed', detail: Object.assign(new Error('x'), {name: 'NotAllowedError'})})")
    assert status(jarvis).startswith("Erreur · Micro bloqué : cliquez sur le cadenas")
    jarvis.click("#statusActions button:has-text('Réessayer')")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    assert status(jarvis) == "Je vous écoute…"


def test_server_down_is_said(jarvis):
    jarvis.wait_for_timeout(1100)  # the 1 s tick has seen the server connected
    jarvis.evaluate("__jarvis.state.synced = false")
    jarvis.wait_for_function("document.getElementById('statusPill').textContent.startsWith('Serveur JARVIS déconnecté')")
    assert "déconnecté" in jarvis.text_content("#serverChip")
    jarvis.evaluate("__jarvis.state.synced = true")
    jarvis.wait_for_function("document.getElementById('statusPill').textContent.startsWith('En veille')")
    assert "connecté" in jarvis.text_content("#serverChip") and "déconnecté" not in jarvis.text_content("#serverChip")


# ---------------------------------------------------------------- caption and controls

def test_caption_lines(jarvis):
    go_live(jarvis)
    assert jarvis.get_attribute("#caption", "aria-hidden") == "true"
    jarvis.evaluate("__jarvis.bus.emit('caption:user', {itemId: 'u1', text: 'Quelle heure est-il ?', final: true})")
    assert jarvis.inner_text("#you").replace("\u202f", " ") == "Vous : Quelle heure est-il ?"
    emit = "([i, t, f]) => __jarvis.bus.emit('caption:jarvis', {itemId: i, text: t, final: f})"
    jarvis.evaluate(emit, ["a1", "Il est", False])
    jarvis.evaluate(emit, ["a1", "Il est quinze heures", False])  # cumulative
    jarvis.evaluate(emit, ["a1", ", monsieur.", False])              # or a delta
    assert jarvis.inner_text("#transcript .line") == "Il est quinze heures, monsieur."
    for i in range(2, 7):
        jarvis.evaluate(emit, [f"a{i}", f"Phrase {i}.", True])
    lines = jarvis.evaluate("[...document.querySelectorAll('#transcript .line')].map(l => [l.textContent, l.classList.contains('old')])")
    assert lines == [["Phrase 3.", True], ["Phrase 4.", True], ["Phrase 5.", True], ["Phrase 6.", False]]
    # three lines visible at most
    assert jarvis.evaluate("""(() => { const t = document.getElementById('transcript');
      return t.clientHeight <= 3 * parseFloat(getComputedStyle(t).lineHeight) + 2; })()""")


def test_live_controls(jarvis):
    assert jarvis.evaluate("document.getElementById('controls').hidden")
    go_live(jarvis)
    labels = jarvis.evaluate("[...document.querySelectorAll('#controls button')].map(b => b.textContent.replace(/\\u202f/g, ' '))")
    assert labels == ["Micro : activé", "Interrompre", "Veille"]
    assert jarvis.is_disabled("#interruptBtn")
    set_phase(jarvis, "speaking")
    assert not jarvis.is_disabled("#interruptBtn")
    jarvis.evaluate("__sent.length = 0; __emit({type: 'response.created'})")
    jarvis.click("#interruptBtn")
    assert "response.cancel" in jarvis.evaluate("__types()")
    jarvis.click("#micBtn")
    assert jarvis.get_attribute("#micBtn", "aria-pressed") == "true"
    assert jarvis.inner_text("#micBtn").replace("\u202f", " ") == "Micro : coupé"
    jarvis.click("#micBtn")
    assert jarvis.get_attribute("#micBtn", "aria-pressed") == "false"
    jarvis.click("#sleepBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'standby'")
    assert jarvis.evaluate("document.getElementById('controls').hidden")
    assert jarvis.is_visible("#wakeBtn")


def test_top_actions_open_their_panels(jarvis):
    # (bus.on returns its 'off' function: evaluate would call it, hence the trailing 0)
    jarvis.evaluate("window.__opened = []; __jarvis.bus.on('ui:open', n => __opened.push(n)); 0")
    names = jarvis.evaluate("[...document.querySelectorAll('#topActions button')].map(b => b.textContent)")
    assert names == ["Aide", "Journal", "Panneau", "Réglages"]
    for name in ["Aide", "Journal", "Réglages"]:
        jarvis.click(f"#topActions button:has-text('{name}')")
    assert jarvis.evaluate("__opened") == ["aide", "journal", "settings"]


def test_clock_in_french(jarvis):
    assert re.fullmatch(r"\d{1,2}\u00a0h(\u00a0\d{2})?", jarvis.text_content("#clock"))
    assert jarvis.get_attribute("#clock", "datetime")


# ---------------------------------------------------------------- display cards

def test_cards_eviction_time_and_announcement(jarvis):
    hud(jarvis, "addCard('Attention', 'garder', 'warning')")
    hud(jarvis, "addCard('Confirmation requise', 'Lancer ?', 'confirm', {id: 'k1'})")
    for i in range(9):
        hud(jarvis, f"addCard('Info {i}', 'x', 'info')")
    titles = jarvis.evaluate("[...document.querySelectorAll('#cards .card h3')].map(h => h.textContent)")
    # eight at most: the oldest info cards went, never the warning or the confirmation
    assert len(titles) == 8 and titles[-2:] == ["Confirmation requise", "Attention"]
    assert titles[:6] == [f"Info {i}" for i in range(8, 2, -1)]
    # the time is a <time> with a relative French label
    stamp = jarvis.locator("#cards .card").first.locator("time.meta")
    assert stamp.inner_text() == "à l'instant" and stamp.get_attribute("datetime")
    assert jarvis.evaluate("getComputedStyle(document.querySelector('#cards .meta')).color") == "rgb(122, 167, 194)"
    assert jarvis.text_content("#sr") == "Nouvelle carte\u202f: Info 8"
    # close buttons: real buttons, labelled with the card's title
    x = jarvis.locator("#cards .card").first.locator(".x")
    assert x.evaluate("e => e.tagName") == "BUTTON"
    assert x.get_attribute("aria-label") == "Fermer la carte «\u202fInfo 8\u202f»"
    x.focus()
    jarvis.keyboard.press("Enter")
    assert jarvis.locator("#cards .card").count() == 7
    assert jarvis.evaluate("document.activeElement.getAttribute('aria-label')") == "Fermer la carte «\u202fInfo 7\u202f»"


def test_tout_effacer_keeps_confirmations(jarvis):
    hud(jarvis, "addCard('Seule', 'x', 'info')")
    assert not jarvis.is_visible("#cards .clear")  # one card: no 'Tout effacer'
    hud(jarvis, "addCard('Confirmation requise', 'Lancer ?', 'confirm')")
    hud(jarvis, "addCard('Alerte', 'x', 'warning')")
    assert jarvis.is_visible("#cards .clear")
    assert jarvis.inner_text("#cards .clear") == "Tout effacer"
    jarvis.click("#cards .clear")
    assert jarvis.evaluate("[...document.querySelectorAll('#cards .card h3')].map(h => h.textContent)") == ["Confirmation requise"]
    assert not jarvis.is_visible("#cards .clear")


def test_info_and_result_cards_fade_after_ten_minutes(jarvis):
    hud(jarvis, "addCard('Info', 'x', 'info')")
    hud(jarvis, "addCard('Résultat', 'x', 'result')")
    hud(jarvis, "addCard('Alerte', 'x', 'warning')")
    hud(jarvis, "addCard('Épinglée', 'x', 'info', {sticky: true})")
    hud(jarvis, "sweepCards(Date.now() + 9 * 60e3)")
    assert jarvis.locator("#cards .card").count() == 4
    hud(jarvis, "sweepCards(Date.now() + 10 * 60e3 + 1000)")
    jarvis.wait_for_function("document.querySelectorAll('#cards .card').length === 2")
    assert jarvis.evaluate("[...document.querySelectorAll('#cards .card h3')].map(h => h.textContent)") == ["Épinglée", "Alerte"]


def test_code_card_scrolls_and_copies(jarvis):
    jarvis.context.grant_permissions(["clipboard-read", "clipboard-write"])
    code = "Get-ChildItem -Recurse -Filter *.xlsx | Sort-Object LastWriteTime -Descending | Select-Object -First 10"
    jarvis.evaluate("async c => (await import('/static/js/hud.js')).addCard('PowerShell', '```powershell\\n' + c + '\\n```', 'code', {id: 'code1'})", code)
    pre = jarvis.locator("#card-code1 pre")
    assert pre.inner_text() == code  # the fences are gone, the text is verbatim
    assert pre.evaluate("e => getComputedStyle(e).whiteSpace") == "pre"
    assert pre.evaluate("e => getComputedStyle(e).overflowX") == "auto"
    assert pre.get_attribute("tabindex") == "0"
    jarvis.click("#card-code1 .actions button:has-text('Copier')")
    jarvis.wait_for_selector(".toast:has-text('Copié')")
    assert jarvis.evaluate("navigator.clipboard.readText()") == code


def test_cards_have_no_backdrop_filter_and_animate_gently(jarvis):
    hud(jarvis, "addCard('Info', 'x', 'info')")
    card = "getComputedStyle(document.querySelector('#cards .card'))"
    assert jarvis.evaluate(f"{card}.backdropFilter") == "none"
    jarvis.evaluate("async () => (await import('/static/js/report.js')).showReport({title: 'Ventes'})")
    assert jarvis.evaluate("getComputedStyle(document.getElementById('report')).backdropFilter") == "none"
    jarvis.keyboard.press("Escape")
    assert jarvis.evaluate(f"{card}.animationName") == "card-in"
    assert jarvis.evaluate(f"{card}.animationDuration") == "0.18s"
    # the Animations setting turns every animation off
    jarvis.evaluate("__jarvis.settings.set('motion', 'reduced')")
    jarvis.wait_for_function("document.body.classList.contains('reduce-motion')")
    assert jarvis.evaluate(f"{card}.animationName") == "none"
    jarvis.evaluate("__jarvis.settings.set('motion', 'auto')")


def test_reduced_motion_has_no_keyframes(jarvis):
    jarvis.emulate_media(reduced_motion="reduce")
    hud(jarvis, "addCard('Info', 'x', 'info')")
    assert jarvis.evaluate("getComputedStyle(document.querySelector('#cards .card')).animationName") == "none"
