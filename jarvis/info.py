"""Instant information: weather and news headlines (stub; WP16 fills it in).

One module serves both the voice tool and the morning briefing.
"""

# Tool family interface (see tools.py).
TOOLS: list = []
HANDLERS: dict = {}
CLIENT_TOOLS: set = set()


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
