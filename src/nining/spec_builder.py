"""Deterministic SceneSpec construction for offline and fallback operation."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_sha256, format_utc
from .domain import ContextPack, Materials, SceneSpec
from .timeline import build_context


def _identity(persona: dict[str, Any]) -> dict[str, Any]:
    return {
        key: persona.get(key)
        for key in ("id", "name", "nickname", "gender", "birth_date", "looks", "voice")
        if key in persona
    }


def _state_fields(state: dict[str, Any]) -> dict[str, Any]:
    audience_knows = state.get("audience_knows")
    if audience_knows is None:
        audience_knows = state.get("he_already_knows", [])
    return {
        "relationship": state.get("relationship"),
        "mood": state.get("mood"),
        "today_plan": state.get("today_plan", []),
        "promises": state.get("promises", []),
        "audience_knows": audience_knows,
    }


def _qa_checks(context: ContextPack) -> list[dict[str, Any]]:
    checks = [
        {"check_id": "temporal.no_future", "required": True},
        {"check_id": "continuity.identity", "required": True},
        {"check_id": "continuity.scene", "required": True},
        {"check_id": "evidence.traceable", "required": True},
    ]
    if context.trigger.kind == "voice":
        checks.extend(
            [
                {"check_id": "voice.same_preset_identity", "required": True},
                {"check_id": "voice.script_exact", "required": True},
            ]
        )
    if context.conflicts:
        checks.append({"check_id": "conflict.resolution_reviewed", "required": True})
    return checks


def build_scene_spec(materials: Materials, trigger_id: str) -> SceneSpec:
    context = build_context(materials, trigger_id)
    state = context.state.raw if context.state else {}
    prompt_inputs = {
        "intent": context.trigger.what,
        "why": context.trigger.why,
        "identity": _identity(materials.persona),
        "state": _state_fields(state),
        "recent_visible_messages": [
            {
                "id": item.message_id,
                "at_utc": format_utc(item.at),
                "role": item.role,
                "text": item.text,
            }
            for item in context.visible_messages[-12:]
        ],
    }
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "builder": "deterministic",
        "trigger_id": context.trigger.trigger_id,
        "trigger_at_utc": format_utc(context.trigger.at),
        "kind": context.trigger.kind,
        "intent": context.trigger.what,
        "why": context.trigger.why,
        "say": context.trigger.say,
        "identity": _identity(materials.persona),
        "state": _state_fields(state),
        "visible_message_ids": [item.message_id for item in context.visible_messages],
        "trigger_message_id": (
            context.trigger_message.message_id if context.trigger_message else None
        ),
        "future_locked_message_ids": [item.message_id for item in context.future_messages],
        "conflicts": list(context.conflicts),
        "fulfilled_promises": list(context.fulfilled_promises),
        "evidence": list(context.evidence),
        "generation": {
            "provider": None,
            "model": None,
            "final_eligible": False,
            "prompt_inputs": prompt_inputs,
        },
        "qa_checks": _qa_checks(context),
        "human_review_required": True,
    }
    payload["spec_sha256"] = canonical_sha256(payload)
    return SceneSpec(payload)


def build_all_scene_specs(materials: Materials) -> tuple[SceneSpec, ...]:
    return tuple(build_scene_spec(materials, trigger.trigger_id) for trigger in materials.triggers)

