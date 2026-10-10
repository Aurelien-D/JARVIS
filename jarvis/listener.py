"""The second loopback listener, the only door Tailscale Serve uses.

Stub until the remote core lands: nothing is ever bound, so no request can
arrive on REMOTE_PORT.
"""
from . import config


def configure(app) -> None:
    """The app both listeners serve (server.py calls this once it is built)."""


def start() -> dict:
    return state()


def stop() -> None:
    """Safe to call when nothing runs."""


def state() -> dict:
    return {"running": False, "port": config.REMOTE_PORT, "error": ""}


def start_if_enabled() -> None:
    """At startup: start the listener when remote access is on (it never is yet)."""
