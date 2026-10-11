"""Side panel API: remembered facts (list, correct, forget, bring back)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import memory

router = APIRouter()

NOT_FOUND = "Souvenir introuvable : il a peut-être déjà été oublié."
NOT_IN_TRASH = "Ce souvenir n'est plus dans la corbeille (gardée 7 jours)."


class FactIn(BaseModel):
    text: str


@router.get("/api/memory")
def list_memory():
    return memory.facts()


@router.patch("/api/memory/{fact_id}")
def edit_memory(fact_id: str, body: FactIn, request: Request):
    from . import remote
    try:
        fact = memory.edit(fact_id, body.text, via=remote.caller_of(request).origin)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if fact is None:
        raise HTTPException(404, NOT_FOUND)
    return {"ok": True, "fact": fact}


@router.delete("/api/memory/{fact_id}")
def delete_memory(fact_id: str):
    # By id only: words in a URL must never forget several facts at once.
    return {"ok": True, "removed": len(memory.forget_id(fact_id))}


@router.post("/api/undo/{fact_id}")
def undo_forget(fact_id: str):
    """'Annuler' after a fact was forgotten (by voice or from the panel)."""
    fact = memory.undo(fact_id)
    if fact is None:
        raise HTTPException(404, NOT_IN_TRASH)
    return {"ok": True, "fact": fact}
