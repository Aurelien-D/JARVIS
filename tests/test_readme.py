"""The README (WP08) is the guide a non-developer follows: every command,
setting name, default, shortcut and date it gives must be the code's own.
These tests read the code's sources (a local .env must not change what is
checked) and fail as soon as the README and the code disagree."""
import re
from pathlib import Path

from jarvis import briefing, config, health, scheduler, settings, shell, usage

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
    for name in re.findall(r"(?<!A\.R\.E\.S › )Réglages › (Claude Code|À propos|[A-ZÉ][\wéèû]+)", FLAT):
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
    assert -1 not in at, dict(zip(steps, at))
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
