"""Canonical long-form chapter writing and review pipeline."""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import yaml

from tools.context_schema import normalize_context_payload
from tools.style_synthesizer import render_style_manifest_summary


def configure_writer_llm(config: Any) -> dict[str, Any]:
    """Apply operation-specific provider options and return a safe request summary."""
    provider = str(getattr(config, "provider", "") or "").lower()
    base_url = str(getattr(config, "base_url", "") or "").lower()
    model = str(getattr(config, "model", "") or "")
    requested_mode = os.getenv("OPENWRITE_WRITER_THINKING", "auto").strip().lower()
    if requested_mode not in {"auto", "enabled", "disabled"}:
        requested_mode = "auto"

    is_deepseek_chat = provider in {"openai", "custom"} and "api.deepseek.com" in base_url
    is_flash = model.lower() == "deepseek-v4-flash"
    extra = dict(getattr(config, "extra", {}) or {})
    extra_body = dict(extra.get("extra_body") or {})
    existing_thinking = extra_body.get("thinking")
    effective_mode = "provider_default"

    if isinstance(existing_thinking, dict) and existing_thinking.get("type"):
        effective_mode = str(existing_thinking["type"])
    elif is_deepseek_chat and (requested_mode != "auto" or is_flash):
        effective_mode = "disabled" if requested_mode == "auto" else requested_mode
        extra_body["thinking"] = {"type": effective_mode}
        extra["extra_body"] = extra_body
        config.extra = extra

    return {
        "provider": provider,
        "model": model,
        "max_output_tokens": int(getattr(config, "max_tokens", 0) or 0),
        "thinking": effective_mode,
    }


def apply_runtime_delta_with_fallback(
    truth_manager: Any,
    state_delta: Any,
    updates: dict[str, Any],
    *,
    chapter_id: str,
    known_entities: list[str],
) -> tuple[dict[str, Any], str]:
    """Apply a model delta, falling back only to validated additive legacy updates."""
    from tools.runtime_state import RuntimeStateError, legacy_updates_to_delta

    effective_delta = state_delta
    if not effective_delta and updates:
        effective_delta = legacy_updates_to_delta(
            updates, chapter_id=chapter_id
        ).model_dump(mode="json")
    if not effective_delta:
        return {}, ""

    try:
        truth_manager.apply_runtime_delta(
            effective_delta, known_entities=known_entities
        )
        return dict(effective_delta), ""
    except (RuntimeStateError, ValueError) as exc:
        fallback = legacy_updates_to_delta(updates, chapter_id=chapter_id)
        if not fallback.operations:
            raise
        truth_manager.apply_runtime_delta(
            fallback, known_entities=known_entities
        )
        return fallback.model_dump(mode="json"), str(exc)


def _load_config(project_root: Path) -> dict[str, Any]:
    path = project_root / "novel_config.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _chapter_number(chapter_id: Any) -> int:
    text = str(chapter_id or "").strip()
    if not text:
        return 0
    try:
        return int(text.rsplit("_", 1)[-1])
    except ValueError:
        return 0


def _chapter_path(project_root: Path, novel_id: str, chapter_id: str) -> Path:
    config = _load_config(project_root)
    arc_id = str(config.get("current_arc") or "arc_001")
    return (
        project_root
        / "data"
        / "novels"
        / novel_id
        / "data"
        / "manuscript"
        / arc_id
        / f"{chapter_id}.md"
    )


def _load_chapter(project_root: Path, novel_id: str, chapter_id: str) -> str | None:
    expected = _chapter_path(project_root, novel_id, chapter_id)
    if expected.is_file():
        return expected.read_text(encoding="utf-8")
    root = project_root / "data" / "novels" / novel_id / "data" / "manuscript"
    for pattern in (f"**/{chapter_id}.md", f"**/{chapter_id}_*.md"):
        matches = sorted(root.glob(pattern))
        if matches:
            return matches[0].read_text(encoding="utf-8")
    return None


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def _save_chapter(
    project_root: Path,
    novel_id: str,
    chapter_id: str,
    title: str,
    content: str,
) -> Path:
    path = _chapter_path(project_root, novel_id, chapter_id)
    _atomic_write(path, f"# {title}\n\n{content}".encode())
    return path


def load_chapter(
    project_root: Path,
    novel_id: str,
    chapter_id: str,
) -> str | None:
    """Load a committed chapter from the canonical manuscript layout."""
    return _load_chapter(Path(project_root).resolve(), novel_id, chapter_id)


def save_chapter(
    project_root: Path,
    novel_id: str,
    chapter_id: str,
    title: str,
    content: str,
) -> Path:
    """Atomically save a chapter in the canonical manuscript layout."""
    return _save_chapter(
        Path(project_root).resolve(), novel_id, chapter_id, title, content
    )


def _restore(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, previous)


def _style_profile(documents: Any, max_chars: int) -> str:
    if not isinstance(documents, dict):
        return ""
    labels = (
        ("summary", "风格摘要", 600),
        ("prompt_section", "风格指南", 800),
        ("work.manifest", "风格合成清单", 1200),
        ("work.composed", "作品合成风格", 1200),
        ("work.fingerprint", "作品风格指纹", 800),
        ("craft.dialogue_craft", "对话技法", 700),
        ("craft.scene_craft", "场景技法", 700),
        ("craft.rhythm_craft", "节奏技法", 700),
        ("craft.humanization", "去模板化约束", 700),
        ("craft.ai_patterns", "AI痕迹规避", 700),
        ("source.summary", "提取风格摘要", 700),
        ("source.voice", "提取叙述声音", 700),
        ("source.language", "提取语言习惯", 700),
        ("source.rhythm", "提取节奏", 700),
        ("source.dialogue", "提取对话", 700),
        ("source.consistency", "提取一致性", 700),
    )
    parts: list[str] = []
    used: set[str] = set()
    for key, label, limit in labels:
        raw = documents.get(key, "")
        value = (
            render_style_manifest_summary(raw)
            if key == "work.manifest"
            else str(raw).strip()
        )
        if value:
            used.add(key)
            parts.append(f"## {label}\n{value[:limit]}")
    for key in sorted(documents):
        value = str(documents.get(key) or "").strip()
        if key not in used and value:
            parts.append(f"## {key}\n{value[:600]}")
    return "\n\n".join(parts)[:max_chars]


def _packet_payload(packet: Any) -> dict[str, Any]:
    if isinstance(packet, dict):
        payload = dict(packet)
    elif is_dataclass(packet):
        payload = asdict(cast(Any, packet))
    else:
        payload = {
            key: getattr(packet, key)
            for key in (
                "author_intent",
                "creative_focus",
                "story_background",
                "previous_chapter_content",
                "style_documents",
                "character_documents",
                "concept_documents",
                "prompt_sections",
                "foundation",
                "target_words",
            )
            if getattr(packet, key, None) not in (None, "")
        }
    if hasattr(packet, "to_markdown"):
        payload["outline"] = str(packet.to_markdown())
    return payload


def _render_outline(sections: Any) -> str:
    if not isinstance(sections, dict):
        return ""
    parts = []
    for key in ("大纲窗口", "当前章节", "戏剧位置", "本章目标", "上文"):
        value = str(sections.get(key) or "").strip()
        if value:
            parts.append(f"## {key}\n{value}")
    return "\n\n".join(parts)


def _characters(documents: Any) -> list[dict[str, str]]:
    if isinstance(documents, dict):
        return [
            {"name": str(name), "description": str(content)[:1200]}
            for name, content in documents.items()
            if str(content).strip()
        ]
    if not isinstance(documents, list):
        return []
    characters: list[dict[str, str]] = []
    for index, content in enumerate(documents, start=1):
        text = str(content or "").strip()
        if not text:
            continue
        heading = next(
            (
                line.lstrip("#").strip()
                for line in text.splitlines()
                if line.strip().startswith("#")
            ),
            f"角色{index}",
        )
        characters.append({"name": heading, "description": text[:1200]})
    return characters


def build_writer_payload(
    *,
    context: Any,
    truth: Any,
    packet: dict[str, Any],
    guidance: str,
    target_words: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target_words": target_words or getattr(context, "target_words", 0),
        "author_intent": getattr(context, "author_intent", ""),
        "creative_focus": getattr(context, "creative_focus", ""),
        "chapter_goals": getattr(context, "chapter_goals", []),
        "dramatic_context": getattr(context, "dramatic_context", {}),
        "current_state": getattr(context, "current_state", ""),
        "foreshadowing_summary": getattr(context, "foreshadowing_summary", ""),
        "ledger": getattr(context, "ledger", ""),
        "relationships": getattr(truth, "relationships", ""),
        "recent_chapters": getattr(context, "recent_text", ""),
        "chapter_summaries": getattr(context, "chapter_summaries", ""),
    }
    if packet:
        sections = packet.get("prompt_sections", {})
        concepts = packet.get("concept_documents", {})
        styles = packet.get("style_documents", {})
        if not isinstance(sections, dict):
            sections = {}
        if not isinstance(concepts, dict):
            concepts = {}
        payload["author_intent"] = str(
            packet.get("author_intent")
            or sections.get("作者意图")
            or payload["author_intent"]
        )
        payload["creative_focus"] = str(
            packet.get("creative_focus")
            or sections.get("创作罗盘（当前最高优先级）")
            or payload["creative_focus"]
        )
        payload["chapter_summaries"] = str(
            sections.get("历史章节记忆") or payload["chapter_summaries"]
        )
        outline = str(packet.get("outline") or "").strip() or _render_outline(sections)
        if outline:
            payload["outline"] = outline
        style = _style_profile(styles, 4000)
        if style:
            payload["style_profile"] = style
        active = _characters(packet.get("character_documents", {}))
        if active:
            payload["active_characters"] = active
        for key in ("current_state", "ledger", "relationships"):
            payload[key] = str(concepts.get(key) or payload.get(key) or "")
        payload["foreshadowing_summary"] = str(
            concepts.get("pending_hooks") or payload["foreshadowing_summary"]
        )
        payload["recent_chapters"] = str(packet.get("previous_chapter_content") or "")
        extra = []
        for label, value in (
            ("故事背景", packet.get("story_background")),
            ("基础设定", packet.get("foundation")),
            ("世界规则", concepts.get("world_rules")),
            ("额外要求", guidance),
        ):
            text = str(value or "").strip()
            if text:
                extra.append(f"## {label}\n{text}")
        if extra:
            payload["external_context"] = "\n\n".join(extra)
    elif guidance:
        payload["external_context"] = guidance
    return cast(
        dict[str, Any],
        normalize_context_payload(payload, include_aliases=False),
    )


def build_review_payload(packet: dict[str, Any]) -> dict[str, Any]:
    concepts = packet.get("concept_documents", {})
    sections = packet.get("prompt_sections", {})
    if not isinstance(concepts, dict):
        concepts = {}
    if not isinstance(sections, dict):
        sections = {}
    characters = packet.get("character_documents", {})
    character_text = (
        "\n\n".join(str(item) for item in characters.values())
        if isinstance(characters, dict)
        else ""
    )
    outline = _render_outline(sections)
    if not outline:
        section_items = packet.get("current_arc_sections")
        if isinstance(section_items, list):
            outline_parts = []
            for item in section_items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("section_id") or "").strip()
                summary = str(item.get("summary") or "").strip()
                if title or summary:
                    outline_parts.append(f"## {title}\n{summary}".strip())
            outline = "\n\n".join(outline_parts)
    if not outline:
        outline = str(packet.get("outline") or "").strip()
    payload: dict[str, Any] = {
        "target_words": int(packet.get("target_words") or 0),
        "character_profiles": character_text[:4000],
        "current_state": str(
            concepts.get("current_state") or packet.get("current_state") or ""
        ),
        "relationships": str(
            concepts.get("relationships") or packet.get("relationships") or ""
        ),
        "author_intent": str(
            packet.get("author_intent") or sections.get("作者意图") or ""
        ),
        "creative_focus": str(
            packet.get("creative_focus")
            or sections.get("创作罗盘（当前最高优先级）")
            or ""
        ),
    }
    if outline:
        payload["outline"] = outline[:4000]
    style = _style_profile(packet.get("style_documents", {}), 2500)
    if style:
        payload["style_profile"] = style[:2000]
    previous = str(packet.get("previous_chapter_content") or "").strip()
    if previous:
        payload["recent_chapters"] = previous[:2000]
    return {key: value for key, value in payload.items() if value}


def _collect_truth_updates(state_updates: Any) -> dict[str, str]:
    if not isinstance(state_updates, dict):
        return {}
    aliases = {
        "current_state": "current_state",
        "particle_ledger": "ledger",
        "ledger": "ledger",
        "character_matrix": "relationships",
        "relationships": "relationships",
    }
    return {
        aliases[key]: value
        for key, value in state_updates.items()
        if key in aliases and isinstance(value, str) and value.strip()
    }


def _sync_book_state(
    project_root: Path,
    novel_id: str,
    chapter_id: str,
    review_passed: bool | None,
) -> None:
    from tools.agent.book_state import BookStage, BookStateStore

    store = BookStateStore(project_root, novel_id)
    state = store.load_or_create()
    if _chapter_number(chapter_id) >= _chapter_number(state.current_chapter):
        state.current_chapter = chapter_id
    if review_passed is None:
        state.stage = BookStage.REVIEW_AND_REVISE
        state.blocking_reason = "review_not_run"
        state.last_agent_action = "write_pending_review"
    elif review_passed:
        state.stage = BookStage.CHAPTER_PREFLIGHT
        state.blocking_reason = ""
        state.last_agent_action = "review_review_passed"
    else:
        state.stage = BookStage.REVIEW_AND_REVISE
        state.blocking_reason = "review_revision_requested"
        state.last_agent_action = "review_review_failed"
    store.save(state)


class _TaskCancellationRequested(RuntimeError):
    pass


def _task_phase(args: dict[str, Any], phase: str, note: str = "") -> None:
    callback = args.get("_task_phase")
    if callable(callback):
        callback(phase, note)


def _raise_if_task_cancelled(args: dict[str, Any]) -> None:
    callback = args.get("_cancel_requested")
    if callable(callback) and callback():
        raise _TaskCancellationRequested("任务已取消，模型结果未提交")


def _load_resumable_writer_result(store: Any, manifest: Any) -> SimpleNamespace:
    draft_stage = manifest.stages["draft"]
    fact_stage = manifest.stages["fact_extract"]
    if draft_stage.status != "completed" or fact_stage.status != "completed":
        from tools.chapter_run_v2 import ChapterRunV2Error

        raise ChapterRunV2Error(
            "仅能从已完成 draft 与 fact_extract 的运行恢复",
            code="RUN_NOT_RESUMABLE",
        )
    draft = store.read_artifact(draft_stage.artifact)
    facts = store.read_artifact(fact_stage.artifact)
    if not isinstance(draft, dict) or not isinstance(facts, dict):
        from tools.chapter_run_v2 import ChapterRunV2Error

        raise ChapterRunV2Error("恢复 artifact 结构无效", code="INVALID_ARTIFACT")
    return SimpleNamespace(
        title=str(draft.get("title") or ""),
        content=str(draft.get("content") or ""),
        word_count=int(draft.get("word_count") or 0),
        state_updates=dict(facts.get("legacy_updates") or {}),
        state_delta=dict(facts.get("state_delta") or {}),
        chapter_summary=str(draft.get("chapter_summary") or ""),
        observations=str(draft.get("observations") or ""),
        token_usage=dict(draft.get("token_usage") or {}),
    )


def execute_write_chapter(project_root: Path, args: dict[str, Any]) -> dict[str, Any]:
    from tools.agent import AgentContext, WriterAgent
    from tools.chapter_memory import ChapterMemoryStore
    from tools.chapter_run_store import ChapterRunStore
    from tools.chapter_run_v2 import ChapterRunV2Store
    from tools.context_builder import ContextBuilder
    from tools.llm import LLMClient, LLMConfig
    from tools.project_lock import ProjectBusyError, ProjectWriteLock
    from tools.truth_manager import TruthFilesManager
    from tools.workflow_scheduler import WorkflowScheduler

    project_root = Path(project_root).resolve()
    config = _load_config(project_root)
    if not config:
        return {"ok": False, "error": "未找到项目配置", "code": "INVALID_PROJECT"}
    novel_id = str(config.get("novel_id") or "")
    chapter_id = str(args.get("chapter_id") or "ch_001")
    packet = args.get("context_packet")
    packet = packet if isinstance(packet, dict) else {}
    target_words = int(args.get("target_words") or 0)
    scheduler = WorkflowScheduler(project_root, novel_id)
    workflow = scheduler.load_or_create(chapter_id)
    active_stage = ""
    run_store = ChapterRunStore(project_root, novel_id)
    run_manifest = None
    run_v2_store = ChapterRunV2Store(project_root, novel_id)
    requested_run_v2_id = str(args.get("run_id_v2") or "").strip()
    run_v2_manifest = (
        run_v2_store.load(requested_run_v2_id) if requested_run_v2_id else None
    )
    if requested_run_v2_id and run_v2_manifest is None:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "未找到指定的 Chapter Run V2",
            "code": "INVALID_CHAPTER_RUN",
        }
    if run_v2_manifest is not None and run_v2_manifest.chapter_id != chapter_id:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "Chapter Run V2 与待写章节不匹配",
            "code": "INVALID_CHAPTER_RUN",
        }
    if run_v2_manifest is not None and (
        run_v2_manifest.cancel_requested or run_v2_manifest.status == "cancelled"
    ):
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "已取消的 Chapter Run V2 不能恢复",
            "code": "RUN_CANCELLED",
        }
    run_v2_active_stage = ""
    try:
        with ProjectWriteLock(project_root, novel_id, operation=f"write:{chapter_id}"):
            _task_phase(args, "reading", "读取章节上下文")
            _raise_if_task_cancelled(args)
            active_stage = "context_assembly"
            scheduler.start_stage(workflow, active_stage)
            context = ContextBuilder(project_root, novel_id).build_generation_context(
                chapter_id
            )
            truth_manager = TruthFilesManager(project_root, novel_id)
            truth = truth_manager.load_truth_files()
            baseline_state = truth_manager.load_runtime_state()
            scheduler.complete_stage(
                workflow,
                active_stage,
                message="canonical context assembled",
                data={"chapter_id": chapter_id},
            )
            _task_phase(args, "preparing", "准备写作请求")
            _raise_if_task_cancelled(args)
            active_stage = "writing"
            scheduler.start_stage(workflow, active_stage)
            llm_config = LLMConfig.from_env()
            request_config = configure_writer_llm(llm_config)
            outline_target_words = int(getattr(context, "target_words", 0) or 0)
            effective_target_words = (
                run_v2_manifest.effective_target_words
                if run_v2_manifest is not None
                else target_words or outline_target_words or 6000
            )
            writer = WriterAgent(
                AgentContext(LLMClient(llm_config), llm_config.model, str(project_root))
            )
            writer_payload = build_writer_payload(
                context=context,
                truth=truth,
                packet=packet,
                guidance=str(args.get("guidance") or "").strip(),
                target_words=effective_target_words,
            )
            run_manifest = run_store.create(
                chapter_id,
                requested_target_words=target_words,
                outline_target_words=outline_target_words,
                effective_target_words=effective_target_words,
                provider=str(getattr(llm_config, "provider", "")),
                model=str(getattr(llm_config, "model", "")),
                context_payload={
                    **writer_payload,
                    "context_packet": packet,
                    "request_config": request_config,
                },
                baseline_state_revision=int(getattr(baseline_state, "revision", 0) or 0),
            )
            current_revisions = {
                "context": run_v2_store.content_revision(writer_payload),
                "outline": run_v2_store.content_revision(
                    writer_payload.get("chapter_goals", [])
                ),
                "runtime_state": str(
                    getattr(baseline_state, "revision", 0) or 0
                ),
                "prompt": "writer-creative-v1",
            }
            if run_v2_manifest is None:
                run_v2_manifest = run_v2_store.create(
                    chapter_id,
                    requested_target_words=target_words,
                    effective_target_words=effective_target_words,
                    provider=str(getattr(llm_config, "provider", "")),
                    model=str(getattr(llm_config, "model", "")),
                    model_profile=str(getattr(llm_config, "model", "")),
                    input_revisions=current_revisions,
                )
            else:
                stored_context = run_v2_store.read_artifact(
                    run_v2_manifest.stages["context"].artifact
                )
                if not isinstance(stored_context, dict):
                    raise ValueError("Chapter Run V2 context artifact 无效")
                comparable_context = dict(writer_payload)
                for key in ("target_words", "external_context"):
                    if key in stored_context:
                        comparable_context[key] = stored_context[key]
                    else:
                        comparable_context.pop(key, None)
                current_revisions["context"] = run_v2_store.content_revision(
                    comparable_context
                )
                stale = run_v2_store.mark_stale(
                    run_v2_manifest,
                    {
                        **run_v2_manifest.input_revisions,
                        **current_revisions,
                    },
                )
                if any(
                    stage in stale
                    for stage in ("context", "plan", "draft", "fact_extract")
                ):
                    from tools.chapter_run_v2 import ChapterRunV2Error

                    raise ChapterRunV2Error(
                        "运行输入已变化，不能复用旧草稿",
                        code="RESUME_INPUT_STALE",
                    )
                writer_payload = stored_context
            run_v2_active_stage = "context"
            if run_v2_manifest.stages["context"].status != "completed":
                run_v2_store.start_stage(
                    run_v2_manifest,
                    "context",
                    input_revisions={
                        "context": run_v2_manifest.input_revisions["context"],
                        "runtime_state": run_v2_manifest.input_revisions[
                            "runtime_state"
                        ],
                    },
                )
                context_artifact = run_v2_store.write_artifact(
                    run_v2_manifest, "context", writer_payload
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "context",
                    output=writer_payload,
                    artifact=context_artifact,
                )
            run_v2_active_stage = "plan"
            if run_v2_manifest.stages["plan"].status != "completed":
                run_v2_store.start_stage(
                    run_v2_manifest,
                    "plan",
                    input_revisions={
                        "outline": run_v2_manifest.input_revisions["outline"]
                    },
                    prompt_version="writer-plan-v1",
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "plan",
                    output={
                        "chapter_id": chapter_id,
                        "goals": writer_payload.get("chapter_goals", []),
                        "guidance": writer_payload.get("guidance", ""),
                    },
                )
            run_v2_active_stage = "draft"
            if run_v2_manifest.stages["draft"].status == "completed":
                result = _load_resumable_writer_result(
                    run_v2_store, run_v2_manifest
                )
            else:
                run_v2_store.start_stage(
                    run_v2_manifest,
                    "draft",
                    prompt_version="writer-creative-v1",
                    model_profile=str(getattr(llm_config, "model", "")),
                )
                _task_phase(args, "model", "生成章节草稿")
                result = asyncio.run(
                    writer.write_chapter(
                        context=writer_payload,
                        chapter_number=_chapter_number(chapter_id) or 1,
                        temperature=float(args.get("temperature") or 0.7),
                        target_words=writer_payload.get("target_words") or None,
                    )
                )
                _task_phase(args, "validating", "校验模型结果")
                _raise_if_task_cancelled(args)
                run_v2_store.assert_accepts_result(run_v2_manifest)
                draft_artifact = run_v2_store.write_artifact(
                    run_v2_manifest,
                    "draft",
                    {
                        "title": result.title,
                        "content": result.content,
                        "word_count": result.word_count,
                        "chapter_summary": str(
                            getattr(result, "chapter_summary", "") or ""
                        ),
                        "observations": str(
                            getattr(result, "observations", "") or ""
                        ),
                        "token_usage": dict(
                            getattr(result, "token_usage", {}) or {}
                        ),
                    },
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "draft",
                    output={"title": result.title, "content": result.content},
                    artifact=draft_artifact,
                    usage=dict(getattr(result, "token_usage", {}) or {}),
                )
            _task_phase(args, "validating", "校验模型结果")
            _raise_if_task_cancelled(args)
            updates = _collect_truth_updates(getattr(result, "state_updates", {}))
            state_delta = getattr(result, "state_delta", {})
            known_entities = [
                str(item.get("name") or "").strip()
                for item in writer_payload.get("active_characters", [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
            run_v2_active_stage = "fact_extract"
            if run_v2_manifest.stages["fact_extract"].status != "completed":
                run_v2_store.start_stage(
                    run_v2_manifest,
                    "fact_extract",
                    prompt_version="runtime-delta-v1",
                )
                fact_artifact = run_v2_store.write_artifact(
                    run_v2_manifest,
                    "fact_extract",
                    {"state_delta": state_delta, "legacy_updates": updates},
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "fact_extract",
                    output={"state_delta": state_delta, "legacy_updates": updates},
                    artifact=fact_artifact,
                )
            snapshot = truth_manager.create_snapshot(max(_chapter_number(chapter_id) - 1, 0))
            memory = ChapterMemoryStore(project_root, novel_id)
            draft_path = _chapter_path(project_root, novel_id, chapter_id)
            memory_path = memory.path_for(chapter_id)
            previous_draft = draft_path.read_bytes() if draft_path.is_file() else None
            previous_memory = memory_path.read_bytes() if memory_path.is_file() else None
            try:
                _task_phase(args, "committing", "原子提交章节与运行态")
                _save_chapter(
                    project_root, novel_id, chapter_id, result.title, result.content
                )
                run_v2_active_stage = "settle"
                run_v2_store.start_stage(
                    run_v2_manifest,
                    "settle",
                    prompt_version="runtime-delta-v1",
                )
                state_delta, state_delta_fallback = apply_runtime_delta_with_fallback(
                    truth_manager,
                    state_delta,
                    updates,
                    chapter_id=chapter_id,
                    known_entities=known_entities,
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "settle",
                    output={
                        "state_delta": state_delta,
                        "fallback": state_delta_fallback,
                    },
                )
                memory.save(
                    chapter_id=chapter_id,
                    title=result.title,
                    summary=str(getattr(result, "chapter_summary", "") or ""),
                    word_count=int(getattr(result, "word_count", 0) or 0),
                    observations=str(getattr(result, "observations", "") or ""),
                    token_usage=dict(getattr(result, "token_usage", {}) or {}),
                )
                run_v2_active_stage = "validate"
                run_v2_store.start_stage(
                    run_v2_manifest,
                    "validate",
                    prompt_version="post-write-v1",
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "validate",
                    output={
                        "chapter_id": chapter_id,
                        "word_count": int(getattr(result, "word_count", 0) or 0),
                        "runtime_state": int(
                            getattr(truth_manager.load_runtime_state(), "revision", 0)
                            or 0
                        ),
                    },
                )
                run_v2_active_stage = "commit"
                run_v2_store.start_stage(run_v2_manifest, "commit")
                scheduler.complete_stage(
                    workflow,
                    active_stage,
                    message="chapter committed",
                    data={"draft_path": str(draft_path)},
                )
                _sync_book_state(project_root, novel_id, chapter_id, None)
                run_store.complete_write(
                    run_manifest,
                    draft_content=result.content,
                    usage=dict(getattr(result, "token_usage", {}) or {}),
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "commit",
                    output={
                        "draft_revision": run_store.text_revision(result.content),
                        "runtime_state_revision": int(
                            getattr(truth_manager.load_runtime_state(), "revision", 0)
                            or 0
                        ),
                    },
                    artifact=str(draft_path),
                )
                active_stage = ""
                run_v2_active_stage = ""
            except Exception:
                truth_manager.restore_snapshot(snapshot)
                _restore(draft_path, previous_draft)
                _restore(memory_path, previous_memory)
                if run_v2_manifest is not None:
                    run_v2_store.invalidate_from(
                        run_v2_manifest,
                        "settle",
                        reason="transaction_rollback",
                    )
                raise
            response_payload = {
                "ok": True,
                "chapter_id": chapter_id,
                "title": result.title,
                "word_count": result.word_count,
                "draft_path": str(draft_path),
                "truth_updates": updates,
                "run_id_v2": run_v2_manifest.run_id,
                "usage": dict(getattr(result, "token_usage", {}) or {}),
            }
            if state_delta_fallback:
                response_payload["state_delta"] = state_delta
                response_payload["state_delta_fallback"] = state_delta_fallback
            return response_payload
    except _TaskCancellationRequested as exc:
        if active_stage:
            scheduler.fail_stage(workflow, active_stage, str(exc))
        if run_v2_manifest is not None:
            try:
                run_v2_store.request_cancel(
                    run_v2_manifest, reason="write_cancelled"
                )
                if run_v2_active_stage:
                    run_v2_store.fail_stage(
                        run_v2_manifest,
                        run_v2_active_stage,
                        code="TASK_CANCELLED",
                        message=str(exc),
                    )
                run_v2_store.finalize_cancel(run_v2_manifest)
            except Exception:
                pass
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": str(exc),
            "code": "TASK_CANCELLED",
        }
    except ProjectBusyError as exc:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": str(exc),
            "code": "PROJECT_BUSY",
        }
    except Exception as exc:
        if run_manifest is not None:
            try:
                run_store.fail(run_manifest, stage=active_stage or "write")
            except Exception:
                pass
        if run_v2_manifest is not None and run_v2_active_stage:
            try:
                run_v2_store.fail_stage(
                    run_v2_manifest,
                    run_v2_active_stage,
                    code=str(getattr(exc, "code", "RUN_FAILED") or "RUN_FAILED"),
                    message=str(exc),
                )
            except Exception:
                pass
        if active_stage:
            scheduler.fail_stage(workflow, active_stage, str(exc))
        return {"ok": False, "chapter_id": chapter_id, "error": str(exc)}


def execute_review_chapter(project_root: Path, args: dict[str, Any]) -> dict[str, Any]:
    from tools.agent import AgentContext, ReviewerAgent
    from tools.chapter_assembler import ChapterAssemblerV2
    from tools.chapter_run_store import ChapterRunStore
    from tools.chapter_run_v2 import ChapterRunV2Store
    from tools.llm import LLMClient, LLMConfig
    from tools.project_lock import ProjectBusyError, ProjectWriteLock
    from tools.review_store import ReviewStore
    from tools.truth_manager import TruthFilesManager
    from tools.workflow_scheduler import WorkflowScheduler

    project_root = Path(project_root).resolve()
    config = _load_config(project_root)
    if not config:
        return {"ok": False, "error": "未找到项目配置", "code": "INVALID_PROJECT"}
    novel_id = str(config.get("novel_id") or "")
    chapter_id = str(args.get("chapter_id") or "ch_001")
    scheduler = WorkflowScheduler(project_root, novel_id)
    workflow = scheduler.load_or_create(chapter_id)
    run_store = ChapterRunStore(project_root, novel_id)
    run_v2_store = ChapterRunV2Store(project_root, novel_id)
    requested_run_id = str(args.get("run_id") or "").strip()
    run_manifest = (
        run_store.load(requested_run_id)
        if requested_run_id
        else run_store.latest_for_chapter(
            chapter_id, statuses={"written", "reviewed"}
        )
    )
    if requested_run_id and run_manifest is None:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "未找到指定的章节运行记录",
            "code": "INVALID_CHAPTER_RUN",
        }
    if run_manifest is not None and run_manifest.chapter_id != chapter_id:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "章节运行记录与待审章节不匹配",
            "code": "INVALID_CHAPTER_RUN",
        }
    requested_run_v2_id = str(args.get("run_id_v2") or "").strip()
    run_v2_manifest = (
        run_v2_store.load(requested_run_v2_id)
        if requested_run_v2_id
        else run_v2_store.latest_reviewable_for_chapter(chapter_id)
    )
    if requested_run_v2_id and run_v2_manifest is None:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "未找到指定的 Chapter Run V2 记录",
            "code": "INVALID_CHAPTER_RUN",
        }
    if run_v2_manifest is not None and run_v2_manifest.chapter_id != chapter_id:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "Chapter Run V2 与待审章节不匹配",
            "code": "INVALID_CHAPTER_RUN",
        }
    if requested_run_v2_id and run_v2_manifest is not None and (
        run_v2_manifest.status not in {"committed", "reviewed"}
    ):
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": "指定的 Chapter Run V2 尚未提交，不能用于审稿",
            "code": "CHAPTER_RUN_NOT_REVIEWABLE",
        }
    try:
        with ProjectWriteLock(project_root, novel_id, operation=f"review:{chapter_id}"):
            _task_phase(args, "reading", "读取章节正文")
            _raise_if_task_cancelled(args)
            content = _load_chapter(project_root, novel_id, chapter_id)
            if not content:
                return {
                    "ok": False,
                    "error": f"未找到章节: {chapter_id}",
                    "code": "NOT_FOUND",
                }
            scheduler.start_stage(workflow, "review")
            _task_phase(args, "preparing", "组装审稿上下文")
            llm_config = LLMConfig.from_env()
            reviewer = ReviewerAgent(
                AgentContext(LLMClient(llm_config), llm_config.model, str(project_root))
            )
            packet = ChapterAssemblerV2(
                project_root=project_root,
                novel_id=novel_id,
                style_id=str(config.get("style_id") or novel_id),
            ).assemble(chapter_id)
            review_context = build_review_payload(_packet_payload(packet))
            if run_manifest is not None and run_manifest.effective_target_words > 0:
                review_context["target_words"] = run_manifest.effective_target_words
            prewrite = TruthFilesManager(project_root, novel_id).load_snapshot_before(
                _chapter_number(chapter_id)
            )
            if prewrite is not None:
                review_context["current_state"] = prewrite.current_state
                review_context["relationships"] = prewrite.relationships
            if run_v2_manifest is not None:
                run_v2_store.start_stage(
                    run_v2_manifest,
                    "review",
                    input_revisions={
                        "chapter": run_v2_store.content_revision(content),
                        "review_context": run_v2_store.content_revision(review_context),
                    },
                    prompt_version="reviewer-v1",
                    model_profile=str(getattr(llm_config, "model", "")),
                )
            _task_phase(args, "model", "执行章节审稿")
            result = asyncio.run(reviewer.review(content=content, context=review_context))
            if run_v2_manifest is not None:
                run_v2_store.assert_accepts_result(run_v2_manifest)
            issues: list[dict[str, Any]] = [
                {
                    "severity": str(getattr(issue, "severity", "warning")),
                    "category": str(getattr(issue, "category", "未知")),
                    "description": str(getattr(issue, "description", "")),
                    "suggestion": str(getattr(issue, "suggestion", "")),
                    "dimension": getattr(issue, "dimension", None),
                }
                for issue in result.issues
            ]
            payload = {
                "ok": True,
                "chapter_id": chapter_id,
                "passed": bool(result.passed),
                "score": result.score,
                "issues": len(issues),
                "summary": str(getattr(result, "summary", "") or ""),
                "issue_details": issues,
                "run_id": run_manifest.run_id if run_manifest else "",
                "run_id_v2": run_v2_manifest.run_id if run_v2_manifest else "",
                "effective_target_words": int(review_context.get("target_words") or 0),
            }
            _task_phase(args, "validating", "整理审稿问题")
            _raise_if_task_cancelled(args)
            _task_phase(args, "committing", "保存审稿结果")
            ReviewStore(project_root, novel_id).save(chapter_id, payload)
            scheduler.complete_stage(
                workflow,
                "review",
                message="chapter reviewed",
                data={
                    "passed": bool(result.passed),
                    "errors": [
                        item["description"]
                        for item in issues
                        if item["severity"].lower() == "critical"
                    ],
                    "warnings": [
                        item["description"]
                        for item in issues
                        if item["severity"].lower() != "critical"
                    ],
                },
            )
            _sync_book_state(project_root, novel_id, chapter_id, bool(result.passed))
            if run_manifest is not None:
                run_store.complete_review(run_manifest, review_payload=payload)
            if run_v2_manifest is not None:
                review_artifact = run_v2_store.write_artifact(
                    run_v2_manifest, "review", payload
                )
                run_v2_store.complete_stage(
                    run_v2_manifest,
                    "review",
                    output=payload,
                    artifact=review_artifact,
                )
            return payload
    except _TaskCancellationRequested as exc:
        scheduler.fail_stage(workflow, "review", str(exc))
        if run_v2_manifest is not None:
            run_v2_store.request_cancel(run_v2_manifest, reason="review_cancelled")
            run_v2_store.fail_stage(
                run_v2_manifest,
                "review",
                code="TASK_CANCELLED",
                message=str(exc),
            )
            run_v2_store.finalize_cancel(run_v2_manifest)
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": str(exc),
            "code": "TASK_CANCELLED",
        }
    except ProjectBusyError as exc:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": str(exc),
            "code": "PROJECT_BUSY",
        }
    except Exception as exc:
        scheduler.fail_stage(workflow, "review", str(exc))
        if run_v2_manifest is not None:
            try:
                run_v2_store.fail_stage(
                    run_v2_manifest,
                    "review",
                    code=str(getattr(exc, "code", "REVIEW_FAILED") or "REVIEW_FAILED"),
                    message=str(exc),
                )
            except Exception:
                pass
        return {"ok": False, "chapter_id": chapter_id, "error": str(exc)}


def execute_multi_agent_chapter(
    project_root: Path,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Run the director/writer/reviewer chapter flow behind the same lock boundary."""
    from tools.agent import AgentContext, MultiAgentDirector
    from tools.llm import LLMClient, LLMConfig
    from tools.project_lock import ProjectBusyError, ProjectWriteLock
    from tools.review_store import ReviewStore
    from tools.workflow_scheduler import WorkflowScheduler

    project_root = Path(project_root).resolve()
    config = _load_config(project_root)
    if not config:
        return {"ok": False, "error": "未找到项目配置", "code": "INVALID_PROJECT"}
    novel_id = str(config.get("novel_id") or "")
    chapter_id = str(args.get("chapter_id") or "ch_001")
    scheduler = WorkflowScheduler(project_root, novel_id)
    workflow = scheduler.load_or_create(chapter_id)
    active_stage = ""
    try:
        with ProjectWriteLock(
            project_root,
            novel_id,
            operation=f"multi-write:{chapter_id}",
        ):
            llm_config = LLMConfig.from_env()
            director = MultiAgentDirector(
                AgentContext(
                    LLMClient(llm_config), llm_config.model, str(project_root)
                ),
                novel_id=novel_id,
                style_id=str(config.get("style_id") or novel_id),
            )
            active_stage = "context_assembly"
            scheduler.start_stage(workflow, active_stage)
            packet_markdown = ""
            packet_path = ""
            if bool(args.get("show_packet")):
                packet = director.assemble_packet(chapter_id)
                packet_markdown = packet.to_markdown()
                output_dir = (
                    Path(str(args["packet_output_dir"]))
                    if args.get("packet_output_dir")
                    else project_root
                    / "data"
                    / "novels"
                    / novel_id
                    / "data"
                    / "test_outputs"
                    / "multi_write"
                )
                output_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target = output_dir / f"{chapter_id}_packet_{stamp}.md"
                target.write_text(packet_markdown, encoding="utf-8")
                packet_path = str(target)
            scheduler.complete_stage(
                workflow,
                active_stage,
                message=(
                    "packet assembled for multi-write"
                    if packet_markdown
                    else "packet assembly delegated to multi-write director"
                ),
                data={"chapter_id": chapter_id},
            )
            active_stage = "writing"
            scheduler.start_stage(workflow, active_stage)
            result = asyncio.run(
                director.run(
                    chapter_id=chapter_id,
                    temperature=float(args.get("temperature") or 0.7),
                    run_review=not bool(args.get("no_review")),
                )
            )
            if not result.draft:
                raise RuntimeError("未生成草稿")
            draft_path = _save_chapter(
                project_root,
                novel_id,
                chapter_id,
                result.draft.title,
                result.draft.content,
            )
            scheduler.complete_stage(
                workflow,
                active_stage,
                message="chapter written via multi-write",
                data={"draft_path": str(draft_path)},
            )
            active_stage = ""
            review_payload: dict[str, Any] | None = None
            if result.review:
                issues: list[dict[str, Any]] = [
                    {
                        "severity": str(getattr(issue, "severity", "warning")),
                        "category": str(getattr(issue, "category", "未知")),
                        "description": str(getattr(issue, "description", "")),
                        "suggestion": str(getattr(issue, "suggestion", "")),
                        "dimension": getattr(issue, "dimension", None),
                    }
                    for issue in result.review.issues
                ]
                review_payload = {
                    "ok": True,
                    "chapter_id": chapter_id,
                    "passed": bool(result.review.passed),
                    "score": result.review.score,
                    "issues": len(issues),
                    "summary": str(getattr(result.review, "summary", "") or ""),
                    "issue_details": issues,
                }
                ReviewStore(project_root, novel_id).save(chapter_id, review_payload)
                scheduler.start_stage(workflow, "review")
                scheduler.complete_stage(
                    workflow,
                    "review",
                    message="chapter reviewed via multi-write",
                    data={
                        "passed": bool(result.review.passed),
                        "errors": [
                            item["description"]
                            for item in issues
                            if item["severity"].lower() == "critical"
                        ],
                        "warnings": [
                            item["description"]
                            for item in issues
                            if item["severity"].lower() != "critical"
                        ],
                    },
                )
            _sync_book_state(
                project_root,
                novel_id,
                chapter_id,
                bool(result.review.passed) if result.review else None,
            )
            return {
                "ok": True,
                "chapter_id": chapter_id,
                "title": result.draft.title,
                "draft_path": str(draft_path),
                "review": review_payload,
                "applied_state_updates": dict(result.applied_state_updates or {}),
                "new_concepts": list(result.new_concepts or []),
                "packet_markdown": packet_markdown,
                "packet_path": packet_path,
            }
    except ProjectBusyError as exc:
        return {
            "ok": False,
            "chapter_id": chapter_id,
            "error": str(exc),
            "code": "PROJECT_BUSY",
        }
    except Exception as exc:
        if active_stage:
            scheduler.fail_stage(workflow, active_stage, str(exc))
        return {"ok": False, "chapter_id": chapter_id, "error": str(exc)}
