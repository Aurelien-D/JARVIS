"""Side panel API: remembered facts."""
from fastapi import APIRouter

from . import memory

router = APIRouter()


@router.get("/api/memory")
def list_memory():
    return memory.facts()


@router.delete("/api/memory/{fact_id}")
def delete_memory(fact_id: str):
    return {"ok": True, "removed": len(memory.forget(fact_id))}
