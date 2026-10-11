"""Remote access protections in a real browser (spec 4.12, section 8 rows 27,
27b and 28, and C2's two P1 proofs): the PC page and the paired iPhone side by side.

- P0-27: the PC never speaks, chimes, notifies or acknowledges a phone's or
  Siri's result (it stays in the task panel); the phone ignores the PC's busy
  screen.
- P0-27b: a remote alert reaches the PC page as plain text: no element, no link.
- P0-28: the wake word is off on the phone and on any iOS page, and a
  recognizer is stopped before the voice session takes the microphone.
- P1 (C2), the real gate end to end: a never-paired iPhone meets the closed
  door, then the pairing page (no token), asks, the PC allows, its __Host-
  cookie is HttpOnly, Secure, SameSite=Strict; JARVIS loads with the device's
  own page token, a voice session opens, a PC-only route answers 403, and the
  PC's removal closes its stream and the next request answers 401.
- P1 (C2), the remote CSP enforced (no bypass): the pairing page, the paired
  page with a report (chart, table, notes) and a voice session, and the QR
  code, with no violation.

The phone is remote_page or a fresh browser through FakeServe (the real gate,
nothing faked about it); speech synthesis, notifications and earcons are
faked as in test_delivery. Fictitious devices only."""
import hashlib
import time
from urllib.parse import urlsplit

import httpx
import pytest
from conftest import IP, LOGIN, REMOTE_HOST, phone_origin
from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER

pytestmark = pytest.mark.e2e

SIRI = "siri:k_e2e0000000000001"
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1")

# The browser's voice, notifications (permission in window.__notifPerm) and the
# earcons' tones, recorded (as in test_delivery).
SPIES = r"""
window.__spoken = [];
const fakeTTS = {
  speaking: false, pending: false, paused: false, onvoiceschanged: null,
  getVoices: () => [], cancel() {}, pause() {}, resume() {},
  addEventListener() {}, removeEventListener() {},
  speak(u) {
    __spoken.push(u.text);
    setTimeout(() => { u.onstart && u.onstart(); setTimeout(() => u.onend && u.onend(), 30); }, 10);
  },
};
Object.defineProperty(window, "speechSynthesis", { value: fakeTTS, configurable: true });
window.__notes = []; window.__notifAsked = 0;
window.Notification = class {
  constructor(title, options) { __notes.push({ title, ...options }); }
  close() {}
  static get permission() { return window.__notifPerm || "default"; }
  static requestPermission() { __notifAsked++; return Promise.resolve("granted"); }
};
window.__tones = [];
const createOscillator = AudioContext.prototype.createOscillator;
AudioContext.prototype.createOscillator = function () {
  const o = createOscillator.call(this), start = o.start.bind(o);
  o.start = (at) => { __tones.push(o.frequency.value); return start(at); };
  return o;
};
"""
RECORD = "window.__delivered = []; __jarvis.bus.on('delivered', d => __delivered.push(d)); 0"
# How many recognizers are running when the page asks for the microphone.
MIC_SPY = """() => {
  window.__gum = [];
  const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = (c) => {
    __gum.push((window.__recs || []).filter(r => r.running).length);
    return real(c);
  };
}"""
HOSTILE = "<img src=x onerror=alert(1)>[x](https://ev.il)"


def wait_ready(page):
    page.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")


def until(check, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("condition jamais remplie")


def publish(kind, data):
    from jarvis import events
    return events.publish(kind, data)


def waiting(via):
    from jarvis import inbox
    return [i["payload"].get("id") for i in inbox.pending(via=via)]


def done(task_id, title, via=None):
    task = {"id": task_id, "title": title, "status": "done", "output": "Il fait 18 degres a Lyon.",
            "origin": "voix", "profile": "recherche", "started": time.time() - 5, "ended": time.time()}
    if via:
        task["via"] = via
    return task


@pytest.fixture
def open_pc(context, app_server):
    """open_pc(extra_script) -> the PC's JARVIS page with the fakes (and extra
    ones) in place. Fails the test on any page error."""
    pytest.importorskip("pytest_playwright", reason="pip install -r requirements-dev.txt")
    context.set_default_timeout(10_000)
    context.route("https://api.openai.com/**", lambda route: route.fulfill(
        status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
    opened = []

    def open_(extra=""):
        page = context.new_page()
        page.add_init_script(FAKE_RTC + FAKE_SR + extra)
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(app_server.url)
        wait_ready(page)
        opened.append(errors)
        return page

    yield open_
    for errors in opened:
        assert not errors, f"erreurs dans la page : {errors}"


def with_spies(phone):
    """The phone page again, with the voice, notification and tone spies."""
    phone.add_init_script(SPIES)
    phone.reload()
    wait_ready(phone)
    phone.evaluate(RECORD)
    return phone

# ---------------------------------------------------------------- P0-27: who tells what

def test_pc_never_speaks_phone_results_holds(open_pc, remote_page):
    pc = open_pc("window.__notifPerm = 'granted';" + SPIES)
    pc.wait_for_function("window.__rec && __rec.running")  # the PC page leads (it listens)
    pc.evaluate("document.hasFocus = () => false")  # away from the PC: a notification would show
    pc.evaluate(RECORD + "; __tones.length = 0")
    phone = with_spies(remote_page)

    # A phone's and a Siri result: listed in the PC's task panel, never told there.
    publish("task", done("t-phone", "Météo Lyon", via=phone_origin()))
    publish("task", done("t-siri", "Trajet", via=SIRI))
    publish("reminder", {"id": "r-phone", "title": "Pain", "text": "Acheter du pain", "late_minutes": 0,
                         "via": phone_origin()})
    pc.wait_for_selector(".task.done:has-text('Météo Lyon')")
    pc.wait_for_selector(".task.done:has-text('Trajet')")
    phone.wait_for_function("__spoken.length === 2")  # the phone tells its own two
    pc.wait_for_timeout(800)
    assert pc.evaluate("__spoken") == [] and pc.evaluate("__tones") == []
    assert pc.evaluate("__notes") == [] and pc.evaluate("__delivered") == []
    assert not pc.is_visible("#badge")
    assert pc.locator(".card:has-text('Acheter du pain')").count() == 0  # not the PC's reminder
    assert pc.locator("#card-notif-ask").count() == 0
    assert waiting(SIRI) == ["t-siri"]  # nobody acknowledged it for Siri
    assert sorted(phone.evaluate("__spoken")) == ["Monsieur, la tâche « Météo Lyon » est terminée.",
                                                  "Monsieur, un rappel : Acheter du pain"]
    until(lambda: waiting(phone_origin()) == [])  # acknowledged by the phone itself

    # A reload of the PC page: « Pendant votre absence » never lists them either.
    pc.reload()
    wait_ready(pc)
    pc.wait_for_timeout(1000)
    assert pc.evaluate("__spoken") == []
    assert waiting(SIRI) == ["t-siri"]

    # The spies do work: the PC's own result is told on the PC, never on the phone.
    phone.evaluate("__spoken.length = 0")
    publish("task", done("t-pc", "Rapport"))
    pc.wait_for_function("__spoken.length === 1")
    assert pc.evaluate("__spoken") == ["Monsieur, la tâche « Rapport » est terminée."]
    phone.wait_for_selector(".task.done:has-text('Rapport')", state="attached")
    phone.wait_for_timeout(500)
    assert phone.evaluate("__spoken") == []


def test_remote_page_ignores_the_pc_busy_screen_holds(open_pc, remote_page, monkeypatch):
    from jarvis import desktop
    monkeypatch.setattr(desktop, "attention_state", lambda: "fullscreen")  # a film on the PC
    pc = open_pc(SPIES)
    pc.wait_for_function("__jarvis.state.quiet === true")
    phone = with_spies(remote_page)
    assert phone.evaluate("__jarvis.state.quiet") is False
    assert phone.evaluate("__jarvis.api('/api/delivery')")["attention"] == "ok"
    assert phone.evaluate("async () => (await import('/static/js/delivery.js')).quietNow()") is False
    phone.evaluate("__tones.length = 0")
    pc.evaluate("__tones.length = 0")
    publish("task", done("t-phone", "Météo Lyon", via=phone_origin()))
    publish("task", done("t-pc", "Rapport"))
    phone.wait_for_function("__spoken.length === 1")
    assert phone.evaluate("__spoken") == ["Monsieur, la tâche « Météo Lyon » est terminée."]
    assert phone.evaluate("__tones") == [880, 660]  # the alert earcon
    # The PC keeps its own result behind the badge: its screen is busy.
    pc.wait_for_function("__jarvis.state.pending === 1")
    assert pc.evaluate("__spoken") == [] and pc.evaluate("__tones") == []
    # Even told the PC's state by mistake, the phone page doesn't let it keep it quiet.
    told = []

    def busy_pc(route):
        told.append(route.request.url)
        route.fulfill(json={"leader": None, "quiet_hours": "", "quiet": False, "dnd_until": None,
                            "attention": "fullscreen"})

    phone.route("**/api/delivery", busy_pc)
    phone.evaluate("__jarvis.bus.emit('server:config', {keys: []})")
    phone.wait_for_timeout(500)
    assert told
    assert phone.evaluate("async () => (await import('/static/js/delivery.js')).quietNow()") is False

# ---------------------------------------------------------------- P0-27b: alerts as text

def test_remote_alert_text_renders_as_plain_text_holds(jarvis, remote_page):
    from jarvis import events, inbox
    dialogs = []
    for page in (jarvis, remote_page):
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    events.publish_pc("warning", {"kind": "remote", "text": HOSTILE, "plain": True})
    card = jarvis.locator(".card.warning", has_text="ev.il")
    card.wait_for()
    assert card.locator(".body").text_content() == HOSTILE
    assert card.locator(".body img, .body a").count() == 0
    assert jarvis.locator("#cards img[src='x'], a[href^='https://ev.il']").count() == 0
    # Nor from the inbox, when the PC page opens later.
    inbox.add("warning", {"kind": "remote", "text": HOSTILE + " (2)", "plain": True})
    jarvis.reload()
    wait_ready(jarvis)
    again = jarvis.locator(".card.warning", has_text="(2)")
    again.wait_for()
    assert again.locator(".body").text_content() == HOSTILE + " (2)"
    assert jarvis.locator("img[src='x'], a[href^='https://ev.il']").count() == 0
    # The phone never got it.
    remote_page.wait_for_timeout(500)
    assert remote_page.locator(".card.warning").count() == 0
    assert dialogs == []

# ---------------------------------------------------------------- P0-28: no wake word on the phone

def assert_no_wake_word(page):
    page.wait_for_timeout(3000)  # past wake.js's start-up wait
    assert page.evaluate("__recs.length") == 0  # no recognizer was ever created
    assert page.evaluate("__jarvis.state.mode") == "off"
    assert not page.is_visible("#wakeBtn")
    assert page.evaluate("async () => (await import('/static/js/wake.js')).wakeWanted()") is False


def test_wake_word_is_off_on_remote_and_ios_pages_holds(remote_page, jarvis, browser, app_server):
    # The phone: off even when this browser once switched it on.
    assert_no_wake_word(remote_page)
    remote_page.evaluate("localStorage.setItem('jarvis.settings', JSON.stringify({wake: true}))")
    remote_page.reload()
    wait_ready(remote_page)
    assert_no_wake_word(remote_page)
    # The PC's own page opened on an iPhone (iOS, not remote): off too.
    context = browser.new_context(user_agent=IPHONE_UA, permissions=["microphone"])
    try:
        context.set_default_timeout(10_000)
        context.add_init_script(FAKE_RTC + FAKE_SR)
        ios = context.new_page()
        errors = []
        ios.on("pageerror", lambda err: errors.append(str(err)))
        ios.goto(app_server.url)
        wait_ready(ios)
        assert ios.evaluate("__jarvis.state.remote") is False
        assert_no_wake_word(ios)
        assert errors == []
    finally:
        context.close()
    # A recognizer still running when a session opens on such a page, even
    # mid wake word, is stopped before the microphone is asked for.
    jarvis.evaluate(MIC_SPY)
    for become in ("__jarvis.state.remote = true",
                   f"Object.defineProperty(navigator, 'userAgent', {{value: '{IPHONE_UA}', configurable: true}})"):
        jarvis.wait_for_function("window.__rec && __rec.running && __jarvis.state.mode === 'standby'")
        jarvis.evaluate("__gum.length = 0")
        jarvis.evaluate(f"""() => {{ {become};
            __jarvis.state.wake = {{ final: false, command: "" }};
            __jarvis.voice.connect(); }}""")
        jarvis.wait_for_function("__gum.length === 1")
        assert jarvis.evaluate("__gum") == [0], become
        assert jarvis.evaluate("__recs.every(r => !r.running)")
        jarvis.wait_for_function("__jarvis.state.mode === 'live'")
        jarvis.evaluate("""() => { __jarvis.state.remote = false; delete navigator.userAgent;
            __jarvis.state.wake = null; __jarvis.voice.sleep(); }""")
        jarvis.wait_for_function("__jarvis.state.mode === 'standby'")

# ---------------------------------------------------------------- P1 (C2): the real gate, end to end

# What the browser reports the CSP refused (the remote pages carry it for real here).
VIOLATIONS = ("window.__csp = []; document.addEventListener('securitypolicyviolation', "
              "e => __csp.push(e.violatedDirective + ' ' + e.blockedURI));")
REPORT = {
    "title": "Ventes du trimestre",
    "kpis": [{"label": "Chiffre d'affaires", "value": "12 480 €", "delta": "+12 %"}],
    "chart": {"type": "bar", "categories": ["janvier", "février", "mars"],
              "series": [{"name": "2026", "data": [1.5, 2.25, 1234.5]}]},
    "table": {"columns": ["Mois", "CA"], "rows": [["janvier", 980], ["février", 15], ["mars", 1234.5]]},
    "markdown": "## Conclusion\n\nEn **hausse**.",
}


@pytest.fixture
def fresh_iphone(browser, app_server):
    """fresh_iphone(bypass_csp=True, extra="") -> a new iPhone browser page that was
    never paired (no cookie), with the fake WebRTC. Fails the test on any page error."""
    pytest.importorskip("pytest_playwright", reason="pip install -r requirements-dev.txt")
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


@pytest.fixture
def pc_api(app_server):
    """The PC's own calls (its page token, its port): the local path."""
    from jarvis import security
    with httpx.Client(base_url=app_server.url, headers={"X-Jarvis-Token": security.TOKEN}, trust_env=False,
                      timeout=10) as client:
        yield client


def poll(page, expression, timeout=10.0):
    """page.evaluate until true: Playwright's string waits need 'unsafe-eval', which the remote CSP refuses."""
    from playwright.sync_api import Error as PageError
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


def test_real_gate_end_to_end_holds(fresh_iphone, pc_api, app_server, monkeypatch):
    from jarvis import config, devices, events, remote, security
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)  # the PC's daily cap: required to switch on
    assert remote.READY is True and remote.is_enabled() is False  # off by default
    phone = fresh_iphone()
    seen = []  # every response of the Serve listener: (status, path, Set-Cookie headers)

    def record(response):
        if response.url.startswith(app_server.remote_url):
            cookies = [h["value"] for h in response.headers_array() if h["name"].lower() == "set-cookie"]
            seen.append((response.status, urlsplit(response.url).path, cookies))
    phone.on("response", record)

    # 0. Remote access off (fresh install): a closed door that says so.
    phone.goto(app_server.remote_url)
    phone.wait_for_function("window.__pair && window.__pair.ready")
    assert phone.evaluate("window.__pair.state") == "off" and (403, "/", []) in seen

    # 1. The PC switches it on and opens pairing, from its own side only.
    r = pc_api.post("/api/remote/state", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True, r.text
    assert pc_api.post("/api/remote/pairing", json={"open": True}).status_code == 200

    # 2. The unpaired page holds no token, neither the PC's nor a device's.
    phone.reload()
    phone.wait_for_function("window.__pair && window.__pair.ready")
    assert phone.evaluate("window.__pair.state") == "pair"
    assert phone.evaluate("!document.querySelector('meta[name=jarvis-token]')")
    assert security.TOKEN not in phone.content() and remote._tokens == {}

    # 3. The phone asks: a code on the phone, the same one on the PC, and no device yet.
    phone.click("button:has-text('Utiliser JARVIS dans Safari')")
    phone.fill("#pairName", "iPhone de test")
    phone.click("button:has-text(\"Demander l'accès\")")
    phone.wait_for_selector(".pair-code")
    code = phone.text_content(".pair-code").strip()
    [request] = [x for x in pc_api.get("/api/remote/pair-requests").json() if x["status"] == "waiting"]
    assert (request["code"], request["ip"], request["login"]) == (code, IP, LOGIN)
    assert devices.active() == []
    [pair_cookie] = [c for c in phone.context.cookies() if c["name"] == "__Host-jarvis-pair"]
    assert pair_cookie["httpOnly"] and pair_cookie["secure"] and pair_cookie["sameSite"] == "Strict"

    # 4. The PC allows it; the phone's next poll collects its device, then JARVIS opens.
    r = pc_api.post(f"/api/remote/pair-requests/{request['id']}/allow", json={"code": code})
    assert r.status_code == 200, r.text
    with phone.expect_navigation(timeout=15_000):
        phone.wait_for_selector(".pair-title:has-text('Associé')", timeout=10_000)
    wait_ready(phone)
    [device] = devices.active()
    assert (device["name"], device["ip"], device["login"]) == ("iPhone de test", IP, LOGIN)
    # The device cookie as the server set it: __Host-, HttpOnly, Secure, SameSite=Strict, Path=/, no Domain...
    [set_cookie] = [c for status, path, cookies in seen if path == "/api/remote/pair-status"
                    for c in cookies if c.startswith("__Host-jarvis=d_")]
    assert {a.strip() for a in set_cookie.split(";")[1:]} == \
        {"Path=/", "Secure", "HttpOnly", "SameSite=Strict", "Max-Age=34560000"}
    # ...as Chromium keeps it, out of the page's reach; the pairing cookie is gone.
    [cookie] = [c for c in phone.context.cookies() if c["name"] == "__Host-jarvis"]
    assert cookie["httpOnly"] and cookie["secure"] and cookie["sameSite"] == "Strict" and cookie["path"] == "/"
    assert cookie["value"].startswith(device["id"] + ".") and "jarvis" not in phone.evaluate("document.cookie")
    assert not [c for c in phone.context.cookies() if c["name"] == "__Host-jarvis-pair"]

    # 5. JARVIS with the device's own page token (never the PC's), and its live stream.
    token = phone.evaluate("document.querySelector('meta[name=jarvis-token]').content")
    assert token and token != security.TOKEN and security.TOKEN not in phone.content()
    assert remote._tokens[hashlib.sha256(token.encode()).hexdigest()]["device"] == device["id"]
    assert phone.evaluate("({remote: __jarvis.state.remote, origin: __jarvis.state.origin})") == \
        {"remote": True, "origin": f"app:{device['id']}"}
    until(lambda: events.has_subscribers(lambda c: c is not None and c.device_id == device["id"]))

    # 6. A voice session opens (fake WebRTC), minted through the gate with the phone's tools only.
    minted = len(app_server.sessions)
    phone.click("#orbBtn")
    phone.wait_for_function("__jarvis.state.mode === 'live'")
    assert remote.mints_today(device["id"]) == 1
    offered = {t["name"] for t in app_server.sessions[minted]["session"]["tools"]}
    assert "display_report" in offered and not offered & {"open_app", "look_at_screen"}
    phone.evaluate("__jarvis.voice.sleep()")
    phone.wait_for_function("__jarvis.state.mode !== 'live'")

    # 7. PC-only routes, asked by the phone's own page with its own token: 403 « Réservé au PC. ».
    probes = (("GET", "/api/remote/devices"), ("GET", "/api/health"), ("POST", "/api/shutdown"),
              ("POST", "/api/remote/complet"), ("POST", "/api/settings/openai-key"))
    for method, path in probes:
        got = phone.evaluate("""([method, path, token]) => fetch(path, {method,
            headers: {'X-Jarvis-Token': token, 'Content-Type': 'application/json'},
            body: method === 'GET' ? undefined : '{"duration": "7d", "key": "sk-x", "confirm": true}'})
            .then(async r => [r.status, (await r.json()).detail])""", [method, path, token])
        assert got == [403, "Réservé au PC."], (method, path, got)
    assert remote.complet_until() == 0 and pc_api.get("/api/remote/devices").status_code == 200  # still running

    # 8. The PC removes it: its stream closes at once, and its next request answers 401.
    phone.wait_for_timeout(300)  # the probes' responses are handed over before the count starts
    seen.clear()
    r = pc_api.delete(f"/api/remote/devices/{device['id']}")
    assert r.status_code == 200 and r.json()["ok"] is True
    until(lambda: not events.has_subscribers(lambda c: c is not None and c.device_id == device["id"]))
    deadline = time.time() + 15
    while not any(path.startswith("/api/") and path not in {p for _, p in probes} for _, path, _ in seen) \
            and time.time() < deadline:
        phone.wait_for_timeout(100)  # Playwright hands the page's responses over while it waits
    probed = {path for _, path in probes}
    calls = [(status, path, cookies) for status, path, cookies in seen
             if path.startswith("/api/") and path not in probed]
    assert {status for status, _, _ in calls} == {401}, calls
    assert "__Host-jarvis=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict" in calls[0][2]
    # The page reloads on that 401 and says « Appareil retiré »; the cookie is gone from the browser.
    phone.wait_for_selector("#pairBody .pair-title", timeout=15_000)
    assert phone.evaluate("window.__pair.state") == "revoked"
    assert not [c for c in phone.context.cookies() if c["name"] == "__Host-jarvis"]
    # The old cookie and page token, replayed through Serve: 401 too.
    with httpx.Client(trust_env=False, timeout=10) as client:
        r = client.get(app_server.remote_url + "api/config",
                       headers={"Cookie": f"__Host-jarvis={cookie['value']}", "X-Jarvis-Token": token})
        assert r.status_code == 401 and r.json()["detail"] == remote.T_REVOKED


# ---------------------------------------------------------------- P1 (C2): under the remote CSP

def test_remote_pages_run_under_the_csp_holds(fresh_iphone, app_server, context, monkeypatch):
    from jarvis import config, remote, store
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    store.save("remote.json", {"enabled": True, "host": REMOTE_HOST, "logins": [LOGIN], "paused_until": 0,
                               "complet_until": 0, "published": False, "changed_at": time.time(),
                               "changed_by": "pc"})
    phone = fresh_iphone(bypass_csp=False, extra=VIOLATIONS)
    console = []
    phone.on("console", lambda m: console.append(m.text))

    # The pairing page, window closed then open.
    response = phone.goto(app_server.remote_url)
    assert response.headers["content-security-policy"] == remote.CSP
    poll(phone, "window.__pair && window.__pair.ready")
    assert phone.evaluate("window.__pair.state") == "closed"
    remote.open_pairing()
    phone.reload()
    poll(phone, "window.__pair && window.__pair.ready")
    assert phone.evaluate("window.__pair.state") == "pair"
    assert phone.evaluate("__csp") == []

    # Paired for real (the request, the PC's approval, the poll), then JARVIS itself.
    phone.click("button:has-text('Utiliser JARVIS dans Safari')")
    phone.click("button:has-text(\"Demander l'accès\")")
    poll(phone, "!!document.querySelector('.pair-code')")
    [request] = remote.pairing_requests()
    with phone.expect_navigation(timeout=15_000):  # the next poll collects the cookie, then JARVIS opens
        remote.allow_pairing(request["id"])
    poll(phone, "!!(window.__jarvis && __jarvis.state.ready && __jarvis.state.synced)", timeout=15.0)
    assert phone.evaluate("__jarvis.state.remote") is True
    assert phone.evaluate("typeof marked === 'object' && typeof DOMPurify === 'function'")  # the pinned CDN scripts
    assert phone.evaluate("__csp") == []

    # A report with a chart (ApexCharts from the pinned CDN), its table and its notes.
    phone.evaluate("r => import('/static/js/report.js').then(m => m.showReport(r))", REPORT)
    poll(phone, "!!document.querySelector('#rchart[data-state=ready], #rchart[data-state=unavailable]')",
         timeout=25.0)
    assert phone.evaluate("__csp") == []
    assert not [m for m in console if "integrity" in m or "Content Security Policy" in m], console
    if phone.get_attribute("#rchart", "data-state") != "ready":
        pytest.skip("ApexCharts (CDN) indisponible")
    assert phone.locator("#rchart svg.apexcharts-svg").count() == 1
    assert phone.locator("#rtable tbody tr").count() == 3 and phone.text_content("#rmd h2") == "Conclusion"
    phone.keyboard.press("Escape")
    poll(phone, "!document.getElementById('report').open")

    # A voice session (connect-src api.openai.com, media from the microphone).
    phone.click("#orbBtn")
    poll(phone, "__jarvis.state.mode === 'live'")
    phone.evaluate("__jarvis.voice.sleep()")
    poll(phone, "__jarvis.state.mode !== 'live'")
    phone.wait_for_timeout(1000)  # late loads (fonts, lazy modules)
    assert phone.evaluate("__csp") == []

    # The QR code: drawn only in the PC's pairing view; served here with the remote
    # CSP added, the pinned qrcode-generator loads and draws its SVG with no violation.
    page = context.new_page()
    pc_console = []
    page.on("console", lambda m: pc_console.append(m.text))
    html = httpx.get(app_server.url, timeout=10, trust_env=False).text
    page.route(app_server.url, lambda route: route.fulfill(status=200, body=html, headers={
        "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
        "Content-Security-Policy": remote.CSP}))
    page.add_init_script(FAKE_RTC + FAKE_SR + VIOLATIONS)
    served = page.goto(app_server.url)
    assert served.headers["content-security-policy"] == remote.CSP
    poll(page, "!!(window.__jarvis && __jarvis.state.ready && __jarvis.state.synced)", timeout=15.0)
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    page.click("#settingsDialog .set-tab:has-text('Accès à distance')")
    page.wait_for_selector(".rm-qr svg.rm-qr-svg, .rm-qr .rm-qr-fallback", timeout=20_000)
    assert page.evaluate("__csp") == []
    assert not [m for m in pc_console if "integrity" in m or "Content Security Policy" in m], pc_console
    if page.locator(".rm-qr svg.rm-qr-svg").count() == 0:
        pytest.skip("qrcode-generator (CDN) indisponible")
    assert page.locator(".rm-qr svg.rm-qr-svg path").count() == 1
    assert REMOTE_HOST in page.get_attribute(".rm-qr svg.rm-qr-svg", "aria-label")  # the URL, nothing else
    page.close()
