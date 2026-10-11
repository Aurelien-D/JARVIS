"""Siri in Chromium (spec 5, UI): on the PC, Réglages › Accès à distance lists
each paired iPhone's Siri keys with [Créer une clé Siri] and [Révoquer]; on
the paired iPhone (remote_page, real gate), the « Assistant raccourci » screen
collects the key once and shows the URL, the header name and value and the
body field, each with its own [Copier], plus the 3-action recipe; the key is
gone once the screen closes. Réglages › Coûts shows Siri's own spending.
The routes are the real ones; OpenAI is never reached.
Fictitious names only: the repository is public."""
import re

import httpx
import pytest

pytestmark = pytest.mark.e2e

HOST = "jarvis-pc.tail0000.ts.net"
NBSP = " "
# Every copy the page makes, recorded (and the clipboard left alone).
SPY = """() => {
  window.__copied = [];
  const spy = { writeText: async (text) => { window.__copied.push(String(text)); } };
  if (navigator.clipboard) Object.defineProperty(navigator.clipboard, "writeText", { configurable: true, value: spy.writeText });
  else Object.defineProperty(navigator, "clipboard", { configurable: true, value: spy });
}"""


def norm(text):
    return re.sub(r"[  ]", " ", text or "")


def the_device():
    from jarvis import devices
    [device] = devices.active()
    return device


def open_distance(page):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    if not page.is_visible("#settingsDialog .set-tab[aria-current='true']:has-text('Accès à distance')"):
        page.click("#settingsDialog .set-tab:has-text('Accès à distance')")
    page.wait_for_selector("#set-sec-distance .rm-block")


def open_assistant(page):
    open_distance(page)
    page.wait_for_selector("#siri-open")
    page.click("#siri-open")
    page.wait_for_selector("#siriAssistant[open]")


def test_the_pc_creates_a_key_and_the_phone_copies_it(jarvis, remote_page, app_server):
    from jarvis import devices
    device = the_device()
    item = f".rm-device[data-device='{device['id']}']"
    # The PC: [Créer une clé Siri] under the paired iPhone (real route).
    open_distance(jarvis)
    jarvis.wait_for_selector(f"{item} [data-slot='siri'] .siri-title")
    assert norm(jarvis.text_content(f"#rm-siri-create-{device['id']}")) == "Créer une clé Siri"
    jarvis.click(f"#rm-siri-create-{device['id']}")
    jarvis.wait_for_selector(f"{item} .siri-said:has-text('Clé créée')")
    [key] = [k for k in devices.get(device["id"])["siri_keys"] if not k["revoked"]]
    jarvis.wait_for_selector(f"{item} .siri-key[data-key='{key['id']}']")
    said = norm(jarvis.text_content(f"{item} .siri-said"))
    assert "Réglages › Accès à distance › Assistant raccourci" in said
    assert "jv_siri_" not in jarvis.content()  # the secret never reaches the PC page

    # The iPhone: the « Assistant raccourci » screen, four rows to copy.
    open_assistant(remote_page)
    remote_page.wait_for_selector("#siriAssistant .siri-row")
    rows = remote_page.eval_on_selector_all(
        "#siriAssistant .siri-row", "rows => rows.map(r => [r.dataset.row, r.querySelector('.siri-label').textContent, "
                                    "r.querySelector('.siri-value').textContent, r.querySelector('button').textContent])")
    url, header, value, field = (r[2] for r in rows)
    assert [r[0] for r in rows] == ["url", "header", "value", "field"]
    assert [norm(r[1]) for r in rows] == ["Adresse (URL)", "Nom de l'en-tête", "Valeur de l'en-tête",
                                          "Champ du corps JSON"]
    assert all(r[3] == "Copier" for r in rows)
    assert url == f"https://{HOST}/api/raccourci" and header == "Authorization" and field == "text"
    assert re.fullmatch(rf"Bearer jv_siri_{key['id']}\.[A-Za-z0-9_-]{{40,}}", value)
    assert norm(remote_page.text_content("#siriTitle")) == "Assistant raccourci"
    steps = remote_page.eval_on_selector_all("#siriAssistant .siri-steps li", "els => els.map(e => e.textContent)")
    assert len(steps) == 3 and norm(steps[0]).startswith("Dicter le texte")
    assert "Obtenir le contenu de l'URL" in norm(steps[1]) and "Authorization" in steps[1]
    assert norm(steps[2]).startswith("Énoncer le texte")
    assert "ne s'affiche qu'une fois" in norm(remote_page.text_content("#siriAssistant .siri-once"))
    # The screen fits the phone: nothing scrolls sideways.
    assert remote_page.evaluate("(() => { const d = document.getElementById('siriAssistant'); "
                                "return d.scrollWidth <= d.clientWidth && document.documentElement.scrollWidth "
                                "<= innerWidth; })()")

    # Each [Copier] copies its own value.
    remote_page.evaluate(SPY)
    for row in ("url", "header", "value", "field"):
        remote_page.click(f"#siri-copy-{row}")
        remote_page.wait_for_function(f"window.__copied.length === {['url', 'header', 'value', 'field'].index(row) + 1}")
    assert remote_page.evaluate("window.__copied") == [url, "Authorization", value, "text"]
    assert norm(remote_page.text_content("#siriAssistant .siri-status")) == "Champ du corps JSON : copié."

    # Closed: the key is gone from the page, and the server hands it out no more.
    remote_page.click("#siri-close")
    remote_page.wait_for_selector("#siriAssistant:not([open])", state="attached")
    remote_page.wait_for_function("document.getElementById('siri-body').childElementCount === 0")
    assert "jv_siri_" not in remote_page.content()
    remote_page.click("#siri-open")
    remote_page.wait_for_selector("#siriAssistant .siri-none")
    assert "Aucune clé Siri en attente" in norm(remote_page.text_content("#siriAssistant .siri-none"))

    # What was copied is what the Shortcut sends: it gets through the gate
    # (OpenAI is unreachable in the tests, and Siri hears why).
    with httpx.Client(trust_env=False) as client:
        r = client.post(f"{app_server.remote_url}api/raccourci", json={"text": "Bonjour"},
                        headers={"Authorization": value})
    assert r.status_code == 502 and r.text == "Pas de connexion à OpenAI depuis le PC : réessayez."


def test_the_pc_lists_and_revokes_siri_keys(jarvis, remote_page):
    from jarvis import devices
    device = the_device()
    first, _ = devices.add_siri_key(device["id"])
    second, _ = devices.add_siri_key(device["id"])
    item = f".rm-device[data-device='{device['id']}']"
    open_distance(jarvis)
    jarvis.wait_for_selector(f"{item} .siri-key[data-key='{second}']")
    keys = jarvis.eval_on_selector_all(f"{item} .siri-key", "els => els.map(e => e.dataset.key)")
    assert keys == [first, second]
    assert "jamais utilisée" in norm(jarvis.text_content(f"{item} .siri-key[data-key='{first}']"))
    assert not jarvis.is_enabled(f"#rm-siri-create-{device['id']}")  # two at most
    assert "2 clés au plus par iPhone" in norm(jarvis.text_content(item))
    # [Révoquer] asks first.
    jarvis.click(f"#rm-siri-revoke-{first}")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    assert "Révoquer cette clé Siri ?" in norm(jarvis.text_content("#settingsConfirm"))
    jarvis.click("#settingsConfirm button[value='cancel']")
    assert not devices.siri_key_revoked(first)
    jarvis.click(f"#rm-siri-revoke-{first}")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    jarvis.click("#settingsConfirm button[value='ok']")
    jarvis.wait_for_selector(f"{item} .siri-said:has-text('Clé Siri révoquée.')")
    jarvis.wait_for_selector(f"{item} .siri-key[data-key='{first}']", state="detached")
    assert devices.siri_key_revoked(first) and not devices.siri_key_revoked(second)
    assert jarvis.is_enabled(f"#rm-siri-create-{device['id']}")


def test_the_assistant_says_when_no_key_waits(remote_page):
    open_assistant(remote_page)
    remote_page.wait_for_selector("#siriAssistant .siri-none")
    text = norm(remote_page.text_content("#siriAssistant"))
    assert "Aucune clé Siri en attente : créez-en une sur le PC" in text
    assert remote_page.locator("#siriAssistant .siri-row").count() == 0
    remote_page.keyboard.press("Escape")
    remote_page.wait_for_selector("#siriAssistant:not([open])", state="attached")
    remote_page.wait_for_function("document.getElementById('siri-body').childElementCount === 0")


def test_siri_strings_render_as_text(jarvis, remote_page):
    """A device name can hold no markup (devices.clean_label), and the key list
    is drawn with textContent: nothing becomes an element."""
    from jarvis import devices
    device = the_device()
    devices.add_siri_key(device["id"])
    open_distance(jarvis)
    jarvis.wait_for_selector(".siri-key")
    assert jarvis.locator(".siri-key img, .siri-key a, [data-slot='siri'] script").count() == 0


def test_couts_shows_siri_spending_apart(jarvis):
    from jarvis import usage
    usage.add_text("gpt-6-luna", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})  # 0,60 $
    jarvis.reload()
    jarvis.wait_for_function("window.__jarvis && __jarvis.state.ready")
    jarvis.click("#topActions button:has-text('Réglages')")
    jarvis.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    jarvis.click("#settingsDialog .set-tab:has-text('Coûts')")
    jarvis.wait_for_selector("#settingsUsage .usage-today")
    today = jarvis.text_content("#settingsUsage .usage-today")
    assert today.endswith(f" · Siri ≈ 0,60{NBSP}$") and today.startswith(f"Aujourd'hui ≈ 0,60{NBSP}$")
    jarvis.click("#settingsUsage .usage-details summary")
    head = jarvis.eval_on_selector_all("#settingsUsage .usage-table thead th", "els => els.map(e => e.textContent)")
    assert head == ["Jour", "Voix", "Claude", "Siri", "Total"]
    row = jarvis.eval_on_selector("#settingsUsage .usage-table tbody tr", "r => [...r.children].map(c => c.textContent)")
    assert row[1:] == [f"0,00{NBSP}$", f"0,00{NBSP}$", f"0,60{NBSP}$", f"0,60{NBSP}$"]
    assert "Siri" in norm(jarvis.get_attribute("#costChip button", "title"))
