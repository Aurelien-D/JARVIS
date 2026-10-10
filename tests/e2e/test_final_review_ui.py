"""The final review's fixes, in Chromium (the server runs in this process):

- the morning briefing's teaser (« appelez-moi quand vous voudrez
  l'entendre ») doesn't acknowledge it: a reload brings its card back and the
  next conversation still tells it, and only then is it acknowledged;
- a reminder replayed from the inbox says when it was due and how late, in
  words that read well (« 2 h 15 », « 2 jours », not « 2880 min »);
- « Quitter JARVIS » ends the voice session (it goes straight to OpenAI and
  would run on, billed), lets the microphone go and closes the window.

Nothing here depends on the time of day: deliveries are made at « now » and
quiet hours are switched off."""
from datetime import datetime

import pytest
from test_delivery import SPIES, no_page_open, open_page, publish, until, wait_ready  # noqa: F401 - fixtures
from test_integration_wave3 import BRIEF, clean  # noqa: F401 - fixture
from test_untrusted_text import go_live

from jarvis import briefing, config, inbox

pytestmark = pytest.mark.e2e

FMT_TIME = "async (t) => (await import('/static/js/strings-fr.js')).fmtTime(new Date(t * 1000))"


def pending_ids():
    return [i["id"] for i in inbox.pending()]


def test_the_briefing_teaser_keeps_it_until_a_conversation_tells_it_even_after_a_reload(
        no_page_open, clean, open_page, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    until(lambda: inbox.dnd_until() is None)
    page = open_page(SPIES)
    payload = briefing.deliver(BRIEF, datetime.now())
    assert payload["capped"] is False and payload["queued"] is False
    page.locator("#cards .card", has_text="Appeler maman").wait_for()
    page.wait_for_function("window.__spoken.length > 0")
    assert page.evaluate("window.__spoken")[0].startswith("Bonjour monsieur. Votre briefing du matin est prêt")
    page.wait_for_function("__jarvis.state.pending === 1")
    page.wait_for_timeout(600)  # the teaser has been said (the fake voice takes 30 ms)
    assert payload["inbox_id"] in pending_ids()  # a teaser is no telling

    # The window reloaded (or closed and opened again): the card is back, the
    # teaser is said again, and it still waits for a conversation.
    page.reload()
    wait_ready(page)
    page.locator("#cards .card", has_text="Appeler maman").wait_for()
    page.wait_for_function("window.__spoken.length > 0")
    assert page.evaluate("window.__spoken") == ["Pendant votre absence : votre briefing du matin est prêt."]
    page.wait_for_function("__jarvis.state.pending === 1")
    page.wait_for_timeout(600)
    assert payload["inbox_id"] in pending_ids()

    # The next conversation tells it, framed as outside data; told in full, it is acknowledged.
    go_live(page)
    page.wait_for_function("__texts().some(t => t.includes('Appeler maman'))", timeout=15_000)
    told = page.evaluate("__texts().find(t => t.includes('Appeler maman'))")
    assert told.startswith("Données non fiables")
    page.evaluate("__emit({type: 'response.created'})")
    page.evaluate("__emit({type: 'response.done', response: {status: 'completed', output: []}})")
    until(lambda: payload["inbox_id"] not in pending_ids())


def test_a_late_reminder_says_when_it_was_due_and_how_late_in_plain_words(
        no_page_open, clean, open_page, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    # Came due while no page was open: one on time, one 2 h 15 late, one missed over a weekend.
    publish("reminder", {"id": "a-l-heure", "title": "Thé", "text": "Thé", "late_minutes": 0})
    publish("reminder", {"id": "en-retard", "title": "Pain", "text": "Sortir le pain", "late_minutes": 135})
    publish("reminder", {"id": "week-end", "title": "Garage", "text": "Rappeler le garage", "late_minutes": 2880})
    created = {i["payload"]["id"]: i["created"] for i in inbox.pending()}
    page = open_page(SPIES)

    def title(text):
        card = page.locator("#cards .card.warning", has_text=text)
        card.wait_for()
        return card.locator("h3 .t").inner_text()

    on_time = page.evaluate(FMT_TIME, created["a-l-heure"])
    assert title("Thé") == f"Rappel ({on_time})"
    due = page.evaluate(FMT_TIME, created["en-retard"] - 135 * 60)
    assert title("Sortir le pain") == f"Rappel de {due} (en retard de 2 h 15)"
    due = page.evaluate(FMT_TIME, created["week-end"] - 2880 * 60)
    assert title("Rappeler le garage") == f"Rappel de {due} (en retard de 2 jours)"

    # Live, the same words (they also go to the voice model).
    publish("reminder", {"id": "live-late", "title": "Courrier", "text": "Poster le courrier", "late_minutes": 2880})
    assert title("Poster le courrier") == "Rappel (en retard de 2 jours)"
    publish("reminder", {"id": "live-min", "title": "Four", "text": "Éteindre le four", "late_minutes": 45})
    assert title("Éteindre le four") == "Rappel (en retard de 45 min)"
    assert "2880" not in page.inner_text("#cards")


def test_quitting_ends_the_voice_session_lets_the_microphone_go_and_closes_the_window(open_page):  # noqa: F811
    page = open_page(SPIES + "window.__closed = 0; window.close = () => { window.__closed++; };")
    page.wait_for_function("window.__rec && __rec.running && __jarvis.state.mode === 'standby'")
    go_live(page)
    page.wait_for_function("__pc.connectionState === 'connected'")
    publish("shutdown", {})  # what server._request_shutdown sends before the streams end
    page.wait_for_function("window.__closed === 1")
    assert page.evaluate("__pc.connectionState") == "closed"  # the OpenAI session is over
    assert page.evaluate("__jarvis.state.mode") == "off"
    assert page.evaluate("__recs.every(r => !r.running)")  # no wake word any more
    # Nothing brings the wake word back in this page.
    page.evaluate("__jarvis.bus.emit('mode', {mode: 'standby'})")
    page.wait_for_timeout(500)
    assert page.evaluate("__recs.every(r => !r.running)")
    assert page.evaluate("window.__pcs") == 1
