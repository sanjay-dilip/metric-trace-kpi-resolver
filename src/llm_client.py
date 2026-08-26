"""Provider-isolation layer for the project's one external dependency: the
Hugging Face Inference Providers API (huggingface_hub SDK, free tier). Per
decision 9 (docs/decisions.md), every LLM call in this project goes through
generate_structured -- agent code never imports huggingface_hub directly,
so swapping providers again later touches only this module. (Originally
built against Gemini; swapped to Hugging Face after Gemini's key required
prepaid billing credits rather than offering a true free tier.)

Scope, deliberately narrow: the API call, retry-on-rate-limit, and response
extraction. No prompt construction, no domain logic -- that belongs to the
caller (e.g. src/explainer.py)."""

from functools import lru_cache
from typing import TypeVar

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

_DEFAULT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"


class LLMSettings(BaseSettings):
    """Reads HF_TOKEN (and an optional HF_MODEL override) from .env, never
    from a hardcoded value."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    hf_token: str
    hf_model: str = _DEFAULT_MODEL


@lru_cache(maxsize=1)
def _get_client() -> InferenceClient:
    """Lazily constructed on first call, not at import time (config.py's
    own no-I/O-at-import convention), and cached so every caller in one
    process reuses the same client/settings read."""
    settings = LLMSettings()
    return InferenceClient(token=settings.hf_token)


def _model_name() -> str:
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
        model=_model_name(),
        messages=[{"role": "user", "content": prompt}],
        response_format=response_format,
    )


def generate_structured(prompt: str, response_schema: type[T] | None = None) -> str | T:
    """Send `prompt` to the configured Hugging Face model. With
    response_schema=None (Day 8's explainer use case), returns the plain-text
    response. With a pydantic model class, returns a validated instance of
    it (schema-constrained JSON output, via the model's response_format
    support where the underlying provider honors it) -- unused by the Day 8
    explainer, built in now since Build 3's unsupported-claim-rate checker
    will need it and retrofitting the signature later is worse than
    building it in from the start.

    Raises the underlying huggingface_hub error directly (after tenacity's
    rate-limit retries are exhausted, or immediately for any other error)
    -- no swallowing, no silent empty-string fallback."""
    response = _call_llm(prompt, response_schema)
    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Hugging Face returned an empty response with no message content")

    if response_schema is not None:
        return response_schema.model_validate_json(content)

    return content
