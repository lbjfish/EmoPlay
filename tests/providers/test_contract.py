from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from nining.config import ModelConfig, load_config
from nining.providers import (
    AzureImageAdapter,
    AzureTextAdapter,
    BillingState,
    CurlIPv4Transport,
    DeepSeekTextAdapter,
    DeterministicTextAdapter,
    HttpRequest,
    HttpResponse,
    MacOSSayVoiceAdapter,
    MockAdapter,
    PlaceholderImageAdapter,
    PromptOnlyVoiceAdapter,
    ProviderErrorKind,
    ProviderRequest,
    ProviderRouter,
    RequestKind,
)


class FixtureTransport:
    def __init__(self, outcomes: list[HttpResponse | BaseException]) -> None:
        self.outcomes = outcomes
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response(
    status: int, payload: Any, headers: dict[str, str] | None = None
) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        headers=headers or {},
        body=json.dumps(payload).encode(),
    )


def text_success(value: str = "answer") -> HttpResponse:
    return response(
        200,
        {
            "choices": [{"message": {"content": value}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    )


def azure_text(
    transport: FixtureTransport | Callable[[HttpRequest], HttpResponse],
) -> AzureTextAdapter:
    return AzureTextAdapter(
        endpoint="https://fixture.openai.azure.com/tenant",
        api_key="TEST_ONLY_azure-super-secret",
        transport=transport,
        network_allowed=True,
    )


def deepseek_text(
    transport: FixtureTransport | Callable[[HttpRequest], HttpResponse],
) -> DeepSeekTextAdapter:
    return DeepSeekTextAdapter(
        endpoint="https://api.deepseek.com/v1",
        api_key="TEST_ONLY_deepseek-super-secret",
        transport=transport,
        network_allowed=True,
    )


def test_azure_text_200_builds_gpt_5_6_request_with_idempotency_and_redaction() -> None:
    transport = FixtureTransport([text_success("来自 Azure 的回答")])
    request = ProviderRequest(kind="text", prompt="你好", parameters={"temperature": 0})
    result = azure_text(transport).execute(request)

    assert result.ok is True
    assert result.provider == "azure_openai"
    assert result.model == "gpt-5.6-sol"
    assert result.text == "来自 Azure 的回答"
    assert BillingState(result.billing_state).value == "unknown"
    assert result.final_eligible is True
    wire = transport.requests[0]
    assert "/openai/deployments/gpt-5.6-sol/chat/completions" in wire.url
    assert "api-version=" in wire.url
    assert wire.headers["api-key"] == "TEST_ONLY_azure-super-secret"
    assert "Authorization" not in wire.headers
    assert wire.headers["Idempotency-Key"] == request.idempotency_key
    assert json.loads(wire.body) == {
        "messages": [{"content": "你好", "role": "user"}],
        "model": "gpt-5.6-sol",
        "temperature": 0,
    }
    audit = json.dumps(
        {"request": wire.to_audit_dict(), "result": result.to_audit_dict()}
    )
    assert "TEST_ONLY_azure-super-secret" not in audit
    assert "fixture.openai.azure.com" not in audit
    assert "你好" not in audit
    assert "[REDACTED]" in audit


def test_azure_v1_responses_request_and_output_are_supported() -> None:
    transport = FixtureTransport(
        [
            response(
                200,
                {
                    "id": "resp_fixture",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "精炼提示词"}
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            )
        ]
    )
    endpoint = "https://fixture.services.ai." + "azure.com/openai/v1/responses"
    adapter = AzureTextAdapter(
        ModelConfig(
            provider="azure_openai",
            default_model="gpt-5.6-sol",
            extras={"api_style": "responses"},
        ),
        endpoint=endpoint,
        api_key="TEST_ONLY_azure-secret",
        transport=transport,
        network_allowed=True,
    )
    result = adapter.execute(
        ProviderRequest(
            kind="text",
            prompt="原始提示",
            parameters={"max_completion_tokens": 1200, "seed": 7},
        )
    )

    assert result.ok and result.text == "精炼提示词"
    assert result.usage == {"input_tokens": 10, "output_tokens": 5}
    wire = transport.requests[0]
    assert wire.url == endpoint
    payload = json.loads(wire.body)
    assert payload == {
        "input": [{"content": "原始提示", "role": "user"}],
        "max_output_tokens": 1200,
        "model": "gpt-5.6-sol",
        "store": False,
    }
    assert wire.headers["Authorization"] == "Bear" + "er TEST_ONLY_azure-secret"
    assert "api-key" not in wire.headers


def test_200_with_malformed_schema_fails_closed_without_output() -> None:
    transport = FixtureTransport([response(200, {"choices": []})])
    result = azure_text(transport).execute(ProviderRequest(kind="text", prompt="x"))

    assert result.ok is False
    assert result.error and result.error.kind is ProviderErrorKind.MALFORMED_RESPONSE
    assert BillingState(result.billing_state).value == "unknown"


def test_explicit_remote_config_never_invents_an_account_model_name() -> None:
    transport = FixtureTransport([text_success("must not run")])
    adapter = AzureTextAdapter(
        ModelConfig(
            provider="azure_openai",
            endpoint_env="AZURE_TEXT_ENDPOINT",
            api_key_env="AZURE_TEXT_API_KEY",
            model_env="AZURE_TEXT_MODEL",
        ),
        environ={
            "AZURE_TEXT_ENDPOINT": "https://fixture.openai.azure.com",
            "AZURE_TEXT_API_KEY": "TEST_ONLY_azure-secret",
        },
        transport=transport,
        network_allowed=True,
    )

    result = adapter.execute(ProviderRequest(kind="text", prompt="x"))

    assert result.ok is False
    assert result.error and result.error.code == "configuration_unavailable"
    assert result.error.details == {"missing": ["model"]}
    assert transport.requests == []


@pytest.mark.parametrize(
    ("status", "expected", "eligible"),
    [
        (401, ProviderErrorKind.AUTHENTICATION, False),
        (403, ProviderErrorKind.PERMISSION, False),
        (404, ProviderErrorKind.DEPLOYMENT, True),
        (429, ProviderErrorKind.RATE_LIMIT, True),
        (503, ProviderErrorKind.TRANSIENT, True),
    ],
)
def test_http_error_contract_classifies_statuses(
    status: int, expected: ProviderErrorKind, eligible: bool
) -> None:
    transport = FixtureTransport(
        [response(status, {"error": {"code": "fixture_failure"}}, {"Retry-After": "2"})]
    )
    result = azure_text(transport).execute(ProviderRequest(kind="text", prompt="x"))

    assert result.ok is False
    assert result.error and result.error.kind is expected
    assert result.error and result.error.fallback_eligible is eligible
    assert result.error and result.error.http_status == status
    if status == 429:
        assert result.error.retry_after_seconds == 2


def test_timeout_and_policy_block_have_separate_safe_dispositions() -> None:
    timed_out = azure_text(FixtureTransport([TimeoutError("late")])).execute(
        ProviderRequest(kind="text", prompt="x")
    )
    policy = azure_text(
        FixtureTransport([response(400, {"error": {"code": "content_filter"}})])
    ).execute(ProviderRequest(kind="text", prompt="x"))

    assert timed_out.error and timed_out.error.kind is ProviderErrorKind.TIMEOUT
    assert timed_out.error and timed_out.error.fallback_eligible is True
    assert timed_out.model == "gpt-5.6-sol"
    assert BillingState(timed_out.billing_state).value == "unknown"
    assert policy.error and policy.error.kind is ProviderErrorKind.POLICY
    assert policy.error and policy.error.fallback_eligible is False
    assert BillingState(policy.billing_state).value == "zero"


def test_router_falls_from_azure_rate_limit_to_deepseek_once() -> None:
    azure_transport = FixtureTransport(
        [response(429, {"error": {"code": "rate_limit"}})]
    )
    deepseek_transport = FixtureTransport([text_success("DeepSeek fallback")])
    router = ProviderRouter(
        text_adapters=(
            azure_text(azure_transport),
            deepseek_text(deepseek_transport),
            DeterministicTextAdapter(),
        )
    )

    result = router.execute(ProviderRequest(kind="text", prompt="continue"))

    assert result.ok is True
    assert result.provider == "deepseek"
    assert [attempt.provider for attempt in result.attempts] == [
        "azure_openai",
        "deepseek",
    ]
    assert len(azure_transport.requests) == 1
    assert len(deepseek_transport.requests) == 1


def test_router_stops_on_auth_policy_or_validation_without_paid_fallback() -> None:
    azure_transport = FixtureTransport([response(401, {"error": {"code": "bad_key"}})])
    deepseek_transport = FixtureTransport([text_success("must not run")])
    router = ProviderRouter(
        text_adapters=(
            azure_text(azure_transport),
            deepseek_text(deepseek_transport),
            DeterministicTextAdapter(),
        )
    )

    result = router.execute(ProviderRequest(kind="text", prompt="continue"))

    assert result.ok is False
    assert result.error and result.error.kind is ProviderErrorKind.AUTHENTICATION
    assert [attempt.provider for attempt in result.attempts] == ["azure_openai"]
    assert deepseek_transport.requests == []


def test_router_stops_when_failed_call_has_unknown_billing_state() -> None:
    azure_transport = FixtureTransport([response(503, {"error": {"code": "busy"}})])
    deepseek_transport = FixtureTransport([response(503, {"error": {"code": "busy"}})])
    router = ProviderRouter(
        text_adapters=(
            azure_text(azure_transport),
            deepseek_text(deepseek_transport),
            DeterministicTextAdapter(),
        )
    )
    request = ProviderRequest(kind="text", prompt="deterministic fallback")

    result = router.execute(request)

    assert result.ok is False
    assert result.provider == "azure_openai"
    assert len(azure_transport.requests) == 1
    assert len(deepseek_transport.requests) == 0
    assert [BillingState(attempt.billing_state).value for attempt in result.attempts] == [
        "unknown",
    ]


def test_offline_network_gate_precedes_injected_transport() -> None:
    transport = FixtureTransport([text_success("must not run")])
    adapter = AzureTextAdapter(
        endpoint="https://offline-fixture.openai.azure.com",
        api_key="TEST_ONLY_offline-key",
        transport=transport,
        network_allowed=False,
    )

    result = adapter.execute(ProviderRequest(kind="text", prompt="x"))

    assert result.ok is False
    assert result.error and result.error.kind is ProviderErrorKind.NETWORK_DISABLED
    assert BillingState(result.billing_state) is BillingState.ZERO
    assert transport.requests == []


def test_404_deployment_is_eligible_for_configured_secondary() -> None:
    azure_transport = FixtureTransport(
        [response(404, {"error": {"code": "DeploymentNotFound"}})]
    )
    deepseek_transport = FixtureTransport([text_success("second deployment")])
    result = ProviderRouter(
        text_adapters=(azure_text(azure_transport), deepseek_text(deepseek_transport))
    ).execute(ProviderRequest(kind="text", prompt="x"))

    assert result.provider == "deepseek"
    assert result.ok


def test_explicit_safe_rule_can_allow_an_otherwise_blocked_category() -> None:
    azure_transport = FixtureTransport([response(400, {"error": {"code": "invalid"}})])
    deepseek_transport = FixtureTransport([text_success("explicitly safe")])
    result = ProviderRouter(
        text_adapters=(azure_text(azure_transport), deepseek_text(deepseek_transport))
    ).execute(
        ProviderRequest(
            kind="text",
            prompt="x",
            safe_fallback_for=frozenset({ProviderErrorKind.VALIDATION}),
        )
    )

    assert result.ok
    assert result.provider == "deepseek"


def test_azure_image_generations_edits_and_base64_validation() -> None:
    generated = base64.b64encode(b"fixture-png-bytes").decode()
    generation_transport = FixtureTransport(
        [response(200, {"data": [{"b64_json": generated}]})]
    )
    image = AzureImageAdapter(
        endpoint="https://fixture.openai.azure.com",
        api_key="TEST_ONLY_image-secret",
        transport=generation_transport,
        network_allowed=True,
    )
    result = image.execute(
        ProviderRequest(
            kind=RequestKind.IMAGE,
            prompt="portrait",
            parameters={"size": "1024x1024"},
        )
    )

    assert result.ok
    assert result.images == (b"fixture-png-bytes",)
    assert result.final_eligible is True
    generation = generation_transport.requests[0]
    assert "/images/generations" in generation.url
    assert json.loads(generation.body)["model"] == "gpt-image-2"
    assert generation.headers["api-key"] == "TEST_ONLY_image-secret"
    assert "TEST_ONLY_image-secret" not in json.dumps(generation.to_audit_dict())

    edit_request = ProviderRequest(
        kind="image",
        prompt="turn it blue",
        operation="edit",
        image_inputs=(b"input-bytes",),
        mask=b"mask-bytes",
    )
    edit = image.build_edit_request(edit_request)
    assert "/images/edits" in edit.url
    assert edit.headers["Content-Type"].startswith("multipart/form-data")
    assert b'name="image[]"' in edit.body
    assert b'name="mask"' in edit.body
    assert b"input-bytes" in edit.body

    invalid = AzureImageAdapter(
        endpoint="https://fixture.openai.azure.com",
        api_key="TEST_ONLY_image-secret",
        transport=FixtureTransport(
            [response(200, {"data": [{"b64_json": "***not-base64***"}]})]
        ),
        network_allowed=True,
    ).execute(ProviderRequest(kind="image", prompt="portrait"))
    assert invalid.error and invalid.error.kind is ProviderErrorKind.MALFORMED_RESPONSE


def test_azure_v1_image_endpoint_is_normalized_without_legacy_deployment_path() -> None:
    generated = base64.b64encode(b"fixture-png-bytes").decode()
    transport = FixtureTransport([response(200, {"data": [{"b64_json": generated}]})])
    endpoint = (
        "https://fixture.services.ai." + "azure.com/openai/v1/images/generations"
    )
    result = AzureImageAdapter(
        endpoint=endpoint,
        api_key="TEST_ONLY_image-secret",
        transport=transport,
        network_allowed=True,
    ).execute(ProviderRequest(kind="image", prompt="portrait"))

    assert result.ok
    assert transport.requests[0].url == endpoint
    assert "api-version" not in transport.requests[0].url
    assert (
        transport.requests[0].headers["Authorization"]
        == "Bear" + "er TEST_ONLY_image-secret"
    )
    assert "api-key" not in transport.requests[0].headers


@pytest.mark.parametrize(
    ("adapter", "endpoint"),
    [
        (AzureTextAdapter, "http://fixture.openai.azure.com"),
        (AzureTextAdapter, "https://user@fixture.openai.azure.com"),
        (AzureTextAdapter, "https://arbitrary.example.invalid"),
        (DeepSeekTextAdapter, "https://arbitrary.example.invalid/v1"),
    ],
)
def test_remote_endpoint_policy_rejects_unsafe_targets(adapter: Any, endpoint: str) -> None:
    transport = FixtureTransport([text_success("must not run")])
    result = adapter(
        endpoint=endpoint,
        api_key="TEST_ONLY_remote-secret",
        transport=transport,
        network_allowed=True,
    ).execute(ProviderRequest(kind="text", prompt="x"))

    assert result.ok is False
    assert result.error and result.error.kind is ProviderErrorKind.VALIDATION
    assert transport.requests == []


def test_placeholder_and_prompt_only_never_claim_final_output() -> None:
    image = PlaceholderImageAdapter().execute(ProviderRequest(kind="image", prompt="x"))
    voice = PromptOnlyVoiceAdapter().execute(ProviderRequest(kind="voice", prompt="x"))

    assert image.ok and image.final_eligible is False
    assert voice.ok and voice.final_eligible is False
    assert voice.voice_spec and voice.voice_spec["mode"] == "prompt_only"
    assert '"prompt": "x"' not in json.dumps(voice.to_audit_dict())

    with pytest.raises(TypeError, match="final_eligible"):
        ProviderRequest(
            kind="image", prompt="x", final_eligible=cast(Any, "false")
        )


def test_macos_preset_voice_returns_hashed_audio_and_exact_script_is_not_audited() -> None:
    extended_44100 = bytes.fromhex("400eac44000000000000")
    comm = b"\x00\x01" + (44100).to_bytes(4, "big") + b"\x00\x10" + extended_44100
    aiff = b"FORM" + (4 + 8 + len(comm)).to_bytes(4, "big") + b"AIFF"
    aiff += b"COMM" + len(comm).to_bytes(4, "big") + comm
    seen: list[str] = []

    def synthesize(script: str, voice: str, rate: int, output_format: str) -> bytes:
        seen.extend((script, voice, str(rate), output_format))
        return aiff

    result = MacOSSayVoiceAdapter(synthesizer=synthesize).execute(
        ProviderRequest(
            kind="voice",
            prompt="performance directions",
            parameters={
                "script": "精确脚本",
                "voice": "Tingting",
                "rate": 220,
                "output_format": "aiff",
            },
        )
    )

    assert result.ok and result.audio == aiff
    assert result.final_eligible is True
    assert result.metadata["duration_ms"] == 1000
    assert seen == ["精确脚本", "Tingting", "220", "aiff"]
    audit = json.dumps(result.to_audit_dict(), ensure_ascii=False)
    assert "精确脚本" not in audit
    assert result.to_audit_dict()["audio"]["bytes_length"] == len(aiff)


def test_image_rate_limit_falls_to_non_final_placeholder_and_mock_is_non_final() -> (
    None
):
    remote = AzureImageAdapter(
        endpoint="https://fixture.openai.azure.com",
        api_key="TEST_ONLY_image-secret",
        transport=FixtureTransport([response(429, {"error": {"code": "rate_limit"}})]),
        network_allowed=True,
    )
    result = ProviderRouter(image_adapters=(remote, PlaceholderImageAdapter())).execute(
        ProviderRequest(kind="image", prompt="x")
    )
    mock = MockAdapter("text").execute(ProviderRequest(kind="text", prompt="x"))

    assert result.ok and result.provider == "placeholder"
    assert result.final_eligible is False
    assert [attempt.provider for attempt in result.attempts] == [
        "azure_openai",
        "placeholder",
    ]
    assert mock.ok and mock.final_eligible is False


def test_from_config_uses_static_interfaces_and_offline_network_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "config.example.toml")
    config = replace(
        config, runtime=replace(config.runtime, mode="hybrid", allow_network=False)
    )
    router = ProviderRouter.from_config(
        config,
        environ={
            "AZURE_TEXT_ENDPOINT": "https://fixture.openai.azure.com",
            "AZURE_TEXT_API_KEY": "do-not-leak",
            "DEEPSEEK_ENDPOINT": "https://api.deepseek.com/v1",
            "DEEPSEEK_API_KEY": "do-not-leak",
            "DEEPSEEK_TEXT_MODEL": "fixture-model",
        },
    )

    result = router.generate_text("offline guarded")

    assert result.ok
    assert result.provider == "deterministic"
    assert [attempt.provider for attempt in result.attempts] == [
        "azure_openai",
        "deepseek",
        "deterministic",
    ]
    assert {"text", "image", "voice"} <= set(router.capabilities)


def test_from_config_skips_disabled_remote_adapter() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "config.example.toml")
    config = replace(config, text=replace(config.text, enabled=False))

    router = ProviderRouter.from_config(config, environ={})

    assert [adapter.provider for adapter in router.adapters_for("text")] == [
        "deepseek",
        "deterministic",
    ]


def test_live_example_selects_proven_ipv4_image_transport_and_timeout() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "config.creator.example.toml")
    router = ProviderRouter.from_config(
        config,
        environ={
            "AZURE_IMAGE_ENDPOINT": (
                "https://fixture.services.ai."
                + "azure.com/openai/v1/images/generations"
            ),
            "AZURE_IMAGE_API_KEY": "TEST_ONLY_image-key",
            "AZURE_IMAGE_MODEL": "gpt-image-2",
        },
    )

    adapter = router.adapters_for("image")[0]
    assert isinstance(adapter, AzureImageAdapter)
    assert isinstance(adapter.transport, CurlIPv4Transport)
    assert adapter.timeout_seconds == 900
