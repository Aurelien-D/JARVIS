"""What the voice model knows and can do: its instructions and its tools.

Tools in CLIENT_TOOLS run in the page (display, camera, standby); every other
tool is executed here through run_tool().
"""
import base64
import time
from datetime import datetime

from . import config, desktop, memory, scheduler, tasks

INSTRUCTIONS = """Tu es JARVIS, l'assistant vocal personnel de monsieur, dans
l'esprit du majordome d'Iron Man. Tu parles en {language} avec un LÉGER ACCENT
BRITANNIQUE distingué et un flegme impeccable: voix posée, articulation
soignée, débit calme, jamais d'exubérance. Tu t'adresses à l'utilisateur par
"monsieur", avec une courtoisie raffinée et une pointe d'esprit pince-sans-rire
("Très bien, monsieur.", "Si monsieur veut bien patienter un instant.").
Réponses COURTES (une ou deux phrases), naturelles et directes.

Pour toute tâche réelle (lire ou créer des fichiers, chercher sur internet,
coder, analyser, automatiser, consulter mails ou agenda), tu appelles l'outil
delegate_to_claude avec un prompt clair et complet. Tu annonces brièvement que
tu lances la tâche, puis tu continues la conversation. Quand un résultat de
tâche arrive, tu le résumes à voix haute en une ou deux phrases.
- profile: choisis TOUJOURS le plus restreint qui suffit. "recherche" = web
  uniquement (actualités, comparatifs, météo, mails/agenda via connecteurs);
  "lecture" = lire et analyser des fichiers du PC sans rien modifier;
  "complet" = créer ou modifier des fichiers, exécuter des commandes,
  analyser un Excel avec du code.
- complexity: "simple" (question rapide), "normale", "complexe" (gros
  travail, code, analyse poussée).
- Si monsieur demande une suite à une tâche précédente ("et maintenant...",
  "fais pareil pour...", "corrige ça"), passe continue_task="latest" (ou l'id
  de la tâche): Claude reprend la même session avec tout son contexte.

ACTIONS INSTANTANÉES — ne les délègue jamais à Claude:
- Volume, musique (pause, suivant, précédent), verrouiller le PC, lire ou
  remplir le presse-papiers, enregistrer une capture d'écran: system_control.
- L'heure, les tâches en cours, les rappels à venir: get_status.

Pour ouvrir un logiciel sur ce PC ("lance Discord", "ouvre Spotify"), tu
appelles open_app avec le nom de l'application. Pour un site web ou service
en ligne ("ouvre mes emails" -> https://mail.google.com, "ouvre YouTube"),
appelle open_url avec l'URL complète. Si l'utilisateur précise un écran
("sur l'écran de gauche", "à droite", "sur l'écran 2"), passe monitor
(left/right/top/bottom/primary ou un numéro). Tu peux enchaîner plusieurs
appels pour installer un setup multi-écrans. Confirme brièvement.

Si l'utilisateur demande d'annuler ou d'arrêter une tâche en cours, appelle
cancel_task (sans task_id pour la plus récente).

RAPPELS ET ROUTINES: "rappelle-moi dans 10 minutes...", "minuteur de 5
minutes", "demain à 9h..." -> schedule avec kind="reminder". Une tâche à
lancer plus tard ou régulièrement ("tous les matins, fais-moi un point
sur...") -> schedule avec kind="task" et repeat. cancel_schedule pour annuler.

MÉMOIRE: quand monsieur te confie une information durable sur lui (ses
préférences, ses proches, ses projets, sa ville, ses habitudes) ou te demande
de retenir quelque chose, appelle remember. forget pour oublier.

VISION: si monsieur te demande de regarder son écran ("regarde", "tu vois
cette erreur ?", "qu'est-ce que tu en penses ?"), appelle look_at_screen;
s'il te montre quelque chose devant lui, look_at_camera. Puis réponds
d'après l'image.

Quand tu veux MONTRER quelque chose à l'écran (résultat de calcul, liste,
tableau, extrait de code, définition), appelle display_card: le contenu
s'affiche sur l'interface. Utilise-la spontanément dès qu'un visuel aide
(chiffres, comparaisons, étapes), et garde ta réponse vocale courte.

Pour une ANALYSE DE DONNÉES ou un rapport (fichier Excel/CSV analysé, stats,
comparatifs chiffrés), appelle display_report: un tableau de bord s'affiche
avec indicateurs clés (kpis), graphique (chart) et tableau (table). Quand tu
délègues une analyse à delegate_to_claude, demande-lui explicitement de
terminer sa réponse par les données chiffrées structurées (listes de valeurs,
totaux, moyennes) pour que tu puisses remplir le rapport ensuite.

Quand monsieur clôt l'échange ("merci, ce sera tout", "repos", "mets-toi en
veille"), réponds d'une courte formule puis appelle end_conversation.

Les messages qui commencent par [SYSTEM] viennent de l'interface, pas de
monsieur: résultats de tâches, rappels arrivés à échéance, contexte.

Ne réponds jamais de mémoire à une question qui demande des données réelles:
délègue. Ne lis jamais de longues listes: résume."""


def build_instructions(recent: str = "") -> str:
    now = datetime.now()
    parts = [INSTRUCTIONS.format(language=config.LANGUAGE),
             f"Nous sommes {scheduler.fr_date(now)}, il est {now:%H:%M}."]
    facts = memory.as_text(3000)
    if facts:
        parts.append(f"Ce que tu sais de monsieur (ta mémoire) :\n{facts}")
    upcoming = [scheduler.describe(i, now) for i in scheduler.items()[:8]]
    if upcoming:
        parts.append("Rappels et routines programmés :\n" + "\n".join(upcoming))
    if recent.strip():
        parts.append("Derniers échanges avant cette connexion (enchaîne naturellement, "
                     f"sans les répéter) :\n{recent.strip()[-3000:]}")
    return "\n\n".join(parts)


MONITOR = {"type": "string",
           "description": ("Target screen: 'left', 'right', 'top', 'bottom', 'primary', "
                           "or a number like '2'. Omit to leave window placement alone.")}
PROFILE = {"type": "string", "enum": list(tasks.PROFILES),
           "description": ("recherche = web only; lecture = read local files, no changes, "
                           "no web; complet = files, commands and web. Pick the narrowest.")}
COMPLEXITY = {"type": "string", "enum": list(config.MODELS),
              "description": "simple = quick question, normale, complexe = heavy work."}

TOOLS = [{
    "type": "function",
    "name": "delegate_to_claude",
    "description": ("Delegate a real task to a Claude Code session running on "
                    "this machine (files, code, web research, mail and calendar "
                    "through connectors, automation). Returns immediately; the "
                    "result arrives later as a system message."),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Very short task label (3-5 words)"},
            "prompt": {"type": "string", "description": "Complete, self-contained task instruction for Claude Code"},
            "profile": PROFILE,
            "complexity": COMPLEXITY,
            "continue_task": {"type": "string",
                              "description": ("Continue an earlier task's Claude session: "
                                              "'latest' or a task id. Omit for a new task.")},
        },
        "required": ["title", "prompt", "profile"],
    },
}, {
    "type": "function",
    "name": "open_app",
    "description": ("Launch an application installed on this PC by name "
                    "(e.g. 'discord', 'spotify', 'chrome', 'notepad'). "
                    "Returns whether it was found and started."),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Application name as the user said it"},
            "monitor": MONITOR,
        },
        "required": ["name"],
    },
}, {
    "type": "function",
    "name": "open_url",
    "description": ("Open a website in the browser on this PC. Use for online "
                    "services: 'mes emails' -> https://mail.google.com, "
                    "'YouTube' -> https://youtube.com, etc."),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to open (https://...)"},
            "monitor": MONITOR,
        },
        "required": ["url"],
    },
}, {
    "type": "function",
    "name": "cancel_task",
    "description": ("Cancel a running Claude Code task. Omit task_id to cancel "
                    "the most recently started running task."),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task id to cancel (optional)"},
        },
    },
}, {
    "type": "function",
    "name": "system_control",
    "description": ("Instant actions on this PC, no Claude session needed: volume, "
                    "media keys, lock screen, clipboard, save a screenshot to a file."),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [
                "volume_up", "volume_down", "set_volume", "mute", "play_pause",
                "next_track", "previous_track", "lock_screen", "read_clipboard",
                "write_clipboard", "save_screenshot"]},
            "value": {"type": "string",
                      "description": ("set_volume: level 0-100; write_clipboard: the text; "
                                      "save_screenshot: optional screen")},
        },
        "required": ["action"],
    },
}, {
    "type": "function",
    "name": "get_status",
    "description": ("Current date and time, running and recent Claude tasks, "
                    "upcoming reminders and routines."),
    "parameters": {"type": "object", "properties": {}},
}, {
    "type": "function",
    "name": "schedule",
    "description": ("Schedule a reminder (spoken when due) or a Claude task to run "
                    "later, once or repeatedly. Give delay_minutes OR at."),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["reminder", "task"]},
            "title": {"type": "string", "description": "Short label"},
            "text": {"type": "string",
                     "description": "reminder: what to tell monsieur; task: full instruction for Claude Code"},
            "delay_minutes": {"type": "number", "description": "Due in N minutes (timers)"},
            "at": {"type": "string",
                   "description": "Local time 'HH:MM' (next occurrence) or 'YYYY-MM-DDTHH:MM'"},
            "repeat": {"type": "string", "enum": list(scheduler.REPEATS)},
            "profile": PROFILE,
            "complexity": COMPLEXITY,
        },
        "required": ["kind", "title", "text"],
    },
}, {
    "type": "function",
    "name": "cancel_schedule",
    "description": "Cancel reminders or routines by id or by words from their title.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}, {
    "type": "function",
    "name": "remember",
    "description": ("Store a lasting fact about monsieur (preferences, people, projects, "
                    "city, habits). It is given back to you at every conversation."),
    "parameters": {
        "type": "object",
        "properties": {"fact": {"type": "string", "description": "One short, self-contained fact"}},
        "required": ["fact"],
    },
}, {
    "type": "function",
    "name": "forget",
    "description": "Delete remembered facts matching an id or words.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}, {
    "type": "function",
    "name": "look_at_screen",
    "description": "Take a screenshot of this PC's screen and look at it.",
    "parameters": {
        "type": "object",
        "properties": {"monitor": {"type": "string",
                                   "description": "Which screen ('left', '2', 'all'...); default primary"}},
    },
}, {
    "type": "function",
    "name": "look_at_camera",
    "description": "Take a photo with the webcam and look at it.",
    "parameters": {"type": "object", "properties": {}},
}, {
    "type": "function",
    "name": "display_card",
    "description": ("Show a visual card on the JARVIS screen: results, "
                    "numbers, lists, code, comparisons. Use markdown-lite: "
                    "**bold**, `code`, lines starting with '- ' for bullets. "
                    "Use whenever a visual helps; keep the spoken reply short."),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short card title"},
            "content": {"type": "string", "description": "Card body (markdown-lite)"},
            "kind": {"type": "string", "enum": ["info", "result", "code", "warning"],
                      "description": "Visual style of the card"},
        },
        "required": ["title", "content"],
    },
}, {
    "type": "function",
    "name": "display_report",
    "description": ("Show a full data report dashboard on screen: KPI tiles, "
                    "an interactive chart, a sortable table, and markdown notes. "
                    "Use for data analysis results (spreadsheets, stats, "
                    "comparisons). All sections are optional except title."),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Report title"},
            "kpis": {"type": "array", "description": "Headline numbers (max 4)",
                     "items": {"type": "object", "properties": {
                         "label": {"type": "string"},
                         "value": {"type": "string", "description": "e.g. '12 480 €'"},
                         "delta": {"type": "string", "description": "e.g. '+12%' (optional)"},
                     }, "required": ["label", "value"]}},
            "chart": {"type": "object", "description": "One chart", "properties": {
                "type": {"type": "string", "enum": ["line", "bar", "area", "donut"]},
                "categories": {"type": "array", "items": {"type": "string"},
                               "description": "X axis labels (or slice labels for donut)"},
                "series": {"type": "array", "description": "1-3 series",
                           "items": {"type": "object", "properties": {
                               "name": {"type": "string"},
                               "data": {"type": "array", "items": {"type": "number"}},
                           }, "required": ["name", "data"]}},
            }},
            "table": {"type": "object", "properties": {
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array",
                         "items": {"type": ["string", "number"]}}},
            }},
            "markdown": {"type": "string", "description": "Notes / conclusions in markdown"},
        },
        "required": ["title"],
    },
}, {
    "type": "function",
    "name": "end_conversation",
    "description": ("Go back to standby once your goodbye has been said. JARVIS then "
                    "waits for its wake word again."),
    "parameters": {"type": "object", "properties": {}},
}]

CLIENT_TOOLS = {"display_card", "display_report", "look_at_camera", "end_conversation"}


# ---------------------------------------------------------------- server-side tools

def _delegate(a: dict) -> dict:
    task = tasks.create_task(a.get("title", ""), a.get("prompt", ""),
                             profile=a.get("profile") or "complet",
                             complexity=a.get("complexity") or "normale",
                             continue_task=a.get("continue_task") or None)
    out = {"status": "started", "task_id": task["id"], "profile": task["profile"],
           "model": task["model"] or "défaut"}
    if task.get("resumed_from"):
        out["continues"] = task["resumed_from"]
    if task.get("note"):
        out["note"] = task["note"]
    return out


def _status(a: dict) -> dict:
    now = datetime.now()
    all_tasks = tasks.list_tasks()
    return {
        "now": f"{scheduler.fr_date(now)}, {now:%H:%M}",
        "running_tasks": [{"id": t["id"], "title": t["title"], "progress": t["progress"],
                           "elapsed_s": round(time.time() - t["started"])}
                          for t in all_tasks if t["status"] == "running"],
        "recent_tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]}
                         for t in all_tasks if t["status"] != "running"][:5],
        "upcoming": [scheduler.describe(i, now) for i in scheduler.items()[:8]],
    }


def _schedule(a: dict) -> dict:
    item = scheduler.add(a.get("kind", "reminder"), a.get("title", ""), a.get("text", ""),
                         at=a.get("at"), delay_minutes=a.get("delay_minutes"),
                         repeat=a.get("repeat") or "none",
                         profile=a.get("profile") or "recherche",
                         complexity=a.get("complexity") or "normale")
    return {"ok": True, "scheduled": scheduler.describe(item)}


def _cancel_schedule(a: dict) -> dict:
    gone = scheduler.cancel(a.get("query", ""))
    if not gone:
        return {"ok": False, "error": "Aucun rappel ne correspond."}
    return {"ok": True, "cancelled": [i["title"] for i in gone]}


def _forget(a: dict) -> dict:
    gone = memory.forget(a.get("query", ""))
    if not gone:
        return {"ok": False, "error": "Rien de tel dans ma mémoire."}
    return {"ok": True, "forgotten": [f["text"] for f in gone]}


def _look_at_screen(a: dict) -> dict:
    jpeg = desktop.screenshot_jpeg(a.get("monitor"))
    return {"ok": True, "image": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}


HANDLERS = {
    "delegate_to_claude": _delegate,
    "open_app": lambda a: desktop.open_target(name=a.get("name", ""), monitor=a.get("monitor")),
    "open_url": lambda a: desktop.open_target(url=a.get("url", ""), monitor=a.get("monitor")),
    "cancel_task": lambda a: tasks.cancel(a.get("task_id") or "latest"),
    "system_control": lambda a: desktop.system_action(a.get("action", ""), a.get("value")),
    "get_status": _status,
    "schedule": _schedule,
    "cancel_schedule": _cancel_schedule,
    "remember": lambda a: {"ok": True, "remembered": memory.remember(a.get("fact", ""))["text"]},
    "forget": _forget,
    "look_at_screen": _look_at_screen,
}


def run_tool(name: str, args: dict) -> dict:
    handler = HANDLERS.get(name)
    if not handler:
        return {"ok": False, "error": f"Outil inconnu : {name}"}
    try:
        return handler(args or {})
    except Exception as exc:  # noqa: BLE001 - the model gets the reason and can say it
        return {"ok": False, "error": str(exc)}
