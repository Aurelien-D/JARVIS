"""Accessibility of the HUD (design spec §8-9, WP04): axe-core, the keyboard
map, the tab order, live regions, focus, targets and Windows contrast themes.

vendor/axe.min.js is axe-core 4.13.0 (MPL-2.0, Deque Systems), unmodified,
from https://registry.npmjs.org/axe-core/-/axe-core-4.13.0.tgz; its hash is
the one axe-core publishes in sri-history.json (checked below)."""
import base64
import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

AXE = Path(__file__).resolve().parent / "vendor" / "axe.min.js"
AXE_SRI = "sha256-wk8Je9L0UdT5M+i8fY1Tn4ZyouvLXMn58+7IypRwoME="


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def set_phase(page, phase, label=""):
    page.evaluate("([p, l]) => { __jarvis.state.phase = p; __jarvis.bus.emit('phase', {phase: p, label: l}); }",
                  [phase, label])


def ensure_composer(page):
    """The composer form is composer.js's (WP06); a stand-in with the spec's
    markup when that module does not render it yet."""
    page.evaluate("""() => {
      if (document.getElementById('askInput')) return;
      document.getElementById('composer').innerHTML = '<form id="ask"><input id="askInput" '
        + 'placeholder="Écrivez à JARVIS… (Ctrl+J)" aria-label="Message pour JARVIS" autocomplete="off">'
        + '<button type="submit" aria-label="Envoyer">↵</button></form>';
      document.getElementById('ask').addEventListener('submit', e => e.preventDefault());
    }""")


def add_cards(page):
    page.evaluate("""async () => {
      const hud = await import('/static/js/hud.js');
      hud.addCard('Météo Laon', '**14 °C**, averses éparses', 'result');
      hud.addCard('PowerShell', 'Get-ChildItem -Recurse | Sort-Object LastWriteTime -Descending | Select-Object -First 10', 'code');
      hud.addCard('Rappel', 'Appeler le garage', 'warning');
    }""")


def axe_violations(page):
    page.add_script_tag(path=str(AXE))
    return page.evaluate("""async () => {
      const r = await axe.run(document, {resultTypes: ['violations']});
      return r.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')
        .map(v => ({id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 5).map(n => n.target.join(' '))}));
    }""")


def test_vendored_axe_is_the_published_file():
    digest = base64.b64encode(hashlib.sha256(AXE.read_bytes()).digest()).decode()
    assert f"sha256-{digest}" == AXE_SRI
    assert AXE.read_text(encoding="utf-8").startswith("/*! axe v4.13.0")


# ---------------------------------------------------------------- axe-core

@pytest.mark.parametrize("state", ["off", "standby", "live", "live-busy"])
def test_axe_has_no_serious_violation(jarvis, state):
    ensure_composer(jarvis)
    if state == "off":
        jarvis.click("#wakeBtn")
        jarvis.wait_for_function("__jarvis.state.mode === 'off'")
    elif state.startswith("live"):
        go_live(jarvis)
    if state == "live-busy":
        add_cards(jarvis)
        set_phase(jarvis, "speaking")
        jarvis.evaluate("""() => {
          __jarvis.bus.emit('caption:user', {itemId: 'u1', text: 'Quel temps fait-il ?', final: true});
          __jarvis.bus.emit('caption:jarvis', {itemId: 'a1', text: 'Il fait beau, monsieur.', final: true});
          __jarvis.bus.emit('error', {kind: 'lost', message: 'perdu'});
        }""")
    jarvis.wait_for_timeout(300)  # card entrance finished: axe reads final colours
    assert axe_violations(jarvis) == []


def test_axe_harness_catches_a_planted_violation(jarvis):
    """The checks above are not vacuous: an unlabelled button is reported."""
    jarvis.evaluate("document.getElementById('chips').append(document.createElement('button'))")
    assert [v["id"] for v in axe_violations(jarvis)] == ["button-name"]


def test_axe_with_the_side_panel_open_on_a_small_window(jarvis):
    jarvis.set_viewport_size({"width": 1024, "height": 700})
    add_cards(jarvis)
    jarvis.click("#panelBtn")
    jarvis.wait_for_timeout(300)
    assert axe_violations(jarvis) == []


# ---------------------------------------------------------------- semantics

def test_live_regions_and_landmarks(jarvis):
    attrs = jarvis.evaluate("""(() => {
      const a = (id, n) => document.getElementById(id).getAttribute(n);
      return {pill: a('statusPill', 'role'), pillLive: a('statusPill', 'aria-live'), sr: a('sr', 'aria-live'),
              alert: a('srAlert', 'role'), toasts: a('toasts', 'aria-live'), caption: a('caption', 'aria-hidden'),
              orb: a('orb', 'aria-hidden'), cards: a('cards', 'aria-label'), side: a('side', 'aria-label'),
              lang: document.documentElement.lang};
    })()""")
    assert attrs == {"pill": "status", "pillLive": "polite", "sr": "polite", "alert": "alert", "toasts": "polite",
                     "caption": "true", "orb": "true", "cards": "Affichages de JARVIS",
                     "side": "Panneau latéral", "lang": "fr"}
    assert jarvis.get_attribute(".skip", "href") == "#askInput"
    assert jarvis.inner_text(".skip") == "Aller au champ de message"


def test_close_controls_are_labelled_buttons(jarvis):
    add_cards(jarvis)
    jarvis.evaluate("__jarvis.bus.emit('error', {kind: 'lost', message: 'perdu'})")
    jarvis.evaluate("async () => (await import('/static/js/report.js')).showReport({title: 'Ventes'})")
    closers = jarvis.evaluate("""[...document.querySelectorAll('#cards .x, #statusActions .x, #report .x, #toasts .x')]
      .map(e => [e.tagName, (e.getAttribute('aria-label') || '').trim()])""")
    assert len(closers) == 5  # three cards, the error, the report
    for tag, label in closers:
        assert tag == "BUTTON" and label
    jarvis.click("#report .x")
    assert not jarvis.evaluate("document.getElementById('report').open")


def test_targets_are_at_least_24px(jarvis):
    ensure_composer(jarvis)
    add_cards(jarvis)
    go_live(jarvis)
    # (the composer's own buttons are composer.js's, WP06)
    small = jarvis.evaluate("""[...document.querySelectorAll('#topbar button, #stage button, #cards button')]
      .filter(b => b.checkVisibility() && !b.closest('#composer'))
      .map(b => [b.id || b.className || b.textContent, b.getBoundingClientRect()])
      .filter(([, r]) => r.width < 24 || r.height < 24).map(([n]) => n)""")
    assert small == []


# ---------------------------------------------------------------- focus

def test_tab_order_reaches_the_orb(jarvis):
    ensure_composer(jarvis)
    add_cards(jarvis)
    go_live(jarvis)
    jarvis.focus(".skip")  # from the top of the page
    order = ["skip"]
    for _ in range(40):
        jarvis.keyboard.press("Tab")
        order.append(jarvis.evaluate("""(() => { const e = document.activeElement;
          return e.id || (e.closest('#topActions') && 'top') || (e.closest('#cards') && 'cards')
            || (e.closest('#controls') && 'controls') || e.className || e.tagName; })()"""))
        if order[-1] == "sleepBtn":
            break
    assert order[0] == "skip"
    assert "orbBtn" in order
    first = {name: order.index(name) for name in ["skip", "top", "cards", "orbBtn", "askInput", "micBtn"]}
    # skip link → top bar → cards → orb → composer → controls (design spec §9)
    assert list(first.values()) == sorted(first.values()), order


def test_focus_ring_on_the_orb(jarvis):
    jarvis.evaluate("document.activeElement.blur()")
    for _ in range(12):
        jarvis.keyboard.press("Tab")
        if jarvis.evaluate("document.activeElement.id") == "orbBtn":
            break
    style = jarvis.evaluate("""(() => { const s = getComputedStyle(document.getElementById('orbBtn'));
      return [s.outlineStyle, s.outlineColor, s.outlineOffset, s.borderRadius]; })()""")
    assert style == ["solid", "rgb(64, 220, 255)", "-12px", "50%"]


# ---------------------------------------------------------------- the keyboard map

def test_ctrl_j_and_slash_focus_the_composer(jarvis):
    ensure_composer(jarvis)
    jarvis.evaluate("document.activeElement.blur()")
    jarvis.keyboard.press("Control+j")
    assert jarvis.evaluate("document.activeElement.id") == "askInput"
    jarvis.evaluate("document.activeElement.blur()")
    jarvis.keyboard.press("/")
    assert jarvis.evaluate("document.activeElement.id") == "askInput"
    assert jarvis.input_value("#askInput") == ""  # the '/' is not typed
    # in a field, '/' and '?' are just characters
    jarvis.keyboard.type("a/b?")
    assert jarvis.input_value("#askInput") == "a/b?"


def test_question_mark_opens_help(jarvis):
    jarvis.evaluate("window.__opened = []; __jarvis.bus.on('ui:open', n => __opened.push(n)); 0")
    jarvis.evaluate("document.activeElement.blur()")
    jarvis.keyboard.press("?")
    assert jarvis.evaluate("__opened") == ["aide"]


def test_escape_closes_the_report_then_interrupts(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__emit({type: 'response.created'})")
    set_phase(jarvis, "speaking")
    jarvis.evaluate("async () => (await import('/static/js/report.js')).showReport({title: 'Ventes'})")
    jarvis.evaluate("__sent.length = 0")
    jarvis.keyboard.press("Escape")  # 1) the topmost open layer
    assert not jarvis.evaluate("document.getElementById('report').open")
    assert jarvis.evaluate("__types()") == []
    jarvis.keyboard.press("Escape")  # 2) JARVIS speaking: interrupt
    assert jarvis.evaluate("__types()")[:2] == ["response.cancel", "output_audio_buffer.clear"]


def test_escape_interrupts_a_tool_but_not_listening(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__sent.length = 0")
    jarvis.keyboard.press("Escape")  # 3) nothing to close or interrupt
    assert jarvis.evaluate("__types()") == []
    jarvis.evaluate("__emit({type: 'response.created'})")
    set_phase(jarvis, "tool", "Je regarde l'écran…")
    jarvis.keyboard.press("Escape")
    assert "response.cancel" in jarvis.evaluate("__types()")


def test_escape_closes_the_most_recent_layer_first(jarvis):
    jarvis.set_viewport_size({"width": 1024, "height": 700})
    jarvis.evaluate("async () => (await import('/static/js/report.js')).showReport({title: 'Ventes'})")
    jarvis.click("#panelBtn")  # opened after the report: closed first
    jarvis.keyboard.press("Escape")
    assert not jarvis.evaluate("document.body.classList.contains('side-open')")
    assert jarvis.evaluate("document.getElementById('report').open")
    jarvis.keyboard.press("Escape")
    assert not jarvis.evaluate("document.getElementById('report').open")
    # a drawer (the Journal) closes too, and says so
    jarvis.evaluate("""window.__closed = []; __jarvis.bus.on('ui:close', n => __closed.push(n));
      document.getElementById('journalDrawer').hidden = false; 0""")
    jarvis.keyboard.press("Escape")
    assert jarvis.evaluate("document.getElementById('journalDrawer').hidden")
    assert jarvis.evaluate("__closed") == ["journalDrawer"]


def test_ctrl_m_mutes_even_from_the_composer(jarvis):
    ensure_composer(jarvis)
    go_live(jarvis)
    jarvis.keyboard.press("Control+m")
    assert jarvis.get_attribute("#micBtn", "aria-pressed") == "true"
    assert jarvis.inner_text("#statusPill").startswith("Micro coupé")
    assert jarvis.evaluate("__jarvis.state.muted") is True
    jarvis.focus("#askInput")
    jarvis.keyboard.press("Control+m")
    assert jarvis.get_attribute("#micBtn", "aria-pressed") == "false"
    assert jarvis.input_value("#askInput") == ""


def test_space_toggles_the_orb_only_from_the_page_or_the_orb(jarvis):
    ensure_composer(jarvis)
    jarvis.focus("#askInput")
    jarvis.keyboard.press(" ")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"  # typed, not a shortcut
    jarvis.focus("#wakeBtn")
    jarvis.keyboard.press(" ")  # a button keeps its own Space
    assert jarvis.evaluate("__jarvis.state.mode") == "off"
    jarvis.focus("#wakeBtn")
    jarvis.keyboard.press(" ")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"
    jarvis.evaluate("document.activeElement.blur()")
    jarvis.keyboard.press(" ")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    jarvis.focus("#orbBtn")
    jarvis.keyboard.press(" ")  # once, not twice (no double toggle)
    jarvis.wait_for_function("__jarvis.state.mode === 'standby'")
    jarvis.wait_for_timeout(300)
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"


def test_held_space_talks_when_push_to_talk_is_on(jarvis):
    """'Maintenir Espace pour parler': Space goes to voice.pttDown/pttUp (WP02)
    instead of toggling the orb."""
    jarvis.evaluate("__jarvis.settings.set('ptt', true)")
    go_live(jarvis)
    jarvis.evaluate("document.activeElement.blur()")
    jarvis.keyboard.down(" ")
    jarvis.keyboard.down(" ")  # auto-repeat: still one press
    assert jarvis.evaluate("__jarvis.state.mode") == "live"  # held Space does not toggle the orb
    jarvis.keyboard.up(" ")
    assert jarvis.evaluate("__jarvis.state.mode") == "live"
    jarvis.evaluate("__jarvis.settings.set('ptt', false)")


# ---------------------------------------------------------------- Windows contrast themes

def test_forced_colors_keep_the_state_readable(jarvis):
    jarvis.emulate_media(forced_colors="active")
    pill = jarvis.evaluate("""(() => { const p = document.getElementById('statusPill'), s = getComputedStyle(p);
      return {text: p.innerText.trim(), visible: p.checkVisibility(), color: s.color, opacity: s.opacity}; })()""")
    assert pill["text"].startswith("En veille") and pill["visible"]
    assert pill["color"] not in ("rgba(0, 0, 0, 0)", "transparent") and pill["opacity"] == "1"
    assert jarvis.evaluate("getComputedStyle(document.getElementById('orb')).display") == "none"
    label = jarvis.evaluate("getComputedStyle(document.getElementById('orbBtn'), '::after').content")
    assert label == '"En veille"'
    go_live(jarvis)
    assert jarvis.evaluate("getComputedStyle(document.getElementById('orbBtn'), '::after').content") == '"Je vous écoute…"'
