"""Remote access audit trail and security alerts.

Stub until the remote core lands: nothing is written and no alert is raised.
The names and signatures are the shared contract other modules call.
"""

# fn(kind, ntfy_text) for alerts whose ALERTS[kind]["ntfy"] is True. Hooks get
# the kind's fixed sentence, never the PC text (no name, IP or login leaves the PC).
ALERT_HOOKS: list = []
# kind -> {"title", "toast", "ntfy", "dedupe_s", "ntfy_text"}
ALERTS: dict = {}


def event(caller, kind: str, **fields) -> None:
    """One audit line; never raises."""


def alert(kind: str, text: str, caller=None, **fields) -> None:
    """An audit line, then (unless deduplicated) a PC toast, a PC warning card and the hooks."""


def tail(limit: int = 50) -> list[dict]:
    """The last audit lines, newest first."""
    return []


def reset_memory() -> None:
    """Tests: forget the dedupe and throttle windows."""
