"""Réglages › Accès à distance (spec 4.15) in Chromium.

PC view: the jarvis page with the remote core's routes (/api/remote/*, and
the Serve routes) faked with page.route, so every state can be shown; the
integrator removes the fakes once the remote core is merged. Phone view: the
paired iPhone (remote_page). The proofs: strings from a device, whois or the
audit render as text (P0 30b), and the phone's pause asks first (P1)."""
import copy
import json
import re
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

HOST, LOGIN, IP = "jarvis-pc.tail0000.ts.net", "monsieur@example.com", "100.101.102.103"
URL = f"https://{HOST}/"
COMMAND = "tailscale serve --bg --https=443 http://127.0.0.1:8789"
COMMAND_FULL = '& "C:\\Program Files\\Tailscale\\tailscale.exe" serve --bg --https=443 http://127.0.0.1:8789'
QR_URL = "https://cdn.jsdelivr.net/npm/qrcode-generator@2.0.4/dist/qrcode.js"
QR_SRI = "sha384-e9EFD6BGC90bkW9aDV5xbbBfzwN7G8YImHao2lfLVKV/hPB0E0go+H3I64h7oHtA"
CONSENT = "https://login.tailscale.com/f/serve?node=nJARVIS000CNTRL"
EVIL = "<img src=x onerror=alert(1)>[x](https://ev.il)"
DEVICE = {"id": "d_3f9a1c2b4d5e6f70", "name": "iPhone de test", "kind": "app", "login": LOGIN, "ip": IP,
          "ips": [IP, "fd7a:115c:a1e0::1234"], "node_id": "nIPHONE000CNTRL", "os": "iOS", "host_name": "iphone-de-test",
          "paired_at": 1760000000.0, "paired_seq": 0, "last_seen": None, "revoked": False, "revoked_at": None,
          "siri_keys": []}


def request(rid, code, name="iPhone", **over):
    return {"id": rid, "code": code, "name": name, "ip": IP, "login": LOGIN, "os": "iOS", "host_name": "iphone-de-test",
            "created": time.time(), "expires_in": 540, "status": "waiting", **over}


def pc_state(**over):
    return {"enabled": False, "ready": True, "paused_until": 0, "host": HOST, "host_source": "detected",
            "logins": [LOGIN], "logins_source": "env", "port": 8789,
            "listener": {"running": False, "port": 8789, "error": ""}, "cap_usd": 5.0, "cap_ok": True,
            "complet_until": 0, "pairing_until": 0, "published": False, "devices": [], "requests": [],
            "tailscale": {"installed": True, "running": True, "dns_name": HOST, "login": LOGIN},
            "serve_command": COMMAND, "url": URL, **over}


def norm(text):
    return re.sub(r"[\u202f\u00a0]", " ", text or "")


class FakeRemote:
    """The remote core's routes as the page sees them; every call is recorded."""

    def __init__(self, page, state=None, serve=None, phone=None):
        self.page = page
        self.state = state or pc_state()
        self.serve = serve or {"state": "ready", "detail": "Tailscale publie JARVIS sur votre réseau Tailscale seulement.",
                               "url": URL, "command": COMMAND, "command_full": COMMAND_FULL}
        self.phone = phone
        self.calls = []
        self.answers = {}   # (method, path) -> list of (status, body) used before the defaults
        self.audit = []
        page.route("**/api/remote/**", self.handle)

    def answer(self, method, path, status, body):
        self.answers.setdefault((method, path), []).append((status, body))

    def calls_to(self, method, path):
        return [body for m, p, body in self.calls if m == method and p == path]

    def handle(self, route):
        req = route.request
        path = re.sub(r"^https?://[^/]+", "", req.url).split("?")[0]
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, path, body))
        queued = self.answers.get((req.method, path))
        if queued:
            status, data = queued.pop(0)
            route.fulfill(status=status, json=data)
            return
        route.fulfill(json=self.default(req.method, path, body))

    def default(self, method, path, body):
        st = self.state
        if path == "/api/remote/state" and method == "GET":
            return copy.deepcopy(self.phone if self.phone is not None else st)
        if path == "/api/remote/state":
            st["enabled"] = bool(body["enabled"])
            return copy.deepcopy(st)
        if path == "/api/remote/serve":
            return dict(self.serve)
        if path == "/api/remote/serve/publish":
            return {"ok": False, "state": "consent", "consent_url": CONSENT, "error": ""}
        if path == "/api/remote/pairing":
            st["pairing_until"] = time.time() + 600 if body["open"] else 0
            return {"open": bool(body["open"]), "until": st["pairing_until"], "url": URL}
        if path.endswith("/allow"):
            return {"ok": True, "request": {}}
        if path.endswith("/deny"):
            return {"ok": True}
        if path.startswith("/api/remote/devices/") and method == "PATCH":
            device = next(d for d in st["devices"] if path.endswith(d["id"]))
            device["name"] = body["name"]
            return dict(device)
        if path.startswith("/api/remote/devices/") and method == "DELETE":
            st["devices"] = [d for d in st["devices"] if not path.endswith(d["id"])]
            return {"ok": True, "cancelled_tasks": 2}
        if path == "/api/remote/complet":
            st["complet_until"] = {"never": 0, "24h": time.time() + 86400, "7d": time.time() + 7 * 86400}[body["duration"]]
            return {"complet_until": st["complet_until"]}
        if path == "/api/remote/audit":
            return {"lines": self.audit}
        if path == "/api/remote/pause":
            return {"paused_until": time.time() + body["hours"] * 3600}
        if path == "/api/remote/forget":
            return {"ok": True}
        return {}


def open_distance(page):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    if not page.is_visible("#settingsDialog .set-tab[aria-current='true']:has-text('Accès à distance')"):
        page.click("#settingsDialog .set-tab:has-text('Accès à distance')")
    page.wait_for_selector("#set-sec-distance .rm-block")


def section(page):
    return norm(page.text_content("#set-sec-distance"))


@pytest.fixture
def pc(jarvis):
    jarvis.context.grant_permissions(["clipboard-read", "clipboard-write"])
    fake = FakeRemote(jarvis)
    dialogs = []
    jarvis.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    fake.dialogs = dialogs
    return fake

# ---------------------------------------------------------------- the PC view


def test_pc_view_status_and_the_switch(pc, jarvis):
    open_distance(jarvis)
    text = section(jarvis)
    assert "installé et connecté" in text and HOST in text and "(détectée : confirmée en activant l'accès)" in text
    assert LOGIN in text and "(défini dans .env)" in text
    # example.com is not a personal domain: information only, never a blocker.
    assert "Compte professionnel ? Préférez un compte Tailscale personnel (voir le guide)." in text
    switch = "#set-remote-enabled"
    assert jarvis.is_enabled(switch) and not jarvis.is_checked(switch)
    assert norm(jarvis.text_content("label[for='set-remote-enabled']")).strip() == "Accès à distance"
    assert "sans plafond de dépense par jour" in text  # why it may be refused
    jarvis.click(switch)
    jarvis.wait_for_selector("[data-key='remote-enabled'] .set-status:has-text('Accès à distance activé.')")
    assert pc.calls_to("POST", "/api/remote/state") == [{"enabled": True, "host": HOST, "login": LOGIN}]
    assert jarvis.is_checked(switch)
    jarvis.click(switch)
    jarvis.wait_for_selector("[data-key='remote-enabled'] .set-status:has-text('Accès à distance coupé.')")
    assert pc.calls_to("POST", "/api/remote/state")[-1] == {"enabled": False}


def test_a_refused_switch_says_why_and_stays_off(pc, jarvis):
    cap = ("Fixez d'abord un plafond de dépense par jour (Réglages › Coûts) : il protège votre crédit OpenAI quand "
           "JARVIS est utilisé à distance.")
    pc.answer("POST", "/api/remote/state", 400, {"detail": cap})
    pc.state.update(cap_ok=False, cap_usd=0)
    open_distance(jarvis)
    assert "Aucun plafond de dépense par jour" in section(jarvis)
    jarvis.click("#set-remote-enabled")
    jarvis.wait_for_selector("[data-key='remote-enabled'] .set-status.err")
    assert norm(jarvis.text_content("[data-key='remote-enabled'] .set-status")) == cap
    assert not jarvis.is_checked("#set-remote-enabled")
    jarvis.click("[data-key='remote-status'] button:has-text('Fixer un plafond')")
    jarvis.wait_for_selector("#settingsDialog .set-tab[aria-current='true']:has-text('Coûts')")


def test_a_personal_login_gets_no_warning(jarvis):
    fake = FakeRemote(jarvis, state=pc_state(logins=["monsieur@github"], logins_source="saved",
                                             tailscale={"installed": True, "running": True, "dns_name": HOST,
                                                        "login": "monsieur@github"}))
    open_distance(jarvis)
    text = section(jarvis)
    assert "monsieur@github" in text and "(enregistré)" in text and "Compte professionnel" not in text
    assert fake.calls_to("GET", "/api/remote/state")


def test_publish_shows_only_a_tailscale_consent_link_and_the_manual_command(pc, jarvis):
    open_distance(jarvis)
    jarvis.wait_for_selector(".rm-serve-ready")
    assert norm(jarvis.text_content(".rm-serve")) == "Serve : prêt"
    codes = jarvis.eval_on_selector_all("[data-key='remote-serve'] .rm-command code", "els => els.map(e => e.textContent)")
    assert codes == [COMMAND, COMMAND_FULL]
    jarvis.click("#rm-copy-command-full")
    assert jarvis.evaluate("navigator.clipboard.readText()") == COMMAND_FULL
    jarvis.click("#rm-copy-command")
    assert jarvis.evaluate("navigator.clipboard.readText()") == COMMAND
    assert "Copié." in section(jarvis)

    jarvis.click("#rm-publish")
    link = jarvis.wait_for_selector("a.rm-link")
    assert link.get_attribute("href") == CONSENT and link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener noreferrer"
    assert pc.calls_to("POST", "/api/remote/serve/publish") == [None]
    # Any other link is never shown, nor made clickable.
    pc.answer("POST", "/api/remote/serve/publish", 200,
              {"ok": False, "state": "consent", "consent_url": "https://evil.example/login.tailscale.com/f", "error": ""})
    jarvis.click("#rm-publish")
    jarvis.wait_for_selector("text=Lien d'autorisation inattendu")
    assert jarvis.locator("#set-sec-distance a").count() == 0
    assert "evil.example" not in section(jarvis)
    # An error is said as text; Revérifier asks Serve again.
    pc.answer("POST", "/api/remote/serve/publish", 200,
              {"ok": False, "state": "error", "consent_url": "", "error": "Access denied: serve config denied"})
    jarvis.click("#rm-publish")
    jarvis.wait_for_selector(".rm-publish-error:has-text('Access denied')")
    before = len(pc.calls_to("GET", "/api/remote/serve"))
    pc.serve = {**pc.serve, "state": "funnel", "detail": "Funnel actif : Tailscale publie ce PC sur internet."}
    jarvis.click("#rm-serve-check")
    jarvis.wait_for_selector(".rm-serve-funnel")
    assert norm(jarvis.text_content(".rm-serve")) == "Serve : Funnel actif"
    assert len(pc.calls_to("GET", "/api/remote/serve")) > before


def test_pairing_shows_the_url_a_text_fallback_for_the_qr_and_asks_the_code_when_two_wait(pc, jarvis):
    qr_asked = []

    def no_cdn(route):  # a faked body would fail the integrity check: refuse it instead
        qr_asked.append(route.request.url)
        route.fulfill(status=404, body="")
    jarvis.route(QR_URL, no_cdn)
    pc.state["requests"] = [request("r_000000000001", "1234", "iPhone"), request("r_000000000002", "4821", "iPhone pro")]
    open_distance(jarvis)
    jarvis.click("#rm-pair-open")
    assert pc.calls_to("POST", "/api/remote/pairing") == [{"open": True}]
    jarvis.wait_for_selector(".rm-qr-fallback")
    assert qr_asked == [QR_URL]
    assert "tapez l'adresse ci-dessus dans Safari" in norm(jarvis.text_content(".rm-qr-fallback"))
    assert jarvis.text_content("code.rm-url") == URL
    assert re.search(r"Association ouverte encore (10:00|9:\d\d)\.", norm(jarvis.text_content("#rm-countdown")))
    jarvis.wait_for_selector(".rm-request[data-request='r_000000000002']")
    assert "Plusieurs demandes en attente : n'autorisez que celle dont le code s'affiche sur votre iPhone." \
        in section(jarvis)
    row = ".rm-request[data-request='r_000000000002']"
    cells = jarvis.eval_on_selector_all(f"{row} .rm-cell", "els => els.map(e => e.textContent)")
    assert cells == ["iOS", "iphone-de-test", IP, LOGIN]
    assert jarvis.text_content(f"{row} .rm-code") == "4821"
    assert norm(jarvis.text_content(f"{row} .rm-name")) == "« iPhone pro »"
    # Two requests wait: Autoriser first asks for the 4 digits.
    jarvis.click(f"{row} button:has-text('Autoriser')")
    jarvis.wait_for_selector(f"{row} .rm-code-input")
    assert pc.calls_to("POST", "/api/remote/pair-requests/r_000000000002/allow") == []
    pc.answer("POST", "/api/remote/pair-requests/r_000000000002/allow", 409,
              {"detail": "Code différent : vérifiez le code affiché sur l'iPhone."})
    jarvis.fill(f"{row} .rm-code-input", "1234")
    jarvis.click(f"{row} .rm-code-form button[type='submit']")
    jarvis.wait_for_selector(f"{row} .set-status.err")
    assert "Code différent" in norm(jarvis.text_content(f"{row} .set-status"))
    jarvis.wait_for_timeout(3500)  # a poll meanwhile must not wipe what is being typed
    assert jarvis.is_visible(f"{row} .rm-code-input")
    jarvis.fill(f"{row} .rm-code-input", "4821")
    pc.state["requests"] = [pc.state["requests"][0], {**pc.state["requests"][1], "status": "approved"}]
    jarvis.click(f"{row} .rm-code-form button[type='submit']")
    jarvis.wait_for_selector("[data-key='remote-pairing'] > .set-status:has-text('Demande 4821 autorisée')")
    assert pc.calls_to("POST", "/api/remote/pair-requests/r_000000000002/allow") == [{"code": "1234"}, {"code": "4821"}]
    jarvis.wait_for_selector(f"{row}:has-text('Autorisée')")
    # A single request is allowed without typing.
    jarvis.click(".rm-request[data-request='r_000000000001'] button:has-text('Refuser')")
    jarvis.wait_for_timeout(300)
    assert pc.calls_to("POST", "/api/remote/pair-requests/r_000000000001/deny") == [None]
    jarvis.click("#rm-pair-close")
    jarvis.wait_for_selector("#rm-pair-open")
    assert pc.calls_to("POST", "/api/remote/pairing")[-1] == {"open": False}


def test_the_qr_code_is_drawn_module_by_module_as_svg(pc, jarvis):
    # A stand-in for qrcode-generator's API (getModuleCount, isDark): no CDN in the tests.
    jarvis.evaluate("""() => { window.qrcode = (type, level) => ({
        added: '', addData(t) { this.added = t; }, make() {},
        getModuleCount() { return 21; }, isDark(r, c) { return (r * 3 + c) % 5 === 0; } }); }""")
    open_distance(jarvis)
    jarvis.click("#rm-pair-open")
    svg = jarvis.wait_for_selector("svg.rm-qr-svg")
    assert svg.get_attribute("viewBox") == "0 0 29 29" and svg.get_attribute("role") == "img"
    assert URL in svg.get_attribute("aria-label")
    d = jarvis.eval_on_selector("svg.rm-qr-svg path", "e => e.getAttribute('d')")
    assert d.startswith("M4 4h1v1h-1z") and d.count("h1v1h-1z") == sum(
        1 for r in range(21) for c in range(21) if (r * 3 + c) % 5 == 0)
    assert jarvis.locator("#set-sec-distance img, #set-sec-distance foreignObject").count() == 0
    assert jarvis.locator("script[data-lazy='qrcode']").count() == 0  # the library was there: no CDN load


def test_the_qr_library_is_pinned_with_its_integrity_hash():
    source = (Path(__file__).resolve().parents[2] / "static" / "js" / "remote-settings.js").read_text(encoding="utf-8")
    assert f'src: "{QR_URL}"' in source and f'integrity: "{QR_SRI}"' in source
    assert "createSvgTag" not in source and "createImgTag" not in source and "innerHTML" not in source


def test_devices_rename_and_remove_with_a_confirmation(pc, jarvis):
    pc.state["devices"] = [dict(DEVICE)]
    open_distance(jarvis)
    item = f".rm-device[data-device='{DEVICE['id']}']"
    jarvis.wait_for_selector(item)
    assert jarvis.text_content(f"{item} .rm-device-name") == "iPhone de test"
    assert LOGIN in jarvis.text_content(item) and IP in jarvis.text_content(item)
    assert jarvis.locator(f"{item} [data-slot='siri']").count() == 1  # where the Siri key buttons go
    jarvis.click(f"{item} button:has-text('Renommer')")
    jarvis.fill(f"{item} .rm-rename input", "iPhone pro")
    jarvis.click(f"{item} .rm-rename button[type='submit']")
    jarvis.wait_for_selector(f"{item} .rm-device-name:has-text('iPhone pro')")
    assert pc.calls_to("PATCH", f"/api/remote/devices/{DEVICE['id']}") == [{"name": "iPhone pro"}]

    jarvis.click(f"{item} button:has-text('Retirer')")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    confirm = norm(jarvis.text_content("#settingsConfirm"))
    assert "Retirer « iPhone pro » ?" in confirm and "clés Siri" in confirm and "tâches en cours" in confirm
    jarvis.click("#settingsConfirm button[value='cancel']")
    assert pc.calls_to("DELETE", f"/api/remote/devices/{DEVICE['id']}") == []
    jarvis.click(f"{item} button:has-text('Retirer')")
    jarvis.click("#settingsConfirm button[value='ok']")
    jarvis.wait_for_selector("text=Aucun appareil associé.")
    assert pc.calls_to("DELETE", f"/api/remote/devices/{DEVICE['id']}") == [None]
    assert "Tâches arrêtées : 2." in section(jarvis)


def test_full_access_from_the_iphone_is_jamais_24_h_or_7_jours(pc, jarvis):
    open_distance(jarvis)
    legend = norm(jarvis.text_content("[data-key='remote-complet'] legend"))
    assert legend == "Accès complet depuis l'iPhone"
    labels = jarvis.eval_on_selector_all(".rm-radio", "els => els.map(e => e.textContent.trim())")
    assert [norm(x) for x in labels] == ["Jamais", "24 h", "7 jours"]
    assert jarvis.is_checked("#rm-complet-never")
    jarvis.click("label[for='rm-complet-24h']")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    jarvis.click("#settingsConfirm button[value='cancel']")
    jarvis.wait_for_selector("#rm-complet-never:checked")
    assert pc.calls_to("POST", "/api/remote/complet") == []
    jarvis.click("label[for='rm-complet-24h']")
    jarvis.click("#settingsConfirm button[value='ok']")
    jarvis.wait_for_selector(".rm-complet-now:has-text('Autorisé jusqu')")
    assert jarvis.is_checked("#rm-complet-24h")
    jarvis.click("label[for='rm-complet-never']")
    jarvis.wait_for_selector(".rm-complet-now:has-text('Jamais')")
    assert pc.calls_to("POST", "/api/remote/complet") == [{"duration": "24h"}, {"duration": "never"}]


def test_recent_activity_in_plain_french(pc, jarvis):
    now = time.time()
    pc.state["devices"] = [dict(DEVICE)]
    pc.audit = [
        {"t": now - 30, "kind": "request", "caller": "app", "device": DEVICE["id"], "ip": IP, "method": "GET",
         "route": "/api/tasks", "status": 200},
        {"t": now - 90, "kind": "request", "caller": "unpaired", "ip": "100.101.102.104", "method": "POST",
         "route": "/api/remote/pair-request", "status": 403, "reason": "login"},
        {"t": now - 120, "kind": "alert", "alert": "new_device", "text": "Nouvel appareil associé : « iPhone de test »."},
    ]
    open_distance(jarvis)
    jarvis.click("#rm-audit-toggle")
    jarvis.wait_for_selector(".rm-audit-line")
    lines = [norm(x) for x in jarvis.eval_on_selector_all(".rm-audit-line", "els => els.map(e => e.textContent)")]
    assert len(lines) == 3
    assert "iPhone de test" in lines[0] and "GET /api/tasks" in lines[0] and "acceptée" in lines[0]
    assert "appareil non associé" in lines[1] and "compte non autorisé" in lines[1]
    assert "Alerte : Nouvel appareil associé" in lines[2]
    assert jarvis.get_attribute("#rm-audit-toggle", "aria-expanded") == "true"
    assert pc.calls_to("GET", "/api/remote/audit") == [None]


def test_remote_strings_render_as_text_holds(pc, jarvis):
    """P0 30b: a request, a device and audit lines carrying markup and markdown:
    no element, no link, no dialog; the text shows as it is."""
    pc.state["requests"] = [request("r_0000000000ee", "4821", EVIL, os=EVIL, host_name=EVIL, login=EVIL)]
    pc.state["devices"] = [{**DEVICE, "name": EVIL, "login": EVIL}]
    pc.state["pairing_until"] = time.time() + 600
    pc.audit = [{"t": time.time(), "kind": "alert", "alert": "login_change", "text": EVIL, "ip": EVIL},
                {"t": time.time(), "kind": "request", "caller": EVIL, "method": EVIL, "route": EVIL, "reason": EVIL}]
    jarvis.route(QR_URL, lambda route: route.fulfill(status=404, body=""))
    open_distance(jarvis)
    jarvis.wait_for_selector(".rm-request")
    jarvis.click("#rm-audit-toggle")
    jarvis.wait_for_selector(".rm-audit-line")
    jarvis.click(".rm-device button:has-text('Retirer')")  # the name goes into the dialog's title too
    jarvis.wait_for_selector("#settingsConfirm[open]")
    jarvis.wait_for_timeout(300)
    for root in ("#settingsDialog", "#settingsConfirm"):
        assert jarvis.locator(f"{root} img, {root} a, {root} b, {root} iframe").count() == 0, root
    assert jarvis.locator("a[href*='ev.il']").count() == 0
    assert pc.dialogs == []
    assert EVIL in jarvis.text_content(".rm-request .rm-name")
    assert jarvis.eval_on_selector_all(".rm-request .rm-cell", "els => els.map(e => e.textContent)") == [EVIL, EVIL, IP, EVIL]
    assert jarvis.text_content(".rm-device-name") == EVIL
    assert EVIL in jarvis.text_content("#settingsConfirm h3")
    assert EVIL in jarvis.text_content("#rm-audit-list")
    jarvis.click("#settingsConfirm button[value='cancel']")


def test_the_section_is_honest_before_the_remote_core(jarvis):
    """Without fakes this version's routes answer 501: the section says so, in French."""
    jarvis.click("#topActions button:has-text('Réglages')")
    jarvis.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    jarvis.click("#settingsDialog .set-tab:has-text('Accès à distance')")
    jarvis.wait_for_selector("#set-sec-distance .set-note")
    assert "pas encore disponible dans cette version" in section(jarvis)

# ---------------------------------------------------------------- the iPhone


@pytest.fixture
def phone(remote_page):
    fake = FakeRemote(remote_page, phone={"enabled": True, "paused_until": 0,
                                          "device": {"id": "d_e2e0000000000001", "name": "iPhone de test"},
                                          "complet_until": 0, "url": URL})
    return fake


def test_phone_view_opens_on_its_own_section(phone, remote_page):
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#settingsDialog .set-tab[aria-current='true']:has-text('Accès à distance')")
    remote_page.wait_for_selector("[data-key='remote-phone']")
    text = section(remote_page)
    assert "iPhone de test" in text and "Accès à distance actif" in text
    assert remote_page.is_visible("#rm-pause") and remote_page.is_visible("#rm-forget")
    assert norm(remote_page.text_content("#rm-pause")) == "Mettre en pause"
    assert norm(remote_page.text_content("#rm-forget")) == "Oublier cet iPhone"
    assert remote_page.locator("[data-slot='raccourci']").count() == 1  # where the Siri assistant goes
    # Nothing of the PC's: no switch, no publishing, no pairing.
    assert remote_page.locator("#set-remote-enabled, #rm-publish, #rm-pair-open").count() == 0
    assert remote_page.locator("#onboarding[open]").count() == 0


def test_phone_pause_asks_first_holds(phone, remote_page):
    """P1: the phone's pause asks « Couper l'accès depuis l'iPhone ? » first, with
    [1 h] [24 h] [Annuler]; nothing is sent until a duration is chosen."""
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#rm-pause")
    remote_page.click("#rm-pause")
    remote_page.wait_for_selector("#remotePause[open]")
    text = norm(remote_page.text_content("#remotePause"))
    assert "Couper l'accès depuis l'iPhone ?" in text
    assert "Seul le PC pourra le rallumer avant la fin de la pause." in text
    buttons = [norm(b) for b in remote_page.eval_on_selector_all("#remotePause button", "els => els.map(e => e.textContent)")]
    assert buttons == ["1 h", "24 h", "Annuler"]
    assert remote_page.evaluate("document.activeElement.textContent") == "Annuler"  # Entrée cuts nothing
    assert phone.calls_to("POST", "/api/remote/pause") == []
    remote_page.click("#remotePause button[value='cancel']")
    remote_page.wait_for_selector("#remotePause:not([open])", state="attached")
    assert phone.calls_to("POST", "/api/remote/pause") == []
    remote_page.click("#rm-pause")
    remote_page.wait_for_selector("#remotePause[open]")
    remote_page.keyboard.press("Escape")  # Échap is Annuler too
    remote_page.wait_for_timeout(200)
    assert phone.calls_to("POST", "/api/remote/pause") == []
    remote_page.click("#rm-pause")
    remote_page.click("#remotePause button[value='1']")
    remote_page.wait_for_selector("[data-key='remote-phone'] .set-status:has-text('en pause jusqu')")
    assert phone.calls_to("POST", "/api/remote/pause") == [{"hours": 1}]


def test_phone_forget_asks_first(phone, remote_page):
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#rm-forget")
    remote_page.click("#rm-forget")
    remote_page.wait_for_selector("#settingsConfirm[open]")
    text = norm(remote_page.text_content("#settingsConfirm"))
    assert "Oublier cet iPhone ?" in text and "clés Siri" in text
    remote_page.click("#settingsConfirm button[value='cancel']")
    assert phone.calls_to("POST", "/api/remote/forget") == []
    remote_page.click("#rm-forget")
    with remote_page.expect_navigation(timeout=5000):
        remote_page.click("#settingsConfirm button[value='ok']")
    assert phone.calls_to("POST", "/api/remote/forget") == [None]


def test_the_mise_en_route_never_opens_on_the_iphone(remote_page, monkeypatch):
    """A fresh data folder opens the first-run checklist on the PC; the iPhone
    never gets it: every check there is about this PC."""
    from jarvis import health
    monkeypatch.setattr(health, "onboarded", lambda: False)
    remote_page.reload()
    remote_page.wait_for_function("window.__jarvis && __jarvis.state.ready")
    remote_page.wait_for_timeout(800)
    assert remote_page.locator("#onboarding[open]").count() == 0
    remote_page.evaluate("__jarvis.bus.emit('ui:open', 'onboarding')")
    remote_page.wait_for_timeout(300)
    assert remote_page.locator("#onboarding[open]").count() == 0


def test_axe_has_no_serious_violation_in_the_pc_view(pc, jarvis):
    from test_settings_ui import axe_violations
    pc.state.update(enabled=True, devices=[dict(DEVICE)],
                    requests=[request("r_000000000001", "1234"), request("r_000000000002", "4821")])
    pc.audit = [{"t": time.time(), "kind": "request", "caller": "app", "device": DEVICE["id"], "method": "GET",
                 "route": "/api/tasks", "status": 200}]
    jarvis.route(QR_URL, lambda route: route.fulfill(status=404, body=""))
    open_distance(jarvis)
    jarvis.click("#rm-publish")
    jarvis.wait_for_selector("a.rm-link")
    jarvis.click("#rm-pair-open")
    jarvis.wait_for_selector(".rm-request")
    jarvis.click("#rm-audit-toggle")
    jarvis.wait_for_selector(".rm-audit-line")
    assert axe_violations(jarvis) == []


def test_axe_has_no_serious_violation_on_the_phone(phone, remote_page):
    from test_settings_ui import axe_violations
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#rm-pause")
    assert axe_violations(remote_page) == []
