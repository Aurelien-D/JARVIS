"""Claude Code sessions running in the background.

Each task is one `claude -p` process. The prompt goes in through stdin, never
on the command line: on Windows `claude` is often a .cmd script, and cmd.exe
would reinterpret quotes, & or % inside a spoken prompt. Output is streamed
as JSON lines, which gives live progress (each tool Claude uses) and the
session id needed to continue that same session later.

Safety: every task runs under a profile. recherche and lecture are allowlists
of built-in tools (--tools) with every MCP tool denied, started in safe mode
(no hooks, plugins or CLAUDE.md); the tools Claude Code announces at start-up
are checked again and a task that got more than its profile is killed.
complet has every tool, in auto mode and only after monsieur's "oui"
(confirm.py), with deny rules that keep it out of JARVIS's own files.
"""
import atexit
import contextlib
import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from . import config, events, memory, store, usage

HISTORY_FILE = "tasks.json"
STATE_FILE = "state.json"  # shared with the scheduler; the health check reads our flags there
HISTORY_SIZE = 30
MAX_OUTPUT = 20000
LOG_SIZE = 60
MAX_FILES = 30
ACTIVE = ("running", "en_file")  # not over yet

TASKS: dict = {}
PROCS: dict = {}  # task_id -> Popen, kept out of TASKS so tasks stay JSON-safe
ON_FINISH: list = []  # fn(task), called once a task is over (confirm.py: approval requests)
_lock = threading.RLock()

# What each profile may use. --tools only restricts built-in tools: MCP tools
# are denied separately (deny_rules). The voice model picks the narrowest.
PROFILES = {
    # Web research: no local files, no commands. A malicious page read along
    # the way finds nothing to steal and no way to act.
    "recherche": {"tools": "WebSearch,WebFetch"},
    # Local analysis: reads files but can't change them, run commands or go
    # online, so nothing it reads can be sent out.
    "lecture": {"tools": "Read,Glob,Grep"},
    # Everything: files, commands, web. Asked for with a confirmation.
    "complet": {"tools": None},
}
DEFAULT_PROFILE = "lecture"

# Tools that act on the PC or reach out. In recherche or lecture, Claude Code
# announcing one of them at start-up means --tools was not applied (an old
# version, a new default): the task is stopped before it does anything.
FORBIDDEN = {"Bash", "PowerShell", "Monitor", "Write", "Edit", "NotebookEdit", "Workflow",
             "Agent", "Skill", "RemoteTrigger", "PushNotification", "SendUserFile",
             "MultiEdit", "REPL", "Task"}  # older or sibling names of the same powers

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
_TOOL_NAME = re.compile(r"[A-Za-z][\w-]*")  # a bare tool name, never a rule or a flag


class T:
    """French messages (the same wording as static/js/strings-fr.js)."""
    claude_missing = ("Claude Code est introuvable. Installez-le : ouvrez PowerShell et tapez "
                      "irm https://claude.ai/install.ps1 | iex")
    claude_logged_out = ("Claude Code n'est pas connecté : ouvrez un terminal, tapez claude "
                         "et connectez-vous.")
    sandbox = ("Profil de sécurité non appliqué par cette version de Claude Code : tâche "
               "arrêtée. Mettez Claude Code à jour (claude update).")
    too_old = ("Cette version de Claude Code ne connaît pas l'option {option} : "
               "mettez-la à jour (claude update).")
    budget = "Plafond du jour atteint ({amount}). Modifiable dans Réglages › Coûts."
    empty = "Consigne vide : dites ce que Claude doit faire."
    interrupted = "JARVIS a été fermé pendant la tâche."
    workdir = "Dossier de travail introuvable : {path}"
    timeout = "Délai dépassé : la tâche a été arrêtée au bout de {minutes} min."
    cancelled = "Annulée par monsieur."
    approval = "Approuvé par monsieur : exécute maintenant l'action refusée."
    nothing_to_approve = "Rien à approuver pour cette tâche."
    queued = "En file d'attente…"
    no_web_resume = ("Une recherche web ne reprend pas une session qui a eu accès à vos fichiers : "
                     "nouvelle session.")
    starting = "Démarrage…"


MISSING_CLAUDE = T.claude_missing  # the name older callers know

# error subtypes of the final result (they carry errors: string[], no result)
RESULT_ERRORS = {
    "error_max_turns": "Claude a atteint le nombre maximal d'étapes sans terminer.",
    "error_max_budget_usd": "Budget de la tâche atteint ({budget}) : Claude s'est arrêté avant la fin.",
    "error_during_execution": "Claude Code a rencontré une erreur pendant la tâche.",
}
# system/api_retry `error` values
RETRY_ERRORS = {
    "authentication_failed": "connexion refusée",
    "billing_error": "problème de facturation",
    "rate_limit": "limite d'utilisation atteinte",
    "overloaded": "serveurs surchargés",
    "model_not_found": "modèle introuvable",
    "server_error": "erreur du serveur",
    "invalid_request": "requête refusée",
}
STATUS_FR = {"done": "terminée", "cancelled": "annulée", "error": "en échec",
             "interrompue": "interrompue"}
_AUTH_ERROR = re.compile(r"authentication_failed|invalid api key|please run /login|"
                         r"not logged in|oauth token has expired", re.I)

# Hardening flags that can heal themselves: setting name and state.json key.
_HEALABLE = {"--safe-mode": ("SAFE_MODE", "safe_mode_broken"),
             "--restricted": ("RESTRICTED", "restricted_broken")}

_UNSAVED = ("full_prompt", "timed_out", "voice_session")
_PRIVATE = _UNSAVED + ("log",)  # the log has its own route: not in every live update


def public(task: dict) -> dict:
    return {k: v for k, v in task.items() if k not in _PRIVATE and not k.startswith("_")}


def _saved(task: dict) -> dict:
    return {k: v for k, v in task.items() if k not in _UNSAVED and not k.startswith("_")}


def normalize_profile(profile) -> str:
    """A missing or unknown profile reads files only: never more than asked."""
    return profile if isinstance(profile, str) and profile in PROFILES else DEFAULT_PROFILE


# ---------------------------------------------------------------- command line

def claude_command() -> list:
    # shutil.which honours PATHEXT, so this also finds claude.cmd on Windows.
    exe = shutil.which("claude")
    if not exe:
        raise FileNotFoundError("claude")
    return [exe]


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if config.IS_WINDOWS else {}


_version = {"cmd": None, "value": None, "at": 0.0}


def claude_version(refresh: bool = False):
    """The installed Claude Code version as a tuple, e.g. (2, 1, 295); None if unknown.

    `claude --version` runs once and is cached (an unreadable answer for 10 min),
    since flags such as --permission-prompts depend on it.
    """
    try:
        cmd = tuple(claude_command())
    except FileNotFoundError:
        return None
    with _lock:
        if (not refresh and _version["cmd"] == cmd
                and (_version["value"] or time.time() - _version["at"] < 600)):
            return _version["value"]
    value = None
    try:
        out = subprocess.run([*cmd, "--version"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=10, stdin=subprocess.DEVNULL,
                             **_no_window()).stdout
        found = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
        value = tuple(int(n) for n in found.groups()) if found else None
    except (OSError, subprocess.SubprocessError):
        value = None
    with _lock:
        _version.update(cmd=cmd, value=value, at=time.time())
    return value


def version_text() -> str:
    """The version for people (health check, settings): '2.1.295' or 'inconnue'."""
    version = claude_version()
    return ".".join(map(str, version)) if version else "inconnue"


def _at_least(*minimum) -> bool:
    version = claude_version()
    return version is not None and tuple(version) >= minimum


def posix(path) -> str:
    """A path the way Claude Code's permission rules see it. On Windows they are
    normalised to POSIX form first: C:\\Users\\X becomes /c/Users/X."""
    text = str(path)
    drive = re.match(r"^([A-Za-z]):(?:[\\/]|$)(.*)$", text)
    if drive:
        rest = re.sub(r"[\\/]+", "/", drive.group(2)).strip("/")  # a doubled separator counts once
        return f"/{drive.group(1).lower()}/{rest}".rstrip("/")
    if config.IS_WINDOWS:
        text = text.replace("\\", "/")
    return text.rstrip("/") or "/"


def _rule_path(path) -> str:
    return "/" + posix(path)  # '//c/Users/X': an absolute path in a permission rule


def _mcp_servers() -> list:
    """The servers the extra MCP file declares, as Claude Code names them in a
    tool (mcp__<server>__<tool>: other characters become '_'); [] if unreadable."""
    path = (config.MCP_CONFIG or "").strip()
    if not path:
        return []
    try:
        data = json.loads(Path(os.path.expanduser(path)).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    return [re.sub(r"[^A-Za-z0-9_-]", "_", name)[:100] for name in servers if isinstance(name, str) and name]


def deny_rules(profile: str) -> list:
    """--disallowedTools rules for a profile. Deny beats allow in every mode.

    They only cover Claude's own file tools: a script or command run by
    Claude (complet only) can still write anywhere. In complet they are guard
    rails against mistakes and planted instructions, not a sandbox; the
    confirmation and auto mode do the rest.
    """
    root, data = _rule_path(config.ROOT), _rule_path(config.DATA_DIR)
    if profile == "recherche":
        return ["mcp__*"]
    if profile == "lecture":
        # JARVIS's secrets and its data (memory, journal, the window's browser profile).
        return ["mcp__*", f"Read({root}/.env)", f"Read({data}/**)", f"Read({data}/app-window*/**)"]
    # complet: never A.R.E.S's remember (it writes into A.R.E.S's own prompt),
    # under whatever name the MCP file of Réglages › Claude Code gives it too,
    # never JARVIS's code, data, nor Claude Code's own settings, hooks and MCP list.
    rules = [f"mcp__{name}__remember" for name in dict.fromkeys([config.ARES_MCP_NAME, *_mcp_servers()])]
    for target in (root, data):
        rules += [f"Edit({target}/**)", f"Write({target}/**)"]
    rules += ["Edit(~/.claude/**)", "Write(~/.claude/**)", "Edit(~/.mcp.json)",
              "Write(~/.mcp.json)", f"Read({root}/.env)"]
    return rules


def _permission_mode() -> str:
    mode = (config.PERMISSION_MODE or "").strip()
    return "" if mode.lower() in ("", "off") else mode


def _setting(flag: str) -> str:
    return str(getattr(config, _HEALABLE[flag][0], "auto") or "auto").strip().lower()


def hardening(profile: str) -> list:
    """Flags that keep hooks, plugins, CLAUDE.md and project settings out of
    recherche and lecture (hooks ignore --tools). 'auto' drops a flag that this
    Claude Code turned out not to support; 'on' always sends it; 'off' never."""
    if normalize_profile(profile) == "complet":
        return []
    flags = []
    safe = _setting("--safe-mode")
    if safe == "on" or (safe != "off" and not _broken("--safe-mode")):
        flags.append("--safe-mode")
    # Not for lecture: --restricted confines file tools to the working
    # directory, and lecture exists to read monsieur's files where they are.
    # It also refuses bypassPermissions, which monsieur may have chosen.
    restricted = _setting("--restricted")
    if (profile == "recherche" and restricted != "off"
            and _permission_mode() != "bypassPermissions"
            and (restricted == "on" or (not _broken("--restricted") and _at_least(2, 1, 248)))):
        flags.append("--restricted")
    return flags


def build_command(profile: str, model: str = "", resume: str | None = None, *,
                  allowed: list | None = None, flags: list | None = None) -> list:
    profile = normalize_profile(profile)
    cmd = claude_command() + ["-p", "--output-format", "stream-json", "--verbose"]
    mode = _permission_mode()
    if mode:
        cmd += ["--permission-mode", mode]
        # Nobody is there to answer a prompt: deny at once instead of letting
        # Claude ask again (needs 2.1.259+).
        if mode in ("auto", "dontAsk") and _at_least(2, 1, 259):
            cmd += ["--permission-prompts", "none"]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd += ["--resume", resume]
    tools = PROFILES[profile]["tools"]
    if tools:
        cmd += ["--tools", tools]
    for rule in deny_rules(profile):  # one argument per rule: paths may hold spaces
        cmd += ["--disallowedTools", rule]
    if allowed:
        cmd += ["--allowedTools", ",".join(allowed)]
    cmd += hardening(profile) if flags is None else flags
    if config.TASK_BUDGET_USD > 0:
        cmd += ["--max-budget-usd", str(config.TASK_BUDGET_USD)]
    # Extra MCP servers only where MCP tools are allowed: elsewhere they would
    # start for nothing (and a stdio server is a process of its own).
    if config.MCP_CONFIG and profile == "complet":
        cmd += ["--mcp-config", config.MCP_CONFIG]
    return cmd


def violations(profile: str, tools) -> list:
    """Tools Claude Code announced at start-up that this profile must not have."""
    if normalize_profile(profile) == "complet":
        return []
    return sorted({t for t in tools if isinstance(t, str) and (t in FORBIDDEN or t.startswith("mcp__"))})


# ---------------------------------------------------------------- processes

# JARVIS's own secrets (.env is copied into os.environ): a full-access task
# running commands could otherwise read them and show them in its output.
SECRET_ENV = ("OPENAI_API_KEY",)


def child_env() -> dict:
    return {k: v for k, v in os.environ.items() if k.upper() not in SECRET_ENV}


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


_job = None  # Windows Job Object holding every claude process


def _job_assign(proc) -> bool:
    """Windows: put claude in a Job Object that is killed with JARVIS, so a
    crash or a killed JARVIS never leaves Claude sessions running on their own.
    Best effort: the children claude.cmd already started may escape it."""
    global _job
    if not config.IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsage", ctypes.c_size_t),
                        ("PeakJobMemoryUsage", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                                wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        if _job is None:
            job = k32.CreateJobObjectW(None, None)
            info = ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return False  # 9 = JobObjectExtendedLimitInformation
            _job = job
        return bool(k32.AssignProcessToJobObject(_job, int(proc._handle)))
    except Exception:  # noqa: BLE001 - only a safety net: the task runs without it
        return False


def _workdir() -> str:
    return os.path.expanduser(str(config.WORKDIR))


def _logged_out() -> bool:
    """`claude auth status` exits with 1 when nobody is logged in."""
    try:
        r = subprocess.run(claude_command() + ["auth", "status"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10,
                           stdin=subprocess.DEVNULL, **_no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 1


# ---------------------------------------------------------------- self-healing flags

def _state() -> dict:
    state = store.load(STATE_FILE, {})
    return state if isinstance(state, dict) else {}


def _broken(flag: str) -> bool:
    """This flag broke the runs of this very Claude Code version (an update tries again)."""
    seen = _state().get(_HEALABLE[flag][1])
    return bool(seen) and seen in (True, version_text())


def _mark_broken(flags: list):
    with store.LOCK:
        state = _state()
        for flag in flags:
            state[_HEALABLE[flag][1]] = version_text()
        store.save(STATE_FILE, state)
    logging.warning("JARVIS: options ignorées par cette version de Claude Code : %s", ", ".join(flags))


# ---------------------------------------------------------------- descriptions

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
    if name in ("Bash", "PowerShell"):
        return f"Commande : {args.get('description') or args.get('command', '')}"
    if name in ("Task", "Agent"):
        return f"Sous-agent : {args.get('description', '')}"
    if name == "TodoWrite":
        return "Planification des étapes"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"Connecteur {parts[1]} : {parts[-1]}"
    return name


def _money(usd: float) -> str:
    return f"{usd:.2f} $".replace(".", ",")


# ---------------------------------------------------------------- lifecycle

def create_task(title: str, prompt: str, profile: str | None = DEFAULT_PROFILE,
                complexity: str = "normale", continue_task: str | None = None,
                origin: str = "voix", voice_session: str | None = None,
                allowed_tools: list | None = None, *, via: str = "pc") -> dict:
    """Start a task (or queue it when MAX_CONCURRENT_TASKS already run).

    origin: the channel (voix, clavier, routine, approbation, briefing).
    voice_session: the voice session that asked (confirm.py ties approvals to it).
    allowed_tools: approval of denied tools (approve() only).
    via: who asked, the origin string of remote.Caller ("pc", "app:d_…", "siri:k_…").
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError(T.empty)
    _check_budget()
    profile = normalize_profile(profile)
    complexity = complexity if complexity in config.MODELS else "normale"
    with contextlib.suppress(OSError):  # the run explains a folder it can't use
        Path(_workdir()).mkdir(parents=True, exist_ok=True)
    task = {
        "id": uuid.uuid4().hex[:8], "title": (title or "Tâche").strip()[:80],
        "prompt": prompt, "profile": profile, "complexity": complexity,
        "model": config.MODELS[complexity], "origin": origin, "via": str(via or "pc"),
        "status": "running", "output": "", "progress": T.starting, "steps": 0,
        "started": time.time(), "ended": None,
        "session_id": None, "resume": None, "resumed_from": None, "cost_usd": None,
        "duration_ms": None, "num_turns": None, "permission_denials": [], "files": [],
        "log": [], "voice_session": voice_session,
    }
    if allowed_tools:
        task["allowed_tools"] = list(allowed_tools)
    if continue_task:
        prev = _find_resumable(continue_task)
        if prev and profile == "recherche" and normalize_profile(prev.get("profile")) != "recherche":
            # A web task never resumes a session that saw monsieur's files or
            # memory: a page met along the way could have it sent out.
            task["note"] = T.no_web_resume
        elif prev:
            task["resume"], task["resumed_from"] = prev["session_id"], prev["id"]
        else:
            task["note"] = "Aucune tâche précédente à reprendre : nouvelle session."
    # An approval resumes a session that already has the context.
    task["full_prompt"] = prompt if allowed_tools else _with_memory(prompt, profile)
    with _lock:
        TASKS[task["id"]] = task
    with _slots:
        if not _waiting and len(_active) < _max_slots():
            _active.add(task["id"])
        else:
            _waiting.append(task["id"])
            task.update(status="en_file", progress=T.queued)
    _log(task, task["progress"])
    events.publish("task", public(task))
    threading.Thread(target=_run, args=(task,), daemon=True).start()
    return task


def _over_budget() -> str:
    """The daily cap's refusal in French, or '' when a task may start."""
    cap = config.DAILY_BUDGET_USD
    if cap and cap > 0:
        try:
            # One cap for the voice and the tasks (Réglages › Coûts, usage.py).
            spent = float(usage.claude_spent_today() or 0) + float(usage.realtime_spent_today() or 0)
        except Exception:  # noqa: BLE001 - a broken counter must not block every task
            logging.exception("JARVIS: dépense du jour illisible")
            return ""
        if spent >= cap:
            return T.budget.format(amount=_money(cap))
    return ""


def _check_budget():
    refused = _over_budget()
    if refused:
        raise ValueError(refused)


def _find_resumable(ref: str):
    with _lock:
        done = [t for t in TASKS.values() if t.get("session_id") and t["status"] not in ACTIVE]
    if ref in ("latest", "last", "-"):
        return max(done, key=lambda t: t["started"], default=None)
    return next((t for t in done if t["id"] == ref), None)


def _with_memory(prompt: str, profile: str = DEFAULT_PROFILE) -> str:
    # Never into a web task: a page read along the way could steer Claude into
    # sending monsieur's private facts out (a search query is enough).
    if profile == "recherche":
        return prompt
    facts = memory.as_text(1500)
    if not facts:
        return prompt
    return (f"{prompt}\n\n---\nContexte sur l'utilisateur (mémoire de JARVIS, "
            f"à n'utiliser que si c'est utile) :\n{facts}")


# ---------------------------------------------------------------- concurrency

_slots = threading.Condition()
_waiting: deque = deque()  # queued task ids, first come first served
_active: set = set()       # task ids holding a slot


def _max_slots() -> int:
    try:
        return max(1, int(config.MAX_CONCURRENT_TASKS))
    except (TypeError, ValueError):
        return 1


def _wait_for_slot(task: dict) -> bool:
    """Block a queued task until a slot frees; False if it was stopped meanwhile."""
    with _slots:
        while task["id"] not in _active:
            if task["status"] not in ACTIVE:
                with contextlib.suppress(ValueError):
                    _waiting.remove(task["id"])
                _slots.notify_all()
                return False
            if _waiting and _waiting[0] == task["id"] and len(_active) < _max_slots():
                _waiting.popleft()
                _active.add(task["id"])
                break
            _slots.wait(timeout=1)
    with _lock:
        if task["status"] == "en_file":
            task.update(status="running", progress=T.starting)
            started = True
        else:
            started = False
    if started:
        _log(task, T.starting)
        events.publish("task", public(task))
    return task["status"] == "running"


def _release_slot(task: dict):
    with _slots:
        _active.discard(task["id"])
        with contextlib.suppress(ValueError):
            _waiting.remove(task["id"])
        _slots.notify_all()


# ---------------------------------------------------------------- running

@dataclass
class _Outcome:
    status: str
    output: str
    quick: bool = False         # failed before doing anything
    elapsed: float = 0.0
    unknown_option: str = ""    # "error: unknown option '--x'"
    auth_failed: bool = False
    final: bool = False         # never retried: cancelled, timed out, sandbox violation


def _run(task: dict):
    status, output = "error", ""
    queued = task["status"] == "en_file"
    try:
        if _wait_for_slot(task):
            # The cap reached while it waited its turn: not started either.
            refused = _over_budget() if queued else ""
            if refused:
                status, output = "error", refused
            else:
                status, output = _run_attempts(task)
    except Exception as exc:  # noqa: BLE001
        logging.exception("JARVIS: échec de la tâche %s", task["id"])
        status, output = "error", str(exc)
    try:
        _finish(task, status, output)
    finally:
        _release_slot(task)  # last: a free slot means this task is entirely over


def _run_attempts(task: dict):
    flags = hardening(task["profile"])
    healed: list = []  # flags dropped because this Claude Code refused them
    model = task["model"]
    model_retried = False
    while True:
        out = _attempt(task, model, flags)
        if healed and not (out.unknown_option or out.auth_failed):
            _mark_broken(healed)  # it works without them: remember it, the health check says so
            healed = []
        if out.status != "error" or out.final or task["status"] != "running":
            return out.status, out.output
        drop = _healable(out, flags)
        if drop:
            flags = [f for f in flags if f not in drop]
            healed += drop
            _progress(task, "Option de sécurité refusée par cette version de Claude Code : "
                            "nouvel essai sans elle…")
            continue
        if out.unknown_option:
            return "error", T.too_old.format(option=out.unknown_option)
        if out.auth_failed or (out.elapsed < 5 and _logged_out()):
            return "error", T.claude_logged_out
        # A model alias the account can't use (opus on some plans, say) fails
        # at once: retry on the account's default model rather than give up.
        if out.quick and model and not model_retried:
            model_retried = True
            model = task["model"] = ""
            _progress(task, "Nouvel essai avec le modèle par défaut du compte…")
            continue
        return "error", out.output


def _healable(out: _Outcome, flags: list) -> list:
    """Hardening flags to drop after a failure they may have caused (within 10 s)."""
    if out.elapsed >= 10:
        return []
    auto = [f for f in flags if f in _HEALABLE and _setting(f) == "auto"]
    if out.unknown_option:
        return [out.unknown_option] if out.unknown_option in auto else []
    return auto if out.auth_failed else []


def _attempt(task: dict, model: str, flags: list) -> _Outcome:
    """Run claude once."""
    try:
        cmd = build_command(task["profile"], model, task.get("resume"),
                            allowed=task.get("allowed_tools"), flags=flags)
    except FileNotFoundError:
        return _Outcome("error", T.claude_missing, final=True)
    task["_attempt"] = {"auth_retries": 0}
    began = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", cwd=_workdir(),
            env=child_env(), **_spawn_options(),
        )
    except FileNotFoundError:
        if not Path(_workdir()).is_dir():
            return _Outcome("error", T.workdir.format(path=_workdir()), final=True)
        return _Outcome("error", T.claude_missing, final=True)
    except OSError as exc:
        return _Outcome("error", f"Impossible de lancer Claude Code : {exc}", final=True)
    _job_assign(proc)
    with _lock:
        PROCS[task["id"]] = proc
        stopped = task["status"] != "running"
    if stopped:  # cancelled between two attempts
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

    elapsed = time.time() - began
    stderr = "".join(stderr_parts).strip()
    if task["status"] != "running":  # cancelled, or JARVIS is closing
        return _Outcome(task["status"], task["output"], final=True)
    if task.get("sandbox_violation"):
        _log(task, "Arrêt : outils interdits annoncés (" + ", ".join(task["sandbox_violation"]) + ")")
        return _Outcome("error", T.sandbox, final=True)
    if task.get("timed_out"):
        return _Outcome("error", T.timeout.format(minutes=max(1, round(config.TASK_TIMEOUT / 60))),
                        final=True)
    out_text = "\n".join(plain).strip()
    found = re.search(r"unknown option '?(--[\w-]+)", f"{stderr}\n{out_text}")
    unknown = found.group(1) if found else ""
    quick = elapsed < 20 and task["steps"] == 0
    auth = task["_attempt"]["auth_retries"] >= 2  # one refusal can be a token being renewed
    if result is not None:
        if result.get("is_error") or result.get("subtype") != "success":
            detail = _result_error(result, stderr)
            auth = auth or bool(_AUTH_ERROR.search(detail))
            return _Outcome("error", detail, quick, elapsed, unknown, auth)
        text = result.get("result") if isinstance(result.get("result"), str) else ""
        return _Outcome("done", text.strip() or "(aucune réponse)")
    if proc.returncode == 0 and out_text:
        return _Outcome("done", out_text)
    detail = stderr[:2000] or out_text or f"Claude Code s'est arrêté (code {proc.returncode})."
    auth = auth or bool(_AUTH_ERROR.search(detail))
    return _Outcome("error", detail, quick, elapsed, unknown, auth)


def _result_error(result: dict, stderr: str) -> str:
    """A failed result in French: what happened, then Claude's own words."""
    subtype = result.get("subtype") or ""
    text = result.get("result") if isinstance(result.get("result"), str) else ""
    errors = [str(e) for e in (result.get("errors") or []) if e]
    detail = (text or "").strip() or "; ".join(errors) or stderr[:500]
    known = RESULT_ERRORS.get(subtype)
    if known:
        known = known.format(budget=_money(config.TASK_BUDGET_USD))
        return f"{known} Détail : {detail[:1500]}" if detail else known
    return detail[:2000] or f"Claude Code s'est arrêté ({subtype or 'erreur inconnue'})."


def _on_stream_event(task: dict, event: dict):
    kind, subtype = event.get("type"), event.get("subtype")
    if kind == "system" and subtype == "init":
        task["session_id"] = event.get("session_id") or task["session_id"]
        task["mcp_servers"] = event.get("mcp_servers") or []
        tools = event.get("tools")
        bad = violations(task["profile"], tools) if isinstance(tools, list) else []
        if bad:  # the kill switch: this Claude Code did not apply the profile
            task["sandbox_violation"] = bad
            proc = PROCS.get(task["id"])
            if proc:
                kill_tree(proc)
    elif kind == "system" and subtype == "api_retry":
        error = str(event.get("error") or "")
        try:
            delay = max(1, math.ceil(float(event.get("retry_delay_ms") or 0) / 1000))
        except (TypeError, ValueError):
            delay = 1
        _progress(task, f"Claude indisponible ({RETRY_ERRORS.get(error, 'erreur temporaire')}), "
                        f"nouvel essai dans {delay} s…")
        if error == "authentication_failed":
            # One refusal can be a token being renewed; two mean nobody is logged
            # in, and waiting through every retry would only delay the answer.
            attempt = task.setdefault("_attempt", {"auth_retries": 0})
            attempt["auth_retries"] += 1
            if attempt["auth_retries"] >= 2:
                proc = PROCS.get(task["id"])
                if proc:
                    kill_tree(proc)
    elif kind == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name, args = block.get("name", ""), block.get("input") or {}
                bad = violations(task["profile"], [name])
                if bad and not task.get("sandbox_violation"):  # used without being announced
                    task["sandbox_violation"] = bad
                    proc = PROCS.get(task["id"])
                    if proc:
                        kill_tree(proc)
                task["steps"] += 1
                _track_write(task, block.get("id"), name, args)
                _progress(task, describe_tool(name, args))
            elif block.get("type") == "text" and block.get("text", "").strip():
                _progress(task, block["text"].strip().splitlines()[0])
    elif kind == "user":
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                _untrack_write(task, block.get("tool_use_id"))
    elif kind == "result":
        task["session_id"] = event.get("session_id") or task["session_id"]
        cost = event.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            task["cost_usd"] = round((task.get("cost_usd") or 0) + cost, 6)
        for key in ("duration_ms", "num_turns"):
            if event.get(key) is not None:
                task[key] = event[key]
        task["permission_denials"] = _denials(task, event.get("permission_denials"))
        return event
    return None


def _track_write(task: dict, tool_use_id, name: str, args: dict):
    """Files Claude creates or changes, for the task viewer (deduplicated, at most 30)."""
    path = args.get(WRITE_TOOLS.get(name, ""))
    if not isinstance(path, str) or not path:
        return
    task.setdefault("_writes", {})[tool_use_id or uuid.uuid4().hex] = path
    if path not in task["files"] and len(task["files"]) < MAX_FILES:
        task["files"].append(path)


def _untrack_write(task: dict, tool_use_id):
    """That write failed or was refused: the file was not written by it."""
    writes = task.get("_writes") or {}
    path = writes.pop(tool_use_id, None)
    if path and path not in writes.values() and path in task["files"]:
        task["files"].remove(path)


def _denials(task: dict, raw) -> list:
    """result.permission_denials: what Claude was not allowed to do (tool and a summary)."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name") or item.get("tool") or "")
        args = item.get("tool_input") if isinstance(item.get("tool_input"), dict) else {}
        if not name:
            continue
        out.append({"tool": name, "detail": describe_tool(name, args)[:160]})
        _untrack_write(task, item.get("tool_use_id"))  # a refused write wrote nothing
    return out[:20]


def _log(task: dict, line: str):
    line = " ".join(str(line).split())[:160]
    if not line:
        return
    log = task.setdefault("log", [])
    log.append({"t": round(time.time(), 1), "text": line})
    del log[:-LOG_SIZE]


def _progress(task: dict, line: str):
    if task["status"] != "running":
        return
    _log(task, line)
    task["progress"] = line[:160]
    events.publish("task", public(task))


def _on_timeout(task: dict, proc):
    task["timed_out"] = True
    kill_tree(proc)


def _finish(task: dict, status: str, output: str):
    # Counted before the task reads as finished: whoever sees it ended (a test,
    # the next task's budget check) sees its cost too.
    _record_cost(task)
    with _lock:
        if task["status"] in ("cancelled", "interrompue"):  # the cancel or the shutdown won the race
            status, output = task["status"], task["output"]
        _log(task, f"Fin : {STATUS_FR.get(status, status)}")  # before the status: it ends the log
        task.update(status=status, output=(output or "")[:MAX_OUTPUT],
                    ended=task.get("ended") or time.time(), progress="")
    _save_history()
    events.publish("task", public(task))
    for hook in list(ON_FINISH):
        try:
            hook(task)
        except Exception:  # noqa: BLE001 - a hook must not lose the task
            logging.exception("JARVIS: suite de tâche en échec")


def _record_cost(task: dict):
    add = getattr(usage, "add_claude", None)  # the daily counter, when there is one
    if add and task.get("cost_usd"):
        try:
            add(task["cost_usd"])
        except Exception:  # noqa: BLE001
            logging.exception("JARVIS: coût de la tâche non enregistré")


def cancel(task_id: str = "latest") -> dict:
    with _lock:
        if task_id in ("latest", "last", "-", ""):
            active = [t for t in TASKS.values() if t["status"] in ACTIVE]
            if not active:
                return {"ok": False, "error": "Aucune tâche en cours."}
            task = max(active, key=lambda t: t["started"])
        else:
            task = TASKS.get(task_id)
            if not task:
                return {"ok": False, "error": "Tâche inconnue."}
            if task["status"] not in ACTIVE:
                state = STATUS_FR.get(task["status"], task["status"])
                return {"ok": False, "error": f"La tâche est déjà {state}."}
        # Flag first so the runner's own ending doesn't overwrite it.
        task.update(status="cancelled", output=T.cancelled, ended=time.time(), progress="")
        proc = PROCS.get(task["id"])
    with _slots:
        _slots.notify_all()  # a queued task leaves the queue
    if proc and proc.poll() is None:
        kill_tree(proc)
    events.publish("task", public(task))
    return {"ok": True, "cancelled": task["id"], "title": task["title"]}


def approve(task_id: str) -> dict:
    """Resume a task's session with the tools Claude was denied, once monsieur
    agreed. Called only by confirm.py, after his "oui". Deny rules still win:
    an approval can never reach JARVIS's files or a profile's forbidden tools."""
    with _lock:
        task = TASKS.get(task_id)
    if not task or not task.get("session_id") or task["status"] in ACTIVE:
        raise ValueError(T.nothing_to_approve)
    names = sorted({d["tool"] for d in task.get("permission_denials") or []
                    if _TOOL_NAME.fullmatch(str(d.get("tool") or ""))})
    if not names:
        raise ValueError(T.nothing_to_approve)
    return create_task(task["title"], T.approval, profile=task["profile"],
                       complexity=task.get("complexity", "normale"), continue_task=task_id,
                       origin="approbation", voice_session=task.get("voice_session"),
                       allowed_tools=names, via=task.get("via") or "pc")


def shutdown():
    """JARVIS is closing: stop every running or queued task and keep it in the
    history as interrupted."""
    with _lock:
        stopped = [t for t in TASKS.values() if t["status"] in ACTIVE]
        for task in stopped:
            task.update(status="interrompue", output=T.interrupted, ended=time.time(), progress="")
        procs = [PROCS.get(t["id"]) for t in stopped]
    with _slots:
        _slots.notify_all()
    for proc in procs:
        if proc and proc.poll() is None:
            kill_tree(proc)
    _save_history()
    for task in stopped:
        events.publish("task", public(task))


@atexit.register
def _at_exit():
    # Ctrl+C or a crash: no orphan claude left behind (its own process group
    # would survive JARVIS on Linux and macOS).
    with _lock:
        busy = any(t["status"] in ACTIVE for t in TASKS.values())
    if busy:
        shutdown()


# ---------------------------------------------------------------- history

def list_tasks() -> list:
    with _lock:
        items = [public(t) for t in TASKS.values()]
    return sorted(items, key=lambda t: t["started"], reverse=True)


def running() -> list:
    """Tasks not over yet: running or queued."""
    return [t for t in list_tasks() if t["status"] in ACTIVE]


def task_log(task_id: str):
    """The last progress lines of a task, or None for an unknown task."""
    with _lock:
        task = TASKS.get(task_id)
        return None if task is None else list(task.get("log") or [])


def load_history():
    for task in store.load(HISTORY_FILE, []):
        if isinstance(task, dict) and task.get("id") and task.get("status") not in ACTIVE:
            TASKS.setdefault(task["id"], task)


def _save_history():
    with _lock:
        done = sorted((t for t in TASKS.values() if t["status"] not in ACTIVE),
                      key=lambda t: t["started"])
        for old in done[:-HISTORY_SIZE]:  # keep memory bounded on long uptimes
            TASKS.pop(old["id"], None)
        keep = [_saved(t) for t in done[-HISTORY_SIZE:]]
    with store.LOCK:
        store.save(HISTORY_FILE, keep)
