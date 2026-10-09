"""The Realtime session payload (what the voice model is configured with) and
minting its ephemeral key, against a fake OpenAI."""
import httpx
import pytest

from jarvis import config, realtime


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    """The documented defaults, whatever this PC's .env says."""
    for name, value in {"REALTIME_MODEL": "gpt-realtime-2.1", "TRANSCRIBE_MODEL": "gpt-4o-mini-transcribe",
                        "TRANSCRIBE_FALLBACK": "whisper-1", "REASONING_EFFORT": "low",
                        "NOISE_REDUCTION": "far_field", "EAGERNESS": "auto", "VOICE_SPEED": 1.0,
                        "SECRET_TTL": 120, "RETENTION_RATIO": 0.8, "SPEECH_LANG": "fr-FR",
                        "OPENAI_API_KEY": "sk-fake"}.items():
        monkeypatch.setattr(config, name, value)
    realtime._REJECTED_TRANSCRIBE.clear()
    yield
    realtime._REJECTED_TRANSCRIBE.clear()


def audio_input(payload):
    return payload["session"]["audio"]["input"]


def test_default_voice_model_is_the_supported_one():
    # gpt-realtime shuts down on 2027-01-20.
    source = (config.ROOT / "jarvis" / "config.py").read_text(encoding="utf-8")
    assert 'os.environ.get("REALTIME_MODEL", "gpt-realtime-2.1")' in source


def test_transcription_defaults_to_one_language():
    tr = audio_input(realtime.session_payload())["transcription"]
    assert tr == {"model": "gpt-4o-mini-transcribe", "language": "fr"}
    assert "languages" not in tr


def test_new_transcription_models_take_a_language_list(monkeypatch):
    for model in ("gpt-transcribe", "gpt-live-transcribe"):
        monkeypatch.setattr(config, "TRANSCRIBE_MODEL", model)
        tr = audio_input(realtime.session_payload())["transcription"]
        assert tr["model"] == model and tr["languages"] == ["fr"]
        assert "language" not in tr  # never both: OpenAI refuses the session
        assert "JARVIS" in tr["keywords"] and "monsieur" in tr["keywords"]


def test_turn_taking_noise_expiry_and_truncation():
    payload = realtime.session_payload("monsieur : bonjour")
    inp = audio_input(payload)
    assert inp["turn_detection"] == {"type": "semantic_vad", "eagerness": "auto",
                                     "create_response": True, "interrupt_response": True}
    assert inp["noise_reduction"] == {"type": "far_field"}
    assert payload["expires_after"] == {"anchor": "created_at", "seconds": 120}
    session = payload["session"]
    assert session["truncation"] == {"type": "retention_ratio", "retention_ratio": 0.8}
    assert session["type"] == "realtime" and session["tool_choice"] == "auto"
    assert session["max_output_tokens"] == 4096
    assert session["audio"]["output"] == {"voice": config.VOICE, "speed": 1.0}
    assert "monsieur : bonjour" in session["instructions"]
    assert "delegate_to_claude" in [t["name"] for t in session["tools"]]
    assert "idle_timeout_ms" not in repr(payload)  # server_vad only


def test_reasoning_only_for_realtime_2_models(monkeypatch):
    session = realtime.session_payload()["session"]
    assert session["reasoning"] == {"effort": "low"}
    assert session["parallel_tool_calls"] is True
    for model in ("gpt-realtime", "gpt-realtime-mini"):
        monkeypatch.setattr(config, "REALTIME_MODEL", model)
        session = realtime.session_payload()["session"]
        assert "reasoning" not in session and "parallel_tool_calls" not in session
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1-mini")
    assert realtime.session_payload()["session"]["reasoning"] == {"effort": "low"}


def test_settings_typos_never_reach_openai(monkeypatch):
    """A bad value in .env falls back to a valid one instead of a refused session."""
    for name, value in {"EAGERNESS": "pressé", "NOISE_REDUCTION": "casque", "REASONING_EFFORT": "max",
                        "VOICE_SPEED": 9.0, "SECRET_TTL": 1, "RETENTION_RATIO": 3.0}.items():
        monkeypatch.setattr(config, name, value)
    payload = realtime.session_payload()
    inp = audio_input(payload)
    assert inp["turn_detection"]["eagerness"] == "auto"
    assert inp["noise_reduction"] == {"type": "far_field"}
    assert payload["session"]["reasoning"] == {"effort": "low"}
    assert payload["session"]["audio"]["output"]["speed"] == 1.5
    assert payload["expires_after"]["seconds"] == 10
    assert payload["session"]["truncation"]["retention_ratio"] == 1.0
    monkeypatch.setattr(config, "NOISE_REDUCTION", "off")
    assert audio_input(realtime.session_payload())["noise_reduction"] is None
    monkeypatch.setattr(config, "NOISE_REDUCTION", "near_field")
    assert audio_input(realtime.session_payload())["noise_reduction"] == {"type": "near_field"}
    monkeypatch.setattr(config, "EAGERNESS", "low")  # « Il me coupe trop tôt »
    assert audio_input(realtime.session_payload())["turn_detection"]["eagerness"] == "low"


# ---------------------------------------------------------------- minting

class FakeOpenAI:
    """httpx.post stand-in: answers from a list and records every payload."""

    def __init__(self, *answers):
        self.answers, self.payloads = list(answers), []

    def __call__(self, url, headers, json, timeout):
        assert url == realtime.CLIENT_SECRETS_URL
        assert headers["Authorization"] == "Bearer sk-fake"
        self.payloads.append(json)
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    def models(self):
        return [p["session"]["audio"]["input"]["transcription"]["model"] for p in self.payloads]


def refused(message, code="invalid_value"):
    return 400, {"error": {"type": "invalid_request_error", "code": code, "message": message}}


def test_refused_transcription_model_is_retried_once_with_the_fallback(monkeypatch):
    fake = FakeOpenAI(refused("Invalid transcription model: gpt-4o-mini-transcribe"),
                      (200, {"value": "ek_ok", "expires_at": 1}))
    monkeypatch.setattr(realtime.httpx, "post", fake)
    assert realtime.mint()["value"] == "ek_ok"
    assert fake.models() == ["gpt-4o-mini-transcribe", "whisper-1"]
    assert fake.payloads[1]["session"]["audio"]["input"]["transcription"] == {"model": "whisper-1",
                                                                              "language": "fr"}
    # The next session goes straight to the model that works.
    fake.answers.append((200, {"value": "ek_2"}))
    assert realtime.mint()["value"] == "ek_2"
    assert fake.models() == ["gpt-4o-mini-transcribe", "whisper-1", "whisper-1"]


def test_fallback_is_tried_only_once(monkeypatch):
    fake = FakeOpenAI(refused("Invalid transcription model"), refused("Invalid transcription model"))
    monkeypatch.setattr(realtime.httpx, "post", fake)
    with pytest.raises(realtime.MintError) as exc:
        realtime.mint()
    assert len(fake.payloads) == 2
    assert exc.value.status == 502
    assert "transcription" in exc.value.detail and "OpenAI 400" in exc.value.detail


def test_other_refusals_are_not_retried(monkeypatch):
    fake = FakeOpenAI(refused("Unknown parameter: 'session.foo'", code="unknown_parameter"))
    monkeypatch.setattr(realtime.httpx, "post", fake)
    with pytest.raises(realtime.MintError) as exc:
        realtime.mint()
    assert len(fake.payloads) == 1
    assert exc.value.detail.startswith("OpenAI a refusé d'ouvrir la session : Unknown parameter")


@pytest.mark.parametrize("status,body,expected", [
    (401, {"error": {"code": "invalid_api_key", "message": "Incorrect API key"}}, "Clé OpenAI refusée"),
    (429, {"error": {"code": "insufficient_quota", "message": "You exceeded your quota"}},
     "Crédit OpenAI épuisé"),
    (429, {"error": {"code": "rate_limit_exceeded", "message": "Rate limit"}}, "Trop de demandes"),
    (404, {"error": {"code": "model_not_found", "message": "The model does not exist"}},
     "Modèle vocal indisponible"),
    (503, {"error": {"message": "overloaded"}}, "OpenAI rencontre un problème"),
])
def test_refusals_are_explained_in_french(monkeypatch, status, body, expected):
    monkeypatch.setattr(realtime.httpx, "post", FakeOpenAI((status, body)))
    with pytest.raises(realtime.MintError) as exc:
        realtime.mint()
    assert exc.value.status == 502  # never 401: the page would think its own token died
    assert exc.value.detail.startswith(expected)
    assert f"(OpenAI {status})" in exc.value.detail


def test_network_problems_are_explained_in_french(monkeypatch):
    def offline(*args, **kwargs):
        raise httpx.ConnectError("getaddrinfo failed")

    def slow(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(realtime.httpx, "post", offline)
    with pytest.raises(realtime.MintError) as exc:
        realtime.mint()
    assert exc.value.detail == realtime.ERRORS["network"]
    monkeypatch.setattr(realtime.httpx, "post", slow)
    with pytest.raises(realtime.MintError) as exc:
        realtime.mint()
    assert exc.value.status == 504 and "ne répond pas" in exc.value.detail


def test_missing_key_is_explained_in_french(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(realtime.MintError) as exc:
        realtime.mint()
    assert exc.value.status == 500
    assert exc.value.detail.startswith("Clé OpenAI absente")
