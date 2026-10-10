"""Remote access protections in a real browser (spec 4.12, section 8 rows 27,
27b and 28): the PC page and the paired iPhone side by side.

- P0-27: the PC never speaks, chimes, notifies or acknowledges a phone's or
  Siri's result (it stays in the task panel); the phone ignores the PC's busy
  screen.
- P0-27b: a remote alert reaches the PC page as plain text: no element, no link.
- P0-28: the wake word is off on the phone and on any iOS page, and a
  recognizer is stopped before the voice session takes the microphone.

The phone is remote_page (real gate); speech synthesis, notifications
and earcons are faked as in test_delivery. Fictitious devices only."""
import time

import pytest
from conftest import phone_origin
from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER

pytestmark = pytest.mark.e2e

SIRI = "siri:k_e2e0000000000001"
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1")

# The browser's voice, notifications (permission in window.__notifPerm) and the
# earcons' tones, recorded (as in test_delivery).
SPIES = r"""
window.__spoken = [];
const fakeTTS = {
  speaking: false, pending: false, paused: false, onvoiceschanged: null,
  getVoices: () => [], cancel() {}, pause() {}, resume() {},
  addEventListener() {}, removeEventListener() {},
  speak(u) {
    __spoken.push(u.text);
    setTimeout(() => { u.onstart && u.onstart(); setTimeout(() => u.onend && u.onend(), 30); }, 10);
  },
};
Object.defineProperty(window, "speechSynthesis", { value: fakeTTS, configurable: true });
window.__notes = []; window.__notifAsked = 0;
window.Notification = class {
  constructor(title, options) { __notes.push({ title, ...options }); }
  close() {}
  static get permission() { return window.__notifPerm || "default"; }
  static requestPermission() { __notifAsked++; return Promise.resolve("granted"); }
};
window.__tones = [];
const createOscillator = AudioContext.prototype.createOscillator;
AudioContext.prototype.createOscillator = function () {
  const o = createOscillator.call(this), start = o.start.bind(o);
  o.start = (at) => { __tones.push(o.frequency.value); return start(at); };
  return o;
};
"""
RECORD = "window.__delivered = []; __jarvis.bus.on('delivered', d => __delivered.push(d)); 0"
# How many recognizers are running when the page asks for the microphone.
MIC_SPY = """() => {
  window.__gum = [];
  const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = (c) => {
    __gum.push((window.__recs || []).filter(r => r.running).length);
    return real(c);
  };
}"""
HOSTILE = "<img src=x onerror=alert(1)>[x](https://ev.il)"


def wait_ready(page):
    page.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")


def until(check, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("condition jamais remplie")


def publish(kind, data):
    from jarvis import events
    return events.publish(kind, data)


def waiting(via):
    from jarvis import inbox
    return [i["payload"].get("id") for i in inbox.pending(via=via)]


def done(task_id, title, via=None):
    task = {"id": task_id, "title": title, "status": "done", "output": "Il fait 18 degres a Lyon.",
            "origin": "voix", "profile": "recherche", "started": time.time() - 5, "ended": time.time()}
    if via:
        task["via"] = via
    return task


@pytest.fixture
def open_pc(context, app_server):
    """open_pc(extra_script) -> the PC's JARVIS page with the fakes (and extra
    ones) in place. Fails the test on any page error."""
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


def with_spies(phone):
    """The phone page again, with the voice, notification and tone spies."""
    phone.add_init_script(SPIES)
    phone.reload()
    wait_ready(phone)
    phone.evaluate(RECORD)
    return phone

# ---------------------------------------------------------------- P0-27: who tells what

def test_pc_never_speaks_phone_results_holds(open_pc, remote_page):
    pc = open_pc("window.__notifPerm = 'granted';" + SPIES)
    pc.wait_for_function("window.__rec && __rec.running")  # the PC page leads (it listens)
    pc.evaluate("document.hasFocus = () => false")  # away from the PC: a notification would show
    pc.evaluate(RECORD + "; __tones.length = 0")
    phone = with_spies(remote_page)

    # A phone's and a Siri result: listed in the PC's task panel, never told there.
    publish("task", done("t-phone", "Météo Lyon", via=phone_origin()))
    publish("task", done("t-siri", "Trajet", via=SIRI))
    publish("reminder", {"id": "r-phone", "title": "Pain", "text": "Acheter du pain", "late_minutes": 0,
                         "via": phone_origin()})
    pc.wait_for_selector(".task.done:has-text('Météo Lyon')")
    pc.wait_for_selector(".task.done:has-text('Trajet')")
    phone.wait_for_function("__spoken.length === 2")  # the phone tells its own two
    pc.wait_for_timeout(800)
    assert pc.evaluate("__spoken") == [] and pc.evaluate("__tones") == []
    assert pc.evaluate("__notes") == [] and pc.evaluate("__delivered") == []
    assert not pc.is_visible("#badge")
    assert pc.locator(".card:has-text('Acheter du pain')").count() == 0  # not the PC's reminder
    assert pc.locator("#card-notif-ask").count() == 0
    assert waiting(SIRI) == ["t-siri"]  # nobody acknowledged it for Siri
    assert sorted(phone.evaluate("__spoken")) == ["Monsieur, la tâche « Météo Lyon » est terminée.",
                                                  "Monsieur, un rappel : Acheter du pain"]
    until(lambda: waiting(phone_origin()) == [])  # acknowledged by the phone itself

    # A reload of the PC page: « Pendant votre absence » never lists them either.
    pc.reload()
    wait_ready(pc)
    pc.wait_for_timeout(1000)
    assert pc.evaluate("__spoken") == []
    assert waiting(SIRI) == ["t-siri"]

    # The spies do work: the PC's own result is told on the PC, never on the phone.
    phone.evaluate("__spoken.length = 0")
    publish("task", done("t-pc", "Rapport"))
    pc.wait_for_function("__spoken.length === 1")
    assert pc.evaluate("__spoken") == ["Monsieur, la tâche « Rapport » est terminée."]
    phone.wait_for_selector(".task.done:has-text('Rapport')", state="attached")
    phone.wait_for_timeout(500)
    assert phone.evaluate("__spoken") == []


def test_remote_page_ignores_the_pc_busy_screen_holds(open_pc, remote_page, monkeypatch):
    from jarvis import desktop
    monkeypatch.setattr(desktop, "attention_state", lambda: "fullscreen")  # a film on the PC
    pc = open_pc(SPIES)
    pc.wait_for_function("__jarvis.state.quiet === true")
    phone = with_spies(remote_page)
    assert phone.evaluate("__jarvis.state.quiet") is False
    assert phone.evaluate("__jarvis.api('/api/delivery')")["attention"] == "ok"
    assert phone.evaluate("async () => (await import('/static/js/delivery.js')).quietNow()") is False
    phone.evaluate("__tones.length = 0")
    pc.evaluate("__tones.length = 0")
    publish("task", done("t-phone", "Météo Lyon", via=phone_origin()))
    publish("task", done("t-pc", "Rapport"))
    phone.wait_for_function("__spoken.length === 1")
    assert phone.evaluate("__spoken") == ["Monsieur, la tâche « Météo Lyon » est terminée."]
    assert phone.evaluate("__tones") == [880, 660]  # the alert earcon
    # The PC keeps its own result behind the badge: its screen is busy.
    pc.wait_for_function("__jarvis.state.pending === 1")
    assert pc.evaluate("__spoken") == [] and pc.evaluate("__tones") == []
    # Even told the PC's state by mistake, the phone page doesn't let it keep it quiet.
    told = []

    def busy_pc(route):
        told.append(route.request.url)
        route.fulfill(json={"leader": None, "quiet_hours": "", "quiet": False, "dnd_until": None,
                            "attention": "fullscreen"})

    phone.route("**/api/delivery", busy_pc)
    phone.evaluate("__jarvis.bus.emit('server:config', {keys: []})")
    phone.wait_for_timeout(500)
    assert told
    assert phone.evaluate("async () => (await import('/static/js/delivery.js')).quietNow()") is False

# ---------------------------------------------------------------- P0-27b: alerts as text

def test_remote_alert_text_renders_as_plain_text_holds(jarvis, remote_page):
    from jarvis import events, inbox
    dialogs = []
    for page in (jarvis, remote_page):
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    events.publish_pc("warning", {"kind": "remote", "text": HOSTILE, "plain": True})
    card = jarvis.locator(".card.warning", has_text="ev.il")
    card.wait_for()
    assert card.locator(".body").text_content() == HOSTILE
    assert card.locator(".body img, .body a").count() == 0
    assert jarvis.locator("#cards img[src='x'], a[href^='https://ev.il']").count() == 0
    # Nor from the inbox, when the PC page opens later.
    inbox.add("warning", {"kind": "remote", "text": HOSTILE + " (2)", "plain": True})
    jarvis.reload()
    wait_ready(jarvis)
    again = jarvis.locator(".card.warning", has_text="(2)")
    again.wait_for()
    assert again.locator(".body").text_content() == HOSTILE + " (2)"
    assert jarvis.locator("img[src='x'], a[href^='https://ev.il']").count() == 0
    # The phone never got it.
    remote_page.wait_for_timeout(500)
    assert remote_page.locator(".card.warning").count() == 0
    assert dialogs == []

# ---------------------------------------------------------------- P0-28: no wake word on the phone

def assert_no_wake_word(page):
    page.wait_for_timeout(3000)  # past wake.js's start-up wait
    assert page.evaluate("__recs.length") == 0  # no recognizer was ever created
    assert page.evaluate("__jarvis.state.mode") == "off"
    assert not page.is_visible("#wakeBtn")
    assert page.evaluate("async () => (await import('/static/js/wake.js')).wakeWanted()") is False


def test_wake_word_is_off_on_remote_and_ios_pages_holds(remote_page, jarvis, browser, app_server):
    # The phone: off even when this browser once switched it on.
    assert_no_wake_word(remote_page)
    remote_page.evaluate("localStorage.setItem('jarvis.settings', JSON.stringify({wake: true}))")
    remote_page.reload()
    wait_ready(remote_page)
    assert_no_wake_word(remote_page)
    # The PC's own page opened on an iPhone (iOS, not remote): off too.
    context = browser.new_context(user_agent=IPHONE_UA, permissions=["microphone"])
    try:
        context.set_default_timeout(10_000)
        context.add_init_script(FAKE_RTC + FAKE_SR)
        ios = context.new_page()
        errors = []
        ios.on("pageerror", lambda err: errors.append(str(err)))
        ios.goto(app_server.url)
        wait_ready(ios)
        assert ios.evaluate("__jarvis.state.remote") is False
        assert_no_wake_word(ios)
        assert errors == []
    finally:
        context.close()
    # A recognizer still running when a session opens on such a page, even
    # mid wake word, is stopped before the microphone is asked for.
    jarvis.evaluate(MIC_SPY)
    for become in ("__jarvis.state.remote = true",
                   f"Object.defineProperty(navigator, 'userAgent', {{value: '{IPHONE_UA}', configurable: true}})"):
        jarvis.wait_for_function("window.__rec && __rec.running && __jarvis.state.mode === 'standby'")
        jarvis.evaluate("__gum.length = 0")
        jarvis.evaluate(f"""() => {{ {become};
            __jarvis.state.wake = {{ final: false, command: "" }};
            __jarvis.voice.connect(); }}""")
        jarvis.wait_for_function("__gum.length === 1")
        assert jarvis.evaluate("__gum") == [0], become
        assert jarvis.evaluate("__recs.every(r => !r.running)")
        jarvis.wait_for_function("__jarvis.state.mode === 'live'")
        jarvis.evaluate("""() => { __jarvis.state.remote = false; delete navigator.userAgent;
            __jarvis.state.wake = null; __jarvis.voice.sleep(); }""")
        jarvis.wait_for_function("__jarvis.state.mode === 'standby'")
