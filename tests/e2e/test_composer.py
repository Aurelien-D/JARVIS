"""Writing to JARVIS (static/js/composer.js): the text field, the "/tâche"
shortcut, the help card, the suggestion chips, the rotating hint and the
first-session introduction."""
import pytest

import voice_helpers
from voice_helpers import emit, go_live, items, record_requests, types

clock_jarvis = voice_helpers.clock_jarvis  # the page whose clock the test controls (a fixture)

pytestmark = pytest.mark.e2e

INTRO = "Première session : présente-toi en deux phrases et propose trois exemples de demandes."


def ask(page, text):
    page.fill("#askInput", text)
    page.press("#askInput", "Enter")


def texts(page):
    return page.evaluate("__texts()")


# ---------------------------------------------------------------- the text field

def test_field_is_rendered_as_specified(jarvis):
    field = jarvis.locator("form#ask input#askInput")
    assert field.get_attribute("placeholder") == "Écrivez à JARVIS… (Ctrl+J)"
    assert field.get_attribute("aria-label") == "Message pour JARVIS"
    assert field.get_attribute("autocomplete") == "off"
    assert jarvis.locator("form#ask button[type=submit]").get_attribute("aria-label") == "Envoyer"
    # The skip link lands on it.
    assert jarvis.get_attribute("a.skip", "href") == "#askInput"


def test_typed_question_while_live_is_sent_then_answered(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__sent.length = 0")
    ask(jarvis, "quelle heure est-il")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert types(jarvis) == ["message:input_text", "response.create"]
    item = items(jarvis)[0]
    assert item["role"] == "user" and item["content"][0] == {"type": "input_text", "text": "quelle heure est-il"}
    assert jarvis.input_value("#askInput") == ""


def test_typed_question_in_standby_waits_for_the_session(jarvis):
    jarvis.evaluate("__holdSessionCreated = true")
    ask(jarvis, "quelle heure est-il")
    jarvis.wait_for_function("window.__dc && __dc.readyState === 'open'")
    jarvis.wait_for_timeout(300)
    assert jarvis.evaluate("__jarvis.state.mode") == "connecting"
    assert jarvis.evaluate("__sent.length") == 0
    emit(jarvis, {"type": "session.created", "session": {"id": "sess_1"}})
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert texts(jarvis) == ["quelle heure est-il"]
    assert types(jarvis) == ["message:input_text", "response.create"]


def test_slash_tache_goes_straight_to_claude(jarvis):
    bodies = record_requests(jarvis, "**/api/tasks", answer={"id": "t1", "status": "running"})
    ask(jarvis, "/tâche cherche X")
    jarvis.wait_for_selector("#toasts .toast:has-text('Tâche confiée à Claude')")
    assert bodies == [{"prompt": "cherche X", "profile": "lecture"}]
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"
    assert jarvis.evaluate("window.__pcs") is None  # no voice session, no OpenAI cost
    ask(jarvis, "/tache")  # nothing to do: says how
    jarvis.wait_for_selector("#toasts .toast:has-text('/tâche compare')")
    assert len(bodies) == 1


def test_refused_task_says_why(jarvis):
    record_requests(jarvis, "**/api/tasks", answer={"detail": "La consigne de la tâche est vide."}, status=400)
    ask(jarvis, "/tâche ?")
    jarvis.wait_for_selector("#toasts .toast:has-text('La consigne de la tâche est vide.')")


def test_long_paste_is_summarised(jarvis):
    go_live(jarvis)
    jarvis.evaluate("__sent.length = 0")
    ask(jarvis, "mot " * 600)
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert texts(jarvis)[0].startswith("Résume ce texte :\nmot mot")


def test_ctrl_j_and_slash_focus_the_field(jarvis):
    jarvis.keyboard.press("Control+j")
    assert jarvis.evaluate("document.activeElement.id") == "askInput"
    jarvis.press("#askInput", "Escape")  # Échap leaves the field
    assert jarvis.evaluate("document.activeElement.id") != "askInput"
    jarvis.keyboard.press("/")
    assert jarvis.evaluate("document.activeElement.id") == "askInput"
    assert jarvis.input_value("#askInput") == ""  # the slash itself is not typed


def test_history_comes_back_with_the_arrows(jarvis):
    go_live(jarvis)
    ask(jarvis, "première")
    ask(jarvis, "deuxième")
    jarvis.fill("#askInput", "brouillon")
    jarvis.press("#askInput", "ArrowUp")
    assert jarvis.input_value("#askInput") == "deuxième"
    jarvis.press("#askInput", "ArrowUp")
    assert jarvis.input_value("#askInput") == "première"
    jarvis.press("#askInput", "ArrowDown")
    jarvis.press("#askInput", "ArrowDown")
    assert jarvis.input_value("#askInput") == "brouillon"
    saved = jarvis.evaluate("JSON.parse(localStorage.getItem('jarvis.composer.history'))")
    assert saved[-2:] == ["première", "deuxième"]


# ---------------------------------------------------------------- help

def test_help_lists_what_jarvis_can_do_and_an_example_asks_it(jarvis):
    jarvis.keyboard.press("?")
    card = jarvis.locator("#cards .card:has-text('Ce que je sais faire')")
    card.wait_for()
    # A.R.E.S is off in the harness: its examples only show when it is on (test_integration_wave2).
    assert card.locator(".aide-cat h4").all_text_contents() == [
        "Applications et PC", "Rappels et routines", "Recherche et fichiers", "Météo et actualités",
        "Vision", "Mémoire et journal", "Point du jour"]
    # The global hotkey comes from Réglages › Système (/api/config), after the page's own keys.
    assert card.locator(".aide-keys").inner_text().replace("\n", " · ") == jarvis.evaluate(
        "import('/static/js/strings-fr.js').then(m => m.T.help.shortcuts + ' · ' + "
        "m.T.help.globalKey(__jarvis.state.config.hotkey.combo))")
    assert jarvis.evaluate("__jarvis.state.config.hotkey.combo")  # 'Ctrl+Alt+Maj+J' by default
    assert jarvis.evaluate("document.activeElement.className") == "aide-ex"  # opened from the keyboard
    example = card.locator("button.aide-ex", has_text="Baisse le volume")
    phrase = example.inner_text()
    example.click()
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert texts(jarvis) == [phrase]


def test_help_opens_from_the_aide_button_and_the_bus(jarvis):
    jarvis.click("#topActions button:has-text('Aide')")
    jarvis.wait_for_selector("#cards .card:has-text('Ce que je sais faire')")
    jarvis.evaluate("__jarvis.bus.emit('ui:open', 'aide')")
    jarvis.evaluate("__jarvis.bus.emit('ui:open', {panel: 'aide'})")
    assert jarvis.locator("#cards .card:has-text('Ce que je sais faire')").count() == 1  # updated in place


# ---------------------------------------------------------------- chips

def test_standby_suggests_three_examples(jarvis):
    chips = jarvis.locator("#chips button.chip")
    assert chips.count() == 3
    assert jarvis.get_attribute("#chips", "role") == "group"
    phrase = chips.nth(1).inner_text()
    chips.nth(1).click()
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert texts(jarvis) == [phrase]
    assert jarvis.locator("#chips button").count() == 0  # live: no examples


def test_examples_make_room_for_cards(jarvis):
    assert jarvis.locator("#chips button.chip").count() == 3
    jarvis.evaluate("import('/static/js/hud.js').then(h => h.addCard('Info', 'x', 'info', {id: 'i1'}))")
    jarvis.wait_for_function("document.querySelectorAll('#chips button').length === 0")
    jarvis.evaluate("import('/static/js/hud.js').then(h => h.removeCard('i1'))")
    jarvis.wait_for_function("document.querySelectorAll('#chips button').length === 3")


def test_task_result_offers_follow_ups(jarvis):
    go_live(jarvis)
    jarvis.evaluate("""__jarvis.bus.emit('server:task', {id: 'tk1', title: 'Météo Lyon', status: 'done',
      output: 'Il fait 18 degrés.'})""")
    chips = jarvis.locator("#chips button.chip")
    assert chips.all_inner_texts() == ["Continuer la tâche", "Afficher en tableau", "Merci, c'est tout"]
    chips.first.click()
    assert jarvis.input_value("#askInput") == "Suite de « Météo Lyon » : "
    assert jarvis.evaluate("document.activeElement.id") == "askInput"
    assert jarvis.locator("#chips button").count() == 0
    # Another result (read out by delivery.js meanwhile), then "Afficher en tableau" asks for it.
    jarvis.evaluate("__jarvis.bus.emit('server:task', {id: 'tk2', title: 'Ventes', status: 'done'})")
    for _ in range(2):  # both results told
        emit(jarvis, {"type": "response.done", "response": {"status": "completed", "output": []}})
    jarvis.evaluate("__sent.length = 0")
    jarvis.click("#chips button:has-text('Afficher en tableau')")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert texts(jarvis) == ["Affiche le résultat de « Ventes » en tableau."]


def test_compose_event_fills_or_sends(jarvis):
    jarvis.evaluate("__jarvis.bus.emit('ui:compose', {text: 'Suite de « X » : '})")
    assert jarvis.input_value("#askInput") == "Suite de « X » : "
    jarvis.evaluate("__jarvis.bus.emit('ui:compose', {text: 'Bonjour', submit: true})")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    assert texts(jarvis) == ["Bonjour"]


# ---------------------------------------------------------------- hint

def test_hint_rotates_in_standby_and_stays_while_live(clock_jarvis):
    page = clock_jarvis
    first = page.inner_text("#hint")
    assert first.startswith("Essayez : «") or first.startswith("Essayez : «")
    page.clock.fast_forward(8000)
    second = page.inner_text("#hint")
    assert second != first
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")
    live = page.inner_text("#hint")
    page.clock.fast_forward(8000)
    page.clock.fast_forward(8000)
    assert page.inner_text("#hint") == live


# ---------------------------------------------------------------- first session

def test_first_session_introduces_itself_once(jarvis):
    jarvis.evaluate("localStorage.removeItem('jarvis.onboarded.voice')")
    go_live(jarvis)
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    notice = items(jarvis)[0]
    assert notice["role"] == "system" and notice["content"][0]["text"] == INTRO
    assert types(jarvis) == ["message:input_text", "response.create"]
    assert jarvis.evaluate("localStorage.getItem('jarvis.onboarded.voice')") == "1"
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'standby'")
    jarvis.evaluate("__sent.length = 0")
    go_live(jarvis)
    jarvis.wait_for_timeout(300)
    assert jarvis.evaluate("__sent.length") == 0


def test_first_session_with_a_typed_question_answers_once(jarvis):
    """The introduction comes before the question, in the same response."""
    jarvis.evaluate("localStorage.removeItem('jarvis.onboarded.voice')")
    ask(jarvis, "quelle heure est-il")
    jarvis.wait_for_function("__sent.some(m => m.type === 'response.create')")
    jarvis.wait_for_timeout(300)
    assert texts(jarvis) == [INTRO, "quelle heure est-il"]
    assert [i["role"] for i in items(jarvis)] == ["system", "user"]
    assert types(jarvis).count("response.create") == 1
