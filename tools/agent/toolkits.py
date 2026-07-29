"""OpenWrite agent 工具分组。"""

from __future__ import annotations

ORCHESTRATOR_TOOLKIT = {
    "get_status",
    "get_context",
    "search_project",
    "read_project_document",
    "edit_project_document",
    "list_chapters",
    "get_truth_files",
    "create_character",
    "query_world",
    "get_world_relations",
    "review_chapter",
    "get_workflow_status",
    "start_workflow",
    "advance_workflow",
}

WRITING_TOOLKIT = {
    "write_chapter",
    "get_context",
    "list_chapters",
    "get_truth_files",
}

DANTE_DIRECT_TOOLKIT = {
    "get_status",
    "get_context",
    "search_project",
    "read_project_document",
    "edit_project_document",
    "list_chapters",
    "get_truth_files",
    "create_character",
    "query_world",
    "get_world_relations",
    "search_relation_targets",
    "edit_world_relation",
    "edit_world_relations",
    "get_outline_structure",
    "edit_outline_structure",
}

DANTE_ACTION_TOOLKIT = {
    "summarize_ideation",
    "confirm_ideation_summary",
    "generate_outline_draft",
    "confirm_outline_scope",
    "run_chapter_preflight",
    "delegate_chapter_write",
    "delegate_chapter_review",
}

GOETHE_DIRECT_TOOLKIT = {
    "get_status",
    "get_context",
    "search_project",
    "read_project_document",
    "edit_project_document",
    "list_chapters",
    "get_truth_files",
    "create_character",
    "query_world",
    "get_world_relations",
    "search_relation_targets",
    "edit_world_relation",
    "edit_world_relations",
    "get_outline_structure",
    "edit_outline_structure",
}

GOETHE_ACTION_TOOLKIT = {
    "summarize_ideation",
    "generate_foundation_draft",
    "generate_character_draft",
    "generate_outline_draft",
    "read_outline",
    "stage_outline_edits",
    "confirm_outline_edits",
    "discard_outline_edits",
    "extract_style_source",
    "extract_setting_source",
    "review_source_pack",
    "promote_source_pack",
    "prepare_dante_handoff",
}
