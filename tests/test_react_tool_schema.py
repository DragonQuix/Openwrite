"""ReAct tool schema contract tests."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.agent.react import OPENWRITE_TOOLS


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


def test_outline_structure_tool_is_read_only_and_supports_chapter_selection():
    tool = next(t for t in OPENWRITE_TOOLS if t.name == "get_outline_structure")

    assert tool.required == []
    assert "chapter_id" in tool.parameters["properties"]
    assert "只读" in tool.description


def test_outline_edit_tool_requires_revision_and_bounded_operations():
    tool = next(t for t in OPENWRITE_TOOLS if t.name == "edit_outline_structure")

    assert tool.required == ["operation", "revision"]
    assert tool.parameters["properties"]["operation"]["enum"] == [
        "rename",
        "add_child",
        "add_after",
        "delete",
    ]
    assert "增量" in tool.description
    assert "已有正文" in tool.description
    assert "连续补位" in tool.description
    assert "连续重编号" in tool.parameters["properties"]["operation"]["description"]
    assert "confirm" in tool.parameters["properties"]
    assert "默认 false" in tool.parameters["properties"]["confirm"]["description"]
