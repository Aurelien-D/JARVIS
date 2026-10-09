"""Conversation journal: what was said, kept on this PC for JOURNAL_DAYS
(stub; WP14 fills it in with the recall tool and /api/journal).
"""
from fastapi import APIRouter

router = APIRouter()

# Tool family interface (see tools.py).
TOOLS: list = []
HANDLERS: dict = {}
CLIENT_TOOLS: set = set()


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
