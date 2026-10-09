"""Costs: voice tokens and Claude task spend, with a daily cap (stub; WP18
fills it in). Until then nothing is counted and nothing is capped.
"""
from fastapi import APIRouter

router = APIRouter()


def claude_spent_today() -> float:
    return 0.0


def over_daily_cap() -> bool:
    return False
