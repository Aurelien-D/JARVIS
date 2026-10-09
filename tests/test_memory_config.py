import os
import time

import pytest

from jarvis import config, memory


def test_remember_dedupes_and_forget_matches_words(published):
    memory.remember("Monsieur préfère le thé")
    memory.remember("monsieur préfère le THÉ")
    memory.remember("Sa fille s'appelle Léa")
    assert len(memory.facts()) == 2
    assert [f["text"] for f in memory.forget("thé")["forgotten"]] == ["Monsieur préfère le thé"]
    assert [f["text"] for f in memory.facts()] == ["Sa fille s'appelle Léa"]
    assert [e["type"] for e in published].count("memory") == 3


def test_forget_by_id_only_removes_that_fact(published):
    a = memory.remember("Projet Valdor")
    memory.remember("Projet JARVIS")
    assert [f["id"] for f in memory.forget(a["id"])["forgotten"]] == [a["id"]]
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


# ---------------------------------------------------------------- safe forgetting (WP14)

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import server
    from jarvis import security
    return TestClient(server.app, base_url="http://127.0.0.1:8788",
                      headers={"X-Jarvis-Token": security.TOKEN})


def test_forget_with_several_matches_is_ambiguous_and_deletes_nothing(published):
    facts = [memory.remember(t) for t in ("Monsieur préfère le thé", "Monsieur habite à Laon",
                                          "Monsieur a un chien")]
    out = memory.forget("monsieur")
    assert out["ok"] is False
    assert sorted(a["id"] for a in out["ambiguous"]) == sorted(f["id"] for f in facts)
    assert {a["text"] for a in out["ambiguous"]} == {f["text"] for f in facts}
    assert len(memory.facts()) == 3  # nothing deleted
    # With the id the model asked for, exactly that one goes.
    assert [f["id"] for f in memory.forget(facts[1]["id"])["forgotten"]] == [facts[1]["id"]]
    assert [f["text"] for f in memory.facts()] == ["Monsieur préfère le thé", "Monsieur a un chien"]
    # The exact wording names one fact even when it is part of another.
    memory.remember("Projet Valdor")
    memory.remember("Projet Valdor 2")
    assert [f["text"] for f in memory.forget("projet valdor")["forgotten"]] == ["Projet Valdor"]
    assert memory.forget("introuvable") == {"ok": False, "error": "Rien de tel dans ma mémoire."}


def test_the_forget_tool_says_ambiguous_and_gives_the_ids(published):
    from jarvis import tools
    a = memory.remember("Monsieur boit du thé")
    memory.remember("Monsieur boit du café")
    out = tools.run_tool("forget", {"query": "boit"})
    assert out["ok"] is False and len(out["ambiguous"]) == 2 and "id" in out["error"]
    out = tools.run_tool("forget", {"query": a["id"]})
    assert out == {"ok": True, "forgotten": ["Monsieur boit du thé"], "ids": [a["id"]]}


def test_a_forgotten_fact_waits_seven_days_in_the_trash(published, monkeypatch):
    fact = memory.remember("Le code du garage est 1234")
    memory.forget("garage")
    assert memory.facts() == []
    assert memory.undo(fact["id"])["text"] == "Le code du garage est 1234"
    assert [f["id"] for f in memory.facts()] == [fact["id"]]
    assert memory.undo(fact["id"]) is None  # once only
    memory.forget(fact["id"])
    later = time.time() + memory.TRASH_DAYS * 86400 + 60
    monkeypatch.setattr(memory.time, "time", lambda: later)
    assert memory.undo(fact["id"]) is None  # too late: emptied after 7 days


def test_memory_routes_edit_delete_by_id_and_undo(client, published):
    tea = memory.remember("Monsieur préfère le thé")
    memory.remember("Monsieur aime le jazz")
    # PATCH: validated text, 404 for an unknown id, 422 without a text (the panel's probe).
    r = client.patch(f"/api/memory/{tea['id']}", json={"text": "  Monsieur préfère   le thé vert "})
    assert r.status_code == 200 and r.json()["fact"]["text"] == "Monsieur préfère le thé vert"
    assert client.patch(f"/api/memory/{tea['id']}", json={"text": "   "}).status_code == 400
    assert client.patch(f"/api/memory/{tea['id']}", json={"text": "x" * 501}).status_code == 400
    assert client.patch("/api/memory/zzz", json={"text": "ok"}).status_code == 404
    assert client.patch("/api/memory/__sonde__", json={}).status_code == 422
    # DELETE takes an id, never words: 'monsieur' in the URL deletes nothing.
    assert client.delete("/api/memory/monsieur").json() == {"ok": True, "removed": 0}
    assert len(memory.facts()) == 2
    assert client.delete(f"/api/memory/{tea['id']}").json()["removed"] == 1
    # 'Annuler' brings it back.
    r = client.post(f"/api/undo/{tea['id']}")
    assert r.status_code == 200 and r.json()["fact"]["id"] == tea["id"]
    assert {f["text"] for f in memory.facts()} == {"Monsieur préfère le thé vert", "Monsieur aime le jazz"}
    assert client.post(f"/api/undo/{tea['id']}").status_code == 404


def test_a_damaged_trash_never_breaks_undo(published):
    import json
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / memory.TRASH_FILE).write_text(json.dumps(
        [{"id": "a"}, {"id": "b", "fact": "pas un dict", "deleted": time.time()}, "texte"]), encoding="utf-8")
    assert memory.undo("a") is None and memory.undo("b") is None
