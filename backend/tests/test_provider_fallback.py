from __future__ import annotations

import asyncio

import pytest

from app.services.model_provider import (
    FallbackModelProvider,
    ProviderError,
    ProviderResponse,
    ProviderUsage,
)


def response(content: str = "ready") -> ProviderResponse:
    return ProviderResponse(
        content=content,
        tool_calls=[],
        finish_reason="stop",
        usage=ProviderUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        request_id="fallback-test",
    )


class StubProvider:
    def __init__(self, label: str, *, configured: bool, result=None):
        self.provider_label = label
        self.model = f"{label.lower()}-model"
        self.base_url = f"https://{label.lower()}.example/v1"
        self.configured = configured
        self.result = result or response(label)
        self.calls = 0

    async def complete(self, messages, tools, max_completion_tokens):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def test_connection(self):
        return await self.complete([], [], 16)


def test_fallback_handles_primary_access_restriction_without_exposing_credentials():
    primary = StubProvider("Meta", configured=True, result=ProviderError("access_restricted", "safe primary error"))
    fallback = StubProvider("OpenRouter", configured=True)
    provider = FallbackModelProvider(primary, fallback, enabled=True)

    result = asyncio.run(provider.complete([], [], 32))

    assert result.content == "OpenRouter"
    assert primary.calls == 1
    assert fallback.calls == 1
    assert provider.provider_label == "OpenRouter"
    assert "key" not in provider.base_url


def test_fallback_handles_empty_primary_completion():
    primary = StubProvider("Meta", configured=True, result=response(""))
    fallback = StubProvider("OpenRouter", configured=True)
    provider = FallbackModelProvider(primary, fallback, enabled=True)

    result = asyncio.run(provider.complete([], [], 32))

    assert result.content == "OpenRouter"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_fallback_does_not_replay_ambiguous_timeout_or_rate_limit():
    for code in ("provider_timeout", "connection_failed", "rate_limited", "provider_error"):
        primary = StubProvider("Meta", configured=True, result=ProviderError(code, "safe primary error"))
        fallback = StubProvider("OpenRouter", configured=True)
        provider = FallbackModelProvider(primary, fallback, enabled=True)

        with pytest.raises(ProviderError) as error:
            asyncio.run(provider.complete([], [], 32))

        assert error.value.code == code
        assert primary.calls == 1
        assert fallback.calls == 0


def test_transient_fallback_requires_explicit_opt_in():
    primary = StubProvider("Meta", configured=True, result=ProviderError("provider_timeout", "safe timeout"))
    fallback = StubProvider("OpenRouter", configured=True)
    provider = FallbackModelProvider(primary, fallback, enabled=True, allow_transient_fallback=True)

    result = asyncio.run(provider.complete([], [], 32))

    assert result.content == "OpenRouter"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_fallback_is_used_when_primary_is_not_configured():
    primary = StubProvider("Meta", configured=False)
    fallback = StubProvider("OpenRouter", configured=True)
    provider = FallbackModelProvider(primary, fallback, enabled=True)

    result = asyncio.run(provider.test_connection())

    assert result.content == "OpenRouter"
    assert primary.calls == 0
    assert fallback.calls == 1


def test_disabled_fallback_does_not_call_configured_fallback():
    primary = StubProvider("Meta", configured=False)
    fallback = StubProvider("OpenRouter", configured=True)
    provider = FallbackModelProvider(primary, fallback, enabled=False)

    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.test_connection())

    assert error.value.code == "not_configured"
    assert primary.calls == 0
    assert fallback.calls == 0
