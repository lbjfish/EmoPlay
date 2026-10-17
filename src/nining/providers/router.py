"""Explicit, bounded provider routing and fallback policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..capabilities import Capability, discover_capabilities
from ..config import AppConfig, ModelConfig
from .adapters import (
    AzureImageAdapter,
    AzureTextAdapter,
    DeepSeekTextAdapter,
    DeterministicTextAdapter,
    MacOSSayVoiceAdapter,
    PlaceholderImageAdapter,
    PromptOnlyVoiceAdapter,
    ProviderAdapter,
)
from .contract import (
    BillingState,
    ProviderAttempt,
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResult,
    RequestKind,
)
from .transport import CurlIPv4Transport, TransportLike


def _fallback_names(model: ModelConfig) -> tuple[str, ...]:
    if model.fallback_providers:
        return model.fallback_providers
    if model.fallback_provider:
        return (model.fallback_provider,)
    return ()


def _canonical_provider_name(value: str) -> str:
    lowered = value.strip().lower()
    aliases = {
        "azure": "azure_openai",
        "azure-openai": "azure_openai",
        "azureopenai": "azure_openai",
        "deepseek_text": "deepseek",
        "prompt-only": "prompt_only",
    }
    return aliases.get(lowered, lowered)


class ProviderRouter:
    """Run each adapter at most once and only fall back on eligible errors.

    The normal text chain is Azure gpt-5.6-sol, DeepSeek, then deterministic.
    No retry loop lives here: an unknown billing outcome is recorded on the
    failed attempt and the same remote adapter is never called again for that
    logical request.
    """

    def __init__(
        self,
        *,
        text_adapters: Sequence[ProviderAdapter] = (),
        image_adapters: Sequence[ProviderAdapter] = (),
        voice_adapters: Sequence[ProviderAdapter] = (),
        capabilities: Mapping[str, Capability] | None = None,
    ) -> None:
        self._chains: dict[RequestKind, tuple[ProviderAdapter, ...]] = {
            RequestKind.TEXT: tuple(text_adapters),
            RequestKind.IMAGE: tuple(image_adapters),
            RequestKind.VOICE: tuple(voice_adapters),
        }
        self.capabilities = dict(capabilities or {})

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        environ: Mapping[str, str] | None = None,
        transports: Mapping[str, TransportLike] | TransportLike | None = None,
    ) -> ProviderRouter:
        """Build adapters from existing config/capability interfaces.

        A callable or transport object makes tests entirely socket-free.  If no
        transport is supplied, remote adapters still require
        ``runtime.allow_network`` before they may create a stdlib HTTP request.
        """

        env = environ if environ is not None else None
        network_allowed = config.runtime.allow_network and config.runtime.mode not in {
            "offline",
            "release-view",
        }

        def transport_for(provider: str, kind: RequestKind) -> TransportLike | None:
            if transports is None:
                return None
            if isinstance(transports, Mapping):
                selected = transports.get(f"{kind.value}:{provider}")
                if selected is None:
                    selected = transports.get(provider)
                return selected
            return transports

        def text_adapter(model: ModelConfig) -> ProviderAdapter | None:
            if not model.enabled:
                return None
            name = _canonical_provider_name(model.provider)
            if name == "azure_openai":
                return AzureTextAdapter(
                    model,
                    environ=env,
                    transport=transport_for("azure_openai", RequestKind.TEXT),
                    network_allowed=network_allowed,
                )
            if name == "deepseek":
                return DeepSeekTextAdapter(
                    model,
                    environ=env,
                    transport=transport_for("deepseek", RequestKind.TEXT),
                    network_allowed=network_allowed,
                )
            if name in {"deterministic", "local"}:
                return DeterministicTextAdapter()
            return None

        def image_adapter(model: ModelConfig) -> ProviderAdapter | None:
            if not model.enabled:
                return None
            name = _canonical_provider_name(model.provider)
            if name == "azure_openai":
                selected_transport = transport_for(
                    "azure_openai", RequestKind.IMAGE
                )
                if (
                    selected_transport is None
                    and model.extras.get("transport") == "curl_ipv4"
                ):
                    selected_transport = CurlIPv4Transport()
                return AzureImageAdapter(
                    model,
                    environ=env,
                    transport=selected_transport,
                    network_allowed=network_allowed,
                )
            if name in {"placeholder", "local"}:
                return PlaceholderImageAdapter()
            return None

        def voice_adapter(model: ModelConfig) -> ProviderAdapter | None:
            name = _canonical_provider_name(model.provider)
            if name == "macos_say":
                return MacOSSayVoiceAdapter(model)
            return PromptOnlyVoiceAdapter()

        def secondary_config(name: str) -> ModelConfig:
            candidate = config.providers.get(name)
            if candidate is not None:
                return candidate
            return ModelConfig(provider=name, enabled=True)

        text_chain: list[ProviderAdapter] = []
        primary_text = text_adapter(config.text)
        if primary_text is not None:
            text_chain.append(primary_text)
        for name in _fallback_names(config.text):
            candidate = text_adapter(secondary_config(name))
            if candidate is not None and not any(
                item.provider == candidate.provider for item in text_chain
            ):
                text_chain.append(candidate)
        if not any(item.provider == "deterministic" for item in text_chain):
            text_chain.append(DeterministicTextAdapter())

        image_chain: list[ProviderAdapter] = []
        primary_image = image_adapter(config.image)
        if primary_image is not None:
            image_chain.append(primary_image)
        for name in _fallback_names(config.image):
            candidate = image_adapter(secondary_config(name))
            if candidate is not None and not any(
                item.provider == candidate.provider for item in image_chain
            ):
                image_chain.append(candidate)
        if not any(item.provider == "placeholder" for item in image_chain):
            image_chain.append(PlaceholderImageAdapter())

        voice_chain: list[ProviderAdapter] = []
        if config.voice.enabled:
            primary_voice = voice_adapter(config.voice)
            if primary_voice is not None:
                voice_chain.append(primary_voice)
        for name in _fallback_names(config.voice):
            candidate = voice_adapter(secondary_config(name))
            if candidate is not None and not any(
                item.provider == candidate.provider for item in voice_chain
            ):
                voice_chain.append(candidate)
        if not any(item.provider == "prompt_only" for item in voice_chain):
            voice_chain.append(PromptOnlyVoiceAdapter())

        return cls(
            text_adapters=text_chain,
            image_adapters=image_chain,
            voice_adapters=voice_chain,
            capabilities=discover_capabilities(config, environ),
        )

    def adapters_for(self, kind: RequestKind | str) -> tuple[ProviderAdapter, ...]:
        return self._chains[RequestKind(kind)]

    def execute(self, request: ProviderRequest) -> ProviderResult:
        chain = self._chains.get(RequestKind(request.kind), ())
        if not chain:
            return ProviderResult.failure(
                request,
                provider="router",
                error=ProviderError(
                    ProviderErrorKind.AVAILABILITY,
                    "no_adapter",
                    "no provider adapter is configured for this request kind",
                ),
                billing_state=BillingState.ZERO,
            )
        attempts: list[ProviderAttempt] = []
        for index, adapter in enumerate(chain):
            result = adapter.execute(request)
            attempts.append(
                ProviderAttempt(
                    provider=result.provider,
                    model=result.model,
                    success=result.ok,
                    billing_state=result.billing_state,
                    error=result.error,
                )
            )
            if result.ok:
                return result.with_attempts(tuple(attempts))
            assert result.error is not None
            if (
                index == len(chain) - 1
                or not request.allows_fallback(result.error)
                or BillingState(result.billing_state) is not BillingState.ZERO
            ):
                return result.with_attempts(tuple(attempts))
        raise AssertionError("provider chain must return or stop")

    def route(self, request: ProviderRequest) -> ProviderResult:
        """Alias for integrations that name provider execution as routing."""

        return self.execute(request)

    def generate_text(
        self,
        prompt: str,
        *,
        model: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ProviderResult:
        return self.execute(
            ProviderRequest(
                kind=RequestKind.TEXT,
                prompt=prompt,
                model=model,
                parameters=parameters or {},
                idempotency_key=idempotency_key,
            )
        )

    def generate_image(
        self,
        prompt: str,
        *,
        operation: str = "generate",
        model: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ProviderResult:
        return self.execute(
            ProviderRequest(
                kind=RequestKind.IMAGE,
                prompt=prompt,
                operation=operation,
                model=model,
                parameters=parameters or {},
                idempotency_key=idempotency_key,
            )
        )

    def build_voice_spec(
        self,
        prompt: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ProviderResult:
        return self.execute(
            ProviderRequest(
                kind=RequestKind.VOICE,
                prompt=prompt,
                parameters=parameters or {},
                idempotency_key=idempotency_key,
            )
        )


def build_provider_router(
    config: AppConfig,
    *,
    environ: Mapping[str, str] | None = None,
    transports: Mapping[str, TransportLike] | TransportLike | None = None,
) -> ProviderRouter:
    """Convenience factory retained as the public config integration point."""

    return ProviderRouter.from_config(config, environ=environ, transports=transports)


router_from_config = build_provider_router
