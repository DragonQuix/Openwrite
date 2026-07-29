"""ReAct tool schema contract tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.agent.react import OPENWRITE_SYSTEM_PROMPT, OPENWRITE_TOOLS


def test_react_never_exposes_model_credentials_as_tools():
    names = {tool.name for tool in OPENWRITE_TOOLS}

    assert "configure_model" not in names
    assert "API Key" in OPENWRITE_SYSTEM_PROMPT
    assert "不得索取" in OPENWRITE_SYSTEM_PROMPT
    assert "接口格式" in OPENWRITE_SYSTEM_PROMPT
    assert "Base URL 与模型名由用户填写" in OPENWRITE_SYSTEM_PROMPT
    assert "CommonMark" in OPENWRITE_SYSTEM_PROMPT
    assert "Studio 会安全渲染" in OPENWRITE_SYSTEM_PROMPT


def test_update_truth_file_tool_schema_uses_canonical_names():
    tool = next(t for t in OPENWRITE_TOOLS if t.name == "update_truth_file")
    desc = tool.parameters["properties"]["file_name"]["description"]

    assert "current_state/ledger/relationships" in desc
    assert "particle_ledger" not in desc
    assert "character_matrix" not in desc


def test_relation_edit_tool_requires_preview_confirmation_contract():
    tool = next(t for t in OPENWRITE_TOOLS if t.name == "edit_world_relation")
    properties = tool.parameters["properties"]

    assert tool.required == ["source_id", "target_id"]
    assert "base_revision" in properties
    assert "confirm" in properties
    assert "默认 false" in properties["confirm"]["description"]
    assert properties["action"]["enum"] == ["upsert", "remove"]


def test_project_document_tools_require_revisioned_preview_contract():
    read_tool = next(t for t in OPENWRITE_TOOLS if t.name == "read_project_document")
    edit_tool = next(t for t in OPENWRITE_TOOLS if t.name == "edit_project_document")
    properties = edit_tool.parameters["properties"]

    assert read_tool.required == ["path"]
    assert "src、data/manuscript、data/foreshadowing" in read_tool.description
    assert edit_tool.required == ["path", "edits"]
    assert "old_text/new_text" in edit_tool.description
    assert "默认只预览 diff" in edit_tool.description
    assert "revision" in properties
    assert "confirm" in properties
    assert "默认 false" in properties["confirm"]["description"]


def test_batch_relation_tools_support_search_and_confirmed_writes():
    search_tool = next(t for t in OPENWRITE_TOOLS if t.name == "search_relation_targets")
    batch_tool = next(t for t in OPENWRITE_TOOLS if t.name == "edit_world_relations")
    properties = batch_tool.parameters["properties"]

    assert search_tool.required == ["query"]
    assert "出身地点" in search_tool.description
    assert "能力体系" in search_tool.description
    assert batch_tool.required == ["relations"]
    assert "批量新增、更新或删除" in batch_tool.description
    assert "默认只预览所有源文件 diff" in batch_tool.description
    assert "base_revisions" in properties
    assert properties["relations"]["items"]["required"] == ["source_id", "target_id"]
    assert (
        properties["relations"]["items"]["properties"]["action"]["enum"]
        == ["upsert", "remove"]
    )


def test_outline_structure_tool_is_read_only_and_supports_chapter_selection():
    tool = next(t for t in OPENWRITE_TOOLS if t.name == "get_outline_structure")

    assert tool.required == []
    assert "chapter_id" in tool.parameters["properties"]
    assert "只读" in tool.description


def test_world_relations_tool_contract_mentions_shared_search_surface():
    tool = next(t for t in OPENWRITE_TOOLS if t.name == "get_world_relations")

    assert "Studio 同源" in tool.description
    assert "名称、ID 或关系文字" in tool.description


def test_outline_edit_tool_requires_revision_and_bounded_operations():
    tool = next(t for t in OPENWRITE_TOOLS if t.name == "edit_outline_structure")

    assert tool.required == ["operation", "revision"]
    assert tool.parameters["properties"]["operation"]["enum"] == [
        "rename",
        "update_summary",
        "add_child",
        "add_after",
        "delete",
    ]
    assert "增量" in tool.description
    assert "修改节点内容" in tool.description
    assert "已有正文" in tool.description
    assert "连续补位" in tool.description
    assert "summary" in tool.parameters["properties"]
    assert "连续重编号" in tool.parameters["properties"]["operation"]["description"]
    assert "confirm" in tool.parameters["properties"]
    assert "默认 false" in tool.parameters["properties"]["confirm"]["description"]
