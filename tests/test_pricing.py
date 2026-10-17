from __future__ import annotations

from nining.pricing import estimate_provider_cost
from nining.providers import BillingState


def test_azure_text_and_image_cost_formulas_use_token_usage() -> None:
    text = estimate_provider_cost(
        provider="azure_openai",
        model="gpt-5.6-sol",
        kind="text",
        usage={"input_tokens": 1000, "output_tokens": 500},
        billing_state="unknown",
    )
    image = estimate_provider_cost(
        provider="azure_openai",
        model="gpt-image-2",
        kind="image",
        usage={
            "input_tokens_details": {"text_tokens": 100, "image_tokens": 50},
            "output_tokens": 1000,
        },
        billing_state="unknown",
    )

    assert text.amount_micros == 14_000
    assert text.currency == "USD"
    assert text.billing_state is BillingState.ESTIMATED
    assert image.amount_micros == 30_900
    assert image.billing_state is BillingState.ESTIMATED


def test_cost_estimation_never_invents_missing_usage_or_local_cost() -> None:
    missing = estimate_provider_cost(
        provider="azure_openai",
        model="gpt-image-2",
        kind="image",
        usage={},
        billing_state="unknown",
    )
    local = estimate_provider_cost(
        provider="macos_say",
        model="macos-say:Tingting",
        kind="voice",
        usage={},
        billing_state="zero",
    )

    assert missing.amount_micros is None
    assert missing.currency is None
    assert missing.billing_state is BillingState.UNKNOWN
    assert local.amount_micros == 0
    assert local.billing_state is BillingState.ZERO
