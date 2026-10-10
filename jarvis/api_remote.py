"""Routes of remote access: the switch, pause, pairing, devices, the full-access
opt-in and the audit trail (spec 3.14).

The remote route table (remote.REMOTE_ALLOW) already keeps the PC-only routes
away from a phone; each PC-only handler still refuses a remote caller, and the
iPhone-only ones (pairing from the phone, forget) refuse the PC: the table does
not protect the local path. Réglages hears every change through a PC-only
'remote' event.
"""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import devices, events

router = APIRouter()

PC_ONLY = "Réservé au PC."
IPHONE_ONLY = "Réservé à l'iPhone."


def _caller(request: Request):
    from . import (
        remote,  # late: remote is imported inside functions everywhere (section 0)
    )
    return remote.caller_of(request)


def _pc(request: Request):
    """The caller, which must be this PC's page."""
    caller = _caller(request)
    if caller.remote:
        raise HTTPException(403, PC_ONLY)
    return caller


def _changed(kind: str, **data) -> None:
    events.publish_pc("remote", {"kind": kind, **data})


def _error(exc) -> HTTPException:
    return HTTPException(getattr(exc, "status", 400), str(exc))

# ---------------------------------------------------------------- the switch and the pause


class StateIn(BaseModel):
    enabled: bool
    host: str | None = None
    login: str | None = None


class PauseIn(BaseModel):
    hours: int


@router.get("/api/remote/state")
def get_state(request: Request):
    from . import remote
    return remote.state_for(_caller(request))


@router.post("/api/remote/state")
def post_state(body: StateIn, request: Request):
    from . import remote
    caller = _pc(request)
    try:
        state = remote.set_enabled(body.enabled, host=body.host, login=body.login, by=caller)
    except remote.RemoteError as exc:
        raise HTTPException(400, str(exc)) from None
    _changed("state")
    return state


@router.post("/api/remote/pause")
def post_pause(body: PauseIn, request: Request):
    from . import remote
    try:
        until = remote.pause(body.hours, by=_caller(request))
    except remote.RemoteError as exc:
        raise _error(exc) from None
    _changed("state")
    return {"paused_until": until}

# ---------------------------------------------------------------- pairing


class PairingIn(BaseModel):
    open: bool


class PairRequestIn(BaseModel):
    name: str = ""


class AllowIn(BaseModel):
    name: str | None = None
    code: str | None = None


@router.post("/api/remote/pairing")
def post_pairing(body: PairingIn, request: Request):
    from . import remote
    _pc(request)
    if body.open:
        try:
            remote.open_pairing()
        except remote.RemoteError as exc:
            raise _error(exc) from None
    else:
        remote.close_pairing()
    host = remote.host()
    _changed("state")
    until = remote.pairing_until()
    return {"open": bool(until), "until": until, "url": f"https://{host}/" if host else ""}


@router.post("/api/remote/pair-request")
def pair_request(request: Request, body: PairRequestIn | None = None):
    from . import remote
    caller = _caller(request)
    if not caller.remote:
        raise HTTPException(403, IPHONE_ONLY)
    try:
        answer, cookie = remote.request_pairing(caller, (body.name if body else "")[:200],
                                                request.headers.get("user-agent", ""))
    except remote.RemoteError as exc:
        raise _error(exc) from None
    response = JSONResponse(answer)
    response.headers.append("set-cookie", remote.cookie_header(cookie, remote.PAIR_MAX_AGE, remote.PAIR_COOKIE))
    return response


@router.get("/api/remote/pair-status")
def pair_status(request: Request):
    from . import remote
    caller = _caller(request)
    if not caller.remote:
        raise HTTPException(403, IPHONE_ONLY)
    try:
        answer, cookie = remote.pairing_status(caller, request.cookies.get(remote.PAIR_COOKIE, ""))
    except remote.RemoteError as exc:
        raise _error(exc) from None
    response = JSONResponse(answer)
    if cookie:  # delivered once: the device's own cookie, and the pairing one goes
        response.headers.append("set-cookie", remote.cookie_header(cookie))
        response.headers.append("set-cookie", remote.cookie_header("", name=remote.PAIR_COOKIE))
    return response


@router.get("/api/remote/pair-requests")
def pair_requests(request: Request):
    from . import remote
    _pc(request)
    return remote.pairing_requests()


@router.post("/api/remote/pair-requests/{request_id}/allow")
def allow_request(request_id: str, request: Request, body: AllowIn | None = None):
    from . import remote
    _pc(request)
    try:
        allowed = remote.allow_pairing(request_id, name=body.name if body else None, code=body.code if body else None)
    except remote.RemoteError as exc:
        raise _error(exc) from None
    return {"ok": True, "request": allowed}


@router.post("/api/remote/pair-requests/{request_id}/deny")
def deny_request(request_id: str, request: Request):
    from . import remote
    _pc(request)
    try:
        remote.deny_pairing(request_id)
    except remote.RemoteError as exc:
        raise _error(exc) from None
    return {"ok": True}

# ---------------------------------------------------------------- devices


class RenameIn(BaseModel):
    name: str


@router.get("/api/remote/devices")
def list_devices(request: Request):
    _pc(request)
    return [devices.public(d) for d in devices.active()]


@router.patch("/api/remote/devices/{device_id}")
def rename_device(device_id: str, body: RenameIn, request: Request):
    from . import audit
    caller = _pc(request)
    try:
        device = devices.rename(device_id, body.name[:200])
    except KeyError:
        raise HTTPException(404, "Appareil inconnu.") from None
    audit.event(caller, "device", device=device_id, text=f"Appareil renommé « {device['name']} ».")
    _changed("devices")
    return device


@router.delete("/api/remote/devices/{device_id}")
def revoke_device(device_id: str, request: Request):
    from . import remote
    caller = _pc(request)
    try:
        cancelled = remote.revoke_device(device_id, by=caller)
    except KeyError:
        raise HTTPException(404, "Appareil inconnu.") from None
    return {"ok": True, "cancelled_tasks": cancelled}


@router.post("/api/remote/forget")
def forget_this_device(request: Request):
    """The phone forgets itself: revoked at once, its cookie cleared."""
    from . import remote
    caller = _caller(request)
    if caller.kind == "pc":
        raise HTTPException(403, IPHONE_ONLY)
    if caller.kind != "app":
        raise HTTPException(401, "Appareil non associé : associez-le depuis le PC.")
    try:
        remote.revoke_device(caller.device_id, by=caller)
    except KeyError:
        raise HTTPException(404, "Appareil inconnu.") from None
    response = JSONResponse({"ok": True})
    response.headers.append("set-cookie", remote.cookie_header(""))
    return response

# ---------------------------------------------------------------- the full-access opt-in and the audit


class CompletIn(BaseModel):
    duration: str


@router.post("/api/remote/complet")
def post_complet(body: CompletIn, request: Request):
    from . import remote
    _pc(request)
    try:
        until = remote.set_complet(body.duration)
    except remote.RemoteError as exc:
        raise HTTPException(400, str(exc)) from None
    _changed("state")
    return {"complet_until": until}


@router.get("/api/remote/audit")
def get_audit(request: Request, limit: int = Query(50, ge=1, le=200)):
    from . import audit
    _pc(request)
    return {"lines": audit.tail(limit)}
