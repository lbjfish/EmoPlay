from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC
from pathlib import Path

import pytest

from nining.canonical import canonical_json, canonical_sha256, parse_utc
from nining.domain import Message, SourceRef
from nining.loader import MaterialError, load_materials
from nining.spec_builder import build_all_scene_specs, build_scene_spec
from nining.timeline import build_context

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "materials" / "sample"


def copy_sample(tmp_path: Path) -> Path:
    target = tmp_path / "materials"
    shutil.copytree(SAMPLE, target)
    return target


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_generic_materials(tmp_path: Path) -> Path:
    target = tmp_path / "generic-materials"
    target.mkdir()
    write_json(
        target / "persona.json",
        {
            "id": "persona-alex",
            "name": "Alex Rivera",
            "nickname": "Alex",
            "gender": "nonbinary",
            "birth_date": "1990-01-01",
            "looks": {"hair": "black", "jacket": "green"},
            "voice": {"preset": "alto-calm"},
        },
    )
    write_json(target / "user.json", {"id": "user-sam", "name": "Sam"})
    (target / "chat.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "g001",
                        "at": "2026-10-15T09:00:00Z",
                        "role": "persona",
                        "name": "Alex",
                        "kind": "text",
                        "text": "I am not going to yoga class tonight.",
                    }
                ),
                json.dumps(
                    {
                        "id": "g002",
                        "at": "2026-10-15T10:00:00Z",
                        "role": "persona",
                        "name": "Alex",
                        "kind": "image",
                        "trigger": "G1",
                        "text": None,
                    }
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_json(
        target / "triggers.json",
        [
            {
                "id": "G1",
                "at": "2026-10-15T10:00:00Z",
                "kind": "image",
                "what": "Send a desk photo",
                "why": "Sam asked for it",
            }
        ],
    )
    write_json(
        target / "state.json",
        [
            {
                "trigger": "G1",
                "at": "2026-10-15T10:00:00Z",
                "relationship": {"desc": "coworkers"},
                "mood": "focused",
                "today_plan": [
                    {
                        "id": "plan-yoga",
                        "what": "yoga class tonight",
                        "status": "planned",
                    }
                ],
                "promises": [],
                "audience_knows": ["Alex uses a blue notebook"],
            }
        ],
    )
    return target


def test_load_sample_materials() -> None:
    materials = load_materials(SAMPLE)
    assert materials.persona["name"] == "陈柠"
    assert len(materials.messages) == 68
    assert [item.trigger_id for item in materials.triggers] == [f"T{index}" for index in range(1, 7)]
    assert materials.manifest["sha256"]


def test_invalid_jsonl_reports_line(tmp_path: Path) -> None:
    target = copy_sample(tmp_path)
    with (target / "chat.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    with pytest.raises(MaterialError) as caught:
        load_materials(target)
    assert caught.value.path == "chat.jsonl"
    assert caught.value.line == 69


def test_duplicate_message_id_fails_closed(tmp_path: Path) -> None:
    target = copy_sample(tmp_path)
    first = (target / "chat.jsonl").read_text(encoding="utf-8").splitlines()[0]
    with (target / "chat.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(first + "\n")
    with pytest.raises(MaterialError, match="duplicate message id"):
        load_materials(target)


def test_unicode_and_emoji_are_preserved() -> None:
    materials = load_materials(SAMPLE)
    t2 = materials.triggers_by_id["T2"]
    assert "😭" in (t2.say or "")
    assert "柠柠" in canonical_json(materials.persona)


def test_t1_cutoff_trigger_placeholder_and_future_lock() -> None:
    materials = load_materials(SAMPLE)
    context = build_context(materials, "T1")
    visible = [item.message_id for item in context.visible_messages]
    future = [item.message_id for item in context.future_messages]
    assert visible[-1] == "m021"
    assert context.trigger_message and context.trigger_message.message_id == "m022"
    assert "m023" in future
    assert "m023" not in visible


def test_t2_does_not_know_late_result() -> None:
    materials = load_materials(SAMPLE)
    context = build_context(materials, "T2")
    visible = {item.message_id for item in context.visible_messages}
    assert "m038" not in visible
    assert "m039" not in visible
    assert "9:40" in (context.trigger.say or "")


def test_t1_plan_conflict_uses_later_specific_chat() -> None:
    context = build_context(load_materials(SAMPLE), "T1")
    conflict = next(item for item in context.conflicts if "瑜伽" in item["topic"])
    assert conflict["state_value"] == "planned"
    assert conflict["resolution"] == "cancelled"
    assert "message:m011" in conflict["evidence_ids"]


def test_t3_fulfills_promise_p1() -> None:
    context = build_context(load_materials(SAMPLE), "T3")
    assert context.fulfilled_promises == ("P1",)
    assert "m044" in {item.message_id for item in context.visible_messages}
    assert "m047" in {item.message_id for item in context.future_messages}


def test_equal_timestamp_uses_sequence_before_placeholder_only() -> None:
    materials = load_materials(SAMPLE)
    trigger = materials.triggers_by_id["T1"]
    before = Message(
        message_id="equal-before",
        at=trigger.at,
        sequence_no=20,
        role="persona",
        name="test",
        kind="text",
        text="visible before placeholder",
        trigger_id=None,
        raw={},
        source=SourceRef("test", "equal-before", 1),
    )
    after = replace(before, message_id="equal-after", sequence_no=22, text="locked after placeholder")
    modified = replace(materials, messages=materials.messages + (before, after))
    context = build_context(modified, "T1")
    visible = {item.message_id for item in context.visible_messages}
    future = {item.message_id for item in context.future_messages}
    assert "equal-before" in visible
    assert "equal-after" in future


def test_all_specs_are_stable_and_traceable() -> None:
    materials = load_materials(SAMPLE)
    first_specs = build_all_scene_specs(materials)
    first = [item.to_dict() for item in first_specs]
    second = [item.to_dict() for item in build_all_scene_specs(materials)]
    assert first == second
    assert [item["trigger_id"] for item in first] == [f"T{index}" for index in range(1, 7)]
    for spec_object, spec in zip(first_specs, first, strict=True):
        payload_without_hash = {
            key: value for key, value in spec.items() if key != "spec_sha256"
        }
        assert spec["spec_sha256"] == canonical_sha256(payload_without_hash)
        assert spec_object.sha256 == spec["spec_sha256"]
        assert spec["evidence"]
        assert spec["future_locked_message_ids"] or spec["trigger_id"] == "T6"
        evidence_ids = {item["evidence_id"] for item in spec["evidence"]}
        assert not {
            f"message:{message_id}" for message_id in spec["future_locked_message_ids"]
        } & evidence_ids
        assert spec["generation"]["final_eligible"] is False


def test_changed_persona_is_not_hardcoded(tmp_path: Path) -> None:
    target = copy_sample(tmp_path)
    persona_path = target / "persona.json"
    persona = json.loads(persona_path.read_text(encoding="utf-8"))
    persona["name"] = "林青"
    persona["nickname"] = "青青"
    persona["city"] = "成都"
    persona_path.write_text(json.dumps(persona, ensure_ascii=False, indent=2), encoding="utf-8")
    spec = build_scene_spec(load_materials(target), "T1").to_dict()
    assert spec["identity"]["name"] == "林青"
    assert "陈柠" not in canonical_json(spec["identity"])


def test_generic_materials_are_not_sample_hardcoded(tmp_path: Path) -> None:
    spec = build_scene_spec(load_materials(write_generic_materials(tmp_path)), "G1").to_dict()
    assert spec["identity"]["name"] == "Alex Rivera"
    assert spec["state"]["audience_knows"] == ["Alex uses a blue notebook"]
    conflict = spec["conflicts"][0]
    assert conflict["topic"] == "yoga class tonight"
    assert conflict["resolution"] == "cancelled"
    assert "message:g001" in conflict["evidence_ids"]
    assert "陈柠" not in canonical_json(spec)
    assert "阿杰" not in canonical_json(spec)


def test_scene_spec_to_dict_is_deep_copy() -> None:
    spec_object = build_scene_spec(load_materials(SAMPLE), "T1")
    payload = spec_object.to_dict()
    payload["generation"]["final_eligible"] = True
    assert spec_object.to_dict()["generation"]["final_eligible"] is False


def test_loader_rejects_trigger_placeholder_timestamp_drift(tmp_path: Path) -> None:
    target = copy_sample(tmp_path)
    lines = (target / "chat.jsonl").read_text(encoding="utf-8").splitlines()
    trigger_record = json.loads(lines[21])
    trigger_record["at"] = "2026-10-14T11:43:01Z"
    lines[21] = json.dumps(trigger_record, ensure_ascii=False)
    (target / "chat.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(MaterialError, match="trigger placeholder timestamp must equal"):
        load_materials(target)


def test_loader_rejects_state_after_trigger(tmp_path: Path) -> None:
    target = copy_sample(tmp_path)
    state = json.loads((target / "state.json").read_text(encoding="utf-8"))
    state[0]["at"] = "2026-10-14T11:43:01Z"
    write_json(target / "state.json", state)
    with pytest.raises(MaterialError, match="state snapshot is after trigger"):
        load_materials(target)


def test_timezone_is_required() -> None:
    with pytest.raises(ValueError, match="lacks timezone"):
        parse_utc("2026-10-15T12:00:00")
    assert parse_utc("2026-10-15T12:00:00+08:00").tzinfo == UTC
