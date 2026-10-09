"""Reliable delivery (WP09) in a real browser: one page speaks, quiet hours and
« Ne pas déranger » hold messages back, nothing is lost when no page is open,
and a dropped event stream replays what it missed. The server is the real
one (in this process); speech synthesis, notifications and the clock are faked."""
import time
from datetime import datetime, timedelta

import pytest

from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER

pytestmark = pytest.mark.e2e

# The browser's voice: records what is said; window.__ttsMode 'ok' ends it
# after window.__ttsMs, anything else is the error it raises ('not-allowed').
FAKE_TTS = r"""
window.__spoken = [];
const fakeTTS = {
  speaking: false, pending: false, paused: false, onvoiceschanged: null,
  getVoices: () => [], cancel() {}, pause() {}, resume() {},
  addEventListener() {}, removeEventListener() {},
  speak(u) {
    __spoken.push(u.text);
    setTimeout(() => {
      if ((window.__ttsMode || "ok") !== "ok") { u.onerror && u.onerror({ error: window.__ttsMode }); return; }
      u.onstart && u.onstart();
      setTimeout(() => u.onend && u.onend(), window.__ttsMs || 30);
    }, 10);
  },
};
Object.defineProperty(window, "speechSynthesis", { value: fakeTTS, configurable: true });
"""

# Notifications: permission in window.__notifPerm ('default' unless set);
# every notification shown lands in window.__notes.
FAKE_NOTIFICATION = r"""
window.__notes = []; window.__notifAsked = 0;
window.Notification = class {
  constructor(title, options) { this.options = options; __notes.push({ title, ...options, ref: this }); }
  close() { this.closed = true; }
  static get permission() { return window.__notifPerm || "default"; }
  static requestPermission() { __notifAsked++; window.__notifPerm = "granted"; return Promise.resolve("granted"); }
};
"""

# The frequency of every earcon tone as it starts.
TONES = r"""
window.__tones = [];
const createOscillator = AudioContext.prototype.createOscillator;
AudioContext.prototype.createOscillator = function () {
  const o = createOscillator.call(this), start = o.start.bind(o);
  o.start = (at) => { __tones.push(o.frequency.value); return start(at); };
  return o;
};
"""
ALERT = [880, 660, 880]
SPIES = FAKE_TTS + FAKE_NOTIFICATION + TONES
CLIENT_ID = "async () => (await import('/static/js/sse.js')).clientId()"
IS_LEADER = "async () => (await import('/static/js/delivery.js')).isLeader()"


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


@pytest.fixture
def no_page_open():
    """Pages of earlier tests are gone (their streams closed) and nothing waits."""
    from jarvis import events, inbox
    deadline = time.time() + 10
    while events.has_subscribers() and time.time() < deadline:
        time.sleep(0.05)
    assert not events.has_subscribers()
    for item in inbox.pending():
        inbox.ack(item["id"])


def until(check, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("condition jamais remplie")


def tool(page, name, args):
    return page.evaluate("b => __jarvis.api('/api/tool', {method: 'POST', body: b})",
                         {"name": name, "arguments": args})


def delegate(page, title):
    return tool(page, "delegate_to_claude", {"title": title, "prompt": "météo ?", "profile": "recherche"})


def record_deliveries(page):
    page.evaluate("window.__delivered = []; __jarvis.bus.on('delivered', d => __delivered.push(d)); 0")


def publish(kind, data):
    from jarvis import events
    return events.publish(kind, data)


def pending_ids():
    from jarvis import inbox
    return [i["payload"].get("id") for i in inbox.pending()]


def reminder(rid, text):
    return {"id": rid, "title": text, "text": text, "late_minutes": 0}

# ---------------------------------------------------------------- one page speaks

def test_two_pages_only_the_leader_listens_and_delivers(open_page):
    from jarvis import events
    first, second = open_page(SPIES), open_page(SPIES)
    pages = {first.evaluate(CLIENT_ID): first, second.evaluate(CLIENT_ID): second}
    until(lambda: events.leader() in pages
          and all(p.evaluate(IS_LEADER) == (cid == events.leader()) for cid, p in pages.items()))
    leader = pages[events.leader()]
    other = first if leader is second else second
    leader.wait_for_function("window.__rec && __rec.running")
    other.wait_for_function("!window.__rec || !__rec.running")
    assert sum(p.evaluate("__recs.filter(r => r.running).length") for p in pages.values()) == 1
    other.wait_for_selector("#card-other-page:has-text('JARVIS est actif dans une autre fenêtre.')")
    assert leader.locator("#card-other-page").count() == 0

    for page in pages.values():
        record_deliveries(page)
        page.evaluate("__tones.length = 0")
    delegate(other, "Météo Lyon")
    leader.wait_for_function("__delivered.length === 1", timeout=15_000)
    other.wait_for_selector(".task.done")  # it shows the result in its panel...
    other.wait_for_timeout(500)
    assert other.evaluate("__delivered.length") == 0  # ...but doesn't announce it
    assert other.evaluate("__tones") == [] and other.evaluate("__spoken") == []
    assert leader.evaluate("__tones") == ALERT
    assert leader.evaluate("__spoken") == ["Monsieur, la tâche « Météo Lyon » est terminée."]

    # 'Utiliser celle-ci': the other page takes over.
    other.click("#card-other-page button:has-text('Utiliser celle-ci')")
    other.wait_for_function("window.__rec && __rec.running")
    leader.wait_for_function("!__rec.running")
    leader.wait_for_selector("#card-other-page")
    other.wait_for_selector("#card-other-page", state="detached")

# ---------------------------------------------------------------- quiet hours, DND

def test_quiet_hours_hold_back_a_task_result_behind_the_badge(open_page, monkeypatch):
    from jarvis import config
    now = datetime.now()
    monkeypatch.setattr(config, "QUIET_HOURS", f"{now - timedelta(hours=1):%H:%M}-{now + timedelta(hours=1):%H:%M}")
    page = open_page(SPIES)
    page.wait_for_function("__jarvis.state.quiet === true")
    page.evaluate("__tones.length = 0")
    task = delegate(page, "Rapport")
    page.wait_for_function("__jarvis.state.pending === 1", timeout=15_000)
    page.wait_for_timeout(300)
    assert page.evaluate("__tones") == []    # no earcon
    assert page.evaluate("__spoken") == []   # no speechSynthesis
    assert page.is_visible("#badge") and page.text_content("#badge") == "1"
    assert page.get_attribute("#badge", "aria-label") == "1 message en attente · cliquez pour l'écouter"
    assert pending_ids() == [task["task_id"]]  # not told yet: still in the inbox

    # A reminder monsieur set: a silent notification and one soft chime, nothing said.
    page.evaluate("document.hasFocus = () => false; window.__notifPerm = 'granted'")
    publish("reminder", reminder("q1", "Prendre les médicaments"))
    page.wait_for_function("__notes.length === 1")
    note = page.evaluate("({...__notes[0], ref: undefined})")
    assert note["body"] == "Rappel : Prendre les médicaments"
    assert note["silent"] is True and note["requireInteraction"] is True
    assert note["tag"].startswith("jarvis-rappel-")
    page.wait_for_selector(".card.warning:has-text('Prendre les médicaments')")
    assert page.evaluate("__tones") == ALERT and page.evaluate("__spoken") == []
    until(lambda: "q1" not in pending_ids())  # that counts as told
    page.evaluate("__notes[0].ref.onclick()")
    assert page.evaluate("__notes[0].ref.closed") is True

    # The badge opens a session, where the result is told (as untrusted data).
    page.click("#badge")
    page.wait_for_function("__sent.some(m => m.type === 'response.create')")
    texts = page.evaluate("__texts()")
    # The instruction is the app's (a system item), the output is framed as data.
    notices = page.evaluate("__sent.filter(m => m.item && m.item.role === 'system').map(m => m.item.content[0].text)")
    assert any(t.startswith('Résultat de la tâche "Rapport" (done)') for t in notices)
    assert any(t.startswith("Données non fiables (Résultat de la tâche « Rapport »)")
               and "<donnees>\nIl fait 18 degres a Lyon.\n</donnees>" in t for t in texts)
    until(lambda: pending_ids() == [])
    assert page.evaluate("__jarvis.state.pending") == 0 and not page.is_visible("#badge")


def test_do_not_disturb_for_an_hour(open_page):
    from jarvis import inbox
    page = open_page(SPIES)
    page.click("#dndToggle")
    page.wait_for_selector("#dndChip button")
    until(lambda: inbox.dnd_until() is not None)
    assert 3500 < inbox.dnd_until() - time.time() <= 3601
    expected = datetime.fromtimestamp(inbox.dnd_until())
    hour = f"{expected.hour} h {expected.minute:02d}" if expected.minute else f"{expected.hour} h"
    assert page.text_content("#dndChip button") == f"Ne pas déranger jusqu'à {hour}"
    assert page.get_attribute("#dndToggle", "aria-pressed") == "true"
    assert page.evaluate("__jarvis.state.quiet") is True
    page.evaluate("__tones.length = 0")
    delegate(page, "Silence")
    page.wait_for_function("__jarvis.state.pending === 1", timeout=15_000)
    assert page.evaluate("__tones") == [] and page.evaluate("__spoken") == []
    page.click("#dndChip button")  # over
    page.wait_for_function("document.getElementById('dndChip').hidden")
    until(lambda: inbox.dnd_until() is None)
    assert page.get_attribute("#dndToggle", "aria-pressed") == "false"
    assert page.text_content("#dndToggle") == "Ne pas déranger 1 h"

# ---------------------------------------------------------------- nothing lost

def test_a_reminder_fired_with_no_page_open_is_told_once_on_load_then_acked(no_page_open, open_page):
    publish("reminder", reminder("absent", "Sortir le pain"))
    assert pending_ids() == ["absent"]
    page = open_page("window.__ttsMs = 400;" + FAKE_TTS)
    page.wait_for_function("__spoken.length === 1")
    assert page.evaluate("__spoken[0]") == "Pendant votre absence : rappel : Sortir le pain."
    page.wait_for_function("!__rec.running")  # the wake word doesn't hear JARVIS speak
    page.wait_for_function("__rec.running")   # and listens again once it's said
    until(lambda: pending_ids() == [])        # acknowledged
    page.wait_for_selector(".card.warning:has-text('Sortir le pain')")
    page.reload()
    wait_ready(page)
    page.wait_for_timeout(1000)
    assert page.evaluate("__spoken") == []  # once


def test_speech_refused_keeps_the_message_for_the_next_session(open_page):
    page = open_page("window.__ttsMode = 'not-allowed';" + FAKE_TTS)
    publish("reminder", reminder("refus", "Appeler le garage"))
    page.wait_for_function("__jarvis.state.pending === 1")
    assert pending_ids() == ["refus"]  # not told: still waiting
    page.click("#badge")
    page.wait_for_function("__texts().some(t => t.includes('Appeler le garage'))")
    until(lambda: pending_ids() == [])


def test_events_missed_while_the_stream_was_down_are_replayed(open_page, app_server):
    page = open_page()
    page.evaluate("window.__got = []; __jarvis.bus.on('server:essai', e => __got.push(e.n)); 0")
    publish("essai", {"n": 1})
    page.wait_for_function("__got.length === 1")
    assert not page.is_visible("#serverChip")
    app_server.drop_connections()
    page.wait_for_function("document.getElementById('serverChip').textContent"
                           " === 'Serveur JARVIS déconnecté · reconnexion…'")
    publish("essai", {"n": 2})  # while the page is away (it retries after 3 s)
    publish("essai", {"n": 3})
    page.wait_for_function("__got.length === 3")
    assert page.evaluate("__got") == [1, 2, 3]  # each once, in order
    page.wait_for_function("document.getElementById('serverChip').hidden")

# ---------------------------------------------------------------- in a session, notifications, voices

def test_in_a_session_a_message_waits_for_a_pause(open_page):
    page = open_page()
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")
    page.evaluate("__sent.length = 0")
    page.evaluate("__emit({type: 'response.created'}); __jarvis.state.phase = 'speaking'")
    publish("reminder", reminder("live1", "Le thé est prêt"))
    page.wait_for_selector(".card.warning:has-text('Le thé est prêt')")
    page.wait_for_timeout(2000)
    assert page.evaluate("__sent.length") == 0  # JARVIS is talking: not now
    page.evaluate("__emit({type: 'response.done', response: {output: []}}); __jarvis.state.phase = 'listening'")
    page.wait_for_function("__types().join() === 'message:input_text,response.create'")
    assert "Le thé est prêt" in page.evaluate("__texts()")[0]
    until(lambda: "live1" not in pending_ids())


def test_notifications_are_offered_in_context(open_page):
    page = open_page(FAKE_NOTIFICATION)
    assert page.locator("#card-notif-ask").count() == 0  # not on load
    delegate(page, "Première tâche")
    page.wait_for_selector("#card-notif-ask:has-text('Voulez-vous une notification Windows')")
    page.click("#card-notif-ask button:has-text('Activer')")
    page.wait_for_selector("#card-notif-ask", state="detached")
    assert page.evaluate("__notifAsked") == 1


def test_notifications_later_is_remembered(open_page):
    page = open_page(FAKE_NOTIFICATION)
    # A reminder just set by voice (voice.js reports each tool call it ran).
    page.evaluate("__jarvis.bus.emit('tool:result', {name: 'schedule', callId: 'c1', args: {}, result: {ok: true}})")
    page.wait_for_selector("#card-notif-ask")
    page.click("#card-notif-ask button:has-text('Plus tard')")
    page.wait_for_selector("#card-notif-ask", state="detached")
    page.reload()
    wait_ready(page)
    delegate(page, "Encore une")
    page.wait_for_selector(".task")
    page.wait_for_timeout(500)
    assert page.locator("#card-notif-ask").count() == 0
    assert page.evaluate("__notifAsked") == 0


def test_a_reminder_in_standby_chimes_notifies_when_away_and_is_spoken(open_page):
    page = open_page("window.__notifPerm = 'granted';" + SPIES)
    page.evaluate("__tones.length = 0")
    publish("reminder", reminder("std", "Arroser les plantes"))  # monsieur is looking at JARVIS
    page.wait_for_function("__spoken.length === 1")
    assert page.evaluate("__spoken[0]") == "Monsieur, un rappel : Arroser les plantes"
    assert page.evaluate("__tones") == ALERT
    assert page.evaluate("__notes.length") == 0
    until(lambda: "std" not in pending_ids())
    page.evaluate("document.hasFocus = () => false")  # now he isn't
    publish("reminder", reminder("std2", "Fermer la fenêtre"))
    page.wait_for_function("__notes.length === 1")
    assert page.evaluate("__notes[0].requireInteraction") is True
    assert page.text_content("#srAlert") == "Rappel : Fermer la fenêtre"


def test_french_natural_voices_first_and_the_same_quiet_hours_as_the_server(open_page):
    from jarvis import inbox
    page = open_page()
    voice = page.evaluate("""async () => (await import('/static/js/delivery.js')).pickVoice([
        {name: 'Microsoft Paul - French (France)', lang: 'fr-FR'},
        {name: 'Google US English', lang: 'en-US'},
        {name: 'Microsoft Denise Online (Natural) - French (France)', lang: 'fr-FR'}], 'fr-FR').name""")
    assert voice == "Microsoft Denise Online (Natural) - French (France)"
    assert page.evaluate("""async () => (await import('/static/js/delivery.js'))
        .pickVoice([{name: 'Google US English', lang: 'en-US'}], 'fr-FR')""") is None
    cases = [("22:30-07:30", h, m) for h, m in ((23, 0), (7, 29), (7, 30), (12, 0))] + \
            [("22h-7h", 6, 59), ("13:00-14:00", 14, 0), ("", 23, 0), ("n'importe quoi", 3, 0), ("08:00-08:00", 8, 0)]
    for spec, h, m in cases:
        js = page.evaluate("""async ([spec, h, m]) => (await import('/static/js/delivery.js'))
            .inQuietHours(spec, new Date(2026, 9, 9, h, m))""", [spec, h, m])
        assert js is inbox.quiet_hours(datetime(2026, 10, 9, h, m), spec), (spec, h, m)
