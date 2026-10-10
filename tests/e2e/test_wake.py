"""The wake word (WP07) in a real browser: on-device French recognition when
Chrome has it, Google's otherwise, a stricter match on the name, the
post-wake guard, and a refused microphone that pauses instead of switching
the wake word off for good. Speech recognition, permissions and the clock
are faked; everything else is the real page and server."""
import json

import pytest

from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER

pytestmark = pytest.mark.e2e

# On-device recognition as Chrome 139+ exposes it (static available/install),
# plus SpeechRecognitionPhrase (142+). window.__availability is what
# available() answers; install() records its calls and makes the pack available.
LOCAL_SR = r"""
window.__availCalls = []; window.__installs = [];
window.SpeechRecognition.available = async (o) => { __availCalls.push(o); return window.__availability || "available"; };
window.SpeechRecognition.install = async (o) => { __installs.push(o); window.__availability = "available"; return true; };
window.SpeechRecognitionPhrase = class { constructor(phrase, boost) { this.phrase = phrase; this.boost = boost; } };
"""

# navigator.permissions.query({name: 'microphone'}) answers window.__perm.
FAKE_PERMISSION = r"""
window.__perm = { state: "prompt", onchange: null };
const realQuery = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = async (d) => (d && d.name === "microphone") ? window.__perm : realQuery(d);
"""


def wait_ready(page):
    page.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")


@pytest.fixture
def open_page(context, app_server):
    """open_page(extra_script) -> a JARVIS page with the fakes (and extra ones)
    in place. Fails the test on any page error."""
    pytest.importorskip("pytest_playwright", reason="pip install -r requirements-dev.txt")
    context.set_default_timeout(10_000)
    context.route("https://api.openai.com/**", lambda route: route.fulfill(
        status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
    opened = []

    def open_(extra=""):
        page = context.new_page()
        page.add_init_script(FAKE_RTC + FAKE_SR + extra)
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(app_server.url)
        wait_ready(page)
        opened.append(errors)
        return page

    yield open_
    for errors in opened:
        assert not errors, f"erreurs dans la page : {errors}"


def listening(page):
    page.wait_for_function("window.__rec && __rec.running && __jarvis.state.mode === 'standby'")


def saved_settings(page):
    return json.loads(page.evaluate("localStorage.getItem('jarvis.settings') || '{}'"))

# ---------------------------------------------------------------- local or Google

def test_on_device_recognition_with_the_name_as_a_phrase(open_page):
    page = open_page("window.__availability = 'available';" + LOCAL_SR)
    listening(page)
    assert page.evaluate("__availCalls[0]") == {"langs": ["fr-FR"], "processLocally": True}
    assert page.evaluate("__rec.processLocally") is True
    assert page.evaluate("__rec.phrases.length") == 1
    assert page.evaluate("[__rec.phrases[0].phrase, __rec.phrases[0].boost]") == ["Jarvis", 8.0]
    assert page.evaluate("__jarvis.state.wakeEngine") == "local"
    # said once, in the status line (hud.js); #wakeEngine keeps what it doesn't say
    page.wait_for_function("document.getElementById('statusPill').textContent.endsWith(' · écoute locale')")
    assert page.text_content("#wakeEngine") == ""


def test_google_recognition_without_biasing_when_the_pack_is_unavailable(open_page):
    page = open_page("window.__availability = 'unavailable';" + LOCAL_SR)
    listening(page)
    assert page.evaluate("__rec.processLocally") is None
    assert page.evaluate("__rec.phrases") is None  # biasing only works on-device
    assert page.evaluate("__jarvis.state.wakeEngine") == "cloud"
    page.wait_for_function("document.getElementById('statusPill').textContent.endsWith(' · écoute via Google')")


def test_the_pack_installs_only_after_a_click(open_page):
    page = open_page("window.__availability = 'downloadable';" + LOCAL_SR)
    listening(page)
    page.wait_for_timeout(300)
    assert page.evaluate("__installs.length") == 0  # needs a click on the page first
    assert page.evaluate("__rec.processLocally") is None
    page.click("#orbBtn")
    page.wait_for_function("__installs.length === 1")
    assert page.evaluate("__installs[0]") == {"langs": ["fr-FR"], "processLocally": True}
    page.wait_for_selector(".card:has-text('Pack vocal français hors ligne installé.')")
    page.wait_for_function("__jarvis.state.mode === 'live'")
    page.click("#orbBtn")  # back to standby: listening on-device now
    page.wait_for_function("__jarvis.state.mode === 'standby' && __rec.running && __rec.processLocally === true")
    assert page.evaluate("__installs.length") == 1


@pytest.mark.parametrize("error", ["language-not-supported", "phrases-not-supported", "service-not-allowed"])
def test_an_on_device_failure_falls_back_to_google(open_page, error):
    page = open_page(LOCAL_SR)
    listening(page)
    assert page.evaluate("__rec.processLocally") is True
    count = page.evaluate("__recs.length")
    page.evaluate(f"__rec.onerror({{error: '{error}'}})")
    page.wait_for_function(f"__recs.length > {count} && __rec.running")
    assert page.evaluate("__rec.processLocally") is None
    assert page.evaluate("__rec.phrases") is None
    page.wait_for_function("document.getElementById('statusPill').textContent.endsWith(' · écoute via Google')")
    assert page.evaluate("__jarvis.state.mode") == "standby"
    assert saved_settings(page).get("wake") is not False  # not a refusal

# ---------------------------------------------------------------- hearing the name

def test_the_name_must_start_the_sentence(open_page):
    page = open_page()
    listening(page)
    page.evaluate("__say(\"j'ai parlé à Jarvis hier\", true)")
    page.evaluate("__say('bon alors Jarvis tu fais quoi', true)")
    page.wait_for_timeout(200)
    assert page.evaluate("__jarvis.state.mode") == "standby"
    assert page.evaluate("__sent.length") == 0

    page.evaluate("__say('Jarvis, ouvre Spotify', true)")
    page.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert page.evaluate("__texts()") == ["ouvre Spotify"]

    page.click("#orbBtn")
    listening(page)
    page.evaluate("__sent.length = 0; __say('dis Jarvis', true)")
    page.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert "Oui, monsieur" in page.evaluate("__texts()")[0]
    page.click("#orbBtn")
    listening(page)
    page.evaluate("__sent.length = 0; __say('OK Jarvis quelle heure est-il', true)")
    page.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert page.evaluate("__texts()") == ["quelle heure est-il"]


def test_the_command_is_shown_while_it_is_captured(open_page):
    page = open_page()
    listening(page)
    page.evaluate("window.__captions = []; __jarvis.bus.on('caption:user', c => __captions.push(c)); 0")
    page.evaluate("__say('Jarvis ouvre Spot', false)")
    page.wait_for_function("__captions.length === 1")
    assert page.evaluate("__captions[0]") == {"itemId": "wake", "text": "ouvre Spot", "final": False, "dim": True}


def go_live_by_name(page):
    """'Jarvis' alone, with the page's clock faked from now on."""
    page.clock.install()
    page.evaluate("__sent.length = 0; __say('Jarvis', true)")
    for _ in range(100):  # the session opens on real fetches and fake timers
        if page.evaluate("__jarvis.state.mode") == "live":
            break
        page.clock.run_for(100)
    assert page.evaluate("__jarvis.state.mode") == "live"
    page.wait_for_function("__sent.some(m => m.type === 'response.create')")


def test_a_wake_followed_by_silence_goes_back_to_sleep(open_page):
    page = open_page()
    listening(page)
    go_live_by_name(page)
    page.evaluate("""__emit({type: 'response.created'});  // « Oui, monsieur ? », then nothing
      __emit({type: 'response.done', response: {status: 'completed', output: []}});
      __emit({type: 'output_audio_buffer.stopped'})""")
    page.clock.run_for(7000)
    assert page.evaluate("__jarvis.state.mode") == "live"
    page.clock.run_for(1500)  # 8 s without monsieur speaking
    page.wait_for_function("__jarvis.state.mode === 'standby'")
    assert page.evaluate("__jarvis.settings.get('falseWakes')") == 1
    assert saved_settings(page)["falseWakes"] == 1


def test_a_wake_followed_by_speech_stays_awake(open_page):
    page = open_page()
    listening(page)
    go_live_by_name(page)
    page.clock.run_for(3000)
    page.evaluate("__jarvis.bus.emit('phase', {phase: 'user'})")  # speech_started (voice.js)
    page.clock.run_for(10000)
    assert page.evaluate("__jarvis.state.mode") == "live"
    assert page.evaluate("__jarvis.settings.get('falseWakes', 0)") == 0


def test_no_paid_session_from_the_wake_word_over_the_daily_cap(open_page):
    page = open_page()
    listening(page)
    page.evaluate("__jarvis.state.config.usage_capped = true")
    page.evaluate("__say('Jarvis, ouvre Spotify', true)")
    page.wait_for_selector(".toast:has-text('Plafond')")
    assert page.evaluate("__jarvis.state.mode") == "standby"
    assert page.evaluate("window.__pcs || 0") == 0
    page.click("#orbBtn")  # a click still works, after a confirmation (usage.js, WP18)
    page.click("#capDialog[open] button[value='ok']")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def test_only_the_leader_page_listens(open_page):
    page = open_page()
    listening(page)
    me = page.evaluate("async () => (await import('/static/js/sse.js')).clientId()")
    rec = page.evaluate("__recs.length")
    page.evaluate("__jarvis.bus.emit('server:leader', {type: 'leader', client: 'une-autre-page'})")
    page.wait_for_function("!__rec.running")
    assert page.inner_text("#wakeEngine") == "dans l'autre fenêtre"
    page.wait_for_function("document.getElementById('statusPill').textContent.endsWith(\" · écoute dans l'autre fenêtre\")")
    page.evaluate("__recs[__recs.length - 1].onresult({resultIndex: 0, results: [Object.assign([{transcript: 'Jarvis'}], {isFinal: true})]})")
    page.wait_for_timeout(200)
    assert page.evaluate("__jarvis.state.mode") == "standby"  # a stopped recognizer is not heard
    page.evaluate(f"__jarvis.bus.emit('server:leader', {{type: 'leader', client: '{me}'}})")
    page.wait_for_function(f"__recs.length > {rec} && __rec.running")

# ---------------------------------------------------------------- refusals and restarts

def test_an_expired_one_time_permission_pauses_without_switching_off(open_page):
    page = open_page(FAKE_PERMISSION)
    listening(page)
    page.evaluate("__rec.onerror({error: 'not-allowed'})")
    assert page.evaluate("__jarvis.state.mode") == "off"
    page.wait_for_selector(".card.warning:has-text(\"mot d'éveil en pause\")")
    page.wait_for_function("typeof __perm.onchange === 'function'")
    assert "wake" not in saved_settings(page)  # unchanged in localStorage
    assert page.inner_text("#wakeEngine") == "en pause (micro refusé)"
    assert page.get_attribute("#wakeBtn", "data-on") == "true"
    # Allowed again (the next prompt answered): listening again by itself.
    page.evaluate("__perm.state = 'granted'; __perm.onchange()")
    listening(page)
    assert page.locator(".card.warning:has-text(\"mot d'éveil en pause\")").count() == 0


def test_a_real_denial_switches_the_wake_word_off(open_page):
    page = open_page("window.__permState = 'denied';" + FAKE_PERMISSION.replace('state: "prompt"', "state: window.__permState"))
    listening(page)
    page.evaluate("__rec.onerror({error: 'not-allowed'})")
    page.wait_for_function("__jarvis.settings.get('wake') === false")
    assert saved_settings(page)["wake"] is False
    assert page.evaluate("__jarvis.state.mode") == "off"
    assert page.text_content("#wakeBtn") == "Mot d'éveil\u202f: désactivé"
    page.wait_for_selector(".card.warning:has-text(\"mot d'éveil désactivé\")")


def test_restart_after_sleep_or_a_network_change(open_page):
    page = open_page()
    listening(page)
    count = page.evaluate("__recs.length")
    page.evaluate("dispatchEvent(new Event('online'))")
    page.wait_for_function(f"__recs.length === {count + 1} && __rec.running")
    assert page.evaluate(f"__recs[{count - 1}].running") is False  # the old one was dropped
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")  # back on screen
    page.wait_for_function(f"__recs.length === {count + 2} && __rec.running")


def test_network_errors_back_off_and_a_result_resets(open_page):
    page = open_page()
    listening(page)
    page.clock.install()
    count = page.evaluate("__recs.length")
    page.evaluate("__rec.onerror({error: 'network'}); __rec.onend()")
    page.clock.run_for(500)
    assert page.evaluate("__recs.length") == count  # not at once: 600 ms
    page.clock.run_for(200)
    assert page.evaluate("__recs.length") == count + 1
    page.evaluate("__rec.onerror({error: 'network'}); __rec.onend()")
    page.clock.run_for(1100)
    assert page.evaluate("__recs.length") == count + 1  # 1.2 s now
    page.clock.run_for(200)
    assert page.evaluate("__recs.length") == count + 2
    page.evaluate("__say('rien à voir', true)")  # it hears again: back to a quick restart
    page.clock.run_for(20000)
    page.evaluate("__rec.onend()")
    page.clock.run_for(350)
    assert page.evaluate("__recs.length") == count + 3


def test_the_switch_lives_in_controls_extra_and_is_remembered(open_page):
    page = open_page()
    listening(page)
    toggle = "#controlsExtra #wakeBtn"
    assert page.text_content(toggle) == "Mot d'éveil\u202f: activé"
    assert page.get_attribute(toggle, "data-on") == "true"
    assert page.inner_text("#statusPill").endswith(" · écoute via Google")
    page.click(toggle)
    assert page.evaluate("__jarvis.state.mode") == "off"
    assert not page.evaluate("__rec.running")
    assert page.text_content(toggle) == "Mot d'éveil\u202f: désactivé"
    assert page.get_attribute(toggle, "data-on") == "false"
    page.reload()
    wait_ready(page)
    assert page.evaluate("__jarvis.state.mode") == "off"
    page.click(toggle)
    listening(page)
    page.click("#orbBtn")  # hidden while live (the live controls take over)
    page.wait_for_function("__jarvis.state.mode === 'live'")
    assert not page.is_visible("#wakeExtra")
