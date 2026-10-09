"""Side panel API: Claude Code tasks (list, details, cancel)."""
from fastapi import APIRouter, HTTPException

from . import tasks

router = APIRouter()


@router.get("/api/tasks")
def list_tasks():
    return tasks.list_tasks()


@router.get("/api/task/{task_id}")
def get_task(task_id: str):
    task = tasks.TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    return tasks.public(task)


@router.post("/api/task/{task_id}/cancel")
def cancel_task(task_id: str):
    return tasks.cancel(task_id)
