"""Réglages › Accès à distance (spec 4.15) in Chromium.

PC view: the jarvis page on the remote core's real routes (/api/remote/*). The
server runs in this process, so the PC's side is set up where the server
keeps it: a daily cap (remote.READY is True since C2), Tailscale installed
and running (tailscale.self_info and whois faked, Serve's status and publish
answers too: no Tailscale CLI in the tests), pairing requests asked through
the remote core, real devices and audit lines. Only the P0 30b proof keeps a
faked route: the remote core cleans every device string before it is stored,
so markup could not reach the page otherwise.
Phone view: the paired iPhone (remote_page, real gate). The proofs: strings
from a device, whois or the audit render as text (P0 30b), and the phone's
pause asks first (P1)."""
import copy
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.e2e

HOST, LOGIN, IP = "jarvis-pc.tail0000.ts.net", "monsieur@example.com", "100.101.102.103"
IP2 = "100.101.102.104"
URL = f"https://{HOST}/"
QR_URL = "https://cdn.jsdelivr.net/npm/qrcode-generator@2.0.4/dist/qrcode.js"
QR_SRI = "sha384-e9EFD6BGC90bkW9aDV5xbbBfzwN7G8YImHao2lfLVKV/hPB0E0go+H3I64h7oHtA"
CONSENT = "https://login.tailscale.com/f/serve?node=nJARVIS000CNTRL"
EVIL = "<img src=x onerror=alert(1)>[x](https://ev.il)"
SERVE_READY = {"state": "ready", "detail": "Tailscale publie JARVIS sur votre réseau Tailscale seulement.", "url": URL}
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
            "serve_command": "tailscale serve --bg --https=443 http://127.0.0.1:8789", "url": URL, **over}


def norm(text):
    return re.sub(r"[\u202f\u00a0]", " ", text or "")


class Calls:
    """Every /api/remote/* call as it leaves the page; the real routes answer."""

    def __init__(self, page):
        self.calls = []
        page.on("request", self._seen)

    def _seen(self, req):
        path = urlsplit(req.url).path
        if path.startswith("/api/remote/"):
            self.calls.append((req.method, path, json.loads(req.post_data) if req.post_data else None))

    def calls_to(self, method, path):
        return [body for m, p, body in self.calls if m == method and p == path]


class FakeRemote(Calls):
    """The remote core's routes faked as the page sees them (only for P0 30b)."""

    def __init__(self, page, state):
        super().__init__(page)
        self.state = state
        self.audit = []
        page.route("**/api/remote/**", self.handle)

    def handle(self, route):
        req = route.request
        path = urlsplit(req.url).path
        if path == "/api/remote/state":
            route.fulfill(json=copy.deepcopy(self.state))
        elif path == "/api/remote/serve":
            route.fulfill(json={**SERVE_READY, "command": self.state["serve_command"], "command_full": ""})
        elif path == "/api/remote/audit":
            route.fulfill(json={"lines": self.audit})
        else:
            route.fulfill(json={})


@pytest.fixture
def real(monkeypatch):
    """The PC's side, in the server's own process (see the module docstring)."""
    from jarvis import config, remote, tailscale
    assert remote.READY is True  # since C2: the switch itself still starts off
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    monkeypatch.setattr(config, "REMOTE_HOST", "")  # detected from Tailscale, confirmed by switching on
    fake = SimpleNamespace(serve=dict(SERVE_READY), published=[], unpublished=[], ts={
        "installed": True, "running": True, "dns_name": HOST, "login": LOGIN, "tailnet": "tail0000.ts.net",
        "magicdns": True, "https": True, "ip": "100.64.0.1", "ips": ["100.64.0.1"]})
    monkeypatch.setattr(tailscale, "self_info", lambda: dict(fake.ts))
    monkeypatch.setattr(tailscale, "whois", lambda ip: {
        "node_id": "nIPHONE000CNTRL", "addresses": [ip], "host_name": "iphone-de-test", "os": "iOS", "login": LOGIN})
    monkeypatch.setattr(tailscale, "serve_status", lambda: dict(fake.serve))
    monkeypatch.setattr(tailscale, "publish", lambda: fake.published.pop(0) if fake.published else
                        {"ok": False, "state": "consent", "consent_url": CONSENT, "error": ""})
    monkeypatch.setattr(tailscale, "unpublish", lambda: fake.unpublished.append(1) or
                        {"ok": True, "state": "absent", "error": ""})

    def switch_on():
        remote.set_enabled(True, host=HOST, login=LOGIN)
    fake.switch_on = switch_on
    return fake


def ask_pairing(name, ip=IP):
    """A phone's request, as the remote core takes it from the Serve listener."""
    from jarvis import remote
    asked, _ = remote.request_pairing(remote.Caller(kind="unpaired", ip=ip, login=LOGIN), name=name)
    return asked


def open_distance(page):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    if not page.is_visible("#settingsDialog .set-tab[aria-current='true']:has-text('Accès à distance')"):
        page.click("#settingsDialog .set-tab:has-text('Accès à distance')")
    page.wait_for_selector("#set-sec-distance .rm-block")


def section(page):
    return norm(page.text_content("#set-sec-distance"))


@pytest.fixture
def pc(jarvis, real):
    jarvis.context.grant_permissions(["clipboard-read", "clipboard-write"])
    calls = Calls(jarvis)
    calls.real = real
    dialogs = []
    jarvis.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    calls.dialogs = dialogs
    return calls

# ---------------------------------------------------------------- the PC view


def test_pc_view_status_and_the_switch(pc, jarvis):
    from jarvis import remote
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
    assert remote.is_enabled() and remote.host() == HOST  # the detected name is confirmed and saved
    jarvis.click(switch)
    jarvis.wait_for_selector("[data-key='remote-enabled'] .set-status:has-text('Accès à distance coupé.')")
    assert pc.calls_to("POST", "/api/remote/state")[-1] == {"enabled": False}
    # Off withdrew JARVIS's Serve configuration (it was exactly JARVIS's target).
    assert not remote.is_enabled() and pc.real.unpublished == [1]


def test_a_refused_switch_says_why_and_stays_off(pc, jarvis, monkeypatch):
    from jarvis import config, remote
    cap = ("Fixez d'abord un plafond de dépense par jour (Réglages › Coûts) : il protège votre crédit OpenAI quand "
           "JARVIS est utilisé à distance.")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    open_distance(jarvis)
    assert "Aucun plafond de dépense par jour" in section(jarvis)
    jarvis.click("#set-remote-enabled")
    jarvis.wait_for_selector("[data-key='remote-enabled'] .set-status.err")
    assert norm(jarvis.text_content("[data-key='remote-enabled'] .set-status")) == cap
    assert not jarvis.is_checked("#set-remote-enabled") and not remote.is_enabled()
    jarvis.click("[data-key='remote-status'] button:has-text('Fixer un plafond')")
    jarvis.wait_for_selector("#settingsDialog .set-tab[aria-current='true']:has-text('Coûts')")


def test_a_personal_login_gets_no_warning(jarvis, real, monkeypatch):
    from jarvis import config, store
    monkeypatch.setattr(config, "REMOTE_LOGINS", "")
    real.ts["login"] = "monsieur@github"
    store.save("remote.json", {"enabled": False, "host": "", "logins": ["monsieur@github"], "paused_until": 0,
                               "complet_until": 0, "published": False, "changed_at": 0, "changed_by": "pc"})
    calls = Calls(jarvis)
    open_distance(jarvis)
    text = section(jarvis)
    assert "monsieur@github" in text and "(enregistré)" in text and "Compte professionnel" not in text
    assert calls.calls_to("GET", "/api/remote/state")


def test_a_hidden_apple_address_is_a_personal_account(jarvis, real, monkeypatch):
    """Sign in with Apple's « Masquer mon adresse e-mail » (the guide's Apple
    choice): privaterelay.appleid.com is personal, never « Compte professionnel ? »."""
    from jarvis import config, store
    hidden = "a1b2c3d4e5@privaterelay.appleid.com"
    monkeypatch.setattr(config, "REMOTE_LOGINS", "")
    real.ts["login"] = hidden
    store.save("remote.json", {"enabled": False, "host": "", "logins": [hidden], "paused_until": 0,
                               "complet_until": 0, "published": False, "changed_at": 0, "changed_by": "pc"})
    open_distance(jarvis)
    text = section(jarvis)
    assert hidden in text and "Compte professionnel" not in text


def test_publish_shows_only_a_tailscale_consent_link_and_the_manual_command(pc, jarvis):
    from jarvis import remote, tailscale
    pc.real.switch_on()  # publishing needs remote access on (409 otherwise)
    open_distance(jarvis)
    jarvis.wait_for_selector(".rm-serve-ready")
    assert norm(jarvis.text_content(".rm-serve")) == "Serve : prêt"
    codes = jarvis.eval_on_selector_all("[data-key='remote-serve'] .rm-command code", "els => els.map(e => e.textContent)")
    assert codes == [tailscale.manual_command(), tailscale.manual_command(True)]
    jarvis.click("#rm-copy-command-full")
    assert jarvis.evaluate("navigator.clipboard.readText()") == tailscale.manual_command(True)
    jarvis.click("#rm-copy-command")
    assert jarvis.evaluate("navigator.clipboard.readText()") == tailscale.manual_command()
    assert "Copié." in section(jarvis)

    jarvis.click("#rm-publish")
    link = jarvis.wait_for_selector("a.rm-link")
    assert link.get_attribute("href") == CONSENT and link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener noreferrer"
    assert pc.calls_to("POST", "/api/remote/serve/publish") == [None]
    assert remote.state_for(remote.PC)["published"] is True  # switching on again re-publishes
    # Any other link is never shown, nor made clickable.
    pc.real.published.append({"ok": False, "state": "consent",
                              "consent_url": "https://evil.example/login.tailscale.com/f", "error": ""})
    jarvis.click("#rm-publish")
    jarvis.wait_for_selector("text=Lien d'autorisation inattendu")
    assert jarvis.locator("#set-sec-distance a").count() == 0
    assert "evil.example" not in section(jarvis)
    # An error is said as text; Revérifier asks Serve again.
    pc.real.published.append({"ok": False, "state": "error", "consent_url": "",
                              "error": "Access denied: serve config denied"})
    jarvis.click("#rm-publish")
    jarvis.wait_for_selector(".rm-publish-error:has-text('Access denied')")
    before = len(pc.calls_to("GET", "/api/remote/serve"))
    fix = "tailscale funnel --https=443 off"
    fix_full = fix.replace("tailscale ", f'& "{tailscale.POWERSHELL_EXE}" ', 1)
    pc.real.serve.update(state="funnel", detail="Funnel actif : Tailscale publie ce PC sur internet.",
                         fix=fix, fix_full=fix_full)
    jarvis.click("#rm-serve-check")
    jarvis.wait_for_selector(".rm-serve-funnel")
    assert norm(jarvis.text_content(".rm-serve")) == "Serve : Funnel actif"
    assert len(pc.calls_to("GET", "/api/remote/serve")) > before
    # The command that stops it, on its own line (never wrapped inside a sentence), with Copier.
    assert "Pour l'arrêter, à taper d'abord dans PowerShell :" in section(jarvis)
    codes = jarvis.eval_on_selector_all("[data-key='remote-serve'] .rm-command code", "els => els.map(e => e.textContent)")
    assert codes[:2] == [fix, fix_full]
    jarvis.click("#rm-copy-fix")
    assert jarvis.evaluate("navigator.clipboard.readText()") == fix
    jarvis.click("#rm-copy-fix-full")
    assert jarvis.evaluate("navigator.clipboard.readText()") == fix_full


def test_pairing_shows_the_url_a_text_fallback_for_the_qr_and_asks_the_code_when_two_wait(pc, jarvis):
    from jarvis import devices, remote
    qr_asked = []

    def no_cdn(route):  # a faked body would fail the integrity check: refuse it instead
        qr_asked.append(route.request.url)
        route.fulfill(status=404, body="")
    jarvis.route(QR_URL, no_cdn)
    pc.real.switch_on()
    open_distance(jarvis)
    jarvis.click("#rm-pair-open")
    assert pc.calls_to("POST", "/api/remote/pairing") == [{"open": True}]
    jarvis.wait_for_selector(".rm-qr-fallback")
    assert qr_asked == [QR_URL]
    assert "tapez l'adresse ci-dessus dans Safari" in norm(jarvis.text_content(".rm-qr-fallback"))
    assert jarvis.text_content("code.rm-url") == URL
    assert re.search(r"Association ouverte encore (10:00|9:\d\d)\.", norm(jarvis.text_content("#rm-countdown")))
    # Two phones ask (two addresses): the PC page hears it from its event stream.
    first = ask_pairing("iPhone", ip=IP2)
    second = ask_pairing("iPhone pro")
    row = f".rm-request[data-request='{second['request_id']}']"
    jarvis.wait_for_selector(row)
    jarvis.wait_for_selector(f".rm-request[data-request='{first['request_id']}']")
    assert "Plusieurs demandes en attente : n'autorisez que celle dont le code s'affiche sur votre iPhone." \
        in section(jarvis)
    cells = jarvis.eval_on_selector_all(f"{row} .rm-cell", "els => els.map(e => e.textContent)")
    assert cells == ["iOS", "iphone-de-test", IP, LOGIN]
    assert jarvis.text_content(f"{row} .rm-code") == second["code"]
    assert norm(jarvis.text_content(f"{row} .rm-name")) == "« iPhone pro »"
    # Two requests wait: Autoriser first asks for the 4 digits.
    allow = f"/api/remote/pair-requests/{second['request_id']}/allow"
    jarvis.click(f"{row} button:has-text('Autoriser')")
    jarvis.wait_for_selector(f"{row} .rm-code-input")
    assert pc.calls_to("POST", allow) == []
    # One [Autoriser] at a time: the row's first buttons give way to the code's.
    assert jarvis.locator(f"{row} button:visible", has_text="Autoriser").count() == 1
    assert not jarvis.is_visible(f"{row} > .set-actions")
    jarvis.fill(f"{row} .rm-code-input", first["code"])  # the other phone's code
    jarvis.click(f"{row} .rm-code-form button[type='submit']")
    jarvis.wait_for_selector(f"{row} .set-status.err")
    assert "Code différent" in norm(jarvis.text_content(f"{row} .set-status"))
    jarvis.wait_for_timeout(3500)  # a poll meanwhile must not wipe what is being typed
    assert jarvis.is_visible(f"{row} .rm-code-input")
    jarvis.fill(f"{row} .rm-code-input", second["code"])
    jarvis.click(f"{row} .rm-code-form button[type='submit']")
    jarvis.wait_for_selector(f"[data-key='remote-pairing'] > .set-status:has-text('Demande {second['code']} autorisée')")
    assert pc.calls_to("POST", allow) == [{"code": first["code"]}, {"code": second["code"]}]
    jarvis.wait_for_selector(f"{row}:has-text('Autorisée')")
    assert [r["status"] for r in remote.pairing_requests() if r["id"] == second["request_id"]] == ["approved"]
    # The other one is refused; no device exists until a phone collects its secret.
    jarvis.click(f".rm-request[data-request='{first['request_id']}'] button:has-text('Refuser')")
    jarvis.wait_for_timeout(300)
    assert pc.calls_to("POST", f"/api/remote/pair-requests/{first['request_id']}/deny") == [None]
    assert devices.active() == []
    jarvis.click("#rm-pair-close")
    jarvis.wait_for_selector("#rm-pair-open")
    assert pc.calls_to("POST", "/api/remote/pairing")[-1] == {"open": False}
    assert remote.pairing_until() == 0


def test_the_qr_code_is_drawn_module_by_module_as_svg(pc, jarvis):
    # A stand-in for qrcode-generator's API (getModuleCount, isDark): no CDN in the tests.
    jarvis.evaluate("""() => { window.qrcode = (type, level) => ({
        added: '', addData(t) { this.added = t; }, make() {},
        getModuleCount() { return 21; }, isDark(r, c) { return (r * 3 + c) % 5 === 0; } }); }""")
    pc.real.switch_on()
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
    from jarvis import devices, tasks
    dev, _ = devices.add("iPhone de test", ip=IP, login=LOGIN, os="iOS", host_name="iphone-de-test",
                         node_id="nIPHONE000CNTRL", ips=(IP, "fd7a:115c:a1e0::1234"))
    open_distance(jarvis)
    item = f".rm-device[data-device='{dev['id']}']"
    jarvis.wait_for_selector(item)
    assert jarvis.text_content(f"{item} .rm-device-name") == "iPhone de test"
    assert LOGIN in jarvis.text_content(item) and IP in jarvis.text_content(item)
    assert jarvis.locator(f"{item} [data-slot='siri']").count() == 1  # where the Siri key buttons go
    jarvis.click(f"{item} button:has-text('Renommer')")
    jarvis.fill(f"{item} .rm-rename input", "iPhone pro")
    jarvis.click(f"{item} .rm-rename button[type='submit']")
    jarvis.wait_for_selector(f"{item} .rm-device-name:has-text('iPhone pro')")
    assert pc.calls_to("PATCH", f"/api/remote/devices/{dev['id']}") == [{"name": "iPhone pro"}]
    assert devices.get(dev["id"])["name"] == "iPhone pro"

    jarvis.click(f"{item} button:has-text('Retirer')")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    confirm = norm(jarvis.text_content("#settingsConfirm"))
    assert "Retirer « iPhone pro » ?" in confirm and "clés Siri" in confirm and "tâches en cours" in confirm
    jarvis.click("#settingsConfirm button[value='cancel']")
    assert pc.calls_to("DELETE", f"/api/remote/devices/{dev['id']}") == []
    jarvis.click(f"{item} button:has-text('Retirer')")
    # Two tasks the phone launched are still running: removing it stops them.
    started = [tasks.create_task(f"Veille {n}", "Cherche la météo", profile="lecture", via=f"app:{dev['id']}")
               for n in (1, 2)]
    jarvis.click("#settingsConfirm button[value='ok']")
    jarvis.wait_for_selector("text=Aucun appareil associé.")
    assert pc.calls_to("DELETE", f"/api/remote/devices/{dev['id']}") == [None]
    assert "Tâches arrêtées : 2." in section(jarvis)
    assert devices.is_revoked(dev["id"]) and not [t for t in tasks.running() if t["id"] in
                                                   {s["id"] for s in started}]


def test_full_access_from_the_iphone_is_jamais_24_h_or_7_jours(pc, jarvis):
    from jarvis import remote
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
    assert remote.complet_allowed() and 86000 < remote.complet_until() - time.time() <= 86400
    jarvis.click("label[for='rm-complet-never']")
    jarvis.wait_for_selector(".rm-complet-now:has-text('Jamais')")
    assert pc.calls_to("POST", "/api/remote/complet") == [{"duration": "24h"}, {"duration": "never"}]
    assert not remote.complet_allowed()


def test_recent_activity_in_plain_french(pc, jarvis):
    from jarvis import audit, devices, remote
    dev, _ = devices.add("iPhone de test", ip=IP, login=LOGIN, os="iOS", ips=(IP,))
    phone = remote.Caller(kind="app", device_id=dev["id"], ip=IP, login=LOGIN, name="iPhone de test")
    stranger = remote.Caller(kind="unpaired", ip=IP2, login="autre@example.com")
    # Oldest first (the trail reads newest first).
    audit.alert("new_device", "Nouvel appareil associé : « iPhone de test ».", phone)
    audit.event(stranger, "request", method="POST", route="/api/remote/pair-request", status=403, reason="login")
    audit.event(phone, "request", method="GET", route="/api/tasks", status=200)
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


def test_recent_activity_credits_the_pcs_own_actions_to_the_pc(pc, jarvis):
    """Switching on, allowing full access and making a Siri key are the PC's own
    clicks: « PC », never « appareil non associé » nor the iPhone the key is for;
    the duration reads as the buttons say it."""
    from jarvis import devices, raccourci, remote
    dev, _ = devices.add("iPhone de test", ip=IP, login=LOGIN, os="iOS", ips=(IP,))
    pc.real.switch_on()
    remote.set_complet("24h")
    raccourci.create_key(dev["id"], remote.PC)
    open_distance(jarvis)
    jarvis.click("#rm-audit-toggle")
    jarvis.wait_for_selector(".rm-audit-line")
    rows = jarvis.eval_on_selector_all(".rm-audit-line", """els => els.map(e => [
        e.querySelector('.rm-audit-who').textContent, e.querySelector('.rm-audit-what').textContent])""")
    rows = [(norm(who), norm(what)) for who, what in rows]
    assert rows and {who for who, _ in rows} == {"PC"}, rows
    whats = " | ".join(what for _, what in rows)
    for words in ("Accès à distance activé.", "Accès complet depuis l'iPhone : 24 h.", "Clé Siri créée"):
        assert words in whats, (words, whats)
    assert "24h" not in whats


def test_remote_strings_render_as_text_holds(jarvis):
    """P0 30b: a request, a device and audit lines carrying markup and markdown:
    no element, no link, no dialog; the text shows as it is. The route is faked
    (spec section 8): the remote core cleans these strings before storing them."""
    device = {k: v for k, v in DEVICE.items() if k != "siri_keys"}
    fake = FakeRemote(jarvis, pc_state(requests=[request("r_0000000000ee", "4821", EVIL, os=EVIL, host_name=EVIL,
                                                         login=EVIL)],
                                       devices=[{**device, "name": EVIL, "login": EVIL}],
                                       pairing_until=time.time() + 600))
    fake.audit = [{"t": time.time(), "kind": "alert", "alert": "login_change", "text": EVIL, "ip": EVIL},
                  {"t": time.time(), "kind": "request", "caller": EVIL, "method": EVIL, "route": EVIL, "reason": EVIL}]
    dialogs = []
    jarvis.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
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
    assert dialogs == []
    assert EVIL in jarvis.text_content(".rm-request .rm-name")
    assert jarvis.eval_on_selector_all(".rm-request .rm-cell", "els => els.map(e => e.textContent)") == [EVIL, EVIL, IP, EVIL]
    assert jarvis.text_content(".rm-device-name") == EVIL
    assert EVIL in jarvis.text_content("#settingsConfirm h3")
    assert EVIL in jarvis.text_content("#rm-audit-list")
    jarvis.click("#settingsConfirm button[value='cancel']")


def test_the_section_is_honest_while_this_version_is_not_ready(jarvis, monkeypatch):
    """This version is ready (remote.READY, C2): on a fresh install the switch is
    there, off, and says nothing of « pas encore ». A version held back (the test
    hook sets READY back to False) greys it out and says so in French."""
    from jarvis import remote
    assert remote.READY is True and not remote.is_enabled()
    open_distance(jarvis)
    jarvis.wait_for_selector("#set-remote-enabled")
    assert jarvis.is_enabled("#set-remote-enabled") and not jarvis.is_checked("#set-remote-enabled")
    assert "Pas encore disponible" not in section(jarvis)
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_selector("#settingsDialog[open]", state="detached")
    monkeypatch.setattr(remote, "READY", False)
    open_distance(jarvis)
    jarvis.wait_for_selector("#set-remote-enabled")
    assert "Pas encore disponible dans cette version de JARVIS." in section(jarvis)
    assert not jarvis.is_enabled("#set-remote-enabled")

# ---------------------------------------------------------------- the iPhone


@pytest.fixture
def phone(remote_page):
    return Calls(remote_page)


def test_phone_view_opens_on_its_own_section(phone, remote_page):
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#settingsDialog .set-tab[aria-current='true']:has-text('Accès à distance')")
    remote_page.wait_for_selector("[data-key='remote-phone']")
    text = section(remote_page)
    assert "iPhone de test" in text and "Accès à distance actif" in text
    assert phone.calls_to("GET", "/api/remote/state")
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
    from jarvis import remote
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
    assert phone.calls_to("POST", "/api/remote/pause") == [] and remote.paused_until() == 0
    remote_page.click("#rm-pause")
    remote_page.click("#remotePause button[value='1']")
    remote_page.wait_for_selector("[data-key='remote-phone'] .set-status:has-text('en pause jusqu')")
    assert phone.calls_to("POST", "/api/remote/pause") == [{"hours": 1}]
    assert 3500 < remote.paused_until() - time.time() <= 3600


def test_a_24_h_pause_says_which_day_it_ends(phone, remote_page):
    """« jusqu'à 14 h 30 » for a pause of 24 h would read as now: the day is said."""
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#rm-pause")
    remote_page.click("#rm-pause")
    remote_page.click("#remotePause button[value='24']")
    remote_page.wait_for_selector("[data-key='remote-phone'] .set-status:has-text('en pause jusqu')")
    text = section(remote_page)
    assert "Accès à distance en pause jusqu'à demain à " in text and "Seul le PC peut le rallumer avant." in text
    assert "En pause jusqu'à demain à " in text


def test_the_pc_says_which_day_a_pause_ends(pc, jarvis):
    from jarvis import remote
    pc.real.switch_on()
    remote.pause(24, by=remote.PC)
    open_distance(jarvis)
    assert "En pause jusqu'à demain à " in section(jarvis)
    remote.pause(0, by=remote.PC)


def test_phone_forget_asks_first(phone, remote_page):
    from jarvis import devices
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#rm-forget")
    remote_page.click("#rm-forget")
    remote_page.wait_for_selector("#settingsConfirm[open]")
    text = norm(remote_page.text_content("#settingsConfirm"))
    assert "Oublier cet iPhone ?" in text and "clés Siri" in text
    remote_page.click("#settingsConfirm button[value='cancel']")
    assert phone.calls_to("POST", "/api/remote/forget") == []
    [device] = devices.active()
    remote_page.click("#rm-forget")
    with remote_page.expect_navigation(timeout=5000):
        remote_page.click("#settingsConfirm button[value='ok']")
    assert phone.calls_to("POST", "/api/remote/forget") == [None]
    # Forgotten for real: the device is revoked and its cookie gone, so the page is the pairing page.
    assert devices.is_revoked(device["id"]) and devices.active() == []
    remote_page.wait_for_function("window.__pair && window.__pair.ready")
    assert remote_page.evaluate("window.__pair.state") == "closed"


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

    from jarvis import audit, devices, remote
    pc.real.switch_on()
    devices.add("iPhone de test", ip=IP, login=LOGIN, os="iOS", ips=(IP,))
    remote.open_pairing()
    ask_pairing("iPhone", ip=IP2)
    ask_pairing("iPhone pro")
    audit.event(remote.Caller(kind="unpaired", ip=IP2, login=LOGIN), "request", method="GET", route="/api/tasks",
                status=401, reason="unpaired")
    jarvis.route(QR_URL, lambda route: route.fulfill(status=404, body=""))
    open_distance(jarvis)
    jarvis.click("#rm-publish")
    jarvis.wait_for_selector("a.rm-link")
    jarvis.wait_for_selector(".rm-request")
    jarvis.click("#rm-audit-toggle")
    jarvis.wait_for_selector(".rm-audit-line")
    assert axe_violations(jarvis) == []


def test_axe_has_no_serious_violation_on_the_phone(phone, remote_page):
    from test_settings_ui import axe_violations
    remote_page.click("#topActions button:has-text('Réglages')")
    remote_page.wait_for_selector("#rm-pause")
    assert axe_violations(remote_page) == []
