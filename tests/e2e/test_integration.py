"""Wave 1 working together in a real browser: the HUD follows the phases the
voice session emits, tool labels show while a tool runs, a 'complet' task
asked by the voice model waits on a confirmation card (buttons and keys),
a spoken « oui » only counts after the question, the composer and the
keyboard map share one set of handlers, and the wake word, quiet hours and
delivery hand a task result over to the session that opens.

Set JARVIS_E2E_SHOTS to a folder to also save screenshots of the main states."""
import json
import os

import pytest

from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER
from test_delivery import SPIES, delegate, pending_ids, until, wait_ready
from voice_helpers import emit, go_live, items, listen

pytestmark = pytest.mark.e2e

SHOTS = os.environ.get("JARVIS_E2E_SHOTS", "")
COMPLET = {"title": "Ranger les téléchargements", "prompt": "Range ~/Downloads par type de fichier",
           "profile": "complet"}
CARD = ".card.confirm[data-state='pending']"
NOTICES = "__sent.filter(m => m.item && m.item.role === 'system').map(m => m.item.content[0].text)"


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch):
    """No pending request from another test, and nothing really opens or locks here."""
    from jarvis import confirm, desktop
    confirm.PENDING.clear()
    done = []
    monkeypatch.setattr(desktop, "open_target", lambda **kw: done.append(kw) or {"ok": True})
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: done.append(action) or {"ok": True})
    yield done
    confirm.PENDING.clear()


@pytest.fixture
def spied_page(context, app_server):
    """A JARVIS page that also records the browser's voice, notifications and earcon tones."""
    context.set_default_timeout(10_000)
    context.route("https://api.openai.com/**", lambda route: route.fulfill(
        status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
    page = context.new_page()
    page.add_init_script(FAKE_RTC + FAKE_SR + SPIES)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(app_server.url)
    wait_ready(page)
    yield page
    assert not errors, f"erreurs dans la page : {errors}"


def status(page):
    return page.inner_text("#statusPill")


def body_state(page):
    return page.evaluate("document.body.dataset.state")


def call(name, call_id, args=None):
    return {"type": "response.done", "response": {"status": "completed", "output": [
        {"type": "function_call", "name": name, "call_id": call_id, "status": "completed",
         "arguments": json.dumps(args or {})}]}}


def output(page, call_id):
    """The function_call_output the page sent back for this call."""
    page.wait_for_function(f"__sent.some(m => m.item && m.item.call_id === '{call_id}')")
    return json.loads(page.evaluate(f"__sent.find(m => m.item && m.item.call_id === '{call_id}').item.output"))


def jarvis_says(page, text, item="a1"):
    """One spoken answer from the model, start to end."""
    emit(page, {"type": "response.created", "response": {"id": f"r_{item}"}})
    emit(page, {"type": "output_audio_buffer.started"})
    emit(page, {"type": "response.output_audio_transcript.delta", "item_id": item, "delta": text})
    emit(page, {"type": "response.output_audio_transcript.done", "item_id": item, "transcript": text})
    emit(page, {"type": "response.done", "response": {"status": "completed", "output": []}})
    emit(page, {"type": "output_audio_buffer.stopped"})


def hold_tools(page):
    """/api/tool calls wait in the returned list until each is continued."""
    held = []
    page.route("**/api/tool", lambda route: held.append(route))
    return held


def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"{name}.png"))


# ---------------------------------------------------------------- the HUD follows voice.js

def test_the_status_line_and_the_page_follow_the_realtime_events(jarvis):
    go_live(jarvis)
    listen(jarvis, "phase")
    assert status(jarvis) == "Je vous écoute…" and body_state(jarvis) == "live-listening"
    emit(jarvis, {"type": "input_audio_buffer.speech_started", "item_id": "u1"})
    assert status(jarvis) == "Je vous entends…" and body_state(jarvis) == "live-user"
    emit(jarvis, {"type": "input_audio_buffer.speech_stopped", "item_id": "u1"})
    assert status(jarvis) == "Réflexion…" and body_state(jarvis) == "live-thinking"
    emit(jarvis, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
                  "transcript": "Quelle heure est-il ?"})
    assert "Quelle heure est-il" in jarvis.inner_text("#you")
    emit(jarvis, {"type": "response.created", "response": {"id": "r1"}})
    emit(jarvis, {"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": "Il est midi"})
    assert body_state(jarvis) == "live-speaking"
    assert status(jarvis).startswith("JARVIS répond")
    assert jarvis.inner_text("#transcript .line") == "Il est midi"
    assert not jarvis.is_disabled("#interruptBtn")  # speaking: it can be interrupted
    emit(jarvis, {"type": "response.done", "response": {"status": "completed", "output": []}})
    emit(jarvis, {"type": "output_audio_buffer.stopped"})
    assert status(jarvis) == "Je vous écoute…" and body_state(jarvis) == "live-listening"
    assert jarvis.is_disabled("#interruptBtn")
    phases = [p["phase"] for p in jarvis.evaluate("__bus.phase")]
    assert phases == ["user", "thinking", "speaking", "listening"]


def test_a_running_tool_shows_its_label_and_echap_interrupts_it(jarvis):
    go_live(jarvis)
    held = hold_tools(jarvis)
    emit(jarvis, call("get_status", "t1"))
    jarvis.wait_for_function("document.getElementById('statusPill').textContent === 'Je fais le point…'")
    assert body_state(jarvis) == "live-tool"
    assert not jarvis.is_disabled("#interruptBtn")
    until(lambda: held)
    jarvis.evaluate("__sent.length = 0")
    jarvis.keyboard.press("Escape")  # keys.js: a tool runs, so Échap interrupts
    assert jarvis.evaluate("__types()") == ["output_audio_buffer.clear"]
    assert status(jarvis) == "Je vous écoute…"
    held[0].continue_()
    assert "error" not in output(jarvis, "t1")  # still run and answered...
    jarvis.wait_for_timeout(300)
    assert "response.create" not in jarvis.evaluate("__types()")  # ...but JARVIS stays quiet


# ---------------------------------------------------------------- confirmation, end to end

def test_a_complet_task_from_the_voice_waits_for_lancer_then_runs_and_is_told(jarvis):
    from jarvis import tasks
    go_live(jarvis)
    jarvis.evaluate("window.__alerts = 0; __jarvis.bus.on('earcon', e => { if (e.kind === 'alert') __alerts++; }); 0")
    emit(jarvis, call("delegate_to_claude", "d1", COMPLET))
    out = output(jarvis, "d1")
    assert out["status"] == "needs_confirmation" and out["pending_id"]
    card = jarvis.locator(CARD)
    card.wait_for()
    # A hud card: title, the full prompt as plain text, [Lancer] (primary) [Annuler].
    assert card.locator("h3").inner_text().startswith("Confirmation requise")
    jarvis.wait_for_selector(f"{CARD} .confirm-detail:has-text('{COMPLET['prompt']}')")  # pushed by the server
    assert card.locator(".actions button").all_inner_texts() == ["Lancer", "Annuler"]
    assert "primary" in card.locator(".actions button").first.get_attribute("class")
    assert not [t for t in tasks.TASKS.values() if t["profile"] == "complet"]  # nothing runs yet
    assert status(jarvis) == "En attente de votre confirmation"
    # JARVIS asks the question aloud; afterwards the page waits on the card again.
    jarvis_says(jarvis, "Je confie cette tâche à Claude avec accès complet : je lance ?")
    assert jarvis.evaluate("__jarvis.state.phase") == "confirm"
    assert status(jarvis) == "En attente de votre confirmation"
    assert jarvis.evaluate("__alerts") == 1  # the alert sounds once per request
    # Échap has nothing to close or interrupt here: the request stays.
    jarvis.keyboard.press("Escape")
    assert jarvis.locator(CARD).count() == 1
    # Keyboard: the buttons are real buttons.
    jarvis.evaluate("__sent.length = 0")
    with jarvis.expect_request("**/api/pending/*/decide") as req:
        card.locator(".actions button", has_text="Lancer").focus()
        jarvis.keyboard.press("Enter")
    assert req.value.post_data_json == {"decision": "oui"}
    jarvis.wait_for_selector(".card.result[data-state='done']:has-text('Lancé.')")
    started = [t for t in tasks.TASKS.values() if t["profile"] == "complet"]
    assert [t["prompt"] for t in started] == [COMPLET["prompt"]]
    notices = jarvis.evaluate(NOTICES)
    assert any(n.startswith("Monsieur a confirmé à l'écran : Confier à Claude") for n in notices)
    assert jarvis.evaluate("__jarvis.state.phase") == "listening"
    # The task ends (fake claude): delivery.js tells it at the next pause, as data.
    jarvis.wait_for_function("""__sent.some(m => m.item && m.item.role === 'system'
      && m.item.content[0].text.startsWith('Résultat de la tâche "Ranger les téléchargements" (done)'))""",
                             timeout=20_000)
    data = [i["content"][0]["text"] for i in items(jarvis) if i.get("role") == "user"]
    assert any(d.startswith("Données non fiables (Résultat de la tâche « Ranger les téléchargements »)")
               and "<donnees>\nIl fait 18 degres a Lyon.\n</donnees>" in d for d in data)
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    until(lambda: started[0]["id"] not in pending_ids())  # told: acknowledged in the inbox


def test_a_spoken_oui_only_counts_after_the_question(jarvis):
    from jarvis import tasks
    go_live(jarvis)
    emit(jarvis, call("delegate_to_claude", "d2", COMPLET))
    pending = output(jarvis, "d2")["pending_id"]
    jarvis.locator(CARD).wait_for()
    # The model says yes on its own, before monsieur spoke: refused.
    emit(jarvis, call("confirm_action", "c1", {"pending_id": pending, "decision": "oui"}))
    assert output(jarvis, "c1")["error"] == "Confirmation refusée : attendez la réponse de monsieur."
    assert jarvis.locator(CARD).count() == 1
    # Monsieur answers: voice.js reports his turn when he stops speaking...
    emit(jarvis, {"type": "input_audio_buffer.speech_started", "item_id": "u2"})
    with jarvis.expect_response("**/api/voice/turn"):
        emit(jarvis, {"type": "input_audio_buffer.speech_stopped", "item_id": "u2"})
    emit(jarvis, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u2",
                  "transcript": "Oui, vas-y."})
    # ...so the model's confirmation now goes through.
    emit(jarvis, call("confirm_action", "c2", {"pending_id": pending, "decision": "oui"}))
    assert output(jarvis, "c2")["status"] == "started"
    jarvis.wait_for_selector(".card[data-state='done']:has-text('Lancé.')")  # pushed by the server
    assert jarvis.locator(CARD).count() == 0
    assert [t["prompt"] for t in tasks.TASKS.values() if t["profile"] == "complet"] == [COMPLET["prompt"]]


def test_echap_during_the_lock_countdown_only_cancels_the_lock(jarvis, nothing_real):
    go_live(jarvis)
    emit(jarvis, call("system_control", "l1", {"action": "lock_screen"}))
    card = jarvis.locator("#card-lock-countdown")
    card.wait_for()
    assert status(jarvis) == "Commande système…"  # the tool's label while it waits
    jarvis.evaluate("__sent.length = 0")
    jarvis.keyboard.press("Escape")
    assert output(jarvis, "l1") == {"ok": False, "cancelled": True, "error": "Verrouillage annulé par monsieur."}
    # One Échap, one meaning: no interruption of JARVIS on top of it.
    assert "response.cancel" not in jarvis.evaluate("__types()")
    assert "output_audio_buffer.clear" not in jarvis.evaluate("__types()")
    jarvis.wait_for_timeout(3300)
    assert nothing_real == []


# ---------------------------------------------------------------- composer and keys

def test_the_composer_and_the_keyboard_map_work_together(jarvis):
    from jarvis import tasks
    # Standby with nothing on screen: three example chips.
    jarvis.wait_for_function("document.querySelectorAll('#chips .chip.example').length === 3")
    # '?' (keys.js) opens one help card, focused on its first example.
    jarvis.keyboard.press("?")
    jarvis.locator("#card-aide").wait_for()
    assert jarvis.locator("#card-aide").count() == 1
    assert jarvis.evaluate("document.activeElement.classList.contains('aide-ex')")
    jarvis.click("#card-aide .x")
    # '/' focuses the field; Ctrl+J inside it stays there (never Chrome's Downloads).
    jarvis.evaluate("document.activeElement.blur()")
    jarvis.keyboard.press("/")
    assert jarvis.evaluate("document.activeElement.id") == "askInput"
    assert jarvis.input_value("#askInput") == ""  # the slash was not typed
    prevented = jarvis.evaluate("""() => { const e = new KeyboardEvent('keydown', {key: 'j', ctrlKey: true,
      bubbles: true, cancelable: true}); document.getElementById('askInput').dispatchEvent(e); return e.defaultPrevented; }""")
    assert prevented
    # Typed text opens a session and goes through voice.sendText once it is ready.
    jarvis.fill("#askInput", "Quel temps fait-il à Laon ?")
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    said = [i["content"][0]["text"] for i in items(jarvis) if i.get("role") == "user"]
    assert said == ["Quel temps fait-il à Laon ?"]
    assert "Quel temps fait-il" in jarvis.inner_text("#you")
    assert jarvis.locator("#chips .chip.example").count() == 0  # live: no standby examples
    # '/tâche …' goes straight to Claude Code, read-only, without the voice model.
    before = len(jarvis.evaluate("__sent"))
    jarvis.fill("#askInput", "/tâche liste mes fichiers Excel")
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_selector(".toast:has-text('Tâche confiée à Claude')")
    assert [t["profile"] for t in tasks.TASKS.values() if t["prompt"] == "liste mes fichiers Excel"] == ["lecture"]
    assert len(jarvis.evaluate("__sent")) == before


# ---------------------------------------------------------------- wake word, quiet hours, delivery

def test_quiet_hours_hold_a_result_until_the_wake_word_opens_a_session(spied_page):
    from jarvis import inbox
    page = spied_page
    page.wait_for_function("window.__rec && __rec.running")  # the leader page listens
    page.click("#dndToggle")  # « Ne pas déranger 1 h »
    page.wait_for_function("__jarvis.state.quiet === true")
    page.evaluate("__tones.length = 0")
    task = delegate(page, "Météo du jour")
    page.wait_for_function("__jarvis.state.pending === 1", timeout=15_000)
    assert page.is_visible("#badge") and page.evaluate("__tones") == [] and page.evaluate("__spoken") == []
    assert task["task_id"] in pending_ids()  # kept in the inbox: not told yet
    # Monsieur calls JARVIS: his own call still works (and chimes) in quiet hours.
    page.evaluate("__say('Jarvis, quoi de neuf ?', true)")
    page.wait_for_function("__jarvis.state.mode === 'live'")
    assert page.evaluate("__tones")[:2] == [660, 880]  # the wake earcon
    page.wait_for_function("__sent.some(m => m.type === 'response.create')")
    jarvis_says(page, "Rien de particulier, monsieur.")  # then a pause: the result's turn
    page.wait_for_function("""__sent.some(m => m.item && m.item.role === 'system'
      && m.item.content[0].text.startsWith('Résultat de la tâche "Météo du jour"'))""", timeout=10_000)
    said = [i["content"][0]["text"] for i in items(page) if i.get("role") == "user"]
    assert said[0] == "quoi de neuf ?"  # his words first, then the result as data
    assert any("<donnees>" in s for s in said[1:])
    until(lambda: task["task_id"] not in pending_ids())
    assert page.evaluate("__jarvis.state.pending") == 0 and not page.is_visible("#badge")
    inbox.set_dnd(None)


# ---------------------------------------------------------------- screenshots (JARVIS_E2E_SHOTS)

@pytest.mark.skipif(not SHOTS, reason="JARVIS_E2E_SHOTS non défini")
@pytest.mark.parametrize("size", [(1440, 900), (1024, 700)], ids=["1440x900", "1024x700"])
def test_screenshots_of_the_integrated_page(jarvis, size):
    w, h = size
    name = f"{w}x{h}"
    jarvis.set_viewport_size({"width": w, "height": h})
    jarvis.wait_for_function("document.querySelectorAll('#chips .chip.example').length === 3")
    jarvis.wait_for_timeout(400)
    shot(jarvis, f"{name}-1-veille")
    go_live(jarvis)
    jarvis.wait_for_timeout(400)
    shot(jarvis, f"{name}-2-ecoute")
    emit(jarvis, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
                  "transcript": "Jarvis, comment se sont passées les ventes du trimestre ?"})
    emit(jarvis, {"type": "response.created", "response": {"id": "r1"}})
    emit(jarvis, {"type": "output_audio_buffer.started"})
    emit(jarvis, {"type": "response.output_audio_transcript.delta", "item_id": "a1",
                  "delta": "Très bien, monsieur. Le chiffre d'affaires progresse de 4 %, porté par le cidre."})
    jarvis.wait_for_timeout(300)
    shot(jarvis, f"{name}-3-reponse")
    emit(jarvis, {"type": "response.done", "response": {"status": "completed", "output": []}})
    emit(jarvis, {"type": "output_audio_buffer.stopped"})
    held = hold_tools(jarvis)
    emit(jarvis, call("get_status", "t1"))
    jarvis.wait_for_function("document.getElementById('statusPill').textContent === 'Je fais le point…'")
    jarvis.wait_for_timeout(300)
    shot(jarvis, f"{name}-4-outil")
    until(lambda: held)
    held[0].continue_()
    output(jarvis, "t1")
    jarvis.unroute("**/api/tool")
    emit(jarvis, call("delegate_to_claude", "d1", COMPLET))
    jarvis.locator(CARD).wait_for()
    jarvis.wait_for_timeout(300)
    shot(jarvis, f"{name}-5-confirmation")
