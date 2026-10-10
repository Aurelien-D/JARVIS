"""ntfy notifications on the iPhone (minimal text, never a task's output).

Stub until ntfy lands: nothing is ever sent and the routes answer 501.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter()

TRANSPORT = None  # an httpx transport (tests)
_NOT_YET = "Pas encore disponible."


def reset_memory() -> None:
    """Tests: forget the send windows (nothing kept yet)."""

# ---------------------------------------------------------------- routes

@router.get("/api/notify")
def get_notify():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/notify/test")
def post_test():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/notify/topic")
def post_topic():
    raise HTTPException(501, _NOT_YET)
