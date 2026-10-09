"""Claude Code sessions running in the background.

Each task is one `claude -p` process. The prompt goes in through stdin, never
on the command line: on Windows `claude` is often a .cmd script, and cmd.exe
would reinterpret quotes, & or % inside a spoken prompt. Output is streamed
as JSON lines, which gives live progress (each tool Claude uses) and the
session id needed to continue that same session later.
"""
import contextlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import config, events, memory, store

HISTORY_FILE = "tasks.json"
HISTORY_SIZE = 30
MAX_OUTPUT = 20000

TASKS: dict = {}
PROCS: dict = {}  # task_id -> Popen, kept out of TASKS so tasks stay JSON-safe
_lock = threading.RLock()

# Tools each profile denies (--disallowedTools). The voice model picks the
# narrowest profile that does the job.
PROFILES = {
    # Web research: no local files, no commands. A malicious page read along
    # the way finds nothing to steal and no way to act.
    "recherche": ["Bash", "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep"],
    # Local analysis: reads files but can't change them, run commands or go
    # online, so nothing it reads can be sent out.
    "lecture": ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"],
    # Everything: files, commands, web.
    "complet": [],
}

MISSING_CLAUDE = ("La commande 'claude' est introuvable. Installe Claude Code : "
                  "npm install -g @anthropic-ai/claude-code")
_PRIVATE = ("full_prompt", "timed_out")


def public(task: dict) -> dict:
    return {k: v for k, v in task.items() if k not in _PRIVATE}


def claude_command() -> list:
    # shutil.which honours PATHEXT, so this also finds claude.cmd on Windows.
    exe = shutil.which("claude")
    if not exe:
        raise FileNotFoundError("claude")
    return [exe]


def build_command(profile: str, model: str, resume: str | None) -> list:
    cmd = claude_command() + ["-p", "--output-format", "stream-json", "--verbose"]
    if config.PERMISSION_MODE and config.PERMISSION_MODE.lower() != "off":
        cmd += ["--permission-mode", config.PERMISSION_MODE]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    if config.MCP_CONFIG:
        cmd += ["--mcp-config", config.MCP_CONFIG]
    denied = PROFILES.get(profile, [])
    if denied:
        cmd += ["--disallowedTools", ",".join(denied)]
    return cmd


def _spawn_options() -> dict:
    if config.IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}  # own process group, so kill_tree reaches the children


def kill_tree(proc):
    """Kill a process and its children (claude.cmd spawns node)."""
    if config.IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)


def describe_tool(name: str, args: dict) -> str:
    """One readable progress line for a tool Claude is using."""
    def base(key):
        return Path(str(args.get(key, ""))).name

    if name == "WebSearch":
        return f"Recherche web : {args.get('query', '')}"
    if name == "WebFetch":
        return f"Lecture d'une page : {args.get('url', '')}"
    if name == "Read":
        return f"Lecture : {base('file_path')}"
    if name in ("Write", "Edit", "MultiEdit"):
        return f"Écriture : {base('file_path')}"
    if name == "NotebookEdit":
        return f"Écriture : {base('notebook_path')}"
    if name in ("Glob", "Grep"):
        return f"Recherche dans les fichiers : {args.get('pattern', '')}"
    if name == "Bash":
        return f"Commande : {args.get('description') or args.get('command', '')}"
    if name in ("Task", "Agent"):
        return f"Sous-agent : {args.get('description', '')}"
    if name == "TodoWrite":
        return "Planification des étapes"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"Connecteur {parts[1]} : {parts[-1]}"
    return name


# ---------------------------------------------------------------- lifecycle

def create_task(title: str, prompt: str, profile: str = "complet",
                complexity: str = "normale", continue_task: str | None = None,
                origin: str = "voix") -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("consigne vide")
    profile = profile if profile in PROFILES else "complet"
    complexity = complexity if complexity in config.MODELS else "normale"
    task = {
        "id": uuid.uuid4().hex[:8], "title": (title or "Tâche").strip()[:80],
        "prompt": prompt, "profile": profile, "complexity": complexity,
        "model": config.MODELS[complexity], "origin": origin,
        "status": "running", "output": "", "progress": "Démarrage…", "steps": 0,
        "started": time.time(), "ended": None,
        "session_id": None, "resume": None, "resumed_from": None, "cost_usd": None,
    }
    if continue_task:
        prev = _find_resumable(continue_task)
        if prev:
            task["resume"], task["resumed_from"] = prev["session_id"], prev["id"]
        else:
            task["note"] = "Aucune tâche précédente à reprendre : nouvelle session."
    task["full_prompt"] = _with_memory(prompt)
    with _lock:
        TASKS[task["id"]] = task
    events.publish("task", public(task))
    threading.Thread(target=_run, args=(task,), daemon=True).start()
    return task


def _find_resumable(ref: str):
    with _lock:
        done = [t for t in TASKS.values() if t.get("session_id") and t["status"] != "running"]
    if ref in ("latest", "last", "-"):
        return max(done, key=lambda t: t["started"], default=None)
    return next((t for t in done if t["id"] == ref), None)


def _with_memory(prompt: str) -> str:
    facts = memory.as_text(1500)
    if not facts:
        return prompt
    return (f"{prompt}\n\n---\nContexte sur l'utilisateur (mémoire de JARVIS, "
            f"à n'utiliser que si c'est utile) :\n{facts}")


def _run(task: dict):
    try:
        status, output, quick_failure = _attempt(task, task["model"])
        # A model alias the account can't use (opus on some plans, say) fails
        # at once: retry on the account's default model rather than give up.
        if status == "error" and quick_failure and task["model"]:
            _progress(task, "Nouvel essai avec le modèle par défaut du compte…")
            task["model"] = ""
            status, output, _ = _attempt(task, "")
    except Exception as exc:  # noqa: BLE001
        status, output = "error", str(exc)
    _finish(task, status, output)


def _attempt(task: dict, model: str):
    """Run claude once. Returns (status, output, failed_before_doing_anything)."""
    try:
        cmd = build_command(task["profile"], model, task.get("resume"))
    except FileNotFoundError:
        return "error", MISSING_CLAUDE, False
    began = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", cwd=config.WORKDIR,
            **_spawn_options(),
        )
    except FileNotFoundError:
        if not Path(config.WORKDIR).is_dir():
            return "error", f"Dossier de travail introuvable : {config.WORKDIR}", False
        return "error", MISSING_CLAUDE, False
    with _lock:
        PROCS[task["id"]] = proc
        cancelled = task["status"] == "cancelled"
    if cancelled:  # cancelled between two attempts
        kill_tree(proc)

    watchdog = threading.Timer(config.TASK_TIMEOUT, _on_timeout, args=(task, proc))
    watchdog.daemon = True
    watchdog.start()
    stderr_parts: list = []
    reader = threading.Thread(target=lambda: stderr_parts.append(proc.stderr.read()), daemon=True)
    reader.start()
    try:
        proc.stdin.write(task["full_prompt"])
        proc.stdin.close()
    except OSError:
        pass  # the process already died; its error output says why

    result, plain = None, []
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            plain.append(line)
            continue
        if isinstance(event, dict):
            result = _on_stream_event(task, event) or result
    proc.wait()
    watchdog.cancel()
    reader.join(timeout=5)
    with _lock:
        PROCS.pop(task["id"], None)

    stderr = "".join(stderr_parts).strip()
    quick = time.time() - began < 20 and task["steps"] == 0
    if task["status"] == "cancelled":
        return "cancelled", task["output"], False
    if task.get("timed_out"):
        return "error", "Timeout : la session Claude a dépassé la limite de temps.", False
    if result is not None:
        text = (result.get("result") or "").strip()
        if result.get("is_error") or result.get("subtype") != "success":
            detail = text or stderr[:2000] or f"Claude Code s'est arrêté ({result.get('subtype')})."
            return "error", detail, quick
        return "done", text or "(aucune réponse)", False
    out = "\n".join(plain).strip()
    if proc.returncode == 0 and out:
        return "done", out, False
    return "error", stderr[:2000] or out or f"Claude Code s'est arrêté (code {proc.returncode}).", quick


def _on_stream_event(task: dict, event: dict):
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        task["session_id"] = event.get("session_id") or task["session_id"]
    elif kind == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                task["steps"] += 1
                _progress(task, describe_tool(block.get("name", ""), block.get("input") or {}))
            elif block.get("type") == "text" and block.get("text", "").strip():
                _progress(task, block["text"].strip().splitlines()[0])
    elif kind == "result":
        task["session_id"] = event.get("session_id") or task["session_id"]
        task["cost_usd"] = event.get("total_cost_usd")
        return event
    return None


def _progress(task: dict, line: str):
    if task["status"] != "running":
        return
    task["progress"] = line[:160]
    events.publish("task", public(task))


def _on_timeout(task: dict, proc):
    task["timed_out"] = True
    kill_tree(proc)


def _finish(task: dict, status: str, output: str):
    with _lock:
        if task["status"] == "cancelled":  # the cancel won the race
            status, output = "cancelled", task["output"]
        task.update(status=status, output=(output or "")[:MAX_OUTPUT],
                    ended=time.time(), progress="")
    _save_history()
    events.publish("task", public(task))


def cancel(task_id: str = "latest") -> dict:
    with _lock:
        if task_id in ("latest", "last", "-", ""):
            running = [t for t in TASKS.values() if t["status"] == "running"]
            if not running:
                return {"ok": False, "error": "Aucune tâche en cours."}
            task = max(running, key=lambda t: t["started"])
        else:
            task = TASKS.get(task_id)
            if not task:
                return {"ok": False, "error": "Tâche inconnue."}
            if task["status"] != "running":
                return {"ok": False, "error": f"La tâche est déjà {task['status']}."}
        # Flag first so the runner's own ending doesn't overwrite it.
        task.update(status="cancelled", output="Annulée par l'utilisateur.",
                    ended=time.time(), progress="")
        proc = PROCS.get(task["id"])
    if proc and proc.poll() is None:
        kill_tree(proc)
    events.publish("task", public(task))
    return {"ok": True, "cancelled": task["id"], "title": task["title"]}


# ---------------------------------------------------------------- history

def list_tasks() -> list:
    with _lock:
        items = [public(t) for t in TASKS.values()]
    return sorted(items, key=lambda t: t["started"], reverse=True)


def running() -> list:
    return [t for t in list_tasks() if t["status"] == "running"]


def load_history():
    for task in store.load(HISTORY_FILE, []):
        if isinstance(task, dict) and task.get("id") and task.get("status") != "running":
            TASKS.setdefault(task["id"], task)


def _save_history():
    with _lock:
        done = sorted((t for t in TASKS.values() if t["status"] != "running"),
                      key=lambda t: t["started"])
        for old in done[:-HISTORY_SIZE]:  # keep memory bounded on long uptimes
            TASKS.pop(old["id"], None)
        keep = [public(t) for t in done[-HISTORY_SIZE:]]
    with store.LOCK:
        store.save(HISTORY_FILE, keep)
