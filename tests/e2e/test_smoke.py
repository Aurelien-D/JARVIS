"""Smoke scenarios in a real browser: the voice session, the wake word and the
HUD behave as before the module split. The page is driven through the
window.__jarvis test hook and the fakes in fakes.py."""
import pytest

pytestmark = pytest.mark.e2e


def tool(page, name, args):
    """Run a voice tool on the server, as the voice model would."""
    return page.evaluate("b => __jarvis.api('/api/tool', {method: 'POST', body: b})",
                         {"name": name, "arguments": args})


def emit(page, event):
    page.evaluate("ev => __emit(ev)", event)


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def call(name, call_id, args="{}"):
    return {"type": "response.done", "response": {"output": [
        {"type": "function_call", "name": name, "call_id": call_id, "arguments": args}]}}


# ---------------------------------------------------------------- page

def test_page_loads_as_modules_without_globals(jarvis):
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"
    assert jarvis.evaluate("document.body.dataset.state") == "standby-idle"
    assert "EN VEILLE" in jarvis.inner_text("#statusPill").upper()
    scripts = jarvis.evaluate("[...document.scripts].filter(s => s.src.startsWith(location.origin))"
                              ".map(s => [new URL(s.src).pathname, s.type])")
    assert scripts == [["/static/js/main.js", "module"]]
    # The old classic script leaked everything as globals; modules don't.
    assert jarvis.evaluate("['state', 'connect', 'handleEvent', 'addCard', 'api']"
                           ".every(name => !(name in window))")
    assert jarvis.evaluate("Object.keys(__jarvis).sort()") == ["api", "bus", "settings", "state", "voice"]


# ---------------------------------------------------------------- live session

def test_orb_click_goes_live(jarvis):
    go_live(jarvis)
    assert "EN LIGNE" in jarvis.inner_text("#statusPill").upper()
    assert jarvis.evaluate("__sent.length") == 0  # nothing said on a plain connect
    assert jarvis.evaluate("document.body.dataset.state") == "live-idle"


def test_task_result_is_read_out_while_live(jarvis):
    go_live(jarvis)
    tool(jarvis, "delegate_to_claude", {"title": "Météo", "prompt": "météo ?", "profile": "recherche"})
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    texts = jarvis.evaluate("__texts()")
    assert any(t.startswith('[SYSTEM] Résultat de la tâche "Météo" (done)') for t in texts)


def test_one_response_at_a_time(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__sent.length = 0")
    emit(jarvis, {"type": "response.created"})
    jarvis.evaluate("__jarvis.bus.emit('deliver', {text: 'Rappel test', kind: 'reminder', spoken: 'x'})")
    assert jarvis.evaluate("__types()") == ["message:input_text"]  # waits for the running response
    emit(jarvis, {"type": "response.done", "response": {"output": []}})
    assert jarvis.evaluate("__types()") == ["message:input_text", "response.create"]


def test_server_tool_round_trip(jarvis):
    go_live(jarvis)
    jarvis.evaluate("window.__results = []; __jarvis.bus.on('tool:result', d => __results.push(d)); __sent.length = 0")
    emit(jarvis, {"type": "response.created"})
    emit(jarvis, call("get_status", "k1"))
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    out = jarvis.evaluate("JSON.parse(__sent.find(m => m.item && m.item.type === 'function_call_output').item.output)")
    assert out["now"].count(",") == 1 and "running_tasks" in out
    assert jarvis.evaluate("__results.map(r => [r.name, r.callId])") == [["get_status", "k1"]]


def test_camera_photo_goes_back_as_an_image(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__sent.length = 0")
    emit(jarvis, call("look_at_camera", "k2"))
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert jarvis.evaluate("__types()") == ["function_call_output", "message:input_image", "response.create"]
    assert jarvis.locator(".card img[alt='Caméra']").count() == 1


def test_transcripts_carry_over_to_a_reconnection(jarvis, app_server):
    go_live(jarvis)
    emit(jarvis, {"type": "conversation.item.input_audio_transcription.completed",
                  "transcript": "Retiens que j’adore le jazz"})
    emit(jarvis, {"type": "response.output_audio_transcript.delta", "delta": "Bien noté"})
    emit(jarvis, {"type": "response.output_audio_transcript.done", "transcript": "Bien noté, monsieur."})
    assert "jazz" in jarvis.inner_text("#you")
    assert jarvis.inner_text("#transcript") == "Bien noté"
    pcs = jarvis.evaluate("__pcs")
    jarvis.evaluate("__dc.close()")  # the connection drops
    jarvis.wait_for_function(f"__pcs > {pcs} && __jarvis.state.mode === 'live'")
    recent = app_server.sessions[-1]["session"]["instructions"]
    assert "jazz" in recent and "Bien noté" in recent


def test_connection_error_is_shown(jarvis, monkeypatch):
    from jarvis import realtime

    def refuse(recent=""):
        raise realtime.MintError(502, "OpenAI 401: clé refusée")

    monkeypatch.setattr(realtime, "mint", refuse)
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("document.getElementById('statusPill').textContent.includes('clé refusée')")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"
    assert "ERREUR" in jarvis.inner_text("#statusPill").upper()


def test_end_conversation_goes_back_to_standby(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__sent.length = 0")
    emit(jarvis, call("end_conversation", "k3"))
    jarvis.wait_for_function("__jarvis.state.mode !== 'live'")
    assert "response.create" not in jarvis.evaluate("__types()")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"


def test_result_finished_while_asleep_is_told_on_wake(jarvis):
    tool(jarvis, "delegate_to_claude", {"title": "Actus", "prompt": "actus ?", "profile": "recherche"})
    jarvis.wait_for_function("__jarvis.state.queue.length === 1")
    jarvis.evaluate("__sent.length = 0")
    go_live(jarvis)
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert any("Actus" in t for t in jarvis.evaluate("__texts()"))
    assert jarvis.evaluate("__jarvis.state.queue.length") == 0


def test_idle_session_goes_to_standby(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__jarvis.state.config.idle_minutes = 0.02; __jarvis.state.lastActivity = Date.now() - 5000")
    jarvis.wait_for_function("__jarvis.state.mode !== 'live'", timeout=8000)
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"


def test_typed_text_opens_a_session_and_is_sent(jarvis):
    jarvis.evaluate("__jarvis.voice.sendText('Quelle heure est-il ?')")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert jarvis.evaluate("__texts()") == ["Quelle heure est-il ?"]
    assert "Quelle heure" in jarvis.inner_text("#you")


# ---------------------------------------------------------------- wake word

def test_wake_word_flow(jarvis):
    jarvis.wait_for_function("window.__rec && __rec.running")
    assert "EN VEILLE" in jarvis.inner_text("#statusPill").upper()
    assert "ON" in jarvis.inner_text("#wakeBtn").upper()

    jarvis.evaluate("__say('bonjour tout le monde', true)")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"  # other words are ignored

    jarvis.evaluate("__say('Jarvis', false)")  # an interim result already connects
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    jarvis.evaluate("__say('Jarvis, ouvre Discord', true)")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert jarvis.evaluate("__texts()") == ["ouvre Discord"]  # monsieur's own words
    assert not jarvis.evaluate("__rec.running")  # not listening while live

    jarvis.click("#orbBtn")  # back to sleep
    jarvis.wait_for_function("__jarvis.state.mode === 'standby' && __rec.running")

    jarvis.evaluate("__sent.length = 0; __say('Jarvis', true)")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    texts = jarvis.evaluate("__texts()")
    assert len(texts) == 1 and "Oui, monsieur" in texts[0]  # the name alone: a short greeting
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'standby'")


def test_wake_word_switch_is_remembered(jarvis, reload_jarvis):
    jarvis.click("#wakeBtn")
    assert jarvis.evaluate("__jarvis.state.mode") == "off"
    assert not jarvis.evaluate("__rec.running")
    assert "OFF" in jarvis.inner_text("#wakeBtn").upper()
    reload_jarvis()
    assert jarvis.evaluate("__jarvis.state.mode") == "off"
    jarvis.click("#wakeBtn")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"


# ---------------------------------------------------------------- HUD

def test_cdn_libraries_load(jarvis):
    libs = jarvis.evaluate("({marked: typeof marked?.parse, purify: typeof DOMPurify?.sanitize,"
                           " apex: typeof ApexCharts, grid: typeof gridjs?.Grid})")
    assert libs == {"marked": "function", "purify": "function", "apex": "function", "grid": "function"}


def test_task_card_shows_progress_then_result(jarvis):
    tool(jarvis, "delegate_to_claude", {"title": "Météo Lyon", "prompt": "quel temps à Lyon ?",
                                        "profile": "recherche", "complexity": "simple"})
    jarvis.wait_for_selector(".task .prog:has-text('Recherche web')")
    jarvis.wait_for_selector(".task.done")
    card = jarvis.inner_text(".task")
    assert "Météo Lyon" in card and "18 degres" in card


def test_reminder_card_and_side_panels(jarvis):
    tool(jarvis, "schedule", {"kind": "reminder", "title": "Thé", "text": "Le thé est prêt",
                              "delay_minutes": 0.05})
    jarvis.wait_for_selector("#schedules .item:has-text('Thé')")
    tool(jarvis, "remember", {"fact": "Monsieur habite à Lyon"})
    jarvis.wait_for_selector("#memory .item:has-text('Monsieur habite à Lyon')")
    jarvis.wait_for_selector(".card.warning:has-text('Le thé est prêt')")


def test_display_card_and_report(jarvis):
    jarvis.evaluate("""__jarvis.voice.handleEvent({type: 'response.done', response: {output: [
      {type: 'function_call', name: 'display_card', call_id: 'c1', arguments: JSON.stringify({title: 'Résultat',
         content: '**18 °C** à Lyon\\n- ciel clair\\n- vent faible', kind: 'result'})},
      {type: 'function_call', name: 'display_report', call_id: 'c2', arguments: JSON.stringify({title: 'Ventes',
         kpis: [{label: 'CA', value: '12 480 €', delta: '+12%'}],
         chart: {type: 'bar', categories: ['jan', 'fév', 'mar'], series: [{name: '2026', data: [3, 5, 4]}]},
         table: {columns: ['Mois', 'CA'], rows: [['jan', 3], ['fév', 5], ['mar', 4]]},
         markdown: '## Conclusion\\nEn hausse.'})}]}})""")
    jarvis.wait_for_selector("#report[open] .apexcharts-canvas")
    jarvis.wait_for_selector("#rtable .gridjs-wrapper")
    card = jarvis.inner_html(".card.result .body")
    assert "<strong>18 °C</strong>" in card and "<li>ciel clair</li>" in card
    assert jarvis.text_content("#rtitle") == "Ventes"
    assert "12 480 €" in jarvis.inner_text("#kpis")
    assert jarvis.evaluate("document.activeElement === document.body")  # the report doesn't steal focus
    jarvis.click("#report .rhead .x")
    assert not jarvis.evaluate("document.getElementById('report').open")


def test_card_options(jarvis):
    """hud.addCard(title, content, kind, {id, actions, sticky, ttlMs}): later packages rely on it."""
    jarvis.evaluate("""async () => {
      const hud = await import('/static/js/hud.js');
      hud.addCard('Confirmation requise', 'Lancer ?', 'warning', {id: 'c1', sticky: true,
        actions: [{label: 'Lancer', primary: true, onClick: () => { window.__clicked = true; }}]});
      for (let i = 0; i < 8; i++) hud.addCard('Info ' + i, 'x', 'info');
      hud.addCard('Bref', 'x', 'info', {ttlMs: 300});
    }""")
    assert jarvis.locator("#cards .card").count() == 6
    assert jarvis.locator("#card-c1").count() == 1  # sticky: never evicted
    jarvis.click("#card-c1 .actions button:has-text('Lancer')")
    assert jarvis.evaluate("window.__clicked === true")
    jarvis.evaluate("""async () => (await import('/static/js/hud.js'))
      .addCard('Confirmation requise', 'Mis à jour', 'warning', {id: 'c1', sticky: true})""")
    assert jarvis.locator("#card-c1").count() == 1  # same id: updated in place
    assert jarvis.inner_text("#card-c1 .body") == "Mis à jour"
    assert jarvis.locator("#card-c1 .actions").count() == 0
    jarvis.wait_for_function("![...document.querySelectorAll('#cards h3')].some(h => h.textContent.includes('Bref'))")
    jarvis.evaluate("async () => (await import('/static/js/hud.js')).removeCard('c1')")
    assert jarvis.locator("#card-c1").count() == 0
