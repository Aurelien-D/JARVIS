"""Delivery on the paired iPhone (spec 4.12) in a real browser, next to the PC
page: a task launched from the phone is announced on the phone and only
listed on the PC; each « Pendant votre absence » holds its own device's
messages; the phone catches up on open without any leader; and when JARVIS
stops on the PC, the phone says so and keeps its page.

The phone is remote_page (fake-guard mode); speech synthesis, notifications,
earcons and the voice session are faked. Fictitious devices only."""
import json
import time

import pytest
from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER

pytestmark = pytest.mark.e2e

PHONE = "app:d_e2e0000000000001"

# The browser's voice, notifications and the earcons' tones, recorded (as in test_delivery).
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


@pytest.fixture
def no_pc_page():
    """The PC pages of earlier tests are gone (their streams closed): no leader."""
    from jarvis import events
    until(lambda: not events.has_subscribers())
    assert events.leader() is None


def with_spies(phone, url=None):
    """The phone page (re)loaded with the voice, notification and tone spies."""
    phone.add_init_script(SPIES)
    if url:
        phone.goto(url)
    else:
        phone.reload()
    wait_ready(phone)
    phone.evaluate(RECORD)
    return phone


def call(page, name, call_id, args):
    """The voice model calls a tool in this page's session."""
    page.evaluate("ev => __emit(ev)", {"type": "response.done", "response": {"output": [
        {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(args)}]}})


def output(page, call_id):
    page.wait_for_function(f"__sent.some(m => m.item && m.item.call_id === '{call_id}')")
    return json.loads(page.evaluate(f"__sent.find(m => m.item && m.item.call_id === '{call_id}').item.output"))

# ---------------------------------------------------------------- live

def test_a_task_launched_from_the_phone_is_told_on_the_phone_and_only_listed_on_the_pc(open_pc, remote_page):
    from jarvis import tasks
    pc = open_pc(SPIES)
    pc.wait_for_function("window.__rec && __rec.running")  # the PC page leads
    pc.evaluate(RECORD + "; __tones.length = 0")
    phone = with_spies(remote_page)
    # Asked by voice on the phone: the task carries the phone's origin.
    phone.click("#orbBtn")
    phone.wait_for_function("__jarvis.state.mode === 'live'")
    call(phone, "delegate_to_claude", "d1", {"title": "Météo Lyon", "prompt": "météo ?", "profile": "recherche"})
    started = output(phone, "d1")
    assert started["status"] == "started"
    assert tasks.TASKS[started["task_id"]]["via"] == PHONE
    phone.evaluate("__jarvis.voice.sleep()")  # back in its pocket: the badge and the voice tell it
    phone.wait_for_function("__jarvis.state.mode !== 'live'")
    phone.evaluate("__tones.length = 0")

    # The phone: the alert, the spoken line, and the details behind its badge.
    phone.wait_for_function("__spoken.length === 1", timeout=15_000)
    assert phone.evaluate("__spoken") == ["Monsieur, la tâche « Météo Lyon » est terminée."]
    phone.wait_for_function("__jarvis.state.pending === 1")
    assert phone.is_visible("#badge") and phone.text_content("#badge") == "1"
    assert phone.evaluate("__tones") == [880, 660]
    until(lambda: waiting(PHONE) == [])  # acknowledged by the phone (its own message)
    assert phone.locator("#card-other-page").count() == 0  # the PC's election is not its business

    # The PC: listed in its task panel, nothing else.
    pc.wait_for_selector(".task.done:has-text('Météo Lyon')")
    pc.wait_for_timeout(500)
    assert pc.evaluate("__spoken") == [] and pc.evaluate("__tones") == [] and pc.evaluate("__delivered") == []
    assert not pc.is_visible("#badge") and pc.evaluate("__jarvis.state.pending") == 0
    assert pc.locator("#card-notif-ask").count() == 0  # not even the offer of notifications

    # The badge opens a session on the phone, where the result is told as data.
    phone.click("#badge")
    phone.wait_for_function("__texts().some(t => t.includes('Il fait 18 degres a Lyon.'))")

# ---------------------------------------------------------------- on open

def test_each_absence_message_holds_its_own_device_s_messages(no_pc_page, open_pc, remote_page, app_server):
    phone = remote_page
    phone.goto("about:blank")  # the phone closed too
    publish("reminder", {"id": "r-pc", "title": "Arroser", "text": "Arroser les plantes", "late_minutes": 0})
    publish("task", {"id": "t-phone", "title": "Trajet", "status": "done", "output": "20 minutes.",
                     "origin": "voix", "via": PHONE})
    assert waiting("pc") == ["r-pc"] and waiting(PHONE) == ["t-phone"]

    # The phone opens with no PC page at all (no leader): it catches up on its own at once.
    with_spies(phone, app_server.remote_url)
    phone.wait_for_function("__spoken.length === 1")
    assert phone.evaluate("__spoken") == ["Pendant votre absence : la tâche « Trajet » est terminée."]
    until(lambda: waiting(PHONE) == [])
    assert waiting("pc") == ["r-pc"]  # the PC's reminder waits for the PC
    assert phone.locator(".card:has-text('Arroser les plantes')").count() == 0

    # The PC page: its reminder only, never the phone's task.
    pc = open_pc(SPIES)
    pc.wait_for_function("__spoken.length === 1")
    assert pc.evaluate("__spoken") == ["Pendant votre absence : rappel : Arroser les plantes."]
    until(lambda: waiting("pc") == [])
    pc.wait_for_timeout(500)
    assert pc.evaluate("__spoken.length") == 1
    phone.wait_for_timeout(300)
    assert phone.evaluate("__spoken.length") == 1

# ---------------------------------------------------------------- JARVIS stops on the PC

def test_the_phone_says_jarvis_is_closed_on_the_pc_and_keeps_its_page(remote_page):
    from jarvis import events
    phone = remote_page
    phone.evaluate("window.__closed = 0; window.close = () => { window.__closed++; }; 0")
    phone.click("#orbBtn")
    phone.wait_for_function("__jarvis.state.mode === 'live' && __pc.connectionState === 'connected'")
    events.publish("shutdown", {})  # what server._request_shutdown sends before the streams end
    phone.wait_for_function("!document.getElementById('serverChip').hidden")
    assert phone.text_content("#serverChip") == "JARVIS est fermé sur le PC"
    assert phone.evaluate("__pc.connectionState") == "closed"  # the voice session is over
    assert phone.evaluate("__jarvis.state.mode") == "off"
    phone.wait_for_timeout(500)
    assert phone.evaluate("window.__closed") == 0  # the phone's page stays
