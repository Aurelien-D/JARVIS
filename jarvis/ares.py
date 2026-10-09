"""A.R.E.S fast lane: agenda, tasks and notes from the local A.R.E.S MCP server
(stub; WP15 fills it in). Until then the family stays hidden.
"""
from fastapi import APIRouter

router = APIRouter()

# Tool family interface (see tools.py).
TOOLS: list = []
HANDLERS: dict = {}
CLIENT_TOOLS: set = set()


def available() -> bool:
    return False


def instructions_block() -> str:
    return ""


def agenda_text() -> str:
    """Today's agenda as text for the instructions and the briefing ('' = nothing)."""
    return ""
