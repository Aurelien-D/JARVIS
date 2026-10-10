"""The pairing page (spec 4.15) in Chromium, as the unpaired iPhone sees it
through the Serve listener: every state of page.PAIR_STATES, the Home Screen
explanation on iOS with « Utiliser JARVIS dans Safari », then a request, its
code, the 2 s poll and « Associé ! » before JARVIS opens.

The remote core's routes (pair-request, pair-status) are faked with
page.route until it is merged; the integrator removes those fakes then."""
import json
import re

import pytest

pytestmark = pytest.mark.e2e

CODE = "4821"
TEXTS = {
    "closed": ("Association fermée", "Associer un iPhone, puis touchez Réessayer"),
    "off": ("Accès à distance coupé", "L'accès à distance est coupé sur le PC"),
    "paused": ("Accès à distance en pause", "Il reprendra seul à la fin de la pause"),
    "refused": ("Accès refusé", "Ouvrez JARVIS depuis son icône"),
    "locked": ("Accès bloqué", "réessayez dans 15 minutes"),
    "revoked": ("Appareil retiré", "Cet appareil a été retiré sur le PC"),
}


def ready(page):
    page.wait_for_function("window.__pair && window.__pair.ready")


def norm(text):
    """The page sets French typography (narrow no-break spaces): compare with plain spaces."""
    return re.sub(r"[  ]", " ", text or "")


class FakePairing:
    """POST /api/remote/pair-request and GET /api/remote/pair-status, as the remote core answers."""

    def __init__(self, page, statuses=("waiting", "approved"), request_error=None):
        self.page = page
        self.statuses = list(statuses)
        self.request_error = request_error
        self.requests, self.polls = [], 0
        page.route("**/api/remote/pair-request", self.pair_request)
        page.route("**/api/remote/pair-status", self.pair_status)

    def pair_request(self, route):
        self.requests.append(json.loads(route.request.post_data or "{}"))
        if self.request_error:
            status, detail = self.request_error
            route.fulfill(status=status, json={"detail": detail})
            return
        route.fulfill(json={"request_id": "r_0123456789ab", "code": CODE, "expires_in": 600})

    def pair_status(self, route):
        self.polls += 1
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        route.fulfill(json={"status": status, "code": CODE})


@pytest.mark.parametrize("remote_page", [{"pair_state": s} for s in TEXTS], indirect=True,
                         ids=list(TEXTS))
def test_every_refusal_state_says_why_with_a_retry(remote_page):
    ready(remote_page)
    state = remote_page.evaluate("window.__pair.state")
    title, sentence = TEXTS[state]
    assert norm(remote_page.text_content(".pair-title")) == title
    assert sentence in norm(remote_page.text_content(".pair-text"))
    assert remote_page.title() == "JARVIS"
    # The page never holds a token nor loads the app.
    assert remote_page.evaluate("!document.querySelector('meta[name=jarvis-token]') && !window.__jarvis")
    with remote_page.expect_navigation():
        remote_page.click("button:has-text('Réessayer')")
    ready(remote_page)
    assert remote_page.evaluate("window.__pair.state") == state


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": True}], indirect=True)
def test_an_iphone_outside_the_home_screen_is_told_how_to_add_it(remote_page):
    ready(remote_page)
    steps = [norm(t) for t in remote_page.eval_on_selector_all(".pair-steps li", "els => els.map(e => e.textContent)")]
    assert len(steps) == 4
    assert "Partager" in steps[0] and "Sur l'écran d'accueil" in steps[1]
    assert "« Ouvrir comme app web »" in steps[2] and "Ajouter" in steps[2]
    assert "Ouvrez l'icône JARVIS" in steps[3]
    assert remote_page.locator("#pairName").count() == 0
    remote_page.click("button:has-text('Utiliser JARVIS dans Safari')")
    remote_page.wait_for_selector("#pairName")
    assert remote_page.input_value("#pairName") == "iPhone"
    assert remote_page.eval_on_selector("#pairName", "e => getComputedStyle(e).fontSize") == "16px"  # no iOS zoom
    assert remote_page.is_visible("button:has-text(\"Demander l'accès\")")


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": True}], indirect=True)
def test_the_home_screen_app_shows_the_form_at_once(remote_page):
    remote_page.add_init_script("Object.defineProperty(navigator, 'standalone', {get: () => true})")
    remote_page.reload()
    ready(remote_page)
    assert remote_page.locator(".pair-steps").count() == 0
    assert remote_page.is_visible("#pairName")


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": False}], indirect=True)
def test_request_code_poll_then_associated_and_jarvis_opens(remote_page, app_server):
    fake = FakePairing(remote_page, statuses=("waiting", "approved"))
    reloads = []

    def app(route):  # the next load of "/" is JARVIS itself (the device cookie is set by then)
        reloads.append(route.request.url)
        route.fulfill(content_type="text/html", body="<!doctype html><title>JARVIS</title><p id='app'>JARVIS</p>")
    remote_page.route(app_server.remote_url, app)
    ready(remote_page)
    remote_page.fill("#pairName", "iPhone de test")
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector(".pair-code")
    assert remote_page.text_content(".pair-code") == CODE
    assert "Sur le PC, vérifiez que le même code s'affiche, puis cliquez Autoriser." in norm(
        remote_page.text_content(".pair-body"))
    assert fake.requests == [{"name": "iPhone de test"}]
    remote_page.wait_for_selector(".pair-title:has-text('Associé')", timeout=8000)
    assert fake.polls == 2  # every 2 s: waiting, then approved
    remote_page.wait_for_selector("#app", timeout=5000)
    assert reloads == [app_server.remote_url]


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": False}], indirect=True)
@pytest.mark.parametrize("status, words", [("denied", "refusée sur le PC"), ("expired", "La demande a expiré")])
def test_denied_or_expired_offers_recommencer(remote_page, status, words):
    fake = FakePairing(remote_page, statuses=(status,))
    ready(remote_page)
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector("button:has-text('Recommencer')", timeout=6000)
    assert words in norm(remote_page.text_content(".pair-body"))
    polls = fake.polls
    remote_page.wait_for_timeout(2500)
    assert fake.polls == polls  # the poll stopped
    remote_page.click("button:has-text('Recommencer')")
    remote_page.wait_for_selector("#pairName")
    assert remote_page.is_enabled("button:has-text(\"Demander l'accès\")")


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": False}], indirect=True)
def test_the_poll_pauses_while_the_page_is_hidden(remote_page):
    fake = FakePairing(remote_page, statuses=("waiting",))
    ready(remote_page)
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector(".pair-code")
    remote_page.evaluate("""() => {
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    polls = fake.polls
    remote_page.wait_for_timeout(4500)
    assert fake.polls == polls
    remote_page.evaluate("""() => {
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    deadline = 50
    while fake.polls == polls and deadline:
        remote_page.wait_for_timeout(100)
        deadline -= 1
    assert fake.polls == polls + 1  # at once when the page comes back


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": False}], indirect=True)
def test_a_refused_request_says_why_and_can_be_sent_again(remote_page):
    closed = "Association fermée : ouvrez-la sur le PC (Réglages › Accès à distance)."
    fake = FakePairing(remote_page, request_error=(409, closed))
    ready(remote_page)
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector("#pairStatus.err")
    assert norm(remote_page.text_content("#pairStatus")) == closed
    assert remote_page.is_enabled("button:has-text(\"Demander l'accès\")")
    fake.request_error = None
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector(".pair-code")
    assert len(fake.requests) == 2


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair"}, {"pair_state": "pair", "ios": False},
                                         {"pair_state": "locked"}], indirect=True)
def test_axe_has_no_serious_violation(remote_page):
    from test_settings_ui import axe_violations
    ready(remote_page)
    assert axe_violations(remote_page) == []
