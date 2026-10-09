"""The OpenAI Realtime voice session: what it is configured with, and minting
the ephemeral secret the page uses to open it (the API key never leaves this
PC: the browser only ever sees a short-lived key).
"""
import httpx

from . import config, instructions, tools

CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"


class MintError(Exception):
    """OpenAI refused or could not be reached; status is what the page receives."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def session_payload(recent: str = "") -> dict:
    return {
        "session": {
            "type": "realtime",
            "model": config.REALTIME_MODEL,
            "instructions": instructions.build_instructions(recent),
            "tools": tools.session_tools(),
            "audio": {
                "input": {"transcription": {"model": "whisper-1"}},
                "output": {"voice": config.VOICE},
            },
        }
    }


def mint(recent: str = "") -> dict:
    """Ask OpenAI for an ephemeral key; returns its JSON ({"value": "ek_...", ...})."""
    if not config.OPENAI_API_KEY:
        raise MintError(500, "OPENAI_API_KEY manquant: copie .env.example vers .env et mets ta clé.")
    try:
        r = httpx.post(
            CLIENT_SECRETS_URL,
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json=session_payload(recent), timeout=30,
        )
    except httpx.HTTPError as exc:
        raise MintError(502, f"OpenAI injoignable : {exc}") from None
    if r.status_code >= 400:
        # 502, not OpenAI's own status: a 401 here is about the API key, not our page token.
        raise MintError(502, f"OpenAI {r.status_code}: {r.text[:300]}")
    return r.json()
