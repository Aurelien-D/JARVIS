"""The README (WP08) says what a non-developer needs: living with A.R.E.S, the
security model as it is, OpenAI's deadlines, how to update, the shortcuts,
and the wave-3 abilities (snoozed reminders, local briefing, costs and cap)."""
import re
from pathlib import Path

from jarvis import config, health

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


def test_acceptance_sections_and_facts():
    for needle in ("Coexistence avec A.R.E.S", "20 janvier 2027", "26 février 2027", "install.ps1",
                   "Ctrl+Alt+Maj+J"):
        assert needle in README, needle


def test_claude_code_is_installed_with_the_native_installer():
    assert "npm install -g @anthropic-ai/claude-code" not in README
    assert "irm https://claude.ai/install.ps1 | iex" in README and "claude update" in README


def test_deadlines_match_the_health_check():
    assert health.REALTIME_END.isoformat() == "2027-01-20" and health.TRANSCRIBE_END.isoformat() == "2027-02-26"
    assert "gpt-realtime-2.1-mini" in README


def test_security_section_tells_the_truth():
    security = README[README.index("## 🔒 Sécurité"):README.index("## 🔁 Ce qui part où")]
    for needle in ("liste blanche", "Accès complet", "confirmation", "auto", "~/JARVIS-travail",
                   "pas une barrière", "script"):
        assert needle in security, needle
    assert "resterait bloquée" not in README  # a headless prompt is denied, it never hangs
    assert "bypassPermissions` (aucune demande)" not in README


def test_coexistence_update_and_data_flows():
    ares = README[README.index("## 🤝 Coexistence avec A.R.E.S"):]
    assert "Hey Jarvis" in ares and "Vosk" in ares and "Arès" in ares and "6178" in ares
    assert "## 🔄 Mettre à jour" in README and "pip install -r requirements.txt" in README
    assert "## 🔁 Ce qui part où" in README and "## ⌨️ Raccourcis" in README


def test_wave3_abilities_are_documented():
    for needle in ("+10 min", "Demain", "reporte-le", "Briefing du matin", "Réglages › Coûts",
                   "Plafond du jour", "estimation", "JARVIS a remarqué"):
        assert needle in README, needle


def test_defaults_in_the_table_are_the_real_ones():
    """Each default in the README's table is the one config.py falls back on
    (read from its source: a local .env must not change what is checked)."""
    source = (Path(config.__file__)).read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `(\w+)` \| `([^`]*)` \|", README, re.M))
    assert len(rows) >= 15
    assert rows.pop("JARVIS_DATA_DIR") == "data" and 'str(ROOT / "data")' in source
    for name, shown in rows.items():
        found = re.search(rf'"{name}", "?([^")]*)"?\)', source)
        assert found, name
        default = {"True": "1", "False": "0"}.get(found.group(1), found.group(1))
        assert shown == default, (name, shown, default)
