from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
)

from ..config import Settings


class ProviderError(Exception):
    """Safe, user-facing provider failure without raw SDK or credential details."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    tool_calls: list[ProviderToolCall]
    finish_reason: str | None
    usage: ProviderUsage
    request_id: str | None

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class MetaModelProvider:
    """OpenAI SDK adapter for Meta Model API Chat Completions."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = (
            AsyncOpenAI(
                base_url=settings.meta_base_url.rstrip("/"),
                api_key=settings.model_api_key,
                timeout=settings.model_timeout_seconds,
                max_retries=0,
            )
            if settings.model_api_key
            else None
        )

    @property
    def configured(self) -> bool:
        return bool(self.settings.model_api_key)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_completion_tokens: int,
    ) -> ProviderResponse:
        if self._client is None:
            raise ProviderError("not_configured", "Meta model access is not configured on the backend.")
        request: dict[str, Any] = {
            "model": self.settings.meta_model,
            "messages": messages,
            "max_completion_tokens": max_completion_tokens,
            "tool_choice": "auto",
        }
        if tools:
            request["tools"] = tools
        try:
            response = await self._client.chat.completions.create(**request)
        except Exception as exc:
            raise self._map_error(exc) from exc
        return self._parse_response(response)

    async def test_connection(self) -> ProviderResponse:
        return await self.complete(
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            tools=[],
            max_completion_tokens=min(32, self.settings.max_completion_tokens),
        )

    def _parse_response(self, response: Any) -> ProviderResponse:
        choices = getattr(response, "choices", None) or []
        if not choices or getattr(choices[0], "message", None) is None:
            raise ProviderError("malformed_provider_response", "Meta returned an unexpected completion response.")
        choice = choices[0]
        message = choice.message
        tool_calls: list[ProviderToolCall] = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            call_id = getattr(tool_call, "id", None)
            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 64:
                raise ProviderError("malformed_provider_response", "Meta returned an invalid tool-call identifier.")
            if not isinstance(name, str) or not name:
                raise ProviderError("malformed_provider_response", "Meta returned a tool call without a function name.")
            if not isinstance(arguments, str):
                raise ProviderError("malformed_provider_response", "Meta returned invalid tool-call arguments.")
            tool_calls.append(ProviderToolCall(id=call_id, name=name, arguments=arguments))
        usage = getattr(response, "usage", None)
        return ProviderResponse(
            content=getattr(message, "content", None) or "",
            tool_calls=tool_calls,
            finish_reason=getattr(choice, "finish_reason", None),
            usage=ProviderUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            ),
            request_id=getattr(response, "id", None),
        )

    @staticmethod
    def _map_error(exc: Exception) -> ProviderError:
        if isinstance(exc, AuthenticationError):
            return ProviderError("authentication_failed", "Meta rejected the backend API key.")
        if isinstance(exc, PermissionDeniedError):
            return ProviderError("access_restricted", "The Meta account or model is not permitted for this request.")
        if isinstance(exc, RateLimitError):
            return ProviderError("rate_limited", "Meta rate-limited this request. Wait and retry explicitly.", retryable=True)
        if isinstance(exc, APITimeoutError):
            return ProviderError("provider_timeout", "Meta did not respond before the configured timeout.", retryable=True)
        if isinstance(exc, APIConnectionError):
            return ProviderError("connection_failed", "The backend could not connect to Meta.", retryable=True)
        if isinstance(exc, BadRequestError):
            return ProviderError("invalid_provider_request", "Meta rejected the model request. Check the configured model and limits.")
        if isinstance(exc, APIStatusError):
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403}:
                return ProviderError("access_restricted", "Meta refused access to this model request.")
            if status_code == 429:
                return ProviderError("rate_limited", "Meta rate-limited this request. Wait and retry explicitly.", retryable=True)
            if status_code in {408, 504}:
                return ProviderError("provider_timeout", "Meta did not complete the request in time.", retryable=True)
            return ProviderError("provider_error", "Meta returned an unexpected service error.", retryable=True)
        return ProviderError("provider_error", "The Meta request could not be completed.", retryable=True)
