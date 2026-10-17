"""Time-bounded context compilation, evidence, conflicts, and promise transitions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .canonical import format_utc
from .domain import ContextPack, Materials, Message, Trigger

CANCEL_MARKERS = (
    "取消",
    "不去",
    "翘了",
    "翘课",
    "不想动",
    "不参加",
    "改天",
    "cancel",
    "skip",
    "not going",
    "cannot go",
    "can't go",
    "won't go",
)


def _compact(text: str) -> str:
    return "".join(character for character in text.casefold() if not character.isspace())


def _contains_cancel_marker(text: str) -> bool:
    compact_text = _compact(text)
    return any(_compact(marker) in compact_text for marker in CANCEL_MARKERS)


def _ngrams(text: str, minimum: int = 2, maximum: int = 4) -> set[str]:
    compact = _compact(text)
    return {
        compact[start : start + size]
        for size in range(minimum, min(maximum, len(compact)) + 1)
        for start in range(len(compact) - size + 1)
    }


def _related(left: str, right: str) -> bool:
    compact_left = _compact(left)
    compact_right = _compact(right)
    if not compact_left or not compact_right:
        return False
    if compact_left in compact_right or compact_right in compact_left:
        return True
    ignored = {"晚上", "今天", "明天", "下午", "上午", "下班", "时候"}
    candidates = {item for item in _ngrams(left) if item not in ignored}
    return any(item in compact_right for item in candidates)


def _plan_conflicts(state: dict[str, Any], messages: Iterable[Message]) -> tuple[dict[str, Any], ...]:
    conflicts: list[dict[str, Any]] = []
    plans = state.get("today_plan", [])
    if not isinstance(plans, list):
        return ()
    for plan in plans:
        if not isinstance(plan, dict) or plan.get("status") != "planned":
            continue
        what = plan.get("what")
        if not isinstance(what, str) or not what.strip():
            continue
        for message in messages:
            text = message.text or ""
            if _contains_cancel_marker(text) and _related(what, text):
                plan_id = plan.get("id")
                plan_label = plan_id if isinstance(plan_id, str) and plan_id else what
                conflicts.append(
                    {
                        "conflict_id": f"plan:{plan_label}:{message.message_id}",
                        "topic": what,
                        "state_value": "planned",
                        "message_value": "cancelled",
                        "resolution": "cancelled",
                        "resolution_rule": "later_specific_chat_overrides_older_plan",
                        "evidence_ids": [
                            f"state:{state.get('trigger')}",
                            f"message:{message.message_id}",
                        ],
                    }
                )
                break
    return tuple(conflicts)


def _fulfilled_promises(trigger: Trigger, state: dict[str, Any]) -> tuple[str, ...]:
    trigger_text = tuple(
        value
        for value in (trigger.what, trigger.why, trigger.say)
        if isinstance(value, str) and value.strip()
    )
    fulfilled: list[str] = []
    seen: set[str] = set()
    promises = state.get("promises", [])
    if not isinstance(promises, list):
        return ()
    for promise in promises:
        if not isinstance(promise, dict):
            continue
        promise_id = promise.get("id")
        what = promise.get("what")
        if not isinstance(promise_id, str) or not promise_id or promise_id in seen:
            continue
        status = promise.get("status")
        if isinstance(status, str) and status.casefold() in {
            "cancelled",
            "canceled",
            "done",
            "expired",
            "fulfilled",
        }:
            continue
        if any(
            promise_id in text or (isinstance(what, str) and _related(what, text))
            for text in trigger_text
        ):
            fulfilled.append(promise_id)
            seen.add(promise_id)
    return tuple(fulfilled)


def build_context(materials: Materials, trigger_id: str) -> ContextPack:
    trigger = materials.triggers_by_id.get(trigger_id)
    if trigger is None:
        raise KeyError(f"unknown trigger: {trigger_id}")
    state = materials.states_by_trigger.get(trigger_id)

    messages = tuple(
        sorted(materials.messages, key=lambda item: (item.at, item.sequence_no, item.message_id))
    )
    trigger_messages = [
        item
        for item in messages
        if item.trigger_id == trigger_id and item.at == trigger.at
    ]
    trigger_message = (
        min(trigger_messages, key=lambda item: (item.sequence_no, item.message_id))
        if trigger_messages
        else None
    )

    visible: list[Message] = []
    future: list[Message] = []
    for message in messages:
        before_by_time = message.at < trigger.at
        before_placeholder = (
            trigger_message is not None
            and message.at == trigger.at
            and message.sequence_no < trigger_message.sequence_no
        )
        if before_by_time or before_placeholder:
            visible.append(message)
        elif trigger_message is None or message.message_id != trigger_message.message_id:
            future.append(message)

    state_raw = state.raw if state else {}
    conflicts = _plan_conflicts(state_raw, visible)
    fulfilled = _fulfilled_promises(trigger, state_raw)

    evidence: list[dict[str, Any]] = [item.to_evidence() for item in visible]
    evidence.append(
        {
            "evidence_id": f"trigger:{trigger.trigger_id}",
            "source": trigger.source.to_dict(),
            "at_utc": format_utc(trigger.at),
            "visibility": "visible",
            "value": trigger.raw,
        }
    )
    if state:
        evidence.append(
            {
                "evidence_id": f"state:{state.trigger_id}",
                "source": state.source.to_dict(),
                "at_utc": format_utc(state.at),
                "visibility": "visible",
                "value": state.raw,
            }
        )

    return ContextPack(
        trigger=trigger,
        state=state,
        visible_messages=tuple(visible),
        trigger_message=trigger_message,
        future_messages=tuple(future),
        conflicts=conflicts,
        fulfilled_promises=fulfilled,
        evidence=tuple(evidence),
    )

