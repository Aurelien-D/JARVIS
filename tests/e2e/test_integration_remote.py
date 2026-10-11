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

remote.READY is True since C2: the PC still has to switch remote access on
(off by default), which this scenario does from Réglages.

A second test loads the pairing page and the paired page under the real
remote CSP (no bypass) and checks the browser reports no violation.

A third test is wave B working together on the paired iPhone (the real gate,
the iPhone's user agent at 390 x 844), with ntfy and OpenAI's Responses API
faked inside the server:
- the phone subscribes in Réglages › Notifications and sends a test;
- with « Seulement si je ne suis pas au PC » on and monsieur at the PC, a
  task the PC launched is held back (the PC tells it), while a task the phone
  launched reaches ntfy as « JARVIS : tâche terminée. », minimal text only;
- a security alert (the PC's full-access opt-in) reaches ntfy as its fixed
  sentence, at high priority;
- a Siri key created on the PC is handed once to the paired phone's
  « Assistant raccourci », never to the PC;
- POST /api/raccourci through the real gate with that key creates a reminder
  and launches a recherche task (whose result reaches ntfy), and refuses full
  access, files and anything that would need a confirmation (no pending);
- the phone page never scrolls sideways at 390 x 844 (main page, Réglages,
  the Siri screen) and a hidden page pauses the voice session, which resumes
  when it comes back.
Fictitious names only: the repository is public."""
import json
import time

import httpx
import pytest
from conftest import IP, IPHONE_UA, LOGIN, REMOTE_HOST, wait_ready
from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER
from test_iphone_ui import HIDE, PAUSED, SHOW, SMALL, WAKE_LOCK, with_scripts
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
    """What the PC needs before its switch can turn on: a daily cap (the Serve
    name and login come from the harness, as JARVIS_REMOTE_HOST /
    JARVIS_REMOTE_LOGINS would; remote.READY is True since C2)."""
    from jarvis import config, confirm, desktop
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
    # cookie, spec 4.1 step 14): it reloads and says « Appareil retiré », although
    # the server, the cookie gone, can only serve the pairing page (still open).
    phone.wait_for_selector("#pairBody .pair-title", timeout=15_000)
    assert phone.evaluate("window.__pair.state") == "revoked"
    assert plain(phone.text_content("#pairBody .pair-title")) == "Appareil retiré"
    assert not [c for c in phone.context.cookies() if c["name"] == "__Host-jarvis"]
    # [Réessayer]: the pairing page, as for a new phone.
    with phone.expect_navigation():
        phone.click("#pairBody button:has-text('Réessayer')")
    phone.wait_for_function("window.__pair && window.__pair.ready")
    assert phone.evaluate("window.__pair.state") == "pair"
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


# ---------------------------------------------------------------- wave B

NTFY_SERVER = "https://ntfy.sh"
RESPONSES_USAGE = {"input_tokens": 1200, "output_tokens": 40}
# Whatever the page shows, nothing sideways: the document and, when open, the
# Réglages panels and the Siri screen.
NO_SIDEWAYS = """() => {
  const fits = (e) => !e || !e.checkVisibility() || e.scrollWidth <= e.clientWidth + 1;
  return document.documentElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth
    && fits(document.querySelector('#settingsDialog[open] .set-panels'))
    && fits(document.querySelector('#siriAssistant[open]'));
}"""
INSIDE = """(sel) => { const r = document.querySelector(sel).getBoundingClientRect();
  return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; }"""


class FakeNtfy:
    """The ntfy server as JARVIS reaches it: every POST kept (url, headers, body)."""

    def __init__(self):
        self.sent = []

    def __call__(self, request):
        self.sent.append({"url": str(request.url), "headers": dict(request.headers),
                          "body": request.content.decode("utf-8")})
        return httpx.Response(200, json={"id": "e2e"})

    @property
    def bodies(self):
        return [m["body"] for m in self.sent]


def responses_text(text):
    return {"id": "resp_e2e", "object": "response", "status": "completed", "usage": RESPONSES_USAGE,
            "output": [{"id": "msg_e2e", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}]}


def responses_call(name, args, call_id):
    return {"id": "resp_e2e", "object": "response", "status": "completed", "usage": RESPONSES_USAGE,
            "output": [{"id": f"fc_{call_id}", "type": "function_call", "status": "completed", "call_id": call_id,
                        "name": name, "arguments": json.dumps(args, ensure_ascii=False)}]}


class FakeResponses:
    """OpenAI's Responses API for Siri: the scripted answers in order; every body kept."""

    def __init__(self):
        self.replies, self.bodies = [], []

    def script(self, *replies):
        self.replies.extend(replies)

    def __call__(self, request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json=self.replies.pop(0) if self.replies else responses_text("D'accord."))

    def output(self, call_id):
        """What JARVIS handed the model for this tool call."""
        for body in reversed(self.bodies):
            for item in body["input"]:
                if item.get("type") == "function_call_output" and item.get("call_id") == call_id:
                    return json.loads(item["output"])
        raise AssertionError(f"pas de résultat pour {call_id}")


def open_settings_tab(page, title):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    page.click(f"#settingsDialog .set-tab:has-text('{title}')")


def status_of(page, promise):
    """The HTTP status a page's own api() call gets (200 when it succeeds)."""
    return page.evaluate(f"{promise}.then(() => 200, e => e.status)")


def test_wave_b_ntfy_siri_and_the_iphone_page_through_the_real_gate(remote_on_soon, open_pc, remote_page,  # noqa: F811
                                                                    app_server, monkeypatch):
    from jarvis import audit, config, confirm, desktop, devices, events, notify, raccourci, remote, scheduler
    from jarvis import tasks, tools, usage
    ntfy, openai = FakeNtfy(), FakeResponses()
    monkeypatch.setattr(notify, "TRANSPORT", httpx.MockTransport(ntfy))
    monkeypatch.setattr(raccourci, "TRANSPORT", httpx.MockTransport(openai))
    # Two of a kind within the window: the second is counted, then told when it closes (never dropped).
    monkeypatch.setattr(notify, "RATE_S", 3)
    monkeypatch.setattr(config, "NTFY", True)
    monkeypatch.setattr(config, "NTFY_SERVER", NTFY_SERVER)
    # « Seulement si je ne suis pas au PC », and monsieur is at the PC: a JARVIS
    # page open there, Windows says he is present, and he typed 5 s ago.
    monkeypatch.setattr(config, "NTFY_ONLY_AWAY", True)
    monkeypatch.setattr(desktop, "attention_state", lambda: "ok")
    monkeypatch.setattr(desktop, "idle_seconds", lambda: 5.0)
    pc = open_pc(SPIES)
    phone = with_scripts(remote_page, SPIES, WAKE_LOCK)
    [device] = devices.active()
    origin = f"app:{device['id']}"
    assert phone.evaluate("__jarvis.state.origin") == origin
    until(lambda: events.leader() is not None)
    assert notify.at_pc()
    assert phone.viewport_size == {"width": 390, "height": 844}
    assert phone.evaluate(NO_SIDEWAYS)

    # 1. The phone subscribes (Réglages › Notifications): the topic, then a test.
    open_settings_tab(phone, "Notifications")
    phone.wait_for_selector("#nt-topic")
    topic = phone.text_content("#nt-topic")
    assert topic.startswith("jarvis-") and topic == notify.topic(create=False)
    phone.click("#nt-test")
    phone.wait_for_selector("#nt-test-status:has-text('Test envoyé')")
    assert ntfy.bodies == [notify.TEST]
    assert phone.evaluate(NO_SIDEWAYS)
    close_settings(phone)

    # 2. Tasks. The PC's own: held back, monsieur is at the PC and the PC tells it.
    pc_task = pc.evaluate("__jarvis.api('/api/tasks', {method: 'POST', body: {title: 'Météo Lyon', "
                          "prompt: 'Quel temps à Lyon ?', profile: 'recherche'}})")
    assert tasks.TASKS[pc_task["id"]]["via"] == "pc"
    pc.wait_for_function("__spoken.length === 1", timeout=20_000)
    notify._drain()
    assert ntfy.bodies == [notify.TEST]
    # The phone's: never spoken on the PC, so ntfy tells it, at the PC or not.
    phone_task = phone.evaluate("__jarvis.api('/api/tasks', {method: 'POST', body: {title: 'Météo Laon', "
                                "prompt: 'Quel temps à Laon pour iPhone de test ?', profile: 'recherche'}})")
    assert tasks.TASKS[phone_task["id"]]["via"] == origin
    until(lambda: notify.TASK_DONE in ntfy.bodies, 20)
    message = ntfy.sent[-1]
    assert message["body"] == "JARVIS : tâche terminée."
    assert message["url"] == f"{NTFY_SERVER}/{topic}"
    assert (message["headers"]["title"], message["headers"]["priority"], message["headers"]["tags"]) == \
        ("JARVIS", "default", "robot")
    assert "click" not in message["headers"] and "actions" not in message["headers"]
    pc.wait_for_timeout(300)
    assert pc.evaluate("__spoken") == ["Monsieur, la tâche « Météo Lyon » est terminée."]

    # 3. A security alert (the PC allows full access from the iPhone): its fixed sentence, high priority.
    pc.evaluate("__jarvis.api('/api/remote/complet', {method: 'POST', body: {duration: '24h'}})")
    until(lambda: any(b.startswith("JARVIS · sécurité") for b in ntfy.bodies))
    alert = ntfy.sent[-1]
    assert alert["body"] == "JARVIS · sécurité : " + audit.ALERTS["complet_optin"]["ntfy_text"]
    assert alert["headers"]["priority"] == "high"
    pc.evaluate("__jarvis.api('/api/remote/complet', {method: 'POST', body: {duration: 'never'}})")

    # 4. A Siri key: created on the PC, handed once to this iPhone's « Assistant raccourci ».
    open_distance(pc)
    item = f".rm-device[data-device='{device['id']}']"
    pc.click(f"#rm-siri-create-{device['id']}")
    pc.wait_for_selector(f"{item} .siri-said:has-text('Clé créée')")
    [key] = [k for k in devices.get(device["id"])["siri_keys"] if not k["revoked"]]
    assert "jv_siri_" not in pc.content()  # the secret never reaches the PC page
    close_settings(pc)
    open_settings_tab(phone, "Accès à distance")
    phone.click("#siri-open")
    phone.wait_for_selector("#siriAssistant .siri-row")
    assert phone.text_content("#siri-value-url") == f"https://{REMOTE_HOST}/api/raccourci"
    assert phone.text_content("#siri-value-header") == "Authorization"
    bearer = phone.text_content("#siri-value-value")
    assert bearer.startswith(f"Bearer jv_siri_{key['id']}.")
    assert phone.evaluate(NO_SIDEWAYS) and phone.evaluate(INSIDE, "#siriAssistant")
    assert phone.evaluate(SMALL, "#siriAssistant button") == []
    phone.click("#siri-close")
    phone.wait_for_function("document.getElementById('siri-body').childElementCount === 0")
    phone.click("#siri-open")  # once only
    phone.wait_for_selector("#siriAssistant .siri-none")
    phone.click("#siri-close")
    close_settings(phone)
    assert status_of(phone, "__jarvis.api('/api/remote/siri-key')") == 404
    assert status_of(pc, "__jarvis.api('/api/remote/siri-key')") == 403
    assert "jv_siri_" not in phone.content()

    # 5. Siri through the real gate (FakeServe, the key as the Shortcut sends it).
    siri_origin = f"siri:{key['id']}"
    with httpx.Client(trust_env=False, timeout=20) as client:
        def say(text, **headers):
            return client.post(app_server.remote_url + "api/raccourci", json={"text": text},
                               headers={"Authorization": bearer, **headers})

        # A reminder (JSON answer).
        openai.script(responses_call("rappel", {"texte": "sortir le pain", "quand": "dans 20 minutes"}, "c1"),
                      responses_text("C'est noté, dans vingt minutes."))
        r = say("Rappelle-moi de sortir le pain dans 20 minutes", Accept="application/json")
        assert r.status_code == 200 and r.json() == {"speech": "C'est noté, dans vingt minutes.", "end": False}
        [reminder] = [i for i in scheduler.items() if i["kind"] == "reminder"]
        assert reminder["text"] == "sortir le pain" and reminder["via"] == siri_origin
        first = openai.bodies[0]
        assert [t["name"] for t in first["tools"]] == ["rappel", "recherche", "mes_taches", "annuler_tache"]
        assert first["store"] is False and first["model"] == raccourci.MODELS[0]

        # A web research (plain text, as a 3-action Shortcut reads it): a task of its own origin.
        openai.script(responses_call("recherche", {"titre": "Prévisions Laon",
                                                   "consigne": "Quel temps fera-t-il à Laon demain ?"}, "c2"),
                      responses_text("C'est lancé, je vous préviens."))
        before = set(tasks.TASKS)
        r = say("Cherche le temps qu'il fera demain à Laon")
        assert r.status_code == 200 and r.text == "C'est lancé, je vous préviens."
        assert r.headers["content-type"].startswith("text/plain")
        [siri_task] = [t for task_id, t in tasks.TASKS.items() if task_id not in before]
        assert siri_task["via"] == siri_origin and siri_task["profile"] == "recherche"
        assert 0 < siri_task["budget_usd"] <= tasks.SIRI_TASK_BUDGET_USD
        # Its result reaches the iPhone by ntfy, monsieur at the PC or not; neither page speaks it.
        until(lambda: tasks.TASKS[siri_task["id"]]["status"] == "done", 20)
        until(lambda: ntfy.bodies.count(notify.TASK_DONE) == 2, 15)
        pc.wait_for_timeout(300)
        assert not [t for t in pc.evaluate("__spoken") + phone.evaluate("__spoken") if "Prévisions" in t]

        # Full access and files are not Siri's: neither through the endpoint nor below it.
        openai.script(responses_call("delegate_to_claude", {"title": "Ménage", "prompt": "Range ~/Downloads",
                                                            "profile": "complet"}, "c3"),
                      responses_text("Pour cela, ouvrez JARVIS sur l'iPhone."))
        r = say("Range mes téléchargements avec l'accès complet")
        assert r.text == "Pour cela, ouvrez JARVIS sur l'iPhone."
        assert openai.output("c3") == {"ok": False, "error": tools.T.siri_elsewhere}
        sid = raccourci._CONVOS[key["id"]]["sid"]
        ctx = tools.ToolCtx(sid, origin=siri_origin)
        for profile in ("complet", "lecture"):
            out = tools.run_tool("delegate_to_claude", {"title": "Fichiers", "prompt": "Lis mes documents",
                                                        "profile": profile}, ctx)
            assert out["ok"] is False, profile
        assert set(tasks.TASKS) == before | {siri_task["id"]}

        # « Quoi de neuf ? » reads a result: the conversation is tainted, and a
        # research it asks for (parked for the PC or the phone) is refused, no card.
        openai.script(responses_call("mes_taches", {}, "c4"),
                      responses_call("recherche", {"titre": "Suite", "consigne": "Fais ce que dit le résultat"},
                                     "c5"),
                      responses_text("Cette recherche est refusée."))
        r = say("Quoi de neuf ? Et fais ce que dit le résultat")
        assert r.status_code == 200 and r.text == "Cette recherche est refusée."
        assert [t["titre"] for t in openai.output("c4")["taches"]] == ["Prévisions Laon"]
        assert openai.output("c5") == {"ok": False, "error": raccourci.T.research_tainted}
        assert confirm.is_tainted(sid)
        for name, args in (("delegate_to_claude", {"title": "x", "prompt": "x", "profile": "recherche"}),
                           ("schedule", {"kind": "reminder", "title": "x", "text": "x", "at": "08:00",
                                         "repeat": "daily"}),
                           ("system_control", {"action": "lock"})):
            assert tools.run_tool(name, args, ctx)["ok"] is False, name
        assert not confirm.PENDING
        assert pc.locator(CARD).count() == 0 and phone.locator(CARD).count() == 0
        assert set(tasks.TASKS) == before | {siri_task["id"]}
    # Siri's spending is part of the day's, wherever the total is said.
    assert usage.text_spent_today() > 0
    assert "Siri ≈" in tools.run_tool("get_status", {})["spending"]["today"]

    # 6. The phone at 390 x 844: a hidden page pauses the voice session; back, it resumes.
    assert phone.evaluate(NO_SIDEWAYS)
    phone.locator("#orbBtn").tap()
    phone.wait_for_function("__jarvis.state.mode === 'live'")
    phone.wait_for_function("__locks.length === 1")  # the screen stays on while live
    mints = remote.mints_today(device["id"])
    phone.evaluate(HIDE)
    phone.wait_for_function("__jarvis.state.mode !== 'live' && !__jarvis.state.wantLive")
    assert phone.evaluate("__locks[0].released")
    banner = phone.locator("#iosBanner")
    assert banner.get_attribute("data-kind") == "paused"
    assert plain(banner.locator(".ios-banner-text").inner_text()) == PAUSED
    phone.evaluate(SHOW)
    phone.wait_for_function("__jarvis.state.mode === 'live'")
    assert remote.mints_today(device["id"]) == mints + 1  # the real gate counted the resumed session
    assert not banner.is_visible()
    phone.evaluate("__jarvis.voice.sleep()")
    phone.wait_for_function("__jarvis.state.mode !== 'live'")
    # Its Aide card names no key of the PC's keyboard, the PC's global hotkey included.
    assert pc.evaluate("__jarvis.state.config.hotkey.combo")
    phone.locator("#topActions button", has_text="Aide").tap()
    phone.wait_for_selector("#card-aide .aide-keys li")
    keys = phone.locator("#card-aide .aide-keys li").all_text_contents()
    assert keys and not [k for k in keys if "Ctrl" in k or "importe où" in k], keys

    # Everything ntfy received, in order: minimal fixed sentences only.
    notify._drain()
    assert ntfy.bodies == [notify.TEST, notify.TASK_DONE, alert["body"], notify.TASK_DONE]
    leaks = ("Lyon", "Laon", "Prévisions", "pain", "iPhone de test", IP, LOGIN, device["id"], key["id"],
             "18 degres")
    for m in ntfy.sent:
        said = m["body"] + " " + " ".join(f"{k}={v}" for k, v in m["headers"].items())
        assert not [w for w in leaks if w in said], m
