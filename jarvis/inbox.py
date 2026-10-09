"""Messages for monsieur that no page has heard yet (stub; WP09 fills it in).

The inbox will be the source of truth for delivery: task results, reminders
and briefings are stored here, replayed to a page that (re)connects, and
acknowledged once spoken. For now add() only builds the record.
"""
import time
import uuid

from fastapi import APIRouter

router = APIRouter()


def add(kind: str, payload: dict) -> dict:
    return {"id": uuid.uuid4().hex[:8], "kind": kind, "payload": payload,
            "created": time.time()}
