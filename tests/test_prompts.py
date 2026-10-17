from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from nining.loader import load_materials
from nining.prompts import build_base_media_prompt, build_prompt_composer_request
from nining.spec_builder import build_scene_spec

ROOT = Path(__file__).resolve().parents[1]


def _reference_visual_spec() -> dict[str, Any]:
    materials = load_materials(ROOT / "materials" / "sample")
    identity = {
        key: materials.persona[key]
        for key in (
            "id",
            "name",
            "nickname",
            "gender",
            "birth_date",
            "looks",
            "voice",
        )
        if key in materials.persona
    }
    return {
        "kind": "image",
        "trigger_id": "REFERENCE_VISUAL",
        "trigger_at_utc": None,
        "intent": "Fictional adult identity anchor",
        "why": "Establish a text-designed identity reference.",
        "identity": identity,
        "state": {"mood": "natural"},
        "future_locked_message_ids": [],
        "conflicts": [],
        "evidence": [
            {
                "evidence_id": "persona:identity",
                "visibility": "reference_only",
                "source": {"file": "persona.json"},
                "value": {"identity": identity},
            }
        ],
        "generation": {"prompt_inputs": {"identity": identity}},
    }


def test_t1_prompt_contains_verified_runtime_scene_constraints() -> None:
    materials = load_materials(ROOT / "materials" / "sample")
    spec = build_scene_spec(materials, "T1").to_dict()

    prompt = build_base_media_prompt(spec)

    assert "锁骨发" in prompt
    assert "刘海" in prompt
    assert "黄色卫衣" in prompt
    assert "嗓子真的哑了" not in prompt
    assert "Visible time-specific facts override" in prompt
    assert "T1 SCENE LOCK" in prompt
    assert "clavicle-length hair" in prompt
    assert "own left side is shorter" in prompt
    assert "yellow hoodie" in prompt
    assert "restaurant selfie" in prompt
    assert "T1 REFERENCE-EDIT LOCK" in prompt


def test_reference_prompt_locks_subject_left_right_without_scene_leakage() -> None:
    prompt = build_base_media_prompt(_reference_visual_spec())

    assert "REFERENCE-IDENTITY SCOPE" in prompt
    assert "REFERENCE-IDENTITY ORIENTATION LOCK" in prompt
    assert "subject's own left" in prompt
    assert "银色小月亮耳钉" in prompt
    assert "yellow hoodie" not in prompt
    assert "second-grade child" not in prompt
    assert "huangge tree" not in prompt
    assert "persona.json" not in prompt


def test_t3_prompt_locks_child_scene_and_excludes_adult_t1_traits() -> None:
    materials = load_materials(ROOT / "materials" / "sample")
    spec = build_scene_spec(materials, "T3").to_dict()

    prompt = build_base_media_prompt(spec)

    assert "T3 CHILDHOOD SCENE LOCK" in prompt
    assert "second-grade child" in prompt
    assert "red overalls" in prompt
    assert "huangge tree" in prompt
    assert "dog visibly larger than the child" in prompt
    assert "dog's own left ear" in prompt
    assert "T3 AGE-SEPARATION LOCK" in prompt
    assert "T3 ADULT-T1 EXCLUSION" in prompt
    assert "adult yellow hoodie" in prompt
    assert "state.json" not in prompt
    assert "chat.jsonl" not in prompt


def test_future_locked_text_is_excluded_even_when_prompt_inputs_are_contaminated() -> None:
    materials = load_materials(ROOT / "materials" / "sample")
    spec = build_scene_spec(materials, "T1").to_dict()
    locked_id = spec["future_locked_message_ids"][0]
    sentinel = "FUTURE_LOCKED_SENTINEL_MUST_NOT_APPEAR"
    contaminated_message = {
        "id": locked_id,
        "at_utc": "2026-10-14T11:45:00Z",
        "role": "persona",
        "text": sentinel,
    }
    prompt_inputs = spec["generation"]["prompt_inputs"]
    prompt_inputs["recent_visible_messages"].append(contaminated_message)
    spec["evidence"].append(
        {
            "evidence_id": f"message:{locked_id}",
            "at_utc": "2026-10-14T11:45:00Z",
            "visibility": "visible",
            "source": {"file": "injected-future.jsonl"},
            "value": {
                "role": "persona",
                "text": sentinel,
            },
        }
    )

    prompt = build_base_media_prompt(spec)

    assert sentinel not in prompt
    assert "T1 SCENE LOCK" in prompt


def test_sample_profiles_do_not_pollute_a_replaceable_t1_scenespec() -> None:
    spec: dict[str, Any] = {
        "kind": "image",
        "trigger_id": "T1",
        "trigger_at_utc": "2026-11-01T09:00:00Z",
        "intent": "Send a library photo",
        "why": "A friend requested it.",
        "identity": {
            "name": "Alex Rivera",
            "looks": {"hair": "black curls", "style": "green cardigan"},
        },
        "state": {"mood": "focused"},
        "future_locked_message_ids": ["g002"],
        "conflicts": [],
        "evidence": [
            {
                "evidence_id": "message:g001",
                "at_utc": "2026-11-01T08:59:00Z",
                "visibility": "visible",
                "source": {"file": "generic-materials.jsonl"},
                "value": {
                    "role": "persona",
                    "text": "I am at the library in a green cardigan.",
                },
            }
        ],
        "generation": {
            "prompt_inputs": {
                "recent_visible_messages": [
                    {
                        "id": "g001",
                        "at_utc": "2026-11-01T08:59:00Z",
                        "role": "persona",
                        "text": "I am at the library in a green cardigan.",
                    }
                ]
            }
        },
    }

    prompt = build_base_media_prompt(deepcopy(spec))

    assert "green cardigan" in prompt
    assert "T1 SCENE LOCK" not in prompt
    assert "yellow hoodie" not in prompt
    assert "clavicle-length hair" not in prompt
    assert "huangge tree" not in prompt
    assert "generic-materials.jsonl" not in prompt


def test_voice_prompt_and_composer_keep_exact_script() -> None:
    materials = load_materials(ROOT / "materials" / "sample")
    spec = build_scene_spec(materials, "T5").to_dict()
    script = spec["say"]
    assert isinstance(script, str)

    prompt = build_base_media_prompt(spec)
    composer = build_prompt_composer_request(spec, prompt)

    assert f"VOICE SCRIPT (EXACT):\n{script}" in prompt
    assert script in composer
    assert "Do not add greetings" in prompt
