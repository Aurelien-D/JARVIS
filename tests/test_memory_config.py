import os

import pytest

from jarvis import config, memory


def test_remember_dedupes_and_forget_matches_words(published):
    memory.remember("Monsieur préfère le thé")
    memory.remember("monsieur préfère le THÉ")
    memory.remember("Sa fille s'appelle Léa")
    assert len(memory.facts()) == 2
    assert [f["text"] for f in memory.forget("thé")] == ["Monsieur préfère le thé"]
    assert [f["text"] for f in memory.facts()] == ["Sa fille s'appelle Léa"]
    assert [e["type"] for e in published].count("memory") == 3


def test_forget_by_id_only_removes_that_fact(published):
    a = memory.remember("Projet Valdor")
    memory.remember("Projet JARVIS")
    assert [f["id"] for f in memory.forget(a["id"])] == [a["id"]]
    assert len(memory.facts()) == 1


def test_memory_text_is_newest_first_and_bounded(published):
    for i in range(50):
        memory.remember(f"fait numéro {i}")
    text = memory.as_text(100)
    assert text.startswith("- fait numéro 49")
    assert len(text) <= 100


def test_empty_facts_are_refused(published):
    with pytest.raises(ValueError):
        memory.remember("   ")
    with pytest.raises(ValueError):
        memory.forget("")


def test_env_file_from_notepad_is_read(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    # BOM + Windows line endings + quotes + comments, as Notepad saves it.
    env.write_bytes("﻿JARVIS_TEST_A=un\r\n# commentaire\r\nJARVIS_TEST_B=\"deux\"\r\nJARVIS_TEST_C=garde\r\n"
                    .encode("utf-8"))
    for key in ("JARVIS_TEST_A", "JARVIS_TEST_B"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JARVIS_TEST_C", "réel")
    config.load_env(env)
    assert os.environ["JARVIS_TEST_A"] == "un"
    assert os.environ["JARVIS_TEST_B"] == "deux"
    assert os.environ["JARVIS_TEST_C"] == "réel"  # real environment wins
