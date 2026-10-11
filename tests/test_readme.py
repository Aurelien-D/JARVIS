"""The README (WP08) is the guide a non-developer follows: every command,
setting name, default, shortcut and date it gives must be the code's own.
These tests read the code's sources (a local .env must not change what is
checked) and fail as soon as the README and the code disagree."""
import re
import time
from pathlib import Path

import pytest
from remote_helpers import IP, LOGIN, REMOTE_HOST, enable_remote
from test_security import served_routes

from jarvis import (
    audit,
    briefing,
    config,
    devices,
    health,
    listener,
    notify,
    raccourci,
    remote,
    scheduler,
    settings,
    shell,
    tailscale,
    tasks,
    tools,
    usage,
)

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
FLAT = " ".join(README.split())  # prose as one line: a phrase may wrap anywhere
CONFIG_SRC = Path(config.__file__).read_text(encoding="utf-8")
KEYS_JS = (ROOT / "static/js/keys.js").read_text(encoding="utf-8")
COMPOSER_JS = (ROOT / "static/js/composer.js").read_text(encoding="utf-8")
STRINGS_JS = (ROOT / "static/js/strings-fr.js").read_text(encoding="utf-8")
WAKE_JS = (ROOT / "static/js/wake.js").read_text(encoding="utf-8")
SERVER_SRC = (ROOT / "server.py").read_text(encoding="utf-8")
SHELL_SRC = Path(shell.__file__).read_text(encoding="utf-8")
SETTINGS_SRC = Path(settings.__file__).read_text(encoding="utf-8")
BAT = (ROOT / "JARVIS.bat").read_text(encoding="utf-8")
REMOTE_SRC = Path(remote.__file__).read_text(encoding="utf-8")
TAILSCALE_SRC = Path(tailscale.__file__).read_text(encoding="utf-8")
LISTENER_SRC = Path(listener.__file__).read_text(encoding="utf-8")
RACCOURCI_SRC = Path(raccourci.__file__).read_text(encoding="utf-8")
NOTIFY_SRC = Path(notify.__file__).read_text(encoding="utf-8")
SIRI_UI_JS = (ROOT / "static/js/siri-ui.js").read_text(encoding="utf-8")
REMOTE_SETTINGS_JS = (ROOT / "static/js/remote-settings.js").read_text(encoding="utf-8")
IPHONE = "## 📱 JARVIS sur l'iPhone"


def default(name: str) -> str:
    """The default config.py falls back on for an environment variable, as written there."""
    found = re.search(rf'"{name}", "?([^")]*)"?\)', CONFIG_SRC)
    assert found, name
    return {"True": "1", "False": "0"}.get(found.group(1), found.group(1))


def raw_section(heading: str) -> str:
    """A '## ' section of the README, up to the next one, line by line (its tables)."""
    start = README.index(heading)
    end = README.find("\n## ", start + len(heading))
    return README[start:end if end > 0 else len(README)]


def section(heading: str) -> str:
    """The same section as one line, for its prose."""
    return " ".join(raw_section(heading).split())


def test_crlf_line_endings():
    raw = (ROOT / "README.md").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_acceptance_sections_and_facts():
    for needle in ("Coexistence avec A.R.E.S", "20 janvier 2027", "26 février 2027", "install.ps1",
                   "Ctrl+Alt+Maj+J"):
        assert needle in README, needle
    for heading in ("## 💰 Coûts et plafonds", "## 🎛️ Réglages", "## ⌨️ Raccourcis",
                    "## 🤝 Coexistence avec A.R.E.S", "## 🔒 Sécurité", "## 🔁 Ce qui part où",
                    "## 📅 Échéances OpenAI", "## 🔄 Mettre à jour", "## ❓ Problèmes fréquents",
                    "## 🧪 Pour les développeurs"):
        assert heading in README, heading


def test_claude_code_is_installed_with_the_native_installer():
    native = README.index("irm https://claude.ai/install.ps1 | iex")
    assert "claude update" in README
    npm = README.find("npm install -g @anthropic-ai/claude-code")
    if npm >= 0:  # only ever as the alternative, after the native installer
        assert native < npm and "Autre méthode" in README[npm - 200:npm]


def test_claude_code_minimum_version_is_the_health_checks():
    assert f"Claude Code **{'.'.join(map(str, health.CLAUDE_MIN))} ou plus récent**" in README


def test_deadlines_match_the_health_check():
    assert health.REALTIME_END.isoformat() == "2027-01-20" and health.TRANSCRIBE_END.isoformat() == "2027-02-26"
    deadlines = section("## 📅 Échéances OpenAI")
    assert "gpt-realtime-2.1-mini" in deadlines and default("REALTIME_MODEL") in deadlines
    for model in health.OLD_TRANSCRIBE:  # every retired transcription model is named
        assert model in deadlines, model
    assert default("JARVIS_TRANSCRIBE_MODEL") in deadlines and default("JARVIS_TRANSCRIBE_FALLBACK") in deadlines
    assert "JARVIS_TRANSCRIBE_MODEL=" in deadlines  # what to test, and how


def test_security_section_tells_the_truth():
    security = FLAT[FLAT.index("## 🔒 Sécurité"):FLAT.index("## 🔁 Ce qui part où")]
    for needle in ("liste blanche", "Accès complet", "confirmation", "auto", "~/JARVIS-travail",
                   "pas une barrière", "script", "Web uniquement", "Lecture seule", "deux minutes",
                   "90 secondes", "3 secondes"):
        assert needle in security, needle
    assert default("JARVIS_WORKDIR") == "~/JARVIS-travail"
    assert default("JARVIS_PENDING_TTL") == "90" and default("JARVIS_SECRET_TTL") == "120"
    assert "resterait bloquée" not in README  # a headless prompt is denied, it never hangs
    assert "bypassPermissions` (aucune demande)" not in README


def test_coexistence_update_and_data_flows():
    ares = section("## 🤝 Coexistence avec A.R.E.S")
    assert "Hey Jarvis" in ares and "Vosk" in ares and "Arès" in ares and "6178" in ares
    assert "## 🔄 Mettre à jour" in README and "pip install -r requirements.txt" in README
    flows = section("## 🔁 Ce qui part où")
    for where in ("OpenAI", "Google", "Microsoft", "Anthropic", "Open-Meteo", "RSS", "A.R.E.S",
                  "127.0.0.1:6178"):
        assert where in flows, where


def test_ares_mcp_command_matches_the_config():
    """The exact command, with the name the deny rules use and A.R.E.S's own address."""
    name, url = default("JARVIS_ARES_MCP_NAME"), default("JARVIS_ARES_URL")
    assert url == "http://127.0.0.1:6178/mcp"
    assert f"claude mcp add --transport http --scope user {name} {url}" in section("## 🤝 Coexistence avec A.R.E.S")


def test_launch_commands_match_the_server():
    assert '"--app"' in SERVER_SRC and '"--autostart", choices=["on", "off"]' in SERVER_SRC
    assert "server.py --app" in BAT
    for command in ("python server.py --app", "python server.py --autostart on"):
        assert command in README, command
    assert f"http://127.0.0.1:{default('JARVIS_PORT')}" in README


def test_shortcut_table_is_the_keyboard_map():
    table = raw_section("## ⌨️ Raccourcis")
    shown = set()
    for row in re.findall(r"^\| (\*\*.+?) \|", table, re.MULTILINE):
        shown.update(re.findall(r"\*\*(.+?)\*\*", row))
    hotkey = shell.hotkey_label(default("JARVIS_HOTKEY"))
    assert hotkey == "Ctrl+Alt+Maj+J"
    handled = {  # each key shown -> what in the code handles it
        hotkey: 'bus.on("server:hotkey"' in KEYS_JS,
        "Espace": 'key === " "' in KEYS_JS,
        "Maintenir Espace": "pttDown()" in KEYS_JS,
        "Ctrl+J": 'lower === "j"' in KEYS_JS,
        "/": 'key === "/"' in KEYS_JS,
        "Entrée": 'form.addEventListener("submit"' in COMPOSER_JS,
        "↑": '"ArrowUp"' in COMPOSER_JS,
        "↓": '"ArrowDown"' in COMPOSER_JS,
        "Échap": '"Escape"' in KEYS_JS,
        "Ctrl+M": 'lower === "m"' in KEYS_JS,
        "?": 'key === "?"' in KEYS_JS,
    }
    assert shown == set(handled), shown ^ set(handled)
    assert all(handled.values()), [k for k, ok in handled.items() if not ok]
    assert "HISTORY_MAX = 20" in COMPOSER_JS and "20 derniers messages" in section("## ⌨️ Raccourcis")
    # The Aide card's own list says the same keys.
    help_keys = re.search(r'shortcuts: "([^"]+)"', STRINGS_JS.split("help: {", 1)[1]).group(1)
    for part in help_keys.split(" · "):
        assert part.split(" : ")[0] in shown, part


def test_reglages_sections_are_real():
    titles = {title for _, title in settings.SECTIONS}
    # A.R.E.S has Réglages of its own (« A.R.E.S › Réglages › Application de bureau »).
    for name in re.findall(r"(?<!A\.R\.E\.S › )Réglages › (Claude Code|À propos|Accès à distance|[A-ZÉ][\wéèû]+)", FLAT):
        assert name in titles, name
    table = raw_section("## 🎛️ Réglages")
    rows = set(re.findall(r"^\| \*\*(.+?)\*\* \|", table, re.MULTILINE))
    assert rows == titles, rows ^ titles


def test_ui_labels_quoted_are_the_interfaces():
    code = STRINGS_JS + SETTINGS_SRC + SHELL_SRC
    for label in ("Maintenir Espace pour parler", "Lancer JARVIS au démarrage de Windows",
                  "Ouvrir le dossier data", "Purger le journal", "Ouvrir la mise en route", "Revérifier",
                  "Tester le micro", "Tester la voix", "Terminer", "Ajouter les titres de l'actualité",
                  "Confirmation requise", "Lancer", "Autoriser", "Refuser", "Ouvrir quand même",
                  "Voir les chiffres", "Ne pas déranger 1 h", "Effacer l'historique",
                  "Afficher dans l'explorateur", "Dernier rapport", "Interrompre", "Veille",
                  "JARVIS a remarqué", "Ce que je sais faire", "+10 min", "+1 h", "Demain", "Fait"):
        assert label in FLAT, label
        assert label in code, label


def test_onboarding_steps_in_order():
    steps = re.findall(r'"([^"]+)"', re.search(r"steps: \[([^\]]+)\]", STRINGS_JS).group(1))
    text = FLAT[FLAT.index("### La Mise en route"):FLAT.index("### Au quotidien")]
    at = [text.find(f"**{step}**") for step in steps]
    assert -1 not in at, dict(zip(steps, at, strict=True))
    assert at == sorted(at)


def test_tray_menu_items():
    menu = FLAT[FLAT.index("**L'icône JARVIS**"):FLAT.index("Pour quitter")]
    for label in re.findall(r'item\("([^"]+)"', SHELL_SRC):
        assert label in menu, label


def test_about_points_at_a_real_section():
    name = re.search(r"suivez le README, section « ([^»]+) »", STRINGS_JS).group(1)
    assert re.search(rf"^## \S+ {re.escape(name)}\r?$", README, re.MULTILINE), name


def test_voice_prices_are_the_ledgers():
    """0,02 $ a minute of monsieur's voice and 0,08 $ of JARVIS's, from usage.PRICES."""
    full, mini = usage.PRICES[default("REALTIME_MODEL")], usage.PRICES["gpt-realtime-2.1-mini"]
    per_minute_in = usage.USER_AUDIO_TOKENS_PER_SECOND * 60 * full["audio_in"] / 1e6
    per_minute_out = 20 * 60 * full["audio_out"] / 1e6  # 1 token per 50 ms of JARVIS's voice
    costs = section("## 💰 Coûts et plafonds")
    for amount in (per_minute_in, per_minute_out):
        assert f"{amount:.2f} $".replace(".", ",") in costs, amount
    assert round(full["audio_in"] / mini["audio_in"]) == 3 and "trois fois moins cher" in costs
    assert usage.WARN_RATIO == 0.8 and "80 %" in costs


def test_numbers_in_the_prose_are_the_codes():
    prose = FLAT  # a phrase may wrap anywhere in the file
    assert default("JARVIS_IDLE_MINUTES") == "3" and "après 3 minutes" in prose
    assert default("JARVIS_QUIET_HOURS") == "22:30-07:30" and "22 h 30 à 7 h 30" in prose
    assert default("JARVIS_BRIEFING_TIME") == "08:00" and "8 h par défaut" in prose
    assert default("JARVIS_BRIEFING_DAYS") == "lun-ven" and "du lundi au vendredi par défaut" in prose
    assert default("JARVIS_JOURNAL_DAYS") == "30" and "30 jours" in prose
    assert default("JARVIS_TASK_BUDGET_USD") == "2.0" and "2 $ par défaut" in prose
    assert default("JARVIS_TASK_TIMEOUT") == "600" and "10 min" in prose
    assert default("JARVIS_MAX_CONCURRENT_TASKS") == "3" and "trois tâches à la fois" in prose
    assert "GUARD_MS = 8000" in WAKE_JS and "8 secondes" in prose
    assert briefing.NEWS_COUNT == 3 and "trois titres" in prose
    assert briefing.WINDOW.total_seconds() == 4 * 3600 and "quatre heures" in prose
    assert scheduler.MISSED_ROUTINE_S == 2 * 3600 and "plus de deux heures" in prose


def test_wave3_abilities_are_documented():
    for needle in ("+10 min", "Demain", "reporte-le", "Briefing du matin", "Réglages › Coûts",
                   "Plafond du jour", "estimation", "JARVIS a remarqué"):
        assert needle in FLAT, needle


def test_defaults_in_the_table_are_the_real_ones():
    """Each default in the README's table is the one config.py falls back on."""
    rows = dict(re.findall(r"^\| `(\w+)` \| `([^`]*)` \|", README, re.MULTILINE))
    assert len(rows) >= 40
    assert rows.pop("JARVIS_DATA_DIR") == "data" and 'str(ROOT / "data")' in CONFIG_SRC
    for name, shown in rows.items():
        assert shown == default(name), (name, shown, default(name))


def test_every_setting_of_config_is_in_the_table():
    documented = set(re.findall(r"^\| `(\w+)` \|", README, re.MULTILINE))
    names = set(re.findall(r'"((?:JARVIS|REALTIME|OPENAI)_[A-Z_]+)"', CONFIG_SRC))
    assert names <= documented, sorted(names - documented)


# ---------------------------------------------------------------- JARVIS sur l'iPhone (C1)


def js_block(name: str) -> str:
    """One top-level block of strings-fr.js (T.<name>), as written."""
    start = STRINGS_JS.index(f"\n  {name}: {{\n")
    return STRINGS_JS[start:STRINGS_JS.index("\n  },", start)]


def js_text(block: str, key: str) -> str:
    """The string a key of that block holds (the first one of that name)."""
    found = re.search(rf'\b{key}: "([^"]*)"', js_block(block))
    assert found, (block, key)
    return found.group(1)


def guide() -> str:
    """The iPhone guide as one line, for its prose."""
    return section(IPHONE)


def test_the_iphone_guide_replaces_the_placeholder():
    assert IPHONE in README and "guide à venir" not in README
    assert "JARVIS sur l'iPhone" in FLAT[FLAT.index("**Dans ce guide**"):FLAT.index("## 💰 Coûts et plafonds")]
    # Placed after the reminders and before A.R.E.S, as the table of contents says.
    assert README.index("## ⏰ Rappels") < README.index(IPHONE) < README.index("## 🤝 Coexistence avec A.R.E.S")
    steps = re.findall(r"^### (\d+)\. ", raw_section(IPHONE), re.MULTILINE)
    assert steps == [str(n) for n in range(1, 13)], steps
    for needle in ("Pas de mot d'éveil", "L'écoute s'arrête", "iOS redemande le micro",
                   "Le PC doit rester allumé et éveillé", "iOS 26 ou 27", "30 à 45 minutes"):
        assert needle in guide(), needle
    # Only the fictitious examples of the spec: the repository is public.
    for example in (REMOTE_HOST, LOGIN, IP, "tail0000.ts.net", "iPhone de test"):
        assert example in guide(), example
    assert not re.search(r"\b[\w-]+\.tail(?!0000)[0-9a-f]+\.ts\.net\b", README)
    assert not re.findall(r"\b100\.(?!101\.102\.103\b)\d+\.\d+\.\d+\b", README)


def test_both_serve_commands_are_the_ones_jarvis_runs():
    """The two commands the README gives are tailscale.manual_command(False/True),
    the exact command « Publier sur Tailscale » runs, on the default Serve port."""
    short, full = tailscale.manual_command(False), tailscale.manual_command(True)
    args = " ".join(tailscale._serve_args())
    assert short == f"tailscale {args}"
    assert full == f'& "{tailscale.POWERSHELL_EXE}" {args}' and tailscale.POWERSHELL_EXE == tailscale.WINDOWS_PATHS[0]
    assert "_popen(exe, _serve_args())" in TAILSCALE_SRC  # what the button runs
    assert f"http://127.0.0.1:{default('JARVIS_REMOTE_PORT')}" in short
    blocks = [b.strip() for b in re.findall(r"^ *```\w*\r?\n(.*?)\r?\n *```", README, re.MULTILINE | re.DOTALL)]
    assert short in blocks and full in blocks
    # Every Tailscale command the README shows is one of those two (never Funnel,
    # a TCP forward, a reset); the bare names appear only in « Règle zéro ».
    assert {b for b in blocks if "tailscale" in b.lower()} == {short, full}
    spans = set(re.findall(r"`([^`\r\n]*tailscale [^`\r\n]*)`", README))
    assert spans <= {short, full, "tailscale serve", "tailscale funnel"}, spans
    rule = guide()[guide().index("**Règle zéro**"):guide().index("**Ce qu'il faut**")]
    for word in ("`tailscale serve`", "**jamais** `tailscale funnel`", "`--tcp`", "`--tls-terminated-tcp`",
                 "redirection de port", "ngrok", "Cloudflare"):
        assert word in rule, word
    assert FLAT.count("`tailscale funnel`") == 1
    assert "PowerShell (Win+X › Terminal)" in guide() and "Win+X › Terminal" in js_text("remote", "manualHelp")


def test_the_serve_port_default_is_the_configs():
    port = default("JARVIS_REMOTE_PORT")
    assert port == "8789" and port != default("JARVIS_PORT")
    assert f"le port {port} (`JARVIS_REMOTE_PORT`)" in guide()
    assert f"seconde porte du PC (le port {port})" in section("## 🔒 Sécurité")
    said = f"Port {port} déjà utilisé : choisissez un autre JARVIS_REMOTE_PORT."
    assert 'f"Port {port} déjà utilisé : choisissez un autre JARVIS_REMOTE_PORT."' in LISTENER_SRC
    assert f"« {said} »" in guide()
    assert "`JARVIS_REMOTE_PORT=8790`" in guide()  # another port, as the troubleshooting says


def test_the_serve_states_are_the_settings_words():
    states = {key: js_text("remote", key) for key in ("ready", "absent", "funnel", "tcp", "wrong_target")}
    assert states == {"ready": "prêt", "absent": "absent", "funnel": "Funnel actif", "tcp": "relais TCP",
                      "wrong_target": "cible inattendue"}
    assert set(tailscale.DANGEROUS) == {"funnel", "tcp", "wrong_target"}
    table = raw_section(IPHONE)
    for word in states.values():
        assert f"| « {word} » |" in table, word
    assert "« **Serve : prêt** »" in guide() and "serveLine: (word) => `Serve : ${word}`" in STRINGS_JS
    assert tailscale.WATCH_EVERY == 600 and "toutes les 10 minutes" in guide()
    assert health.REMOTE_FIX == "Réglages › Accès à distance › Publier sur Tailscale."
    assert health.REMOTE_FIX.rstrip(".") in guide()
    assert js_text("remote", "consentLink") == "Autoriser HTTPS sur Tailscale" and "**Autoriser HTTPS sur Tailscale**" in guide()
    assert js_text("remote", "manual") == "Commande manuelle" and "« Commande manuelle »" in guide()


def test_every_label_of_the_iphone_screens_is_quoted_as_the_code_says():
    """Spec 3.16b: the README names each control with the interface's own words."""
    labels = {  # (strings-fr.js block, key) -> label
        ("remote", "switchLabel"): "Accès à distance",
        ("remote", "publish"): "Publier sur Tailscale",
        ("remote", "pair"): "Associer un iPhone",
        ("remote", "allow"): "Autoriser",
        ("remote", "deny"): "Refuser",
        ("remote", "revoke"): "Retirer",
        ("remote", "completTitle"): "Accès complet depuis l'iPhone",
        ("remote", "never"): "Jamais",
        ("remote", "day"): "24 h",
        ("remote", "week"): "7 jours",
        ("remote", "auditTitle"): "Activité récente",
        ("remote", "pause"): "Mettre en pause",
        ("remote", "forget"): "Oublier cet iPhone",
        ("remote", "copy"): "Copier",
        ("pair", "ask"): "Demander l'accès",
        ("notify", "copyTopic"): "Copier le sujet",
        ("notify", "test"): "Envoyer un test",
        ("notify", "newTopic"): "Nouveau sujet",
        ("siri", "create"): "Créer une clé Siri",
        ("siri", "assistant"): "Assistant raccourci",
        ("siri", "copy"): "Copier",
        ("siri", "revoke"): "Révoquer",
    }
    for (block, key), label in labels.items():
        assert js_text(block, key) == label, (block, key)
        assert label in guide(), label
    buttons = set(labels.values()) - {"Accès à distance", "Accès complet depuis l'iPhone", "Activité récente"}
    for label in buttons:  # a button the guide makes you press is in bold
        assert f"**{label}**" in guide(), label
    # The tray item and the Réglages section titles.
    assert 'item("Accès à distance (activer ou couper)"' in SHELL_SRC
    assert "« Accès à distance (activer ou couper) »" in guide()
    titles = dict(settings.SECTIONS)
    assert titles["distance"] == "Accès à distance" and titles["notifications"] == "Notifications"
    assert "Réglages › Accès à distance" in guide() and "Réglages › Notifications" in guide()
    assert "Réglages › Accès à distance › **Associer un iPhone**" in guide()
    assert "Réglages › Accès à distance › **Activité récente**" in guide()
    assert "Réglages › Notifications › **Copier le sujet**" in guide()
    # The personal-account line of the PC view.
    assert f"« {js_text('remote', 'personal')} »" in guide()
    assert 'env: "(défini dans .env)"' in js_block("remote")
    assert "« (défini dans .env) »" in guide()


def test_the_home_screen_steps_are_the_pairing_pages():
    steps = re.findall(r'"([^"]+)"', re.search(r"installSteps: \[([^\]]+)\]", js_block("pair")).group(1))
    for words in ("⋯", "Partager", "Sur l'écran d'accueil", "« Ouvrir comme app web »", "Ajouter"):
        assert any(words in step for step in steps), words
        assert words in guide(), words
    order = [guide().index(w) for w in ("**⋯**", "**Partager**", "**Sur l'écran d'accueil**",
                                        "**« Ouvrir comme app web »**", "**Ajouter**")]
    assert order == sorted(order)
    assert js_text("pair", "installTitle") == "D'abord, ajoutez JARVIS à l'écran d'accueil"
    assert "« D'abord, ajoutez JARVIS à l'écran d'accueil »" in guide()
    assert js_text("pair", "useSafari") == "Utiliser JARVIS dans Safari" and "« Utiliser JARVIS dans Safari »" in guide()
    assert js_text("pair", "approved") == "Associé !" and "« Associé ! »" in guide()
    for key in ("retry", "restart"):
        assert f"**{js_text('pair', key)}**" in guide(), key


def test_pairing_shows_a_four_digit_code_typed_on_the_pc_when_several_wait(monkeypatch):
    enable_remote(monkeypatch)
    until = remote.open_pairing()
    assert remote.PAIR_MINUTES == 10 and round((until - time.time()) / 60) == 10
    assert "L'association reste ouverte 10 minutes" in guide()
    first, _ = remote.request_pairing(remote.Caller(kind="unpaired", ip=IP, login=LOGIN), "iPhone de test")
    assert re.fullmatch(r"\d{4}", first["code"])
    assert "**code à 4 chiffres**" in guide() and "le code à 4 chiffres" in section("## 🔒 Sécurité")
    second, _ = remote.request_pairing(remote.Caller(kind="unpaired", ip="100.101.102.104", login=LOGIN), "iPad")
    assert re.fullmatch(r"\d{4}", second["code"]) and second["code"] != first["code"]
    with pytest.raises(remote.RemoteError) as refused:  # two wait: the PC types the code
        remote.allow_pairing(first["request_id"])
    assert str(refused.value) == remote.T_CODE
    assert remote.allow_pairing(first["request_id"], code=first["code"])["status"] == "approved"
    assert "Si plusieurs demandes attendent, JARVIS vous fait **taper le code**" in guide()
    assert js_text("remote", "typeCode") == "Code affiché sur l'iPhone (4 chiffres)"
    assert devices.MAX_DEVICES == 5 and "5 appareils au plus" in guide()


def test_the_iphone_pauses_for_1_h_or_24_h_and_only_the_pc_resumes(monkeypatch):
    enable_remote(monkeypatch)
    phone = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip=IP, login=LOGIN)
    for hours in (0, 2, 12, 48):
        with pytest.raises(remote.RemoteError):
            remote.pause(hours, by=phone)
    for hours in (1, 24):
        until = remote.pause(hours, by=phone)
        assert until == pytest.approx(time.time() + hours * 3600, abs=5)
        assert remote.paused_until() == until  # one pause for all of remote access, Siri included
    assert remote.pause(0, by=remote.PC) == 0  # the PC resumes it before the end
    assert (js_text("remote", "pause1"), js_text("remote", "pause24")) == ("1 h", "24 h")
    assert ("**Mettre en pause** (sur l'iPhone, Réglages › Accès à distance) : coupe tout l'accès à distance "
            "(vos iPhone et Siri) **1 h** ou **24 h**") in guide()
    assert js_text("remote", "resume") == "Reprendre maintenant" and "« Reprendre maintenant »" in guide()
    assert "on peut seulement le mettre en pause, 1 h ou 24 h" in section("## 🔒 Sécurité")


def test_full_access_from_the_iphone_is_jamais_24_h_or_7_jours(monkeypatch):
    enable_remote(monkeypatch)
    spans = {"never": 0, "24h": 86400, "7d": 7 * 86400}
    for duration, span in spans.items():
        until = remote.set_complet(duration)
        assert until == (pytest.approx(time.time() + span, abs=5) if span else 0), duration
    with pytest.raises(remote.RemoteError):
        remote.set_complet("30d")
    assert 'COMPLET = [["never", "never"], ["24h", "day"], ["7d", "week"]]' in REMOTE_SETTINGS_JS
    assert "**Accès complet depuis l'iPhone** : **Jamais** (par défaut), **24 h** ou **7 jours**" in guide()
    text = guide()
    for needle in ("bouton **Lancer**, **sur l'iPhone qui l'a demandée**", "Un « oui » à la voix ne la lance pas",
                   "le PC peut l'annuler, pas la lancer", "Les **routines** avec accès complet se programment sur "
                   "le PC seulement", "« Confirmation requise »"):
        assert needle in text, needle
    from jarvis import confirm
    assert confirm.T.complet_routine_pc == "Une routine avec accès complet se programme sur le PC."
    assert "données externes" in confirm.T.complet_tainted
    assert audit.ALERTS["remote_complet"]["ntfy"] and audit.ALERTS["remote_complet"]["dedupe_s"] == 0


def test_what_the_iphone_may_do_on_the_pc_is_the_allowlist():
    assert tools.REMOTE_PC_ACTIONS == {"volume_up", "volume_down", "set_volume", "mute", "play_pause",
                                       "next_track", "previous_track", "lock_screen"}
    assert not {"open_app", "look_at_screen"} & tools.APP_TOOLS
    assert not {"read_clipboard", "write_clipboard", "save_screenshot"} & tools.REMOTE_PC_ACTIONS
    text = guide()
    assert ("monter ou baisser le son, le couper, lecture ou pause, piste suivante ou précédente, verrouiller "
            "la session") in text
    assert ("Presse-papiers, captures d'écran, regard sur l'écran et ouverture d'applications restent réservés "
            "au PC") in text
    from jarvis import confirm
    assert confirm.T.pc_action.split(" : ")[0] == "Agir sur le PC à distance" and "« Agir sur le PC à distance : … ? »" in text
    assert js_text("confirm", "linkTitle") == "Lien à ouvrir" and js_text("confirm", "openLink") == "Ouvrir le lien"
    assert "« Lien à ouvrir »" in text and "**Ouvrir le lien**" in text
    # Réglages on the phone: its sections and the few settings it may change.
    titles = dict(settings.SECTIONS)
    names = [titles[s] for s in remote.REMOTE_SECTIONS]
    assert f"seulement {', '.join(names[:-1])} et {names[-1]}" in text
    assert remote.REMOTE_SETTINGS == {"voice", "voice_speed", "briefing_time", "briefing_days", "briefing_news",
                                      "quiet_hours", "city"}
    assert "vous changez la voix, sa vitesse, le briefing du matin, les heures calmes et votre ville" in text
    assert 'wake_word": False if caller.remote' in SERVER_SRC and "Pas de mot d'éveil" in text


def test_the_iphone_screen_words_are_the_interfaces():
    text = guide()
    for key, quoted in (("paused", True), ("resume", False), ("composerPlaceholder", True)):
        label = js_text("ios", key)
        assert (f"« {label} »" if quoted else f"**{label}**") in text, key
    assert js_text("delivery", "pcClosed") == "JARVIS est fermé sur le PC" and "« JARVIS est fermé sur le PC »" in text
    assert js_text("ios", "micBlocked").startswith("Micro refusé") and "**« Micro refusé » sur l'iPhone**" in text
    # The keyboard table stays the PC's; the iPhone's touch help is named after it.
    assert js_text("ios", "gestures") == "Commandes tactiles"
    assert "« Commandes tactiles »" in section("## ⌨️ Raccourcis")


def test_ntfy_messages_and_security_alerts_are_the_fixed_texts():
    text = guide()
    raw = raw_section(IPHONE)
    arrives = raw[raw.index("**Ce qui arrive**"):raw.index("**Les alertes de sécurité**")]
    for message in (notify.TASK_DONE, notify.TASK_FAILED, notify.REMINDER, notify.PENDING,
                    notify.SIRI_READY, notify.TASKS_DONE.format(n=2)):
        assert f"« {message} »" in arrives, message
    assert f"« {notify.TEST} »" in text  # what « Envoyer un test » sends
    assert notify.ALERT == "JARVIS · sécurité : {text}" and "« JARVIS · sécurité : … »" in text
    sent = {kind: a["ntfy_text"] for kind, a in audit.ALERTS.items() if a["ntfy"]}
    assert len(sent) == 12, sorted(sent)  # every alert that reaches the iPhone has its row
    table = raw_section(IPHONE)
    for kind, alert in sent.items():
        assert re.search(rf"^\| (?:.* )?« {re.escape(alert)} »", table, re.MULTILINE), kind
    for kind in ("pair_request", "siri_key", "remote_paused", "listener_error"):  # the PC's toast only
        assert not audit.ALERTS[kind]["ntfy"], kind
    assert "Click" not in NOTIFY_SRC and "**JARVIS n'envoie jamais de lien**" in text
    labels = {s.key: s.label for s in settings.SCHEMA if s.section == "notifications"}
    assert labels == {"ntfy": "Notifications sur l'iPhone (ntfy)", "ntfy_server": "Serveur ntfy",
                      "ntfy_only_away": "Seulement si je ne suis pas au PC",
                      "ntfy_reminder_text": "Texte des rappels dans la notification"}
    for label in labels.values():
        assert f"« {label} »" in text, label
    assert default("JARVIS_NTFY_SERVER") == "https://ntfy.sh" and "serveur ntfy.sh, celui par défaut" in text
    assert "« S'abonner »" in js_text("notify", "step4") and "**S'abonner**" in text
    assert "Ce qui vient de l'iPhone ou de Siri, les rappels et les alertes partent toujours" in text
    away = next(s.help for s in settings.SCHEMA if s.key == "ntfy_only_away")
    assert "Ce qui vient de l'iPhone ou de Siri, les rappels et les alertes de sécurité partent toujours" in away
    assert notify.REMINDER_TEXT_MAX == 60


def test_the_siri_recipe_is_the_assistants(monkeypatch):
    """The URL, the header and the body field the « Assistant raccourci » hands
    over, the reply the Shortcut speaks, and the 3-action recipe, as the app says."""
    assert "POST" in served_routes()["/api/raccourci"]
    assert (frozenset({"POST"}), "/api/raccourci", frozenset({"siri"})) in [
        (frozenset(m), t, frozenset(s)) for m, t, s in remote.REMOTE_ALLOW]
    assert remote._SIRI_HEADER.pattern.startswith(r"^Bearer jv_siri_")
    device, _ = devices.add("iPhone de test", ip=IP, login=LOGIN, os="iOS", host_name="iphone-de-test", ips=(IP,))
    monkeypatch.setattr(remote, "host", lambda: REMOTE_HOST)
    raccourci.create_key(device["id"], remote.PC)
    handed = raccourci.collect_key(remote.Caller(kind="app", device_id=device["id"], ip=IP, login=LOGIN))
    text = guide()
    assert handed["url"] == f"https://{REMOTE_HOST}/api/raccourci" and f"`{handed['url']}`" in text
    assert handed["header"] == "Authorization" and "Nom de l'en-tête : `Authorization`" in text
    assert handed["value"].startswith("Bearer jv_siri_") and "`Bearer jv_siri_…`" in text
    assert '["field", S.rowField, "text"]' in SIRI_UI_JS and "Champ du corps JSON : `text`" in text
    for key, row in (("rowUrl", "Adresse (URL)"), ("rowHeader", "Nom de l'en-tête"),
                     ("rowValue", "Valeur de l'en-tête"), ("rowField", "Champ du corps JSON")):
        assert js_text("siri", key) == row and f"- {row} : `" in text, key
    # The body field is "text"; the answer is plain text unless JSON is asked for.
    siri = remote.Caller(kind="siri", device_id=device["id"], key_id="k_0123456789abcdef", ip=IP, login=LOGIN)
    assert raccourci.answer(siri, b'{"texte": "bonjour"}')[:2] == (400, raccourci.T.bad_request)
    assert "return PlainTextResponse(speech, status_code=status)" in RACCOURCI_SRC
    assert "JARVIS répond en texte simple" in text
    # The recipe, in the assistant's words and order.
    recipe = js_block("siri")
    for words in ("Décrire un raccourci", "Jarvis", "Dicter le texte", "Français", "Obtenir le contenu de l'URL",
                  "En afficher plus", "POST", "Authorization", "JSON", "Texte dicté", "Énoncer le texte",
                  "Toujours autoriser", "Dis Siri, Jarvis"):
        assert words in recipe and words in text, words
    order = [text.index(w) for w in ("**Dicter le texte**", "**Obtenir le contenu de l'URL**", "**Énoncer le texte**")]
    assert order == sorted(order)
    assert "« En afficher plus » › Méthode : **POST**" in text and "**Toujours autoriser**" in text


def test_what_siri_can_do_and_its_limits_are_the_codes():
    text = guide()
    assert sorted(raccourci.WRAPPERS) == ["annuler_tache", "mes_taches", "rappel", "recherche"]
    assert "Siri n'a que quatre outils" in section("## 🔒 Sécurité")
    assert "« Pour cela, ouvrez JARVIS sur l'iPhone. »" in raccourci.INSTRUCTIONS
    assert tools.T.siri_elsewhere == "Pour cela, ouvrez JARVIS sur l'iPhone." and "« Pour cela, ouvrez JARVIS sur l'iPhone. »" in text
    assert raccourci.T.later == "Je m'en occupe, je vous préviens sur l'iPhone." and f"« {raccourci.T.later} »" in text
    assert raccourci.T.no_ntfy.startswith("Rappel non créé") and "« Rappel non créé… »" in text
    assert raccourci.DEADLINE_S == 7.5 and "plus de 7,5 secondes" in text
    assert raccourci.CONVERSATION_TTL == 300 and "Siri garde le fil de la conversation 5 minutes" in text
    for closing in ("Merci", "C'est tout, merci"):
        assert raccourci._CLOSING.fullmatch(closing), closing
    assert "« Merci » ou « C'est tout, merci » la termine" in text
    assert '(("siri-min", caller.key_id), 6, 60), (("siri-day", caller.key_id), 60, 86400)' in REMOTE_SRC
    assert raccourci.MAX_RUNNING_RESEARCH == 2 and raccourci.MAX_RESEARCH_PER_DAY == 10
    assert "6 questions par minute et 60 par jour, 2 recherches à la fois et 10 par jour" in text
    assert tasks.SIRI_TASK_BUDGET_USD == 0.5 and "0,50 $ au plus" in text
    assert raccourci.HANDOFF_S == 600 and "La clé attend l'iPhone 10 minutes" in text
    assert devices.MAX_SIRI_KEYS == 2 and "deux clés au plus par iPhone" in text
    assert "const MAX_SIRI_KEYS = 2;" in REMOTE_SETTINGS_JS
    assert remote.T_SIRI_UNKNOWN == "Clé Siri inconnue : recréez-la sur le PC." and f"« {remote.T_SIRI_UNKNOWN} »" in text
    assert "iPhone 15 Pro et plus récents" in text and "Toucher le dos › Toucher deux fois" in text


def test_the_troubleshooting_messages_are_the_codes():
    text = guide()
    for message in (remote.T_PROXY, remote.T_LOGIN, remote.T_MOVED, remote.T_LOCKED, remote.T_CAPPED,
                    remote.T_MINTS):
        assert f"« {message} »" in text, message
    assert remote.T_NO_CAP.startswith("Fixez d'abord un plafond de dépense par jour")
    assert "« Fixez d'abord un plafond de dépense par jour… »" in text
    titles = js_block("pair")
    for title in ("Appareil retiré", "Accès refusé"):
        assert f'"{title}"' in titles and f"« {title} »" in text, title
    assert remote.FAIL_MAX == 5 and remote.FAIL_WINDOW_S == 600 and remote.LOCK_S == 900
    assert "Cinq échecs en dix minutes bloquent" in text and "15 minutes" in text
    assert "Cinq échecs en dix minutes bloquent la source 15 minutes" in section("## 🔒 Sécurité")
    assert remote.MINTS_PER_WINDOW == 12 and remote.MINT_WINDOW_S == 600 and remote.MINTS_PER_DAY == 40
    assert "12 conversations par 10 minutes et 40 par jour" in text
    assert "12 connexions par 10 minutes et 40 par jour" in section("## 🔒 Sécurité")
    assert f"« {remote.T_PROXY} »" in section("## ❓ Problèmes fréquents")
    assert "Apps › Safari › Micro" in text  # where iOS 18 and later keep Safari's microphone setting


def test_security_and_data_flows_tell_the_remote_model():
    security = section("## 🔒 Sécurité")
    for needle in ("### L'accès depuis l'iPhone", "Coupé par défaut", "Funnel", "« Activité récente »",
                   "bouton **Lancer** sur l'iPhone", "jamais pour une routine", "Un plafond obligatoire",
                   "aucune ligne du fichier `.env` ne l'allume"):
        assert needle in security, needle
    # Nothing in .env switches remote access on: it lives in data/remote.json.
    assert not re.search(r'"JARVIS_REMOTE_(?:ENABLED|ON)"', CONFIG_SRC) and remote.REMOTE_FILE == "remote.json"
    assert not remote._DEFAULTS["enabled"]
    flows = section("## 🔁 Ce qui part où")
    for needle in ("Tailscale", "ntfy.sh", "store=false", "Apple", "journaux des certificats HTTPS"):
        assert needle in flows, needle
    assert '"store": False' in RACCOURCI_SRC


def test_the_settings_column_of_the_variables_table_is_the_schemas():
    """« Dans Réglages » names the section a variable is set in (the ntfy ones
    included), and « — » those Réglages does not have."""
    titles = dict(settings.SECTIONS)
    by_attr = {s.attr: titles[s.section] for s in settings.SCHEMA}
    rows = re.findall(r"^\| `(\w+)` \| [^|]+ \| ([^|]+?) \|", README, re.MULTILINE)
    assert len(rows) >= 40
    for var, shown in rows:
        found = re.search(rf'^(\w+) = [^\n]*"{var}"', CONFIG_SRC, re.MULTILINE)
        model = re.search(rf'"(\w+)": os\.environ\.get\("{var}"', CONFIG_SRC)
        attr = found.group(1) if found else f"MODELS.{model.group(1)}" if model else ""
        expected = "Connexion" if var == "OPENAI_API_KEY" else by_attr.get(attr, "—")
        assert shown == expected, (var, shown, expected)
