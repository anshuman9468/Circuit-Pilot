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


class OpenAICompatibleModelProvider:
    """Small adapter for providers exposing Chat Completions compatibility."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        timeout_seconds: float,
        provider_label: str,
        max_completion_tokens: int = 32,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.provider_label = provider_label
        self.max_completion_tokens = max_completion_tokens
        self._client = (
            AsyncOpenAI(
                base_url=self.base_url,
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
            if api_key
            else None
        )

    @property
    def configured(self) -> bool:
        return self._client is not None

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_completion_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        if self._client is None:
            raise ProviderError("not_configured", f"{self.provider_label} model access is not configured on the backend.")
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_completion_tokens,
            "tool_choice": "auto",
        }
        if response_format is not None:
            request["response_format"] = response_format
        request.update(self.request_options())
        if tools:
            request["tools"] = tools
        try:
            response = await self._client.chat.completions.create(**request)
        except Exception as exc:
            raise self._map_error(exc) from exc
        return self._parse_response(response)

    def request_options(self) -> dict[str, Any]:
        """Provider-specific documented options, kept out of the common contract."""

        return {}

    @property
    def supports_json_mode(self) -> bool:
        """Whether this provider can be asked for a JSON object response."""

        return False

    async def test_connection(self) -> ProviderResponse:
        return await self.complete(
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            tools=[],
            max_completion_tokens=min(32, self.max_completion_tokens),
        )

    def _parse_response(self, response: Any) -> ProviderResponse:
        choices = getattr(response, "choices", None) or []
        if not choices or getattr(choices[0], "message", None) is None:
            raise ProviderError("malformed_provider_response", f"{self.provider_label} returned an unexpected completion response.")
        choice = choices[0]
        message = choice.message
        tool_calls: list[ProviderToolCall] = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            call_id = getattr(tool_call, "id", None)
            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 64:
                raise ProviderError("malformed_provider_response", f"{self.provider_label} returned an invalid tool-call identifier.")
            if not isinstance(name, str) or not name:
                raise ProviderError("malformed_provider_response", f"{self.provider_label} returned a tool call without a function name.")
            if not isinstance(arguments, str):
                raise ProviderError("malformed_provider_response", f"{self.provider_label} returned invalid tool-call arguments.")
            tool_calls.append(ProviderToolCall(id=call_id, name=name, arguments=arguments))
        content = getattr(message, "content", None) or ""
        if not content.strip() and not tool_calls:
            raise ProviderError(
                "malformed_provider_response",
                f"{self.provider_label} returned neither an answer nor a tool call.",
            )
        usage = getattr(response, "usage", None)
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=getattr(choice, "finish_reason", None),
            usage=ProviderUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            ),
            request_id=getattr(response, "id", None),
        )

    def _map_error(self, exc: Exception) -> ProviderError:
        provider = self.provider_label
        if isinstance(exc, AuthenticationError):
            return ProviderError("authentication_failed", f"{provider} rejected the backend API key.")
        if isinstance(exc, PermissionDeniedError):
            return ProviderError("access_restricted", f"The {provider} account or model is not permitted for this request.")
        if isinstance(exc, RateLimitError):
            return ProviderError("rate_limited", f"{provider} rate-limited this request. Wait and retry explicitly.", retryable=True)
        if isinstance(exc, APITimeoutError):
            return ProviderError("provider_timeout", f"{provider} did not respond before the configured timeout.", retryable=True)
        if isinstance(exc, APIConnectionError):
            return ProviderError("connection_failed", f"The backend could not connect to {provider}.", retryable=True)
        if isinstance(exc, BadRequestError):
            return ProviderError("invalid_provider_request", f"{provider} rejected the model request. Check the configured model and limits.")
        if isinstance(exc, APIStatusError):
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403}:
                return ProviderError("access_restricted", f"{provider} refused access to this model request.")
            if status_code == 404:
                return ProviderError("invalid_provider_request", f"{provider} could not find the configured model. Check the model identifier and availability.")
            if status_code == 429:
                return ProviderError("rate_limited", f"{provider} rate-limited this request. Wait and retry explicitly.", retryable=True)
            if status_code in {408, 504}:
                return ProviderError("provider_timeout", f"{provider} did not complete the request in time.", retryable=True)
            return ProviderError("provider_error", f"{provider} returned an unexpected service error.", retryable=True)
        return ProviderError("provider_error", f"The {provider} request could not be completed.", retryable=True)


class MetaModelProvider(OpenAICompatibleModelProvider):
    """OpenAI SDK adapter for Meta Model API Chat Completions."""

    def __init__(self, settings: Settings, *, enabled: bool = True):
        super().__init__(
            api_key=settings.model_api_key if enabled else None,
            base_url=settings.meta_base_url,
            model=settings.meta_model,
            timeout_seconds=settings.model_timeout_seconds,
            provider_label="Meta",
            max_completion_tokens=settings.max_completion_tokens,
        )


class OpenRouterModelProvider(OpenAICompatibleModelProvider):
    """OpenRouter adapter using its OpenAI-compatible Chat Completions API."""

    def __init__(self, settings: Settings):
        super().__init__(
            api_key=settings.model_fallback_api_key,
            base_url=settings.model_fallback_base_url,
            model=settings.model_fallback_model,
            timeout_seconds=settings.model_timeout_seconds,
            provider_label="OpenRouter",
            max_completion_tokens=settings.max_completion_tokens,
        )

    def request_options(self) -> dict[str, Any]:
        # OpenRouter documents this parameter for reasoning-capable models.
        # Planning and generation require answer content, so do not spend the
        # bounded completion budget on hidden reasoning tokens.
        # `extra_body` is the OpenAI Python SDK escape hatch for provider-
        # specific fields; passing `reasoning` as a top-level SDK argument is
        # rejected before the request reaches OpenRouter.
        return {"extra_body": {"reasoning": {"effort": "none"}}}

    @property
    def supports_json_mode(self) -> bool:
        # OpenRouter's free router uses this signal to select models that
        # support structured JSON responses. Keep it provider-specific so the
        # Meta request remains limited to its documented common parameters.
        return True


class FallbackModelProvider:
    """Use Meta first and fall back only for non-ambiguous access/configuration failures.

    Timeouts, connection failures, rate limits, and generic provider failures are not
    automatically replayed against another provider because the first request may
    already have been accepted and could be billable.
    """

    FALLBACK_ERROR_CODES = frozenset({
        "not_configured",
        "authentication_failed",
        "access_restricted",
        "invalid_provider_request",
        # An accepted response with no answer or tool call is a provider
        # contract failure, not an ambiguous timeout. It is safe to try the
        # explicitly configured fallback for this case.
        "malformed_provider_response",
    })

    def __init__(
        self,
        primary: MetaModelProvider,
        fallback: OpenRouterModelProvider,
        *,
        enabled: bool,
        allow_transient_fallback: bool = False,
    ):
        self.primary = primary
        self.fallback = fallback
        self.enabled = enabled
        self.allow_transient_fallback = allow_transient_fallback
        self._last_provider: OpenAICompatibleModelProvider | None = None

    @property
    def configured(self) -> bool:
        return self.primary.configured or (self.enabled and self.fallback.configured)

    @property
    def model(self) -> str:
        provider = self._last_provider or (self.primary if self.primary.configured or not self.fallback_configured else self.fallback)
        return provider.model

    @property
    def base_url(self) -> str:
        provider = self._last_provider or (self.primary if self.primary.configured or not self.fallback_configured else self.fallback)
        return provider.base_url

    @property
    def provider_label(self) -> str:
        provider = self._last_provider or (self.primary if self.primary.configured or not self.fallback_configured else self.fallback)
        return provider.provider_label

    @property
    def fallback_configured(self) -> bool:
        return self.enabled and self.fallback.configured

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_completion_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        if not self.primary.configured:
            if self.fallback_configured:
                return await self._complete_with(self.fallback, messages, tools, max_completion_tokens, response_format)
            raise ProviderError("not_configured", "No configured model provider is available on the backend.")
        try:
            return self._usable_response(
                await self._complete_with(self.primary, messages, tools, max_completion_tokens, response_format),
                self.primary,
            )
        except ProviderError as exc:
            fallback_codes = self.FALLBACK_ERROR_CODES
            if self.allow_transient_fallback:
                fallback_codes = fallback_codes | {"provider_timeout", "connection_failed"}
            if not self.fallback_configured or exc.code not in fallback_codes:
                raise
            return self._usable_response(
                await self._complete_with(self.fallback, messages, tools, max_completion_tokens, response_format),
                self.fallback,
            )

    async def _complete_with(self, provider: OpenAICompatibleModelProvider, messages, tools, max_completion_tokens, response_format=None):
        self._last_provider = provider
        if response_format is None:
            return await provider.complete(messages, tools, max_completion_tokens)
        return await provider.complete(
            messages,
            tools,
            max_completion_tokens,
            response_format=response_format,
        )

    @property
    def supports_json_mode(self) -> bool:
        # Do not send OpenRouter-only fields to Meta. In the local fallback
        # configuration the primary provider is disabled, so this is true;
        # when Meta is active, the common request remains unchanged.
        return not self.primary.configured and self.fallback_configured

    @staticmethod
    def _usable_response(response: ProviderResponse, provider: OpenAICompatibleModelProvider) -> ProviderResponse:
        # Keep this check at the composition boundary as well as in the SDK
        # adapter. Tests and future injected providers may return the typed
        # response directly without going through OpenAICompatibleModelProvider.
        if not response.content.strip() and not response.tool_calls:
            raise ProviderError(
                "malformed_provider_response",
                f"{provider.provider_label} returned neither an answer nor a tool call.",
            )
        return response

    async def test_connection(self) -> ProviderResponse:
        if not self.primary.configured and self.fallback_configured:
            self._last_provider = self.fallback
            return await self.fallback.test_connection()
        if not self.primary.configured:
            raise ProviderError("not_configured", "No configured model provider is available on the backend.")
        try:
            self._last_provider = self.primary
            return await self.primary.test_connection()
        except ProviderError as exc:
            fallback_codes = self.FALLBACK_ERROR_CODES
            if self.allow_transient_fallback:
                fallback_codes = fallback_codes | {"provider_timeout", "connection_failed"}
            if not self.fallback_configured or exc.code not in fallback_codes:
                raise
            self._last_provider = self.fallback
            return await self.fallback.test_connection()
