"""Defensive checks of the Claude Code task profiles (jarvis/tasks.py): each
test proves that one protection holds.

5. recherche and lecture are allowlists (--tools) with every MCP tool denied;
   complet keeps the protected-path rules; Windows paths become //c/... rules;
   a missing or unknown profile is lecture; memory never reaches a web task.
6. The kill switch: a Claude Code that announces more than its profile is
   stopped with the 'Profil de sécurité' error, its processes gone.
"""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import config, memory, scheduler, security, tasks, tools
from test_tasks import FAKE_CLAUDE, denied, echo, gone, option, wait


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
    monkeypatch.setattr(config, "CONFIRM_COMPLET", True)
    return script


@pytest.fixture(autouse=True)
def no_leftover_tasks():
    yield
    for task in tasks.running():
        tasks.cancel(task["id"])
    end = time.time() + 10
    while (tasks._active or tasks._waiting) and time.time() < end:
        time.sleep(0.05)


@pytest.fixture
def client():
    import server
    return TestClient(server.app, base_url="http://127.0.0.1:8788",
                      headers={"X-Jarvis-Token": security.TOKEN})


ALLOWLIST = {"recherche": "WebSearch,WebFetch", "lecture": "Read,Glob,Grep"}

# ---------------------------------------------------------------- 5. what each profile gets

@pytest.mark.parametrize("profile", ["recherche", "lecture"])
def test_restricted_profiles_are_allowlists_with_mcp_denied_holds(fake_claude, monkeypatch, profile):
    monkeypatch.setattr(config, "MCP_CONFIG", "mcp.json")  # never loaded below complet
    for allowed in (None, ["Bash", "Write", "mcp__ares__remember"]):  # even with an approval
        for mode in ("auto", "bypassPermissions", "off"):
            monkeypatch.setattr(config, "PERMISSION_MODE", mode)
            cmd = tasks.build_command(profile, "sonnet", allowed=allowed)
            assert cmd.count("--tools") == 1 and option(cmd, "--tools") == ALLOWLIST[profile]
            assert not set(option(cmd, "--tools").split(",")) & tasks.FORBIDDEN
            assert "mcp__*" in denied(cmd)
            assert "--mcp-config" not in cmd
            assert "--dangerously-skip-permissions" not in cmd
            assert "--safe-mode" in cmd  # no hooks, plugins or CLAUDE.md to widen it


def _windows_install(monkeypatch):
    """JARVIS installed in a Windows folder with spaces and accents."""
    monkeypatch.setattr(config, "ROOT", r"C:\Users\Hélène Exemple\Mes Programmes\JARVIS")
    monkeypatch.setattr(config, "DATA_DIR", r"C:\Users\Hélène Exemple\Mes Programmes\JARVIS\données")
    monkeypatch.setattr(config, "ARES_MCP_NAME", "ares")
    monkeypatch.setattr(config, "SAFE_MODE", "on")  # no state.json to read in that folder here


def test_complet_keeps_the_protected_path_rules_holds(fake_claude, monkeypatch):
    _windows_install(monkeypatch)
    root = "//c/Users/Hélène Exemple/Mes Programmes/JARVIS"
    data = f"{root}/données"
    protected = {"mcp__ares__remember", f"Edit({root}/**)", f"Write({root}/**)", f"Edit({data}/**)",
                 f"Write({data}/**)", "Edit(~/.claude/**)", "Write(~/.claude/**)", "Edit(~/.mcp.json)",
                 "Write(~/.mcp.json)", f"Read({root}/.env)"}
    for allowed in (None, ["Bash", "Edit", "Write"]):  # an approval never lifts them
        cmd = tasks.build_command("complet", allowed=allowed)
        assert protected <= set(denied(cmd))
        assert "--tools" not in cmd
    # lecture keeps JARVIS's secrets and data out of reach, in the same //c/ form.
    rules = denied(tasks.build_command("lecture"))
    assert {f"Read({root}/.env)", f"Read({data}/**)", f"Read({data}/app-window*/**)"} <= set(rules)
    # One argument per rule: a path with spaces stays whole.
    cmd = tasks.build_command("complet")
    assert f"Edit({root}/**)" in cmd


@pytest.mark.parametrize("given, expected", [
    (r"C:\Users\Hélène Exemple\Documents\Été 2026", "/c/Users/Hélène Exemple/Documents/Été 2026"),
    (r"C:\Users\X", "/c/Users/X"),
    (r"c:\users\x\\", "/c/users/x"),
    (r"D:\Données personnelles\JARVIS\data" + "\\", "/d/Données personnelles/JARVIS/data"),
    ("E:/Mixte\\séparateurs/ici", "/e/Mixte/séparateurs/ici"),
    (r"C:\\Users\\Double", "/c/Users/Double"),
    ("C:\\", "/c"),
    ("C:", "/c"),
    ("Z:/", "/z"),
    ("/home/user/jarvis/", "/home/user/jarvis"),
    ("/", "/"),
])
def test_windows_paths_become_posix_permission_paths_holds(given, expected):
    assert tasks.posix(given) == expected
    assert tasks._rule_path(given) == "/" + expected


def test_windows_paths_with_backslashes_only_become_posix_on_windows_holds(monkeypatch):
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    assert tasks.posix(r"\Users\Hélène\JARVIS" + "\\") == "/Users/Hélène/JARVIS"


BAD_PROFILES = [None, "", "xyz", "Complet", " complet", "COMPLET", "complet\u200b", "full", 3,
                ["complet"], {"complet": True}]


def test_missing_or_unknown_profile_becomes_lecture_holds(fake_claude, client):
    lecture = tasks.build_command("lecture", "sonnet")
    for profile in BAD_PROFILES:
        assert tasks.normalize_profile(profile) == "lecture", profile
        assert tasks.build_command(profile, "sonnet") == lecture, profile
        assert tasks.hardening(profile) == tasks.hardening("lecture"), profile
    assert tasks.create_task("Sans", "x", profile=None)["profile"] == "lecture"
    assert tasks.create_task("Inconnu", "x", profile="xyz")["profile"] == "lecture"
    # The voice tool without a profile (or a wrong one), and the composer's route.
    out = tools.run_tool("delegate_to_claude", {"title": "Sans", "prompt": "x"})
    assert out["profile"] == "lecture"
    out = tools.run_tool("delegate_to_claude", {"title": "Faux", "prompt": "x", "profile": "admin"})
    assert out["profile"] == "lecture"
    body = client.post("/api/tasks", json={"prompt": "x", "profile": "tout"}).json()
    assert body["profile"] == "lecture"
    for task in list(tasks.TASKS.values()):
        argv = echo(wait(task))["argv"]
        assert option(argv, "--tools") == "Read,Glob,Grep"


def test_memory_never_reaches_recherche_prompts_holds(fake_claude, monkeypatch):
    memory.remember("Monsieur habite à Lyon, 12 rue des Lilas")
    secret = "rue des Lilas"
    # Directly, through the voice tool, and when a routine fires.
    web = wait(tasks.create_task("Météo", "quel temps ?", profile="recherche"))
    voiced = wait(tasks.TASKS[tools.run_tool("delegate_to_claude", {
        "title": "Actus", "prompt": "les actus", "profile": "recherche"})["task_id"]])
    scheduler._fire({"kind": "task", "title": "Veille", "text": "veille web", "profile": "recherche"}, 0)
    routine = wait(next(t for t in tasks.TASKS.values() if t["origin"] == "routine"))
    for task in (web, voiced, routine):
        assert secret not in echo(task)["prompt"] and secret not in task["full_prompt"]
    # A local task does get it (the memory is there, it just stays out of the web).
    local = wait(tasks.create_task("Notes", "range mes notes", profile="lecture"))
    assert secret in echo(local)["prompt"]
    # A web task never resumes that session: its context holds the memory and what was read.
    out = tools.run_tool("delegate_to_claude", {"title": "Suite", "prompt": "et sur le web ?",
                                                "profile": "recherche", "continue_task": "latest"})
    latest = wait(tasks.TASKS[out["task_id"]])
    assert "--resume" not in echo(latest)["argv"] and latest["resumed_from"] is None
    assert out["note"] == latest["note"]  # the model is told it starts afresh
    follow = wait(tasks.create_task("Suite web", "cherche en ligne", profile="recherche",
                                    continue_task=local["id"]))
    assert "--resume" not in echo(follow)["argv"]
    assert follow["resumed_from"] is None and follow["note"]
    # Web after web still resumes; local after web too (it cannot send anything out).
    again = wait(tasks.create_task("Encore", "et demain ?", profile="recherche", continue_task=web["id"]))
    assert option(echo(again)["argv"], "--resume") == "sess-final"
    back = wait(tasks.create_task("Local", "compare", profile="lecture", continue_task=web["id"]))
    assert option(echo(back)["argv"], "--resume") == "sess-final"


# ---------------------------------------------------------------- 6. the kill switch

# Claude Code that ignores --tools: it announces the profile's tools plus the
# one named after INTERDIT:, starts a child of its own, then waits.
KILL_FAKE = r'''
import json, os, subprocess, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.300 (Claude Code)")
    sys.exit(0)
prompt = sys.stdin.read()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open("pids.txt", "a") as f:
    f.write(f"{os.getpid()} {child.pid}\n")
tools = args[args.index("--tools") + 1].split(",") if "--tools" in args else []
extra = prompt.split("INTERDIT:", 1)[1].split()[0]
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s", "tools": tools + [extra]}), flush=True)
time.sleep(60)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "s",
                  "result": "fini"}), flush=True)
'''


def dead(pid: int) -> bool:
    """Gone, or a zombie waiting for its parent to read its exit code."""
    if gone(pid):
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return False


@pytest.mark.parametrize("profile", ["recherche", "lecture"])
@pytest.mark.parametrize("forbidden", ["Bash", "PowerShell", "Write", "Edit", "mcp__x", "mcp__ares__remember"])
def test_kill_switch_stops_a_task_announcing_a_forbidden_tool_holds(tmp_path, monkeypatch, profile, forbidden):
    script = tmp_path / "kill_fake.py"
    script.write_text(KILL_FAKE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    started = time.time()
    task = wait(tasks.create_task("Test", f"INTERDIT:{forbidden}", profile=profile))
    assert task["status"] == "error"
    assert task["output"].startswith("Profil de sécurité non appliqué")
    assert task["sandbox_violation"] == [forbidden]
    assert time.time() - started < 15  # stopped, not left to run
    lines = (Path(config.WORKDIR) / "pids.txt").read_text().split("\n")
    runs = [line.split() for line in lines if line.strip()]
    assert len(runs) == 1  # never retried, with or without a flag
    end = time.time() + 10
    while not all(dead(int(pid)) for pid in runs[0]) and time.time() < end:
        time.sleep(0.05)
    assert all(dead(int(pid)) for pid in runs[0]), runs  # claude and what it started
    assert task["id"] not in tasks.PROCS


def test_kill_switch_spares_the_tools_a_profile_allows_holds():
    assert tasks.violations("recherche", ["WebSearch", "WebFetch", "TodoWrite"]) == []
    assert tasks.violations("lecture", ["Read", "Glob", "Grep"]) == []
    for name in sorted(tasks.FORBIDDEN) + ["mcp__a__b"]:
        assert tasks.violations("recherche", ["WebSearch", name]) == [name]
        assert tasks.violations("lecture", ["Read", name]) == [name]
        assert tasks.violations(None, [name]) == [name]  # no profile is lecture
        assert tasks.violations("complet", [name]) == []


# Announces only the profile's tools, then uses the one named after INTERDIT: anyway.
MIDWAY_FAKE = r'''
import json, sys, time
args = sys.argv[1:]
prompt = sys.stdin.read()
tools = args[args.index("--tools") + 1].split(",") if "--tools" in args else []
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s", "tools": tools}), flush=True)
used = prompt.split("INTERDIT:", 1)[1].split()[0]
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t1", "name": used, "input": {"command": "dir"}}]}}), flush=True)
time.sleep(60)
'''


@pytest.mark.parametrize("profile", ["recherche", "lecture"])
@pytest.mark.parametrize("forbidden", ["Bash", "PowerShell", "Write", "mcp__x"])
def test_kill_switch_stops_a_forbidden_tool_used_without_being_announced_holds(
        tmp_path, monkeypatch, profile, forbidden):
    script = tmp_path / "midway_fake.py"
    script.write_text(MIDWAY_FAKE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    started = time.time()
    task = wait(tasks.create_task("Test", f"INTERDIT:{forbidden}", profile=profile))
    assert task["status"] == "error"
    assert task["output"].startswith("Profil de sécurité non appliqué")
    assert task["sandbox_violation"] == [forbidden]
    assert time.time() - started < 15


# Reports whether JARVIS's OpenAI key reached it.
ENV_FAKE = r'''
import json, os, sys
sys.stdin.read()
seen = {k: v for k, v in os.environ.items() if k.upper() == "OPENAI_API_KEY"}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "s",
                  "result": json.dumps(seen)}), flush=True)
'''


@pytest.mark.parametrize("profile", ["recherche", "lecture", "complet"])
def test_claude_tasks_never_see_the_openai_key_holds(tmp_path, monkeypatch, profile):
    script = tmp_path / "env_fake.py"
    script.write_text(ENV_FAKE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-stay-in-jarvis")  # as load_env() leaves it
    task = wait(tasks.create_task("Env", "x", profile=profile))
    assert task["status"] == "done"
    assert task["output"] == "{}"
    assert "sk-must-stay-in-jarvis" not in task["output"]


def test_a_typed_task_never_gets_full_access_without_the_card_holds(fake_claude, client):
    for profile in ("complet", "complet "):
        r = client.post("/api/tasks", json={"prompt": "supprime mes fichiers", "profile": profile})
        if tasks.normalize_profile(profile) == "complet":
            assert r.status_code == 400 and "confirmation" in r.json()["detail"]
        else:  # anything that is not exactly complet is lecture
            assert r.status_code == 200 and r.json()["profile"] == "lecture"
    assert not [t for t in tasks.TASKS.values() if t["profile"] == "complet"]
