"""Deterministic, time-bounded media briefs built from a ``SceneSpec``.

These builders are deliberately provider-neutral.  They never inspect raw
future messages; the only input is the already-audited scene specification.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

_ALLOWED_EVIDENCE_VISIBILITIES = frozenset({"visible", "reference_only"})
_OMITTED_PROMPT_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "endpoint",
        "file",
        "headers",
        "path",
        "relative_path",
        "secret",
        "source",
        "token",
        "url",
    }
)
_SOURCE_REFERENCE = re.compile(
    r"""(?ix)
    https?://[^\s<>\"']+
    |
    (?:[a-z]:)?[\\/][^\s<>\"']+
    |
    (?:[a-z0-9_.-]+[\\/])+[a-z0-9_.-]+
    |
    [a-z0-9_.-]+\.(?:jsonl?|toml|ya?ml|md|txt)
    """
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_text(value: Any) -> str:
    """Retain SceneSpec text while keeping source and network references private."""

    if not isinstance(value, str):
        return ""
    return _SOURCE_REFERENCE.sub("[omitted source reference]", value)


def _safe_prompt_value(value: Any) -> Any:
    """Return a deterministic, prompt-safe projection of JSON-like SceneSpec data."""

    if isinstance(value, Mapping):
        return {
            str(key): _safe_prompt_value(item)
            for key, item in value.items()
            if str(key).casefold() not in _OMITTED_PROMPT_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_safe_prompt_value(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported SceneSpec value omitted]"


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _future_locked_ids(spec: Mapping[str, Any]) -> set[str]:
    raw_ids = spec.get("future_locked_message_ids", [])
    if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
        return set()
    return {item for item in raw_ids if isinstance(item, str) and item}


def _safe_evidence(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project only auditable, pre-cutoff evidence into the runtime prompt."""

    raw_evidence = spec.get("evidence", [])
    if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes)):
        return []
    cutoff = _parse_utc(spec.get("trigger_at_utc"))
    future_locked = _future_locked_ids(spec)
    projected: list[dict[str, Any]] = []
    for raw_item in raw_evidence:
        item = _mapping(raw_item)
        evidence_id = item.get("evidence_id")
        visibility = item.get("visibility")
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or visibility not in _ALLOWED_EVIDENCE_VISIBILITIES
        ):
            continue
        if evidence_id.startswith("message:") and evidence_id.removeprefix(
            "message:"
        ) in future_locked:
            continue
        at_utc = item.get("at_utc")
        evidence_at = _parse_utc(at_utc)
        if cutoff is not None and (evidence_at is None or evidence_at > cutoff):
            continue
        safe_item: dict[str, Any] = {
            "evidence_id": evidence_id,
            "visibility": visibility,
            "value": _safe_prompt_value(item.get("value")),
        }
        if isinstance(at_utc, str) and evidence_at is not None:
            safe_item["at_utc"] = at_utc
        projected.append(safe_item)
    return projected


def _trusted_recent_messages(
    spec: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Keep only recent messages that exactly match allowed SceneSpec evidence."""

    evidence_by_id = {
        item["evidence_id"].removeprefix("message:"): item
        for item in evidence
        if isinstance(item.get("evidence_id"), str)
        and str(item["evidence_id"]).startswith("message:")
    }
    generation = _mapping(spec.get("generation"))
    prompt_inputs = _mapping(generation.get("prompt_inputs"))
    raw_messages = prompt_inputs.get("recent_visible_messages", [])
    if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
        return []
    future_locked = _future_locked_ids(spec)
    trusted: list[dict[str, Any]] = []
    for raw_item in raw_messages:
        item = _mapping(raw_item)
        message_id = item.get("id")
        if (
            not isinstance(message_id, str)
            or message_id in future_locked
            or message_id not in evidence_by_id
        ):
            continue
        evidence_item = evidence_by_id[message_id]
        evidence_value = _mapping(evidence_item.get("value"))
        if (
            item.get("role") != evidence_value.get("role")
            or item.get("text") != evidence_value.get("text")
        ):
            continue
        at_utc = item.get("at_utc")
        if at_utc != evidence_item.get("at_utc") or _parse_utc(at_utc) is None:
            continue
        trusted.append(
            {
                "id": message_id,
                "at_utc": at_utc,
                "role": item.get("role"),
                "text": _safe_text(item.get("text")),
            }
        )
    return trusted


def _message_texts(evidence: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    texts: list[str] = []
    for item in evidence:
        evidence_id = item.get("evidence_id")
        value = _mapping(item.get("value"))
        text = value.get("text")
        if (
            isinstance(evidence_id, str)
            and evidence_id.startswith("message:")
            and isinstance(text, str)
        ):
            texts.append(text)
    return tuple(texts)


def _contains_all(texts: Sequence[str], required_fragments: Sequence[str]) -> bool:
    corpus = "\n".join(texts)
    return all(fragment in corpus for fragment in required_fragments)


def _reference_has_verified_asymmetry(identity: Mapping[str, Any]) -> bool:
    looks = _mapping(identity.get("looks"))
    face = _safe_text(looks.get("face"))
    accessories = _safe_text(looks.get("accessories"))
    orientation = _safe_text(looks.get("note"))
    return (
        _contains_all((face,), ("左", "眉", "痣"))
        and _contains_all((accessories,), ("左耳", "耳洞", "耳钉"))
        and _contains_all((orientation,), ("左右", "本人"))
    )


def _t1_profile_is_verified(spec: Mapping[str, Any], messages: Sequence[str]) -> bool:
    intent = _safe_text(spec.get("intent"))
    return _contains_all(
        messages,
        (
            "锁骨发",
            "刘海",
            "左边比右边短",
            "黄色卫衣",
            "下班",
            "杭州又下雨",
            "跷脚牛肉",
            "人好多",
            "门口那桌",
        ),
    ) and _contains_all((intent,), ("自拍", "吃饭", "新刘海"))


def _has_verified_long_ponytail(identity: Mapping[str, Any]) -> bool:
    hair = _safe_text(_mapping(identity.get("looks")).get("hair"))
    return _contains_all((hair,), ("及胸", "低马尾"))


def _t3_profile_is_verified(messages: Sequence[str]) -> bool:
    return _contains_all(
        messages,
        (
            "二年级",
            "红色背带裤",
            "黄葛树",
            "大黄狗",
            "狗比我还大",
            "左边耳朵缺了一块",
        ),
    )


def _t3_has_verified_t1_traits(messages: Sequence[str]) -> bool:
    return _contains_all(
        messages,
        ("锁骨发", "刘海", "左边比右边短", "黄色卫衣"),
    )


def _image_scene_constraints(
    spec: Mapping[str, Any],
    identity: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Add scene profiles only after their facts are present in this SceneSpec."""

    if spec.get("kind") != "image":
        return []
    trigger_id = spec.get("trigger_id")
    messages = _message_texts(evidence)
    if trigger_id == "REFERENCE_VISUAL":
        constraints = [
            (
                "REFERENCE-IDENTITY SCOPE: This is a timeless fictional identity "
                "anchor. "
                "Do not import temporary scene, haircut, clothing, childhood, or "
                "conversation facts."
            ),
        ]
        if _reference_has_verified_asymmetry(identity):
            constraints.append(
                "REFERENCE-IDENTITY ORIENTATION LOCK: Treat left/right in IDENTITY "
                "JSON as the fictional subject's own sides. Preserve the verified "
                "left-eyebrow and left-ear asymmetry on the subject's own left, make "
                "that side auditable in a non-mirrored view, and do not mirror or "
                "duplicate either marker."
            )
        return constraints
    if trigger_id == "T1" and _t1_profile_is_verified(spec, messages):
        reference_constraint = (
            "T1 REFERENCE-EDIT LOCK: If human-accepted fictional identity "
            "reference images are supplied, use them only for stable facial "
            "identity. Apply the verified T1 hair and clothing changes."
        )
        if _has_verified_long_ponytail(identity):
            reference_constraint += " Do not retain the verified earlier long ponytail."
        if _reference_has_verified_asymmetry(identity):
            reference_constraint += " Do not mirror the verified own-left markers."
        return [
            (
                "T1 SCENE LOCK: Render a casual restaurant selfie while eating in a "
                "rainy after-work setting. Apply the verified clavicle-length "
                "hair, uneven fringe whose own left side is shorter, yellow hoodie, "
                "and crowded entrance-side table."
            ),
            reference_constraint,
        ]
    if trigger_id == "T3" and _t3_profile_is_verified(messages):
        constraints = [
            (
                "T3 CHILDHOOD SCENE LOCK: Render a wholly fictional, age-appropriate "
                "second-grade child in red overalls under a very large huangge tree "
                "with a large yellow local mixed-breed dog visibly larger than the "
                "child. The dog's own left ear has one small, non-graphic missing "
                "notch."
            ),
            (
                "T3 AGE-SEPARATION LOCK: Any adult identity reference may preserve "
                "only gentle, child-compatible facial continuity. Do not shrink, "
                "retouch, or paste an adult face onto a child, and do not import adult "
                "body shape, jewelry, glasses, makeup, or scene styling."
            ),
        ]
        if _t3_has_verified_t1_traits(messages):
            constraints.append(
                "T3 ADULT-T1 EXCLUSION: Do not carry forward the adult yellow hoodie, "
                "clavicle-length hair, uneven own-left-shorter fringe, or restaurant "
                "selfie setting."
            )
        return constraints
    return []


def build_base_media_prompt(spec: Mapping[str, Any]) -> str:
    """Return the authoritative brief for an image or voice operation."""

    kind = str(spec.get("kind", ""))
    if kind not in {"image", "voice"}:
        raise ValueError("scene kind must be image or voice")
    identity = _safe_prompt_value(_mapping(spec.get("identity")))
    state = _safe_prompt_value(_mapping(spec.get("state")))
    intent = _safe_text(spec.get("intent")) or "not supplied"
    why = _safe_text(spec.get("why")) or "not supplied"
    say = spec.get("say")
    conflicts = _safe_prompt_value(spec.get("conflicts", []))
    evidence = _safe_evidence(spec)
    reference_scene = spec.get("trigger_id") == "REFERENCE_VISUAL"
    if reference_scene:
        state = {}
        evidence = [
            item for item in evidence if item.get("visibility") == "reference_only"
        ]
    recent_messages = [] if reference_scene else _trusted_recent_messages(spec, evidence)
    constraints = _image_scene_constraints(
        spec, _mapping(identity), evidence
    )

    common = [
        "You are producing media for a fictional character from text-only source material.",
        "Do not imitate, name, reference, or claim to depict any real person.",
        (
            "Do not include or request credentials, keys, cookies, endpoints, URLs, "
            "filesystem paths, or network operations."
        ),
        (
            "Use only the facts below. Future-locked records are deliberately "
            "excluded; do not infer or reconstruct them from memory, later messages, "
            "or context."
        ),
        "Keep identity, scene, props, handedness, chronology, and audience knowledge consistent.",
        "Visible time-specific facts override mutable baseline traits such as hair, clothing, location, age, and temporary voice condition.",
        f"TRIGGER: {_safe_text(spec.get('trigger_id'))} at {_safe_text(spec.get('trigger_at_utc'))}",
        f"INTENT: {intent}",
        f"WHY: {why}",
        "IDENTITY JSON:\n" + _json(identity),
        "STATE JSON:\n" + _json(state),
        "RECENT VISIBLE MESSAGES:\n" + _json(recent_messages),
        "CONFLICTS TO RESOLVE CONSERVATIVELY:\n" + _json(conflicts),
        "LOCKED EVIDENCE (source metadata omitted):\n" + _json(evidence),
    ]
    common.extend(constraints)
    if kind == "image":
        common.extend(
            [
                "Create one plausible casual photograph, not a polished advertisement.",
                "Preserve stable facial identity markers while applying only time-appropriate changes.",
                "Any visible text must match the requested text exactly; otherwise avoid incidental text.",
                "No watermark, no celebrity likeness, no extra fingers, no mirrored identity markers.",
            ]
        )
    else:
        if not isinstance(say, str) or not say.strip():
            raise ValueError("voice scene requires an exact script")
        common.extend(
            [
                "Design a voice performance; the spoken words must remain byte-for-byte identical to SCRIPT.",
                "Do not add greetings, fillers, music, sound effects, or a second speaker.",
                "VOICE SCRIPT (EXACT):\n" + say,
            ]
        )
    return "\n\n".join(common)


def build_prompt_composer_request(spec: Mapping[str, Any], base_prompt: str) -> str:
    """Ask the configured text model for a concise production formulation."""

    kind = str(spec.get("kind", ""))
    exact_script = spec.get("say") if kind == "voice" else None
    requirements = [
        "Rewrite the authoritative brief below as a concise production prompt.",
        "Return prompt text only, with no Markdown fencing or commentary.",
        "Never add a fact that is not present in the brief.",
        "Never mention a real person.",
    ]
    if exact_script is not None:
        requirements.append(
            "Keep this exact script unchanged in the result: " + str(exact_script)
        )
    requirements.append("AUTHORITATIVE BRIEF:\n" + base_prompt)
    return "\n\n".join(requirements)


def merge_composed_prompt(
    *,
    base_prompt: str,
    composed_prompt: str | None,
    provider: str | None,
    exact_script: str | None,
) -> str:
    """Keep deterministic constraints authoritative over model assistance."""

    if not composed_prompt or provider == "deterministic":
        return base_prompt
    sections = [
        base_prompt,
        "MODEL-ASSISTED FORMULATION (subordinate to the authoritative brief):\n"
        + composed_prompt.strip(),
    ]
    if exact_script is not None:
        sections.append("FINAL SCRIPT (EXACT, OVERRIDES ALL OTHER TEXT):\n" + exact_script)
    return "\n\n".join(sections)
