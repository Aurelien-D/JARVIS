"""Réglages and Mise en route (WP12) in Chromium: the first-run checklist, the
drawer, field by field saving with its French notes, the sensitive settings
and their confirmation, the OpenAI key that never comes back, inert text,
accessibility and layout. The server runs in this process: tests swap its
health checks and data folder, and every setting changed is put back."""
import json
import re
import time

import pytest

from jarvis import config, health, settings

pytestmark = pytest.mark.e2e

KEY = "sk-proj-E2EKEY0123456789abcdefWXYZ"
REAL_ONBOARDED = health.onboarded  # the harness plays an onboarded user; the first run needs the real one
AXE_FILE = __import__("pathlib").Path(__file__).resolve().parent / "vendor" / "axe.min.js"


def check(id_, level, message, fix="", title=None):
    return {"id": id_, "ok": level in ("ok", "info"), "level": level, "title_fr": title or id_,
            "message_fr": message, "fix_fr": fix}


# What the page sees at load: nothing to fix, so nothing opens by itself.
CHECKS = [
    check("openai", "ok", "Clé OpenAI valide · modèle gpt-realtime-2.1 disponible.", title="Clé OpenAI"),
    check("claude", "ok", "Claude Code 2.1.300 installé et connecté (compte claude.ai).", title="Claude Code"),
    check("workdir", "info", "Le dossier de travail sera créé à la première tâche.", title="Dossier de travail"),
    check("deadline_transcribe", "info", "OpenAI arrête whisper-1 le 26 février 2027.", title="Échéance OpenAI"),
]
# The same with a refused key.
BAD_KEY = [check("openai", "error", "Clé OpenAI refusée.", "Vérifiez-la sur platform.openai.com/api-keys.",
                 "Clé OpenAI"), *CHECKS[1:]]


@pytest.fixture
def server_side(app_server, tmp_path, monkeypatch):
    """Health checks as given, a .env in tmp, and every setting back afterwards.
    (After app_server: the harness sets its own empty checks when it starts.)"""
    for s in settings.SCHEMA:
        if not s.attr.startswith("MODELS."):
            monkeypatch.setattr(config, s.attr, getattr(config, s.attr))
    monkeypatch.setattr(config, "MODELS", dict(config.MODELS))
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(settings, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(settings, "_BOOT", dict(settings._BOOT))
    seen = {"list": [dict(c) for c in CHECKS], "refresh": []}

    def run_checks(refresh=False):
        seen["refresh"].append(refresh)
        return [dict(c) for c in seen["list"]]
    monkeypatch.setattr(health, "run_checks", run_checks)
    yield seen
    for name in ("settings.json", "settings.json.bak"):
        (config.DATA_DIR / name).unlink(missing_ok=True)


def open_settings(page, section=None):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    if section:
        page.click(f"#settingsDialog .set-tab:has-text('{section}')")
        page.wait_for_selector(f"#settingsDialog .set-tab[aria-current='true']:has-text('{section}')")


APPLY_PERMISSION = "#settingsDialog [data-key='permission_mode'] button.set-apply"


def field_status(page, key):
    page.wait_for_function("k => { const s = document.querySelector(`#settingsDialog [data-key='${k}'] .set-status`);"
                           " return s && s.textContent.trim().length > 0; }", arg=key)
    return page.text_content(f"#settingsDialog [data-key='{key}'] > .set-status")


def until(page, condition, timeout=5.0):
    """Server-side state changes after the page's request: poll it, never read too early."""
    end = time.time() + timeout
    while not condition():
        if time.time() > end:
            raise AssertionError("condition jamais remplie")
        page.wait_for_timeout(50)


def axe_violations(page):
    page.add_script_tag(path=str(AXE_FILE))
    return page.evaluate("""async () => {
      const r = await axe.run(document, {resultTypes: ['violations']});
      return r.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')
        .map(v => ({id: v.id, nodes: v.nodes.slice(0, 5).map(n => n.target.join(' '))}));
    }""")

# ---------------------------------------------------------------- the first run (acceptance)


def test_a_fresh_data_dir_opens_the_onboarding_and_terminer_keeps_it_closed(server_side, jarvis, reload_jarvis,
                                                                              tmp_path, monkeypatch):
    fresh = tmp_path / "fresh-data"
    monkeypatch.setattr(config, "DATA_DIR", fresh)
    monkeypatch.setattr(health, "onboarded", REAL_ONBOARDED)
    server_side["list"] = BAD_KEY
    reload_jarvis()
    jarvis.wait_for_selector("#onboarding[open]")
    assert jarvis.text_content("#obTitle") == "Mise en route"
    # The seven steps, filled by the server's checks and the page's own.
    jarvis.wait_for_selector("#onboarding .ob-row[data-check='openai'][data-state='fix']")
    titles = jarvis.eval_on_selector_all("#onboarding .ob-steps > .ob-row .ob-title strong", "els => els.map(e => e.textContent)")
    assert titles == ["Clé OpenAI", "Claude Code", "Micro", "Votre micro est…", "Mot d'éveil", "A.R.E.S", "Notifications"]
    assert jarvis.get_attribute("#onboarding .ob-row[data-check='claude']", "data-state") == "ok"
    assert "Clé OpenAI refusée." in jarvis.text_content("#onboarding .ob-row[data-check='openai']")
    jarvis.wait_for_selector("#onboarding .ob-row[data-check='micro'][data-state='ok']")  # granted fake microphone
    others = jarvis.text_content("#onboarding .ob-others")
    assert "Le dossier de travail sera créé" in others and "whisper-1" in others
    # The state is written as a word, never left to colour alone.
    assert jarvis.text_content("#onboarding .ob-row[data-check='openai'] .ob-state") == "À corriger"

    jarvis.click("#onboarding .ob-foot button:has-text('Terminer')")
    jarvis.wait_for_function("!document.getElementById('onboarding').open")
    assert json.loads((fresh / "state.json").read_text(encoding="utf-8"))["onboarded"]
    with jarvis.expect_response(lambda r: "/api/health" in r.url):
        reload_jarvis()
    jarvis.wait_for_timeout(300)  # the answer is in: time for a dialog that should not come
    assert not jarvis.evaluate("document.getElementById('onboarding').open")


def test_a_new_error_reopens_it_once(server_side, jarvis, reload_jarvis):
    server_side["list"] = BAD_KEY
    with jarvis.expect_response(lambda r: "/api/health" in r.url):
        reload_jarvis()
    jarvis.wait_for_selector("#onboarding[open]")  # onboarded, but an error nobody saw yet
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_function("!document.getElementById('onboarding').open")
    with jarvis.expect_response(lambda r: "/api/health" in r.url):
        reload_jarvis()
    jarvis.wait_for_timeout(300)
    assert not jarvis.evaluate("document.getElementById('onboarding').open")  # the same error: no nagging
    server_side["list"].append(check("data", "error", "Le dossier des données n'est pas modifiable."))
    with jarvis.expect_response(lambda r: "/api/health" in r.url):
        reload_jarvis()
    jarvis.wait_for_selector("#onboarding[open]")


def open_onboarding(page, server_side, checks=BAD_KEY):
    server_side["list"] = checks
    page.evaluate("__jarvis.bus.emit('ui:open', 'onboarding')")
    page.wait_for_selector("#onboarding .ob-row[data-check='openai'][data-state='fix']")


def test_onboarding_tests_and_saves(server_side, jarvis, monkeypatch):
    monkeypatch.setattr(config, "NOISE_REDUCTION", "far_field")
    open_onboarding(jarvis, server_side)
    jarvis.wait_for_selector("#ob-far_field:checked")
    # Votre micro est… : a headset means near_field, applied at the next conversation.
    jarvis.click("#onboarding label:has-text('Un casque')")
    jarvis.wait_for_function("document.querySelector(\"#onboarding .ob-row[data-check='mic_kind'] .set-status\").textContent.length > 0")
    until(jarvis, lambda: config.NOISE_REDUCTION == "near_field")
    assert "prochaine conversation" in jarvis.text_content("#onboarding .ob-row[data-check='mic_kind'] .set-status")
    # Tester le micro: the fake device's tone moves the meter.
    jarvis.click("#onboarding button:has-text('Tester le micro')")
    jarvis.wait_for_selector("#onboarding meter.ob-meter")  # shown once the microphone is open
    jarvis.wait_for_function("document.querySelector(\"#onboarding .ob-row[data-check='micro'] .set-status\").textContent"
                             ".includes('Le micro fonctionne')", timeout=15_000)
    # Tester la voix: the browser's voice, said to be the one used outside conversations.
    jarvis.click("#onboarding button:has-text('Tester la voix')")
    assert "hors conversation" in jarvis.text_content("#onboarding .ob-voice-note")
    # Revérifier skips the server's caches.
    server_side["refresh"].clear()
    jarvis.click("#onboarding button:has-text('Revérifier')")
    jarvis.wait_for_function("!document.querySelector('#onboarding .ob-foot button').disabled")
    assert server_side["refresh"] == [True]


def test_the_key_is_saved_and_never_shown_again(server_side, jarvis, tmp_path):
    open_onboarding(jarvis, server_side)
    jarvis.wait_for_selector("#onboarding .ob-row[data-check='openai'] .key-form")
    jarvis.click("#onboarding .key-form button:has-text('Remplacer la clé')")
    jarvis.fill("#onboarding .key-form input[type='password']", KEY)
    with jarvis.expect_response(lambda r: "/api/settings/openai-key" in r.url) as resp:
        jarvis.click("#onboarding .key-form button:has-text('Enregistrer la clé')")
    assert KEY not in resp.value.text()
    jarvis.wait_for_selector("#onboarding .key-current:has-text('sk-…WXYZ')")
    assert (tmp_path / ".env").read_text(encoding="utf-8").strip() == f"OPENAI_API_KEY={KEY}"
    assert config.OPENAI_API_KEY == KEY
    # Nowhere in the page, not even in a field's value.
    assert KEY not in jarvis.content()
    assert jarvis.evaluate("k => [...document.querySelectorAll('input')].every(i => i.value !== k)", KEY)

# ---------------------------------------------------------------- the drawer


def test_reglages_opens_as_a_modal_drawer_and_gives_the_focus_back(server_side, jarvis):
    assert jarvis.is_visible("#topActions button:has-text('Réglages')")  # announced by ui:ready
    open_settings(jarvis)
    tabs = jarvis.eval_on_selector_all("#settingsDialog .set-tab", "els => els.map(e => e.textContent)")
    assert tabs == ["Connexion", "Voix", "Écoute", "Proactivité", "Claude Code", "Coûts", "Système", "Données", "À propos"]
    assert jarvis.evaluate("document.getElementById('settingsDialog').matches(':modal')")
    assert jarvis.evaluate("document.getElementById('settingsDialog').contains(document.activeElement)")
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_function("!document.getElementById('settingsDialog').open")
    jarvis.wait_for_function("document.activeElement.textContent === 'Réglages'")  # back on the invoker


def test_voice_change_is_saved_for_the_next_conversation(server_side, jarvis):
    open_settings(jarvis, "Voix")
    assert "prochaine conversation" in jarvis.text_content("#set-sec-voix .set-note")
    jarvis.select_option("#set-voice", "cedar")
    assert field_status(jarvis, "voice") == "Enregistré · appliqué à la prochaine conversation."
    assert config.VOICE == "cedar"
    assert json.loads((config.DATA_DIR / "settings.json").read_text(encoding="utf-8"))["voice"] == "cedar"


def test_a_refused_value_is_explained_in_french(server_side, jarvis):
    before = config.IDLE_MINUTES
    open_settings(jarvis, "Écoute")
    jarvis.fill("#set-idle_minutes", "999")
    jarvis.press("#set-idle_minutes", "Enter")
    jarvis.locator("#set-idle_minutes").blur()
    text = field_status(jarvis, "idle_minutes")
    assert text == "Mise en veille après\u202f: choisissez une valeur entre 0 min et 120 min."
    assert jarvis.get_attribute("#set-idle_minutes", "aria-invalid") == "true"
    assert config.IDLE_MINUTES == before


def test_bypass_needs_a_confirmation_with_a_red_warning(server_side, jarvis):
    before = config.PERMISSION_MODE
    open_settings(jarvis, "Claude Code")
    assert jarvis.is_visible("#settingsDialog [data-key='permission_mode'] .set-lock")
    jarvis.select_option("#set-permission_mode", "bypassPermissions")
    jarvis.click(APPLY_PERMISSION)
    jarvis.wait_for_selector("#settingsConfirm[open]")
    assert jarvis.is_visible("#settingsConfirm .set-danger")
    assert "Mode sans garde-fou" in jarvis.text_content("#settingsConfirm .set-danger")
    assert "jamais à la voix" in jarvis.text_content("#settingsConfirm .set-confirm-text")
    # A dangerous change starts on Annuler.
    assert jarvis.evaluate("document.activeElement.value") == "cancel"
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_function("v => document.getElementById('set-permission_mode').value === v", arg=before)
    assert config.PERMISSION_MODE == before
    jarvis.select_option("#set-permission_mode", "bypassPermissions")
    jarvis.click(APPLY_PERMISSION)
    jarvis.wait_for_selector("#settingsConfirm[open]")
    jarvis.click("#settingsConfirm button[value='ok']")
    assert field_status(jarvis, "permission_mode") == "Enregistré · appliqué à la prochaine tâche."
    assert config.PERMISSION_MODE == "bypassPermissions"
    assert jarvis.is_visible("#settingsDialog [data-key='permission_mode'] .set-danger")


def test_arrow_keys_look_through_a_list_without_saving_or_confirming_at_each_step(server_side, jarvis):
    """WCAG 3.2.2: a closed list changes on each arrow key (and fires 'change')."""
    before = config.PERMISSION_MODE
    config.VOICE = "ballad"  # put back by server_side; three steps down stay in the list
    puts = []
    jarvis.on("request", lambda r: puts.append(r.post_data) if r.method == "PUT" and "/api/settings" in r.url else None)
    open_settings(jarvis, "Claude Code")
    jarvis.focus("#set-permission_mode")
    for _ in range(2):
        jarvis.keyboard.press("ArrowDown")
    assert jarvis.input_value("#set-permission_mode") != before  # the list did move
    jarvis.wait_for_timeout(1200)  # longer than a plain list waits before it saves
    assert not jarvis.evaluate("document.getElementById('settingsConfirm').open")
    assert puts == [] and config.PERMISSION_MODE == before
    assert field_status(jarvis, "permission_mode") == "Pas encore appliqué\u202f: choisissez «\u202fAppliquer\u202f»."
    assert jarvis.get_attribute(APPLY_PERMISSION, "aria-label") == "Appliquer\u202f: Mode de permission des tâches"
    # 'Appliquer' asks first; Échap puts the saved value back, nothing sent.
    jarvis.click(APPLY_PERMISSION)
    jarvis.wait_for_selector("#settingsConfirm[open]")
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_function("v => document.getElementById('set-permission_mode').value === v", arg=before)
    assert puts == [] and config.PERMISSION_MODE == before

    # A plain list: two steps in a row are saved once, when the choice settles.
    jarvis.click("#settingsDialog .set-tab:has-text('Voix')")
    jarvis.wait_for_selector("#set-voice")
    jarvis.focus("#set-voice")
    jarvis.keyboard.press("ArrowDown")
    jarvis.keyboard.press("ArrowDown")
    chosen = jarvis.input_value("#set-voice")
    assert field_status(jarvis, "voice") == "Enregistré · appliqué à la prochaine conversation."
    jarvis.wait_for_timeout(300)
    assert len(puts) == 1 and json.loads(puts[0]) == {"voice": chosen}
    assert config.VOICE == chosen
    # Leaving the list saves at once, without waiting.
    jarvis.keyboard.press("ArrowDown")
    third = jarvis.input_value("#set-voice")
    assert third != chosen
    jarvis.keyboard.press("Tab")
    until(jarvis, lambda: len(puts) == 2, timeout=0.6)  # sent before the list would have settled
    until(jarvis, lambda: config.VOICE == third)


def test_restart_settings_say_so(server_side, jarvis):
    open_settings(jarvis, "Système")
    jarvis.fill("#set-hotkey", "Ctrl+Alt+Maj+K")
    jarvis.locator("#set-hotkey").blur()
    assert field_status(jarvis, "hotkey") == "Enregistré · redémarrage nécessaire."
    assert config.HOTKEY == "ctrl+alt+shift+k"  # stored as the server normalised it...
    assert jarvis.input_value("#set-hotkey") == "Ctrl+Alt+Maj+K"  # ...shown as the keyboard names it
    banner = jarvis.text_content("#settingsDialog .set-restart")
    assert "Redémarrage nécessaire pour" in banner and "Raccourci global" in banner
    # A.R.E.S's own shortcut is refused, in French.
    jarvis.fill("#set-hotkey", "ctrl+alt+j")
    jarvis.locator("#set-hotkey").blur()
    jarvis.wait_for_function("document.querySelector(\"[data-key='hotkey'] .set-status\").classList.contains('err')")
    assert "A.R.E.S" in jarvis.text_content("#settingsDialog [data-key='hotkey'] .set-status")


def test_days_and_quiet_hours(server_side, jarvis, monkeypatch):
    monkeypatch.setattr(config, "BRIEFING_DAYS", "lun-ven")
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    open_settings(jarvis, "Proactivité")
    checked = jarvis.eval_on_selector_all("#settingsDialog .set-days input:checked", "els => els.map(e => e.value)")
    assert checked == ["lun", "mar", "mer", "jeu", "ven"]
    jarvis.uncheck("#set-briefing_days-mer")
    until(jarvis, lambda: config.BRIEFING_DAYS == "lun,mar,jeu,ven")
    jarvis.check("#set-briefing_days-mer")
    until(jarvis, lambda: config.BRIEFING_DAYS == "lun-ven")
    # No day at all is refused, and said so (the last valid choice stays).
    for day in ("lun", "mar", "mer", "jeu", "ven"):
        jarvis.uncheck(f"#set-briefing_days-{day}")
    jarvis.wait_for_function("document.querySelector(\"[data-key='briefing_days'] .set-status\").classList.contains('err')")
    assert "au moins un jour" in jarvis.text_content("#settingsDialog [data-key='briefing_days'] .set-status")
    assert config.BRIEFING_DAYS == "ven"
    # Quiet hours: switched on, with the usual range.
    assert not jarvis.is_checked("#set-quiet_hours")
    jarvis.check("#set-quiet_hours")
    assert field_status(jarvis, "quiet_hours") == "Enregistré."
    assert config.QUIET_HOURS == "22:30-07:30"
    jarvis.wait_for_function("__jarvis.state.config.quiet_hours === '22:30-07:30'")  # /api/config again


def test_wake_word_in_settings_switches_this_browser(server_side, jarvis):
    open_settings(jarvis, "Écoute")
    assert jarvis.is_checked("#set-wake_word")
    jarvis.uncheck("#set-wake_word")
    field_status(jarvis, "wake_word")
    assert config.WAKE_WORD is False
    assert jarvis.evaluate("__jarvis.settings.get('wake')") is False
    jarvis.keyboard.press("Escape")
    assert jarvis.text_content("#wakeBtn") == "Mot d'éveil : désactivé"


def test_browser_preferences_stay_in_this_browser(server_side, jarvis):
    open_settings(jarvis, "Écoute")
    jarvis.check("#set-ptt")
    assert jarvis.evaluate("__jarvis.settings.get('ptt')") is True
    options = jarvis.eval_on_selector_all("#set-micId option", "els => els.map(e => e.value)")
    assert options[0] == "" and len(options) >= 2  # 'Par défaut du système' and the fake devices
    jarvis.select_option("#set-micId", options[1])
    assert jarvis.evaluate("__jarvis.settings.get('micId')") == options[1]
    jarvis.click("#settingsDialog .set-tab:has-text('Système')")
    jarvis.select_option("#set-motion", "reduced")
    assert jarvis.evaluate("document.body.classList.contains('reduce-motion')")
    jarvis.select_option("#set-motion", "auto")
    # The next conversation asks for the chosen microphone.
    jarvis.keyboard.press("Escape")
    jarvis.evaluate("""() => { const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = (c) => { window.__asked = c; return real(c); }; }""")
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    assert jarvis.evaluate("window.__asked.audio.deviceId") == {"ideal": options[1]}


def test_connexion_shows_the_masked_key_and_the_health_check(server_side, jarvis):
    server_side["list"] = BAD_KEY
    open_settings(jarvis, "Connexion")
    jarvis.wait_for_selector("#setHealth .ob-row[data-check='openai']")
    assert jarvis.text_content("#settingsDialog .key-current") == "Clé enregistrée : sk-…"
    assert jarvis.get_attribute("#setHealth .ob-row[data-check='openai']", "data-state") == "fix"
    # Replacing the key in place asks first; Annuler keeps the old one.
    jarvis.click("#settingsDialog .key-form button:has-text('Remplacer la clé')")
    jarvis.fill("#settingsDialog .key-form input[type='password']", KEY)
    jarvis.click("#settingsDialog .key-form button:has-text('Enregistrer la clé')")
    jarvis.wait_for_selector("#settingsConfirm[open]")
    assert jarvis.text_content("#settingsConfirm .set-confirm-value") == "La clé OpenAI enregistrée (sk-…) sera remplacée."
    jarvis.click("#settingsConfirm button[value='cancel']")
    jarvis.wait_for_function("!document.getElementById('settingsConfirm').open")
    assert config.OPENAI_API_KEY == "sk-fake"
    jarvis.click("#settingsDialog .key-form button:has-text('Enregistrer la clé')")
    jarvis.click("#settingsConfirm button[value='ok']")
    jarvis.wait_for_selector("#settingsDialog .key-current:has-text('sk-…WXYZ')")
    assert config.OPENAI_API_KEY == KEY
    assert KEY not in jarvis.content()
    # 'Ouvrir la mise en route' hands over to the onboarding dialog.
    jarvis.click("#settingsDialog button:has-text('Ouvrir la mise en route')")
    jarvis.wait_for_selector("#onboarding[open]")
    assert not jarvis.evaluate("document.getElementById('settingsDialog').open")


def test_connexion_points_the_key_fix_at_the_form_above(server_side, jarvis, monkeypatch):
    """In Réglages › Connexion, 'go to Réglages › Connexion' would send monsieur where he is."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    absent = health.check_openai()  # the real check, the real words
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-fake")
    assert "Réglages › Connexion" in absent["fix_fr"]
    server_side["list"] = [absent, *CHECKS[1:]]
    open_settings(jarvis, "Connexion")
    row = "#setHealth .ob-row[data-check='openai']"
    jarvis.wait_for_selector(row)
    text = jarvis.text_content(row)
    assert "Collez votre clé ci-dessus. Elle se crée sur platform.openai.com/api-keys." in text
    assert "Réglages › Connexion" not in text


def about_row(page, name):
    return page.evaluate("""n => { const dt = [...document.querySelectorAll('#set-sec-apropos dt')]
      .find(d => d.textContent === n); return dt ? dt.nextElementSibling.textContent : null; }""", name)


def test_about_names_the_deadlines_of_the_models_in_use(server_side, jarvis, monkeypatch):
    from jarvis import realtime
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1")
    monkeypatch.setattr(config, "TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
    monkeypatch.setattr(config, "TRANSCRIBE_FALLBACK", "whisper-1")
    monkeypatch.setattr(realtime, "_REJECTED_TRANSCRIBE", set())
    open_settings(jarvis, "À propos")
    jarvis.wait_for_selector("#set-sec-apropos dt")
    deadlines = about_row(jarvis, "Échéances")
    assert "gpt-4o-mini-transcribe et whisper-1" in deadlines and "26 février 2027" in deadlines
    assert "gpt-realtime " not in deadlines  # gpt-realtime-2.1, in use, is not retired
    # The voice model changed in Voix: À propos follows at once.
    jarvis.click("#settingsDialog .set-tab:has-text('Voix')")
    jarvis.select_option("#set-realtime_model", "gpt-realtime")
    assert field_status(jarvis, "realtime_model") == "Enregistré · appliqué à la prochaine conversation."
    jarvis.click("#settingsDialog .set-tab:has-text('À propos')")
    jarvis.wait_for_function("""() => { const dt = [...document.querySelectorAll('#set-sec-apropos dt')]
      .find(d => d.textContent === 'Échéances'); return dt && dt.nextElementSibling.textContent
      .includes('gpt-realtime le 20 janvier 2027'); }""")
    assert about_row(jarvis, "Modèle vocal") == "gpt-realtime"


def test_server_text_stays_inert(server_side, jarvis):
    evil = '<img src=x onerror="window.__pwned=1"><b>gras</b>'
    open_onboarding(jarvis, server_side, [check("openai", "error", evil, evil, title=evil)])
    jarvis.keyboard.press("Escape")
    open_settings(jarvis, "Connexion")
    jarvis.wait_for_selector("#setHealth .ob-row[data-check='openai']")
    for root in ("#onboarding", "#settingsDialog"):
        assert jarvis.evaluate(f"document.querySelector('{root}').querySelectorAll('img, b').length") == 0
    assert evil in jarvis.text_content("#setHealth .ob-row[data-check='openai'] .ob-msg")
    assert not jarvis.evaluate("window.__pwned")


def test_typography_and_vous_in_both_dialogs(server_side, jarvis):
    open_onboarding(jarvis, server_side)
    texts = [jarvis.text_content("#onboarding")]
    jarvis.keyboard.press("Escape")
    open_settings(jarvis)
    for tab in jarvis.eval_on_selector_all("#settingsDialog .set-tab", "els => els.map(e => e.textContent)"):
        jarvis.click(f"#settingsDialog .set-tab:has-text('{tab}')")
        jarvis.wait_for_selector(f"#settingsDialog .set-section:not([hidden]) .set-section-title:has-text('{tab}')")
        texts.append(jarvis.text_content("#settingsDialog .set-section:not([hidden])"))
    for text in texts:
        assert not re.search(r"[  ][?!:;»]", text), re.findall(r".{20}[  ][?!:;»].{5}", text)
        own = re.sub(r"«[^»]*»", "", text)
        assert not re.search(r"\b(tu|toi|ton|ta|tes|te)\b", own, re.I)


@pytest.mark.parametrize("which", ["settings", "onboarding"])
def test_axe_has_no_serious_violation(server_side, jarvis, which):
    if which == "settings":
        open_settings(jarvis, "Claude Code")
    else:
        open_onboarding(jarvis, server_side)
    assert axe_violations(jarvis) == []


@pytest.mark.parametrize("size", [(1024, 700), (360, 640)])
def test_dialogs_fit_without_sideways_scroll(server_side, jarvis, size):
    jarvis.set_viewport_size({"width": size[0], "height": size[1]})
    open_settings(jarvis, "Proactivité")
    box = jarvis.eval_on_selector("#settingsDialog", "e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.bottom]; }")
    assert box[0] >= 0 and box[1] <= size[0] + 0.5 and box[2] <= size[1] + 0.5
    for sel in ("#settingsDialog", "#settingsDialog .set-panels"):
        assert jarvis.eval_on_selector(sel, "e => e.scrollWidth <= e.clientWidth + 1"), sel
    jarvis.keyboard.press("Escape")
    open_onboarding(jarvis, server_side)
    box = jarvis.eval_on_selector("#onboarding", "e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.bottom]; }")
    assert box[0] >= 0 and box[1] <= size[0] + 0.5 and box[2] <= size[1] + 0.5
    assert jarvis.eval_on_selector("#onboarding .ob-body", "e => e.scrollWidth <= e.clientWidth + 1")
