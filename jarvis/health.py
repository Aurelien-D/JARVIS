"""Bilan de santé: everything that keeps JARVIS from working, found before the
first click (the onboarding) and on demand (Réglages).

GET /api/health returns [{id, ok, level, title_fr, message_fr, fix_fr}]:
level is ok, info, warning or error; ok is false when something must be
fixed. Checks run side by side, each bounded in time, and the slow ones are
cached (the OpenAI key 10 minutes, Claude Code's login 5 minutes) unless
monsieur asks to check again. Nothing here spends model quota: the key is
tested by reading the model's description, which is free. A.R.E.S is asked
for real (ares.reachable), and on Windows the shell's state is read: the
global hotkey, the tray icon, the start with Windows (shell.py, desktop.py).
"""
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from . import config, desktop, store, tasks

router = APIRouter()

STATE_FILE = "state.json"  # shared with tasks and the scheduler: 'onboarded' lives there
MODEL_URL = "https://api.openai.com/v1/models/{model}"
CLAUDE_MIN = (2, 1, 259)   # --permission-prompts none: unattended tasks are denied, never stuck
OPENAI_TTL = 600           # a verdict on the key
OPENAI_RETRY_TTL = 60      # a network failure or an OpenAI outage
AUTH_TTL = 300          # `claude auth status` starts node: not at every page load
CHECK_TIMEOUT = 20         # no check may hold the page longer
LOW_DISK = 200 * 1024 * 1024

REALTIME_END = date(2027, 1, 20)
TRANSCRIBE_END = date(2027, 2, 26)
OLD_TRANSCRIBE = ("whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe")

AUTH_METHODS = {"claude.ai": "compte claude.ai", "oauth_token": "jeton OAuth",
                "api_key": "clé API Anthropic", "api_key_helper": "script de clé API",
                "third_party": "fournisseur tiers"}

_cache: dict = {}  # name -> (signature, at, ttl, result)
_cache_lock = threading.Lock()


def item(id_: str, level: str, title: str, message: str, fix: str = "") -> dict:
    return {"id": id_, "ok": level in ("ok", "info"), "level": level, "title_fr": title,
            "message_fr": message, "fix_fr": fix}


def forget(name: str | None = None):
    """Drop a cached verdict (the key was just replaced), or all of them."""
    with _cache_lock:
        if name is None:
            _cache.clear()
        else:
            _cache.pop(name, None)


def _cached(name: str, signature: str, refresh: bool):
    with _cache_lock:
        hit = _cache.get(name)
    if hit and not refresh and hit[0] == signature and time.time() - hit[1] < hit[2]:
        return hit[3]
    return None


def _remember(name: str, signature: str, ttl: float, result):
    with _cache_lock:
        _cache[name] = (signature, time.time(), ttl, result)
    return result


def _today() -> date:
    return date.today()


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if config.IS_WINDOWS else {}

# ---------------------------------------------------------------- OpenAI


def probe_openai(key: str, model: str):
    """(status, error code) of GET /v1/models/{model}: free, and it tells a bad
    key (401) from a model this account can't use (403/404)."""
    r = httpx.get(MODEL_URL.format(model=model), headers={"Authorization": f"Bearer {key}"},
                  timeout=8)
    code = ""
    if r.status_code >= 400:
        try:
            err = r.json().get("error") or {}
            code = str(err.get("code") or err.get("type") or "") if isinstance(err, dict) else ""
        except (ValueError, AttributeError):
            code = ""
    return r.status_code, code


def check_openai(refresh: bool = False) -> dict:
    title = "Clé OpenAI"
    key, model = (config.OPENAI_API_KEY or "").strip(), config.REALTIME_MODEL
    if not key:
        return item("openai", "error", title, "Clé OpenAI absente : JARVIS ne peut pas parler.",
                    "Collez votre clé dans Réglages › Connexion. Elle se crée sur "
                    "platform.openai.com/api-keys.")
    from .settings import KEY_SHAPE
    if not KEY_SHAPE.match(key):
        return item("openai", "error", title, "La clé OpenAI n'a pas la bonne forme : elle commence par sk-.",
                    "Copiez-la à nouveau depuis platform.openai.com/api-keys et remplacez-la "
                    "dans Réglages › Connexion.")
    # The key itself is never kept here: only a digest tells a new key from the old one.
    signature = hashlib.sha256(f"{key}\0{model}".encode()).hexdigest()
    hit = _cached("openai", signature, refresh)
    if hit:
        return hit
    ttl = OPENAI_TTL
    try:
        status, code = probe_openai(key, model)
    except httpx.TimeoutException:
        status, code, ttl = -1, "", OPENAI_RETRY_TTL
    except httpx.HTTPError:
        status, code, ttl = 0, "", OPENAI_RETRY_TTL
    except Exception as exc:  # noqa: BLE001 - its message may quote the request, key included
        logging.warning("JARVIS: clé OpenAI non vérifiée (%s)", type(exc).__name__)
        status, code, ttl = 0, "", OPENAI_RETRY_TTL
    fix_key = "Vérifiez-la sur platform.openai.com/api-keys, puis remplacez-la dans Réglages › Connexion."
    if status == 200:
        result = item("openai", "ok", title, f"Clé OpenAI valide · modèle {model} disponible.")
    elif status == 401:
        result = item("openai", "error", title, "Clé OpenAI refusée.", fix_key)
    elif status == 403:
        result = item("openai", "error", title, f"Cette clé n'a pas accès au modèle {model}.",
                      "Autorisez ce modèle pour le projet de la clé sur platform.openai.com, "
                      "ou choisissez-en un autre dans Réglages › Voix.")
    elif status == 404:
        result = item("openai", "error", title, f"Modèle vocal {model} indisponible pour votre compte.",
                      "Choisissez-en un autre dans Réglages › Voix.")
    elif status == 429 and code == "insufficient_quota":
        result = item("openai", "error", title, "Crédit OpenAI épuisé.",
                      "Vérifiez la facturation sur platform.openai.com.")
    elif status == 429:
        result, ttl = item("openai", "warning", title, "OpenAI limite les demandes pour le moment.",
                           "Revérifiez dans un instant."), OPENAI_RETRY_TTL
    elif status == -1:
        result = item("openai", "warning", title, "OpenAI ne répond pas : clé non vérifiée.",
                      "Vérifiez internet, puis cliquez sur Revérifier.")
    elif status == 0:
        result = item("openai", "warning", title, "Pas de connexion à OpenAI : clé non vérifiée.",
                      "Vérifiez internet (ou le pare-feu), puis cliquez sur Revérifier.")
    elif status >= 500:
        result, ttl = item("openai", "warning", title, f"OpenAI rencontre un problème (erreur {status}).",
                           "Revérifiez dans quelques minutes."), OPENAI_RETRY_TTL
    else:
        result, ttl = item("openai", "warning", title, f"Réponse inattendue d'OpenAI (erreur {status}).",
                           fix_key), OPENAI_RETRY_TTL
    return _remember("openai", signature, ttl, result)

# ---------------------------------------------------------------- Claude Code


def _install_fix() -> str:
    if config.IS_WINDOWS:
        return ("Installez-le : ouvrez PowerShell et tapez irm https://claude.ai/install.ps1 | iex, "
                "puis cliquez sur Revérifier.")
    return ("Installez-le : dans un terminal, tapez curl -fsSL https://claude.ai/install.sh | bash, "
            "puis cliquez sur Revérifier.")


def auth_status(refresh: bool = False):
    """`claude auth status`: (exit code, parsed JSON or {}), or (None, reason).

    Exit 0 = logged in, 1 = nobody. The JSON carries loggedIn and authMethod
    (claude.ai, oauth_token, api_key, api_key_helper, third_party...). Run with
    the same environment as the tasks, so it sees what they will see."""
    try:
        cmd = tasks.claude_command()
    except FileNotFoundError:
        return None, "introuvable"
    signature = repr(cmd)
    hit = _cached("claude_auth", signature, refresh)
    if hit:
        return hit
    try:
        r = subprocess.run([*cmd, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, stdin=subprocess.DEVNULL,
                           env=tasks.child_env(), **_no_window())
    except subprocess.TimeoutExpired:
        return _remember("claude_auth", signature, 15, (None, "délai dépassé"))
    except (OSError, subprocess.SubprocessError):
        return _remember("claude_auth", signature, 15, (None, "commande impossible à lancer"))
    out = r.stdout or ""
    data = {}
    start, end = out.find("{"), out.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(out[start:end + 1])
            data = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            data = {}
    return _remember("claude_auth", signature, AUTH_TTL, (r.returncode, data))


def check_claude(refresh: bool = False) -> list:
    """Installed, recent enough and logged in; plus what the login means for
    the claude.ai connectors."""
    title = "Claude Code"
    try:
        tasks.claude_command()
    except FileNotFoundError:
        return [item("claude", "error", title, "Claude Code est introuvable.", _install_fix())]
    version = tasks.claude_version(refresh=refresh)
    shown = ".".join(map(str, version)) if version else ""
    code, data = auth_status(refresh)
    out = []
    login_fix = ("Ouvrez un terminal, tapez claude et connectez-vous (ou claude auth login), "
                 "puis cliquez sur Revérifier.")
    if code == 1 or (code == 0 and data.get("loggedIn") is False):
        out.append(item("claude", "error", title, f"Claude Code {shown} n'est pas connecté.".replace("  ", " "),
                        login_fix))
    elif version is None:
        out.append(item("claude", "warning", title, "Claude Code est installé, mais sa version est illisible.",
                        "Ouvrez un terminal et tapez claude --version ; réinstallez-le si la commande échoue."))
    elif tuple(version) < CLAUDE_MIN:
        need = ".".join(map(str, CLAUDE_MIN))
        out.append(item("claude", "warning", title,
                        f"Claude Code {shown} est trop ancien pour JARVIS ({need} ou plus).",
                        "Mettez-le à jour : tapez claude update dans un terminal."))
    elif code != 0:
        why = data if isinstance(data, str) else f"code {code}"
        out.append(item("claude", "warning", title, f"Claude Code {shown} : connexion non vérifiée ({why}).",
                        login_fix))
    else:
        method = AUTH_METHODS.get(str(data.get("authMethod") or ""))
        how = f" ({method})" if method else ""
        out.append(item("claude", "ok", title, f"Claude Code {shown} installé et connecté{how}."))
    method = str(data.get("authMethod") or "") if isinstance(data, dict) else ""
    env_keys = [k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(k)]
    if code == 0 and (env_keys or (method and method != "claude.ai")):
        how = " et ".join(env_keys) + (" est défini" if len(env_keys) == 1 else " sont définis") if env_keys \
            else f"connexion par {AUTH_METHODS.get(method, method)}"
        out.append(item("claude_connectors", "info", "Connecteurs claude.ai",
                        f"Les connecteurs claude.ai (Gmail, Google Agenda…) ne se chargent qu'avec une "
                        f"connexion par compte claude.ai : ici, {how}.",
                        "Pour les utiliser dans les tâches, connectez Claude Code avec votre compte "
                        "claude.ai (dans un terminal : claude, puis /login) et retirez ANTHROPIC_API_KEY "
                        "ou ANTHROPIC_AUTH_TOKEN de l'environnement."))
    return out


def check_hardening(refresh: bool = False) -> list:
    """The protections a task profile lost because this Claude Code refused them (WP01)."""
    broken = getattr(tasks, "_broken", None)
    if not callable(broken):
        return []
    lost = [flag for flag in ("--safe-mode", "--restricted") if broken(flag)]
    if not lost:
        return []
    return [item("claude_hardening", "warning", "Protections des tâches",
                 f"Cette version de Claude Code refuse {' et '.join(lost)} : les tâches web et lecture "
                 "tournent sans cette protection.",
                 "Mettez Claude Code à jour (claude update) : JARVIS réessaiera tout seul.")]


def check_permission(refresh: bool = False) -> list:
    if (config.PERMISSION_MODE or "").strip() != "bypassPermissions":
        return []
    return [item("permission", "warning", "Mode de permission",
                 "Mode sans garde-fou : Claude peut tout modifier sans contrôle.",
                 "Choisissez « auto » dans Réglages › Claude Code.")]


def check_git_bash(refresh: bool = False) -> list:
    if not config.IS_WINDOWS:
        return []
    found = shutil.which("bash") or any(
        (Path(os.environ.get(var, "")) / "Git" / "bin" / "bash.exe").is_file()
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if os.environ.get(var))
    if found:
        return [item("git_bash", "info", "Git Bash", "Git Bash présent : Claude utilisera Bash pour ses commandes.")]
    return [item("git_bash", "info", "Git Bash",
                 "Git Bash absent : Claude utilisera PowerShell pour ses commandes (tâches avec accès complet).")]

# ---------------------------------------------------------------- this PC


def _writable(folder: Path) -> bool:
    try:
        fd, name = tempfile.mkstemp(dir=folder, prefix=".jarvis-test-")
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def check_workdir(refresh: bool = False) -> list:
    title = "Dossier de travail"
    folder = Path(os.path.expanduser(str(config.WORKDIR)))
    fix = "Choisissez-en un autre dans Réglages › Claude Code."
    if folder.is_dir():
        if _writable(folder):
            return [item("workdir", "ok", title, f"Dossier de travail : {folder}")]
        return [item("workdir", "error", title, f"Le dossier de travail n'est pas modifiable : {folder}", fix)]
    parent = next((p for p in folder.parents if p.is_dir()), None)
    if parent is not None and _writable(parent):
        return [item("workdir", "info", title, f"Le dossier de travail sera créé à la première tâche : {folder}")]
    return [item("workdir", "error", title, f"Dossier de travail introuvable : {folder}", fix)]


def check_data(refresh: bool = False) -> list:
    title = "Données de JARVIS"
    folder = config.DATA_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not (folder.is_dir() and _writable(folder)):
        return [item("data", "error", title, f"Le dossier des données n'est pas modifiable : {folder}",
                     "Vérifiez ses droits, ou choisissez-en un autre (JARVIS_DATA_DIR dans le fichier .env).")]
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        free = None
    if free is not None and free < LOW_DISK:
        return [item("data", "warning", title, f"Disque presque plein : {free // (1024 * 1024)} Mo libres.",
                     "Libérez de la place : la mémoire, les rappels et le journal ne pourraient plus être enregistrés.")]
    return [item("data", "ok", title, f"Données de JARVIS : {folder}")]


def mic_consent() -> str:
    """Windows privacy switches for the microphone: 'deny' when the system or
    the desktop-apps switch blocks it (Chrome then gets NotAllowedError or
    NotReadableError), 'allow', or '' when unknown (not Windows, unreadable).
    UNVERIFIED on every Windows build: read defensively, any doubt is ''."""
    if not config.IS_WINDOWS:
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    seen = False
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in ("", "\\NonPackaged"):
            try:
                with winreg.OpenKey(hive, base + sub) as key:
                    value = str(winreg.QueryValueEx(key, "Value")[0]).strip().lower()
            except OSError:
                continue
            seen = True
            if value == "deny":
                return "deny"
    return "allow" if seen else ""


def check_microphone(refresh: bool = False) -> list:
    if not config.IS_WINDOWS:
        return []
    try:
        consent = mic_consent()
    except Exception:  # noqa: BLE001 - unknown is not an error
        consent = ""
    if consent != "deny":
        return []
    return [item("micro_windows", "error", "Micro (Windows)", "Windows bloque le micro pour Chrome et Edge.",
                 "Paramètres Windows › Confidentialité et sécurité › Microphone : activez « Accès au "
                 "microphone » et « Autoriser les applications de bureau à accéder au microphone ».")]


def _browser_name(exe: str) -> str:
    low = Path(exe).name.lower()
    if "chrome" in low:
        return "Google Chrome"
    if "edge" in low:
        return "Microsoft Edge"
    if "chromium" in low:
        return "Chromium"
    return Path(exe).stem


def check_browser(refresh: bool = False) -> list:
    title = "Navigateur"
    finder = getattr(desktop, "_app_browser", None)
    exe = finder() if callable(finder) else None
    if not exe:
        return [item("browser", "warning", title,
                     "Ni Chrome ni Edge trouvé : JARVIS s'ouvrira dans le navigateur par défaut, sans fenêtre à lui.",
                     "Installez Google Chrome (google.com/chrome), puis relancez JARVIS.")]
    name = _browser_name(str(exe))
    if name == "Microsoft Edge":
        return [item("browser", "info", title, "Fenêtre JARVIS : Microsoft Edge. Chrome est conseillé pour "
                     "le mot d'éveil (reconnaissance du français sur ce PC).")]
    return [item("browser", "ok", title, f"Fenêtre JARVIS : {name}.")]


def check_pillow(refresh: bool = False) -> list:
    if importlib.util.find_spec("PIL") is None:
        return [item("pillow", "warning", "Captures d'écran", "Pillow manquant : JARVIS ne peut pas regarder l'écran.",
                     "Dans le dossier de JARVIS, tapez pip install -r requirements.txt.")]
    return []


def check_mcp(refresh: bool = False) -> list:
    path = (config.MCP_CONFIG or "").strip()
    if not path:
        return []
    from .settings import SettingError, read_mcp_file
    try:
        n = read_mcp_file(os.path.expanduser(path))
    except SettingError as exc:
        return [item("mcp", "error", "Connecteurs MCP", str(exc),
                     "Corrigez le fichier, ou videz le réglage dans Réglages › Claude Code.")]
    return [item("mcp", "ok", "Connecteurs MCP", f"Connecteurs MCP en plus : {n} serveur{'s' if n > 1 else ''}.")]


def check_ares(refresh: bool = False) -> list:
    from . import ares
    mode = ares._mode()  # the same reading as ares.py ('jamais', '0'... count as off)
    if mode == "off":
        return []
    if refresh:  # Revérifier: no 30 s pause nor last answer kept
        ares.reset()
    try:
        up = bool(ares.reachable())  # 'on' too: asked for real, not assumed
    except Exception:  # noqa: BLE001 - optional
        up = False
    if up:
        return [item("ares", "info", "A.R.E.S", "A.R.E.S répond : agenda, tâches et notes accessibles.")]
    return [item("ares", "warning" if mode == "on" else "info", "A.R.E.S",
                 "A.R.E.S n'est pas joignable (facultatif).",
                 "Dans A.R.E.S : Réglages › Application de bureau, activez le serveur MCP local.")]


def _windows() -> bool:
    return config.IS_WINDOWS  # a function: the tests check the Windows side elsewhere


def check_windows(refresh: bool = False) -> list:
    """The global hotkey, the notification-area icon and the start with Windows
    (WP13), once JARVIS runs (shell.start, in server.py's main)."""
    from . import shell
    if not _windows() or not shell.running():
        return []
    out = []
    wanted = shell.hotkey_label()
    hotkey = shell.hotkey_state()
    if hotkey.get("active"):
        out.append(item("hotkey", "ok", "Raccourci global",
                        f"{hotkey['combo']} : JARVIS vient au premier plan depuis n'importe quelle application."))
    elif wanted:
        out.append(item("hotkey", "warning", "Raccourci global",
                        f"Le raccourci {wanted} ne fonctionne pas : un autre programme l'utilise déjà.",
                        "Choisissez-en un autre dans Réglages › Système."))
    elif str(config.HOTKEY or "").strip().lower() not in shell.OFF:
        out.append(item("hotkey", "warning", "Raccourci global",
                        f"Raccourci « {str(config.HOTKEY)[:40]} » invalide.",
                        "Choisissez-en un autre dans Réglages › Système."))
    if config.TRAY and not shell.tray_active():
        out.append(item("tray", "info", "Icône de notification",
                        "Pas d'icône dans la zone de notification (module pystray absent).",
                        "Dans le dossier de JARVIS, tapez pip install -r requirements.txt, puis relancez JARVIS."))
    probe = getattr(desktop, "autostart_enabled", None)
    if callable(probe):
        try:
            on = bool(probe())
        except Exception:  # noqa: BLE001 - shown as unknown: nothing to say
            on = None
        if on is not None:
            out.append(item("autostart", "info", "Démarrage avec Windows",
                            "JARVIS démarre avec Windows." if on else
                            "JARVIS ne démarre pas avec Windows (Réglages › Système pour l'activer)."))
    return out


def check_deadlines(refresh: bool = False) -> list:
    """OpenAI retires models on fixed dates: say so before the voice stops."""
    out = []
    today = _today()
    model = config.REALTIME_MODEL or ""
    if model == "gpt-realtime" or model.startswith("gpt-realtime-mini") or re.match(r"gpt-realtime-\d{4}-", model):
        days = (REALTIME_END - today).days
        if days > 0:
            out.append(item("deadline_realtime", "warning", "Échéance OpenAI",
                            f"OpenAI arrête {model} le 20 janvier 2027 (dans {days} jours).",
                            "Choisissez gpt-realtime-2.1 dans Réglages › Voix."))
        else:
            out.append(item("deadline_realtime", "error", "Échéance OpenAI",
                            f"OpenAI a arrêté {model} le 20 janvier 2027.",
                            "Choisissez gpt-realtime-2.1 dans Réglages › Voix."))
    from . import realtime
    used = sorted({m for m in (realtime.transcribe_model(), config.TRANSCRIBE_FALLBACK) if m in OLD_TRANSCRIBE})
    if used:
        names = " et ".join(used)
        days = (TRANSCRIBE_END - today).days
        if days > 0:
            out.append(item("deadline_transcribe", "info", "Échéance OpenAI",
                            f"OpenAI arrête {names} (transcription de ce que vous dites) le 26 février 2027 "
                            f"(dans {days} jours).",
                            "Une mise à jour de JARVIS proposera un remplaçant avant cette date."))
        else:
            out.append(item("deadline_transcribe", "warning", "Échéance OpenAI",
                            f"OpenAI a arrêté {names} le 26 février 2027 : ce que vous dites peut ne plus "
                            "s'afficher.", "Mettez JARVIS à jour."))
    return out


# In the order the dialog lists them.
CHECKS = [check_openai, check_claude, check_hardening, check_permission, check_microphone,
          check_workdir, check_data, check_browser, check_pillow, check_mcp, check_git_bash,
          check_windows, check_ares, check_deadlines]


def _run_one(fn, refresh: bool) -> list:
    try:
        out = fn(refresh)
    except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
        logging.exception("JARVIS: vérification %s impossible", fn.__name__)
        name = fn.__name__.replace("check_", "")
        return [item(name, "warning", "Vérification", f"Vérification « {name} » impossible "
                     f"({type(exc).__name__}).", "Cliquez sur Revérifier.")]
    return out if isinstance(out, list) else [out]


def run_checks(refresh: bool = False) -> list:
    """Every check, side by side; one that overruns is reported as such."""
    if refresh:
        forget()
    results = []
    pool = ThreadPoolExecutor(max_workers=len(CHECKS), thread_name_prefix="jarvis-health")
    try:
        futures = [(fn, pool.submit(_run_one, fn, refresh)) for fn in CHECKS]
        deadline = time.time() + CHECK_TIMEOUT
        for fn, future in futures:
            try:
                results += future.result(timeout=max(0.1, deadline - time.time()))
            except Exception:  # noqa: BLE001 - concurrent TimeoutError (not builtin before 3.11)
                name = fn.__name__.replace("check_", "")
                results.append(item(name, "warning", "Vérification",
                                    f"Vérification « {name} » trop longue : abandonnée.", "Cliquez sur Revérifier."))
    finally:
        pool.shutdown(wait=False)
    return results


@router.get("/api/health")
def get_health(refresh: bool = False):
    return run_checks(refresh)

# ---------------------------------------------------------------- first run


def onboarded() -> bool:
    state = store.load(STATE_FILE, {})
    return bool(isinstance(state, dict) and state.get("onboarded"))


def set_onboarded(done: bool = True):
    with store.LOCK:
        state = store.load(STATE_FILE, {})
        state = state if isinstance(state, dict) else {}
        if done:
            state["onboarded"] = round(time.time())
        else:
            state.pop("onboarded", None)
        store.save(STATE_FILE, state)


class OnboardingIn(BaseModel):
    done: bool = True


@router.get("/api/onboarding")
def get_onboarding():
    return {"onboarded": onboarded()}


@router.post("/api/onboarding")
def post_onboarding(body: OnboardingIn):
    set_onboarded(body.done)
    return {"onboarded": body.done}
