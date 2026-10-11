"""Confirmation cards between the paired iPhone and the PC (spec 4.11), both pages
open in one test: a request is launched only where it may be (canLaunch, the
server's own rule); the other device sees [Annuler] and a note. open_url from
the phone comes back as a link card with [Ouvrir le lien], and nothing opens
on the PC. The phone is remote_page (real gate); voice is the fake WebRTC."""
import json

import pytest
from conftest import phone_origin

pytestmark = pytest.mark.e2e

COMPLET = {"title": "Ranger les téléchargements", "prompt": "Range ~/Downloads par type de fichier",
           "profile": "complet"}
CARD = ".card.confirm[data-state='pending']"


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch):
    """Nothing opens, locks or changes the volume on this machine: the list says what would have run."""
    from jarvis import confirm, desktop
    confirm.PENDING.clear()
    ran = []
    monkeypatch.setattr(desktop, "open_target", lambda **kw: ran.append(("open", kw)) or {"ok": True})
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: ran.append((action, value))
                        or {"ok": True})
    yield ran
    confirm.PENDING.clear()


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def call(page, name, call_id, args):
    """The voice model calls a tool in this page's session."""
    page.evaluate("ev => __emit(ev)", {"type": "response.done", "response": {"output": [
        {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(args)}]}})


def output(page, call_id):
    page.wait_for_function(f"__sent.some(m => m.item && m.item.call_id === '{call_id}')")
    return json.loads(page.evaluate(f"__sent.find(m => m.item && m.item.call_id === '{call_id}').item.output"))


def buttons(card):
    return [b.strip() for b in card.locator(".actions button").all_inner_texts()]


def plain(text: str) -> str:
    """The page's French typography (thin spaces before ':' and around « ») as plain spaces."""
    return text.replace("\u202f", " ").replace("\u00a0", " ")


def test_a_pc_action_asked_on_the_phone_launches_from_the_phone_only(remote_page, jarvis, nothing_real):
    from jarvis import confirm
    go_live(remote_page)
    call(remote_page, "system_control", "v1", {"action": "volume_up"})
    asked = output(remote_page, "v1")
    assert asked["status"] == "needs_confirmation"
    assert asked["summary"] == "Agir sur le PC à distance : monter le son du PC ?"
    # The phone: [Lancer] and [Annuler].
    mine = remote_page.locator(CARD)
    mine.wait_for()
    assert buttons(mine) == ["Lancer", "Annuler"] and mine.get_attribute("data-launch") == "yes"
    # The PC: only [Annuler], and where it is launched instead.
    theirs = jarvis.locator(CARD)
    theirs.wait_for()
    assert buttons(theirs) == ["Annuler"] and theirs.get_attribute("data-launch") == "no"
    assert plain(theirs.locator(".confirm-note").inner_text()) == "Demandée depuis l'iPhone : elle se lance sur l'iPhone."
    assert nothing_real == []
    with remote_page.expect_request("**/api/pending/*/decide") as req:
        mine.locator("button", has_text="Lancer").click()
    assert req.value.post_data_json == {"decision": "oui"}
    remote_page.wait_for_selector(".card[data-state='done']")
    assert nothing_real == [("volume_up", None)]
    assert confirm.PENDING[asked["pending_id"]]["via"] == phone_origin()
    # The PC's card hears the outcome from the server.
    jarvis.wait_for_selector(".card[data-state='done']")
    assert jarvis.locator(CARD).count() == 0


def test_a_request_asked_on_the_pc_shows_only_annuler_on_the_phone(remote_page, jarvis):
    from jarvis import tasks
    go_live(jarvis)
    call(jarvis, "delegate_to_claude", "c1", COMPLET)
    assert output(jarvis, "c1")["status"] == "needs_confirmation"
    pc_card = jarvis.locator(CARD)
    pc_card.wait_for()
    assert buttons(pc_card) == ["Lancer", "Annuler"]
    phone_card = remote_page.locator(CARD)
    phone_card.wait_for()
    assert buttons(phone_card) == ["Annuler"]
    assert plain(phone_card.locator(".confirm-note").inner_text()) == "Demandée sur le PC : elle se lance sur le PC."
    # Its side panel says the same: no Lancer on the waiting task.
    waiting = remote_page.locator("article.task.waiting")
    waiting.wait_for(state="attached")
    assert [b.get_attribute("data-action") for b in waiting.locator(".actions button").all()] == ["dismiss"]
    assert plain(waiting.locator(".prog").text_content()) == "Demandée sur le PC : elle se lance sur le PC."
    # The phone may cancel it.
    phone_card.locator("button", has_text="Annuler").click()
    remote_page.wait_for_selector(".card[data-state='cancelled']")
    jarvis.wait_for_selector(".card[data-state='cancelled']")
    assert not [t for t in tasks.TASKS.values() if t["profile"] == "complet"]


def test_open_url_from_the_phone_is_a_link_card_and_nothing_opens_on_the_pc(remote_page, jarvis, nothing_real):
    from jarvis import confirm
    remote_page.evaluate("() => { window.__opened = []; window.open = (...a) => { __opened.push(a); return null; }; }")
    go_live(remote_page)
    call(remote_page, "open_url", "u1", {"url": "https://example.org/page?x=1"})
    sent = output(remote_page, "u1")
    assert sent["opened"] is False and sent["link"] == "https://example.org/page?x=1"
    card = remote_page.locator(".card[data-link='yes']")
    card.wait_for()
    assert card.locator(".body").inner_text().strip() == "example.org"
    assert buttons(card) == ["Ouvrir le lien"]
    # No « Ouverture de … » card beside it: nothing opens on the PC from the phone.
    assert remote_page.locator(".card", has_text="Ouverture de").count() == 0
    card.locator("button", has_text="Ouvrir le lien").click()
    assert remote_page.evaluate("__opened") == [["https://example.org/page?x=1", "_blank", "noopener,noreferrer"]]
    # After outside content: the whole address and a warning, as text.
    confirm.mark_tainted(remote_page.evaluate("__jarvis.voice.sessionId()"), "Page web")
    hostile = "https://evil.example/<img src=x onerror=alert(1)>" + "a" * 400
    call(remote_page, "open_url", "u2", {"url": hostile})
    assert output(remote_page, "u2")["tainted"] is True
    tainted = remote_page.locator(".card[data-link='yes']", has_text="evil.example")
    tainted.wait_for()
    text = plain(tainted.locator(".body").inner_text())
    assert "evil.example" in text and "Lien proposé après des données externes : vérifiez-le." in text
    assert tainted.locator(".body img").count() == 0 and tainted.locator(".confirm-detail").inner_text() == \
        hostile[:300]
    # Nothing opened on the PC, and the PC page shows no link card.
    assert nothing_real == [] and jarvis.locator(".card[data-link='yes']").count() == 0
