"""Provider-isolation layer for the project's one external LLM dependency.
Per decision 9 (docs/decisions.md), every LLM call in this project goes
through generate_structured -- agent code never imports a provider SDK (or
`requests`, for Ollama) directly, so swapping providers again later touches
only this module. History: originally built against Gemini; swapped to
Hugging Face (huggingface_hub SDK, free tier) after Gemini's key required
prepaid billing credits rather than offering a true free tier; swapped again
to a local Ollama install (Build 4 migration, Part 1, decision 41) after
Hugging Face's free tier proved unsustainable at this project's real call
volume (decision 39) -- Ollama is now the DEFAULT provider (LLMSettings.llm_provider),
with the Hugging Face path kept, not deleted, exactly per this module's own
stated purpose: swapping providers touches only this module, and a provider
that already worked stays available rather than being torn out.

Scope, deliberately narrow: the API call, retry policy, and response
extraction, per provider. No prompt construction, no domain logic -- that
belongs to the caller (e.g. src/explainer.py)."""

from functools import lru_cache
from typing import Literal, TypeVar

import requests
from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError
from huggingface_hub.inference._generated.types.chat_completion import (
    ChatCompletionInputJSONSchema,
    ChatCompletionInputResponseFormatJSONSchema,
    ChatCompletionOutput,
)
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config import PROJECT_ROOT

T = TypeVar("T", bound=BaseModel)

_DEFAULT_HF_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
_DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

_OLLAMA_TIMEOUT_SECONDS = 1800
"""Build 4 migration, Part 1: originally set to 300s, reasoned from Part 0/
3B smoke-test timings (70-90s for an explainer-scale prompt, run with this
process holding the CPU alone) -- corrected after Stage 1's own real
production-path sanity check measured a genuine range of 174s-1014s for
the SAME kind of call on THIS machine, because this is a shared desktop
running several other CPU-hungry processes concurrently (multiple Claude
Code sessions, VS Code, a browser) that were not running during the
earlier smoke tests. 300s was proven wrong by direct measurement, not
theory -- the 1014s call exceeded it more than 3x over and yet the call
still succeeded (the timeout did not actually abort it; the exact
mechanism was not chased further, since the practical fix -- a genuinely
generous ceiling -- addresses the real risk either way). Set to 1800s
(30 min), giving real headroom above the worst case actually observed,
not a theoretical one: a too-short timeout aborts a legitimately-slow-but-
succeeding call, which is worse than waiting, since retrying a genuine
timeout does not make a CPU-bound, contended machine faster."""


class LLMSettings(BaseSettings):
    """Reads provider selection and both providers' settings from .env,
    never from a hardcoded value. hf_token defaults to empty (Build 4
    migration, Part 1) -- it was previously required unconditionally, but
    requiring a Hugging Face token to even construct LLMSettings no longer
    makes sense now that Ollama (no auth, no token) is the default
    provider; a caller that actually selects llm_provider="huggingface"
    with no real token still fails loudly, just later and naturally (the
    Hugging Face API itself rejects an empty/invalid token as an auth
    error), not via a settings-construction-time validation error that
    would otherwise block Ollama-only usage for no reason."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    llm_provider: Literal["huggingface", "ollama"] = "ollama"
    hf_token: str = ""
    hf_model: str = _DEFAULT_HF_MODEL
    ollama_model: str = _DEFAULT_OLLAMA_MODEL
    ollama_base_url: str = _DEFAULT_OLLAMA_BASE_URL


@lru_cache(maxsize=1)
def _get_client() -> InferenceClient:
    """Lazily constructed on first call, not at import time (config.py's
    own no-I/O-at-import convention), and cached so every caller in one
    process reuses the same client/settings read."""
    settings = LLMSettings()
    return InferenceClient(token=settings.hf_token)


def _hf_model_name() -> str:
    return LLMSettings().hf_model


def _is_rate_limit_error(exc: BaseException) -> bool:
    """True only for a 429 response. A malformed prompt or an auth failure
    (4xx, not 429) must surface immediately, not retry silently -- this is
    the one predicate tenacity retries on."""
    return isinstance(exc, HfHubHTTPError) and exc.response.status_code == 429


@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _call_llm(prompt: str, response_schema: type[BaseModel] | None) -> ChatCompletionOutput:
    client = _get_client()
    response_format = None
    if response_schema is not None:
        response_format = ChatCompletionInputResponseFormatJSONSchema(
            type="json_schema",
            json_schema=ChatCompletionInputJSONSchema(
                name=response_schema.__name__, schema=response_schema.model_json_schema()
            ),
        )
    return client.chat_completion(
        model=_hf_model_name(),
        messages=[{"role": "user", "content": prompt}],
        response_format=response_format,
    )


def _generate_huggingface(prompt: str, response_schema: type[T] | None) -> str | T:
    """The original (Day 8) Hugging Face path, unchanged in behavior --
    only extracted into its own function so generate_structured can
    dispatch to it by provider. Raises the underlying huggingface_hub
    error directly (after tenacity's rate-limit retries are exhausted, or
    immediately for any other error) -- no swallowing, no silent
    empty-string fallback."""
    response = _call_llm(prompt, response_schema)
    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Hugging Face returned an empty response with no message content")

    if response_schema is not None:
        return response_schema.model_validate_json(content)

    return content


def _ollama_settings() -> tuple[str, str]:
    settings = LLMSettings()
    return settings.ollama_model, settings.ollama_base_url


def _is_ollama_connection_error(exc: BaseException) -> bool:
    """True only when the Ollama server could not be reached at all (e.g.
    the app hasn't finished starting yet) -- a genuinely transient
    condition worth a brief retry. Explicitly NOT true for
    requests.exceptions.Timeout: a call that times out at
    _OLLAMA_TIMEOUT_SECONDS is, by that constant's own generous margin, a
    real problem (the model hung, or the prompt is pathological), and
    retrying it would just wait the same long timeout again for no
    benefit -- it must surface immediately, the same "don't retry a
    non-transient failure" discipline _is_rate_limit_error already
    applies to the Hugging Face path."""
    return isinstance(exc, requests.exceptions.ConnectionError)


@retry(
    retry=retry_if_exception(_is_ollama_connection_error),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_ollama(prompt: str, response_schema: type[BaseModel] | None) -> dict:
    model, base_url = _ollama_settings()
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if response_schema is not None:
        payload["format"] = response_schema.model_json_schema()
    response = requests.post(f"{base_url}/api/chat", json=payload, timeout=_OLLAMA_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _generate_ollama(prompt: str, response_schema: type[T] | None) -> str | T:
    """Build 4 migration, Part 1: the Ollama path, proven working by Part
    0's and the 3B comparison's standalone smoke-test scripts before being
    wired in here -- same schema-constrained structured-output mechanism
    (the `format` parameter, Ollama's local HTTP API), same
    pydantic-model-parses-the-raw-content-directly contract as the
    Hugging Face path (no markdown-fence stripping, no regex -- confirmed
    unnecessary by both smoke tests). Raises requests' own HTTPError
    directly for a non-2xx response (no retry -- see
    _is_ollama_connection_error's own docstring for why only a connection
    failure is treated as transient), and the underlying
    pydantic.ValidationError directly if response_schema is given and the
    content doesn't validate -- no swallowing, matching
    _generate_huggingface's own fail-loud contract."""
    body = _call_ollama(prompt, response_schema)
    content = body.get("message", {}).get("content")
    if not content:
        raise ValueError("Ollama returned an empty response with no message content")

    if response_schema is not None:
        return response_schema.model_validate_json(content)

    return content


def generate_structured(prompt: str, response_schema: type[T] | None = None) -> str | T:
    """Send `prompt` to the configured provider (LLMSettings.llm_provider,
    default "ollama" -- Build 4 migration, Part 1, decision 41; was
    unconditionally Hugging Face before). With response_schema=None (the
    explainer's own use case), returns the plain-text response. With a
    pydantic model class, returns a validated instance of it
    (schema-constrained JSON output -- both providers support this, via
    their own respective mechanisms: Hugging Face's response_format,
    Ollama's format parameter).

    Dispatch only -- no provider-specific logic lives here, matching this
    module's own "swapping providers touches only this module" charter.
    Each provider's own function documents its own fail-loud/retry
    contract; this function does not add another layer of error handling
    on top."""
    settings = LLMSettings()
    if settings.llm_provider == "ollama":
        return _generate_ollama(prompt, response_schema)
    return _generate_huggingface(prompt, response_schema)
