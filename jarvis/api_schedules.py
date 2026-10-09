"""Side panel API: reminders and routines."""
from fastapi import APIRouter

from . import scheduler

router = APIRouter()


@router.get("/api/schedules")
def list_schedules():
    return scheduler.items()


@router.delete("/api/schedules/{item_id}")
def delete_schedule(item_id: str):
    return {"ok": True, "removed": len(scheduler.cancel(item_id))}
