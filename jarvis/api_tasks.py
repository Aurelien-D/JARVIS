"""Side panel API: Claude Code tasks (list, details, log, cancel, start from the
keyboard, try again, show a file it wrote)."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, confirm, desktop, tasks, tools

router = APIRouter()

COMPLET_REFUSED = "L'accès complet passe par une confirmation : demandez-le à JARVIS."


class TaskIn(BaseModel):
    prompt: str = ""
    title: str = ""
    profile: str = "lecture"
    complexity: str = "normale"
    continue_task: str | None = None


class RevealIn(BaseModel):
    path: str = ""


@router.get("/api/tasks")
def list_tasks():
    return tasks.list_tasks()


@router.post("/api/tasks")
def create_task(body: TaskIn):
    """A task typed by monsieur (composer): no voice model, no OpenAI cost."""
    if tasks.normalize_profile(body.profile) == "complet":
        # Full access always goes through the confirmation card (voice or button).
        raise HTTPException(400, COMPLET_REFUSED)
    try:
        first_line = (body.prompt.strip().splitlines() or [""])[0][:60]
        task = tasks.create_task(body.title or first_line, body.prompt, profile=body.profile,
                                 complexity=body.complexity, continue_task=body.continue_task,
                                 origin="clavier")
    except ValueError as exc:  # empty prompt, daily cap reached: already in French
        raise HTTPException(400, str(exc)) from None
    return tasks.public(task)


def _task(task_id: str) -> dict:
    task = tasks.TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Tâche inconnue.")
    return task


@router.get("/api/task/{task_id}")
def get_task(task_id: str):
    return tasks.public(_task(task_id))


@router.get("/api/task/{task_id}/log")
def get_task_log(task_id: str):
    log = tasks.task_log(task_id)
    if log is None:
        raise HTTPException(404, "Tâche inconnue.")
    return {"id": task_id, "log": log}


@router.post("/api/task/{task_id}/cancel")
def cancel_task(task_id: str):
    return tasks.cancel(task_id)


@router.post("/api/task/{task_id}/retry")
def retry_task(task_id: str):
    """'Réessayer' on a finished task: the same prompt and profile, stored here,
    never sent back by the page. Full access asks first: the request goes to
    the same confirmation store as the voice tool (a card with [Lancer]), and
    nothing starts before monsieur's click."""
    task = _task(task_id)
    if task["status"] in tasks.ACTIVE:
        raise HTTPException(409, "La tâche est encore en cours.")
    if task.get("origin") == "approbation" or not str(task.get("prompt") or "").strip():
        # An approval resumes a session with tools allowed once: never replayed.
        raise HTTPException(400, "Cette tâche ne peut pas être relancée telle quelle : redemandez-la à JARVIS.")
    args = {"title": task.get("title") or "", "prompt": task["prompt"],
            "profile": tasks.normalize_profile(task.get("profile")),
            "complexity": task.get("complexity") or "normale"}
    if task.get("resumed_from"):
        args["continue_task"] = task["resumed_from"]  # the same earlier session again
    if args["profile"] == "complet":
        # The voice tool's own gate parks it; no voice session, so only the
        # card's button can say yes (confirm.decide refuses a voice "oui").
        parked = confirm.gate("delegate_to_claude", args, tools.ToolCtx(session_id=None))
        if not parked:  # confirmations switched off in .env: still never from a button
            raise HTTPException(400, COMPLET_REFUSED)
        return parked
    try:
        fresh = tasks.create_task(args["title"], args["prompt"], profile=args["profile"],
                                  complexity=args["complexity"], continue_task=args.get("continue_task"),
                                  origin="clavier")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"status": "started", "task": tasks.public(fresh)}


@router.post("/api/task/{task_id}/reveal")
def reveal_file(task_id: str, body: RevealIn):
    """'Afficher dans l'explorateur': only a file this task wrote, shown in its
    folder, never opened or run (a .bat or .lnk written by Claude must not start)."""
    task = _task(task_id)
    if not body.path or body.path not in (task.get("files") or []):
        raise HTTPException(403, "Ce fichier n'a pas été écrit par cette tâche.")
    path = Path(body.path)
    if not path.is_absolute():  # Claude works in its own folder
        path = Path(config.WORKDIR) / path
    if not path.exists():
        raise HTTPException(410, f"Le fichier n'existe plus : {path.name}")
    try:
        desktop.reveal_in_explorer(str(path))
    except NotImplementedError as exc:  # this platform (or this version) can't show it
        raise HTTPException(501, str(exc)) from None
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(500, f"Impossible d'afficher le fichier : {exc}") from None
    return {"ok": True, "path": str(path)}
