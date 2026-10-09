"""The Journal drawer, the forget 'Annuler' toast and the A.R.E.S agenda in
Chromium (static/js/journal.js, static/js/ares.js), with a fake A.R.E.S MCP
server (tests/fake_ares.py): turns reach the server and survive a reload,
the last batch leaves on pagehide with a keepalive fetch carrying the token,
the drawer works from the keyboard and every text in it stays inert."""
import json
import time
from datetime import date

import pytest
from fake_ares import FakeAres

pytestmark = pytest.mark.e2e

EVIL = "garage <img src=x onerror=\"window.__pwned='journal'\"> <b>gras</b> [x](javascript:window.__pwned='lien')"


def emit(page, event):
    page.evaluate("ev => __emit(ev)", event)


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def call(name, call_id, args=None):
    return {"type": "response.done", "response": {"output": [
        {"type": "function_call", "name": name, "call_id": call_id, "status": "completed",
         "arguments": json.dumps(args or {})}]}}


def said(page, item_id, text):
    emit(page, {"type": "response.output_audio_transcript.done", "item_id": item_id, "transcript": text})


def heard(page, item_id, text):
    emit(page, {"type": "conversation.item.input_audio_transcription.completed", "item_id": item_id,
                "transcript": text})


def stored(n, timeout=6.0):
    """Wait until the server's journal for today holds n turns; returns them.
    (The server's own lines are left out: a task cancelled at the end of the
    previous test may still note itself.)"""
    from jarvis import journal
    end = time.time() + timeout
    rows = []
    while time.time() < end:
        rows = [r for r in journal.read_day(date.today().isoformat()) if r["role"] != "system"]
        if len(rows) >= n:
            return rows
        time.sleep(0.1)
    raise AssertionError(f"journal : {len(rows)} entrées au lieu de {n} : {rows}")


def open_journal(page):
    page.click("#topActions [aria-controls=journalDrawer]")
    page.wait_for_selector("#journalDrawer:not([hidden])")


@pytest.fixture(autouse=True)
def fresh_journal():
    from jarvis import journal
    journal._seen.clear()
    journal._seen_set.clear()
    yield


@pytest.fixture
def fake_ares(monkeypatch):
    from jarvis import ares, config
    server = FakeAres()

    def reset():
        ares._state.update(protocol=None, session=None, version=None, tools=None,
                           down_until=0.0, probed=0.0, up=False)
        ares._agenda.update(text="", at=0.0, ok=False)
        ares._last_published.update(available=None, lines=None)

    reset()
    monkeypatch.setattr(config, "ARES", "auto")
    monkeypatch.setattr(config, "ARES_URL", server.url)
    server.reset = reset
    yield server
    if ares._refresher:
        ares._refresher.join(5)
    server.stop()
    reset()


# ---------------------------------------------------------------- the journal

def test_three_turns_survive_a_reload(jarvis, reload_jarvis):
    go_live(jarvis)
    heard(jarvis, "u1", "Qu'est-ce que j'ai aujourd'hui ?")
    said(jarvis, "a1", "Vous devez appeler le labo à 14 h, monsieur.")
    jarvis.fill("#askInput", "Note que je dois rappeler le garage")
    jarvis.press("#askInput", "Enter")
    rows = stored(3)  # sent 2 s after the last turn
    assert [(r["role"], r["source"]) for r in rows] == [("user", "voice"), ("jarvis", "voice"), ("user", "text")]

    reload_jarvis()
    # The thread of the conversation is back for the next session...
    jarvis.wait_for_function("__jarvis.state.history.length === 3")
    history = jarvis.evaluate("__jarvis.state.history.map(h => [h.role, h.text])")
    assert history == [["monsieur", "Qu'est-ce que j'ai aujourd'hui ?"],
                       ["JARVIS", "Vous devez appeler le labo à 14 h, monsieur."],
                       ["monsieur", "Note que je dois rappeler le garage"]]
    # ...and the drawer shows the three turns.
    open_journal(jarvis)
    jarvis.wait_for_function("document.querySelectorAll('#journalLog .jr-row:not(.system)').length === 3")
    texts = jarvis.locator("#journalLog .jr-row:not(.system) .jr-txt").all_inner_texts()
    assert texts == ["Qu'est-ce que j'ai aujourd'hui ?", "Vous devez appeler le labo à 14 h, monsieur.",
                     "Note que je dois rappeler le garage"]
    assert jarvis.locator("#journalLog .jr-row:not(.system) .jr-who").all_inner_texts() == ["Vous", "JARVIS", "Vous"]
    log = jarvis.locator("#journalDrawer [role=log]")
    assert log.get_attribute("aria-live") == "polite" and log.get_attribute("aria-relevant") == "additions"


def test_pagehide_sends_a_keepalive_fetch_with_the_token_header(jarvis):
    go_live(jarvis)
    jarvis.evaluate("""() => {
      window.__fetches = [];
      const real = window.fetch;
      window.fetch = (url, opts = {}) => { __fetches.push({ url: String(url), opts }); return real(url, opts); };
    }""")
    heard(jarvis, "u1", "Dernière phrase avant de partir")
    jarvis.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }))")
    sent = jarvis.evaluate("__fetches.filter(f => f.url.includes('/api/journal'))")
    assert len(sent) == 1
    opts = sent[0]["opts"]
    token = jarvis.get_attribute('meta[name="jarvis-token"]', "content")
    assert opts["keepalive"] is True and opts["method"] == "POST"
    assert opts["headers"]["X-Jarvis-Token"] == token  # a header, never ?token= in a beacon URL
    assert "token" not in sent[0]["url"]
    assert json.loads(opts["body"])["entries"][0]["text"] == "Dernière phrase avant de partir"
    assert stored(1)[0]["text"] == "Dernière phrase avant de partir"
    jarvis.wait_for_timeout(2300)  # nothing is sent twice afterwards
    assert len(stored(1)) == 1


def test_the_drawer_from_the_keyboard_and_search(jarvis):
    from jarvis import journal
    now = time.time()
    journal.append([{"ts": now - 30, "role": "user", "text": "Appelle le Garage Martin"},
                    {"ts": now - 20, "role": "jarvis", "text": "C'est noté, monsieur."},
                    {"ts": now - 10, "role": "user", "text": "La voiture est garagé ce soir"}])
    button = jarvis.locator("#topActions [aria-controls=journalDrawer]")
    assert button.is_visible()  # shown once journal.js announced itself (ui:ready)
    button.focus()
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_selector("#journalDrawer:not([hidden])")
    assert button.get_attribute("aria-expanded") == "true"
    assert jarvis.evaluate("document.activeElement.id") == "journalSearch"
    jarvis.wait_for_function("document.querySelectorAll('#journalLog .jr-row:not(.system)').length === 3")
    assert jarvis.locator("#journalDay option").first.inner_text() == "Aujourd'hui"
    jarvis.keyboard.type("GARAGÉ")
    jarvis.wait_for_function("document.querySelectorAll('#journalLog .jr-row:not(.system)').length === 2")
    assert jarvis.locator("#journalLog .jr-row:not(.system) mark").all_inner_texts() == ["Garage", "garagé"]
    assert "2 résultats" in jarvis.inner_text("#journalStatus")
    jarvis.keyboard.press("Tab")  # the scrolling log takes the focus
    assert jarvis.evaluate("document.activeElement.id") == "journalLog"
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_selector("#journalDrawer", state="hidden")
    assert button.get_attribute("aria-expanded") == "false"
    assert jarvis.evaluate("document.activeElement === document.querySelector('#topActions [aria-controls=journalDrawer]')")
    # The ✕ closes it too, and the button toggles it.
    button.click()
    jarvis.wait_for_selector("#journalDrawer:not([hidden])")
    jarvis.click("#journalDrawer .jr-head .x")
    jarvis.wait_for_selector("#journalDrawer", state="hidden")


def test_clearing_the_history_asks_first(jarvis):
    from jarvis import journal
    journal.append([{"ts": time.time() - 5, "role": "user", "text": "Un secret"}])
    open_journal(jarvis)
    jarvis.wait_for_selector("#journalLog .jr-row")
    jarvis.click(".jr-clear")
    assert jarvis.evaluate("document.activeElement.textContent") == "Annuler"  # the safe choice first
    jarvis.click(".jr-confirm >> text=Annuler")
    assert journal.days() == [date.today().isoformat()]
    jarvis.click(".jr-clear")
    jarvis.click(".jr-confirm >> text=Oui, tout effacer")
    jarvis.wait_for_selector("#journalLog .jr-empty")
    assert jarvis.inner_text("#journalLog .jr-empty") == "Rien dans le journal aujourd'hui."
    assert journal.days() == []
    assert jarvis.evaluate("__jarvis.state.history.length") == 0


def test_journal_text_stays_text(jarvis):
    go_live(jarvis)
    heard(jarvis, "u1", EVIL)
    stored(1)
    open_journal(jarvis)
    jarvis.wait_for_selector("#journalLog .jr-row")
    assert jarvis.locator("#journalDrawer img, #journalDrawer a, #journalDrawer b").count() == 0
    assert "<img" in jarvis.inner_text("#journalLog .jr-txt")
    jarvis.fill("#journalSearch", "garage")
    jarvis.wait_for_selector("#journalLog mark")
    assert jarvis.locator("#journalDrawer img").count() == 0
    assert jarvis.evaluate("window.__pwned === undefined")


def test_a_voice_forget_can_be_undone_from_a_toast(jarvis):
    from jarvis import memory
    memory.remember("Le code du portail est 4321")
    go_live(jarvis)
    emit(jarvis, call("forget", "c1", {"query": "portail"}))
    jarvis.wait_for_selector("#toasts .toast >> text=Souvenir oublié")
    assert memory.facts() == []
    jarvis.click("#toasts .toast button")
    jarvis.wait_for_selector("#toasts .toast >> text=Souvenir rétabli.")
    assert [f["text"] for f in memory.facts()] == ["Le code du portail est 4321"]
    notices = jarvis.evaluate("__sent.filter(m => m.item && m.item.role === 'system').map(m => m.item.content[0].text)")
    assert any("annulé l'oubli" in n for n in notices)  # the model learns it is back
    memory.forget("portail")


# ---------------------------------------------------------------- A.R.E.S

def test_agenda_chip_and_a_card_for_each_write(jarvis, reload_jarvis, fake_ares):
    fake_ares.tasks[0]["title"] = "Appeler le labo <img src=x onerror=\"window.__pwned='agenda'\">"
    reload_jarvis()
    jarvis.wait_for_selector("#agenda:not([hidden]) .agenda-item")
    items = jarvis.locator("#agenda .agenda-item .txt").all_inner_texts()
    assert items[0].startswith("Appeler le labo <img")
    assert jarvis.locator("#agenda img").count() == 0
    assert jarvis.locator("#agenda .agenda-item.late .when").inner_text() == "En retard (2 j)"
    assert jarvis.text_content("#agenda summary h2") == "Agenda A.R.E.S · 2"  # uppercase by CSS only
    chip = jarvis.locator("#aresChip")
    assert chip.is_visible() and "A.R.E.S ●" in chip.inner_text()
    assert jarvis.inner_text("#aresChip .visually-hidden") == "A.R.E.S joignable"
    # A write by voice: a card says what was written, and the agenda follows.
    go_live(jarvis)
    emit(jarvis, call("ares_ajouter", "c1", {"type": "tache", "titre": "Vidange", "quand": "2026-11-02"}))
    jarvis.wait_for_selector("#cards .card.result >> text=Tâche « Vidange » créée")
    assert jarvis.inner_text("#cards .card.result h3") == "A.R.E.S"
    jarvis.wait_for_function("document.querySelectorAll('#agenda .agenda-item').length === 3")
    assert fake_ares.calls("create_task")[-1][1]["dueAt"] == "2026-11-02T09:00"
    assert jarvis.evaluate("window.__pwned === undefined")
    # A.R.E.S gone: the section hides, the chip says ○.
    fake_ares.stop()
    fake_ares.reset()
    reload_jarvis()
    jarvis.wait_for_function("document.querySelector('#aresChip').textContent.includes('○')")
    assert jarvis.locator("#agenda").is_hidden()
    assert jarvis.inner_text("#aresChip .visually-hidden") == "A.R.E.S injoignable"


def test_ares_off_shows_nothing(jarvis):
    from jarvis import config
    assert config.ARES == "off"  # the harness never reaches monsieur's own A.R.E.S
    jarvis.wait_for_timeout(300)
    assert jarvis.locator("#agenda").is_hidden()
    assert jarvis.locator("#aresChip").is_hidden()


# ---------------------------------------------------------------- accessibility

def test_no_serious_axe_issue_with_the_drawer_open_and_the_agenda(jarvis, reload_jarvis, fake_ares):
    from test_a11y import axe_violations
    from jarvis import journal
    now = time.time()
    journal.append([{"ts": now - 20, "role": "user", "text": "Appelle le garage"},
                    {"ts": now - 10, "role": "jarvis", "text": "C'est noté."},
                    {"ts": now - 5, "role": "system", "text": "Tâche lancée : « Devis »"}])
    reload_jarvis()
    jarvis.wait_for_selector("#agenda:not([hidden]) .agenda-item")
    open_journal(jarvis)
    jarvis.wait_for_function("document.querySelectorAll('#journalLog .jr-row').length >= 3")
    jarvis.fill("#journalSearch", "garage")
    jarvis.wait_for_selector("#journalLog mark")
    assert axe_violations(jarvis) == []
    jarvis.click(".jr-clear")  # the confirmation row too
    assert axe_violations(jarvis) == []
