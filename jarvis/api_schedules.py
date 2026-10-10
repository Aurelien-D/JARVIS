"""Side panel API: reminders and routines (list, add, edit, snooze, delete)
and the « JARVIS a remarqué » suggestions.

A full-access routine is never created from here (it needs monsieur's "oui"
through the voice tool's confirmation), and an existing one's instruction
and frequency can't be changed: only its label and its time, and only from
the PC (a phone may still delete it). What is added from a phone carries
its origin (via).
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import remarques, scheduler

router = APIRouter()

COMPLET_PC_ONLY = "Routine avec accès complet : modifiable sur le PC seulement."


class ScheduleIn(BaseModel):
    kind: str = "reminder"
    title: str = ""
    text: str = ""
    at: str | None = None
    delay_minutes: float | str | None = None
    repeat: str = "none"
    days: list[str | int] | str | None = None
    profile: str = "recherche"
    complexity: str = "normale"


class ScheduleEdit(BaseModel):
    title: str | None = None
    text: str | None = None
    at: str | None = None
    delay_minutes: float | str | None = None
    due: float | None = None  # epoch seconds (the panel's date and time fields)
    repeat: str | None = None
    days: list[str | int] | str | None = None


class SnoozeIn(BaseModel):
    minutes: float | str = 10


@router.get("/api/schedules")
def list_schedules():
    return scheduler.items()


def _caller(request: Request):
    from . import remote
    return remote.caller_of(request)


def _pc_only_if_complet(request: Request, item_id: str):
    """A phone never re-times nor snoozes a full-access routine (403)."""
    if not _caller(request).remote:
        return
    item = next((i for i in scheduler.items() if i["id"] == item_id), None)
    if item is not None and item.get("kind") == "task" and item.get("profile") == "complet":
        raise HTTPException(403, COMPLET_PC_ONLY)


@router.post("/api/schedules")
def add_schedule(body: ScheduleIn, request: Request):
    try:
        item = scheduler.add(body.kind, body.title, body.text, at=body.at, delay_minutes=body.delay_minutes,
                             repeat=body.repeat, profile=body.profile, complexity=body.complexity,
                             days=body.days, via=_caller(request).origin)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "item": item, "scheduled": scheduler.describe(item)}


@router.patch("/api/schedules/{item_id}")
def edit_schedule(item_id: str, body: ScheduleEdit, request: Request):
    _pc_only_if_complet(request, item_id)
    try:
        item = scheduler.update(item_id, title=body.title, text=body.text, at=body.at,
                                delay_minutes=body.delay_minutes, due=body.due, repeat=body.repeat,
                                days=body.days)
    except KeyError:
        raise HTTPException(404, scheduler.T.not_found) from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "item": item, "scheduled": scheduler.describe(item)}


@router.post("/api/schedules/{item_id}/snooze")
def snooze_schedule(item_id: str, request: Request, body: SnoozeIn | None = None):
    """'+10 min', '+1 h', 'Demain' on a reminder that just went off."""
    _pc_only_if_complet(request, item_id)
    try:
        item = scheduler.snooze(item_id, (body or SnoozeIn()).minutes)
    except scheduler.Ambiguous as exc:
        raise HTTPException(409, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "item": item, "scheduled": scheduler.describe(item)}


@router.delete("/api/schedules/{item_id}")
def delete_schedule(item_id: str):
    # By id only: words in a URL must never remove several reminders at once.
    return {"ok": True, "removed": len(scheduler.remove(item_id))}


@router.get("/api/remarques")
def list_remarques():
    return remarques.public(remarques.items())


@router.post("/api/remarques/{key}/dismiss")
def dismiss_remarque(key: str):
    try:
        return {"ok": True, **remarques.dismiss(key)}
    except KeyError:
        raise HTTPException(404, remarques.T.unknown) from None
