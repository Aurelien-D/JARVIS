"""Confirmation of risky actions (stub; WP03 fills it in).

Every tool call goes through gate() before it runs. Later, gate() will park a
risky call (full-access task, unknown link after untrusted data...) in a
server-side pending store and answer needs_confirmation; the action then runs
only on a UI button or a confirm tool backed by a later user turn. For now
nothing is gated.

Voice sessions are identified by an id minted with each ephemeral key, so the
store can tie a confirmation to the session that asked for it.
"""
import uuid

from fastapi import APIRouter

router = APIRouter()

# Tool family interface (see tools.py): the confirm tool arrives with WP03.
TOOLS: list = []
HANDLERS: dict = {}
CLIENT_TOOLS: set = set()


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""


def gate(name: str, args: dict, ctx) -> dict | None:
    """None lets the call run; a dict is returned to the model instead."""
    return None


def new_session() -> str:
    return uuid.uuid4().hex


def mark_turn(sid: str | None):
    """Monsieur spoke or typed in this session (a confirmation needs a fresh turn)."""


def mark_tainted(sid: str | None, reason: str):
    """Untrusted data (web page, screen, notes) entered this session."""
