"""Application service joining continuity, providers, traces, storage, and files."""

from __future__ import annotations

import json
import os
import struct
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any

from . import __version__
from .assets import verify_local_asset
from .canonical import canonical_sha256
from .capabilities import capabilities_dict
from .checkpoints import normalize_checkpoint_state
from .config import AppConfig, ModelConfig
from .domain import Materials
from .errors import ClassifiedError, ErrorCategory
from .harness import (
    ActionRegistry,
    ActionResult,
    ActionSpec,
    Decision,
    Evaluation,
    HarnessLoop,
    Observation,
)
from .playthrough import PlaythroughManager
from .pricing import estimate_provider_cost
from .prompts import (
    build_base_media_prompt,
    build_prompt_composer_request,
    merge_composed_prompt,
)
from .providers import (
    BillingState,
    ImageInput,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResult,
    ProviderRouter,
    RequestKind,
    build_provider_router,
)
from .spec_builder import build_scene_spec
from .storage.common import StorageConflictError, StorageError, new_id
from .storage.protocol import StorageBackend
from .trace import JsonlTraceSink, TraceEmitter, load_jsonl, trace_digest


@dataclass(frozen=True)
class GenerationOutcome:
    status: str
    playthrough_id: str
    branch_id: str
    run_id: str
    trigger_id: str
    kind: str
    spec_sha256: str
    spec_path: str
    media_paths: tuple[str, ...]
    asset_ids: tuple[str, ...]
    provider: Mapping[str, Any] | None
    prompt_provider: Mapping[str, Any] | None
    trace_path: str
    final_eligible: bool
    human_review_required: bool
    degraded_reasons: tuple[str, ...]
    safe_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_config_path(config: AppConfig, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config.source_path.parent / path
    return path.resolve()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _classified_provider_error(result: ProviderResult) -> ClassifiedError:
    assert result.error is not None
    kind = ProviderErrorKind(result.error.kind)
    categories = {
        ProviderErrorKind.AVAILABILITY: ErrorCategory.CAPABILITY,
        ProviderErrorKind.DEPLOYMENT: ErrorCategory.CONFIGURATION,
        ProviderErrorKind.RATE_LIMIT: ErrorCategory.RATE_LIMIT,
        ProviderErrorKind.TRANSIENT: ErrorCategory.TRANSIENT,
        ProviderErrorKind.TIMEOUT: ErrorCategory.TIMEOUT,
        ProviderErrorKind.AUTHENTICATION: ErrorCategory.AUTHENTICATION,
        ProviderErrorKind.PERMISSION: ErrorCategory.PERMISSION,
        ProviderErrorKind.POLICY: ErrorCategory.SAFETY,
        ProviderErrorKind.VALIDATION: ErrorCategory.VALIDATION,
        ProviderErrorKind.MALFORMED_RESPONSE: ErrorCategory.VALIDATION,
        ProviderErrorKind.NETWORK_DISABLED: ErrorCategory.CAPABILITY,
        ProviderErrorKind.UNKNOWN: ErrorCategory.UNKNOWN,
    }
    return ClassifiedError(
        categories[kind],
        result.error.code,
        result.error.message,
        result.error.retryable,
        result.error.retry_after_seconds,
        result.error.details,
    )


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    return None, None


def _image_format(data: bytes, requested: object) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    normalized = str(requested or "bin").lower().replace("jpeg", "jpg")
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(
        normalized, "application/octet-stream"
    )
    return normalized if normalized in {"png", "jpg", "webp"} else "bin", mime


class GenerationService:
    """Execute one auditable logical media generation operation."""

    def __init__(
        self,
        *,
        config: AppConfig,
        materials: Materials,
        storage: StorageBackend,
        output_root: str | Path | None = None,
        trace_root: str | Path | None = None,
        call_log_path: str | Path | None = None,
        router: ProviderRouter | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.materials = materials
        self.storage = storage
        self.manager = PlaythroughManager(storage)
        self.output_root = (
            Path(output_root).expanduser().resolve()
            if output_root is not None
            else resolve_config_path(config, config.runtime.output_root)
        )
        self.trace_root = (
            Path(trace_root).expanduser().resolve()
            if trace_root is not None
            else resolve_config_path(config, config.logging.directory) / "runs"
        )
        self.environ = dict(os.environ if environ is None else environ)
        self.router = router or build_provider_router(config, environ=self.environ)
        self.capabilities = capabilities_dict(config, self.environ)
        self._mutation_lock = Lock()
        self._call_log_lock = Lock()
        self.call_log_path = (
            Path(call_log_path).expanduser().resolve()
            if call_log_path is not None
            else resolve_config_path(config, config.logging.directory)
            / "provider_calls.jsonl"
        )

    def ensure_playthrough(
        self, playthrough_id: str | None = None, *, title: str | None = None
    ) -> str:
        if playthrough_id is not None:
            record = self.storage.get_playthrough(playthrough_id)
            if record["materials_manifest_sha256"] != self.materials.manifest["sha256"]:
                raise ValueError("playthrough belongs to different source materials")
            return playthrough_id
        matching = [
            item
            for item in self.storage.list_playthroughs()
            if item["materials_manifest_sha256"] == self.materials.manifest["sha256"]
            and item["status"] == "active"
        ]
        if matching:
            return str(matching[-1]["playthrough_id"])
        display_name = self.materials.persona.get("nickname") or self.materials.persona.get(
            "name"
        )
        started = self.manager.start(
            title=title or f"{display_name or 'Character'} continuity",
            materials_manifest_sha256=str(self.materials.manifest["sha256"]),
            metadata={"materials_root": self.materials.root_name},
        )
        return str(started["playthrough"]["playthrough_id"])

    def _safe_output_path(self, relative_path: str) -> Path:
        path = (self.output_root / relative_path).resolve()
        try:
            path.relative_to(self.output_root)
        except ValueError as exc:
            raise ValueError("output path escaped output root") from exc
        return path

    def _accepted_image_inputs(self, playthrough_id: str) -> tuple[ImageInput, ...]:
        head = self.storage.get_head(playthrough_id)
        checkpoint_id = head.get("checkpoint_id")
        if not isinstance(checkpoint_id, str):
            return ()
        checkpoint = self.storage.get_checkpoint(playthrough_id, checkpoint_id)
        raw_state = checkpoint.get("state")
        if not isinstance(raw_state, Mapping):
            return ()
        state = normalize_checkpoint_state(raw_state)
        accepted_ids = set(state["accepted_assets"])
        latest_decisions: dict[str, Mapping[str, Any]] = {}
        for decision in self.storage.list_decisions(playthrough_id):
            if decision.get("subject_kind") == "asset":
                latest_decisions[str(decision.get("subject_id"))] = decision
        inputs: list[ImageInput] = []
        for asset in self.storage.list_assets(playthrough_id):
            asset_id = str(asset.get("asset_id"))
            last_decision = latest_decisions.get(asset_id)
            if (
                asset_id not in accepted_ids
                or asset.get("media_type") != "image"
                or asset.get("role") != "reference"
                or asset.get("final_eligible") is not True
                or last_decision is None
                or last_decision.get("action") != "accept"
                or last_decision.get("decided_by") != "human"
            ):
                continue
            verified = verify_local_asset(self.output_root, asset)
            if not verified.ok or verified.path is None:
                continue
            inputs.append(
                ImageInput(
                    verified.path.read_bytes(),
                    filename=verified.path.name,
                    content_type=verified.content_type or "image/png",
                )
            )
            if len(inputs) >= 4:
                break
        return tuple(inputs)

    def _write_bytes(self, relative_path: str, data: bytes) -> Path:
        path = self._safe_output_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp-{new_id('file')}")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def _write_json(self, relative_path: str, payload: Mapping[str, Any]) -> Path:
        data = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return self._write_bytes(relative_path, data)

    @staticmethod
    def _provider_mode(provider: str) -> str:
        if provider == "azure_openai":
            return "azure"
        if provider == "mock":
            return "mock"
        return "local"

    def _record_provider_result(
        self,
        *,
        storage_run_id: str,
        story_event_id: str,
        trigger_id: str | None,
        purpose: str,
        request: ProviderRequest,
        result: ProviderResult,
        started_at_utc: str,
        finished_at_utc: str,
        latency_ms: int,
    ) -> list[dict[str, Any]]:
        attempts = result.attempts or ()
        records: list[dict[str, Any]] = []
        for index, attempt in enumerate(attempts, start=1):
            is_final_attempt = index == len(attempts)
            error = attempt.error
            estimate = estimate_provider_cost(
                provider=attempt.provider,
                model=attempt.model,
                kind=request.kind,
                usage=result.usage if is_final_attempt else {},
                billing_state=attempt.billing_state,
            )
            records.append(
                self.storage.record_provider_call(
                    run_id=storage_run_id,
                    trigger_id=trigger_id,
                    story_event_id=story_event_id,
                    purpose=f"{purpose} attempt {index}",
                    provider=attempt.provider,
                    model=attempt.model,
                    mode=self._provider_mode(attempt.provider),
                    endpoint_alias=attempt.provider.upper(),
                    request_sha256=request.request_sha256,
                    prompt_sha256=request.prompt_sha256,
                    parameters={
                        "request": request.to_audit_dict(),
                        "attempt_index": index,
                        "seed": request.parameters.get("seed"),
                        "seed_supported": "seed" in request.parameters,
                        "cost_basis": estimate.basis,
                    },
                    started_at_utc=started_at_utc,
                    finished_at_utc=finished_at_utc,
                    latency_ms=latency_ms if is_final_attempt else None,
                    usage=result.usage if is_final_attempt else {},
                    cost_amount_micros=estimate.amount_micros,
                    cost_currency=estimate.currency,
                    billing_state=estimate.billing_state.value,
                    status="success" if attempt.success else "failed",
                    error_code=error.code if error is not None else None,
                    safe_error=error.message if error is not None else None,
                )
            )
        if not records:
            error = result.error
            estimate = estimate_provider_cost(
                provider=result.provider,
                model=result.model,
                kind=request.kind,
                usage=result.usage,
                billing_state=result.billing_state,
            )
            records.append(
                self.storage.record_provider_call(
                    run_id=storage_run_id,
                    trigger_id=trigger_id,
                    story_event_id=story_event_id,
                    purpose=purpose,
                    provider=result.provider,
                    model=result.model,
                    mode=self._provider_mode(result.provider),
                    endpoint_alias=result.provider.upper(),
                    request_sha256=request.request_sha256,
                    prompt_sha256=request.prompt_sha256,
                    parameters={
                        "request": request.to_audit_dict(),
                        "seed": request.parameters.get("seed"),
                        "seed_supported": "seed" in request.parameters,
                        "cost_basis": estimate.basis,
                    },
                    started_at_utc=started_at_utc,
                    finished_at_utc=finished_at_utc,
                    latency_ms=latency_ms,
                    usage=result.usage,
                    cost_amount_micros=estimate.amount_micros,
                    cost_currency=estimate.currency,
                    billing_state=estimate.billing_state.value,
                    status="success" if result.ok else "failed",
                    error_code=error.code if error is not None else None,
                    safe_error=error.message if error is not None else None,
                )
            )
        self.call_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._call_log_lock, self.call_log_path.open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            for record in records:
                handle.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
        return records

    def _execute_provider_phase(
        self,
        *,
        storage_run_id: str,
        harness_run_id: str,
        trigger_id: str | None,
        phase: str,
        request: ProviderRequest,
        trace: TraceEmitter,
        story_event_id: str,
    ) -> tuple[ProviderResult | None, list[dict[str, Any]], bool]:
        holder: dict[str, Any] = {}
        registry = ActionRegistry()
        capability_name = RequestKind(request.kind).value

        def execute_provider(
            _inputs: Mapping[str, Any], _context: Any
        ) -> ActionResult:
            started = _utc_now()
            monotonic_started = time.monotonic_ns()
            result = self.router.execute(request)
            finished = _utc_now()
            latency_ms = max(0, (time.monotonic_ns() - monotonic_started) // 1_000_000)
            calls = self._record_provider_result(
                storage_run_id=storage_run_id,
                story_event_id=story_event_id,
                trigger_id=trigger_id,
                purpose=phase,
                request=request,
                result=result,
                started_at_utc=started,
                finished_at_utc=finished,
                latency_ms=latency_ms,
            )
            holder.update(result=result, calls=calls)
            if result.ok:
                return ActionResult.success(
                    {
                        "provider_result": result.to_audit_dict(),
                        "provider_call_ids": [item["call_id"] for item in calls],
                        "story_event_id": story_event_id,
                    },
                    charged=result.billing_state not in {BillingState.ZERO},
                )
            return ActionResult.failure(
                _classified_provider_error(result),
                execution_status="completed",
                charged=result.billing_state not in {BillingState.ZERO},
                value={"provider_call_ids": [item["call_id"] for item in calls]},
            )

        action_name = f"provider.{capability_name}"
        registry.register(
            ActionSpec(
                name=action_name,
                handler=execute_provider,
                description=f"execute one bounded {capability_name} provider chain",
                capability=capability_name,
                accepted_capability_states=("available", "degraded"),
                paid=self.config.runtime.allow_network,
                safe_to_retry=False,
            )
        )
        loop = HarnessLoop(
            registry=registry,
            observer=lambda _snapshot: Observation(
                f"{phase} is ready for {trigger_id}",
                {
                    "trigger_id": trigger_id,
                    "kind": capability_name,
                    "request_sha256": request.request_sha256,
                    "capability": self.capabilities.get(capability_name, {}),
                },
            ),
            decider=lambda _snapshot: Decision(
                action_name=action_name,
                inputs={"request_sha256": request.request_sha256},
                rationale_summary="execute the configured bounded provider chain",
                operation_id=phase,
            ),
            evaluator=lambda snapshot: Evaluation(
                bool(snapshot.result and snapshot.result.ok),
                "provider phase completed"
                if snapshot.result and snapshot.result.ok
                else "provider phase failed",
                False,
                {"phase": phase},
            ),
            trace=trace,
            capabilities=self.capabilities,
            max_attempts=1,
        )
        outcome = loop.run(
            {
                "storage_run_id": storage_run_id,
                "phase": phase,
                "paid_action_approved": self.config.runtime.allow_network,
            },
            requirement_ids=(
                "R1_MEDIA",
                "R4_CALL_LOG",
                f"TRIGGER_{trigger_id}" if trigger_id is not None else "REFERENCE_VISUAL",
            ),
            run_id=harness_run_id,
        )
        return holder.get("result"), holder.get("calls", []), outcome.succeeded

    @staticmethod
    def _model_parameters(model: ModelConfig, *, kind: str, spec_sha256: str) -> dict[str, Any]:
        allowed = {
            "text": {"temperature", "max_tokens", "max_completion_tokens", "top_p", "seed"},
            "image": {
                "n",
                "size",
                "quality",
                "output_format",
                "output_compression",
                "background",
                "moderation",
            },
            "voice": {"output_format", "rate", "speed", "voice", "preset"},
        }[kind]
        parameters = {key: value for key, value in model.extras.items() if key in allowed}
        if kind == "text":
            parameters.setdefault("max_completion_tokens", 1200)
            uses_responses = (
                model.provider == "azure_openai"
                and model.extras.get("api_style") == "responses"
            )
            if not uses_responses:
                parameters.setdefault("temperature", 0.2)
                parameters.setdefault("seed", int(spec_sha256[:8], 16))
        elif kind == "image":
            parameters.setdefault("n", 1)
            parameters.setdefault("size", "1024x1024")
            parameters.setdefault("output_format", "png")
        return parameters

    def generate(
        self,
        trigger_id: str,
        *,
        playthrough_id: str | None = None,
        expected_checkpoint_id: str | None = None,
    ) -> GenerationOutcome:
        """Serialize mutation and optionally reject a stale browser action."""

        with self._mutation_lock:
            resolved_playthrough = self.ensure_playthrough(playthrough_id)
            if expected_checkpoint_id is not None:
                current = self.storage.get_head(resolved_playthrough)
                if current.get("checkpoint_id") != expected_checkpoint_id:
                    raise StorageConflictError(
                        "playthrough changed; refresh before generating again"
                    )
            return self._generate_locked(
                trigger_id, playthrough_id=resolved_playthrough
            )

    def generate_background(
        self,
        trigger_id: str,
        *,
        playthrough_id: str | None = None,
        expected_checkpoint_id: str | None = None,
    ) -> GenerationOutcome:
        """Generate a chapter image even when its primary message is voice.

        The resulting image remains an ordinary review-gated candidate attached
        to the chapter.  It never replaces the trigger's primary media kind.
        """

        with self._mutation_lock:
            resolved_playthrough = self.ensure_playthrough(playthrough_id)
            if expected_checkpoint_id is not None:
                current = self.storage.get_head(resolved_playthrough)
                if current.get("checkpoint_id") != expected_checkpoint_id:
                    raise StorageConflictError(
                        "playthrough changed; refresh before generating again"
                    )
            return self._generate_locked(
                trigger_id,
                playthrough_id=resolved_playthrough,
                force_image=True,
            )

    def generate_visual_reference(
        self, *, playthrough_id: str | None = None
    ) -> GenerationOutcome:
        """Generate one text-only fictional identity reference candidate."""

        with self._mutation_lock:
            resolved = self.ensure_playthrough(playthrough_id)
            unresolved = [
                item
                for item in self.storage.list_runs(resolved)
                if item.get("status") == "running"
            ]
            if unresolved:
                raise StorageConflictError(
                    "an earlier generation run is unresolved; reconcile it first"
                )
            return self._generate_visual_reference_locked(resolved)

    def _generate_visual_reference_locked(
        self, playthrough_id: str
    ) -> GenerationOutcome:
        head = self.storage.get_head(playthrough_id)
        branch_id = str(head["branch_id"])
        run_id = new_id("run")
        trace_relative = f"runs/{run_id}.jsonl"
        trace_path = self.trace_root / f"{run_id}.jsonl"
        trace = TraceEmitter(JsonlTraceSink(trace_path))
        identity = {
            key: self.materials.persona.get(key)
            for key in (
                "id",
                "name",
                "nickname",
                "gender",
                "birth_date",
                "looks",
                "voice",
            )
            if key in self.materials.persona
        }
        spec: dict[str, Any] = {
            "schema_version": "1.0",
            "builder": "deterministic-reference",
            "trigger_id": "REFERENCE_VISUAL",
            "trigger_at_utc": None,
            "kind": "image",
            "intent": (
                "Fictional adult identity anchor: natural phone portrait, neutral daylight, "
                "plain background, round face, monolid eyes, left eyebrow-tail mole, and only "
                "the character's left-ear silver crescent earring clearly preserved."
            ),
            "why": "Establish a text-designed identity reference before scene generation.",
            "say": None,
            "identity": identity,
            "state": {"relationship": None, "mood": "natural and friendly", "today_plan": [], "promises": [], "audience_knows": []},
            "visible_message_ids": [],
            "trigger_message_id": None,
            "future_locked_message_ids": [],
            "conflicts": [],
            "fulfilled_promises": [],
            "evidence": [
                {
                    "evidence_id": "persona:identity",
                    "source": {"file": "persona.json", "record_id": None, "line": None},
                    "visibility": "reference_only",
                }
            ],
            "generation": {
                "provider": None,
                "model": None,
                "final_eligible": False,
                "prompt_inputs": {"identity": identity},
            },
            "qa_checks": [
                {"check_id": "continuity.identity", "required": True},
                {"check_id": "safety.no_real_person", "required": True},
                {"check_id": "anatomy.natural", "required": True},
            ],
            "human_review_required": True,
        }
        spec["spec_sha256"] = canonical_sha256(spec)
        self.storage.begin_run(
            run_id=run_id,
            playthrough_id=playthrough_id,
            branch_id=branch_id,
            mode=self.config.runtime.mode,
            requested_storage=self.config.storage.backend,
            effective_storage=self.storage.backend_name,
            fallback_reason=self.storage.fallback_reason,
            materials_manifest_sha256=str(self.materials.manifest["sha256"]),
            config_sha256=self.config.fingerprint(self.environ),
            code_version=__version__,
            capabilities=self.capabilities,
        )
        reference_state: Mapping[str, Any] = {}
        try:
            current_checkpoint = head.get("checkpoint_id")
            state: Mapping[str, Any] = {}
            if isinstance(current_checkpoint, str):
                record = self.storage.get_checkpoint(
                    playthrough_id, current_checkpoint
                )
                if isinstance(record.get("state"), Mapping):
                    state = record["state"]
            reference_state = state
            requested = self.manager.append_event(
                playthrough_id,
                event_type="visual_reference_requested",
                actor="user",
                payload={"run_id": run_id, "spec_sha256": spec["spec_sha256"]},
                run_id=run_id,
                checkpoint_kind="pre_generation",
                checkpoint_label="Before visual identity reference",
                checkpoint_state=normalize_checkpoint_state(state),
            )
            event_id = str(requested["event"]["story_event_id"])
            prefix = f"runs/{run_id}/reference-visual"
            spec_relative = f"{prefix}/reference_spec.json"
            spec_path = self._write_json(spec_relative, spec)
            spec_asset = self.storage.register_asset(
                run_id=run_id,
                role="draft",
                media_type="json",
                relative_path=spec_relative,
                mime_type="application/json",
                size_bytes=spec_path.stat().st_size,
                sha256=sha256(spec_path.read_bytes()).hexdigest(),
                provenance="deterministic",
                final_eligible=False,
            )
            base_prompt = build_base_media_prompt(spec)
            composer_request = ProviderRequest(
                kind="text",
                prompt=build_prompt_composer_request(spec, base_prompt),
                parameters=self._model_parameters(
                    self.config.text,
                    kind="text",
                    spec_sha256=str(spec["spec_sha256"]),
                ),
                final_eligible=False,
            )
            prompt_result, _, prompt_ok = self._execute_provider_phase(
                storage_run_id=run_id,
                harness_run_id=f"{run_id}.prompt",
                trigger_id=None,
                phase="compose_reference_prompt",
                request=composer_request,
                trace=trace,
                story_event_id=event_id,
            )
            final_prompt = merge_composed_prompt(
                base_prompt=base_prompt,
                composed_prompt=(
                    prompt_result.text
                    if prompt_result is not None and prompt_result.ok
                    else None
                ),
                provider=(
                    prompt_result.provider if prompt_result is not None else None
                ),
                exact_script=None,
            )
            image_request = ProviderRequest(
                kind="image",
                prompt=final_prompt,
                parameters=self._model_parameters(
                    self.config.image,
                    kind="image",
                    spec_sha256=str(spec["spec_sha256"]),
                ),
                final_eligible=True,
            )
            image_result, calls, image_ok = self._execute_provider_phase(
                storage_run_id=run_id,
                harness_run_id=f"{run_id}.media",
                trigger_id=None,
                phase="generate_visual_reference",
                request=image_request,
                trace=trace,
                story_event_id=event_id,
            )
            if not image_ok or image_result is None or not image_result.ok:
                raise RuntimeError(
                    image_result.error.message
                    if image_result is not None and image_result.error is not None
                    else "visual reference provider phase failed"
                )
            final_call_id = str(calls[-1]["call_id"]) if calls else None
            asset_ids = [str(spec_asset["asset_id"])]
            media_paths: list[str] = []
            for index, image in enumerate(image_result.images, start=1):
                extension, mime_type = _image_format(
                    image, image_request.parameters.get("output_format")
                )
                role = (
                    "placeholder"
                    if image_result.provider == "placeholder"
                    else "reference"
                )
                relative = f"{prefix}/{role}-{index:02d}.{extension}"
                self._write_bytes(relative, image)
                width, height = _png_dimensions(image)
                asset = self.storage.register_asset(
                    run_id=run_id,
                    provider_call_id=final_call_id,
                    role=role,
                    media_type="image",
                    relative_path=relative,
                    mime_type=mime_type,
                    size_bytes=len(image),
                    width_px=width,
                    height_px=height,
                    sha256=sha256(image).hexdigest(),
                    provenance=(
                        "azure"
                        if image_result.provider == "azure_openai"
                        else "deterministic"
                    ),
                    final_eligible=image_result.final_eligible,
                )
                asset_ids.append(str(asset["asset_id"]))
                media_paths.append(relative)
                self.storage.record_decision(
                    run_id=run_id,
                    subject_kind="asset",
                    subject_id=str(asset["asset_id"]),
                    action=(
                        "needs_review" if image_result.final_eligible else "block"
                    ),
                    reason=(
                        "visual identity reference requires explicit human acceptance"
                        if image_result.final_eligible
                        else "placeholder cannot be an identity reference"
                    ),
                    decided_by="rule",
                )
            trace_sha256 = trace_digest(load_jsonl(trace_path))
            final_state = normalize_checkpoint_state(state)
            final_state["extra"] = {
                **final_state["extra"],
                "reference_candidate_asset_ids": asset_ids[1:],
                "trace_alias": trace_relative,
                "trace_sha256": trace_sha256,
            }
            self.manager.append_event(
                playthrough_id,
                event_type="visual_reference_generated",
                actor="system",
                payload={
                    "run_id": run_id,
                    "asset_ids": asset_ids,
                    "trace_alias": trace_relative,
                    "trace_sha256": trace_sha256,
                },
                run_id=run_id,
                checkpoint_kind="post_generation",
                checkpoint_label="Visual identity reference candidate",
                checkpoint_state=final_state,
            )
            self.storage.finish_run(run_id, status="completed")
            degraded = []
            if not prompt_ok:
                degraded.append("text prompt assistance failed")
            if not image_result.final_eligible:
                degraded.append(
                    f"{image_result.provider} output is not final eligible"
                )
            return GenerationOutcome(
                status="completed",
                playthrough_id=playthrough_id,
                branch_id=branch_id,
                run_id=run_id,
                trigger_id="REFERENCE_VISUAL",
                kind="image",
                spec_sha256=str(spec["spec_sha256"]),
                spec_path=spec_relative,
                media_paths=tuple(media_paths),
                asset_ids=tuple(asset_ids),
                provider=image_result.to_audit_dict(),
                prompt_provider=(
                    prompt_result.to_audit_dict()
                    if prompt_result is not None
                    else None
                ),
                trace_path=trace_relative,
                final_eligible=image_result.final_eligible,
                human_review_required=True,
                degraded_reasons=tuple(degraded),
            )
        except Exception as exc:
            safe_error = f"{type(exc).__name__}: {exc}"
            try:
                failure_state = normalize_checkpoint_state(reference_state)
                failure_trace_sha256 = (
                    trace_digest(load_jsonl(trace_path))
                    if trace_path.is_file()
                    else None
                )
                failure_state["extra"] = {
                    **failure_state["extra"],
                    "run_id": run_id,
                    "trace_alias": trace_relative,
                    "trace_sha256": failure_trace_sha256,
                    "safe_error": safe_error,
                }
                self.storage.record_decision(
                    run_id=run_id,
                    subject_kind="run",
                    subject_id=run_id,
                    action="block",
                    reason="visual reference generation failed before a reviewable image was produced",
                    decided_by="rule",
                )
                self.manager.append_event(
                    playthrough_id,
                    event_type="visual_reference_failed",
                    actor="system",
                    payload={
                        "run_id": run_id,
                        "safe_error": safe_error,
                        "trace_alias": trace_relative,
                        "trace_sha256": failure_trace_sha256,
                    },
                    run_id=run_id,
                    checkpoint_kind="post_generation",
                    checkpoint_label="Visual identity reference failed",
                    checkpoint_state=failure_state,
                )
            except (StorageError, KeyError, TypeError, ValueError):
                pass
            try:
                self.storage.finish_run(
                    run_id,
                    status="failed",
                    safe_error=safe_error,
                )
            except StorageError:
                pass
            raise

    def _generate_locked(
        self,
        trigger_id: str,
        *,
        playthrough_id: str | None = None,
        force_image: bool = False,
    ) -> GenerationOutcome:
        playthrough_id = self.ensure_playthrough(playthrough_id)
        unresolved = [
            item
            for item in self.storage.list_runs(playthrough_id)
            if item.get("status") == "running"
        ]
        if unresolved:
            raise StorageConflictError(
                "an earlier generation run is unresolved; reconcile it before dispatching another provider call"
            )
        spec = build_scene_spec(self.materials, trigger_id).to_dict()
        if force_image:
            original_kind = str(spec.get("kind", ""))
            original_intent = str(spec.get("intent", "")).strip()
            spec["kind"] = "image"
            spec["say"] = None
            spec["intent"] = (
                "Chapter background image for the web story. Show the same "
                "fictional adult character and the time-appropriate setting for: "
                f"{original_intent}. This complements the primary {original_kind} "
                "message and must not add visible text or future facts."
            )
            generation = spec.get("generation")
            generation_data = dict(generation) if isinstance(generation, Mapping) else {}
            generation_data["background_for_primary_kind"] = original_kind
            spec["generation"] = generation_data
            spec.pop("spec_sha256", None)
            spec["spec_sha256"] = canonical_sha256(spec)
        kind = str(spec["kind"])
        if kind not in {"image", "voice"}:
            raise ValueError("only image and voice triggers can generate media")
        head = self.storage.get_head(playthrough_id)
        branch_id = str(head["branch_id"])
        run_id = new_id("run")
        trace_relative = f"runs/{run_id}.jsonl"
        trace_path = self.trace_root / f"{run_id}.jsonl"
        trace = TraceEmitter(JsonlTraceSink(trace_path))
        self.storage.begin_run(
            run_id=run_id,
            playthrough_id=playthrough_id,
            branch_id=branch_id,
            mode=self.config.runtime.mode,
            requested_storage=self.config.storage.backend,
            effective_storage=self.storage.backend_name,
            fallback_reason=self.storage.fallback_reason,
            materials_manifest_sha256=str(self.materials.manifest["sha256"]),
            config_sha256=self.config.fingerprint(self.environ),
            code_version=__version__,
            capabilities=self.capabilities,
        )
        degraded_reasons: list[str] = []
        media_paths: list[str] = []
        asset_ids: list[str] = []
        prompt_result: ProviderResult | None = None
        media_result: ProviderResult | None = None
        terminal_status = "failed"
        terminal_error: str | None = None
        failure_state: Mapping[str, Any] | None = None
        try:
            raw_state = spec.get("state")
            state_data: Mapping[str, Any] = (
                raw_state if isinstance(raw_state, Mapping) else {}
            )
            failure_state = state_data
            current_checkpoint_state: Mapping[str, Any] = {}
            current_checkpoint_id = head.get("checkpoint_id")
            if isinstance(current_checkpoint_id, str):
                current_checkpoint = self.storage.get_checkpoint(
                    playthrough_id, current_checkpoint_id
                )
                raw_checkpoint = current_checkpoint.get("state")
                if isinstance(raw_checkpoint, Mapping):
                    current_checkpoint_state = raw_checkpoint
            pre_generation_state = normalize_checkpoint_state(
                current_checkpoint_state
            )
            pre_generation_state["cursor"] = {
                "trigger_id": trigger_id,
                "phase": "pre_generation",
            }
            pre_generation_state["visible_facts"] = list(
                spec.get("evidence", [])
            )
            pre_generation_state["promises"] = list(
                state_data.get("promises", [])
            )
            pre_generation_state["extra"] = {
                **pre_generation_state["extra"],
                "spec_sha256": spec["spec_sha256"],
            }
            generation_event = self.manager.append_event(
                playthrough_id,
                event_type="generation_requested",
                actor="user",
                payload={
                    "trigger_id": trigger_id,
                    "kind": kind,
                    "spec_sha256": spec["spec_sha256"],
                },
                run_id=run_id,
                trigger_id=trigger_id,
                checkpoint_kind="pre_generation",
                checkpoint_label=f"{trigger_id} before generation",
                checkpoint_state=pre_generation_state,
            )
            event_id = str(generation_event["event"]["story_event_id"])
            run_prefix = f"runs/{run_id}/{trigger_id}"
            spec_relative = f"{run_prefix}/scene_spec.json"
            spec_path = self._write_json(spec_relative, spec)
            spec_asset = self.storage.register_asset(
                run_id=run_id,
                trigger_id=trigger_id,
                role="draft",
                media_type="json",
                relative_path=spec_relative,
                mime_type="application/json",
                size_bytes=spec_path.stat().st_size,
                sha256=sha256(spec_path.read_bytes()).hexdigest(),
                provenance="deterministic",
                final_eligible=False,
            )
            asset_ids.append(str(spec_asset["asset_id"]))

            base_prompt = build_base_media_prompt(spec)
            composer_request = ProviderRequest(
                kind=RequestKind.TEXT,
                prompt=build_prompt_composer_request(spec, base_prompt),
                parameters=self._model_parameters(
                    self.config.text, kind="text", spec_sha256=str(spec["spec_sha256"])
                ),
                final_eligible=False,
            )
            prompt_result, _, prompt_ok = self._execute_provider_phase(
                storage_run_id=run_id,
                harness_run_id=f"{run_id}.prompt",
                trigger_id=trigger_id,
                phase="compose_media_prompt",
                request=composer_request,
                trace=trace,
                story_event_id=event_id,
            )
            if not prompt_ok:
                degraded_reasons.append("text prompt assistance failed; deterministic brief used")
            composed = prompt_result.text if prompt_result is not None and prompt_result.ok else None
            final_prompt = merge_composed_prompt(
                base_prompt=base_prompt,
                composed_prompt=composed,
                provider=prompt_result.provider if prompt_result is not None else None,
                exact_script=str(spec["say"]) if spec.get("say") is not None else None,
            )

            model_config = self.config.image if kind == "image" else self.config.voice
            media_parameters = self._model_parameters(
                model_config, kind=kind, spec_sha256=str(spec["spec_sha256"])
            )
            if kind == "voice":
                media_parameters["script"] = str(spec.get("say") or "")
            reference_inputs = (
                self._accepted_image_inputs(playthrough_id)
                if kind == "image"
                else ()
            )
            if reference_inputs:
                final_prompt += (
                    "\n\nHUMAN-ACCEPTED IDENTITY REFERENCES: Preserve the same fictional "
                    "person's stable facial identity markers. Apply only the age, hair, "
                    "clothing, pose, and scene changes required by this trigger."
                )
            media_request = ProviderRequest(
                kind=RequestKind(kind),
                prompt=final_prompt,
                operation="edit" if reference_inputs else "generate",
                parameters=media_parameters,
                image_inputs=reference_inputs,
                final_eligible=True,
            )
            media_result, media_calls, media_ok = self._execute_provider_phase(
                storage_run_id=run_id,
                harness_run_id=f"{run_id}.media",
                trigger_id=trigger_id,
                phase=f"generate_{kind}",
                request=media_request,
                trace=trace,
                story_event_id=event_id,
            )
            if not media_ok or media_result is None or not media_result.ok:
                terminal_error = (
                    media_result.error.message
                    if media_result is not None and media_result.error is not None
                    else "media provider phase failed"
                )
                raise RuntimeError(terminal_error)
            final_call_id = str(media_calls[-1]["call_id"]) if media_calls else None
            if kind == "image":
                requested_format = media_request.parameters.get("output_format")
                for index, image in enumerate(media_result.images, start=1):
                    extension, mime_type = _image_format(image, requested_format)
                    role = "placeholder" if media_result.provider == "placeholder" else "candidate"
                    relative = f"{run_prefix}/{role}-{index:02d}.{extension}"
                    path = self._write_bytes(relative, image)
                    width, height = _png_dimensions(image)
                    asset = self.storage.register_asset(
                        run_id=run_id,
                        trigger_id=trigger_id,
                        provider_call_id=final_call_id,
                        role=role,
                        media_type="image",
                        relative_path=relative,
                        mime_type=mime_type,
                        size_bytes=len(image),
                        width_px=width,
                        height_px=height,
                        sha256=sha256(image).hexdigest(),
                        provenance="azure"
                        if media_result.provider == "azure_openai"
                        else "deterministic",
                        final_eligible=media_result.final_eligible,
                    )
                    media_paths.append(relative)
                    asset_ids.append(str(asset["asset_id"]))
            elif media_result.audio is not None:
                extension = str(media_result.metadata.get("extension", "aiff"))
                mime_type = str(
                    media_result.metadata.get("mime_type", "audio/aiff")
                )
                relative = f"{run_prefix}/candidate-01.{extension}"
                self._write_bytes(relative, media_result.audio)
                duration_value = media_result.metadata.get("duration_ms")
                duration_ms = (
                    duration_value if isinstance(duration_value, int) else None
                )
                asset = self.storage.register_asset(
                    run_id=run_id,
                    trigger_id=trigger_id,
                    provider_call_id=final_call_id,
                    role="candidate",
                    media_type="audio",
                    relative_path=relative,
                    mime_type=mime_type,
                    size_bytes=len(media_result.audio),
                    duration_ms=duration_ms,
                    sha256=sha256(media_result.audio).hexdigest(),
                    provenance="local",
                    final_eligible=media_result.final_eligible,
                )
                media_paths.append(relative)
                asset_ids.append(str(asset["asset_id"]))
            else:
                voice_payload = {
                    "schema_version": "1.0",
                    "trigger_id": trigger_id,
                    "exact_script": spec.get("say"),
                    "provider_result": media_result.to_audit_dict(),
                    "voice_spec": media_result.voice_spec,
                    "audio_generated": False,
                    "final_eligible": False,
                }
                relative = f"{run_prefix}/voice-spec.json"
                path = self._write_json(relative, voice_payload)
                asset = self.storage.register_asset(
                    run_id=run_id,
                    trigger_id=trigger_id,
                    provider_call_id=final_call_id,
                    role="draft",
                    media_type="json",
                    relative_path=relative,
                    mime_type="application/json",
                    size_bytes=path.stat().st_size,
                    sha256=sha256(path.read_bytes()).hexdigest(),
                    provenance="deterministic",
                    final_eligible=False,
                )
                media_paths.append(relative)
                asset_ids.append(str(asset["asset_id"]))

            if not media_result.final_eligible:
                degraded_reasons.append(
                    f"{media_result.provider} output is not final eligible"
                )
            decision_action = "needs_review" if media_result.final_eligible else "block"
            self.storage.record_decision(
                run_id=run_id,
                trigger_id=trigger_id,
                subject_kind="run",
                subject_id=run_id,
                action=decision_action,
                reason=(
                    "candidate requires explicit human acceptance"
                    if media_result.final_eligible
                    else "degraded output cannot be promoted to final"
                ),
                decided_by="rule",
            )
            trace_sha256 = trace_digest(load_jsonl(trace_path))
            post_generation_state = normalize_checkpoint_state(
                pre_generation_state
            )
            post_generation_state["cursor"] = {
                "trigger_id": trigger_id,
                "phase": "post_generation",
            }
            post_generation_state["extra"] = {
                **post_generation_state["extra"],
                "run_id": run_id,
                "candidate_asset_ids": asset_ids,
            }
            self.manager.append_event(
                playthrough_id,
                event_type="generation_completed",
                actor="system",
                payload={
                    "trigger_id": trigger_id,
                    "run_id": run_id,
                    "provider": media_result.provider,
                    "asset_ids": asset_ids,
                    "final_eligible": media_result.final_eligible,
                    "trace_alias": trace_relative,
                    "trace_sha256": trace_sha256,
                },
                run_id=run_id,
                trigger_id=trigger_id,
                checkpoint_kind="post_generation",
                checkpoint_label=f"{trigger_id} generated draft",
                checkpoint_state=post_generation_state,
            )
            terminal_status = "completed"
            self.storage.finish_run(run_id, status="completed")
            return GenerationOutcome(
                status="completed",
                playthrough_id=playthrough_id,
                branch_id=branch_id,
                run_id=run_id,
                trigger_id=trigger_id,
                kind=kind,
                spec_sha256=str(spec["spec_sha256"]),
                spec_path=spec_relative,
                media_paths=tuple(media_paths),
                asset_ids=tuple(asset_ids),
                provider=media_result.to_audit_dict(),
                prompt_provider=(
                    prompt_result.to_audit_dict() if prompt_result is not None else None
                ),
                trace_path=trace_relative,
                final_eligible=media_result.final_eligible,
                human_review_required=True,
                degraded_reasons=tuple(degraded_reasons),
            )
        except Exception as exc:
            terminal_error = terminal_error or f"{type(exc).__name__}: {exc}"
            try:
                failure_trace_sha256 = (
                    trace_digest(load_jsonl(trace_path)) if trace_path.is_file() else None
                )
                current_head = self.storage.get_head(playthrough_id)
                current_checkpoint_id = current_head.get("checkpoint_id")
                checkpoint_state: Mapping[str, Any] = {}
                if isinstance(current_checkpoint_id, str):
                    current_checkpoint = self.storage.get_checkpoint(
                        playthrough_id, current_checkpoint_id
                    )
                    raw_checkpoint_state = current_checkpoint.get("state")
                    if isinstance(raw_checkpoint_state, Mapping):
                        checkpoint_state = raw_checkpoint_state
                normalized_failure_state = normalize_checkpoint_state(
                    checkpoint_state
                )
                normalized_failure_state["cursor"] = {
                    "trigger_id": trigger_id,
                    "phase": "generation_failed",
                }
                normalized_failure_state["promises"] = list(
                    (failure_state or {}).get("promises", [])
                )
                normalized_failure_state["extra"] = {
                    **normalized_failure_state["extra"],
                    "run_id": run_id,
                    "trace_alias": trace_relative,
                    "trace_sha256": failure_trace_sha256,
                }
                self.manager.append_event(
                    playthrough_id,
                    event_type="generation_failed",
                    actor="system",
                    payload={
                        "trigger_id": trigger_id,
                        "run_id": run_id,
                        "safe_error": terminal_error,
                        "trace_alias": trace_relative,
                        "trace_sha256": failure_trace_sha256,
                    },
                    run_id=run_id,
                    trigger_id=trigger_id,
                    checkpoint_kind="post_generation",
                    checkpoint_label=f"{trigger_id} generation failed",
                    checkpoint_state=normalized_failure_state,
                )
            except (StorageError, KeyError, TypeError, ValueError):
                pass
            if terminal_status == "failed":
                try:
                    self.storage.finish_run(
                        run_id, status="failed", safe_error=terminal_error
                    )
                except StorageError:
                    pass
            raise
