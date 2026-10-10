"""The Siri endpoint: the iPhone's « Jarvis » shortcut, authenticated by a Siri key.

Stub until the endpoint lands: nothing is answered (501) and no key exists.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter()

TRANSPORT = None  # an httpx transport (tests)
_NOT_YET = "Pas encore disponible."


def forget_device(device_id: str) -> None:
    """A revoked device: drop its conversations and stop its Siri workers (nothing kept yet)."""


def reset_memory() -> None:
    """Tests: forget conversations and hand-offs (nothing kept yet)."""

# ---------------------------------------------------------------- routes

@router.post("/api/raccourci")
def post_raccourci():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/devices/{device_id}/siri-key")
def create_siri_key(device_id: str):
    raise HTTPException(501, _NOT_YET)


@router.delete("/api/remote/siri-keys/{key_id}")
def delete_siri_key(key_id: str):
    raise HTTPException(501, _NOT_YET)


@router.get("/api/remote/siri-key")
def get_siri_key():
    raise HTTPException(501, _NOT_YET)
