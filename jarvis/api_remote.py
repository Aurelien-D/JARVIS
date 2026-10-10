"""Routes of remote access: the switch, pause, pairing, devices, the full-access
opt-in and the audit trail.

Stubs until the remote core lands: every route exists (so the guard sweeps
cover it) and answers 501.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter()

_NOT_YET = "Pas encore disponible."


@router.get("/api/remote/state")
def get_state():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/state")
def post_state():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/pause")
def post_pause():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/pairing")
def post_pairing():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/pair-request")
def pair_request():
    raise HTTPException(501, _NOT_YET)


@router.get("/api/remote/pair-status")
def pair_status():
    raise HTTPException(501, _NOT_YET)


@router.get("/api/remote/pair-requests")
def pair_requests():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/pair-requests/{request_id}/allow")
def allow_request(request_id: str):
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/pair-requests/{request_id}/deny")
def deny_request(request_id: str):
    raise HTTPException(501, _NOT_YET)


@router.get("/api/remote/devices")
def list_devices():
    raise HTTPException(501, _NOT_YET)


@router.patch("/api/remote/devices/{device_id}")
def rename_device(device_id: str):
    raise HTTPException(501, _NOT_YET)


@router.delete("/api/remote/devices/{device_id}")
def revoke_device(device_id: str):
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/forget")
def forget_this_device():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/complet")
def post_complet():
    raise HTTPException(501, _NOT_YET)


@router.get("/api/remote/audit")
def get_audit():
    raise HTTPException(501, _NOT_YET)
