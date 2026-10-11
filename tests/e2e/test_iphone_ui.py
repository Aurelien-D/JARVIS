"""The iPhone polish (spec 7) on the paired iPhone's page: remote_page under the
real gate, with the iPhone's user agent, touch and mobile emulation (390 x 844
unless a test says otherwise). Fields at 16 px (iOS zooms into smaller ones),
44 px targets, no keyboard words, iOS's audio session set before the
microphone, the screen kept on while live, a locked screen that pauses the
conversation, a stream reopened when the page comes back, and pending
confirmations first in the bottom sheet."""
import json

import pytest
from conftest import wait_ready

pytestmark = pytest.mark.e2e

NNBSP = "\u202f"
PAUSED = "Conversation en pause (écran verrouillé ou autre app)."
MIC_BLOCKED = ("Micro refusé : réessayez et autorisez le micro quand iOS le demande. "
               "S'il ne le demande plus : Réglages de l'iPhone › Apps › Safari › Micro.")

# What iOS has and Chromium has not, recorded in window.__order:
# navigator.audioSession (Safari 17+) and the moment the microphone opens;
# a muted play() of an <audio> element (voice.primeAudio).
AUDIO_SPIES = r"""
window.__order = [];
(() => {
  let type = "auto";
  Object.defineProperty(navigator, "audioSession", { configurable: true, value: {
    get type() { return type; },
    set type(v) { __order.push("audioSession:" + v); type = v; },
  } });
  const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = (c) => {
    __order.push("getUserMedia");
    if (window.__denyMic) return Promise.reject(new DOMException("refusé", "NotAllowedError"));
    return gum(c);
  };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    __order.push(this.muted ? "play:muted" : "play");
    return play.call(this);
  };
})();
"""

# Screen Wake Lock: every sentinel asked for, and whether it was released.
WAKE_LOCK = r"""
window.__locks = [];
Object.defineProperty(navigator, "wakeLock", { configurable: true, value: {
  request(type) {
    const s = { type, released: false, addEventListener() {},
                release() { s.released = true; return Promise.resolve(); } };
    __locks.push(s);
    return Promise.resolve(s);
  },
} });
"""

# The microphone stream voice.js got (window.__mic) and every AudioContext resume().
MIC_SPY = r"""
window.__resumes = 0;
(() => {
  const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    const stream = await gum(c);
    if (c && c.audio) window.__mic = stream;
    return stream;
  };
  const resume = AudioContext.prototype.resume;
  AudioContext.prototype.resume = function () { window.__resumes++; return resume.call(this); };
})();
"""

# Every EventSource the page opens.
STREAMS = r"""
window.__streams = 0;
window.EventSource = class extends window.EventSource {
  constructor(...args) { super(...args); window.__streams++; window.__stream = this; }
};
"""

HIDE = """() => {
  for (const [k, v] of [['visibilityState', 'hidden'], ['hidden', true]])
    Object.defineProperty(document, k, {configurable: true, get: () => v});
  document.dispatchEvent(new Event('visibilitychange'));
}"""
SHOW = """() => {
  delete document.visibilityState; delete document.hidden;
  document.dispatchEvent(new Event('visibilitychange'));
}"""


def with_scripts(page, *scripts):
    """The phone page reloaded with these init scripts (before its own code)."""
    page.add_init_script("\n".join(scripts))
    page.reload()
    wait_ready(page)
    return page


def go_live(page):
    page.locator("#orbBtn").tap()
    page.wait_for_function("__jarvis.state.mode === 'live'")


def plain(text):
    return text.replace(NNBSP, " ").replace("\u00a0", " ")


def status(page):
    return plain(page.inner_text("#statusPill"))


def call(page, name, call_id, args):
    """The voice model calls a tool in this page's session."""
    page.evaluate("ev => __emit(ev)", {"type": "response.done", "response": {"output": [
        {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(args)}]}})


def output(page, call_id):
    page.wait_for_function(f"__sent.some(m => m.item && m.item.call_id === '{call_id}')")
    return json.loads(page.evaluate(f"__sent.find(m => m.item && m.item.call_id === '{call_id}').item.output"))


# Visible controls under `scope` smaller than 44 x 44 px (half a pixel of rounding allowed).
SMALL = """(scope) => [...document.querySelectorAll(scope)]
  .filter(b => b.checkVisibility())
  .map(b => [b.id || b.className || b.textContent.trim(), b.getBoundingClientRect()])
  .filter(([, r]) => r.width < 43.5 || r.height < 43.5)
  .map(([n, r]) => `${n} ${Math.round(r.width)}x${Math.round(r.height)}`)"""


@pytest.fixture
def nothing_real(monkeypatch):
    """Nothing changes the volume on this machine; no pending request from another test."""
    from jarvis import confirm, desktop
    confirm.PENDING.clear()
    ran = []
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: ran.append(action) or {"ok": True})
    yield ran
    confirm.PENDING.clear()


# ---------------------------------------------------------------- the page itself

def test_the_page_reaches_under_the_notch_on_its_own_background(remote_page):
    page = remote_page
    assert "viewport-fit=cover" in page.get_attribute('meta[name="viewport"]', "content")
    colours = page.evaluate("[document.documentElement, document.body].map(e => getComputedStyle(e).backgroundColor)")
    assert "rgba(0, 0, 0, 0)" not in colours, colours


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair"}], ids=["pair"], indirect=True)
def test_the_pairing_page_reaches_under_the_notch_too(remote_page):
    page = remote_page
    page.wait_for_selector("#pairBody button")
    assert "viewport-fit=cover" in page.get_attribute('meta[name="viewport"]', "content")
    colours = page.evaluate("[document.documentElement, document.body].map(e => getComputedStyle(e).backgroundColor)")
    assert colours == ["rgb(5, 8, 13)", "rgb(5, 8, 13)"]


# ---------------------------------------------------------------- fields and targets

def test_fields_are_16px_so_ios_never_zooms(remote_page):
    page = remote_page
    assert page.evaluate("getComputedStyle(document.getElementById('askInput')).fontSize") == "16px"
    # Every kind of field the page draws (Réglages, the side panel's edit form), whatever its own size.
    sizes = page.evaluate("""() => {
      const box = document.createElement('div');
      box.innerHTML = '<input><select><option>a</option></select><textarea></textarea>'
        + '<input class="set-input mono"><form class="edit-form"><input type="text"></form>';
      document.getElementById('side').append(box);
      const out = [...box.querySelectorAll('input, select, textarea')].map(e => getComputedStyle(e).fontSize);
      box.remove();
      return out;
    }""")
    assert sizes == ["16px"] * 5
    page.locator("#topActions button", has_text="Réglages").tap()
    page.locator("#settingsDialog .set-tab", has_text="Proactivité").tap()  # the phone opens on Accès à distance
    page.wait_for_selector("#settingsDialog .set-input")
    assert set(page.evaluate("[...document.querySelectorAll('#settingsDialog .set-input')]"
                             ".filter(e => e.checkVisibility()).map(e => getComputedStyle(e).fontSize)")) == {"16px"}


def test_every_control_is_at_least_44px(remote_page, nothing_real):
    page = remote_page
    page.evaluate("""async () => {
      const hud = await import('/static/js/hud.js');
      hud.addCard('Météo Nantes', '**14 °C**, averses éparses', 'result');
      hud.addCard('Commande', 'Get-ChildItem', 'code');
    }""")
    go_live(page)
    call(page, "system_control", "v1", {"action": "volume_up"})  # a confirmation card, [Lancer] [Annuler]
    page.wait_for_selector(".card.confirm[data-state='pending']")
    page.locator("#cards .more").tap()  # every card in the sheet
    assert page.evaluate(SMALL, "#topbar button, #stage button, #cards button") == []
    # The side panel, over the stage.
    page.locator("#panelBtn").tap()
    page.wait_for_function("document.body.classList.contains('side-open')")
    assert page.evaluate(SMALL, "#side button") == []
    page.locator("#side .side-close").tap()
    # Réglages: its tabs and every button of the phone's sections.
    page.locator("#topActions button", has_text="Réglages").tap()
    page.wait_for_selector("#settingsDialog .set-tab")
    for tab in page.locator("#settingsDialog .set-tab").all():
        tab.tap()
        page.wait_for_timeout(150)
        assert page.evaluate(SMALL, "#settingsDialog button") == [], tab.inner_text()


def test_no_keyboard_words_on_a_touch_screen(remote_page):
    page = remote_page

    def keyboard_words():
        shown = page.evaluate("""() => document.body.innerText + ' '
          + [...document.querySelectorAll('[placeholder]')].map(e => e.placeholder).join(' ')""")
        return [w for w in ("Espace", "Ctrl+J", "Ctrl+M", "Échap") if w in plain(shown)]

    assert status(page) == "En veille · touchez l'orbe pour parler"
    assert page.get_attribute("#askInput", "placeholder") == "Écrivez à JARVIS…"
    assert keyboard_words() == []
    # Aide: the touch commands instead of the keyboard map.
    page.locator("#topActions button", has_text="Aide").tap()
    page.wait_for_selector("#card-aide .aide-keys li")
    keys = page.locator("#card-aide .aide-keys")
    assert keys.get_attribute("aria-label") == "Commandes tactiles"
    assert plain(keys.locator("li").first.inner_text()) == "Orbe : parler ou mettre en veille"
    assert keyboard_words() == []
    go_live(page)
    page.evaluate("([p]) => { __jarvis.state.phase = p; __jarvis.bus.emit('phase', {phase: p, label: ''}); }",
                  ["speaking"])
    assert status(page) == "JARVIS répond · parlez ou touchez Interrompre"
    page.evaluate("__jarvis.voice.setMuted(true)")
    page.evaluate("([p]) => { __jarvis.state.phase = p; __jarvis.bus.emit('phase', {phase: p, label: ''}); }",
                  ["listening"])
    assert status(page) == "Micro coupé · touchez « Micro » pour le réactiver"
    assert keyboard_words() == []


PC_ONLY = ("Jarvis, ouvre Spotify sur l'écran de gauche", "Regarde mon écran : tu vois l'erreur ?")


def test_aide_and_the_chips_offer_only_what_the_iphone_can_do(remote_page, jarvis):
    """open_app and look_at_screen stay on the PC (tools.py): on the iPhone, Aide,
    the standby chips and « Essayez : … » never offer them, and its touch list is
    titled « Commandes tactiles » on screen; the PC keeps them, with no title."""
    page = remote_page
    page.locator("#topActions button", has_text="Aide").tap()
    page.wait_for_selector("#card-aide .aide-ex")
    offered = [plain(t) for t in page.locator("#card-aide .aide-ex").all_inner_texts()]
    assert offered and not set(PC_ONLY) & set(offered)
    assert "Regarde-moi avec la caméra : je suis bien coiffé ?" in offered  # the iPhone's own camera
    assert page.locator("#card-aide .aide-keys-title").inner_text().strip().lower() == "commandes tactiles"
    shown = plain(page.inner_text("#chips") + " " + page.inner_text("#hint"))
    assert not [e for e in PC_ONLY if e in shown]
    jarvis.evaluate("__jarvis.bus.emit('ui:open', 'aide')")
    jarvis.wait_for_selector("#card-aide .aide-ex")
    assert set(PC_ONLY) <= {plain(t) for t in jarvis.locator("#card-aide .aide-ex").all_inner_texts()}
    assert jarvis.locator("#card-aide .aide-keys-title").count() == 0


def test_the_pc_page_keeps_its_keyboard_words(jarvis):
    assert jarvis.get_attribute("#askInput", "placeholder") == "Écrivez à JARVIS… (Ctrl+J)"
    jarvis.evaluate("__jarvis.bus.emit('ui:open', 'aide')")
    jarvis.wait_for_selector("#card-aide .aide-keys li")
    assert plain(jarvis.locator("#card-aide .aide-keys li").first.inner_text()) == "Espace : parler"


# ---------------------------------------------------------------- iOS audio

def test_ios_audio_session_is_set_before_the_microphone_opens(remote_page):
    page = with_scripts(remote_page, AUDIO_SPIES)
    go_live(page)
    order = page.evaluate("__order")
    assert "audioSession:play-and-record" in order and "getUserMedia" in order
    assert order.index("audioSession:play-and-record") < order.index("getUserMedia")
    # The shared <audio> was played once, muted, inside the tap (before the
    # microphone was even asked): iOS then lets JARVIS's voice play in it.
    assert order.index("play:muted") < order.index("getUserMedia")
    assert page.evaluate("__jarvis.state.mode") == "live"


def test_a_microphone_taken_by_ios_is_said_until_it_comes_back(remote_page):
    page = with_scripts(remote_page, MIC_SPY)
    go_live(page)
    # iOS suspends the sound while a call or Siri holds the microphone.
    page.evaluate("async () => { await (await import('/static/js/audio-fx.js')).audio().suspend(); __resumes = 0; }")
    track = "__mic.getAudioTracks()[0]"
    page.evaluate(f"{track}.dispatchEvent(new Event('mute'))")
    banner = page.locator("#iosBanner")
    assert banner.is_visible() and banner.get_attribute("data-kind") == "mic"
    assert plain(banner.inner_text()).startswith("Micro interrompu (appel, Siri ou autre app)")
    assert not page.locator("#iosBanner button.primary").is_visible()  # nothing to tap: iOS gives it back
    page.evaluate(f"{track}.dispatchEvent(new Event('unmute'))")
    assert not banner.is_visible()
    assert page.evaluate("__resumes") >= 1  # the sound starts again
    assert page.evaluate("__jarvis.state.mode") == "live"


# ---------------------------------------------------------------- the screen, the lock, the way back

def test_the_screen_stays_on_while_live_and_only_then(remote_page):
    page = with_scripts(remote_page, WAKE_LOCK)
    assert page.evaluate("__locks.length") == 0  # standby: the screen may sleep
    go_live(page)
    page.wait_for_function("__locks.length === 1")
    assert page.evaluate("[__locks[0].type, __locks[0].released]") == ["screen", False]
    page.locator("#orbBtn").tap()  # back to standby
    page.wait_for_function("__jarvis.state.mode !== 'live'")
    page.wait_for_function("__locks[0].released")
    go_live(page)
    page.wait_for_function("__locks.length === 2 && !__locks[1].released")
    page.locator("#sleepBtn").tap()
    page.wait_for_function("__locks[1].released")


def test_a_locked_screen_pauses_the_conversation_and_coming_back_resumes_it(remote_page):
    page = with_scripts(remote_page, WAKE_LOCK, AUDIO_SPIES)
    go_live(page)
    page.wait_for_function("__locks.length === 1")
    page.evaluate(HIDE)
    # Paused: no session left open, the screen let go, and the banner says why.
    page.wait_for_function("__jarvis.state.mode !== 'live' && !__jarvis.state.wantLive")
    assert page.evaluate("__locks[0].released")
    banner = page.locator("#iosBanner")
    assert banner.get_attribute("data-kind") == "paused" and not banner.get_attribute("hidden")
    assert plain(banner.locator(".ios-banner-text").inner_text()) == PAUSED
    sessions = page.evaluate("__pcs")
    # Back on screen: the conversation reopens by itself (a reconnection, the
    # recent exchanges carried over), the banner goes, the screen stays on again.
    page.evaluate(SHOW)
    page.wait_for_function("__jarvis.state.mode === 'live'")
    assert page.evaluate("__pcs") == sessions + 1
    assert not banner.is_visible()
    page.wait_for_function("__locks.length === 2 && !__locks[1].released")


def test_a_microphone_refused_on_the_way_back_offers_reprendre(remote_page):
    page = with_scripts(remote_page, AUDIO_SPIES)
    go_live(page)
    page.evaluate(HIDE)
    page.wait_for_function("__jarvis.state.mode !== 'live'")
    page.evaluate("window.__denyMic = true")  # iOS asks for the microphone again, without a tap: refused
    page.evaluate(SHOW)
    resume = page.locator("#iosBanner button.primary")
    resume.wait_for()
    assert resume.inner_text() == "Reprendre"
    # The banner says why, in the iPhone's words (no address bar, no Windows),
    # and [Reprendre] is the only action: no second [Réessayer] on the status line.
    assert plain(page.inner_text("#iosBanner .ios-banner-text")) == MIC_BLOCKED
    assert "Erreur" not in status(page) and not page.locator("#statusActions button").count()
    for word in ("cadenas", "Windows", "barre d'adresse", "Réessayer"):
        assert word not in page.inner_text("#stage"), word
    assert page.evaluate("__jarvis.state.mode") != "live"
    # A tap: iOS lets the microphone open, the conversation goes on.
    page.evaluate("window.__denyMic = false")
    resume.tap()
    page.wait_for_function("__jarvis.state.mode === 'live'")
    assert not page.locator("#iosBanner").is_visible()
    assert "Erreur" not in status(page)
    # Refused on a tap of the orb: the status line says it in the iPhone's words too.
    page.locator("#orbBtn").tap()
    page.wait_for_function("__jarvis.state.mode !== 'live'")
    page.evaluate("window.__denyMic = true")
    page.locator("#orbBtn").tap()
    page.wait_for_function("document.querySelector('#statusPill').textContent.includes('Micro refusé')")
    assert "Windows" not in status(page) and "cadenas" not in status(page)


def test_a_long_pause_waits_for_a_tap(remote_page):
    page = remote_page
    go_live(page)
    page.evaluate(HIDE)
    page.wait_for_function("__jarvis.state.mode !== 'live'")
    sessions = page.evaluate("__pcs")
    # Away longer than the idle timeout (3 min): no new paid session opens by itself.
    page.evaluate("""() => { const real = Date.now; Date.now = () => real() + 4 * 60e3;
      try { delete document.visibilityState; delete document.hidden;
            document.dispatchEvent(new Event('visibilitychange')); } finally { Date.now = real; } }""")
    page.locator("#iosBanner button.primary").wait_for()
    page.wait_for_timeout(300)
    assert page.evaluate("__pcs") == sessions and page.evaluate("__jarvis.state.mode") != "live"
    page.locator("#iosBanner button.primary").tap()
    page.wait_for_function("__jarvis.state.mode === 'live'")


def test_a_silent_stream_is_reopened_when_the_page_comes_back(remote_page):
    page = with_scripts(remote_page, STREAMS)
    assert page.evaluate("__streams") == 1
    # Back after a short moment: the stream spoke less than 30 s ago (pings every 15 s), kept.
    page.evaluate(HIDE)
    page.evaluate(SHOW)
    assert page.evaluate("__streams") == 1
    # Back after iOS froze the page: nothing heard for more than 30 s, a new stream at once.
    page.evaluate(HIDE)
    page.evaluate("""() => { const real = Date.now; Date.now = () => real() + 31e3;
      try { delete document.visibilityState; delete document.hidden;
            document.dispatchEvent(new Event('visibilitychange')); } finally { Date.now = real; } }""")
    assert page.evaluate("__streams") == 2
    page.wait_for_function("__stream.readyState === EventSource.OPEN && __jarvis.state.synced")
    # Back from the back/forward cache, the same (62 s: the stream above was
    # opened while the clock was 31 s ahead).
    page.evaluate("""() => { const real = Date.now; Date.now = () => real() + 62e3;
      try { dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})); } finally { Date.now = real; } }""")
    assert page.evaluate("__streams") == 3


# ---------------------------------------------------------------- the bottom sheet

def test_pending_confirmation_comes_first_in_the_sheet(remote_page, nothing_real):
    page = remote_page
    go_live(page)
    call(page, "display_card", "c1", {"title": "Météo Nantes", "content": "14 °C", "kind": "result"})
    call(page, "system_control", "v1", {"action": "volume_up"})
    assert output(page, "v1")["status"] == "needs_confirmation"
    page.wait_for_selector(".card.confirm[data-state='pending']")
    # Newer cards come after it: the sheet's first (and only shown) card is the question.
    call(page, "display_card", "c2", {"title": "Rappel", "content": "Appeler le garage", "kind": "info"})
    page.wait_for_function("document.querySelectorAll('#cards .card').length === 3")
    first = page.locator("#cards .cards-list > .card").first
    assert "confirm" in first.get_attribute("class").split() and first.get_attribute("data-state") == "pending"
    shown = page.evaluate("[...document.querySelectorAll('#cards .card')].filter(c => c.checkVisibility())"
                          ".map(c => c.querySelector('h3').textContent)")
    assert shown == [first.locator("h3").inner_text()]
    assert page.evaluate("[...document.querySelectorAll('#cards .card h3')].map(h => h.textContent)")[1:] \
        == ["Rappel", "Météo Nantes"]
    # Its [Lancer] is in the window, uncovered, and works from there.
    lancer = first.locator(".actions button.primary")
    box = lancer.bounding_box()
    assert box["y"] + box["height"] <= page.viewport_size["height"] and box["height"] >= 44
    assert page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('button')?.textContent",
                         [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]) == "Lancer"
    lancer.tap()
    page.wait_for_selector(".card[data-state='done']")
    assert nothing_real == ["volume_up"]
