"""data/*.json files: a broken file is set aside and the last good copy restored,
never silently replaced by an empty one."""
import json

from jarvis import config, memory, store


def data_dir():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR


def corrupt_copies(name):
    return sorted(p.name for p in data_dir().glob(f"{name}.corrompu-*"))


def test_save_keeps_the_previous_version_as_bak():
    store.save("memory.json", [{"fact": "un"}])
    assert not (data_dir() / "memory.json.bak").exists()  # nothing to keep the first time
    store.save("memory.json", [{"fact": "deux"}])
    assert json.loads((data_dir() / "memory.json.bak").read_text(encoding="utf-8")) == [{"fact": "un"}]
    assert store.load("memory.json", []) == [{"fact": "deux"}]
    assert not (data_dir() / "memory.json.tmp").exists()


def test_trailing_comma_restores_the_bak_and_keeps_the_broken_file(published):
    tea = {"id": "a1", "text": "Monsieur boit du thé", "created": 1.0}
    gate = {"id": "b2", "text": "Le portail est vert", "created": 2.0}
    store.save("memory.json", [tea])
    store.save("memory.json", [tea, gate])
    broken = json.dumps([tea, gate], ensure_ascii=False)[:-1] + ",]"  # a trailing comma
    (data_dir() / "memory.json").write_text(broken, encoding="utf-8")

    assert store.load("memory.json", []) == [tea]  # the .bak content
    copies = corrupt_copies("memory.json")
    assert len(copies) == 1
    assert (data_dir() / copies[0]).read_text(encoding="utf-8") == broken  # kept as it was
    # Restored in place: the next reads agree, and the page was told.
    assert store.load("memory.json", []) == [tea]
    assert [e["type"] for e in published] == ["warning"]
    assert "memory.json était abîmé" in published[0]["text"]
    assert "sauvegarde restaurée" in published[0]["text"]

    # The next save neither deletes the broken copy nor loses the restored facts.
    memory.remember("Le garage ferme à 18 h")
    assert corrupt_copies("memory.json") == copies
    facts = [f["text"] for f in store.load("memory.json", [])]
    assert facts == ["Monsieur boit du thé", "Le garage ferme à 18 h"]


def test_broken_file_without_bak_falls_back_to_the_default(published):
    (data_dir() / "schedules.json").write_text("[{", encoding="utf-8")
    assert store.load("schedules.json", []) == []
    assert len(corrupt_copies("schedules.json")) == 1
    assert not (data_dir() / "schedules.json").exists()
    assert "repart de zéro" in published[0]["text"]
    store.save("schedules.json", [{"id": "a"}])
    assert len(corrupt_copies("schedules.json")) == 1  # still there after a save


def test_empty_file_and_wrong_shape_count_as_broken(published):
    (data_dir() / "tasks.json").write_bytes(b"")  # what a power cut can leave
    assert store.load("tasks.json", []) == []
    (data_dir() / "state.json").write_text("[1, 2]", encoding="utf-8")  # a dict was expected
    assert store.load("state.json", {}) == {}
    assert len(corrupt_copies("tasks.json")) == len(corrupt_copies("state.json")) == 1
    (data_dir() / "bad.json").write_bytes(b"\xff\xfe{")  # not even utf-8
    assert store.load("bad.json", {"a": 1}) == {"a": 1}


def test_a_broken_file_never_becomes_the_backup(published):
    store.save("memory.json", [{"fact": "bon"}])
    store.save("memory.json", [{"fact": "meilleur"}])
    (data_dir() / "memory.json").write_text("{cassé", encoding="utf-8")
    store.save("memory.json", [{"fact": "nouveau"}])  # saved without loading first
    assert json.loads((data_dir() / "memory.json.bak").read_text(encoding="utf-8")) == [{"fact": "bon"}]
    assert store.load("memory.json", []) == [{"fact": "nouveau"}]


def test_missing_file_is_not_restored_from_the_bak(published):
    store.save("memory.json", [{"fact": "un"}])
    store.save("memory.json", [{"fact": "deux"}])
    (data_dir() / "memory.json").unlink()  # deleted on purpose
    assert store.load("memory.json", []) == []
    assert published == [] and corrupt_copies("memory.json") == []


def test_broken_bak_too_gives_the_default(published):
    (data_dir() / "memory.json").write_text("[", encoding="utf-8")
    (data_dir() / "memory.json.bak").write_text("{", encoding="utf-8")
    assert store.load("memory.json", []) == []
    assert "repart de zéro" in published[0]["text"]


def test_two_breakages_in_the_same_second_keep_both_copies(published):
    for text in ("[1,", "[2,"):
        (data_dir() / "memory.json").write_text(text, encoding="utf-8")
        store.load("memory.json", [])
    copies = corrupt_copies("memory.json")
    assert len(copies) == 2
    assert {(data_dir() / c).read_text(encoding="utf-8") for c in copies} == {"[1,", "[2,"}


def test_warning_reaches_the_inbox_when_no_page_is_open():
    """Files are read at start-up, before any page connects: the warning waits."""
    from jarvis import inbox
    (data_dir() / "memory.json").write_text("[,]", encoding="utf-8")
    store.load("memory.json", [])
    warnings = [i for i in inbox.pending() if i["kind"] == "warning"]
    assert len(warnings) == 1 and "memory.json" in warnings[0]["payload"]["text"]
