"""Wave 2 working together, in Chromium: the modules of WP10 to WP16 meet here.

- Réglages reaches the modules it configures: A.R.E.S (chip, agenda, help
  card, the voice session's tools and instructions), quiet hours (the page's
  delivery, at once), the global hotkey (the help card names it);
- the health check shows A.R.E.S and the Windows integration (hotkey, tray
  icon, start with Windows) in Réglages and in the Mise en route;
- the hotkey drives the voice and hands the keyboard to the composer;
- a full-access 'Réessayer' from the task panel waits for [Lancer], then runs,
  and the journal notes it;
- the help card lists the journal, A.R.E.S and the instant information.
The server runs in this process: every setting changed is put back."""
import time
from datetime import date, datetime, timedelta

import httpx
import pytest
from fake_ares import FakeAres
from test_untrusted_text import call, go_live

from jarvis import config, events, health, settings

pytestmark = pytest.mark.e2e

REAL_RUN_CHECKS = health.run_checks  # the harness swaps it for an empty list when it starts
ASLEEP = "['standby', 'off'].includes(__jarvis.state.mode)"


@pytest.fixture
def restore_settings(app_server, monkeypatch):
    """Every setting as it was, and no settings.json left for the next test."""
    for s in settings.SCHEMA:
        if not s.attr.startswith("MODELS."):
            monkeypatch.setattr(config, s.attr, getattr(config, s.attr))
    monkeypatch.setattr(config, "MODELS", dict(config.MODELS))
    monkeypatch.setattr(settings, "_BOOT", dict(settings._BOOT))
    yield
    for name in ("settings.json", "settings.json.bak"):
        (config.DATA_DIR / name).unlink(missing_ok=True)


@pytest.fixture
def fake_ares(monkeypatch):
    """A.R.E.S's local MCP server, faked; JARVIS starts with A.R.E.S switched off."""
    from jarvis import ares
    server = FakeAres()

    def forget():
        ares._state.update(protocol=None, session=None, version=None, tools=None,
                           down_until=0.0, probed=0.0, up=False)
        ares._agenda.update(text="", at=0.0, ok=False)
        ares._last_published.update(available=None, lines=None)

    forget()
    monkeypatch.setattr(config, "ARES", "off")
    monkeypatch.setattr(config, "ARES_URL", server.url)
    yield server
    if ares._refresher:
        ares._refresher.join(5)
    server.stop()
    forget()


def open_settings(page, section):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    page.click(f"#settingsDialog .set-tab:has-text('{section}')")
    page.wait_for_selector(f"#settingsDialog .set-tab[aria-current='true']:has-text('{section}')")


def close_settings(page):
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('settingsDialog').open")


def saved(page, key):
    """The field's own status line once the server answered (never read too early)."""
    page.wait_for_function(
        "k => { const s = document.querySelector(`#settingsDialog [data-key='${k}'] > .set-status`);"
        " return s && s.textContent.trim().length > 0; }", arg=key)
    return page.text_content(f"#settingsDialog [data-key='{key}'] > .set-status")


def help_titles(page):
    page.evaluate("import('/static/js/composer.js').then(m => m.openHelp())")
    card = page.locator("#cards .card:has-text('Ce que je sais faire')")
    card.wait_for()
    page.wait_for_function("document.querySelectorAll('#cards .aide-cat h4').length > 0")
    return card.locator(".aide-cat h4").all_text_contents()


# ---------------------------------------------------------------- Réglages -> A.R.E.S

def test_ares_switched_on_in_reglages_reaches_chip_agenda_help_and_the_voice(
        restore_settings, fake_ares, jarvis, app_server):
    # Off (the harness): no chip, no agenda, no A.R.E.S examples, no A.R.E.S tools.
    jarvis.wait_for_function("__jarvis.state.ares && __jarvis.state.ares.mode === 'off'")
    assert jarvis.locator("#aresChip").is_hidden() and jarvis.locator("#agenda").is_hidden()
    assert "Agenda A.R.E.S" not in help_titles(jarvis)

    open_settings(jarvis, "Système")
    jarvis.select_option("#set-ares", "auto")
    assert saved(jarvis, "ares") == "Enregistré."
    assert config.ARES == "auto"
    # Every page reloads A.R.E.S on 'config': the chip and the agenda follow at once.
    jarvis.wait_for_selector("#aresChip:has-text('A.R.E.S ●')")
    jarvis.wait_for_selector("#agenda:not([hidden]) .agenda-item")
    close_settings(jarvis)
    assert "Agenda A.R.E.S" in help_titles(jarvis)

    # The next conversation gets the three A.R.E.S tools and the agenda as data.
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    session = app_server.sessions[-1]["session"]
    names = {t["name"] for t in session["tools"]}
    assert {"ares_lire", "ares_ajouter", "ares_modifier", "recall", "info"} <= names
    assert "# A.R.E.S\n" in session["instructions"] and "<donnees>" in session["instructions"]
    jarvis.click("#orbBtn")
    jarvis.wait_for_function(ASLEEP)

    # Switched off again: everything A.R.E.S goes away, nothing more is sent to it.
    open_settings(jarvis, "Système")
    jarvis.select_option("#set-ares", "off")
    jarvis.wait_for_function("__jarvis.state.ares.mode === 'off'")
    jarvis.wait_for_selector("#aresChip", state="hidden")
    assert jarvis.locator("#agenda").is_hidden()
    close_settings(jarvis)
    assert "Agenda A.R.E.S" not in help_titles(jarvis)
    before = len(fake_ares.requests)
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    assert not {"ares_lire"} & {t["name"] for t in app_server.sessions[-1]["session"]["tools"]}
    assert len(fake_ares.requests) == before


# ---------------------------------------------------------------- Réglages -> quiet hours, hotkey

def test_quiet_hours_set_in_reglages_reach_the_page_at_once(restore_settings, jarvis):
    jarvis.wait_for_function("__jarvis.state.quiet === false")
    assert jarvis.locator("#dndChip").is_hidden()
    now = datetime.now()  # an hour each side: true whatever the time, midnight included
    calm = f"{now - timedelta(hours=1):%H:%M}-{now + timedelta(hours=1):%H:%M}"
    jarvis.evaluate("v => __jarvis.api('/api/settings', {method: 'PUT', body: {quiet_hours: v}})", calm)
    # delivery.js reads them again on 'config', not at its next 2-minute poll.
    jarvis.wait_for_function("__jarvis.state.quiet === true", timeout=5000)
    # One glance says why reminders are silent: the chip, which opens the setting.
    chip = jarvis.locator("#dndChip button")
    chip.wait_for()
    assert chip.inner_text() == "Heures calmes"
    assert chip.get_attribute("aria-label") == "Heures calmes · Modifier dans Réglages › Proactivité"
    assert jarvis.get_attribute("#dndChip", "class") == "chip quiet"
    chip.click()
    jarvis.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']:has-text('Proactivité')")
    close_settings(jarvis)
    jarvis.evaluate("__jarvis.api('/api/settings', {method: 'PUT', body: {quiet_hours: ''}})")
    jarvis.wait_for_function("__jarvis.state.quiet === false", timeout=5000)
    jarvis.wait_for_selector("#dndChip", state="hidden")
    # « Ne pas déranger » during quiet hours: the chip names it, its button ends it.
    jarvis.evaluate("v => __jarvis.api('/api/settings', {method: 'PUT', body: {quiet_hours: v}})", calm)
    jarvis.wait_for_function("document.querySelector('#dndChip button')?.textContent === 'Heures calmes'")
    jarvis.click("#dndToggle")
    jarvis.wait_for_function("document.querySelector('#dndChip button')?.textContent.startsWith('Ne pas déranger')")
    jarvis.click("#dndChip button")
    jarvis.wait_for_function("document.querySelector('#dndChip button')?.textContent === 'Heures calmes'")


def test_the_help_card_names_the_hotkey_set_in_reglages(restore_settings, jarvis, reload_jarvis):
    jarvis.evaluate("__jarvis.api('/api/settings', {method: 'PUT', body: {hotkey: 'Ctrl+Alt+Maj+K'}})")
    assert config.HOTKEY == "ctrl+alt+shift+k"
    reload_jarvis()
    titles = help_titles(jarvis)
    assert {"Mémoire et journal", "Météo et actualités"} <= set(titles)
    keys = jarvis.locator("#cards .aide-keys li").all_text_contents()
    assert keys[-1] == "Ctrl+Alt+Maj+K : depuis n'importe où"


# ---------------------------------------------------------------- health: A.R.E.S and Windows

def test_health_shows_ares_and_the_windows_integration(restore_settings, fake_ares, jarvis, monkeypatch):
    from jarvis import desktop, shell
    monkeypatch.setattr(health, "run_checks", REAL_RUN_CHECKS)  # with only the two checks below
    monkeypatch.setattr(health, "CHECKS", [health.check_windows, health.check_ares])
    monkeypatch.setattr(config, "ARES", "auto")
    # JARVIS as started on Windows: Windows refused the hotkey, pystray is missing.
    monkeypatch.setattr(health, "_windows", lambda: True)
    monkeypatch.setattr(shell, "running", lambda: True)
    monkeypatch.setattr(shell, "hotkey_state", lambda: {"active": False, "combo": ""})
    monkeypatch.setattr(shell, "tray_active", lambda: False)
    monkeypatch.setattr(config, "HOTKEY", "ctrl+alt+shift+j")
    monkeypatch.setattr(config, "TRAY", True)
    monkeypatch.setattr(desktop, "autostart_enabled", lambda: True, raising=False)

    open_settings(jarvis, "Connexion")
    jarvis.wait_for_selector("#setHealth .ob-row[data-check='ares']")
    rows = {r["id"]: r for r in jarvis.evaluate("""[...document.querySelectorAll('#setHealth .ob-row')]
        .map(r => ({id: r.dataset.check, state: r.dataset.state, text: r.textContent}))""")}
    assert rows["ares"]["state"] == "info" and "A.R.E.S répond" in rows["ares"]["text"]
    assert rows["hotkey"]["state"] == "fix" and "Ctrl+Alt+Maj+J" in rows["hotkey"]["text"]
    assert "Réglages › Système" in rows["hotkey"]["text"]
    assert rows["tray"]["state"] == "info" and "pystray" in rows["tray"]["text"]
    assert "démarre avec Windows" in rows["autostart"]["text"]

    # Revérifier asks A.R.E.S again (no 30 s pause kept): now it is gone.
    fake_ares.stop()
    jarvis.click("#settingsDialog button:has-text('Revérifier')")
    jarvis.wait_for_function("""(() => { const r = document.querySelector("#setHealth .ob-row[data-check='ares']");
        return r && r.textContent.includes("n'est pas joignable"); })()""")

    # The Mise en route lists the Windows rows under 'Autres vérifications'.
    jarvis.click("#settingsDialog button:has-text('Ouvrir la mise en route')")
    jarvis.wait_for_selector("#onboarding[open] .ob-row[data-check='hotkey']")


# ---------------------------------------------------------------- the hotkey drives voice and composer

def hotkey(action="toggle"):
    events.publish("hotkey", {"action": action, "at": time.time()})


def test_hotkey_starts_the_voice_and_hands_the_keyboard_to_the_composer(jarvis):
    jarvis.wait_for_function(ASLEEP)
    hotkey()
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    jarvis.wait_for_function("document.activeElement && document.activeElement.id === 'askInput'")
    # Typed rather than said: the same conversation gets it.
    jarvis.keyboard.type("Quelle heure est-il ?")
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_function("__texts().includes('Quelle heure est-il ?')")
    hotkey()
    jarvis.wait_for_function(ASLEEP)
    # With 'Maintenir Espace pour parler', the orb takes the keyboard instead.
    jarvis.evaluate("__jarvis.settings.set('ptt', true)")
    try:
        hotkey("talk")
        jarvis.wait_for_function("__jarvis.state.mode === 'live'")
        jarvis.wait_for_function("document.activeElement && document.activeElement.id === 'orbBtn'")
    finally:
        jarvis.evaluate("__jarvis.settings.set('ptt', false)")


def test_the_hotkey_closes_reglages_first_and_never_confirms_a_sensitive_change(restore_settings, jarvis):
    """A modal makes the page inert: the conversation must not start behind it."""
    before = config.PERMISSION_MODE
    puts = []
    jarvis.on("request", lambda r: puts.append(r.post_data) if r.method == "PUT" and "/api/settings" in r.url else None)
    jarvis.wait_for_function(ASLEEP)
    open_settings(jarvis, "Claude Code")
    jarvis.select_option("#set-permission_mode", "bypassPermissions")
    jarvis.click("#settingsDialog [data-key='permission_mode'] button.set-apply")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    hotkey()
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    jarvis.wait_for_function("!document.getElementById('settingsDialog').open"
                             " && !document.getElementById('settingsConfirm').open")
    jarvis.wait_for_function("document.activeElement && document.activeElement.id === 'askInput'")
    jarvis.wait_for_timeout(300)  # the dialogs' own close handlers ran: the focus stays
    assert jarvis.evaluate("document.activeElement.id") == "askInput"
    assert puts == [] and config.PERMISSION_MODE == before
    # A report open: the same.
    hotkey()
    jarvis.wait_for_function(ASLEEP)
    jarvis.evaluate("import('/static/js/report.js').then(m => m.showReport({title: 'Rapport'}))")
    jarvis.wait_for_selector("#report[open]")
    hotkey("talk")
    jarvis.wait_for_function("__jarvis.state.mode === 'live' && !document.getElementById('report').open")
    jarvis.wait_for_function("document.activeElement && document.activeElement.id === 'askInput'")


# ---------------------------------------------------------------- panel -> confirmation -> task -> journal

def test_full_access_retry_waits_for_lancer_then_runs_and_the_journal_notes_it(jarvis):
    from jarvis import confirm, journal, tasks
    confirm.PENDING.clear()
    old = {"id": "w2c0mpl1", "title": "Rangement des factures", "prompt": "Range les factures par mois",
           "profile": "complet", "complexity": "simple", "model": "sonnet", "origin": "voix",
           "status": "error", "output": "Échec", "progress": "", "steps": 1, "started": time.time() - 60,
           "ended": time.time(), "session_id": None, "resumed_from": None, "files": [],
           "permission_denials": []}
    tasks.TASKS[old["id"]] = dict(old)
    try:
        jarvis.evaluate("tk => __jarvis.bus.emit('server:task', tk)", old)
        jarvis.click("#task-w2c0mpl1 .actions button.retry")
        card = jarvis.locator(".card.confirm[data-state='pending']:has-text('Rangement des factures')")
        card.wait_for()
        running = [t for t in tasks.TASKS.values() if t["profile"] == "complet" and t["status"] in tasks.ACTIVE]
        assert running == []  # nothing before monsieur's click
        card.locator("button:has-text('Lancer')").click()
        jarvis.wait_for_selector(".card[data-state='done']:has-text('Rangement des factures')")
        end = time.time() + 10
        fresh = []
        while time.time() < end and not fresh:
            fresh = [t for t in tasks.TASKS.values() if t["id"] != old["id"] and t["profile"] == "complet"
                     and t["prompt"] == old["prompt"]]
            time.sleep(0.05)
        assert fresh, "la tâche n'a pas démarré après Lancer"
        jarvis.wait_for_selector(f"#task-{fresh[0]['id']}")
        end = time.time() + 6
        lines = []
        while time.time() < end:
            lines = [r["text"] for r in journal.read_day(date.today().isoformat()) if r["role"] == "system"]
            if any("Rangement des factures" in t for t in lines):
                break
            time.sleep(0.1)
        assert any(t.startswith("Tâche lancée") and "Rangement des factures" in t for t in lines), lines
    finally:
        tasks.TASKS.pop(old["id"], None)
        confirm.PENDING.clear()


# ---------------------------------------------------------------- instant information on screen

NEWS = ('<?xml version="1.0"?><rss version="2.0"><channel><title>Fil</title>'
        + "".join(f"<item><title>Titre {i}</title><link>https://news.example/{i}</link></item>" for i in range(1, 5))
        + "<item><title>Titre 5</title><link>javascript:alert(1)</link></item>"
        + "</channel></rss>").encode()


def open_meteo_and_news(request):
    if request.url.host.startswith("geocoding"):
        return httpx.Response(200, json={"results": [{"name": "Laon", "latitude": 49.56, "longitude": 3.62}]})
    if "open-meteo" in request.url.host:
        return httpx.Response(200, json={"current": {"temperature_2m": 12, "weather_code": 61}, "daily": {}})
    return httpx.Response(200, content=NEWS)


def test_weather_and_headlines_show_themselves_on_a_card(jarvis, monkeypatch):
    """Say it short, show it full: JARVIS sums up, the card keeps it all."""
    from jarvis import confirm, info
    info._feeds.clear()
    info._forecasts.clear()
    monkeypatch.setattr(info, "TRANSPORT", httpx.MockTransport(open_meteo_and_news))
    monkeypatch.setattr(config, "NEWS_FEEDS", "https://news.example/rss.xml")
    try:
        go_live(jarvis)
        call(jarvis, "info", {"type": "meteo", "ville": "Laon"}, "m1")
        card = jarvis.locator("#card-info-meteo")
        card.wait_for()
        assert card.locator("h3").inner_text() == "Météo · Laon"
        assert card.locator(".body p").first.inner_text().startswith("À Laon, en ce moment : 12 °C")
        assert card.locator(".body .meta").inner_text() == info.CREDIT  # Open-Meteo's credit (CC-BY)

        call(jarvis, "info", {"type": "actus"}, "a1")
        jarvis.wait_for_selector("#card-info-actus li")
        items = jarvis.evaluate("""[...document.querySelectorAll('#card-info-actus li')].map(li => {
          const a = li.querySelector('a');
          return {text: li.textContent, href: a && a.getAttribute('href'), target: a && a.target, rel: a && a.rel}; })""")
        assert [i["text"] for i in items] == [f"Titre {n} · news.example" for n in range(1, 6)]
        assert items[0] == {"text": "Titre 1 · news.example", "href": "https://news.example/1",
                            "target": "_blank", "rel": "noopener noreferrer"}
        assert items[4]["href"] is None  # a javascript: link stays text
        assert jarvis.locator("#card-info-actus h3").inner_text() == "Titres de l'actualité"
        # Asked again: the same card, updated in place.
        call(jarvis, "info", {"type": "actus"}, "a2")
        jarvis.wait_for_timeout(300)
        assert jarvis.locator("#cards [id^='card-info-actus']").count() == 1
        # A failure shows nothing (JARVIS says why).
        monkeypatch.setattr(info, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(503)))
        info._forecasts.clear()
        jarvis.evaluate("document.getElementById('card-info-meteo').remove()")
        call(jarvis, "info", {"type": "meteo", "ville": "Paris"}, "m2")
        jarvis.wait_for_timeout(300)
        assert jarvis.locator("#card-info-meteo").count() == 0
    finally:
        info._feeds.clear()
        info._forecasts.clear()
        confirm.PENDING.clear()
