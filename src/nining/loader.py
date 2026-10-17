"""Strict, portable material loading for JSON and JSONL inputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .canonical import canonical_sha256, parse_utc
from .domain import Materials, Message, SourceRef, StateSnapshot, Trigger

REQUIRED_FILES = (
    "persona.json",
    "user.json",
    "chat.jsonl",
    "state.json",
    "triggers.json",
)


class _StrictJsonError(ValueError):
    """A JSON value is syntactically accepted by Python but invalid for materials."""


@dataclass(frozen=True)
class MaterialError(ValueError):
    path: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        location = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"{location}: {self.message}"


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJsonError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _StrictJsonError(f"non-finite JSON value: {value}")


def _validate_unicode(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise _StrictJsonError("string contains an unpaired Unicode surrogate")
        return
    if isinstance(value, list):
        for item in value:
            _validate_unicode(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_unicode(key)
            _validate_unicode(item)


def _decode_json(text: str) -> Any:
    value = json.loads(
        text,
        object_pairs_hook=_json_object,
        parse_constant=_reject_json_constant,
    )
    _validate_unicode(value)
    return value


def _read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8-sig") as handle:
            return _decode_json(handle.read())
    except FileNotFoundError as exc:
        raise MaterialError(path.name, "required file not found") from exc
    except UnicodeDecodeError as exc:
        raise MaterialError(path.name, "invalid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise MaterialError(path.name, f"invalid JSON: {exc.msg}", exc.lineno) from exc
    except _StrictJsonError as exc:
        raise MaterialError(path.name, f"invalid JSON: {exc}") from exc
    except ValueError as exc:
        raise MaterialError(path.name, f"invalid JSON: {exc}") from exc
    except RecursionError as exc:
        raise MaterialError(path.name, "JSON nesting is too deep") from exc


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        handle = path.open(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise MaterialError(path.name, "required file not found") from exc
    except UnicodeDecodeError as exc:
        raise MaterialError(path.name, "invalid UTF-8") from exc
    records: list[dict[str, Any]] = []
    try:
        with handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = _decode_json(line)
                except UnicodeDecodeError as exc:
                    raise MaterialError(path.name, "invalid UTF-8", line_no) from exc
                except json.JSONDecodeError as exc:
                    raise MaterialError(path.name, f"invalid JSONL: {exc.msg}", line_no) from exc
                except _StrictJsonError as exc:
                    raise MaterialError(path.name, f"invalid JSONL: {exc}", line_no) from exc
                except ValueError as exc:
                    raise MaterialError(path.name, f"invalid JSONL: {exc}", line_no) from exc
                except RecursionError as exc:
                    raise MaterialError(path.name, "JSON nesting is too deep", line_no) from exc
                if not isinstance(value, dict):
                    raise MaterialError(path.name, "JSONL record must be an object", line_no)
                records.append(value)
    except UnicodeDecodeError as exc:
        raise MaterialError(path.name, "invalid UTF-8") from exc
    return tuple(records)


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MaterialError(path, "expected a JSON object")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise MaterialError(path, "expected a JSON array")
    return value


def _string(record: dict[str, Any], key: str, path: str, *, optional: bool = False) -> str | None:
    value = record.get(key)
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise MaterialError(path, f"{key} must be a non-empty string")
    return value


def _unique(records: list[str], label: str, path: str) -> None:
    seen: set[str] = set()
    for item in records:
        if item in seen:
            raise MaterialError(path, f"duplicate {label}: {item}")
        seen.add(item)


def _file_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest = sha256()
        size_bytes = 0
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    size_bytes += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise MaterialError(relative, f"could not read material file: {exc.strerror or exc}") from exc
        files.append(
            {
                "path": relative,
                "size_bytes": size_bytes,
                "sha256": digest.hexdigest(),
            }
        )
    return {"files": files, "sha256": canonical_sha256(files)}


def load_materials(root: str | Path) -> Materials:
    source = Path(root).expanduser().resolve()
    if not source.is_dir():
        raise MaterialError(source.name, "materials path must be a directory")
    for required_name in REQUIRED_FILES:
        if not (source / required_name).is_file():
            raise MaterialError(required_name, "required file not found")

    persona = _object(_read_json(source / "persona.json"), "persona.json")
    user = _object(_read_json(source / "user.json"), "user.json")
    message_records = list(_read_jsonl(source / "chat.jsonl"))
    trigger_records = _array(_read_json(source / "triggers.json"), "triggers.json")
    state_records = _array(_read_json(source / "state.json"), "state.json")

    messages: list[Message] = []
    message_ids: list[str] = []
    for index, raw_value in enumerate(message_records):
        raw = _object(raw_value, f"chat.jsonl:{index + 1}")
        message_id = _string(raw, "id", f"chat.jsonl:{index + 1}")
        at_text = _string(raw, "at", f"chat.jsonl:{index + 1}")
        assert message_id is not None and at_text is not None
        try:
            at = parse_utc(at_text)
        except ValueError as exc:
            raise MaterialError("chat.jsonl", str(exc), index + 1) from exc
        role = _string(raw, "role", f"chat.jsonl:{index + 1}")
        kind = _string(raw, "kind", f"chat.jsonl:{index + 1}")
        assert role is not None and kind is not None
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise MaterialError("chat.jsonl", "text must be string or null", index + 1)
        trigger_id = raw.get("trigger")
        if trigger_id is not None and not isinstance(trigger_id, str):
            raise MaterialError("chat.jsonl", "trigger must be string or null", index + 1)
        name_value = raw.get("name")
        if name_value is not None and not isinstance(name_value, str):
            raise MaterialError("chat.jsonl", "name must be string or null", index + 1)
        messages.append(
            Message(
                message_id=message_id,
                at=at,
                sequence_no=index,
                role=role,
                name=name_value,
                kind=kind,
                text=text,
                trigger_id=trigger_id,
                raw=raw,
                source=SourceRef("chat.jsonl", message_id, index + 1),
            )
        )
        message_ids.append(message_id)
    _unique(message_ids, "message id", "chat.jsonl")

    triggers: list[Trigger] = []
    trigger_ids: list[str] = []
    for index, raw_value in enumerate(trigger_records):
        raw = _object(raw_value, f"triggers.json[{index}]")
        trigger_id = _string(raw, "id", f"triggers.json[{index}]")
        at_text = _string(raw, "at", f"triggers.json[{index}]")
        kind = _string(raw, "kind", f"triggers.json[{index}]")
        what = _string(raw, "what", f"triggers.json[{index}]")
        assert trigger_id and at_text and kind and what
        try:
            at = parse_utc(at_text)
        except ValueError as exc:
            raise MaterialError("triggers.json", str(exc)) from exc
        triggers.append(
            Trigger(
                trigger_id=trigger_id,
                at=at,
                kind=kind,
                what=what,
                why=_string(raw, "why", f"triggers.json[{index}]", optional=True),
                say=_string(raw, "say", f"triggers.json[{index}]", optional=True),
                raw=raw,
                source=SourceRef("triggers.json", trigger_id),
            )
        )
        trigger_ids.append(trigger_id)
    _unique(trigger_ids, "trigger id", "triggers.json")

    states: list[StateSnapshot] = []
    state_ids: list[str] = []
    for index, raw_value in enumerate(state_records):
        raw = _object(raw_value, f"state.json[{index}]")
        trigger_id = _string(raw, "trigger", f"state.json[{index}]")
        at_text = _string(raw, "at", f"state.json[{index}]")
        assert trigger_id and at_text
        try:
            at = parse_utc(at_text)
        except ValueError as exc:
            raise MaterialError("state.json", str(exc)) from exc
        states.append(
            StateSnapshot(
                trigger_id=trigger_id,
                at=at,
                raw=raw,
                source=SourceRef("state.json", trigger_id),
            )
        )
        state_ids.append(trigger_id)
    _unique(state_ids, "state trigger", "state.json")

    trigger_set = set(trigger_ids)
    unknown_states = sorted(set(state_ids) - trigger_set)
    if unknown_states:
        raise MaterialError("state.json", f"unknown trigger references: {', '.join(unknown_states)}")
    unknown_messages = sorted(
        {item.trigger_id for item in messages if item.trigger_id and item.trigger_id not in trigger_set}
    )
    if unknown_messages:
        raise MaterialError("chat.jsonl", f"unknown trigger references: {', '.join(unknown_messages)}")

    triggers_by_id = {item.trigger_id: item for item in triggers}
    for message in messages:
        if message.trigger_id is None:
            continue
        trigger = triggers_by_id[message.trigger_id]
        if message.at != trigger.at:
            raise MaterialError(
                "chat.jsonl",
                f"trigger placeholder timestamp must equal trigger timestamp: {message.trigger_id}",
                message.source.line,
            )
    for state in states:
        trigger = triggers_by_id[state.trigger_id]
        if state.at > trigger.at:
            raise MaterialError(
                "state.json",
                f"state snapshot is after trigger: {state.trigger_id}",
            )

    sales_catalog_path = source / "sales" / "catalog.json"
    sales_chat_path = source / "sales" / "chat.jsonl"
    sales_catalog = (
        _object(_read_json(sales_catalog_path), "sales/catalog.json")
        if sales_catalog_path.is_file()
        else None
    )
    sales_messages = _read_jsonl(sales_chat_path) if sales_chat_path.is_file() else ()

    return Materials(
        root_name=source.name,
        persona=persona,
        user=user,
        messages=tuple(messages),
        triggers=tuple(sorted(triggers, key=lambda item: (item.at, item.trigger_id))),
        states=tuple(states),
        sales_catalog=sales_catalog,
        sales_messages=sales_messages,
        manifest=_file_manifest(source),
    )

