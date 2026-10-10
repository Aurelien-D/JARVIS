"""Wave A working together: the iPhone from pairing to revocation, through the
real gate (FakeServe plays Tailscale Serve; nothing about the guard is faked).

One scenario, two pages: the PC's JARVIS and a fresh iPhone browser.
- The PC switches remote access on in Réglages and opens pairing.
- The phone asks for access, the PC sees the same code and allows it, the
  phone gets its device cookie and opens JARVIS with its own page token.
- The phone opens a voice session (fake WebRTC); a recherche task launched
  from it is announced on the phone and only listed on the PC.
- A full-access request from the phone is refused while the PC's opt-in is
  off; with the opt-in it is parked for the phone's [Lancer] only, never a
  voice « oui », and the PC can only cancel it.
- The PC removes the device: the phone's stream closes at once, its page token
  and cookie are refused, and its next load is the « Appareil retiré » page.

remote.READY stays False in the code until C2: the test turns it on through
its test hook (monkeypatch), never in the module.

A second test loads the pairing page and the paired page under the real
remote CSP (no bypass) and checks the browser reports no violation.
Fictitious names only: the repository is public."""
import time

import httpx
import pytest
from conftest import IP, IPHONE_UA, LOGIN, REMOTE_HOST, wait_ready
from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER
from test_remote_confirm_ui import buttons, plain
from test_remote_delivery_ui import (  # noqa: F401 - fixture
    RECORD,
    SPIES,
    call,
    open_pc,
    output,
    until,
    waiting,
)

pytestmark = pytest.mark.e2e

QR_URL = "https://cdn.jsdelivr.net/npm/qrcode-generator@2.0.4/dist/qrcode.js"
CARD = ".card.confirm[data-state='pending']"
COMPLET = {"title": "Ranger les téléchargements", "prompt": "Range ~/Downloads par type de fichier",
           "profile": "complet"}
VIOLATIONS = ("window.__csp = []; document.addEventListener('securitypolicyviolation', "
              "e => __csp.push(e.violatedDirective + ' ' + e.blockedURI));")


@pytest.fixture
def remote_on_soon(monkeypatch):
    """What the PC needs before its switch can turn on: this version's test hook
    for remote.READY and a daily cap (the Serve name and login come from the
    harness, as JARVIS_REMOTE_HOST / JARVIS_REMOTE_LOGINS would)."""
    from jarvis import config, confirm, desktop, remote
    monkeypatch.setattr(remote, "READY", True)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    monkeypatch.setattr(config, "CONFIRM_COMPLET", True)
    ran = []
    monkeypatch.setattr(desktop, "open_target", lambda **kw: ran.append(("open", kw)) or {"ok": True})
    monkeypatch.setattr(desktop, "system_action", lambda action, value=None: ran.append((action, value))
                        or {"ok": True})
    confirm.PENDING.clear()
    yield ran
    confirm.PENDING.clear()


@pytest.fixture
def iphone(browser, app_server):
    """iphone(bypass_csp=True, extra="") -> a fresh iPhone browser page with no
    cookie (never paired), the fake WebRTC and the spies. Fails the test on any
    page error."""
    contexts, errors = [], []

    def open_(bypass_csp=True, extra=""):
        context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                                      user_agent=IPHONE_UA, permissions=["microphone"], ignore_https_errors=True,
                                      bypass_csp=bypass_csp)
        context.set_default_timeout(10_000)
        context.add_init_script(FAKE_RTC + FAKE_SR + extra)
        context.route("https://api.openai.com/**", lambda route: route.fulfill(
            status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda err: errors.append(str(err)))
        return page

    yield open_
    for context in contexts:
        context.close()
    assert not errors, f"erreurs dans la page : {errors}"


def open_distance(page):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    if not page.is_visible("#settingsDialog .set-tab[aria-current='true']:has-text('Accès à distance')"):
        page.click("#settingsDialog .set-tab:has-text('Accès à distance')")
    page.wait_for_selector("#set-sec-distance .rm-block")


def close_settings(page):
    page.keyboard.press("Escape")
    page.wait_for_selector("#settingsDialog[open]", state="detached")


def refusals(page):
    """Every 4xx/5xx the page receives from JARVIS, as (status, path)."""
    seen = []
    page.on("response", lambda r: seen.append((r.status, r.url.split("?")[0].split("/", 3)[-1]))
            if r.status >= 400 and "127.0.0.1" in r.url else None)
    return seen


def test_the_iphone_from_pairing_to_revocation_through_the_real_gate(remote_on_soon, open_pc, iphone,  # noqa: F811
                                                                   app_server):
    from jarvis import confirm, devices, events, remote, security, tasks
    ran = remote_on_soon
    pc = open_pc(SPIES)
    pc.route(QR_URL, lambda route: route.fulfill(status=404, body=""))  # no CDN: the text fallback
    pc.evaluate(RECORD)

    # 1. The PC switches remote access on and opens pairing (Réglages › Accès à distance).
    open_distance(pc)
    pc.click("#set-remote-enabled")
    pc.wait_for_selector("[data-key='remote-enabled'] .set-status:has-text('Accès à distance activé.')")
    assert remote.is_enabled() and remote.host() == REMOTE_HOST and remote.logins() == [LOGIN]
    pc.click("#rm-pair-open")
    pc.wait_for_selector("#rm-countdown")
    assert remote.pairing_until() > time.time()

    # 2. The phone asks: the pairing page, then a 4-digit code.
    phone = iphone(extra=SPIES)
    phone_refused = refusals(phone)
    phone.goto(app_server.remote_url)
    phone.wait_for_function("window.__pair && window.__pair.ready")
    assert phone.evaluate("window.__pair.state") == "pair"
    assert phone.evaluate("!document.querySelector('meta[name=jarvis-token]')")  # no token before pairing
    phone.click("button:has-text('Utiliser JARVIS dans Safari')")  # not the Home Screen app in this test
    phone.fill("#pairName", "iPhone de test")
    phone.click("button:has-text(\"Demander l'accès\")")
    phone.wait_for_selector(".pair-code")
    code = phone.text_content(".pair-code")

    # 3. The PC sees the same code (from its event stream) and allows it.
    row = pc.locator(".rm-request", has_text=code)
    row.wait_for()
    assert row.locator(".rm-code").text_content() == code
    assert plain(row.locator(".rm-name").text_content()) == "« iPhone de test »"
    row.locator("button", has_text="Autoriser").click()  # one request waiting: no code to type
    pc.wait_for_selector(f"[data-key='remote-pairing'] > .set-status:has-text('Demande {code} autorisée')")

    # 4. The phone collects its device cookie at its next poll and opens JARVIS.
    with phone.expect_navigation(timeout=10_000):  # « Associé ! », then JARVIS itself
        phone.wait_for_selector(".pair-title:has-text('Associé')", timeout=8000)
    wait_ready(phone)
    [device] = devices.active()
    origin = f"app:{device['id']}"
    assert device["name"] == "iPhone de test" and device["ip"] == IP and device["login"] == LOGIN
    assert phone.evaluate("({remote: __jarvis.state.remote, origin: __jarvis.state.origin})") == \
        {"remote": True, "origin": origin}
    token = phone.evaluate("document.querySelector('meta[name=jarvis-token]').content")
    assert token and token != security.TOKEN  # its own page token, never the PC's
    [cookie] = [c for c in phone.context.cookies() if c["name"] == "__Host-jarvis"]
    assert cookie["httpOnly"] and cookie["secure"] and cookie["sameSite"] == "Strict" and cookie["path"] == "/"
    assert cookie["value"].startswith(device["id"] + ".")
    assert events.has_subscribers(lambda c: c is not None and c.device_id == device["id"])
    pc.locator(f".rm-device[data-device='{device['id']}']").wait_for()
    close_settings(pc)
    # Its Réglages: the phone's own sections only, opened on « Accès à distance ».
    phone.click("#topActions button:has-text('Réglages')")
    phone.wait_for_selector("#settingsDialog .set-tab[aria-current='true']:has-text('Accès à distance')")
    shown = [plain(t).strip() for t in phone.locator("#settingsDialog .set-tab").all_inner_texts()]
    allowed = phone.evaluate("__jarvis.api('/api/settings')")["sections"]
    assert {s["id"] for s in allowed} <= set(remote.REMOTE_SECTIONS) and "distance" in {s["id"] for s in allowed}
    assert shown == [plain(s["title"]) for s in allowed] and "Coûts" not in shown
    phone.wait_for_selector("[data-key='remote-phone']:has-text('iPhone de test')")
    close_settings(phone)

    # 5. A voice session on the phone, then a recherche task asked by voice.
    phone.evaluate(RECORD)
    phone.click("#orbBtn")
    phone.wait_for_function("__jarvis.state.mode === 'live'")
    assert remote.mints_today(device["id"]) == 1  # the real gate counted the phone's mint
    call(phone, "delegate_to_claude", "d1", {"title": "Météo Lyon", "prompt": "météo ?", "profile": "recherche"})
    started = output(phone, "d1")
    assert started["status"] == "started" and tasks.TASKS[started["task_id"]]["via"] == origin
    phone.evaluate("__jarvis.voice.sleep()")  # back in its pocket: the phone tells it
    phone.wait_for_function("__jarvis.state.mode !== 'live'")
    phone.wait_for_function("__spoken.length === 1", timeout=15_000)
    assert phone.evaluate("__spoken") == ["Monsieur, la tâche « Météo Lyon » est terminée."]
    until(lambda: waiting(origin) == [])  # the phone acknowledged its own message
    # The PC lists it and says nothing.
    pc.wait_for_selector(".task.done:has-text('Météo Lyon')")
    pc.wait_for_timeout(500)
    assert pc.evaluate("__spoken") == [] and pc.evaluate("__delivered") == []

    # 6. Full access from the phone: closed while the PC's opt-in is off, nothing parked.
    phone.click("#orbBtn")
    phone.wait_for_function("__jarvis.state.mode === 'live'")
    call(phone, "delegate_to_claude", "c1", COMPLET)
    assert output(phone, "c1") == {"ok": False, "error": confirm.T.complet_closed}
    assert not confirm.PENDING and phone.locator(CARD).count() == 0
    # The PC allows it for 24 h.
    open_distance(pc)
    pc.click("label[for='rm-complet-24h']")
    pc.click("#settingsConfirm button[value='ok']")
    pc.wait_for_selector(".rm-complet-now:has-text('Autorisé jusqu')")
    assert remote.complet_allowed()
    close_settings(pc)
    # This conversation carries on from the one that heard the web result: refused all the same.
    call(phone, "delegate_to_claude", "c2", COMPLET)
    assert output(phone, "c2") == {"ok": False, "error": confirm.T.complet_tainted}
    assert not confirm.PENDING
    phone.evaluate("__jarvis.voice.sleep()")
    phone.wait_for_function("__jarvis.state.mode !== 'live'")
    # Half an hour later, a fresh conversation: parked, for the phone's button only.
    phone.evaluate("__jarvis.state.history.length = 0")
    phone.click("#orbBtn")
    phone.wait_for_function("__jarvis.state.mode === 'live'")
    call(phone, "delegate_to_claude", "c3", COMPLET)
    parked = output(phone, "c3")
    assert parked.get("status") == "needs_confirmation", parked
    mine = phone.locator(CARD)
    mine.wait_for()
    assert buttons(mine) == ["Lancer", "Annuler"] and mine.get_attribute("data-launch") == "yes"
    theirs = pc.locator(CARD)
    theirs.wait_for()
    assert buttons(theirs) == ["Annuler"] and theirs.get_attribute("data-launch") == "no"
    public = confirm.public(confirm.PENDING[parked["pending_id"]])
    assert public["button_only"] is True and public["launch_from"] == origin and public["remote_kind"] == "complet"
    # A voice « oui » never launches it.
    call(phone, "confirm_action", "y1", {"pending_id": parked["pending_id"], "decision": "oui"})
    assert output(phone, "y1").get("ok") is False
    assert confirm.PENDING[parked["pending_id"]]["state"] == "pending"
    # The PC may only cancel it; nothing ran.
    theirs.locator("button", has_text="Annuler").click()
    phone.wait_for_selector(".card[data-state='cancelled']")
    assert not [t for t in tasks.TASKS.values() if t["profile"] == "complet"] and ran == []
    phone.evaluate("__jarvis.voice.sleep()")
    phone.wait_for_function("__jarvis.state.mode !== 'live'")

    # Until now the phone was refused nothing (no PC-only call from its page).
    assert phone_refused == []

    # 7. The PC removes the device: the phone's stream ends at once, its credentials are refused.
    open_distance(pc)
    item = pc.locator(f".rm-device[data-device='{device['id']}']")
    item.locator("button", has_text="Retirer").click()
    pc.click("#settingsConfirm button[value='ok']")
    pc.wait_for_selector("text=Aucun appareil associé.")
    until(lambda: not events.has_subscribers(lambda c: c is not None and c.remote))
    assert devices.is_revoked(device["id"])
    # The page finds its stream gone and its token refused (that answer clears the
    # cookie, spec 4.1 step 14): it reloads into the pairing page, still open on the PC.
    phone.wait_for_selector("#pairBody .pair-title", timeout=15_000)
    assert phone.evaluate("window.__pair.state") == "pair"
    assert not [c for c in phone.context.cookies() if c["name"] == "__Host-jarvis"]
    # The old cookie and page token, replayed through Serve, are refused; with the
    # old cookie, the page is « Appareil retiré ».
    with httpx.Client(trust_env=False, timeout=10) as client:
        old = {"Cookie": f"__Host-jarvis={cookie['value']}"}
        r = client.get(app_server.remote_url + "api/config", headers={**old, "X-Jarvis-Token": token})
        assert r.status_code == 401 and r.json()["detail"] == remote.T_REVOKED
        page = client.get(app_server.remote_url, headers=old)
        assert page.status_code == 401 and 'content="revoked"' in page.text
    assert phone_refused and {status for status, _ in phone_refused} == {401}, phone_refused


def test_the_phone_pages_run_under_the_remote_csp(remote_on_soon, iphone, app_server):
    """The pairing page and the paired page, CSP enforced (no bypass): no
    violation, and both start. Polls with page.evaluate (Playwright's string
    waits would need 'unsafe-eval')."""
    from jarvis import remote, store

    from playwright.sync_api import Error as PageError

    def poll(page, expression, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if page.evaluate(expression):
                    return
            except PageError as err:  # asked while the page was navigating: ask again
                if "context was destroyed" not in str(err):
                    raise
            page.wait_for_timeout(100)
        raise AssertionError(f"jamais vrai : {expression}")

    store.save("remote.json", {"enabled": True, "host": REMOTE_HOST, "logins": [LOGIN], "paused_until": 0,
                               "complet_until": 0, "published": False, "changed_at": time.time(),
                               "changed_by": "pc"})
    remote.open_pairing()
    phone = iphone(bypass_csp=False, extra=VIOLATIONS)
    response = phone.goto(app_server.remote_url)
    assert "script-src 'self' https://cdn.jsdelivr.net" in response.headers["content-security-policy"]
    poll(phone, "window.__pair && window.__pair.ready")
    assert phone.evaluate("window.__pair.state") == "pair"
    assert phone.evaluate("__csp") == []
    # Paired (the request, the PC's approval, the poll), then JARVIS itself.
    phone.click("button:has-text('Utiliser JARVIS dans Safari')")
    phone.click("button:has-text(\"Demander l'accès\")")
    poll(phone, "!!document.querySelector('.pair-code')")
    [request] = remote.pairing_requests()
    with phone.expect_navigation():  # the next poll collects the cookie, then JARVIS opens
        remote.allow_pairing(request["id"])
    poll(phone, "!!(window.__jarvis && __jarvis.state.ready && __jarvis.state.synced)", timeout=15.0)
    assert phone.evaluate("__jarvis.state.remote") is True
    phone.wait_for_timeout(1000)  # late loads (fonts, lazy modules)
    assert phone.evaluate("__csp") == []
