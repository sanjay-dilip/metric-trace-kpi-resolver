"""Tests for src.llm_client: the provider-isolation layer, both the
Hugging Face path (Build 1, Week 2 Day 1) and the Ollama path (Build 4
migration, Part 1, decision 41). Every real network call is mocked --
these tests prove each provider's own retry predicate, the plain-text/
schema extraction paths, and the fail-loud behavior on a non-transient
error, without depending on a live HF_TOKEN or a running Ollama server
(Level 1 of the workflow guide's three-tier live-API-test policy: mocked
responses on every run).

LLM_PROVIDER defaults to "ollama" (LLMSettings, src/llm_client.py) --
every Hugging-Face-path test below forces provider="huggingface" via the
module-level `_force_huggingface_provider` autouse fixture, so
generate_structured actually dispatches to the code these tests exist to
prove, rather than silently routing to Ollama and testing nothing real."""

from unittest.mock import MagicMock

import httpx
import pytest
import requests
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel

from src import llm_client
from src.llm_client import _is_rate_limit_error, generate_structured


@pytest.fixture(autouse=True)
def _force_huggingface_provider(monkeypatch):
    """Every test in this file was written against the Hugging Face path
    specifically (see this module's own docstring) -- forced here via
    env var (pydantic-settings' own precedence: env vars override the
    .env file) rather than monkeypatching llm_client internals, so
    generate_structured's real dispatch logic is exercised, not bypassed.
    The dedicated Ollama-path tests below override this back explicitly."""
    monkeypatch.setenv("LLM_PROVIDER", "huggingface")


class _DummySchema(BaseModel):
    text: str


def _http_error(status_code: int) -> HfHubHTTPError:
    response = httpx.Response(status_code=status_code, request=httpx.Request("POST", "https://example.test"))
    return HfHubHTTPError(f"{status_code} error", response=response)


def _rate_limit_error() -> HfHubHTTPError:
    return _http_error(429)


def _auth_error() -> HfHubHTTPError:
    return _http_error(401)


def _fake_chat_response(content: str | None) -> MagicMock:
    message = MagicMock(content=content)
    choice = MagicMock(message=message)
    return MagicMock(choices=[choice])


def test_is_rate_limit_error_true_only_for_429_http_error():
    assert _is_rate_limit_error(_rate_limit_error()) is True
    assert _is_rate_limit_error(_auth_error()) is False
    assert _is_rate_limit_error(ValueError("not an hf error at all")) is False


def test_generate_structured_returns_plain_text_on_success(monkeypatch):
    fake_response = _fake_chat_response("the explanation prose")
    monkeypatch.setattr(llm_client, "_call_llm", lambda prompt, schema: fake_response)

    result = generate_structured("some prompt")

    assert result == "the explanation prose"


def test_generate_structured_returns_parsed_schema_instance(monkeypatch):
    fake_response = _fake_chat_response('{"text": "structured output"}')
    monkeypatch.setattr(llm_client, "_call_llm", lambda prompt, schema: fake_response)

    result = generate_structured("some prompt", response_schema=_DummySchema)

    assert result == _DummySchema(text="structured output")


def test_generate_structured_raises_when_content_is_not_valid_json_for_schema(monkeypatch):
    fake_response = _fake_chat_response("not valid json")
    monkeypatch.setattr(llm_client, "_call_llm", lambda prompt, schema: fake_response)

    with pytest.raises(Exception):
        generate_structured("some prompt", response_schema=_DummySchema)


def test_generate_structured_raises_on_empty_content_response(monkeypatch):
    fake_response = _fake_chat_response(None)
    monkeypatch.setattr(llm_client, "_call_llm", lambda prompt, schema: fake_response)

    with pytest.raises(ValueError):
        generate_structured("some prompt")


def test_call_llm_retries_rate_limit_errors_then_succeeds(monkeypatch):
    """Two 429s, then success -- tenacity must retry the rate-limit error
    (not surface it immediately) and return the eventual successful
    response. Sleep is patched to 0 so this test does not actually wait
    through the exponential backoff."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda seconds: None)

    fake_client = MagicMock()
    fake_response = _fake_chat_response("ok")
    fake_client.chat_completion.side_effect = [
        _rate_limit_error(),
        _rate_limit_error(),
        fake_response,
    ]
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake_client)
    monkeypatch.setattr(llm_client, "_hf_model_name", lambda: "meta-llama/Llama-3.1-8B-Instruct")

    result = generate_structured("some prompt")

    assert result == "ok"
    assert fake_client.chat_completion.call_count == 3


def test_call_llm_does_not_retry_and_reraises_a_non_rate_limit_error(monkeypatch):
    """An auth failure or malformed request (any non-429 error) must
    surface immediately -- retrying it silently would hide a real,
    non-transient problem behind a delay."""
    fake_client = MagicMock()
    fake_client.chat_completion.side_effect = _auth_error()
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake_client)
    monkeypatch.setattr(llm_client, "_hf_model_name", lambda: "meta-llama/Llama-3.1-8B-Instruct")

    with pytest.raises(HfHubHTTPError):
        generate_structured("some prompt")

    assert fake_client.chat_completion.call_count == 1


def test_call_llm_reraises_after_exhausting_retries(monkeypatch):
    """Every attempt hits a 429 -- tenacity's stop_after_attempt(5) must
    give up and reraise the original error, not fail silently or return
    empty/malformed output."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda seconds: None)

    fake_client = MagicMock()
    fake_client.chat_completion.side_effect = [_rate_limit_error() for _ in range(5)]
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake_client)
    monkeypatch.setattr(llm_client, "_hf_model_name", lambda: "meta-llama/Llama-3.1-8B-Instruct")

    with pytest.raises(HfHubHTTPError) as exc_info:
        generate_structured("some prompt")

    assert exc_info.value.response.status_code == 429
    assert fake_client.chat_completion.call_count == 5


def test_is_ollama_connection_error_true_only_for_connection_error():
    """Retries only a genuine "server not reachable" condition -- a
    Timeout (a real, generously-margined 300s timeout expiring) must NOT
    be treated as transient, matching this project's own reasoning that
    retrying a real timeout just waits the same long duration again for
    no benefit."""
    from src.llm_client import _is_ollama_connection_error

    assert _is_ollama_connection_error(requests.exceptions.ConnectionError("refused")) is True
    assert _is_ollama_connection_error(requests.exceptions.Timeout("timed out")) is False
    assert _is_ollama_connection_error(ValueError("not a requests error at all")) is False


def test_generate_structured_ollama_returns_plain_text_on_success(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_client, "_call_ollama", lambda prompt, schema: {"message": {"content": "the explanation prose"}})

    result = generate_structured("some prompt")

    assert result == "the explanation prose"


def test_generate_structured_ollama_returns_parsed_schema_instance(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        llm_client, "_call_ollama", lambda prompt, schema: {"message": {"content": '{"text": "structured output"}'}}
    )

    result = generate_structured("some prompt", response_schema=_DummySchema)

    assert result == _DummySchema(text="structured output")


def test_generate_structured_ollama_raises_on_empty_content_response(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_client, "_call_ollama", lambda prompt, schema: {"message": {"content": ""}})

    with pytest.raises(ValueError):
        generate_structured("some prompt")


def test_generate_structured_ollama_raises_when_content_is_not_valid_json_for_schema(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_client, "_call_ollama", lambda prompt, schema: {"message": {"content": "not valid json"}})

    with pytest.raises(Exception):
        generate_structured("some prompt", response_schema=_DummySchema)


def test_call_ollama_retries_connection_errors_then_succeeds(monkeypatch):
    """Two connection errors, then success -- the same "retry the
    transient case, then succeed" shape test_call_llm_retries_rate_limit_errors_then_succeeds
    already proves for the Hugging Face path, mirrored here for Ollama's
    own transient case (server not reachable yet)."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda seconds: None)

    responses = [
        requests.exceptions.ConnectionError("refused"),
        requests.exceptions.ConnectionError("refused"),
        MagicMock(json=lambda: {"message": {"content": "ok"}}, raise_for_status=lambda: None),
    ]

    def fake_post(url, json, timeout):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(requests, "post", fake_post)

    result = generate_structured("some prompt")

    assert result == "ok"
    assert responses == []


def test_call_ollama_does_not_retry_and_reraises_a_timeout(monkeypatch):
    """A real timeout (the model genuinely hung, or a pathological
    prompt) must surface immediately, not retry -- see
    _is_ollama_connection_error's own docstring for why."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    call_count = 0

    def fake_post(url, json, timeout):
        nonlocal call_count
        call_count += 1
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(requests.exceptions.Timeout):
        generate_structured("some prompt")

    assert call_count == 1


def test_call_ollama_reraises_after_exhausting_connection_retries(monkeypatch):
    """Every attempt hits a connection error -- tenacity's
    stop_after_attempt(3) must give up and reraise, not fail silently."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda seconds: None)
    call_count = 0

    def fake_post(url, json, timeout):
        nonlocal call_count
        call_count += 1
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(requests.exceptions.ConnectionError):
        generate_structured("some prompt")

    assert call_count == 3


def test_call_ollama_sends_format_schema_and_default_model(monkeypatch):
    """Confirms the real request shape: response_schema's JSON schema goes
    in the `format` field (Ollama's own structured-output mechanism,
    proven working by Part 0's standalone smoke test), and the default
    model/base_url from LLMSettings are used when no override is set."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return MagicMock(json=lambda: {"message": {"content": '{"text": "x"}'}}, raise_for_status=lambda: None)

    monkeypatch.setattr(requests, "post", fake_post)

    generate_structured("some prompt", response_schema=_DummySchema)

    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["json"]["model"] == "llama3.1:8b"
    assert captured["json"]["format"] == _DummySchema.model_json_schema()
    assert captured["timeout"] == llm_client._OLLAMA_TIMEOUT_SECONDS
