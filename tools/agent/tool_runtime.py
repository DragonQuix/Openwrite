"""Shared tool executor registry for all novel interaction surfaces."""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml

from tools.novel_service import NovelApplicationService, NovelServiceError

ToolExecutor = Callable[[dict[str, Any]], dict[str, Any]]


def _service_error(exc: NovelServiceError) -> dict[str, Any]:
    return {
        "ok": False,
        "blocked": exc.code == "PROJECT_BUSY",
        "code": exc.code,
        "error": str(exc),
    }


def _service_executor(project_root: Path, operation: str) -> ToolExecutor:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        try:
            service = NovelApplicationService(project_root)
            if operation == "write_chapter":
                return service.write_chapter(args)
            if operation == "review_chapter":
                return service.review_chapter(
                    str(args.get("chapter_id") or "latest")
                )
            raise NovelServiceError(f"未知应用服务操作: {operation}")
        except NovelServiceError as exc:
            return _service_error(exc)

    return execute


def _project(project_root: Path) -> tuple[dict[str, Any], str]:
    path = project_root / "novel_config.yaml"
    if not path.exists():
        raise NovelServiceError("未找到项目配置", code="INVALID_PROJECT")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not data.get("novel_id"):
        raise NovelServiceError("项目配置缺少 novel_id", code="INVALID_PROJECT")
    return data, str(data["novel_id"])


def _application_executor(project_root: Path, operation: str) -> ToolExecutor:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        try:
            config, novel_id = _project(project_root)
            if operation == "get_status":
                from tools.agent.book_state import BookStateStore
                from tools.novel_workspace import list_chapters
                from tools.truth_manager import TruthFilesManager

                chapters = list_chapters(project_root, novel_id)
                snapshots = TruthFilesManager(project_root, novel_id).list_snapshots()
                current_arc = config.get("current_arc")
                current_chapter = config.get("current_chapter")
                state_store = BookStateStore(project_root, novel_id)
                if state_store.path.exists():
                    state = state_store.load_or_create()
                    current_arc = state.current_arc or current_arc
                    current_chapter = state.current_chapter or current_chapter
                return {
                    "novel_id": novel_id,
                    "current_arc": current_arc,
                    "current_chapter": current_chapter,
                    "chapters_written": len(chapters),
                    "snapshots": len(snapshots),
                }
            if operation == "get_context":
                from tools.context_builder import ContextBuilder

                chapter_id = str(args.get("chapter_id") or "next")
                preview = NovelApplicationService(project_root).context_preview(
                    chapter_id
                )
                packet = cast(dict[str, Any], preview["packet"])
                sections = packet.get("prompt_sections", {})
                try:
                    window_size = max(1, min(20, int(args.get("window_size") or 5)))
                except (TypeError, ValueError):
                    window_size = 5
                generation_context = ContextBuilder(
                    project_root, novel_id
                ).build_generation_context(
                    str(preview["chapter_id"]), window_size=window_size
                )
                return {
                    "chapter_id": preview["chapter_id"],
                    "target_words": preview["target_words"],
                    "chapter_goals": packet.get("chapter_goals", []),
                    "sections": list(sections) if isinstance(sections, dict) else [],
                    "compression": {
                        "message_budget": dict(generation_context.compression),
                        "packet_documents": dict(packet.get("compression", {}) or {}),
                    },
                    "context_packet": packet,
                }
            if operation == "search_project":
                from tools.project_search import ProjectSearchIndex

                novel_root = project_root / "data" / "novels" / novel_id
                return ProjectSearchIndex(novel_root).search(
                    str(args.get("query") or ""),
                    scope=str(args.get("scope") or "all"),
                    limit=int(args.get("limit") or 20),
                )
            if operation == "read_project_document":
                return _read_project_document(project_root, novel_id, args)
            if operation == "edit_project_document":
                return _edit_project_document(project_root, novel_id, args)
            if operation == "list_chapters":
                from tools.novel_workspace import list_chapters

                return {
                    "chapters": [
                        {
                            "number": int(item.chapter_id.split("_")[-1]),
                            "chapter_id": item.chapter_id,
                            "title": item.title,
                        }
                        for item in list_chapters(project_root, novel_id)
                    ]
                }
            if operation == "get_truth_files":
                from tools.truth_manager import TruthFilesManager

                truth = TruthFilesManager(project_root, novel_id).load_truth_files()
                return {
                    "current_state": truth.current_state[:500],
                    "ledger": truth.ledger[:500],
                    "relationships": truth.relationships[:500],
                }
            if operation == "create_character":
                service = NovelApplicationService(project_root)
                path = service.create_document(
                    kind="character",
                    name=str(args.get("name") or ""),
                    description=str(args.get("description") or ""),
                )
                return {
                    "ok": True,
                    "file": str(path),
                    "name": str(args.get("name") or ""),
                    "safe_name": path.stem,
                }
            if operation == "create_outline":
                content = str(args.get("outline_content") or "")
                path = project_root / "data" / "novels" / novel_id / "src" / "outline.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                return {"file": str(path), "size": len(content)}
            if operation == "get_outline_structure":
                from tools.outline_tree import build_outline_structure

                return build_outline_structure(
                    project_root / "data" / "novels" / novel_id,
                    chapter_id=str(args.get("chapter_id") or ""),
                )
            if operation == "edit_outline_structure":
                from tools.outline_tree import OutlineEditError, mutate_outline_structure
                from tools.outline_tree import build_outline_structure as build_structure

                root = project_root / "data" / "novels" / novel_id
                outline_path = root / "src" / "outline.md"
                if not outline_path.exists():
                    return {"ok": False, "error": "当前没有可编辑的大纲"}
                original = outline_path.read_text(encoding="utf-8")
                try:
                    edit = mutate_outline_structure(
                        root,
                        operation=str(args.get("operation") or ""),
                        revision=str(args.get("revision") or ""),
                        node_id=str(args.get("node_id") or ""),
                        title=str(args.get("title") or ""),
                        summary=str(args.get("summary") or ""),
                        kind=str(args.get("kind") or ""),
                    )
                except OutlineEditError as exc:
                    return {
                        "ok": False,
                        "applied": False,
                        "conflict": exc.code == "conflict",
                        "error": str(exc),
                    }
                revised = str(edit["content"])
                diff = "".join(
                    difflib.unified_diff(
                        original.splitlines(keepends=True),
                        revised.splitlines(keepends=True),
                        fromfile="src/outline.md",
                        tofile="src/outline.md (preview)",
                    )
                )
                if not bool(args.get("confirm")):
                    return {
                        "ok": True,
                        "applied": False,
                        "message": edit["message"],
                        "renumbered": edit.get("renumbered", []),
                        "skipped_renumbering": edit.get("skipped_renumbering", []),
                        "revision": str(args.get("revision") or ""),
                        "diff": diff,
                        "next_action": "使用相同参数并设置 confirm=true",
                    }
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=outline_path.parent,
                    prefix=".outline.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    handle.write(revised)
                    temporary = Path(handle.name)
                temporary.replace(outline_path)
                from tools.git_checkpoint import GitCheckpointManager

                checkpoint_path = outline_path.relative_to(project_root).as_posix()
                checkpoint = GitCheckpointManager(project_root).checkpoint(
                    [checkpoint_path],
                    "outline: incremental tree edit",
                )
                return {
                    "ok": True,
                    "applied": True,
                    "message": edit["message"],
                    "selection_hint": edit["selection_hint"],
                    "renumbered": edit.get("renumbered", []),
                    "skipped_renumbering": edit.get("skipped_renumbering", []),
                    "diff": diff,
                    "checkpoint": checkpoint.to_dict(),
                    "outline": build_structure(root),
                }
            if operation == "update_truth_file":
                from tools.context_schema import normalize_truth_file_key
                from tools.truth_manager import TruthFilesManager

                key = normalize_truth_file_key(str(args.get("file_name") or ""))
                if key not in {"current_state", "ledger", "relationships"}:
                    return {"ok": False, "error": f"Unknown file: {key}"}
                manager = TruthFilesManager(project_root, novel_id)
                truth = manager.load_truth_files()
                content = str(args.get("content") or "")
                setattr(truth, key, content)
                manager.save_truth_files(truth)
                return {"file": key, "size": len(content)}
            if operation == "query_world":
                from tools.world_query import get_entity, list_entities

                entity_id = str(args.get("entity_id") or "")
                if entity_id:
                    entity = get_entity(novel_id, entity_id, project_root)
                    if not entity:
                        return {"ok": False, "error": f"实体不存在: {entity_id}"}
                    return {
                        "entity": {
                            "id": entity["id"],
                            "name": entity["name"],
                            "type": entity["type"],
                            "subtype": entity["subtype"],
                            "status": entity["status"],
                            "description": str(entity["description"] or "")[:200],
                            "rules": list(entity["rules"] or [])[:5],
                            "relations": list(entity["relations"] or [])[:10],
                        }
                    }
                entities = list_entities(
                    novel_id,
                    entity_type=args.get("type"),
                    project_root=project_root,
                )
                return {"entities": entities, "count": len(entities)}
            if operation == "get_world_relations":
                from tools.world_query import get_relations_topology

                graph = get_relations_topology(novel_id, project_root)
                return {
                    "entities": graph["nodes"],
                    "relations": [
                        {
                            "source": edge["source"],
                            "target": edge["target"],
                            "description": edge["label"],
                        }
                        for edge in graph["edges"][:50]
                    ],
                    "total_entities": graph["totals"]["nodes"],
                    "total_relations": graph["totals"]["edges"],
                }
            if operation == "search_relation_targets":
                from tools.world_query import search_relation_targets

                return search_relation_targets(
                    novel_id,
                    str(args.get("query") or ""),
                    project_root=project_root,
                    entity_type=str(args.get("type") or args.get("entity_type") or ""),
                    limit=int(args.get("limit") or 20),
                )
            if operation == "edit_world_relation":
                from tools.world_query import edit_world_relation

                return edit_world_relation(
                    novel_id,
                    str(args.get("source_id") or ""),
                    str(args.get("target_id") or ""),
                    str(args.get("description") or ""),
                    project_root=project_root,
                    action=str(args.get("action") or "upsert"),
                    base_revision=str(args.get("base_revision") or ""),
                    confirm=bool(args.get("confirm")),
                )
            if operation == "edit_world_relations":
                from tools.world_query import edit_world_relations

                return edit_world_relations(
                    novel_id,
                    args.get("relations") if isinstance(args.get("relations"), list) else [],
                    project_root=project_root,
                    confirm=bool(args.get("confirm")),
                    base_revisions=(
                        args.get("base_revisions")
                        if isinstance(args.get("base_revisions"), dict)
                        else {}
                    ),
                )
            if operation in {
                "create_foreshadowing",
                "update_foreshadowing",
                "list_foreshadowing",
                "validate_foreshadowing",
            }:
                return _foreshadowing(project_root, novel_id, operation, args)
            if operation in {
                "get_workflow_status",
                "start_workflow",
                "advance_workflow",
            }:
                return _workflow(project_root, novel_id, operation, args)
            if operation == "validate_truth":
                return _validate_truth(project_root, novel_id, args)
            if operation == "extract_dialogue_fingerprint":
                return _dialogue_fingerprint(project_root, novel_id, args)
            if operation == "validate_post_write":
                return _validate_post_write(project_root, novel_id, args)
            if operation == "chunk_text":
                return _chunk_text(args)
            if operation == "compress_section":
                return _compress_section(project_root, novel_id, args)
            if operation in {
                "get_chapter_run_v2",
                "record_chapter_intervention",
                "update_chapter_intervention",
                "cancel_chapter_run_v2",
            }:
                from tools.chapter_run_v2 import chapter_run_v2_action

                action_by_operation = {
                    "get_chapter_run_v2": str(args.get("action") or "list"),
                    "record_chapter_intervention": "record_intervention",
                    "update_chapter_intervention": "update_intervention",
                    "cancel_chapter_run_v2": "cancel",
                }
                return chapter_run_v2_action(
                    project_root,
                    novel_id,
                    {**args, "action": action_by_operation[operation]},
                )
            if operation == "diagnose_runtime":
                from tools.runtime_diagnostics import RuntimeDiagnosticsService

                return RuntimeDiagnosticsService(
                    project_root, novel_id
                ).run().model_dump(mode="json")
            if operation == "manage_rolling_plan":
                from tools.rolling_planning import rolling_plan_action

                return rolling_plan_action(project_root, novel_id, args)
            if operation in {"manage_manuscript_versions", "manage_annotations"}:
                from tools.manuscript_editing import manuscript_editing_action

                if operation == "manage_manuscript_versions":
                    action_map = {
                        "list": "versions",
                        "get": "version",
                        "checkpoint": "checkpoint",
                        "restore": "restore",
                    }
                    default_action = "list"
                else:
                    action_map = {
                        "list": "annotations",
                        "create": "annotate",
                        "resolve": "resolve_annotation",
                    }
                    default_action = "list"
                requested = str(args.get("action") or default_action)
                if requested not in action_map:
                    return {
                        "ok": False,
                        "blocked": False,
                        "code": "INVALID_EDITING_ACTION",
                        "error": "未知正文编辑操作",
                    }
                return manuscript_editing_action(
                    project_root,
                    novel_id,
                    {**args, "action": action_map[requested]},
                )
            from tools.agent.audit_tools import AUDIT_TOOL_NAMES, execute_audit_tool

            if operation in AUDIT_TOOL_NAMES:
                return execute_audit_tool(project_root, novel_id, operation, args)
            raise NovelServiceError(f"未知应用工具: {operation}")
        except NovelServiceError as exc:
            return _service_error(exc)
        except Exception as exc:
            from tools.chapter_run_v2 import ChapterRunV2Error
            from tools.manuscript_editing import ManuscriptEditingError
            from tools.rolling_planning import RollingPlanningError

            if isinstance(exc, ChapterRunV2Error):
                return {
                    "ok": False,
                    "blocked": exc.code
                    in {"REVISION_REQUIRED", "STALE_REVISION", "CONFIRMATION_REQUIRED"},
                    "code": exc.code,
                    "error": str(exc),
                }
            if isinstance(exc, RollingPlanningError):
                return {
                    "ok": False,
                    "blocked": exc.code.startswith("STALE_"),
                    "code": exc.code,
                    "error": str(exc),
                }
            if isinstance(exc, ManuscriptEditingError):
                return {
                    "ok": False,
                    "blocked": exc.code
                    in {"CONFIRMATION_REQUIRED", "STALE_REVISION"},
                    "code": exc.code,
                    "error": str(exc),
                }
            raise

    return execute


def _read_project_document(
    project_root: Path, novel_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    path_result = _resolve_project_document(project_root, novel_id, args)
    if isinstance(path_result, dict):
        return path_result
    path, relative = path_result
    content = path.read_text(encoding="utf-8")
    revision = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    try:
        max_chars = max(1000, min(80000, int(args.get("max_chars") or 24000)))
    except (TypeError, ValueError):
        max_chars = 24000
    truncated = len(content) > max_chars
    return {
        "ok": True,
        "path": relative,
        "revision": revision,
        "content": content[:max_chars],
        "truncated": truncated,
        "size": len(content),
    }


def _edit_project_document(
    project_root: Path, novel_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    path_result = _resolve_project_document(project_root, novel_id, args)
    if isinstance(path_result, dict):
        return path_result
    path, relative = path_result
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        return {"ok": False, "error": "edits 不能为空"}
    if len(edits) > 50:
        return {"ok": False, "error": "单次最多编辑 50 个片段"}
    original = path.read_text(encoding="utf-8")
    revision = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    revised = original
    applied: list[dict[str, Any]] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return {"ok": False, "error": f"第 {index + 1} 个修改不是对象"}
        old_text = str(edit.get("old_text") or "")
        new_text = str(edit.get("new_text") or "")
        replace_all = bool(edit.get("replace_all"))
        if not old_text:
            return {"ok": False, "error": f"第 {index + 1} 个修改缺少 old_text"}
        occurrences = revised.count(old_text)
        if occurrences == 0:
            return {
                "ok": False,
                "error": "old_text_not_found",
                "message": f"第 {index + 1} 个 old_text 不存在，请重新读取文件。",
                "revision": revision,
            }
        if occurrences > 1 and not replace_all:
            return {
                "ok": False,
                "error": "ambiguous_old_text",
                "message": f"第 {index + 1} 个 old_text 匹配到 {occurrences} 处。",
                "revision": revision,
            }
        revised = revised.replace(old_text, new_text, -1 if replace_all else 1)
        applied.append(
            {
                "index": index + 1,
                "replacements": occurrences if replace_all else 1,
                "replace_all": replace_all,
            }
        )
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            revised.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    payload = {
        "ok": True,
        "applied": False,
        "changed": revised != original,
        "path": relative,
        "revision": revision,
        "edit_count": len(applied),
        "edits": applied,
        "diff": diff,
        "next_action": "确认后使用相同 path/edits/revision，并设置 confirm=true",
    }
    if not bool(args.get("confirm")) or revised == original:
        return payload
    if str(args.get("revision") or args.get("base_revision") or "").strip() != revision:
        return {
            **payload,
            "ok": False,
            "error": "document_revision_conflict",
            "message": "文件已变化，请重新读取后确认。",
        }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(revised)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return {
        **payload,
        "applied": True,
        "revision": hashlib.sha256(revised.encode("utf-8")).hexdigest()[:16],
    }


def _resolve_project_document(
    project_root: Path, novel_id: str, args: dict[str, Any]
) -> tuple[Path, str] | dict[str, Any]:
    raw_path = str(args.get("path") or args.get("source_path") or "").strip()
    if not raw_path:
        return {"ok": False, "error": "缺少 path"}
    novel_root = (project_root / "data" / "novels" / novel_id).resolve()
    candidate = (novel_root / raw_path).resolve()
    allowed_roots = [
        (novel_root / "src").resolve(),
        (novel_root / "data" / "manuscript").resolve(),
        (novel_root / "data" / "foreshadowing").resolve(),
    ]
    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        return {
            "ok": False,
            "error": "只能读取或修改小说 src、manuscript、foreshadowing 资产",
        }
    if not candidate.is_file():
        return {"ok": False, "error": f"文件不存在: {raw_path}"}
    try:
        relative = candidate.relative_to(novel_root).as_posix()
    except ValueError:
        return {"ok": False, "error": "文件不在当前小说项目内"}
    return candidate, relative


def _chapter_text(project_root: Path, novel_id: str, chapter_id: str) -> str:
    root = project_root / "data" / "novels" / novel_id / "data" / "manuscript"
    if chapter_id == "latest":
        candidates = sorted(
            root.glob("**/ch_*.md"),
            key=lambda path: int(path.stem.split("_")[-1]),
        )
        if candidates:
            chapter_id = candidates[-1].stem
    for pattern in (f"**/{chapter_id}.md", f"**/{chapter_id}_*.md"):
        matches = sorted(root.glob(pattern))
        if matches:
            return matches[0].read_text(encoding="utf-8")
    return ""


def _validate_truth(
    project_root: Path, novel_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    from tools.state_validator import StateValidator
    from tools.truth_manager import TruthFilesManager

    chapter_id = str(args.get("chapter_id") or "latest")
    truth = TruthFilesManager(project_root, novel_id).load_truth_files()
    issues = StateValidator().validate(
        current_state=truth.current_state,
        content=_chapter_text(project_root, novel_id, chapter_id),
        chapter_number=(
            int(chapter_id.split("_")[-1])
            if chapter_id.startswith("ch_") and chapter_id[3:].isdigit()
            else 1
        ),
    )
    return {
        "chapter_id": chapter_id,
        "issues": [
            {
                "severity": issue.severity,
                "category": issue.category,
                "description": issue.description,
            }
            for issue in issues
        ],
        "issue_count": len(issues),
        "critical_count": sum(issue.severity == "critical" for issue in issues),
    }


def _dialogue_fingerprint(
    project_root: Path, novel_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    from tools.dialogue_fingerprint import DialogueFingerprintExtractor

    chapter_id = str(args.get("chapter_id") or "latest")
    content = _chapter_text(project_root, novel_id, chapter_id)
    if not content:
        return {"ok": False, "error": f"未找到章节: {chapter_id}"}
    names = args.get("character_names")
    fingerprints = DialogueFingerprintExtractor().extract(
        [content], character_names=names if isinstance(names, list) and names else None
    )
    return {
        "chapter_id": chapter_id,
        "fingerprints": [
            {
                "character": item.character_name,
                "avg_sentence_length": item.avg_sentence_length,
                "common_bigrams": item.common_bigrams[:5],
                "question_ratio": item.question_ratio,
                "speech_patterns": item.speech_patterns[:5],
                "summary": item.to_prompt_text(),
            }
            for item in fingerprints
        ],
    }


def _validate_post_write(
    project_root: Path, novel_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    from tools.post_validator import PostWriteValidator

    chapter_id = str(args.get("chapter_id") or "latest")
    content = _chapter_text(project_root, novel_id, chapter_id)
    if not content:
        return {"ok": False, "error": f"未找到章节: {chapter_id}"}
    violations = PostWriteValidator().validate(content)
    return {
        "chapter_id": chapter_id,
        "violations": [
            {
                "severity": item.severity,
                "rule": item.rule,
                "description": item.description,
                "location": item.location,
            }
            for item in violations
        ],
        "error_count": sum(item.severity == "error" for item in violations),
        "warning_count": sum(item.severity == "warning" for item in violations),
        "passed": not violations,
    }


def _chunk_text(args: dict[str, Any]) -> dict[str, Any]:
    from tools.text_chunker import TextChunker

    path = Path(str(args.get("file_path") or ""))
    if not path.exists():
        return {"ok": False, "error": f"文件不存在: {path}"}
    if not path.is_file():
        return {"ok": False, "error": "不支持的路径类型"}
    result = TextChunker(chunk_size=int(args.get("chunk_size") or 30000)).chunk_file(
        path
    )
    chunks = [
        {
            "index": item.index,
            "chapter_range": item.chapter_range,
            "char_count": item.char_count,
        }
        for item in result.chunks
    ]
    return {"file": str(path), "total_chunks": len(chunks), "chunks": chunks}


def _compress_section(
    project_root: Path, novel_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    from tools.progressive_compressor import ProgressiveCompressor

    compressor = ProgressiveCompressor(
        project_root, str(args.get("novel_id") or novel_id)
    )
    arc_id = str(args.get("arc_id") or "arc_001")
    section_id = str(args.get("section_id") or "")
    result = (
        compressor.compress_section(arc_id, section_id)
        if section_id
        else compressor.compress_arc(arc_id)
    )
    compressed = getattr(
        result,
        "compressed_text",
        getattr(result, "merged_summary", ""),
    )
    total_words = getattr(
        result,
        "word_count",
        getattr(result, "total_word_count", 0),
    )
    ratio = (len(compressed) / total_words) if total_words else 0
    payload: dict[str, Any] = {
        "arc_id": arc_id,
        "compressed": str(compressed or "")[:500],
        "compression_ratio": ratio,
    }
    if section_id:
        payload["section_id"] = section_id
    return payload


def _foreshadowing(
    project_root: Path,
    novel_id: str,
    operation: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    from tools.foreshadowing_manager import ForeshadowingDAGManager

    manager = ForeshadowingDAGManager(project_root, novel_id)
    if operation == "create_foreshadowing":
        payload = dict(args)
        payload["action"] = "create"
        result = NovelApplicationService(project_root).manage_foreshadowing(payload)
        return cast(dict[str, Any], result["result"])
    if operation == "update_foreshadowing":
        payload = dict(args)
        payload["action"] = "update"
        result = NovelApplicationService(project_root).manage_foreshadowing(payload)
        return cast(dict[str, Any], result["result"])
    if operation == "validate_foreshadowing":
        valid, errors = manager.validate_dag()
        return {"valid": valid, "errors": errors}
    nodes = manager.get_pending_nodes(
        min_weight=int(args.get("min_weight") or 1),
        layer=str(args.get("layer") or "") or None,
    )
    statistics = manager.get_statistics()
    return {
        "nodes": [node.model_dump() for node in nodes],
        "total": statistics["total"],
        "by_status": statistics["by_status"],
        "by_layer": statistics["by_layer"],
    }


def _workflow(
    project_root: Path,
    novel_id: str,
    operation: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    from tools.workflow_scheduler import WorkflowScheduler

    scheduler = WorkflowScheduler(project_root, novel_id)
    chapter_id = str(args.get("chapter_id") or "")
    if operation == "start_workflow":
        state = scheduler.create_workflow(chapter_id)
        return {
            "chapter_id": state.chapter_id,
            "current_stage": state.current_stage,
            "message": f"工作流已创建: {chapter_id}",
        }
    if operation == "advance_workflow":
        state = scheduler.load_workflow(chapter_id)
        if state is None:
            return {"ok": False, "error": f"未找到工作流: {chapter_id}"}
        stage = str(args.get("stage_name") or "")
        if stage:
            scheduler.start_stage(state, stage)
        else:
            scheduler.complete_stage(
                state,
                state.current_stage,
                message="advanced via agent tool",
            )
        return {
            "chapter_id": state.chapter_id,
            "current_stage": state.current_stage,
            "message": f"已推进到: {state.current_stage}",
        }
    if chapter_id:
        state = scheduler.load_workflow(chapter_id)
        if state is None:
            return {"ok": False, "error": f"未找到工作流: {chapter_id}"}
        return {
            "chapter_id": state.chapter_id,
            "current_stage": state.current_stage,
            "stages": {stage.name: stage.to_dict() for stage in state.stages},
            "is_complete": scheduler.is_complete(state),
        }
    active_states = scheduler.list_active_workflows()
    active = [state.chapter_id for state in active_states]
    complete = [
        path.stem.removeprefix("wf_")
        for path in sorted(scheduler.workflow_dir.glob("wf_*.yaml"))
        if (state := scheduler.load_workflow(path.stem.removeprefix("wf_")))
        and scheduler.is_complete(state)
    ]
    return {"active": active, "complete": complete, "active_count": len(active)}


def build_tool_executors(project_root: Path) -> dict[str, ToolExecutor]:
    """Build the canonical tool registry shared by CLI and both novel agents."""
    project_root = Path(project_root).resolve()
    executors: dict[str, ToolExecutor] = {
        "write_chapter": _service_executor(project_root, "write_chapter"),
        "review_chapter": _service_executor(project_root, "review_chapter"),
    }
    application_operations = {
        "get_status",
        "get_context",
        "search_project",
        "read_project_document",
        "edit_project_document",
        "list_chapters",
        "create_outline",
        "get_outline_structure",
        "edit_outline_structure",
        "create_character",
        "get_truth_files",
        "update_truth_file",
        "create_foreshadowing",
        "list_foreshadowing",
        "update_foreshadowing",
        "validate_foreshadowing",
        "query_world",
        "get_world_relations",
        "search_relation_targets",
        "edit_world_relation",
        "edit_world_relations",
        "get_workflow_status",
        "start_workflow",
        "advance_workflow",
        "validate_truth",
        "extract_dialogue_fingerprint",
        "validate_post_write",
        "chunk_text",
        "compress_section",
        "inspect_agent_context",
        "list_chapter_runs",
        "get_chapter_run_v2",
        "record_chapter_intervention",
        "update_chapter_intervention",
        "cancel_chapter_run_v2",
        "diagnose_runtime",
        "manage_rolling_plan",
        "manage_manuscript_versions",
        "manage_annotations",
        "get_runtime_state",
        "get_chapter_review",
        "get_task_activity",
        "get_goethe_handoff",
    }
    executors.update(
        {
            name: _application_executor(project_root, name)
            for name in application_operations
        }
    )
    return executors
