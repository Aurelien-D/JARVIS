"""The pairing page (spec 4.15) in Chromium, as the unpaired iPhone sees it
through the Serve listener: every state of page.PAIR_STATES, the Home Screen
explanation on iOS with « Utiliser JARVIS dans Safari », then a request, its
code, the 2 s poll and « Associé ! » before JARVIS opens.

Everything goes through the real gate and the real pairing routes (A1); the
PC's side (allow, deny, close the window) is the PC's own API."""
import re

import httpx
import pytest

pytestmark = pytest.mark.e2e

TEXTS = {
    "closed": ("Association fermée", "Associer un iPhone, puis touchez Réessayer"),
    "off": ("Accès à distance coupé", "L'accès à distance est coupé sur le PC"),
    "paused": ("Accès à distance en pause", "Il reprendra seul à la fin de la pause"),
    # The likeliest refusal: the iPhone's Tailscale app is signed in to another account.
    "refused": ("Accès refusé", "connecté à Tailscale avec le même compte que le PC"),
    "locked": ("Accès bloqué", "réessayez dans 15 minutes"),
    "revoked": ("Appareil retiré", "Cet appareil a été retiré sur le PC"),
}


def ready(page):
    page.wait_for_function("window.__pair && window.__pair.ready")


def norm(text):
    """The page sets French typography (narrow no-break spaces): compare with plain spaces."""
    return re.sub(r"[  ]", " ", text or "")


class Pairing:
    """The phone's pairing calls as they leave the page, and the PC's side of
    pairing through its own API (the local path, with the PC's token)."""

    def __init__(self, page, app_server):
        from jarvis import security
        self.requests, self.polls = [], 0
        self.pc = httpx.Client(base_url=app_server.url, headers={"X-Jarvis-Token": security.TOKEN},
                               trust_env=False, timeout=10)
        page.on("request", self._seen)

    def _seen(self, request):
        if request.url.endswith("/api/remote/pair-request"):
            self.requests.append(request.post_data_json)
        elif request.url.endswith("/api/remote/pair-status"):
            self.polls += 1

    def waiting(self):
        return [r for r in self.pc.get("/api/remote/pair-requests").json() if r["status"] == "waiting"]

    def answer(self, verb):
        [request] = self.waiting()
        r = self.pc.post(f"/api/remote/pair-requests/{request['id']}/{verb}", json={})
        assert r.status_code == 200, r.text
        return request

    def window(self, open_):
        assert self.pc.post("/api/remote/pairing", json={"open": open_}).status_code == 200


@pytest.mark.parametrize("remote_page", [{"pair_state": s} for s in TEXTS], indirect=True,
                         ids=list(TEXTS))
def test_every_refusal_state_says_why_with_a_retry(remote_page, request):
    ready(remote_page)
    state = remote_page.evaluate("window.__pair.state")
    # The state the real server led to is the one this case set up, never another one's.
    assert state == request.node.callspec.params["remote_page"]["pair_state"]
    title, sentence = TEXTS[state]
    assert norm(remote_page.text_content(".pair-title")) == title
    assert sentence in norm(remote_page.text_content(".pair-text"))
    assert remote_page.title() == "JARVIS"
    # The page never holds a token nor loads the app.
    assert remote_page.evaluate("!document.querySelector('meta[name=jarvis-token]') && !window.__jarvis")
    with remote_page.expect_navigation():
        remote_page.click("button:has-text('Réessayer')")
    ready(remote_page)
    # The real gate cleared a revoked device's cookie: the retry finds the plain pairing page.
    assert remote_page.evaluate("window.__pair.state") == ("closed" if state == "revoked" else state)


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": True}], indirect=True)
def test_an_iphone_outside_the_home_screen_is_told_how_to_add_it(remote_page):
    ready(remote_page)
    steps = [norm(t) for t in remote_page.eval_on_selector_all(".pair-steps li", "els => els.map(e => e.textContent)")]
    assert len(steps) == 4
    assert "Partager" in steps[0] and "Sur l'écran d'accueil" in steps[1]
    assert "« Ouvrir comme app web »" in steps[2] and "Ajouter" in steps[2]
    assert "Ouvrez l'icône JARVIS" in steps[3]
    assert remote_page.locator("#pairName").count() == 0
    # Safari tabs share their cookie: it is the Home Screen app that needs its own pairing.
    assert "l'icône de l'écran d'accueil devra alors être associée à part" in norm(
        remote_page.text_content(".pair-note"))
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
    from conftest import wait_ready

    from jarvis import devices
    pairing = Pairing(remote_page, app_server)
    ready(remote_page)
    remote_page.fill("#pairName", "iPhone de test")
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector(".pair-code")
    code = remote_page.text_content(".pair-code")
    assert re.fullmatch(r"\d{4}", code)
    assert "Sur le PC, vérifiez que le même code s'affiche, puis cliquez Autoriser." in norm(
        remote_page.text_content(".pair-body"))
    assert pairing.requests == [{"name": "iPhone de test"}]
    # The PC sees the same code; it allows after the first poll (2 s) said "waiting".
    with remote_page.expect_response("**/api/remote/pair-status") as first:
        pass
    assert first.value.json() == {"status": "waiting", "code": code}
    assert pairing.answer("allow")["code"] == code
    with remote_page.expect_navigation(timeout=10_000):  # the next load of "/" is JARVIS itself
        remote_page.wait_for_selector(".pair-title:has-text('Associé')", timeout=8000)
        assert pairing.polls == 2  # every 2 s: waiting, then approved
        # The device exists now: its cookie came with the "approved" answer.
        [device] = devices.active()
        assert device["name"] == "iPhone de test"
    wait_ready(remote_page)
    assert remote_page.evaluate("__jarvis.state.origin") == f"app:{device['id']}"


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": False}], indirect=True)
@pytest.mark.parametrize("status, words", [("denied", "refusée sur le PC"), ("expired", "La demande a expiré")])
def test_denied_or_expired_offers_recommencer(remote_page, app_server, status, words):
    from jarvis import devices
    pairing = Pairing(remote_page, app_server)
    ready(remote_page)
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector(".pair-code")
    if status == "denied":
        pairing.answer("deny")
    else:  # the PC closes the window: what was not collected expires
        pairing.window(False)
    remote_page.wait_for_selector("button:has-text('Recommencer')", timeout=6000)
    assert words in norm(remote_page.text_content(".pair-body"))
    polls = pairing.polls
    remote_page.wait_for_timeout(2500)
    assert pairing.polls == polls  # the poll stopped
    assert devices.active() == []
    remote_page.click("button:has-text('Recommencer')")
    remote_page.wait_for_selector("#pairName")
    assert remote_page.is_enabled("button:has-text(\"Demander l'accès\")")


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": False}], indirect=True)
def test_the_poll_pauses_while_the_page_is_hidden(remote_page, app_server):
    pairing = Pairing(remote_page, app_server)
    ready(remote_page)
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector(".pair-code")
    remote_page.evaluate("""() => {
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    polls = pairing.polls
    remote_page.wait_for_timeout(4500)
    assert pairing.polls == polls
    remote_page.evaluate("""() => {
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    deadline = 50
    while pairing.polls == polls and deadline:
        remote_page.wait_for_timeout(100)
        deadline -= 1
    assert pairing.polls == polls + 1  # at once when the page comes back


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair", "ios": False}], indirect=True)
def test_a_refused_request_says_why_and_can_be_sent_again(remote_page, app_server):
    closed = "Association fermée : ouvrez-la sur le PC (Réglages › Accès à distance)."
    pairing = Pairing(remote_page, app_server)
    ready(remote_page)
    pairing.window(False)  # closed on the PC after the page loaded
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector("#pairStatus.err")
    assert norm(remote_page.text_content("#pairStatus")) == closed
    assert remote_page.is_enabled("button:has-text(\"Demander l'accès\")")
    pairing.window(True)
    remote_page.click("button:has-text(\"Demander l'accès\")")
    remote_page.wait_for_selector(".pair-code")
    assert len(pairing.requests) == 2 and len(pairing.waiting()) == 1


@pytest.mark.parametrize("remote_page", [{"pair_state": "pair"}, {"pair_state": "pair", "ios": False},
                                         {"pair_state": "locked"}], indirect=True)
def test_axe_has_no_serious_violation(remote_page):
    from test_settings_ui import axe_violations
    ready(remote_page)
    assert axe_violations(remote_page) == []
