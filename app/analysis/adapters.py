"""Provider adapters for OpenAI, Anthropic and Mistral over their HTTPS APIs.

Each adapter is a thin translation layer: neutral ``ProviderRequest`` in,
neutral ``ProviderResponse`` out. Vendor SDKs are deliberately not used (see
docs/architecture.md); the request and response shapes below were read from
the vendors' official Python SDK sources.

Safety properties:
- API keys are read from the process environment at call time, never stored
  in config, requests, responses or exception text.
- Base URLs are fixed constants, so a stray variable cannot redirect a key.
- Error messages contain the HTTP status and the vendor's short error type,
  never the response body or request content.
- A truncated, filtered, refused or empty answer is an error, not a draft.
"""

from __future__ import annotations

import os
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Mapping

import httpx

from .providers import (
    AnalysisProvider,
    ProviderError,
    ProviderRegistry,
    ProviderRequest,
    ProviderResponse,
)

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 180.0
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 30.0
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# Value read from the vendor's SDK source (anthropic/_constants.py). Not yet
# exercised against the live API from this repository; override with
# ANTHROPIC_API_VERSION if the vendor requires a different one.
ANTHROPIC_DEFAULT_API_VERSION = "2026-10-21"


class HttpChatAdapter(ABC):
    name: str
    base_url: str
    key_variable: str

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: httpx.Timeout | None = None,
    ):
        self._env = os.environ if env is None else env
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout or httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    # -- vendor specific ---------------------------------------------------
    @abstractmethod
    def _build(self, request: ProviderRequest, api_key: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (url, headers, json body)."""

    @abstractmethod
    def _parse(self, data: Mapping[str, Any]) -> tuple[str, str | None, int | None, int | None, str | None]:
        """Return (text, finish_reason, input_tokens, output_tokens, served_model).

        Raise ProviderError for a truncated, refused, filtered or malformed answer.
        """

    # -- shared ------------------------------------------------------------
    def _api_key(self) -> str:
        key = self._env.get(self.key_variable, "").strip()
        if not key:
            raise ProviderError(f"{self.name}: environment variable {self.key_variable} is not set")
        return key

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not request.model:
            raise ProviderError(f"{self.name}: no model configured for role '{request.role.value}'")
        if not request.prompt.strip() or not request.context.strip():
            raise ProviderError(f"{self.name}: prompt and context must not be empty")
        url, headers, body = self._build(request, self._api_key())
        started = time.monotonic()
        last_problem = "unknown error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            delay = None
            try:
                response = self._client.post(url, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_problem = f"network error ({type(exc).__name__})"
            else:
                if response.status_code == 200:
                    return self._finish(response, request, started, attempt)
                last_problem = f"HTTP {response.status_code}{self._error_type(response)}"
                if response.status_code not in RETRYABLE_STATUS:
                    raise ProviderError(f"{self.name}: request failed: {last_problem}")
                delay = self._retry_after(response)
            if attempt < MAX_ATTEMPTS:
                self._sleep(delay if delay is not None else min(2.0 ** (attempt - 1) + random.uniform(0, 0.5), 10.0))
        raise ProviderError(f"{self.name}: gave up after {MAX_ATTEMPTS} attempts: {last_problem}")

    def _finish(self, response: httpx.Response, request: ProviderRequest, started: float, attempts: int) -> ProviderResponse:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name}: response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name}: unexpected response shape")
        text, finish, tokens_in, tokens_out, served_model = self._parse(data)
        if not text.strip():
            raise ProviderError(f"{self.name}: the model returned no text")
        return ProviderResponse(
            text=text,
            provider=self.name,
            model=served_model or request.model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000),
            attempts=attempts,
            finish_reason=finish,
        )

    @staticmethod
    def _error_type(response: httpx.Response) -> str:
        try:
            error = response.json().get("error")
            kind = error.get("type") if isinstance(error, dict) else None
        except (ValueError, AttributeError):
            return ""
        return f" ({kind})" if isinstance(kind, str) and _SAFE_ERROR_TYPE.match(kind) else ""

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        try:
            seconds = float(response.headers.get("retry-after", ""))
        except ValueError:
            return None
        return min(max(seconds, 0.0), MAX_RETRY_AFTER)

    @staticmethod
    def _as_int(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class _ChatCompletionsAdapter(HttpChatAdapter):
    """Shared by OpenAI and Mistral, which use the same chat-completions shape."""

    token_limit_field: str

    def _build(self, request, api_key):
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.prompt},
                {"role": "user", "content": request.context},
            ],
            self.token_limit_field: request.max_output_tokens,
        }
        return self.base_url, headers, body

    def _parse(self, data):
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError(f"{self.name}: response had no choices")
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish != "stop":
            # "length" = truncated; "content_filter"/"tool_calls"/others = not a usable answer.
            raise ProviderError(f"{self.name}: answer was not complete (finish_reason={finish!r})")
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # chunked content (Mistral may return this)
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
        if not isinstance(content, str):
            raise ProviderError(f"{self.name}: response had no text content")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return (
            content,
            finish,
            self._as_int(usage.get("prompt_tokens")),
            self._as_int(usage.get("completion_tokens")),
            data.get("model") if isinstance(data.get("model"), str) else None,
        )


class OpenAIAdapter(_ChatCompletionsAdapter):
    name = "openai"
    base_url = "https://api.openai.com/v1/chat/completions"
    key_variable = "OPENAI_API_KEY"
    token_limit_field = "max_completion_tokens"


class MistralAdapter(_ChatCompletionsAdapter):
    name = "mistral"
    base_url = "https://api.mistral.ai/v1/chat/completions"
    key_variable = "MISTRAL_API_KEY"
    token_limit_field = "max_tokens"


class AnthropicAdapter(HttpChatAdapter):
    name = "anthropic"
    base_url = "https://api.anthropic.com/v1/messages"
    key_variable = "ANTHROPIC_API_KEY"

    def _build(self, request, api_key):
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._env.get("ANTHROPIC_API_VERSION", "").strip() or ANTHROPIC_DEFAULT_API_VERSION,
            "Content-Type": "application/json",
        }
        body = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "system": request.prompt,
            "messages": [{"role": "user", "content": request.context}],
        }
        return self.base_url, headers, body

    def _parse(self, data):
        stop = data.get("stop_reason")
        if stop not in ("end_turn", "stop_sequence"):
            # max_tokens = truncated; refusal, tool_use, pause_turn, context exceeded = no usable answer.
            raise ProviderError(f"{self.name}: answer was not complete (stop_reason={stop!r})")
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ProviderError(f"{self.name}: response had no content")
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return (
            text,
            stop,
            self._as_int(usage.get("input_tokens")),
            self._as_int(usage.get("output_tokens")),
            data.get("model") if isinstance(data.get("model"), str) else None,
        )


def build_registry(
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ProviderRegistry:
    """Registry with the mock provider and the three real adapters.

    Adapters read their API key lazily, so a missing key only matters for a
    role that is actually configured to use that provider.
    """
    from .mock_provider import MockProvider

    providers: dict[str, AnalysisProvider] = {"mock": MockProvider()}
    for adapter in (OpenAIAdapter, AnthropicAdapter, MistralAdapter):
        providers[adapter.name] = adapter(env, transport=transport, sleep=sleep)
    return ProviderRegistry(providers)
