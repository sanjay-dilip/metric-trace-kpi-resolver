"""Tests for src.llm_client (Build 1, Week 2 Day 1): the Hugging Face
provider-isolation layer. Every real network call is mocked -- these tests
prove the retry predicate, the plain-text/schema extraction paths, and the
fail-loud behavior on a non-rate-limit error, without depending on a live
HF_TOKEN or making a real API call (Level 1 of the workflow guide's
three-tier live-API-test policy: mocked responses on every run)."""

from unittest.mock import MagicMock

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel

from src import llm_client
from src.llm_client import _is_rate_limit_error, generate_structured


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
    monkeypatch.setattr(llm_client, "_model_name", lambda: "meta-llama/Llama-3.1-8B-Instruct")

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
    monkeypatch.setattr(llm_client, "_model_name", lambda: "meta-llama/Llama-3.1-8B-Instruct")

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
    monkeypatch.setattr(llm_client, "_model_name", lambda: "meta-llama/Llama-3.1-8B-Instruct")

    with pytest.raises(HfHubHTTPError) as exc_info:
        generate_structured("some prompt")

    assert exc_info.value.response.status_code == 429
    assert fake_client.chat_completion.call_count == 5
