"""Bounded observe -> decide -> act -> evaluate -> correct/stop loop."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from hashlib import sha256
from typing import Any

from nining.errors import (
    BugEvent,
    ErrorEvent,
    ErrorSeverity,
    HarnessError,
    classify_exception,
)
from nining.trace import TraceEmitter, canonical_json, random_id

from .models import (
    ActionContext,
    ActionResult,
    Correction,
    Decision,
    Evaluation,
    LoopOutcome,
    LoopSnapshot,
    LoopState,
    Observation,
)
from .policy import RetryPolicy
from .registry import ActionRegistry, ActionResolution, ActionSpec

Observer = Callable[[LoopSnapshot], Observation]
Decider = Callable[[LoopSnapshot], Decision]
Evaluator = Callable[[LoopSnapshot], Evaluation]
Corrector = Callable[[LoopSnapshot], Correction]


def _idempotency_key(
    run_id: str,
    operation_id: str,
    requested_action: str,
    selected_action: str,
    inputs: Mapping[str, Any],
) -> str:
    payload = canonical_json(
        {
            "run_id": run_id,
            "operation_id": operation_id,
            "requested_action": requested_action,
            "selected_action": selected_action,
            "inputs": inputs,
        }
    )
    return "idem_" + sha256(payload.encode("utf-8")).hexdigest()


def _default_evaluator(snapshot: LoopSnapshot) -> Evaluation:
    assert snapshot.result is not None
    if snapshot.result.ok:
        return Evaluation(True, "action completed successfully", False)
    error = snapshot.result.error
    return Evaluation(
        False,
        "action failed",
        bool(error and error.retryable),
        {"error_code": error.code if error else "unknown"},
    )


def _default_corrector(snapshot: LoopSnapshot) -> Correction:
    assert snapshot.decision is not None
    assert snapshot.evaluation is not None
    if not snapshot.evaluation.correctable:
        return Correction(
            False,
            "evaluation cannot be corrected automatically",
            stop_reason="non-correctable-evaluation",
        )
    return Correction(
        True,
        "retry the same logical operation under retry policy",
        action_name=snapshot.decision.action_name,
        inputs=snapshot.decision.inputs,
        operation_id=snapshot.decision.operation_id,
    )


class HarnessLoop:
    """A small deterministic controller with conservative side-effect policy."""

    def __init__(
        self,
        *,
        registry: ActionRegistry,
        observer: Observer,
        decider: Decider,
        trace: TraceEmitter,
        evaluator: Evaluator | None = None,
        corrector: Corrector | None = None,
        capabilities: Mapping[str, Any] | None = None,
        retry_policy: RetryPolicy | None = None,
        max_attempts: int = 3,
        run_id_factory: Callable[[], str] = random_id,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.registry = registry
        self.observer = observer
        self.decider = decider
        self.evaluator = evaluator or _default_evaluator
        self.corrector = corrector or _default_corrector
        self.trace = trace
        self.capabilities = dict(capabilities or {})
        self.retry_policy = retry_policy or RetryPolicy()
        self.max_attempts = max_attempts
        self.run_id_factory = run_id_factory

    def _stop(
        self,
        *,
        run_id: str,
        attempt: int,
        requirements: tuple[str, ...],
        reason: str,
        result: ActionResult | None,
        evaluation: Evaluation | None,
        succeeded: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> LoopOutcome:
        self.trace.emit(
            run_id=run_id,
            state=LoopState.STOP,
            attempt=attempt,
            requirement_ids=requirements,
            stop_reason=reason,
            result=result.to_dict() if result else None,
            evaluation=evaluation.to_dict() if evaluation else None,
            metadata=metadata,
        )
        return LoopOutcome(
            run_id,
            succeeded,
            reason,
            attempt,
            result,
            evaluation,
            requirements,
        )

    def _phase_failure(
        self,
        *,
        exc: BaseException,
        phase: str,
        run_id: str,
        attempt: int,
        requirements: tuple[str, ...],
        action_name: str | None,
        result: ActionResult | None = None,
        evaluation: Evaluation | None = None,
    ) -> LoopOutcome:
        classified = classify_exception(exc)
        occurrence_id = f"error_{run_id}_{attempt}_{phase}"
        event = ErrorEvent(
            classified,
            phase,
            action_name,
            attempt,
            False,
            occurrence_id,
        )
        self.trace.emit(
            run_id=run_id,
            state=LoopState.ERROR,
            attempt=attempt,
            requirement_ids=requirements,
            error=event.to_dict(),
        )
        if classified.category.value in {"bug", "unknown"}:
            bug = BugEvent(
                bug_id=f"bug_{run_id}_{attempt}_{phase}",
                summary=f"unexpected exception during {phase}",
                phase=phase,
                severity=ErrorSeverity.ERROR,
                reproducible=None,
                requirement_ids=requirements,
                evidence={"error_code": classified.code},
            )
            self.trace.emit(
                run_id=run_id,
                state=LoopState.BUG,
                attempt=attempt,
                requirement_ids=requirements,
                bug=bug.to_dict(),
            )
        return self._stop(
            run_id=run_id,
            attempt=attempt,
            requirements=requirements,
            reason=f"{phase}-error",
            result=result,
            evaluation=evaluation,
            succeeded=False,
            metadata={"error_code": classified.code},
        )

    def run(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        requirement_ids: Sequence[str] = (),
        run_id: str | None = None,
    ) -> LoopOutcome:
        run_id = run_id or self.run_id_factory()
        requirements = tuple(sorted(set(requirement_ids)))
        public_context = dict(context or {})
        attempt = 0
        observation: Observation | None = None
        decision: Decision | None = None
        result: ActionResult | None = None
        evaluation: Evaluation | None = None
        correction: Correction | None = None
        selected_spec: ActionSpec | None = None
        resolution: ActionResolution | None = None
        forced_decision: Decision | None = None
        retry_counts: Counter[str] = Counter()

        while True:
            base = LoopSnapshot(
                run_id,
                attempt,
                requirements,
                public_context,
                observation,
                decision,
                resolution.selected_name if resolution else None,
                result,
                evaluation,
                correction,
            )
            try:
                observation = self.observer(base)
                if not isinstance(observation, Observation):
                    raise TypeError("observer must return Observation")
            except Exception as exc:  # noqa: BLE001 - callback boundary is an error boundary
                return self._phase_failure(
                    exc=exc,
                    phase="observe",
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    action_name=decision.action_name if decision else None,
                    result=result,
                    evaluation=evaluation,
                )
            self.trace.emit(
                run_id=run_id,
                state=LoopState.OBSERVE,
                attempt=attempt,
                requirement_ids=requirements,
                observation=observation.to_dict(),
                correction=correction.to_dict() if correction else None,
            )

            snapshot = replace(base, observation=observation)
            try:
                decision = forced_decision or self.decider(snapshot)
                forced_decision = None
                if not isinstance(decision, Decision):
                    raise TypeError("decider must return Decision")
            except Exception as exc:  # noqa: BLE001 - callback boundary is an error boundary
                return self._phase_failure(
                    exc=exc,
                    phase="decide",
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    action_name=None,
                    result=result,
                    evaluation=evaluation,
                )
            self.trace.emit(
                run_id=run_id,
                state=LoopState.DECIDE,
                attempt=attempt,
                requirement_ids=requirements,
                observation=observation.to_dict(),
                decision=decision.to_dict(),
            )
            if decision.stop_reason:
                return self._stop(
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    reason=decision.stop_reason,
                    result=result,
                    evaluation=evaluation,
                    succeeded=bool(evaluation and evaluation.passed),
                )
            if attempt >= self.max_attempts:
                return self._stop(
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    reason="max-attempts-reached",
                    result=result,
                    evaluation=evaluation,
                    succeeded=False,
                )

            attempt += 1
            selected_spec = None
            resolution = None
            idempotency_key: str | None = None
            try:
                assert decision.action_name is not None
                resolution = self.registry.resolve(decision.action_name, self.capabilities)
                selected_spec = resolution.spec
                idempotency_key = _idempotency_key(
                    run_id,
                    decision.operation_id,
                    decision.action_name,
                    resolution.selected_name,
                    decision.inputs,
                )
                action_context = ActionContext(
                    run_id,
                    attempt,
                    idempotency_key,
                    requirements,
                    {
                        "requested_action": decision.action_name,
                        "used_fallback": resolution.used_fallback,
                    },
                )
                if (
                    selected_spec.paid
                    and public_context.get("paid_action_approved") is not True
                ):
                    raise PermissionError(
                        "paid action requires explicit operation approval"
                    )
                returned = selected_spec.handler(decision.inputs, action_context)
                result = (
                    returned
                    if isinstance(returned, ActionResult)
                    else ActionResult.success(dict(returned), charged=False)
                )
            except Exception as exc:  # noqa: BLE001 - action plugins are untrusted boundaries
                classified = classify_exception(exc)
                result = ActionResult.failure(classified)

            action_record = {
                "requested_name": decision.action_name,
                "selected_name": resolution.selected_name if resolution else None,
                "used_fallback": resolution.used_fallback if resolution else False,
                "resolution_reason": resolution.reason if resolution else "resolution failed",
                "considered": resolution.considered if resolution else (decision.action_name,),
                "paid": selected_spec.paid if selected_spec else None,
                "safe_to_retry": selected_spec.safe_to_retry if selected_spec else None,
                "idempotency_key": idempotency_key,
                "inputs": decision.inputs,
            }
            self.trace.emit(
                run_id=run_id,
                state=LoopState.ACT,
                attempt=attempt,
                requirement_ids=requirements,
                decision=decision.to_dict(),
                action=action_record,
                result=result.to_dict(),
            )
            if result.error is not None:
                error_event = ErrorEvent(
                    result.error,
                    "act",
                    resolution.selected_name if resolution else decision.action_name,
                    attempt,
                    True,
                    f"error_{run_id}_{attempt}_act",
                )
                self.trace.emit(
                    run_id=run_id,
                    state=LoopState.ERROR,
                    attempt=attempt,
                    requirement_ids=requirements,
                    action=action_record,
                    result=result.to_dict(),
                    error=error_event.to_dict(),
                )

            snapshot = LoopSnapshot(
                run_id,
                attempt,
                requirements,
                public_context,
                observation,
                decision,
                resolution.selected_name if resolution else None,
                result,
                None,
                None,
            )
            try:
                evaluation = self.evaluator(snapshot)
                if not isinstance(evaluation, Evaluation):
                    raise TypeError("evaluator must return Evaluation")
            except Exception as exc:  # noqa: BLE001 - callback boundary is an error boundary
                return self._phase_failure(
                    exc=exc,
                    phase="evaluate",
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    action_name=resolution.selected_name if resolution else decision.action_name,
                    result=result,
                )
            self.trace.emit(
                run_id=run_id,
                state=LoopState.EVALUATE,
                attempt=attempt,
                requirement_ids=requirements,
                action=action_record,
                result=result.to_dict(),
                evaluation=evaluation.to_dict(),
            )
            if evaluation.passed:
                return self._stop(
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    reason="completed",
                    result=result,
                    evaluation=evaluation,
                    succeeded=True,
                )
            if attempt >= self.max_attempts:
                return self._stop(
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    reason="max-attempts-reached",
                    result=result,
                    evaluation=evaluation,
                    succeeded=False,
                )

            snapshot = replace(snapshot, evaluation=evaluation)
            try:
                correction = self.corrector(snapshot)
                if not isinstance(correction, Correction):
                    raise TypeError("corrector must return Correction")
            except Exception as exc:  # noqa: BLE001 - callback boundary is an error boundary
                return self._phase_failure(
                    exc=exc,
                    phase="correct",
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    action_name=resolution.selected_name if resolution else decision.action_name,
                    result=result,
                    evaluation=evaluation,
                )

            target_name = correction.action_name or decision.action_name
            target_spec: ActionSpec | None = None
            try:
                if target_name is not None:
                    target_spec = self.registry.resolve(target_name, self.capabilities).spec
            except HarnessError:
                target_spec = None
            target_key = target_spec.name if target_spec else str(target_name)
            policy = self.retry_policy.assess(
                source=selected_spec,
                target=target_spec,
                result=result,
                evaluation=evaluation,
                correction=correction,
                prior_retries=retry_counts[target_key],
            )
            correction_record = {
                **correction.to_dict(),
                "policy_allowed": policy.allowed,
                "policy_reason": policy.reason,
            }
            self.trace.emit(
                run_id=run_id,
                state=LoopState.CORRECT,
                attempt=attempt,
                requirement_ids=requirements,
                result=result.to_dict(),
                evaluation=evaluation.to_dict(),
                correction=correction_record,
            )
            if not policy.allowed:
                return self._stop(
                    run_id=run_id,
                    attempt=attempt,
                    requirements=requirements,
                    reason=correction.stop_reason or "automatic-correction-blocked",
                    result=result,
                    evaluation=evaluation,
                    succeeded=False,
                    metadata={"policy_reason": policy.reason},
                )

            retry_counts[target_key] += 1
            forced_decision = Decision(
                action_name=target_name,
                inputs=correction.inputs if correction.inputs is not None else decision.inputs,
                rationale_summary=correction.rationale_summary,
                operation_id=correction.operation_id or decision.operation_id,
            )

