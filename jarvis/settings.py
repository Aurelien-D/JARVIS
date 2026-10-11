"""Settings changed from the UI: an override layer on top of config.

The Réglages dialog replaces editing .env for everyday settings. What it
saves lives in data/settings.json and wins over .env: apply_overrides()
copies it onto config at startup, and set_many() changes config at once,
since every module reads config.NAME at call time. Some settings only take
effect later (the next conversation, the next task) or after a restart: each
one says which, and the dialog tells monsieur.

Sensitive settings (the permission mode of Claude tasks, their working
folder, the extra MCP servers, the OpenAI key) need an explicit confirmation
in the request. None of this is a voice tool: only the page, with its session
token, can change a setting, never the voice model.
"""
import codecs
import ipaddress
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config, desktop, events, shell, store

router = APIRouter()

SETTINGS_FILE = "settings.json"
ENV_FILE = config.ROOT / ".env"  # tests point this elsewhere: never the real .env

# When a change takes effect.
NOW, SESSION, TASK, RESTART = "now", "session", "task", "restart"

SECTIONS = [("connexion", "Connexion"), ("voix", "Voix"), ("ecoute", "Écoute"),
            ("proactivite", "Proactivité"), ("claude", "Claude Code"), ("couts", "Coûts"),
            ("distance", "Accès à distance"), ("notifications", "Notifications"),
            ("systeme", "Système"), ("donnees", "Données"), ("apropos", "À propos")]

# Built-in Realtime voices; the voice is fixed once JARVIS has spoken in a session.
VOICES = ("alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar")
DAYS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")
_DAY_ALIASES = {"lundi": "lun", "mardi": "mar", "mercredi": "mer", "jeudi": "jeu",
                "vendredi": "ven", "samedi": "sam", "dimanche": "dim"}
_MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "alt": "alt", "shift": "shift",
              "maj": "shift", "win": "win", "windows": "win", "super": "win"}
_KEY_NAME = re.compile(r"^(?:[a-z0-9]|f(?:[1-9]|1\d|2[0-4])|space|espace)$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
KEY_SHAPE = re.compile(r"^sk-[A-Za-z0-9_\-]{16,250}$")
# The 'url' kind (the ntfy server): plain http only where nobody on the internet listens.
URL_REFUSED = ("Adresse de serveur refusée : https://, ou http:// vers une adresse du réseau local "
               "ou Tailscale.")
# 10/8 as a tuple: the loopback proof bans the any-address literal from every source.
_LOCAL_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"),  # tailnet
               ipaddress.IPv4Network((10 << 24, 8)), ipaddress.ip_network("172.16.0.0/12"),          # private LAN
               ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"))
_TS_NAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net")


@dataclass(frozen=True)
class Setting:
    key: str             # the API name
    attr: str            # config attribute ('MODELS.simple': an entry of config.MODELS)
    kind: str            # bool | int | float | choice | time | hours | days | text | path | file | hotkey | url
    section: str
    label: str
    help: str = ""
    choices: tuple = ()  # ((value, label), ...)
    pattern: str = ""    # a choice may also take any value matching this
    minimum: float = 0
    maximum: float = 0
    step: float = 1
    unit: str = ""
    scale: int = 1       # the dialog shows value / scale (seconds as minutes)
    live: str = NOW
    sensitive: bool = False
    danger: tuple = ()   # (value, warning): a value that deserves a red warning


_CLAUDE_MODELS = (("haiku", "haiku · rapide et économique"), ("sonnet", "sonnet · polyvalent"),
                  ("opus", "opus · le plus capable"), ("", "Modèle par défaut du compte"))
_CLAUDE_MODEL_RE = r"[a-z0-9][a-z0-9.\-\[\]]{0,80}"
BYPASS_WARNING = "Mode sans garde-fou : Claude peut tout modifier sans contrôle. Déconseillé."

SCHEMA = [
    # ---------------------------------------------------------------- Voix
    Setting("voice", "VOICE", "choice", "voix", "Voix de JARVIS",
            "Elle ne change qu'au début d'une conversation.",
            choices=tuple((v, {"ballad": "ballad · majordome britannique",
                               "marin": "marin · conseillée par OpenAI",
                               "cedar": "cedar · conseillée par OpenAI"}.get(v, v)) for v in VOICES),
            live=SESSION),
    Setting("voice_speed", "VOICE_SPEED", "float", "voix", "Vitesse de la voix", "1 = normale.",
            minimum=0.25, maximum=1.5, step=0.05, live=SESSION),
    Setting("realtime_model", "REALTIME_MODEL", "choice", "voix", "Modèle vocal",
            "Le modèle d'OpenAI qui écoute et répond.",
            choices=(("gpt-realtime-2.1", "gpt-realtime-2.1 · conseillé"),
                     ("gpt-realtime-2.1-mini", "gpt-realtime-2.1-mini · moins cher"),
                     ("gpt-realtime", "gpt-realtime · arrêté le 20 janvier 2027"),
                     ("gpt-realtime-mini", "gpt-realtime-mini · arrêté le 20 janvier 2027")),
            pattern=r"gpt-realtime[a-z0-9.\-]{0,60}", live=SESSION),
    Setting("reasoning", "REASONING_EFFORT", "choice", "voix", "Effort de réflexion",
            "Modèles gpt-realtime-2.x seulement : plus d'effort, réponses plus lentes.",
            choices=(("minimal", "minimal · le plus rapide"), ("low", "low · rapide (conseillé)"),
                     ("medium", "medium · équilibré"), ("high", "high · plus lent"), ("xhigh", "xhigh · le plus lent")),
            live=SESSION),
    # ---------------------------------------------------------------- Écoute
    Setting("wake_word", "WAKE_WORD", "bool", "ecoute", "Mot d'éveil « Jarvis »",
            "En veille, JARVIS attend son nom. Le bouton « Mot d'éveil » de l'écran principal "
            "fait de même, pour ce navigateur."),
    Setting("noise_reduction", "NOISE_REDUCTION", "choice", "ecoute", "Votre micro est…",
            "Règle la réduction de bruit.",
            choices=(("near_field", "Un casque"), ("far_field", "Le micro du PC ou de la webcam")),
            live=SESSION),
    Setting("eagerness", "EAGERNESS", "choice", "ecoute", "Prise de parole",
            "Quand JARVIS juge que vous avez fini de parler.",
            choices=(("auto", "Automatique (conseillé)"),
                     ("low", "Il me coupe trop tôt : attendre davantage"),
                     ("medium", "Intermédiaire"), ("high", "Il tarde à répondre : réagir vite")),
            live=SESSION),
    Setting("idle_minutes", "IDLE_MINUTES", "int", "ecoute", "Mise en veille après",
            "Sans parler pendant ce temps, JARVIS se met en veille. 0 = jamais.",
            minimum=0, maximum=120, unit="min"),
    # ---------------------------------------------------------------- Proactivité
    Setting("briefing_time", "BRIEFING_TIME", "time", "proactivite", "Briefing du matin",
            "JARVIS fait le point à cette heure, les jours choisis ci-dessous : rappels du "
            "jour, agenda A.R.E.S et météo, préparés sur ce PC sans frais. Vide = pas de briefing."),
    Setting("briefing_days", "BRIEFING_DAYS", "days", "proactivite", "Jours du briefing"),
    Setting("briefing_news", "BRIEFING_NEWS", "bool", "proactivite",
            "Ajouter les titres de l'actualité au briefing"),
    Setting("quiet_hours", "QUIET_HOURS", "hours", "proactivite", "Heures calmes",
            "Ni annonce à voix haute ni son : les messages attendent votre retour."),
    Setting("city", "CITY", "text", "proactivite", "Votre ville", "Pour la météo.", maximum=80),
    Setting("reopen_on_reminder", "REOPEN_ON_REMINDER", "bool", "proactivite",
            "Rouvrir la fenêtre de JARVIS quand un rappel arrive"),
    # ---------------------------------------------------------------- Claude Code
    Setting("permission_mode", "PERMISSION_MODE", "choice", "claude", "Mode de permission des tâches",
            "Les tâches tournent sans personne pour répondre : ce qui demanderait une "
            "autorisation est refusé.",
            choices=(("auto", "auto · le sûr passe, le risqué est refusé (conseillé)"),
                     ("acceptEdits", "acceptEdits · fichiers autorisés, commandes refusées"),
                     ("dontAsk", "dontAsk · seul ce qui est autorisé d'avance passe"),
                     ("bypassPermissions", "bypassPermissions · aucun contrôle (déconseillé)")),
            live=TASK, sensitive=True, danger=("bypassPermissions", BYPASS_WARNING)),
    Setting("workdir", "WORKDIR", "path", "claude", "Dossier de travail des tâches",
            "Là où Claude Code travaille et range ce qu'il crée.", maximum=400, live=TASK,
            sensitive=True),
    Setting("mcp_config", "MCP_CONFIG", "file", "claude", "Connecteurs MCP en plus",
            "Fichier JSON au format de Claude Code, pour les tâches avec accès complet. "
            "Vide = aucun.", maximum=400, live=TASK, sensitive=True),
    Setting("model_simple", "MODELS.simple", "choice", "claude", "Modèle des tâches simples",
            choices=_CLAUDE_MODELS, pattern=_CLAUDE_MODEL_RE, live=TASK),
    Setting("model_normal", "MODELS.normale", "choice", "claude", "Modèle des tâches normales",
            choices=_CLAUDE_MODELS, pattern=_CLAUDE_MODEL_RE, live=TASK),
    Setting("model_complex", "MODELS.complexe", "choice", "claude", "Modèle des tâches complexes",
            choices=_CLAUDE_MODELS, pattern=_CLAUDE_MODEL_RE, live=TASK),
    Setting("task_timeout", "TASK_TIMEOUT", "int", "claude", "Durée maximale d'une tâche",
            "Au-delà, la tâche est arrêtée.", minimum=60, maximum=7200, step=60, unit="min",
            scale=60, live=TASK),
    Setting("task_budget_usd", "TASK_BUDGET_USD", "float", "claude", "Plafond par tâche",
            "Claude s'arrête une fois ce montant estimé atteint. 0 = pas de plafond.",
            minimum=0, maximum=100, step=0.5, unit="$", live=TASK),
    Setting("max_concurrent_tasks", "MAX_CONCURRENT_TASKS", "int", "claude", "Tâches en même temps",
            "Les suivantes attendent leur tour.", minimum=1, maximum=10, live=TASK),
    # ---------------------------------------------------------------- Coûts
    Setting("daily_budget_usd", "DAILY_BUDGET_USD", "float", "couts", "Plafond de dépense par jour",
            "Voix, Siri et tâches comprises. Une fois atteint, le mot d'éveil n'ouvre plus de "
            "conversation payante, aucune nouvelle tâche Claude ne démarre et le briefing du "
            "matin est lu par la voix du navigateur. 0 = pas de plafond (l'accès à distance "
            "et Siri en demandent un).",
            minimum=0, maximum=1000, step=1, unit="$"),
    # ---------------------------------------------------------------- Notifications (ntfy, notify.py)
    Setting("ntfy", "NTFY", "bool", "notifications", "Notifications sur l'iPhone (ntfy)",
            "Il faut aussi l'app ntfy sur l'iPhone, abonnée au sujet ci-dessous."),
    Setting("ntfy_server", "NTFY_SERVER", "url", "notifications", "Serveur ntfy",
            "https://ntfy.sh par défaut. Un serveur personnel en https://, ou en http:// "
            "sur le réseau local ou Tailscale.", maximum=200),
    Setting("ntfy_only_away", "NTFY_ONLY_AWAY", "bool", "notifications",
            "Seulement si je ne suis pas au PC",
            "Rien pour les tâches et les confirmations du PC tant que vous l'utilisez. Ce qui vient de "
            "l'iPhone ou de Siri, les rappels et les alertes de sécurité partent toujours."),
    Setting("ntfy_reminder_text", "NTFY_REMINDER_TEXT", "bool", "notifications",
            "Texte des rappels dans la notification",
            "Sinon la notification dit seulement « un rappel »."),
    # ---------------------------------------------------------------- Système
    Setting("hotkey", "HOTKEY", "hotkey", "systeme", "Raccourci global",
            "Pour parler à JARVIS depuis n'importe quelle application, par exemple Ctrl+Alt+Maj+J.",
            maximum=40, live=RESTART),
    Setting("browser", "BROWSER", "choice", "systeme", "Navigateur de la fenêtre JARVIS",
            choices=(("auto", "Automatique (Chrome de préférence)"), ("chrome", "Google Chrome"),
                     ("edge", "Microsoft Edge")), live=RESTART),
    Setting("tray", "TRAY", "bool", "systeme", "Icône dans la zone de notification", live=RESTART),
    Setting("ares", "ARES", "choice", "systeme", "A.R.E.S (agenda, tâches, notes)",
            choices=(("auto", "Automatique (s'il répond)"), ("on", "Toujours"), ("off", "Jamais"))),
    # ---------------------------------------------------------------- Données
    Setting("journal_days", "JOURNAL_DAYS", "int", "donnees", "Conservation du journal",
            "Nombre de jours gardés sur ce PC. 0 = pas de journal.", minimum=0, maximum=365,
            unit="jours"),
]
BY_KEY = {s.key: s for s in SCHEMA}
SENSITIVE = {s.key for s in SCHEMA if s.sensitive}
_lock = threading.RLock()


class SettingError(ValueError):
    """A value refused, with the French reason the dialog shows."""


# ---------------------------------------------------------------- reading and writing config

def _get(s: Setting):
    if s.attr.startswith("MODELS."):
        return config.MODELS.get(s.attr.split(".", 1)[1], "")
    return getattr(config, s.attr)


def _put(s: Setting, value):
    if s.attr.startswith("MODELS."):
        config.MODELS[s.attr.split(".", 1)[1]] = value
    else:
        setattr(config, s.attr, value)


def values() -> dict:
    return {s.key: _get(s) for s in SCHEMA}


def shown_values() -> dict:
    """values() as the dialog shows them: the hotkey the way the keyboard and
    Aide name it ('Ctrl+Alt+Maj+J', which normalize_hotkey reads back)."""
    out = values()
    shown = shell.hotkey_label(out["hotkey"])
    try:
        if shown and normalize_hotkey(shown) == out["hotkey"]:  # never a label it would not read back
            out["hotkey"] = shown
    except SettingError:
        pass
    return out


# What each setting was when this JARVIS started (after its saved overrides):
# a 'restart' setting that differs from it waits for the next start.
_BOOT = {s.key: _get(s) for s in SCHEMA}
# .env's own values, before any override: what a reset (null) goes back to.
_ENV = dict(_BOOT)


def restart_pending() -> list:
    return [s.key for s in SCHEMA if s.live == RESTART and _get(s) != _BOOT.get(s.key)]


# ---------------------------------------------------------------- validation

def _number(s: Setting, value, integer: bool):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SettingError(f"{s.label} : un nombre est attendu.")
    try:
        number = float(str(value).replace(",", ".").strip()) if isinstance(value, str) else float(value)
    except ValueError:
        raise SettingError(f"{s.label} : un nombre est attendu.") from None
    if not math.isfinite(number):
        raise SettingError(f"{s.label} : un nombre est attendu.")
    if integer:
        if number != int(number):
            raise SettingError(f"{s.label} : un nombre entier est attendu.")
        number = int(number)
    if not s.minimum <= number <= s.maximum:
        low, high = _fmt(s.minimum, s), _fmt(s.maximum, s)
        raise SettingError(f"{s.label} : choisissez une valeur entre {low} et {high}.")
    return number


def _fmt(n, s: Setting) -> str:
    shown = n / s.scale
    text = (f"{shown:g}").replace(".", ",")
    return f"{text} {s.unit}".strip()


def _text(s: Setting, value, *, empty_ok=True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SettingError(f"{s.label} : un texte est attendu.")
    value = value.strip()
    if _CONTROL_CHARS.search(value):
        raise SettingError(f"{s.label} : caractères invalides.")
    if s.maximum and len(value) > s.maximum:
        raise SettingError(f"{s.label} : {int(s.maximum)} caractères au plus.")
    if not value and not empty_ok:
        raise SettingError(f"{s.label} : ce réglage ne peut pas être vide.")
    return value


def parse_time(text: str):
    """'8:00', '08h30', '8h', '8' -> (8, 0); None when it is not a time of day."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:[:hH]\s*(\d{2})?)?\s*", text or "")
    if not m:
        return None
    h, mins = int(m.group(1)), int(m.group(2) or 0)
    return (h, mins) if 0 <= h <= 23 and 0 <= mins <= 59 else None


def _time(s: Setting, value) -> str:
    value = _text(s, value)
    if not value:
        return ""
    parsed = parse_time(value)
    if not parsed:
        raise SettingError(f"{s.label} : heure invalide, par exemple 08:00.")
    return "%02d:%02d" % parsed


def _hours(s: Setting, value) -> str:
    value = _text(s, value)
    if value.lower() in ("", "off", "non", "aucune", "0"):
        return ""
    parts = re.split(r"\s*(?:-|–|—|\bà\b)\s*", value)
    times = [parse_time(p) for p in parts] if len(parts) == 2 else [None]
    if None in times:
        raise SettingError(f"{s.label} : plage invalide, par exemple 22:30-07:30.")
    if times[0] == times[1]:
        raise SettingError(f"{s.label} : le début et la fin doivent être différents.")
    return "%02d:%02d-%02d:%02d" % (*times[0], *times[1])


def _day(token: str) -> str:
    token = token.strip().lower().rstrip(".")
    token = _DAY_ALIASES.get(token, token)
    if token not in DAYS:
        raise SettingError(f"Jour inconnu : {token[:20]} (lun, mar, mer, jeu, ven, sam, dim).")
    return token


def _days(s: Setting, value) -> str:
    """'tous', 'lun-ven', 'lun,mer,ven' (or a list of days): normalised, never empty."""
    if isinstance(value, list):
        value = ",".join(str(v) for v in value)
    value = _text(s, value).lower().replace(" ", "")
    if value in ("tous", "touslesjours", "*"):
        return "tous"
    if not value:
        raise SettingError(f"{s.label} : choisissez au moins un jour.")
    picked = set()
    for token in value.split(","):
        if not token:
            continue
        if "-" in token:
            start, end = (_day(t) for t in token.split("-", 1))
            i, j = DAYS.index(start), DAYS.index(end)
            span = range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)]
            picked.update(DAYS[k] for k in span)
        else:
            picked.add(_day(token))
    return compact_days(picked)


def compact_days(days) -> str:
    """{'lun', ..., 'ven'} -> 'lun-ven'; all seven -> 'tous'; gaps -> 'lun,mer,ven'."""
    idx = sorted(DAYS.index(d) for d in set(days))
    if not idx:
        raise SettingError("Jours du briefing : choisissez au moins un jour.")
    if len(idx) == 7:
        return "tous"
    if len(idx) > 1 and idx == list(range(idx[0], idx[-1] + 1)):
        return f"{DAYS[idx[0]]}-{DAYS[idx[-1]]}"
    return ",".join(DAYS[i] for i in idx)


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _path(s: Setting, value) -> str:
    value = os.path.expanduser(_text(s, value, empty_ok=False))
    if not os.path.isabs(value):
        raise SettingError(f"{s.label} : indiquez un chemin complet, par exemple "
                           "C:\\Users\\Vous\\JARVIS-travail.")
    folder = Path(os.path.abspath(value))
    # Claude working inside JARVIS's own code or data would work on its own guard rails.
    for own in (config.ROOT, config.DATA_DIR):
        if _inside(folder, Path(os.path.abspath(own))):
            raise SettingError(f"{s.label} : choisissez un dossier à part, ni celui de JARVIS "
                               "ni celui de ses données.")
    return str(folder)


def read_mcp_file(path: str) -> int:
    """How many servers a Claude Code MCP config declares; SettingError when unusable."""
    import json
    target = Path(path)
    if not target.is_file():
        raise SettingError(f"Fichier introuvable : {path}")
    try:
        if target.stat().st_size > 1_000_000:
            raise SettingError("Fichier MCP trop gros (1 Mo au plus).")
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise SettingError("Fichier MCP illisible : ce n'est pas du JSON valide.") from None
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise SettingError("Fichier MCP invalide : il lui manque la liste « mcpServers ».")
    return len(servers)


def _file(s: Setting, value) -> str:
    value = os.path.expanduser(_text(s, value))
    if not value:
        return ""
    if not os.path.isabs(value):
        raise SettingError(f"{s.label} : indiquez le chemin complet du fichier.")
    read_mcp_file(value)
    return os.path.abspath(value)


def _local_host(host: str) -> bool:
    """A tailnet or private LAN address, or a *.ts.net name."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return bool(_TS_NAME.fullmatch(host))
    return any(ip in net for net in _LOCAL_NETS)


def check_url(s: Setting, value) -> str:
    """https:// to any server, http:// only to a tailnet or LAN address; never a
    login, a query or a fragment (nothing hidden in it, nothing that redirects
    the topic elsewhere)."""
    value = _text(s, value, empty_ok=False)
    if any(c.isspace() or c in "?#\\" for c in value):
        raise SettingError(URL_REFUSED)
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".")
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise SettingError(URL_REFUSED) from None
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http") or not host or "@" in parts.netloc or parts.query or parts.fragment:
        raise SettingError(URL_REFUSED)
    if scheme == "http" and not _local_host(host):
        raise SettingError(URL_REFUSED)
    return value


def normalize_hotkey(text: str) -> str:
    """'Ctrl + Alt + Maj + J' -> 'ctrl+alt+shift+j'; SettingError (French) otherwise."""
    parts = [p.strip().lower() for p in str(text or "").replace(" ", "").split("+")]
    if not parts or any(not p for p in parts):
        raise SettingError("Raccourci invalide : par exemple Ctrl+Alt+Maj+J.")
    *mods, key = parts
    mods = [_MODIFIERS.get(m, "") for m in mods]
    if not mods or "" in mods or len(set(mods)) != len(mods):
        raise SettingError("Raccourci invalide : au moins une touche ctrl, alt, maj ou win, puis une "
                           "lettre, un chiffre ou F1 à F24.")
    if not _KEY_NAME.match(key):
        raise SettingError("Raccourci invalide : la dernière touche doit être une lettre, un chiffre "
                           "ou F1 à F24.")
    key = "space" if key == "espace" else key
    order = ["ctrl", "alt", "shift", "win"]
    combo = "+".join(sorted(mods, key=order.index) + [key])
    if combo == "ctrl+alt+j":
        raise SettingError("Ctrl+Alt+J est déjà pris par A.R.E.S : choisissez-en un autre.")
    # WP13's parser, when there is one, has the last word on what Windows accepts.
    parse = getattr(shell, "parse_hotkey", None)
    if callable(parse):
        try:
            parse(combo)
        except ValueError as exc:
            raise SettingError(str(exc)) from None
    return combo


def validate(s: Setting, value):
    """The value to store, normalised; SettingError with a French reason otherwise."""
    if s.kind == "bool":
        if isinstance(value, bool):
            return value
        if value in (0, 1) and not isinstance(value, float):
            return bool(value)
        raise SettingError(f"{s.label} : oui ou non attendu.")
    if s.kind in ("int", "float"):
        return _number(s, value, s.kind == "int")
    if s.kind == "choice":
        if not isinstance(value, str):
            raise SettingError(f"{s.label} : valeur non reconnue.")
        value = value.strip()
        if value in {c[0] for c in s.choices} or (s.pattern and re.fullmatch(s.pattern, value)):
            return value
        shown = value if len(value) <= 40 else value[:40] + "…"
        raise SettingError(f"{s.label} : valeur non reconnue ({shown}).")
    if s.kind == "time":
        return _time(s, value)
    if s.kind == "hours":
        return _hours(s, value)
    if s.kind == "days":
        return _days(s, value)
    if s.kind == "text":
        return _text(s, value)
    if s.kind == "path":
        return _path(s, value)
    if s.kind == "file":
        return _file(s, value)
    if s.kind == "hotkey":
        return normalize_hotkey(_text(s, value, empty_ok=False))
    if s.kind == "url":
        return check_url(s, value)
    raise SettingError(f"{s.label} : type inconnu.")


# ---------------------------------------------------------------- saved overrides

def _saved() -> dict:
    data = store.load(SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def apply_overrides():
    """Copy the settings saved from the UI onto config (startup). A value that
    no longer validates (a hand edit, a removed choice) is skipped and logged:
    .env's value stays."""
    with _lock:
        for key, value in _saved().items():
            s = BY_KEY.get(key)
            if s is None:
                continue
            try:
                _put(s, validate(s, value))
            except SettingError as exc:
                logging.warning("JARVIS: réglage ignoré (%s) : %s", key, exc)
        _BOOT.update({s.key: _get(s) for s in SCHEMA})


def _swap_hotkey(combo: str) -> str:
    """Hand the new combination to the running hotkey thread (shell.py, Windows).
    NOW when Windows took it; RESTART when no thread runs (not Windows, or JARVIS
    not started): the next start reads config.HOTKEY. A combination another
    program holds raises SettingError: the old one stays and nothing is saved."""
    swap = getattr(shell, "set_hotkey", None)
    if not callable(swap):
        return RESTART
    try:
        result = swap(combo)
    except ValueError as exc:  # shell's parser refused it (normalize_hotkey checked already)
        raise SettingError(str(exc)) from None
    except Exception:  # noqa: BLE001 - the restart takes it then
        logging.exception("JARVIS: raccourci non changé à chaud")
        return RESTART
    if not isinstance(result, dict):
        return NOW
    if result.get("error"):
        raise SettingError(str(result["error"]))
    return NOW if result.get("ok") else RESTART


def set_many(changes: dict, confirm: bool = False) -> dict:
    """Validate every change first (all or nothing), then apply, save and tell
    the pages. None resets a setting to its .env value. Returns {key: when}."""
    unknown = [k for k in changes if k not in BY_KEY]
    if unknown:
        raise SettingError(f"Réglage inconnu : {', '.join(sorted(unknown))[:80]}.")
    touchy = sorted(k for k in changes if k in SENSITIVE)
    if touchy and confirm is not True:
        raise SettingError("Réglage sensible : confirmez la modification dans la fenêtre Réglages "
                           "(il ne peut être modifié qu'ici, jamais à la voix).")
    ready = {}
    for key, value in changes.items():
        s = BY_KEY[key]
        ready[key] = None if value is None else validate(s, value)
    applied = {}
    hotkey_was = config.HOTKEY
    if "hotkey" in ready:
        # Windows has the last word on a hotkey (another program may hold it):
        # asked before anything is saved, so a refusal changes nothing.
        applied["hotkey"] = _swap_hotkey(ready["hotkey"] if ready["hotkey"] is not None
                                         else _ENV["hotkey"])
    with _lock, store.LOCK:
        saved = _saved()
        for key, value in ready.items():
            if value is None:
                saved.pop(key, None)
                ready[key] = _ENV[key]
            else:
                saved[key] = value
        try:
            store.save(SETTINGS_FILE, saved)  # saved first: a full disk changes nothing
        except Exception:
            if applied.get("hotkey") == NOW:  # ... the hotkey included
                _swap_hotkey(hotkey_was)
            raise
        for key, value in ready.items():
            _put(BY_KEY[key], value)
            applied.setdefault(key, BY_KEY[key].live)
    if applied.get("hotkey") == NOW:
        _BOOT["hotkey"] = config.HOTKEY  # in effect now: no restart waits for it
    if "ares" in applied:
        from . import ares  # late: only this change needs it
        ares.reset()  # the next look (the pages reload on 'config') asks A.R.E.S again
    if applied:
        logging.info("JARVIS: réglages modifiés : %s", ", ".join(sorted(applied)))
        events.publish("config", {"keys": sorted(applied), "restart": restart_pending()})
    return applied


def overridden() -> list:
    return sorted(k for k in _saved() if k in BY_KEY)


# ---------------------------------------------------------------- the OpenAI key

def mask(key: str) -> str:
    """'sk-…abcd': enough to recognise a key, never enough to use it."""
    key = (key or "").strip()
    if not key:
        return ""
    return f"{key[:3]}…{key[-4:]}" if len(key) >= 12 else f"{key[:3]}…"


def _env_encoding(raw: bytes):
    """The .env file's own encoding, kept on rewrite (Notepad may save it as ANSI)."""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def write_key(key: str):
    """Rewrite the key's line in .env (or add it), keeping every other line,
    its encoding and its line endings. Atomic: tmp file, then replace."""
    path = Path(ENV_FILE)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = b""
    encoding = _env_encoding(raw)
    text = raw.decode(encoding, errors="replace")
    eol = "\r\n" if "\r\n" in text else ("\r\n" if config.IS_WINDOWS and not text else "\n")
    lines = text.splitlines(keepends=True)
    entry = re.compile(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=")
    for i, line in enumerate(lines):
        if entry.match(line):  # the first one wins in load_env: replace it
            ending = line[len(line.rstrip("\r\n")):] or eol
            lines[i] = f"OPENAI_API_KEY={key}{ending}"
            break
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += eol
        lines.append(f"OPENAI_API_KEY={key}{eol}")
    data = "".join(lines).encode(encoding)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------- versions and autostart

_versions = {"jarvis": None, "probed": 0.0}
_probing = threading.Lock()


def _git_version() -> str:
    """'09/10/2026 · b9270d8' from the checkout, '' when there is no git."""
    git = shutil.which("git")
    if not git or not (config.ROOT / ".git").exists():
        return ""
    try:
        out = subprocess.run([git, "-C", str(config.ROOT), "log", "-1", "--format=%cs %h"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=5, stdin=subprocess.DEVNULL,
                             **({"creationflags": subprocess.CREATE_NO_WINDOW} if config.IS_WINDOWS else {}))
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2}) ([0-9a-f]{6,40})", (out.stdout or "").strip())
    return f"{m[3]}/{m[2]}/{m[1]} · {m[4]}" if m else ""


def _probe_versions():
    """In the background: git and `claude --version` can each take a second."""
    from . import tasks
    if not _probing.acquire(blocking=False):
        return
    try:
        if _versions["jarvis"] is None:
            _versions["jarvis"] = _git_version() or "version locale"
        tasks.claude_version()
    except Exception:  # noqa: BLE001 - versions are information only
        logging.exception("JARVIS: versions illisibles")
    finally:
        _probing.release()


def _claude_cached():
    """The Claude Code version tasks.py already knows, without running claude."""
    from . import tasks
    value = getattr(tasks, "_version", {}).get("value")
    return ".".join(map(str, value)) if value else None


def versions() -> dict:
    """What À propos and /api/config show. Never waits: what is not known yet
    is probed in the background (None until then)."""
    from . import realtime
    unknown = _versions["jarvis"] is None or _claude_cached() is None
    if unknown and time.time() - _versions["probed"] > 60:  # an absent claude stays absent a while
        _versions["probed"] = time.time()
        threading.Thread(target=_probe_versions, daemon=True, name="jarvis-versions").start()
    return {"jarvis": _versions["jarvis"], "claude_code": _claude_cached(),
            "python": platform.python_version(), "realtime_model": config.REALTIME_MODEL,
            "transcribe_model": realtime.transcribe_model(), "claude_models": dict(config.MODELS)}


def deadlines() -> list | None:
    """OpenAI's retirement dates that concern the models in use (À propos)."""
    from . import health
    try:
        found = health.check_deadlines()
    except Exception:  # noqa: BLE001 - À propos says 'inconnu' rather than break the dialog
        logging.exception("JARVIS: échéances inconnues")
        return None
    return [" ".join(x for x in (c.get("message_fr"), c.get("fix_fr")) if x) for c in found]


def autostart_state():
    """True or False on Windows; None where JARVIS can't start with the system."""
    probe = getattr(desktop, "autostart_enabled", None)
    if callable(probe):
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 - shown as unknown
            return None
    appdata = os.environ.get("APPDATA")
    if not config.IS_WINDOWS or not appdata:
        return None
    return (Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup/JARVIS.lnk").exists()


def usage_capped() -> bool:
    from . import usage
    try:
        return bool(usage.over_daily_cap())
    except Exception:  # noqa: BLE001 - a broken counter must not block the page
        return False


def hotkey_info() -> dict:
    """The global hotkey as the help card names it: combo '' for none; active
    None where it isn't registered by JARVIS (not Windows, or not started)."""
    active = None
    if config.IS_WINDOWS and shell.running():
        active = bool(shell.hotkey_state().get("active"))
    return {"combo": shell.hotkey_label(), "active": active}


def public_config() -> dict:
    """Added to /api/config: what every module of the page may read at start."""
    return {"quiet_hours": config.QUIET_HOURS, "usage_capped": usage_capped(), "versions": versions(),
            "hotkey": hotkey_info()}


def _platform() -> str:
    return "windows" if config.IS_WINDOWS else "mac" if config.IS_MAC else sys.platform


# ---------------------------------------------------------------- routes

def _schema_entry(s: Setting) -> dict:
    entry = {"key": s.key, "section": s.section, "type": s.kind, "label": s.label, "help": s.help,
             "live": s.live, "sensitive": s.sensitive}
    if s.choices:
        entry["choices"] = [{"value": v, "label": label} for v, label in s.choices]
    if s.kind in ("int", "float"):
        entry.update(min=s.minimum, max=s.maximum, step=s.step, unit=s.unit, scale=s.scale)
    elif s.maximum:
        entry["maxlength"] = int(s.maximum)
    if s.danger:
        entry["danger"] = {"value": s.danger[0], "text": s.danger[1]}
    return entry


@router.get("/api/settings")
def get_settings(request: Request):
    from . import remote  # late: remote reads settings through usage
    key = config.OPENAI_API_KEY or ""
    body = {"sections": [{"id": i, "title": t} for i, t in SECTIONS],
            "schema": [_schema_entry(s) for s in SCHEMA], "values": shown_values(),
            "overridden": overridden(), "restart": restart_pending(),
            "key": {"present": bool(key.strip()), "masked": mask(key)},
            "autostart": autostart_state(), "versions": versions(), "deadlines": deadlines(),
            "data_dir": str(config.DATA_DIR), "platform": _platform()}
    if remote.caller_of(request).remote:
        body = _for_the_phone(body)
    return body


def _for_the_phone(body: dict) -> dict:
    """What a paired iPhone sees of Réglages: its few sections and harmless
    settings, nothing about the key, the data folder or autostart (4.10)."""
    from . import remote
    allowed = remote.REMOTE_SETTINGS
    sections = [s for s in body["sections"] if s["id"] in remote.REMOTE_SECTIONS]
    return {**body, "sections": sections,
            "schema": [e for e in body["schema"] if e["key"] in allowed],
            "values": {k: v for k, v in body["values"].items() if k in allowed},
            "overridden": [k for k in body["overridden"] if k in allowed],
            "restart": [k for k in body["restart"] if k in allowed],
            "key": {"present": True, "masked": ""}, "data_dir": "", "autostart": None, "remote": True}


@router.put("/api/settings")
def put_settings(body: dict, request: Request):
    """A partial dict {key: value}; 'confirm': true for the sensitive ones."""
    from . import remote  # late: remote reads settings through usage
    changes = dict(body)
    confirm = changes.pop("confirm", False)
    phone = remote.caller_of(request).remote
    if phone and (confirm or any(k not in remote.REMOTE_SETTINGS for k in changes)):
        # The phone may be away from home: what runs tasks or spends stays on the PC.
        raise HTTPException(403, "Réglage modifiable sur le PC seulement.")
    if not changes:
        raise HTTPException(400, "Aucun réglage à modifier.")
    try:
        applied = set_many(changes, confirm=confirm is True)
    except SettingError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(500, "Réglages non enregistrés : le dossier des données n'est pas "
                                 "modifiable (disque plein ou protégé).") from None
    # The models may have changed: À propos shows their versions and deadlines.
    out = {"ok": True, "applied": applied, "values": shown_values(), "restart": restart_pending(),
           "overridden": overridden(), "versions": versions(), "deadlines": deadlines()}
    if phone:
        allowed = remote.REMOTE_SETTINGS
        out.update(values={k: v for k, v in out["values"].items() if k in allowed},
                   restart=[k for k in out["restart"] if k in allowed],
                   overridden=[k for k in out["overridden"] if k in allowed])
    return out


class KeyIn(BaseModel):
    key: str = ""
    confirm: bool = False  # a sensitive setting, like the others


@router.post("/api/settings/openai-key")
def put_openai_key(body: KeyIn):
    """Write the OpenAI key into .env and use it at once. The answer carries
    only its masked form: the key never goes back to the browser."""
    if body.confirm is not True:
        raise HTTPException(400, "Réglage sensible : confirmez la modification dans la fenêtre "
                                 "Réglages (il ne peut être modifié qu'ici, jamais à la voix).")
    key = (body.key or "").strip()
    if not KEY_SHAPE.match(key):
        raise HTTPException(400, "Clé OpenAI invalide : elle commence par sk- et se copie depuis "
                                 "platform.openai.com/api-keys.")
    try:
        write_key(key)
    except OSError:
        raise HTTPException(500, "Impossible d'écrire le fichier .env : vérifiez qu'il n'est pas "
                                 "ouvert ailleurs ou en lecture seule.") from None
    config.OPENAI_API_KEY = key
    os.environ["OPENAI_API_KEY"] = key
    from . import health
    health.forget("openai")
    logging.info("JARVIS: clé OpenAI remplacée")
    events.publish("config", {"keys": ["openai_key"], "restart": restart_pending()})
    return {"ok": True, "masked": mask(key)}


class AutostartIn(BaseModel):
    on: bool


@router.post("/api/autostart")
def post_autostart(body: AutostartIn):
    try:
        message = desktop.set_autostart(body.on)
    except (RuntimeError, OSError, KeyError) as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else "Démarrage automatique impossible à régler."
        raise HTTPException(400, detail) from None
    return {"ok": True, "on": body.on, "message": message, "autostart": autostart_state()}


@router.post("/api/settings/open-data")
def open_data_folder():
    """Show JARVIS's data folder in the file manager (a folder, never a file)."""
    folder = config.DATA_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if config.IS_WINDOWS:
            os.startfile(str(folder))  # noqa: S606 - a folder: Explorer opens it
        else:
            opener = shutil.which("open" if config.IS_MAC else "xdg-open")
            if not opener:
                raise OSError("no opener")
            subprocess.Popen([opener, str(folder)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        raise HTTPException(400, f"Impossible d'ouvrir le dossier : {folder}") from None
    return {"ok": True, "path": str(folder)}
