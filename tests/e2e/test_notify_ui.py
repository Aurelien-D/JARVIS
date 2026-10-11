"""Réglages › Notifications (B1, spec 6) in Chromium.

PC view (jarvis): the four settings, the topic in monospace with « Copier le
sujet », the server, « Envoyer un test » and « Nouveau sujet » after a
confirmation. Phone view (remote_page, the real gate): the topic, its copy,
the test and the four steps to subscribe in the ntfy app, none of the
settings. The server runs in this process: ntfy is an httpx.MockTransport that
records what would be sent, so nothing reaches the network."""
import re

import httpx
import pytest

pytestmark = pytest.mark.e2e

TOPIC_RE = re.compile(r"jarvis-[A-Za-z0-9_-]{32}")
AXE_FILE = __import__("pathlib").Path(__file__).resolve().parent / "vendor" / "axe.min.js"


def norm(text):
    return re.sub(r"[  ]", " ", text or "")


class FakeNtfy:
    """A ntfy server in the JARVIS process: records each POST."""

    def __init__(self):
        self.requests = []
        self.status = 200

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, json={"id": "abc", "event": "message"})

    @property
    def bodies(self):
        return [r.content.decode("utf-8") for r in self.requests]


@pytest.fixture
def ntfy(monkeypatch):
    from jarvis import config, notify
    fake = FakeNtfy()
    monkeypatch.setattr(notify, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(config, "NTFY", True)
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.sh")
    yield fake
    notify._drain()


def stored_topic():
    from jarvis import notify, store
    return store.load(notify.NTFY_FILE, {}).get("topic", "")


def open_notifications(page):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    page.click("#settingsDialog .set-tab:has-text('Notifications')")
    page.wait_for_selector("#settingsDialog .set-tab[aria-current='true']:has-text('Notifications')")
    page.wait_for_selector("#set-sec-notifications #nt-topic")


def status(page, sel, contains):
    page.wait_for_function("([s, t]) => { const e = document.querySelector(s);"
                           " return e && e.textContent.replace(/[\\u202f\\u00a0]/g, ' ').includes(t); }",
                           arg=[sel, contains])
    return norm(page.text_content(sel))


def until(predicate, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()

# ---------------------------------------------------------------- the PC


def test_pc_view_has_the_settings_the_topic_the_server_and_the_buttons(jarvis, ntfy):
    open_notifications(jarvis)
    sec = "#set-sec-notifications"
    assert norm(jarvis.text_content(f"{sec} .set-section-title")) == "Notifications"
    for key in ("ntfy", "ntfy_server", "ntfy_only_away", "ntfy_reminder_text"):
        assert jarvis.locator(f"{sec} [data-key='{key}']").count() == 1, key
    assert jarvis.is_checked("#set-ntfy") and not jarvis.is_checked("#set-ntfy_only_away")
    assert jarvis.input_value("#set-ntfy_server") == "https://ntfy.sh"
    labels = norm(jarvis.text_content(sec))
    for label in ("Notifications sur l'iPhone (ntfy)", "Serveur ntfy", "Seulement si je ne suis pas au PC",
                  "Texte des rappels dans la notification", "Jamais le contenu d'une tâche"):
        assert label in labels, label
    topic = jarvis.text_content("#nt-topic")
    assert TOPIC_RE.fullmatch(topic) and topic == stored_topic()
    assert "mono" in jarvis.eval_on_selector("#nt-topic", "e => getComputedStyle(e).fontFamily").lower()
    assert norm(jarvis.text_content("#nt-server")) == "Serveur ntfy : https://ntfy.sh"
    assert [norm(jarvis.text_content(b)) for b in ("#nt-copy", "#nt-test", "#nt-new")] == \
        ["Copier le sujet", "Envoyer un test", "Nouveau sujet"]
    assert norm(jarvis.text_content("#nt-state")) == "Notifications activées."
    assert jarvis.locator(f"{sec} .nt-guide").count() == 0  # the steps are the phone's


def test_pc_copies_the_topic_and_sends_a_test(jarvis, ntfy):
    from jarvis import notify
    jarvis.context.grant_permissions(["clipboard-read", "clipboard-write"])
    open_notifications(jarvis)
    topic = jarvis.text_content("#nt-topic")
    jarvis.click("#nt-copy")
    assert "Sujet copié" in status(jarvis, "#nt-copy-status", "Sujet copié")
    assert jarvis.evaluate("navigator.clipboard.readText()") == topic
    jarvis.click("#nt-test")
    assert "Test envoyé : regardez l'iPhone." in status(jarvis, "#nt-test-status", "Test envoyé")
    assert ntfy.bodies == ["JARVIS : notification de test."]
    r = ntfy.requests[0]
    assert str(r.url) == f"https://ntfy.sh/{topic}" and r.headers["Title"] == "JARVIS"
    assert "Dernier envoi réussi" in status(jarvis, "#nt-last", "Dernier envoi réussi")
    # The server refuses: its French reason, in red, and the last failure under the button.
    notify.reset_memory()
    ntfy.status = 500
    jarvis.click("#nt-test")
    text = status(jarvis, "#nt-test-status", "code 500")
    assert text == "Le serveur ntfy a refusé l'envoi (code 500)."
    assert "err" in jarvis.get_attribute("#nt-test-status", "class")
    assert "Dernier échec" in status(jarvis, "#nt-last", "Dernier échec")
    assert topic not in norm(jarvis.text_content("#nt-test-status"))


def test_a_new_topic_asks_first_and_replaces_the_old_one(jarvis, ntfy):
    open_notifications(jarvis)
    old = jarvis.text_content("#nt-topic")
    jarvis.click("#nt-new")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    text = norm(jarvis.text_content("#settingsConfirm"))
    assert "Nouveau sujet ntfy ?" in text and "L'ancien sujet ne recevra plus aucune notification." in text
    assert jarvis.evaluate("document.activeElement.textContent") == "Annuler"  # Entrée renews nothing
    jarvis.click("#settingsConfirm button[value='cancel']")
    jarvis.wait_for_selector("#settingsConfirm:not([open])", state="attached")
    assert stored_topic() == old and jarvis.text_content("#nt-topic") == old
    jarvis.click("#nt-new")
    jarvis.click("#settingsConfirm button[value='ok']")
    status(jarvis, "#nt-copy-status", "Nouveau sujet créé")
    new = jarvis.text_content("#nt-topic")
    assert TOPIC_RE.fullmatch(new) and new != old and stored_topic() == new


def test_the_server_field_refuses_plain_http_and_the_section_follows_its_settings(jarvis, ntfy):
    from jarvis import config, settings
    open_notifications(jarvis)
    jarvis.fill("#set-ntfy_server", "http://ntfy.example")
    jarvis.press("#set-ntfy_server", "Tab")
    text = status(jarvis, "#settingsDialog [data-key='ntfy_server'] > .set-status", "Adresse de serveur refusée")
    assert text == norm(settings.URL_REFUSED)
    assert config.NTFY_SERVER == "https://ntfy.sh"
    jarvis.fill("#set-ntfy_server", "https://ntfy.example.org")
    jarvis.press("#set-ntfy_server", "Tab")
    status(jarvis, "#nt-server", "https://ntfy.example.org")
    assert config.NTFY_SERVER == "https://ntfy.example.org"
    # Switched off on the PC: the state line says so, and a test explains why nothing goes out.
    jarvis.uncheck("#set-ntfy")
    status(jarvis, "#nt-state", "Notifications coupées")
    assert until(lambda: config.NTFY is False)
    jarvis.click("#nt-test")
    assert "activez-les d'abord" in status(jarvis, "#nt-test-status", "activez-les")
    assert ntfy.requests == []


def test_the_section_fits_a_narrow_window_and_passes_axe(jarvis, ntfy):
    jarvis.set_viewport_size({"width": 360, "height": 640})
    open_notifications(jarvis)
    for sel in ("#settingsDialog", "#settingsDialog .set-panels"):
        assert jarvis.eval_on_selector(sel, "e => e.scrollWidth <= e.clientWidth + 1"), sel
    right = jarvis.eval_on_selector("#nt-topic", "e => e.getBoundingClientRect().right")
    assert right <= 360.5
    jarvis.add_script_tag(path=str(AXE_FILE))
    found = jarvis.evaluate("""async () => {
      const r = await axe.run('#settingsDialog', {resultTypes: ['violations']});
      return r.violations.filter(v => v.impact === 'serious' || v.impact === 'critical').map(v => v.id);
    }""")
    assert found == []

# ---------------------------------------------------------------- the iPhone


def test_phone_view_shows_the_topic_the_test_and_the_four_steps(remote_page, ntfy):
    page = remote_page
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    open_notifications(page)
    tabs = [norm(t) for t in page.eval_on_selector_all("#settingsDialog .set-tab", "els => els.map(e => e.textContent)")]
    assert "Notifications" in tabs and tabs.index("Notifications") == tabs.index("Accès à distance") + 1
    sec = "#set-sec-notifications"
    topic = page.text_content("#nt-topic")
    assert TOPIC_RE.fullmatch(topic) and topic == stored_topic()
    # The settings are the PC's: none of them here, nor « Nouveau sujet ».
    assert page.locator(f"{sec} [data-key^='ntfy_'], {sec} [data-key='ntfy'], #nt-new").count() == 0
    steps = [norm(s) for s in page.eval_on_selector_all(f"{sec} .nt-guide li", "els => els.map(e => e.textContent)")]
    assert steps == ["Installez l'app ntfy depuis l'App Store et autorisez ses notifications.",
                     "Dans ntfy, touchez +.", "Collez le sujet copié ci-dessus.",
                     "Touchez « S'abonner » (« Subscribe » si l'app est en anglais)."]
    page.click("#nt-copy")
    status(page, "#nt-copy-status", "Sujet copié")
    assert page.evaluate("navigator.clipboard.readText()") == topic
    page.click("#nt-test")
    status(page, "#nt-test-status", "Test envoyé")
    assert ntfy.bodies == ["JARVIS : notification de test."]
    assert page.eval_on_selector("#settingsDialog .set-panels", "e => e.scrollWidth <= e.clientWidth + 1")


def test_phone_selects_the_topic_when_the_clipboard_is_refused(remote_page, ntfy):
    page = remote_page
    page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: {writeText: () => "
                  "Promise.reject(new Error('NotAllowedError'))}, configurable: true})")
    open_notifications(page)
    page.click("#nt-copy")
    text = status(page, "#nt-copy-status", "Copie impossible")
    assert "touchez le sujet" in text and "err" in page.get_attribute("#nt-copy-status", "class")
    assert page.evaluate("window.getSelection().toString()") == page.text_content("#nt-topic")


def test_phone_says_when_notifications_are_off_on_the_pc(remote_page, ntfy, monkeypatch):
    from jarvis import config
    monkeypatch.setattr(config, "NTFY", False)
    monkeypatch.setattr(config, "NTFY_SERVER", "https://ntfy.example.org")
    open_notifications(remote_page)
    assert "Elles s'activent sur le PC" in norm(remote_page.text_content("#nt-state"))
    steps = [norm(s) for s in remote_page.eval_on_selector_all(".nt-guide li", "els => els.map(e => e.textContent)")]
    assert steps[2] == "Collez le sujet copié ci-dessus, avec le serveur https://ntfy.example.org."
    remote_page.click("#nt-test")
    assert "activez-les d'abord" in status(remote_page, "#nt-test-status", "activez-les")
    assert ntfy.requests == []
