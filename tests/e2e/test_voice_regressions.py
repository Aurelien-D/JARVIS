"""Voice-flow regressions found by the wave 1 review: the post-wake guard
while JARVIS answers, a wake word settled by typed text, every way of
stopping the lock-screen countdown, a typed question then Échap, the tool
phase after a barge-in, typed text without a microphone, mute after sleep,
and the other window while the leader is in a conversation."""
import json

import pytest

import test_wake
from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER
from test_delivery import CLIENT_ID, until
from test_wake import go_live_by_name, listening
from voice_helpers import emit, go_live, types

pytestmark = pytest.mark.e2e
open_page = test_wake.open_page  # open_page(extra_script) -> a page with the fakes (a fixture)


@pytest.fixture
def nothing_real(monkeypatch):
    """Nothing really opens or locks here; returns what would have run."""
    from jarvis import confirm, desktop
    confirm.PENDING.clear()
    done = []
    monkeypatch.setattr(desktop, "open_target", lambda **kw: done.append(kw) or {"ok": True})
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: done.append(action) or {"ok": True})
    yield done
    confirm.PENDING.clear()


def call(name, call_id, args=None):
    return {"type": "response.done", "response": {"status": "completed", "output": [
        {"type": "function_call", "name": name, "call_id": call_id, "status": "completed",
         "arguments": json.dumps(args or {})}]}}


def output(page, call_id):
    page.wait_for_function(f"__sent.some(m => m.item && m.item.call_id === '{call_id}')")
    return json.loads(page.evaluate(f"__sent.find(m => m.item && m.item.call_id === '{call_id}').item.output"))


def false_wakes(page):
    return page.evaluate("__jarvis.settings.get('falseWakes', 0)")

# ---------------------------------------------------------------- the post-wake guard

def test_a_bare_wake_is_not_cut_while_jarvis_answers(open_page):
    page = open_page()
    listening(page)
    go_live_by_name(page)
    emit(page, {"type": "response.created", "response": {"id": "r1"}})
    emit(page, {"type": "output_audio_buffer.started"})
    for _ in range(10):  # ten seconds of JARVIS speaking: not silence
        emit(page, {"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": "bla "})
        page.clock.run_for(1000)
    assert page.evaluate("__jarvis.state.mode") == "live"
    assert false_wakes(page) == 0
    emit(page, {"type": "response.done", "response": {"status": "completed", "output": []}})
    emit(page, {"type": "output_audio_buffer.stopped"})
    page.clock.run_for(8500)  # then nothing from monsieur: a false wake after all
    page.wait_for_function("__jarvis.state.mode === 'standby'")
    assert false_wakes(page) == 1


def test_a_message_told_after_a_bare_wake_keeps_the_session_and_waits_again_if_cut(open_page):
    page = open_page()
    listening(page)
    go_live_by_name(page)
    emit(page, {"type": "response.created", "response": {"id": "r1"}})
    emit(page, {"type": "response.done", "response": {"status": "completed", "output": []}})
    emit(page, {"type": "output_audio_buffer.stopped"})  # « Oui, monsieur ? »
    page.evaluate("__sent.length = 0; __jarvis.bus.emit('deliver', {text: 'Note de test', kind: 'info'})")
    for _ in range(30):  # the pause comes: the message is told
        if "Note de test" in page.evaluate("__texts()"):
            break
        page.clock.run_for(200)
    assert "Note de test" in page.evaluate("__texts()")
    emit(page, {"type": "response.created", "response": {"id": "r2"}})
    page.clock.run_for(12000)  # he called for it: no false wake
    assert page.evaluate("__jarvis.state.mode") == "live"
    assert false_wakes(page) == 0
    # Monsieur talks over it before the end: not heard in full, back behind the badge.
    emit(page, {"type": "response.done", "response": {"status": "cancelled", "output": [],
                                                       "status_details": {"reason": "turn_detected"}}})
    page.wait_for_function("__jarvis.state.pending === 1")
    assert page.is_visible("#badge")

# ---------------------------------------------------------------- typed text while the name is heard

def test_text_typed_while_the_name_is_heard_settles_the_wake(jarvis):
    page = jarvis
    page.wait_for_function("window.__rec && __rec.running")
    page.evaluate("window.__holdSessionCreated = true; __say('Jarvis', false)")
    page.fill("#askInput", "quelle heure est-il")
    page.press("#askInput", "Enter")
    page.wait_for_function("window.__dc && __dc.readyState === 'open'")
    emit(page, {"type": "session.created", "session": {"id": "s"}})
    page.wait_for_function("__jarvis.state.mode === 'live'")
    assert page.evaluate("__jarvis.state.wake") is None
    assert page.evaluate("__recs.filter(r => r.running).length") == 0  # no recognizer during the session
    assert page.evaluate("__texts()") == ["quelle heure est-il"]
    # A sentence starting with the name, in the session: the model hears it, once.
    page.evaluate("__sent.length = 0; __say('Jarvis, ouvre Spotify', true)")
    page.wait_for_timeout(300)
    assert page.evaluate("__texts()") == []
    # And news is told at the next pause, not held back by a stuck wake.
    emit(page, {"type": "response.created", "response": {"id": "r1"}})
    emit(page, {"type": "response.done", "response": {"status": "completed", "output": []}})
    emit(page, {"type": "output_audio_buffer.stopped"})
    page.evaluate("__jarvis.bus.emit('deliver', {text: 'Note de test', kind: 'info'})")
    page.wait_for_function("__texts().includes('Note de test')", timeout=5000)

# ---------------------------------------------------------------- stopping the lock-screen countdown

def start_lock(page):
    go_live(page)
    emit(page, {"type": "response.created"})
    emit(page, call("system_control", "lk", {"action": "lock_screen"}))
    page.wait_for_selector("#card-lock-countdown .confirm-countdown")


def test_interrompre_cancels_the_lock_countdown(nothing_real, jarvis):
    start_lock(jarvis)
    assert not jarvis.is_disabled("#interruptBtn")
    jarvis.click("#interruptBtn")
    assert output(jarvis, "lk") == {"ok": False, "cancelled": True, "error": "Verrouillage annulé par monsieur."}
    jarvis.wait_for_timeout(3500)
    assert "lock_screen" not in nothing_real


def test_speaking_during_the_lock_countdown_cancels_it(nothing_real, jarvis):
    start_lock(jarvis)
    emit(jarvis, {"type": "input_audio_buffer.speech_started", "item_id": "u1"})  # « Non, attends ! »
    assert output(jarvis, "lk")["cancelled"] is True
    jarvis.wait_for_timeout(3500)
    assert "lock_screen" not in nothing_real

# ---------------------------------------------------------------- a typed question, then Échap

def test_a_question_typed_while_jarvis_speaks_is_answered_after_echap(jarvis):
    page = jarvis
    go_live(page)
    emit(page, {"type": "response.created", "response": {"id": "r1"}})
    emit(page, {"type": "output_audio_buffer.started"})
    emit(page, {"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": "Alors, voici "})
    page.evaluate("__sent.length = 0")
    page.fill("#askInput", "non, plutôt la météo de Paris")
    page.press("#askInput", "Enter")
    page.wait_for_function("__sent.some(m => m.item && m.item.role === 'user')")
    assert "response.create" not in types(page)  # one response at a time: it waits
    page.keyboard.press("Escape")  # keys.js: Échap works from the field too
    emit(page, {"type": "response.done", "response": {"status": "cancelled", "output": [],
                                                       "status_details": {"reason": "client_cancelled"}}})
    emit(page, {"type": "output_audio_buffer.cleared"})
    page.wait_for_function("__types().includes('response.create')")
    t = types(page)
    assert t.index("response.cancel") < t.index("response.create")  # stopped, then his answer

# ---------------------------------------------------------------- a barge-in while a tool runs

def test_after_a_barge_in_the_running_tool_shows_again(jarvis):
    page = jarvis
    go_live(page)
    held = []
    page.route("**/api/tool", lambda route: held.append(route))
    emit(page, {"type": "response.created", "response": {"id": "r1"}})
    emit(page, call("get_status", "c1"))
    until(lambda: (page.evaluate("1"), held)[1])
    # Monsieur asks something else meanwhile and gets a quick answer.
    emit(page, {"type": "input_audio_buffer.speech_started", "item_id": "u1"})
    emit(page, {"type": "input_audio_buffer.speech_stopped", "item_id": "u1"})
    emit(page, {"type": "response.created", "response": {"id": "r2"}})
    emit(page, {"type": "output_audio_buffer.started"})
    emit(page, {"type": "response.done", "response": {"status": "completed", "output": []}})
    emit(page, {"type": "output_audio_buffer.stopped"})
    page.wait_for_function("__jarvis.state.phase === 'tool'")
    assert page.inner_text("#statusPill") == "Je fais le point…"
    assert page.evaluate("document.body.dataset.state") == "live-tool"
    held[0].fulfill(status=200, json={"ok": True})
    page.wait_for_function("__sent.some(m => m.type === 'response.create')")

# ---------------------------------------------------------------- typed text without a microphone

NO_MIC = r"""
navigator.mediaDevices.getUserMedia = () =>
  Promise.reject(Object.assign(new Error('Requested device not found'), { name: 'NotFoundError' }));
"""


def test_typed_text_comes_back_when_the_session_cannot_open(context, app_server):
    context.set_default_timeout(10_000)
    context.route("https://api.openai.com/**", lambda route: route.fulfill(
        status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
    page = context.new_page()
    page.add_init_script(FAKE_RTC + FAKE_SR + NO_MIC)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(app_server.url)
    page.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")
    page.fill("#askInput", "quelle heure est-il ?")
    page.press("#askInput", "Enter")
    page.wait_for_function("document.getElementById('statusPill').classList.contains('err')")
    assert page.input_value("#askInput") == "quelle heure est-il ?"  # not lost
    assert "Aucun micro détecté" in page.inner_text("#statusPill")
    # 'Réessayer' sends it again (and gives it back again if it still fails).
    page.fill("#askInput", "")
    page.click("#statusActions button:has-text('Réessayer')")
    page.wait_for_function("document.getElementById('askInput').value === 'quelle heure est-il ?'")
    assert errors == []

# ---------------------------------------------------------------- mute and sleep

def test_mute_does_not_survive_sleep(jarvis):
    page = jarvis
    go_live(page)
    page.keyboard.press("Control+m")
    page.wait_for_function("__jarvis.state.muted === true")
    page.click("#sleepBtn")
    page.wait_for_function("__jarvis.state.mode === 'standby'")
    assert page.evaluate("__jarvis.state.muted") is False
    page.wait_for_function("window.__rec && __rec.running")
    page.evaluate("__say('Jarvis, quelle heure est-il', true)")
    page.wait_for_function("__jarvis.state.mode === 'live'")
    assert page.evaluate("__jarvis.state.muted") is False
    assert "Micro coupé" not in page.inner_text("#statusPill")

# ---------------------------------------------------------------- the other window

def test_the_other_window_explains_while_the_leader_is_in_a_conversation(open_page):
    from jarvis import events
    a, b = open_page(), open_page()
    until(lambda: events.leader() in (a.evaluate(CLIENT_ID), b.evaluate(CLIENT_ID)))
    a.bring_to_front()
    a.click("#orbBtn")
    a.wait_for_function("__jarvis.state.mode === 'live'")
    until(lambda: events.leader() == a.evaluate(CLIENT_ID))
    b.bring_to_front()
    b.evaluate("window.dispatchEvent(new Event('focus'))")
    card = b.locator("#card-other-page")
    card.filter(has_text="en conversation dans une autre fenêtre").wait_for()
    assert card.locator("button:has-text('Utiliser celle-ci')").count() == 0  # it would do nothing
    a.click("#sleepBtn")
    # The conversation is over: the choice comes back (unless this page took
    # over on its own, being the focused one), and it works.
    b.wait_for_function("""!document.getElementById('card-other-page')
      || !!document.querySelector('#card-other-page .actions button')""")
    if card.count():
        card.locator("button", has_text="Utiliser celle-ci").click()
    until(lambda: events.leader() == b.evaluate(CLIENT_ID), timeout=5)
    b.wait_for_function("!document.getElementById('card-other-page')")
    a.locator("#card-other-page button:has-text('Utiliser celle-ci')").wait_for()
