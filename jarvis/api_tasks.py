"""Side panel API: Claude Code tasks (list, details, log, cancel, start from the keyboard)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import tasks

router = APIRouter()


class TaskIn(BaseModel):
    prompt: str = ""
    title: str = ""
    profile: str = "lecture"
    complexity: str = "normale"
    continue_task: str | None = None


@router.get("/api/tasks")
def list_tasks():
    return tasks.list_tasks()


@router.post("/api/tasks")
def create_task(body: TaskIn):
    """A task typed by monsieur (composer): no voice model, no OpenAI cost."""
    if tasks.normalize_profile(body.profile) == "complet":
        # Full access always goes through the confirmation card (voice or button).
        raise HTTPException(400, "L'accès complet passe par une confirmation : demandez-le à JARVIS.")
    try:
        first_line = (body.prompt.strip().splitlines() or [""])[0][:60]
        task = tasks.create_task(body.title or first_line, body.prompt, profile=body.profile,
                                 complexity=body.complexity, continue_task=body.continue_task,
                                 origin="clavier")
    except ValueError as exc:  # empty prompt, daily cap reached: already in French
        raise HTTPException(400, str(exc)) from None
    return tasks.public(task)


@router.get("/api/task/{task_id}")
def get_task(task_id: str):
    task = tasks.TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Tâche inconnue.")
    return tasks.public(task)


@router.get("/api/task/{task_id}/log")
def get_task_log(task_id: str):
    log = tasks.task_log(task_id)
    if log is None:
        raise HTTPException(404, "Tâche inconnue.")
    return {"id": task_id, "log": log}


@router.post("/api/task/{task_id}/cancel")
def cancel_task(task_id: str):
    return tasks.cancel(task_id)
