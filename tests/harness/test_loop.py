from __future__ import annotations

from typing import Any

import pytest

from nining.errors import NonRetryableActionError, RetryableActionError
from nining.harness import (
    ActionRegistry,
    ActionResult,
    ActionSpec,
    Correction,
    Decision,
    Evaluation,
    HarnessLoop,
    Observation,
)


def observer(snapshot: Any) -> Observation:
    return Observation(
        "current public state captured",
        {"attempt_seen": snapshot.attempt, "scene": snapshot.context.get("scene")},
    )


def one_action_decider(name: str, inputs: dict[str, Any] | None = None):
    def decide(snapshot: Any) -> Decision:
        del snapshot
        return Decision(
            name,
            inputs or {"value": 1},
            "selected the registered action from observable state",
            "operation-1",
        )

    return decide


def test_success_loop_has_complete_public_state_order_and_ids(trace_pair: Any) -> None:
    emitter, sink = trace_pair
    registry = ActionRegistry()
    received: list[Any] = []

    def succeed(inputs: Any, context: Any) -> dict[str, Any]:
        received.append((inputs, context))
        return {"answer": inputs["value"] + 1}

    registry.register(ActionSpec("local.compute", succeed, safe_to_retry=True))
    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("local.compute"),
        trace=emitter,
    ).run({"scene": "T1"}, run_id="run-success", requirement_ids=("REQ-2", "REQ-1"))

    assert outcome.succeeded is True
    assert outcome.stop_reason == "completed"
    assert outcome.attempts == 1
    assert [record["state"] for record in sink.records] == [
        "observe",
        "decide",
        "act",
        "evaluate",
        "stop",
    ]
    assert all(record["run_id"] == "run-success" for record in sink.records)
    assert all(record["requirement_ids"] == ["REQ-1", "REQ-2"] for record in sink.records)
    assert [record["sequence"] for record in sink.records] == [1, 2, 3, 4, 5]
    assert received[0][1].idempotency_key.startswith("idem_")
    assert sink.records[1]["decision"]["rationale_summary"]
    assert "chain_of_thought" not in sink.records[1]["decision"]


def test_evaluation_correction_loop_retries_same_logical_operation(trace_pair: Any) -> None:
    emitter, sink = trace_pair
    registry = ActionRegistry()
    calls: list[str] = []

    def improve(inputs: Any, context: Any) -> ActionResult:
        del inputs
        calls.append(context.idempotency_key)
        return ActionResult.success({"quality": len(calls)})

    def evaluate(snapshot: Any) -> Evaluation:
        quality = snapshot.result.value["quality"]
        return Evaluation(
            quality >= 2,
            "quality gate passed" if quality >= 2 else "quality below threshold",
            correctable=quality < 2,
            checks={"quality": quality},
        )

    def correct(snapshot: Any) -> Correction:
        return Correction(
            True,
            "apply the same deterministic enhancement once",
            action_name=snapshot.decision.action_name,
            inputs=snapshot.decision.inputs,
            operation_id=snapshot.decision.operation_id,
        )

    registry.register(ActionSpec("local.improve", improve, safe_to_retry=True))
    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("local.improve"),
        evaluator=evaluate,
        corrector=correct,
        trace=emitter,
    ).run(run_id="run-correct")

    assert outcome.succeeded is True
    assert outcome.attempts == 2
    assert calls[0] == calls[1], "same logical retry must reuse its idempotency key"
    assert [item["state"] for item in sink.records].count("correct") == 1
    correction = next(item for item in sink.records if item["state"] == "correct")
    assert correction["correction"]["policy_allowed"] is True
    assert correction["correction"]["policy_reason"] == "safe unpaid correction allowed"


def test_max_attempt_bound_stops_even_for_retryable_failure(trace_pair: Any) -> None:
    emitter, sink = trace_pair
    registry = ActionRegistry()
    call_count = 0

    def always_transient(inputs: Any, context: Any) -> ActionResult:
        nonlocal call_count
        del inputs, context
        call_count += 1
        raise RetryableActionError("temporary fixture failure")

    registry.register(ActionSpec("local.transient", always_transient, safe_to_retry=True))
    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("local.transient"),
        trace=emitter,
        max_attempts=2,
    ).run(run_id="run-bound")

    assert outcome.succeeded is False
    assert outcome.stop_reason == "max-attempts-reached"
    assert outcome.attempts == 2
    assert call_count == 2
    assert sink.records[-1]["state"] == "stop"
    assert sink.records[-1]["stop_reason"] == "max-attempts-reached"


def test_non_retryable_error_stops_after_one_action(trace_pair: Any) -> None:
    emitter, sink = trace_pair
    registry = ActionRegistry()

    def invalid(inputs: Any, context: Any) -> ActionResult:
        del inputs, context
        raise NonRetryableActionError("fixture input rejected")

    registry.register(ActionSpec("local.validate", invalid, safe_to_retry=True))
    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("local.validate"),
        trace=emitter,
    ).run(run_id="run-invalid")

    assert outcome.succeeded is False
    assert outcome.attempts == 1
    assert outcome.final_result is not None
    assert outcome.final_result.error is not None
    assert outcome.final_result.error.retryable is False
    assert [item["state"] for item in sink.records].count("act") == 1
    correction = next(item for item in sink.records if item["state"] == "correct")
    assert correction["correction"]["policy_allowed"] is False


def test_retryable_safe_unpaid_action_retries_and_keeps_error_event(trace_pair: Any) -> None:
    emitter, sink = trace_pair
    registry = ActionRegistry()
    calls = 0

    def flaky(inputs: Any, context: Any) -> dict[str, Any]:
        nonlocal calls
        del inputs, context
        calls += 1
        if calls == 1:
            raise TimeoutError("fixture timed out")
        return {"recovered": True}

    registry.register(ActionSpec("local.flaky", flaky, safe_to_retry=True))
    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("local.flaky"),
        trace=emitter,
    ).run(run_id="run-flaky")

    assert outcome.succeeded is True
    assert calls == 2
    error_records = [item for item in sink.records if item["state"] == "error"]
    assert len(error_records) == 1
    assert error_records[0]["error"]["error"]["category"] == "timeout"
    assert error_records[0]["error"]["handled"] is True


@pytest.mark.parametrize(
    ("action_name", "register", "expected_policy_fragment"),
    [
        ("azure.paid", True, "paid"),
        ("missing.action", False, "unknown"),
    ],
)
def test_paid_and_unknown_actions_are_never_blindly_retried(
    trace_pair: Any,
    action_name: str,
    register: bool,
    expected_policy_fragment: str,
) -> None:
    emitter, sink = trace_pair
    registry = ActionRegistry()

    def transient(inputs: Any, context: Any) -> ActionResult:
        del inputs, context
        raise RetryableActionError("provider temporarily unavailable")

    if register:
        registry.register(
            ActionSpec(
                action_name,
                transient,
                paid=True,
                safe_to_retry=True,
                capability=None,
            )
        )

    def force_retry(snapshot: Any) -> Correction:
        return Correction(
            True,
            "retry proposal used to exercise the guard",
            action_name=snapshot.decision.action_name,
            inputs=snapshot.decision.inputs,
        )

    def mark_correctable(snapshot: Any) -> Evaluation:
        return Evaluation(False, "failed", correctable=True)

    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider(action_name),
        evaluator=mark_correctable,
        corrector=force_retry,
        trace=emitter,
    ).run(run_id=f"run-{action_name}")

    assert outcome.succeeded is False
    assert outcome.attempts == 1
    correction = next(item for item in sink.records if item["state"] == "correct")
    assert correction["correction"]["policy_allowed"] is False
    assert expected_policy_fragment in correction["correction"]["policy_reason"]


def test_capability_unavailable_selects_declared_local_fallback(trace_pair: Any) -> None:
    emitter, sink = trace_pair
    registry = ActionRegistry()
    executed: list[str] = []

    def paid_image(inputs: Any, context: Any) -> dict[str, Any]:
        del inputs, context
        executed.append("azure")
        return {"kind": "final"}

    def placeholder(inputs: Any, context: Any) -> dict[str, Any]:
        del inputs, context
        executed.append("placeholder")
        return {"kind": "placeholder", "final_eligible": False}

    registry.register(
        ActionSpec(
            "image.azure",
            paid_image,
            capability="image",
            fallbacks=("image.placeholder",),
            paid=True,
        )
    )
    registry.register(
        ActionSpec("image.placeholder", placeholder, safe_to_retry=True)
    )
    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("image.azure"),
        capabilities={"image": {"state": "unavailable"}},
        trace=emitter,
    ).run(run_id="run-fallback")

    assert outcome.succeeded is True
    assert executed == ["placeholder"]
    act = next(item for item in sink.records if item["state"] == "act")
    assert act["action"]["requested_name"] == "image.azure"
    assert act["action"]["selected_name"] == "image.placeholder"
    assert act["action"]["used_fallback"] is True
    assert outcome.final_result is not None
    assert outcome.final_result.value["final_eligible"] is False


def test_paid_fallback_requires_explicit_operation_approval(trace_pair: Any) -> None:
    emitter, _sink = trace_pair
    registry = ActionRegistry()
    executed: list[str] = []

    def secondary(_inputs: Any, _context: Any) -> dict[str, bool]:
        executed.append("secondary")
        return {"ok": True}

    registry.register(
        ActionSpec(
            "remote.primary",
            lambda _inputs, _context: {"unexpected": True},
            capability="primary",
            fallbacks=("remote.secondary",),
            paid=True,
        )
    )
    registry.register(
        ActionSpec(
            "remote.secondary",
            secondary,
            paid=True,
        )
    )
    outcome = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("remote.primary"),
        capabilities={"primary": {"state": "unavailable"}},
        trace=emitter,
    ).run(run_id="run-paid-fallback")

    assert outcome.succeeded is False
    assert executed == []
    assert outcome.final_result and outcome.final_result.error
    assert outcome.final_result.error.category.value == "permission"

    approved = HarnessLoop(
        registry=registry,
        observer=observer,
        decider=one_action_decider("remote.primary"),
        capabilities={"primary": {"state": "unavailable"}},
        trace=emitter,
    ).run(
        {"paid_action_approved": True},
        run_id="run-paid-fallback-approved",
    )
    assert approved.succeeded is True
    assert executed == ["secondary"]

