"""Tailscale helper: detection, whois, the Serve status and « Publier sur Tailscale ».

Stub until the helper lands: Tailscale is never looked for nor run, and the
routes answer 501. publish() and unpublish() never raise (remote.set_enabled
calls them best effort).
"""
from fastapi import APIRouter, HTTPException

from . import config

router = APIRouter()

RUN = None  # tests replace it: fn(args: list[str], timeout: float) -> subprocess.CompletedProcess
_NOT_YET = "Pas encore disponible."


def exe_path() -> str | None:
    return None


def self_info() -> dict:
    return {"installed": False, "running": False, "ips": []}


def whois(ip: str) -> dict:
    return {}


def serve_status() -> dict:
    return {"state": "no_tailscale", "detail": "", "url": ""}


def publish() -> dict:
    return {"ok": False, "state": "no_tailscale", "consent_url": "", "error": _NOT_YET}


def unpublish() -> dict:
    return {"ok": False, "state": "no_tailscale", "consent_url": "", "error": _NOT_YET}


def manual_command(full_path: bool = False) -> str:
    """The Serve command shown with [Copier]; full_path: PowerShell with the literal install path."""
    args = f"serve --bg --https=443 http://127.0.0.1:{config.REMOTE_PORT}"
    return f'& "C:\\Program Files\\Tailscale\\tailscale.exe" {args}' if full_path else f"tailscale {args}"


def start_watch() -> None:
    """The periodic Serve check (nothing to watch yet)."""


def stop_watch() -> None:
    """Stops the periodic Serve check."""


def reset_memory() -> None:
    """Tests: forget the caches and the running publish process."""

# ---------------------------------------------------------------- routes (PC only)

@router.get("/api/remote/serve")
def get_serve():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/serve/publish")
def post_publish():
    raise HTTPException(501, _NOT_YET)


@router.post("/api/remote/serve/unpublish")
def post_unpublish():
    raise HTTPException(501, _NOT_YET)
