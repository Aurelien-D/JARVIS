"""The OpenAI Realtime voice session: what it is configured with, and minting
the ephemeral secret the page uses to open it (the API key never leaves this
PC: the browser only ever sees a short-lived key).
"""
import httpx

from . import config, instructions, tools

CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

# Only these two transcription models take a language list and keywords; the
# others take a single ISO-639-1 language. OpenAI refuses a payload with both.
_LANGUAGES_MODELS = ("gpt-transcribe", "gpt-live-transcribe")
_KEYWORDS = ["JARVIS", "Claude", "A.R.E.S", "monsieur"]
_EAGERNESS = ("low", "medium", "high", "auto")
_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
_NOISE = ("near_field", "far_field")

# Transcription models OpenAI refused in this run (retired, not on this
# account...): later sessions go straight to the fallback instead of paying a
# failed round trip on every connection.
_REJECTED_TRANSCRIBE: set = set()

# What the page shows when the session cannot be opened (design spec §11).
ERRORS = {
    "no_key": ("Clé OpenAI absente : ajoutez-la dans Réglages › Connexion "
               "(ou OPENAI_API_KEY dans le fichier .env), puis réessayez."),
    "unauthorized": "Clé OpenAI refusée : vérifiez-la sur platform.openai.com/api-keys.",
    "quota": "Crédit OpenAI épuisé : vérifiez la facturation sur platform.openai.com.",
    "rate": "Trop de demandes envoyées à OpenAI : réessayez dans un instant.",
    "model": ("Modèle vocal indisponible pour votre compte : choisissez-en un autre "
              "dans Réglages › Voix."),
    "transcription": ("Modèle de transcription refusé par OpenAI : choisissez-en un autre "
                      "(JARVIS_TRANSCRIBE_MODEL)."),
    "network": "Pas de connexion à OpenAI : vérifiez internet.",
    "timeout": "OpenAI ne répond pas : réessayez.",
    "server": "OpenAI rencontre un problème : réessayez dans un instant.",
    "other": "OpenAI a refusé d'ouvrir la session",
}


class MintError(Exception):
    """OpenAI refused or could not be reached; status is what the page receives."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _choice(value: str, allowed: tuple, default: str) -> str:
    """A setting from .env or the UI: a typo must not make OpenAI refuse the session."""
    value = (value or "").strip().lower()
    return value if value in allowed else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_reasoning_model(model: str) -> bool:
    """gpt-realtime-2.x reason; sending reasoning settings to older models is untested."""
    return model.startswith("gpt-realtime-2")


def transcribe_model() -> str:
    """The configured transcription model, unless OpenAI already refused it."""
    model = config.TRANSCRIBE_MODEL
    if model in _REJECTED_TRANSCRIBE and config.TRANSCRIBE_FALLBACK:
        return config.TRANSCRIBE_FALLBACK
    return model


def _transcription(model: str) -> dict:
    """What monsieur says, written down (captions, journal, reconnection context)."""
    if model in _LANGUAGES_MODELS:
        return {"model": model, "languages": ["fr"], "keywords": list(_KEYWORDS)}
    return {"model": model, "language": (config.SPEECH_LANG or "fr-FR")[:2].lower()}


def _noise_reduction():
    value = (config.NOISE_REDUCTION or "").strip().lower()
    if value in ("", "off", "none", "aucun", "aucune", "0", "non"):
        return None
    return {"type": value if value in _NOISE else "far_field"}


def session_payload(recent: str = "", transcribe: str | None = None, *, scope: str = "pc") -> dict:
    """The client_secrets body: everything about the session is fixed here, on the
    server, so the page cannot change the instructions or the tools."""
    model = config.REALTIME_MODEL
    session = {
        "type": "realtime",
        "model": model,
        "instructions": instructions.build_instructions(recent, scope=scope),
        "tools": tools.session_tools(scope),
        "tool_choice": "auto",
        "max_output_tokens": 4096,
        # Every response re-bills the conversation: past this share of the
        # context, drop the oldest turns in one go (keeps the cache useful).
        "truncation": {"type": "retention_ratio",
                       "retention_ratio": _clamp(config.RETENTION_RATIO, 0.0, 1.0)},
        "audio": {
            "input": {
                "noise_reduction": _noise_reduction(),
                "transcription": _transcription(transcribe or transcribe_model()),
                # semantic_vad waits for the end of the thought, not just a pause:
                # French dictation has long ones. No idle_timeout_ms (server_vad only).
                "turn_detection": {"type": "semantic_vad",
                                   "eagerness": _choice(config.EAGERNESS, _EAGERNESS, "auto"),
                                   "create_response": True,
                                   "interrupt_response": True},
            },
            "output": {"voice": config.VOICE, "speed": _clamp(config.VOICE_SPEED, 0.25, 1.5)},
        },
    }
    if is_reasoning_model(model):
        session["reasoning"] = {"effort": _choice(config.REASONING_EFFORT, _EFFORTS, "low")}
        session["parallel_tool_calls"] = True
    return {
        # A key that leaks (logs, extensions) dies quickly: the page uses it at once.
        "expires_after": {"anchor": "created_at",
                          "seconds": int(_clamp(config.SECRET_TTL, 10, 7200))},
        "session": session,
    }


def _openai_error(r: httpx.Response) -> tuple:
    """(code, message) from OpenAI's JSON error body, or ('', raw text)."""
    try:
        err = r.json().get("error") or {}
        if isinstance(err, str):
            return "", err
        return str(err.get("code") or err.get("type") or ""), str(err.get("message") or "")
    except (ValueError, AttributeError):
        return "", r.text[:200]


def explain(r: httpx.Response) -> str:
    """OpenAI's refusal in French for the page, with its status for a diagnosis."""
    code, message = _openai_error(r)
    status, text = r.status_code, f"{code} {message}".lower()
    if status == 401:
        key = "unauthorized"
    elif status == 429:
        key = "quota" if "quota" in text else "rate"
    elif "transcri" in text:
        key = "transcription"
    elif status in (400, 403, 404) and "model" in text:
        key = "model"
    elif status >= 500:
        key = "server"
    else:
        # OpenAI may quote the key back: the page shows (and logs) this text.
        if config.OPENAI_API_KEY:
            message = message.replace(config.OPENAI_API_KEY, "sk-…")
        short = message.strip()[:160]
        reason = f" : {short}" if short else ""
        return f"{ERRORS['other']}{reason} (OpenAI {status})"
    return f"{ERRORS[key]} (OpenAI {status})"


def _post(payload: dict) -> httpx.Response:
    try:
        return httpx.post(
            CLIENT_SECRETS_URL,
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            # Under the page's own 20 s limit, so the French reason gets there first.
            json=payload, timeout=15,
        )
    except httpx.TimeoutException:
        raise MintError(504, ERRORS["timeout"]) from None
    except httpx.HTTPError:
        raise MintError(502, ERRORS["network"]) from None


def mint(recent: str = "", *, scope: str = "pc") -> dict:
    """Ask OpenAI for an ephemeral key; returns its JSON ({"value": "ek_...", ...}).
    scope: the caller's kind ("pc", "app"), which sets its instructions and tools."""
    if not config.OPENAI_API_KEY:
        raise MintError(500, ERRORS["no_key"])
    model = transcribe_model()
    r = _post(session_payload(recent, model, scope=scope))
    fallback = config.TRANSCRIBE_FALLBACK
    if (r.status_code == 400 and "transcription" in r.text.lower()
            and fallback and fallback != model):
        # The transcription model was refused (retired, not on this account):
        # one more try with the fallback, remembered for the next sessions.
        _REJECTED_TRANSCRIBE.add(model)
        r = _post(session_payload(recent, fallback, scope=scope))
    if r.status_code >= 400:
        # 502, not OpenAI's own status: a 401 here is about the API key, not our page token.
        raise MintError(502, explain(r))
    return r.json()
