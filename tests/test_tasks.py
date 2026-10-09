import json
import sys
import time

import pytest

from jarvis import config, memory, store, tasks

# Stands in for `claude -p --output-format stream-json`: echoes back what it got.
FAKE_CLAUDE = r'''
import json, sys, time
sys.stdin.reconfigure(encoding="utf-8")  # like the real CLI (node reads UTF-8)
prompt = sys.stdin.read()
def emit(obj):
    print(json.dumps(obj), flush=True)
emit({"type": "system", "subtype": "init", "session_id": "sess-init"})
if "DORS" in prompt:
    time.sleep(60)
if "PLANTE" in prompt:
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": "x"})
    sys.exit(1)
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "WebSearch", "input": {"query": "météo Lyon"}}]}})
emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-final",
      "total_cost_usd": 0.01, "result": json.dumps({"prompt": prompt, "argv": sys.argv[1:]})})
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(config, "MODELS", {"simple": "haiku", "normale": "sonnet", "complexe": "opus"})
    return script


def wait(task, timeout=20):
    end = time.time() + timeout
    while task["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    assert task["status"] != "running", "la tâche ne s'est pas terminée"
    return task


def echo(task):
    return json.loads(task["output"])


def test_prompt_goes_through_stdin_never_the_command_line(fake_claude, published):
    tricky = 'Cherche "Tom & Jerry" à 100 % ^ | <> sans casser cmd.exe'
    task = wait(tasks.create_task("Test", tricky, profile="complet"))
    assert task["status"] == "done"
    got = echo(task)
    assert got["prompt"].startswith(tricky)
    assert not any("Tom & Jerry" in a for a in got["argv"])
    assert task["session_id"] == "sess-final"
    assert task["cost_usd"] == 0.01
    progress = [e["progress"] for e in published if e["type"] == "task"]
    assert "Recherche web : météo Lyon" in progress


def test_profiles_deny_tools(fake_claude):
    argv = echo(wait(tasks.create_task("Web", "x", profile="recherche")))["argv"]
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Read", "Write", "Edit"} <= set(denied)
    argv = echo(wait(tasks.create_task("Lecture", "x", profile="lecture")))["argv"]
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Write", "WebFetch", "WebSearch"} <= set(denied) and "Read" not in denied
    argv = echo(wait(tasks.create_task("Tout", "x", profile="complet")))["argv"]
    assert "--disallowedTools" not in argv


def test_complexity_picks_the_model(fake_claude):
    argv = echo(wait(tasks.create_task("Vite", "x", complexity="simple")))["argv"]
    assert argv[argv.index("--model") + 1] == "haiku"
    argv = echo(wait(tasks.create_task("Dur", "x", complexity="complexe")))["argv"]
    assert argv[argv.index("--model") + 1] == "opus"


def test_follow_up_resumes_the_previous_session(fake_claude):
    first = wait(tasks.create_task("Rapport", "fais un rapport"))
    second = wait(tasks.create_task("Suite", "et pour mars ?", continue_task="latest"))
    argv = echo(second)["argv"]
    assert argv[argv.index("--resume") + 1] == "sess-final"
    assert second["resumed_from"] == first["id"]


def test_follow_up_without_history_starts_fresh(fake_claude):
    task = wait(tasks.create_task("Suite", "x", continue_task="latest"))
    assert "--resume" not in echo(task)["argv"]
    assert "nouvelle session" in task["note"]


def test_memory_is_shared_with_claude(fake_claude):
    memory.remember("Monsieur habite à Lyon")
    task = wait(tasks.create_task("Météo", "quel temps fait-il ?"))
    assert "Monsieur habite à Lyon" in echo(task)["prompt"]


def test_quick_failure_retries_on_the_default_model(fake_claude, published):
    task = wait(tasks.create_task("Casse", "PLANTE", complexity="complexe"))
    assert task["status"] == "error"
    assert task["model"] == ""  # second attempt without --model
    assert any("Nouvel essai" in (e.get("progress") or "") for e in published)


def test_timeout_kills_the_session(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "TASK_TIMEOUT", 1)
    started = time.time()
    task = wait(tasks.create_task("Long", "DORS"))
    assert task["status"] == "error"
    assert task["output"].startswith("Timeout")
    assert time.time() - started < 15


def test_cancel_stops_the_running_session(fake_claude):
    task = tasks.create_task("Long", "DORS")
    end = time.time() + 10
    while task["id"] not in tasks.PROCS and time.time() < end:
        time.sleep(0.05)
    assert tasks.cancel("latest")["cancelled"] == task["id"]
    wait(task)
    assert task["status"] == "cancelled"
    assert tasks.cancel(task["id"])["ok"] is False  # already over


def test_missing_claude_is_explained(monkeypatch):
    def missing():
        raise FileNotFoundError("claude")
    monkeypatch.setattr(tasks, "claude_command", missing)
    task = wait(tasks.create_task("X", "x"))
    assert task["status"] == "error"
    assert "npm install -g @anthropic-ai/claude-code" in task["output"]


def test_empty_prompt_is_refused():
    with pytest.raises(ValueError):
        tasks.create_task("X", "   ")


def test_history_survives_a_restart(fake_claude):
    task = wait(tasks.create_task("Garde", "x"))
    saved = store.load(tasks.HISTORY_FILE, [])
    assert [t["id"] for t in saved] == [task["id"]]
    assert "full_prompt" not in saved[0]
    tasks.TASKS.clear()
    tasks.load_history()
    assert tasks.TASKS[task["id"]]["session_id"] == "sess-final"
