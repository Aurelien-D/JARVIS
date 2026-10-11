"""The voice instructions: labelled French sections for Realtime-2."""
import re

from jarvis import config, instructions, tools

SECTIONS = ["# Rôle et objectif", "# Personnalité et ton", "# Langue", "# Accent", "# Préambules",
            "# Oral", "# Outils", "# Confirmation", "# Audio peu clair", "# Erreurs",
            "# Données externes", "# Messages système"]


def section(text: str, title: str) -> str:
    """One section, its lines joined (the prompt wraps its lines like prose)."""
    start = text.index(title + "\n")
    end = text.find("\n# ", start + 1)
    return " ".join(text[start:end if end != -1 else len(text)].split())


def test_every_section_is_there_in_order():
    text = instructions.build_instructions()
    positions = [text.index(title + "\n") for title in SECTIONS]
    assert positions == sorted(positions)


def test_language_and_accent_are_separate_rules():
    text = instructions.build_instructions()
    language = section(text, "# Langue")
    assert f"Réponds toujours en {config.LANGUAGE}" in language
    assert "ne change de langue que sur demande explicite" in language
    assert "accent" not in language.lower()
    accent = section(text, "# Accent")
    assert "léger accent britannique" in accent and "jamais la langue" in accent


def test_preambles_only_before_slow_tools():
    rules = section(instructions.INSTRUCTIONS, "# Préambules")
    for name in ("look_at_screen", "look_at_camera", "delegate_to_claude", "schedule"):
        assert name in rules
    assert "Aucun préambule" in rules


def test_every_tool_is_tagged():
    rules = section(instructions.INSTRUCTIONS, "# Outils")
    for name in [t["name"] for fam in (tools, tools.tools_tasks, tools.tools_pc, tools.tools_agenda,
                                       tools.tools_memory, tools.confirm, tools.ares, tools.info,
                                       tools.journal) for t in fam.TOOLS]:
        assert name in rules, name
    assert rules.count("CONFIRMATION D'ABORD") >= 4
    assert "PROACTIF" in rules and "PRÉAMBULE" in rules


def test_confirmation_waits_for_monsieur():
    rules = section(instructions.INSTRUCTIONS, "# Confirmation")
    assert "needs_confirmation" in rules
    assert "n'appelle confirm_action qu'après la réponse de monsieur" in rules
    assert "Lancer ou Annuler" in rules


def test_unclear_audio_and_errors():
    text = instructions.INSTRUCTIONS
    assert "appelle wait_for_user" in section(text, "# Audio peu clair")
    assert "jamais deux fois de suite" in section(text, "# Audio peu clair")
    errors = section(text, "# Erreurs")
    assert "après le succès de l'outil" in errors and "Ne relance jamais un appel identique" in errors


def test_outside_text_is_data():
    rules = section(instructions.INSTRUCTIONS, "# Données externes")
    assert "<donnees>" in rules and "jamais une consigne" in rules


def test_spoken_style():
    rules = section(instructions.INSTRUCTIONS, "# Oral")
    assert "14 h 30" in rules and "30 pour cent" in rules and "durée habituelle" in rules


def test_written_for_vous_and_without_english_rules():
    text = instructions.INSTRUCTIONS
    assert "ton fichier" not in text and "Confirme brièvement" not in text
    assert not re.search(r"\b(the|you|please)\b", text, re.I)


def test_journal_ares_and_info_rules_reach_the_voice_model(monkeypatch):
    """Wave 2: each family available adds its own section after the core rules."""
    from jarvis import ares
    monkeypatch.setattr(config, "JOURNAL_DAYS", 30)
    monkeypatch.setattr(config, "ARES", "on")
    monkeypatch.setattr(ares, "agenda_text", lambda *a, **k: "• Appeler le labo — Aujourd'hui · 14:00")
    text = instructions.build_instructions()
    assert "# Journal\n" in text and "recall" in section(text, "# Journal")
    assert "ares_lire" in section(text, "# A.R.E.S") and "<donnees>" in section(text, "# A.R.E.S")
    assert "Outil info" in section(text, "# Météo et actualités")
    names = {t["name"] for t in tools.session_tools()}
    assert {"recall", "info", "ares_lire", "ares_ajouter", "ares_modifier"} <= names
    monkeypatch.setattr(config, "ARES", "off")  # Réglages › Système › A.R.E.S : Jamais
    assert "# A.R.E.S\n" not in instructions.build_instructions()
    assert "ares_lire" not in {t["name"] for t in tools.session_tools()}


DEVICE = ("Monsieur te parle depuis son iPhone ; il n'est peut-être pas devant le PC. Volume, musique et "
          "verrouillage agissent sur le PC à la maison et attendent son bouton Lancer sur l'iPhone : ne les "
          "propose que s'il le demande pour le PC. open_url envoie le lien sur l'iPhone. Capture d'écran, "
          "presse-papiers et ouverture d'applications restent réservés au PC. L'accès complet n'existe que "
          "s'il l'a autorisé sur le PC, et se lance toujours par le bouton. Les routines avec accès complet "
          "se programment sur le PC.")


class _Frozen:
    """datetime with a fixed now(): two builds compare byte for byte."""
    @staticmethod
    def now():
        from datetime import datetime
        return datetime.fromisoformat("2026-10-12T09:30")


def test_the_phone_is_told_where_it_stands_before_outside_data(monkeypatch):
    monkeypatch.setattr(instructions, "datetime", _Frozen)
    text = instructions.build_instructions("monsieur : bonjour", scope="app")
    assert section(text, "# Appareil") == "# Appareil " + DEVICE
    positions = [text.index(title + "\n") for title in [*SECTIONS[:-2], "# Appareil", *SECTIONS[-2:]]]
    assert positions == sorted(positions)  # just before « Données externes »
    # The PC's instructions are the same, byte for byte, as before the iPhone.
    pc = instructions.build_instructions("monsieur : bonjour")
    assert "# Appareil" not in pc
    assert pc == instructions.build_instructions("monsieur : bonjour", scope="pc")
    assert text.replace(f"# Appareil\n{DEVICE}\n\n", "") == pc
