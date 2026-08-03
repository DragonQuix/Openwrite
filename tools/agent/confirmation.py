"""Shared confirmation policy for agent-initiated project mutations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

CONFIRMABLE_TOOLS = {
    "confirm_outline_edits",
    "edit_outline_structure",
    "edit_project_document",
    "edit_world_relation",
    "edit_world_relations",
    "manage_manuscript_versions",
    "update_chapter_intervention",
}

CONFIRMATION_BLOCK_ERROR = "explicit_user_confirmation_required"

_OVERRIDE_NEGATIVE_MARKERS = (
    "不要再确认",
    "不用确认",
    "无需确认",
    "别再确认",
    "不需要确认",
    "直接应用",
    "直接修改",
    "直接写入",
    "直接保存",
)

_NEGATIVE_MARKERS = (
    "不确认",
    "不同意",
    "先不",
    "暂不",
    "不要",
    "别改",
    "取消",
    "放弃",
)

_EXPLICIT_MARKERS = (
    "确认应用",
    "确认修改",
    "确认大纲",
    "确认关系",
    "确认恢复",
    "确认写入",
    "同意修改",
    "同意应用",
    "应用这版",
    "应用修改",
    "写入大纲",
    "写入关系",
    "保存修改",
    "保存大纲",
    "恢复这个版本",
    "采用这版",
    "就按这版",
    "提交修改",
    "直接应用",
    "直接修改",
    "直接写入",
    "无需确认",
    "不用确认",
    "可以应用",
    "可以写入",
    "可以保存",
    "apply this",
    "apply it",
    "confirm apply",
    "looks good",
)

_SHORT_CONFIRMATIONS = {
    "确认",
    "同意",
    "可以",
    "可以的",
    "应用",
    "保存",
    "提交",
    "就这样",
    "改吧",
    "好",
    "好的",
    "行",
    "行的",
    "嗯",
    "yes",
    "y",
    "ok",
    "okay",
    "apply",
    "confirm",
    "lgtm",
}


def is_explicit_mutation_confirmation(text: str) -> bool:
    """Return whether one user turn explicitly authorizes applying a preview."""

    normalized = "".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    if any(marker in normalized for marker in _OVERRIDE_NEGATIVE_MARKERS):
        return True
    if any(marker in normalized for marker in _NEGATIVE_MARKERS):
        return False
    if normalized in _SHORT_CONFIRMATIONS:
        return True
    return any(marker in normalized for marker in _EXPLICIT_MARKERS)


def is_confirmation_policy_block(payload: Mapping[str, Any] | None) -> bool:
    """Return whether a tool payload is a soft confirmation gate, not a hard failure."""

    if not isinstance(payload, Mapping):
        return False
    if payload.get("blocked") is True and payload.get("error") == CONFIRMATION_BLOCK_ERROR:
        return True
    return (
        payload.get("ok") is False
        and payload.get("error") == CONFIRMATION_BLOCK_ERROR
        and payload.get("next_action") == "request_mutation_confirmation"
    )


def guard_confirmable_executors(
    executors: Mapping[str, Callable[[dict[str, Any]], Any]],
    *,
    instruction: Callable[[], str],
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Wrap mutation executors so ``confirm=true`` needs explicit user intent."""

    guarded = dict(executors)
    for name in CONFIRMABLE_TOOLS:
        executor = guarded.get(name)
        if executor is None:
            continue
        guarded[name] = _guard_executor(name, executor, instruction)
    return guarded


def _guard_executor(
    name: str,
    executor: Callable[[dict[str, Any]], Any],
    instruction: Callable[[], str],
) -> Callable[[dict[str, Any]], Any]:
    def guarded(args: dict[str, Any]) -> Any:
        payload = args if isinstance(args, dict) else {}
        applying = name == "confirm_outline_edits" or bool(payload.get("confirm"))
        if not applying or is_explicit_mutation_confirmation(instruction()):
            return executor(payload)
        return {
            "action": name,
            "ok": False,
            "applied": False,
            "blocked": True,
            "error": CONFIRMATION_BLOCK_ERROR,
            "message": "尚未收到用户对本次 diff 的明确应用指令，项目文件未修改。",
            "next_action": "request_mutation_confirmation",
        }

    return guarded
