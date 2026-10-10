import inspect
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import config, memory, security, store, tasks, usage

# Stands in for `claude`: `--version`, `auth status` (exit code from FAKE_AUTH),
# and `-p --output-format stream-json`, which echoes back what it got. Words in
# the prompt pick a scenario.
FAKE_CLAUDE = r'''
import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.300 (Claude Code)")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    code = int(os.environ.get("FAKE_AUTH", "0"))
    print(json.dumps({"loggedIn": code == 0, "authMethod": "claude.ai" if code == 0 else "none"}))
    sys.exit(code)
sys.stdin.reconfigure(encoding="utf-8")  # like the real CLI (node reads UTF-8)
prompt = sys.stdin.read()
with open("pids.txt", "a") as f:  # in the working folder: the tests check the process died
    f.write(f"{os.getpid()}\n")
def emit(obj):
    print(json.dumps(obj), flush=True)
if "OPTION" in prompt and "--safe-mode" in args:
    print("error: unknown option '--safe-mode'", file=sys.stderr)
    sys.exit(1)
tools = args[args.index("--tools") + 1].split(",") if "--tools" in args else ["Bash", "Read", "Write", "Edit"]
if "INTERDIT" in prompt:
    tools = ["WebSearch", "PowerShell"]
emit({"type": "system", "subtype": "init", "session_id": "sess-init", "tools": tools,
      "mcp_servers": [{"name": "ares", "status": "connected"}]})
if "CONNEXION" in prompt and "--safe-mode" in args:
    for attempt in (1, 2, 3):
        emit({"type": "system", "subtype": "api_retry", "attempt": attempt, "retry_delay_ms": 500,
              "error_status": 401, "error": "authentication_failed"})
    time.sleep(60)
if "INTERDIT" in prompt or "DORS" in prompt:
    time.sleep(60)
if "PLANTE" in prompt:
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": "x"})
    sys.exit(1)
if "TOURS" in prompt:
    emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "session_id": "x",
          "errors": ["trop de tours"]})
    sys.exit(1)
if "QUOTA" in prompt:
    emit({"type": "system", "subtype": "api_retry", "attempt": 1, "retry_delay_ms": 2500,
          "error_status": 429, "error": "rate_limit"})
if "ECRIS" in prompt:
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "/x/rapport.md"}},
        {"type": "tool_use", "id": "w2", "name": "Edit", "input": {"file_path": "/x/rapport.md"}},
        {"type": "tool_use", "id": "w3", "name": "Write", "input": {"file_path": "/x/refuse.txt"}},
        {"type": "tool_use", "id": "w4", "name": "NotebookEdit", "input": {"notebook_path": "/x/a.ipynb"}}]}})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "w3", "is_error": True, "content": "refusé"}]}})
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "s1", "name": "WebSearch", "input": {"query": "météo Lyon"}}]}})
denials = []
if "REFUS" in prompt:
    denials = [{"tool_name": "Bash", "tool_use_id": "b1", "tool_input": {"command": "rm -rf build"}}]
emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-final",
      "total_cost_usd": 0.01, "duration_ms": 1234, "num_turns": 3, "permission_denials": denials,
      "result": json.dumps({"prompt": prompt, "argv": args})})
'''

@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MODELS", {"simple": "haiku", "normale": "sonnet", "complexe": "opus"})
    monkeypatch.setattr(config, "PERMISSION_MODE", "auto")
    monkeypatch.setattr(config, "SAFE_MODE", "auto")
    monkeypatch.setattr(config, "RESTRICTED", "auto")
    monkeypatch.setattr(config, "TASK_BUDGET_USD", 2.0)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "MCP_CONFIG", "")
    return script


@pytest.fixture(autouse=True)
def no_leftover_tasks():
    """A task still running must not hold a slot (or report) in the next test."""
    yield
    for task in tasks.running():
        tasks.cancel(task["id"])
    end = time.time() + 10
    while (tasks._active or tasks._waiting) and time.time() < end:
        time.sleep(0.05)


def wait(task, timeout=20):
    end = time.time() + timeout
    while task["status"] in tasks.ACTIVE and time.time() < end:
        time.sleep(0.05)
    assert task["status"] not in tasks.ACTIVE, "la tâche ne s'est pas terminée"
    return task


def echo(task):
    return json.loads(task["output"])


def denied(cmd):
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--disallowedTools"]


def option(cmd, name):
    return cmd[cmd.index(name) + 1] if name in cmd else None


def gone(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) isn't supported on Windows: ask for the exit code
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True
        code = ctypes.c_ulong()
        try:
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value != 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


# ---------------------------------------------------------------- the command line

def test_recherche_is_web_only(fake_claude):
    cmd = tasks.build_command("recherche")
    assert option(cmd, "--tools") == "WebSearch,WebFetch"
    assert denied(cmd) == ["mcp__*"]
    assert "--safe-mode" in cmd
    assert "--restricted" in cmd  # 2.1.248+: no command or code tools at all
    assert "--mcp-config" not in cmd


def test_lecture_reads_without_secrets(fake_claude):
    cmd = tasks.build_command("lecture")
    assert option(cmd, "--tools") == "Read,Glob,Grep"
    rules = denied(cmd)
    assert "mcp__*" in rules
    root = "/" + tasks.posix(config.ROOT)
    assert f"Read({root}/.env)" in rules
    assert any(r.startswith("Read(//") and r.endswith("/**)") for r in rules)  # data/
    assert all(not r.startswith("Read(///") for r in rules)
    assert "--safe-mode" in cmd
    assert "--restricted" not in cmd  # it would confine reads to the working folder


def test_complet_has_every_tool_but_guard_rails(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFIG", "mcp.json")
    monkeypatch.setattr(config, "ARES_MCP_NAME", "ares")
    cmd = tasks.build_command("complet")
    assert "--tools" not in cmd
    rules = denied(cmd)
    data = "/" + tasks.posix(config.DATA_DIR)
    assert "mcp__ares__remember" in rules
    assert f"Edit({data}/**)" in rules and f"Write({data}/**)" in rules
    assert "Edit(~/.claude/**)" in rules and "Write(~/.mcp.json)" in rules
    assert "mcp__*" not in rules
    assert "--safe-mode" not in cmd and "--restricted" not in cmd
    assert option(cmd, "--mcp-config") == "mcp.json"


def test_windows_paths_become_posix_rules():
    assert tasks.posix(r"C:\Users\Aurelien\jarvis") == "/c/Users/Aurelien/jarvis"
    assert tasks.posix("D:/JARVIS/data/") == "/d/JARVIS/data"
    assert tasks.posix("/home/user/jarvis/") == "/home/user/jarvis"
    assert tasks._rule_path(r"C:\Users\X") == "//c/Users/X"


def test_auto_mode_and_prompts_follow_the_version(fake_claude, monkeypatch):
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 259))
    cmd = tasks.build_command("lecture")
    assert option(cmd, "--permission-mode") == "auto"
    assert option(cmd, "--permission-prompts") == "none"
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 200))
    cmd = tasks.build_command("recherche")
    assert "--permission-prompts" not in cmd
    assert "--restricted" not in cmd  # before 2.1.248
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: None)
    assert "--permission-prompts" not in tasks.build_command("lecture")


def test_bypass_mode_never_gets_restricted(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "PERMISSION_MODE", "bypassPermissions")
    cmd = tasks.build_command("recherche")
    assert "--restricted" not in cmd and "--permission-prompts" not in cmd
    monkeypatch.setattr(config, "PERMISSION_MODE", "off")
    assert "--permission-mode" not in tasks.build_command("recherche")


def test_hardening_settings(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "SAFE_MODE", "off")
    monkeypatch.setattr(config, "RESTRICTED", "off")
    cmd = tasks.build_command("recherche")
    assert "--safe-mode" not in cmd and "--restricted" not in cmd
    monkeypatch.setattr(config, "RESTRICTED", "on")
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: None)
    assert "--restricted" in tasks.build_command("recherche")


def test_safe_defaults():
    source = inspect.getsource(config)
    assert '_float("JARVIS_TASK_BUDGET_USD", 2.0)' in source
    assert 'os.environ.get("JARVIS_PERMISSION_MODE", "auto")' in source
    assert 'os.environ.get("JARVIS_WORKDIR", "~/JARVIS-travail")' in source


def test_budget_flag_is_there_by_default(fake_claude, monkeypatch):
    assert option(tasks.build_command("lecture"), "--max-budget-usd") == "2.0"
    monkeypatch.setattr(config, "TASK_BUDGET_USD", 0)
    assert "--max-budget-usd" not in tasks.build_command("lecture")


def test_version_is_read_once(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "_version", {"cmd": None, "value": None, "at": 0.0})
    assert tasks.claude_version() == (2, 1, 300)
    assert tasks.version_text() == "2.1.300"
    script.write_text("print('cassé')", encoding="utf-8")
    assert tasks.claude_version() == (2, 1, 300)  # cached
    assert tasks.claude_version(refresh=True) is None


def test_task_args_reach_claude(fake_claude):
    argv = echo(wait(tasks.create_task("Web", "x", profile="recherche")))["argv"]
    assert option(argv, "--tools") == "WebSearch,WebFetch"
    assert denied(argv) == ["mcp__*"]
    assert Path(config.WORKDIR).is_dir()  # the working folder is created when missing


# ---------------------------------------------------------------- profiles

def test_missing_or_unknown_profile_reads_only(fake_claude):
    assert tasks.create_task("A", "x", profile=None)["profile"] == "lecture"
    assert tasks.create_task("B", "x", profile="xyz")["profile"] == "lecture"
    assert tasks.create_task("C", "x")["profile"] == "lecture"


def test_kill_switch_stops_a_profile_that_was_not_applied(fake_claude, published):
    started = time.time()
    task = wait(tasks.create_task("Web", "INTERDIT", profile="recherche"))
    assert task["status"] == "error"
    assert "Profil de sécurité" in task["output"]
    assert task["sandbox_violation"] == ["PowerShell"]
    assert task["mcp_servers"] == [{"name": "ares", "status": "connected"}]
    assert time.time() - started < 15  # killed, not left to sleep
    pid = int((Path(config.WORKDIR) / "pids.txt").read_text().split()[0])
    assert gone(pid)
    assert task["model"] == "sonnet"  # no retry of any kind


def test_complet_may_announce_any_tool(fake_claude):
    assert tasks.violations("complet", ["Bash", "mcp__ares__x"]) == []
    assert tasks.violations("lecture", ["Read", "Glob", "mcp__x__y", "Skill"]) == ["Skill", "mcp__x__y"]


def test_memory_stays_out_of_web_tasks(fake_claude):
    memory.remember("Monsieur habite à Lyon")
    web = wait(tasks.create_task("Météo", "quel temps fait-il ?", profile="recherche"))
    assert "Lyon" not in echo(web)["prompt"]
    local = wait(tasks.create_task("Fichiers", "range mes notes", profile="lecture"))
    assert "Monsieur habite à Lyon" in echo(local)["prompt"]


# ---------------------------------------------------------------- results

def test_prompt_goes_through_stdin_never_the_command_line(fake_claude, published):
    tricky = 'Cherche "Tom & Jerry" à 100 % ^ | <> sans casser cmd.exe'
    task = wait(tasks.create_task("Test", tricky, profile="complet"))
    assert task["status"] == "done"
    got = echo(task)
    assert got["prompt"].startswith(tricky)
    assert not any("Tom & Jerry" in a for a in got["argv"])
    assert task["session_id"] == "sess-final"
    assert task["cost_usd"] == 0.01
    assert task["duration_ms"] == 1234 and task["num_turns"] == 3
    progress = [e["progress"] for e in published if e["type"] == "task"]
    assert "Recherche web : météo Lyon" in progress


def test_error_result_is_explained_in_french(fake_claude):
    task = wait(tasks.create_task("Long", "TOURS"))
    assert task["status"] == "error"
    assert task["output"].startswith("Claude a atteint le nombre maximal d'étapes")
    assert "trop de tours" in task["output"]
    other = tasks._result_error({"subtype": "error_max_budget_usd", "errors": []}, "")
    assert other == "Budget de la tâche atteint (2,00 $) : Claude s'est arrêté avant la fin."


def test_rate_limit_retry_shows_french_progress(fake_claude, published):
    task = wait(tasks.create_task("Quota", "QUOTA"))
    assert task["status"] == "done"
    progress = [e["progress"] for e in published if e["type"] == "task"]
    assert ("Claude indisponible (limite d'utilisation atteinte), nouvel essai dans 3 s…"
            in progress)


def test_files_and_denials_are_kept(fake_claude):
    task = wait(tasks.create_task("Rapport", "ECRIS REFUS", profile="complet"))
    assert task["files"] == ["/x/rapport.md", "/x/a.ipynb"]  # deduplicated, the failed one dropped
    assert task["permission_denials"] == [{"tool": "Bash", "detail": "Commande : rm -rf build"}]


def test_log_keeps_the_last_lines(fake_claude):
    task = wait(tasks.create_task("Journal", "x"))
    lines = [entry["text"] for entry in tasks.task_log(task["id"])]
    assert "Recherche web : météo Lyon" in lines and lines[-1] == "Fin : terminée"
    for i in range(100):
        tasks._log(task, f"ligne {i}")
    assert len(task["log"]) == tasks.LOG_SIZE
    assert "log" not in tasks.public(task)  # not pushed with every update


def test_complexity_picks_the_model(fake_claude):
    argv = echo(wait(tasks.create_task("Vite", "x", complexity="simple")))["argv"]
    assert option(argv, "--model") == "haiku"
    argv = echo(wait(tasks.create_task("Dur", "x", complexity="complexe")))["argv"]
    assert option(argv, "--model") == "opus"


def test_follow_up_resumes_the_previous_session(fake_claude):
    first = wait(tasks.create_task("Rapport", "fais un rapport"))
    second = wait(tasks.create_task("Suite", "et pour mars ?", continue_task="latest"))
    argv = echo(second)["argv"]
    assert option(argv, "--resume") == "sess-final"
    assert second["resumed_from"] == first["id"]


def test_follow_up_without_history_starts_fresh(fake_claude):
    task = wait(tasks.create_task("Suite", "x", continue_task="latest"))
    assert "--resume" not in echo(task)["argv"]
    assert "nouvelle session" in task["note"]


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
    assert task["output"].startswith("Délai dépassé")
    assert time.time() - started < 15


def test_cancel_stops_the_running_session(fake_claude):
    task = tasks.create_task("Long", "DORS")
    end = time.time() + 10
    while task["id"] not in tasks.PROCS and time.time() < end:
        time.sleep(0.05)
    assert tasks.cancel("latest")["cancelled"] == task["id"]
    wait(task)
    assert task["status"] == "cancelled"
    assert tasks.cancel(task["id"]) == {"ok": False, "error": "La tâche est déjà annulée."}


# ---------------------------------------------------------------- Claude Code missing or logged out

def test_missing_claude_is_explained(monkeypatch):
    def missing():
        raise FileNotFoundError("claude")
    monkeypatch.setattr(tasks, "claude_command", missing)
    task = wait(tasks.create_task("X", "x"))
    assert task["status"] == "error"
    assert "irm https://claude.ai/install.ps1 | iex" in task["output"]
    assert tasks.claude_version() is None


def test_logged_out_is_explained(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_AUTH", "1")
    task = wait(tasks.create_task("X", "PLANTE"))
    assert task["status"] == "error"
    assert task["output"] == tasks.T.claude_logged_out
    assert task["model"] == "sonnet"  # no pointless retry


# ---------------------------------------------------------------- self-healing flags

def test_unknown_safe_mode_heals_itself(fake_claude, published):
    task = wait(tasks.create_task("Web", "OPTION", profile="recherche"))
    assert task["status"] == "done"
    assert "--safe-mode" not in echo(task)["argv"]
    assert store.load("state.json", {})["safe_mode_broken"] == "2.1.300"
    assert "--safe-mode" not in tasks.build_command("lecture")  # remembered
    # A new Claude Code version gets its chance again.
    store.save("state.json", {"safe_mode_broken": "2.1.200"})
    assert "--safe-mode" in tasks.build_command("lecture")


def test_refused_login_under_safe_mode_heals_itself(fake_claude):
    task = wait(tasks.create_task("Web", "CONNEXION", profile="recherche"), timeout=30)
    assert task["status"] == "done"
    argv = echo(task)["argv"]
    assert "--safe-mode" not in argv and "--restricted" not in argv
    state = store.load("state.json", {})
    assert state["safe_mode_broken"] == state["restricted_broken"] == "2.1.300"


def test_safe_mode_on_never_heals(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "SAFE_MODE", "on")
    task = wait(tasks.create_task("Web", "OPTION", profile="lecture"))
    assert task["status"] == "error"
    assert "--safe-mode" in task["output"] and "claude update" in task["output"]
    assert "safe_mode_broken" not in store.load("state.json", {})


# ---------------------------------------------------------------- budgets and concurrency

def test_daily_cap_refuses_new_tasks(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    monkeypatch.setattr(usage, "claude_spent_today", lambda: 5.2)
    with pytest.raises(ValueError, match="Plafond du jour atteint"):
        tasks.create_task("X", "x")
    assert not tasks.TASKS
    monkeypatch.setattr(usage, "claude_spent_today", lambda: 1.0)
    assert tasks.create_task("X", "x")["status"] in tasks.ACTIVE


def test_cost_goes_to_the_daily_counter(fake_claude, monkeypatch):
    spent = []
    monkeypatch.setattr(usage, "add_claude", spent.append, raising=False)
    wait(tasks.create_task("X", "x"))
    assert spent == [0.01]


def test_extra_tasks_wait_in_line(fake_claude, published):
    started = [tasks.create_task(f"T{i}", "DORS") for i in range(4)]
    assert [t["status"] for t in started] == ["running", "running", "running", "en_file"]
    assert started[3]["progress"] == "En file d'attente…"
    assert {t["id"] for t in tasks.running()} == {t["id"] for t in started}
    tasks.cancel(started[0]["id"])
    end = time.time() + 10
    while started[3]["status"] == "en_file" and time.time() < end:
        time.sleep(0.05)
    assert started[3]["status"] == "running"  # a slot freed: it starts


def test_a_queued_task_can_be_cancelled(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 1)
    first = tasks.create_task("Long", "DORS")
    queued = tasks.create_task("Après", "x")
    assert queued["status"] == "en_file"
    assert tasks.cancel(queued["id"])["ok"]
    wait(queued)
    assert queued["status"] == "cancelled"
    assert first["status"] == "running"


def test_shutdown_interrupts_and_saves(fake_claude, published):
    running = tasks.create_task("Long", "DORS")
    pids = Path(config.WORKDIR) / "pids.txt"
    end = time.time() + 10
    # The fake has really started (it wrote its pid), not just been spawned.
    while not (running["id"] in tasks.PROCS and pids.exists() and pids.read_text().strip()) \
            and time.time() < end:
        time.sleep(0.05)
    tasks.shutdown()
    assert running["status"] == "interrompue"
    assert running["output"] == "JARVIS a été fermé pendant la tâche."
    saved = {t["id"]: t for t in store.load(tasks.HISTORY_FILE, [])}
    assert saved[running["id"]]["status"] == "interrompue"
    pid = int(pids.read_text().split()[0])
    end = time.time() + 10
    while not gone(pid) and time.time() < end:  # killed, then reaped by its runner
        time.sleep(0.05)
    assert gone(pid)
    time.sleep(0.3)
    assert running["status"] == "interrompue"  # the runner's own ending doesn't overwrite it


def test_job_object_is_best_effort(monkeypatch):
    class Proc:
        _handle = 0
    assert tasks._job_assign(Proc()) is False  # not Windows
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    assert tasks._job_assign(Proc()) is False  # no kernel32 here: no crash either


# ---------------------------------------------------------------- approval of denied tools

def test_approve_resumes_with_the_denied_tools(fake_claude):
    first = wait(tasks.create_task("Nettoyage", "REFUS", profile="complet"))
    resumed = wait(tasks.approve(first["id"]))
    got = echo(resumed)
    assert got["prompt"] == "Approuvé par monsieur : exécute maintenant l'action refusée."
    assert option(got["argv"], "--resume") == "sess-final"
    assert option(got["argv"], "--allowedTools") == "Bash"
    assert resumed["profile"] == "complet" and resumed["origin"] == "approbation"
    with pytest.raises(ValueError):
        tasks.approve(resumed["id"])  # nothing was denied this time


# ---------------------------------------------------------------- history

def test_empty_prompt_is_refused():
    with pytest.raises(ValueError, match="Consigne vide"):
        tasks.create_task("X", "   ")


def test_history_survives_a_restart(fake_claude):
    task = wait(tasks.create_task("Garde", "x"))
    end = time.time() + 5
    while not store.load(tasks.HISTORY_FILE, []) and time.time() < end:  # saved right after
        time.sleep(0.05)
    saved = store.load(tasks.HISTORY_FILE, [])
    assert [t["id"] for t in saved] == [task["id"]]
    assert "full_prompt" not in saved[0] and saved[0]["log"]
    tasks.TASKS.clear()
    tasks.load_history()
    assert tasks.TASKS[task["id"]]["session_id"] == "sess-final"
    assert tasks.task_log(task["id"])


# ---------------------------------------------------------------- API

@pytest.fixture
def client():
    import server
    return TestClient(server.app, base_url="http://127.0.0.1:8788",
                      headers={"X-Jarvis-Token": security.TOKEN})


def test_keyboard_task_api(client, fake_claude):
    r = client.post("/api/tasks", json={"prompt": "  "})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Consigne vide")
    r = client.post("/api/tasks", json={"prompt": "Résume mes notes\nen détail"})
    assert r.status_code == 200
    body = r.json()
    assert body["profile"] == "lecture" and body["origin"] == "clavier"
    assert body["title"] == "Résume mes notes"
    wait(tasks.TASKS[body["id"]])
    log = client.get(f"/api/task/{body['id']}/log").json()
    assert log["id"] == body["id"] and log["log"][-1]["text"] == "Fin : terminée"
    assert client.get("/api/task/nope/log").status_code == 404


def test_keyboard_task_api_respects_the_cap(client, fake_claude, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    monkeypatch.setattr(usage, "claude_spent_today", lambda: 3.0)
    r = client.post("/api/tasks", json={"prompt": "x"})
    assert r.status_code == 400 and "Plafond" in r.json()["detail"]
