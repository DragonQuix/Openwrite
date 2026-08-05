"""Shared helpers for safely locating long text replacement ranges."""

from __future__ import annotations

from typing import Any

FOLDED_RANGE_ANCHOR_LENGTHS = (96, 48, 24, 12)


def select_folded_range_anchors(
    source: str,
    old_text: str,
    *,
    min_text_chars: int,
) -> dict[str, Any]:
    """Find one ordered range by folding long-text anchors down to 12 chars."""

    text = str(old_text or "").strip()
    if len(text) < min_text_chars:
        return {
            "ok": False,
            "error": "text_too_short_for_range_anchors",
            "message": "文本未达到自动首尾锚点阈值。",
            "details": {"submitted_chars": len(text)},
        }

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    attempted_lengths: list[int] = []
    seen_anchors: set[tuple[str, str]] = set()
    last_start = ""
    last_end = ""
    for anchor_chars in FOLDED_RANGE_ANCHOR_LENGTHS:
        if len(lines) >= 2:
            start_anchor = lines[0][:anchor_chars]
            end_anchor = lines[-1][-anchor_chars:]
        else:
            start_anchor = text[:anchor_chars]
            end_anchor = text[-anchor_chars:]
        if (
            not start_anchor
            or not end_anchor
            or start_anchor == end_anchor
            or (start_anchor, end_anchor) in seen_anchors
        ):
            continue
        seen_anchors.add((start_anchor, end_anchor))
        attempted_lengths.append(anchor_chars)
        last_start = start_anchor
        last_end = end_anchor

        start_occurrences = source.count(start_anchor)
        end_occurrences = source.count(end_anchor)
        details = {
            "anchor_chars": anchor_chars,
            "attempted_anchor_chars": list(attempted_lengths),
            "start_occurrences": start_occurrences,
            "end_occurrences": end_occurrences,
            "suggested_start_text": start_anchor,
            "suggested_end_text": end_anchor,
        }
        if start_occurrences > 1 or end_occurrences > 1:
            return {
                "ok": False,
                "error": "ambiguous_text_range",
                "message": (
                    f"{anchor_chars} 字符首尾锚点存在多处匹配，"
                    "已停止自动折半以避免误改。"
                ),
                "details": details,
            }
        if start_occurrences == 0 or end_occurrences == 0:
            continue

        start = source.find(start_anchor)
        end = source.find(end_anchor)
        if end < start + len(start_anchor):
            return {
                "ok": False,
                "error": "text_range_not_found",
                "message": "首尾锚点均唯一，但顺序不成立。",
                "details": details,
            }
        return {
            "ok": True,
            "start_text": start_anchor,
            "end_text": end_anchor,
            "details": details,
        }

    return {
        "ok": False,
        "error": "text_range_not_found",
        "message": "首尾锚点从 96 字符折半到 12 字符后仍未同时匹配。",
        "details": {
            "attempted_anchor_chars": attempted_lengths,
            "suggested_start_text": last_start,
            "suggested_end_text": last_end,
        },
    }
