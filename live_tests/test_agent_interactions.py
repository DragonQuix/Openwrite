from __future__ import annotations

from pathlib import Path

from live_tests.conftest import require_live_tier
from tools.agent.dante import DanteChatAgent
from tools.goethe import GoetheChatAgent

MUTATING_TOOLS = {
    "create_character",
    "edit_project_document",
    "edit_world_relation",
    "edit_world_relations",
    "edit_outline_structure",
    "stage_outline_edits",
    "confirm_outline_edits",
    "delegate_chapter_write",
}


def _tool_names(activity: list[dict]) -> list[str]:
    return [
        str(item.get("tool") or "")
        for item in activity
        if item.get("event") == "tool_started"
    ]


def test_goethe_reads_outline_without_mutating(live_project: Path, live_env, write_artifact):
    require_live_tier("agent")
    activity: list[dict] = []
    agent = GoetheChatAgent(
        live_project,
        "mujianzhe",
        activity_callback=activity.append,
    )
    reply = agent.respond(
        "这是只读诊断。必须先调用 get_outline_structure 查看 ch_007 的位置，"
        "再用不超过五句话说明它如何承接第六章。不要调用任何编辑、生成或确认工具。"
    )
    calls = _tool_names(activity)
    write_artifact("goethe_read_only.json", {"reply": reply, "activity": activity})

    assert reply
    assert "get_outline_structure" in calls
    assert MUTATING_TOOLS.isdisjoint(calls)


def test_dante_reads_canonical_context_without_writing(
    live_project: Path, live_env, write_artifact
):
    require_live_tier("agent")
    activity: list[dict] = []
    agent = DanteChatAgent(
        live_project,
        "mujianzhe",
        activity_callback=activity.append,
    )
    reply = agent.respond(
        "这是只读诊断。必须调用 get_context，chapter_id 设为 ch_007。"
        "随后列出作者意图、创作罗盘、前章衔接、真相文件、伏笔这五类上下文是否齐全。"
        "不要写章、审稿或修改任何文件。"
    )
    calls = _tool_names(activity)
    write_artifact("dante_context_read.json", {"reply": reply, "activity": activity})

    assert reply
    assert "get_context" in calls
    assert MUTATING_TOOLS.isdisjoint(calls)
