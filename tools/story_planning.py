"""小说立项与滚动大纲草案存储。

这个模块负责 Goethe 侧的 planning 真源与运行态草案：
- `data/planning/*` 记录会话中间产物和未确认内容
- `src/story/*` 与 `src/outline.md` 记录当前 canonical 资产

这里的核心约束不是“保存更多文件”，而是维持草案、确认版和 handoff 之间的镜像关系，
让 Goethe/Dante/CLI 看到的是同一套资产，而不是并行维护多份内容。
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .frontmatter import compose_toml_document, parse_toml_front_matter, strip_front_matter_padding


class StoryPlanningStore:
    """管理立项聊天、基础设定和滚动大纲的草案文件。

    它本身不决定“该写什么”，只负责把 planning 流中的文本资产放到正确位置：
    - ideation / summary 作为会话沉淀
    - background / foundation / outline 作为可晋升的故事资产
    - handoff 文件作为 Goethe -> Dante 的交接记录
    """

    def __init__(self, project_root: Path, novel_id: str):
        self.project_root = Path(project_root).resolve()
        self.novel_id = novel_id
        self.novel_root = self.project_root / "data" / "novels" / novel_id
        self.runtime_planning_dir = self.novel_root / "data" / "planning"
        self.workflow_dir = self.novel_root / "data" / "workflows"
        self.story_src_dir = self.novel_root / "src" / "story"
        self.outline_src_path = self.novel_root / "src" / "outline.md"

        self.ideation_path = self.runtime_planning_dir / "ideation.md"
        self.ideation_summary_path = self.runtime_planning_dir / "ideation_summary.md"
        self.background_draft_path = self.runtime_planning_dir / "background_draft.md"
        self.foundation_draft_path = self.runtime_planning_dir / "foundation_draft.md"
        self.volume_outline_draft_path = (
            self.runtime_planning_dir / "volume_outline_draft.md"
        )
        self.current_state_draft_path = (
            self.runtime_planning_dir / "current_state_draft.md"
        )
        self.foreshadowing_draft_path = (
            self.runtime_planning_dir / "foreshadowing_draft.yaml"
        )
        self.outline_draft_path = self.runtime_planning_dir / "outline_draft.md"
        self.outline_edit_state_path = self.runtime_planning_dir / "outline_edit.yaml"
        self.goethe_handoff_md_path = self.workflow_dir / "goethe_handoff.md"
        self.goethe_handoff_yaml_path = self.workflow_dir / "goethe_handoff.yaml"

    def append_ideation(self, text: str) -> None:
        """追加一段原始灵感记录，不做结构化改写。"""
        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        previous = (
            self.ideation_path.read_text(encoding="utf-8")
            if self.ideation_path.exists()
            else ""
        )
        content = previous.rstrip("\n")
        if content:
            content += "\n"
        content += text
        self.ideation_path.write_text(content.rstrip("\n") + "\n", encoding="utf-8")

    def save_ideation_summary(self, text: str) -> None:
        """保存 ideation 的结构化汇总，并记录与原始 ideation 的对应哈希。"""
        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        ideation = self.ideation_path.read_text(encoding="utf-8") if self.ideation_path.exists() else ""
        source_hash = self._hash_text(ideation)
        meta, body = parse_toml_front_matter(text)
        normalized_body = strip_front_matter_padding(body if meta else text).strip()
        normalized_meta = dict(meta) if meta else {}
        # summary 文档既给人读，也给 agent 做“这份总结是否已过期”的快速判断。
        normalized_meta.setdefault("id", "ideation_summary")
        normalized_meta.setdefault("type", "planning_summary")
        normalized_meta.setdefault("source", "ideation")
        normalized_meta["source_hash"] = source_hash
        normalized_meta.setdefault("summary", self._extract_story_summary(normalized_body))
        normalized_meta.setdefault(
            "detail_refs",
            ["核心方向", "稳定共识", "待确认点", "开放问题", "下一步"],
        )
        self.ideation_summary_path.write_text(
            compose_toml_document(normalized_meta, normalized_body),
            encoding="utf-8",
        )

    def ideation_summary_is_current(self) -> bool:
        """判断 ideation summary 是否仍然覆盖了最新的 ideation 原文。"""
        if not self.ideation_path.exists():
            return not self.ideation_summary_path.exists()
        if not self.ideation_summary_path.exists():
            return False
        meta, body = parse_toml_front_matter(
            self.ideation_summary_path.read_text(encoding="utf-8")
        )
        if not body.strip():
            return False
        current_hash = self._hash_text(self.ideation_path.read_text(encoding="utf-8"))
        return str(meta.get("source_hash", "")).strip() == current_hash

    def read_ideation_summary(self, max_chars: int = 0) -> str:
        if not self.ideation_summary_path.exists():
            return ""
        text = self.ideation_summary_path.read_text(encoding="utf-8")
        meta, body = parse_toml_front_matter(text)
        normalized_body = strip_front_matter_padding(body if meta else text)
        parts = []
        summary = str(meta.get("summary", "")).strip()
        detail_refs = meta.get("detail_refs", [])
        if summary:
            parts.append(f"摘要：{summary}")
        if isinstance(detail_refs, list) and detail_refs:
            parts.append("细节索引：" + "、".join(str(item) for item in detail_refs))
        if normalized_body:
            parts.append(normalized_body)
        rendered = "\n".join(parts).strip()
        if max_chars and len(rendered) > max_chars:
            return rendered[:max_chars]
        return rendered

    def seed_placeholder_foundation_from_ideation_summary(self) -> bool:
        """Persist a confirmed summary when the foundation is still a template."""
        if not self.ideation_summary_is_current():
            return False

        foundation_path = self.story_src_dir / "foundation.md"
        existing = (
            foundation_path.read_text(encoding="utf-8")
            if foundation_path.exists()
            else ""
        )
        if self._story_document_has_content(existing):
            return False

        summary_text = self.ideation_summary_path.read_text(encoding="utf-8")
        summary_meta, summary_body = parse_toml_front_matter(summary_text)
        summary = strip_front_matter_padding(
            summary_body if summary_meta else summary_text
        ).strip()
        if not summary:
            return False

        content = self._normalize_story_document(
            "foundation",
            "# 基础设定\n\n## 已确认想法汇总\n\n" + summary,
        )
        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        self.story_src_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(self.foundation_draft_path, content)
        self._atomic_write_text(foundation_path, content)
        return True

    def save_foundation_draft(
        self,
        background: str,
        foundation: str,
        *,
        volume_outline: str = "",
        current_state: str = "",
        foreshadowing: str | dict[str, Any] | None = None,
    ) -> None:
        """保存基础设定草案；确认前不修改 canonical ``src/story/*``。"""
        graph = (
            self._parse_foreshadowing_graph(foreshadowing)
            if foreshadowing not in (None, "")
            else None
        )
        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        background_content = self._normalize_story_document("background", background)
        foundation_content = self._normalize_story_document("foundation", foundation)
        self._atomic_write_text(self.background_draft_path, background_content)
        self._atomic_write_text(self.foundation_draft_path, foundation_content)
        if str(volume_outline or "").strip():
            self._atomic_write_text(
                self.volume_outline_draft_path,
                str(volume_outline).strip() + "\n",
            )
        if str(current_state or "").strip():
            self._atomic_write_text(
                self.current_state_draft_path,
                str(current_state).strip() + "\n",
            )
        if graph is not None:
            self._atomic_write_text(
                self.foreshadowing_draft_path,
                yaml.safe_dump(
                    graph.model_dump(by_alias=True),
                    allow_unicode=True,
                    sort_keys=False,
                ),
            )

    def promote_foundation(self) -> bool:
        """将 background/foundation 的当前版本收口成 draft 与 src 的一致镜像。"""
        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        self.story_src_dir.mkdir(parents=True, exist_ok=True)

        background_src = self.story_src_dir / "background.md"
        foundation_src = self.story_src_dir / "foundation.md"

        # 在写任何 canonical 文件前先验证全部结构化草案。
        foreshadowing_graph = None
        if self.foreshadowing_draft_path.exists():
            try:
                foreshadowing_graph = self._parse_foreshadowing_graph(
                    self.foreshadowing_draft_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError, yaml.YAMLError):
                return False

        # 有草案时以草案为待确认版本，确认后再覆盖 canonical 文档。
        if self.background_draft_path.exists() and self.foundation_draft_path.exists():
            background_content = self._normalize_story_document(
                "background",
                self.background_draft_path.read_text(encoding="utf-8"),
            )
            foundation_content = self._normalize_story_document(
                "foundation",
                self.foundation_draft_path.read_text(encoding="utf-8"),
            )
            self._atomic_write_text(background_src, background_content)
            self._atomic_write_text(foundation_src, foundation_content)
            self._atomic_write_text(self.background_draft_path, background_content)
            self._atomic_write_text(self.foundation_draft_path, foundation_content)
        elif background_src.exists() and foundation_src.exists():
            # 没有待确认草案时，只接受已经存在的 canonical 文档。
            background_content = self._normalize_story_document(
                "background",
                background_src.read_text(encoding="utf-8"),
            )
            foundation_content = self._normalize_story_document(
                "foundation",
                foundation_src.read_text(encoding="utf-8"),
            )
            self._atomic_write_text(background_src, background_content)
            self._atomic_write_text(foundation_src, foundation_content)
        else:
            return False

        if self.current_state_draft_path.exists():
            from .truth_manager import TruthFilesManager

            state_text = self.current_state_draft_path.read_text(encoding="utf-8")
            state_meta, state_body = parse_toml_front_matter(state_text)
            truth = TruthFilesManager(
                self.project_root, self.novel_id
            ).load_truth_files()
            truth.current_state = strip_front_matter_padding(
                state_body if state_meta else state_text
            ).strip()
            TruthFilesManager(self.project_root, self.novel_id).save_truth_files(truth)

        if foreshadowing_graph is not None:
            dag_path = (
                self.novel_root / "data" / "foreshadowing" / "dag.yaml"
            )
            self._atomic_write_text(
                dag_path,
                yaml.safe_dump(
                    foreshadowing_graph.model_dump(by_alias=True),
                    allow_unicode=True,
                    sort_keys=False,
                ),
            )

        return True

    @staticmethod
    def _parse_foreshadowing_graph(value: str | dict[str, Any]) -> Any:
        from models.foreshadowing import ForeshadowingGraph

        if isinstance(value, dict):
            payload = value
        else:
            text = str(value or "").strip()
            fenced = re.match(r"^```(?:yaml|yml)?\s*\n(?P<body>.*)\n```$", text, re.DOTALL)
            if fenced:
                text = fenced.group("body").strip()
            payload = yaml.safe_load(text) or {}
        if not isinstance(payload, dict):
            raise ValueError("foreshadowing draft must be a YAML object")
        graph = ForeshadowingGraph.model_validate(payload)
        for node_id, node in graph.nodes.items():
            if node_id != node.id:
                raise ValueError(f"foreshadowing node key does not match id: {node_id}")
            if graph.status.get(node_id, node.status) != node.status:
                raise ValueError(f"foreshadowing status mismatch: {node_id}")
        known = set(graph.nodes)
        adjacency = {node_id: [] for node_id in known}
        for edge in graph.edges:
            if edge.from_ not in known:
                raise ValueError(f"foreshadowing edge source does not exist: {edge.from_}")
            if edge.to in known:
                adjacency[edge.from_].append(edge.to)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError(f"foreshadowing graph contains a cycle: {node_id}")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target in adjacency[node_id]:
                visit(target)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in known:
            visit(node_id)
        return graph

    def save_outline_draft(self, content: str, *, mode: str = "generated") -> None:
        """暂存大纲草稿，不在用户确认前改写 canonical outline。"""
        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        source_exists = self.outline_src_path.exists()
        source = self.read_outline_source()
        self._atomic_write_text(self.outline_draft_path, content)
        self._save_outline_edit_state(
            {
                "base_revision": self._hash_text(source),
                "base_exists": source_exists,
                "draft_revision": self._hash_text(content),
                "mode": mode,
            }
        )

    def read_outline_source(self, max_chars: int = 0) -> str:
        """读取已确认的 canonical 大纲。"""
        if not self.outline_src_path.exists():
            return ""
        text = self.outline_src_path.read_text(encoding="utf-8")
        if max_chars and len(text) > max_chars:
            return text[:max_chars]
        return text

    def outline_source_revision(self) -> str:
        """返回 canonical 大纲的内容 revision，用于防止陈旧补丁覆盖新内容。"""
        return self._hash_text(self.read_outline_source())

    def outline_source_is_placeholder(self) -> bool:
        """判断当前大纲是否仍是 init_project 创建的待填写模板。"""
        source = self.read_outline_source()
        return bool(source) and all(
            marker in source
            for marker in ("核心主题: 待填写", "故事简介: 待填写", "内容焦点: 待填写")
        )

    def read_outline_for_edit(
        self,
        *,
        query: str = "",
        start_line: int = 0,
        end_line: int = 0,
        context_lines: int = 40,
        max_chars: int = 30000,
    ) -> dict[str, Any]:
        """返回适合 ReAct 精确编辑的原文窗口和完整文件 revision。"""
        canonical = self.read_outline_source()
        pending = self._load_outline_edit_state()
        editing_source = canonical
        source_kind = "canonical"
        if pending and self.outline_draft_path.exists():
            pending_base = str(pending.get("base_revision", ""))
            pending_draft = self.outline_draft_path.read_text(encoding="utf-8")
            if (
                pending_base == self._hash_text(canonical)
                and str(pending.get("draft_revision", ""))
                == self._hash_text(pending_draft)
            ):
                editing_source = pending_draft
                source_kind = "pending_draft"

        lines = editing_source.splitlines(keepends=True)
        selected_start = 0
        selected_end = len(lines)

        if start_line > 0:
            selected_start = min(max(start_line - 1, 0), len(lines))
            selected_end = (
                min(max(end_line, start_line), len(lines))
                if end_line > 0
                else min(selected_start + max(context_lines * 2, 1), len(lines))
            )
        elif query:
            needle = query.casefold()
            match_index = next(
                (index for index, line in enumerate(lines) if needle in line.casefold()),
                -1,
            )
            if match_index >= 0:
                selected_start = max(0, match_index - context_lines)
                selected_end = min(len(lines), match_index + context_lines + 1)
            else:
                selected_start = 0
                selected_end = min(len(lines), max(context_lines * 2, 1))
        elif max_chars and len(editing_source) > max_chars:
            selected_end = min(len(lines), 240)

        content = "".join(lines[selected_start:selected_end])
        if max_chars and len(content) > max_chars:
            content = content[:max_chars]

        return {
            "ok": True,
            "path": str(self.outline_src_path),
            "exists": self.outline_src_path.exists(),
            "revision": self._hash_text(editing_source),
            "canonical_revision": self._hash_text(canonical),
            "source_kind": source_kind,
            "content": content,
            "start_line": selected_start + 1 if lines else 0,
            "end_line": selected_end,
            "total_lines": len(lines),
            "truncated": content != editing_source,
            "query_found": not query or query.casefold() in editing_source.casefold(),
            "pending_edit": bool(pending),
            "pending_draft_revision": str(pending.get("draft_revision", "")),
        }

    def stage_outline_edits(
        self,
        *,
        base_revision: str,
        edits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """按精确文本替换暂存增量补丁，未提及内容保持字节级不变。"""
        canonical = self.read_outline_source()
        canonical_revision = self._hash_text(canonical)
        if not self.outline_src_path.exists():
            return self._outline_edit_error(
                "missing_outline",
                "当前还没有已确认大纲，请先使用 generate_outline_draft 创建首版草稿。",
                revision=canonical_revision,
            )
        pending = self._load_outline_edit_state()
        working_source = canonical
        expected_revision = canonical_revision
        previous_edit_count = 0
        if pending and self.outline_draft_path.exists():
            if str(pending.get("base_revision", "")) != canonical_revision:
                return self._outline_edit_error(
                    "stale_outline_revision",
                    "已确认大纲在暂存后发生变化，请丢弃或重新生成补丁。",
                    revision=canonical_revision,
                )
            working_source = self.outline_draft_path.read_text(encoding="utf-8")
            expected_revision = self._hash_text(working_source)
            if str(pending.get("draft_revision", "")) != expected_revision:
                return self._outline_edit_error(
                    "stale_outline_draft",
                    "待确认草稿已被外部修改，请重新读取后再编辑。",
                    revision=expected_revision,
                )
            try:
                previous_edit_count = int(pending.get("edit_count", 0) or 0)
            except (TypeError, ValueError):
                previous_edit_count = 0
        if not str(base_revision or "").strip():
            return self._outline_edit_error(
                "missing_base_revision",
                "缺少 base_revision，请先调用 read_outline。",
                revision=expected_revision,
            )
        if base_revision != expected_revision:
            return self._outline_edit_error(
                "stale_outline_revision",
                "大纲已被其他操作修改，请重新读取后再生成补丁。",
                revision=expected_revision,
            )
        if not edits:
            return self._outline_edit_error(
                "missing_edits",
                "没有可暂存的大纲修改。",
                revision=expected_revision,
            )
        if len(edits) > 50:
            return self._outline_edit_error(
                "too_many_edits",
                "单次最多暂存 50 个精确修改。",
                revision=expected_revision,
            )

        revised = working_source
        applied: list[dict[str, Any]] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                return self._outline_edit_error(
                    "invalid_edit",
                    f"第 {index + 1} 个修改不是对象。",
                    revision=expected_revision,
                )
            old_text = str(edit.get("old_text", ""))
            new_text = str(edit.get("new_text", ""))
            replace_all = bool(edit.get("replace_all", False))
            if not old_text:
                return self._outline_edit_error(
                    "empty_old_text",
                    f"第 {index + 1} 个修改缺少 old_text。",
                    revision=expected_revision,
                )
            occurrences = revised.count(old_text)
            if occurrences == 0:
                return self._outline_edit_error(
                    "old_text_not_found",
                    f"第 {index + 1} 个修改的 old_text 不存在，请重新读取原文。",
                    revision=expected_revision,
                )
            if occurrences > 1 and not replace_all:
                return self._outline_edit_error(
                    "ambiguous_old_text",
                    f"第 {index + 1} 个修改匹配到 {occurrences} 处，请扩大 old_text 上下文。",
                    revision=expected_revision,
                )
            revised = revised.replace(old_text, new_text, -1 if replace_all else 1)
            applied.append(
                {
                    "index": index + 1,
                    "replacements": occurrences if replace_all else 1,
                    "replace_all": replace_all,
                }
            )

        if revised == working_source:
            return self._outline_edit_error(
                "no_changes",
                "补丁没有产生任何内容变化。",
                revision=expected_revision,
            )

        self.save_outline_draft(revised, mode="incremental")
        state = self._load_outline_edit_state()
        state["edit_count"] = previous_edit_count + len(applied)
        state["applied"] = applied
        self._save_outline_edit_state(state)
        diff = self._outline_diff(canonical, revised)
        return {
            "ok": True,
            "blocked": False,
            "next_action": "confirm_outline_edits",
            "message": "大纲增量修改已暂存，src/outline.md 尚未改变。",
            "path": str(self.outline_src_path),
            "draft_path": str(self.outline_draft_path),
            "base_revision": canonical_revision,
            "draft_revision": self._hash_text(revised),
            "edit_count": previous_edit_count + len(applied),
            "diff": diff,
        }

    def pending_outline_edit(self) -> dict[str, Any]:
        """读取当前待确认补丁及 diff。"""
        state = self._load_outline_edit_state()
        if not state or not self.outline_draft_path.exists():
            return self._outline_edit_error("missing_pending_edit", "当前没有待确认的大纲修改。")
        source = self.read_outline_source()
        draft = self.outline_draft_path.read_text(encoding="utf-8")
        return {
            "ok": True,
            "blocked": False,
            **state,
            "diff": self._outline_diff(source, draft),
            "path": str(self.outline_src_path),
            "draft_path": str(self.outline_draft_path),
        }

    def discard_outline_edit(self) -> dict[str, Any]:
        """丢弃待确认草稿并恢复为 canonical 大纲镜像。"""
        had_pending = self.outline_edit_state_path.exists()
        if self.outline_src_path.exists():
            self._atomic_write_text(self.outline_draft_path, self.read_outline_source())
        elif self.outline_draft_path.exists():
            self.outline_draft_path.unlink()
        self.outline_edit_state_path.unlink(missing_ok=True)
        return {
            "ok": True,
            "blocked": False,
            "discarded": had_pending,
            "next_action": "continue_planning",
            "message": "已丢弃待确认的大纲修改，src/outline.md 未改变。",
        }

    def read_outline_draft(self, max_chars: int = 0) -> str:
        if not self.outline_draft_path.exists():
            return ""
        text = self.outline_draft_path.read_text(encoding="utf-8").strip()
        if max_chars and len(text) > max_chars:
            return text[:max_chars]
        return text

    def outline_draft_is_current(self) -> bool:
        """判断 outline draft 是否与 canonical outline 保持完全一致。"""
        if not self.outline_src_path.exists() or not self.outline_draft_path.exists():
            return False
        return (
            self.outline_src_path.read_text(encoding="utf-8")
            == self.outline_draft_path.read_text(encoding="utf-8")
        )

    def promote_outline(self, confirmed: bool) -> bool:
        """确认后原子写入 outline；revision 冲突时拒绝覆盖。"""
        if not confirmed:
            return False

        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        self.outline_src_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.outline_draft_path.exists():
            return False

        draft = self.outline_draft_path.read_text(encoding="utf-8")
        state = self._load_outline_edit_state()
        if state:
            source_exists = self.outline_src_path.exists()
            source = self.read_outline_source()
            if bool(state.get("base_exists", False)) != source_exists:
                return False
            if str(state.get("base_revision", "")) != self._hash_text(source):
                return False
            if str(state.get("draft_revision", "")) != self._hash_text(draft):
                return False
        elif self.outline_src_path.exists() and self.read_outline_source() != draft:
            # 旧项目没有 revision 元数据时，只接受本就一致的镜像，避免盲目覆盖。
            return False

        self._atomic_write_text(self.outline_src_path, draft)
        self._atomic_write_text(self.outline_draft_path, draft)
        self.outline_edit_state_path.unlink(missing_ok=True)
        return True

    def _save_outline_edit_state(self, state: dict[str, Any]) -> None:
        self.runtime_planning_dir.mkdir(parents=True, exist_ok=True)
        rendered = yaml.safe_dump(state, allow_unicode=True, sort_keys=False)
        self._atomic_write_text(self.outline_edit_state_path, rendered)

    def _load_outline_edit_state(self) -> dict[str, Any]:
        if not self.outline_edit_state_path.exists():
            return {}
        try:
            data = yaml.safe_load(
                self.outline_edit_state_path.read_text(encoding="utf-8")
            ) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _outline_diff(before: str, after: str, max_chars: int = 16000) -> str:
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/src/outline.md",
                tofile="b/src/outline.md",
            )
        )
        if len(diff) <= max_chars:
            return diff
        return diff[:max_chars] + "\n... diff 已截断 ...\n"

    @staticmethod
    def _outline_edit_error(
        error: str,
        message: str,
        *,
        revision: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "blocked": True,
            "error": error,
            "message": message,
            "revision": revision,
            "next_action": "read_outline",
        }

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = handle.name
            os.replace(temporary_path, path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def save_goethe_handoff(self, manifest: dict[str, Any]) -> tuple[Path, Path]:
        """保存 Goethe -> Dante 交接产物的 Markdown/YAML 双视图。"""
        self.workflow_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(manifest)
        payload.setdefault("id", "goethe_handoff")
        payload.setdefault("type", "handoff")
        payload.setdefault("source_agent", "goethe")
        payload.setdefault("target_agent", "dante")
        payload.setdefault("next_stage", "chapter_preflight")
        payload.setdefault("ready", False)
        payload.setdefault("required_assets", [])

        ready_label = "是" if payload.get("ready") else "否"
        missing_items = payload.get("missing_items", [])
        missing_text = "、".join(str(item) for item in missing_items) if missing_items else "无"
        persona_paths = payload.get("persona_paths", [])
        persona_text = "、".join(str(item) for item in persona_paths) if persona_paths else "无"
        character_paths = payload.get("character_paths", [])
        character_text = "、".join(str(item) for item in character_paths) if character_paths else "无"

        # Markdown 版本给人快速审阅，YAML 版本给运行时和测试稳定读取。
        body = "\n".join(
            [
                "# Goethe -> Dante Handoff",
                "",
                "## Status",
                f"- ready: {ready_label}",
                f"- next_stage: {payload.get('next_stage', 'chapter_preflight')}",
                f"- source_agent: {payload.get('source_agent', 'goethe')}",
                f"- target_agent: {payload.get('target_agent', 'dante')}",
                "",
                "## Required Assets",
                "- " + "\n- ".join(
                    str(item) for item in payload.get("required_assets", [])
                )
                if payload.get("required_assets")
                else "- 无",
                "",
                "## Missing Items",
                f"- {missing_text}",
                "",
                "## Persona Paths",
                f"- {persona_text}",
                "",
                "## Character Paths",
                f"- {character_text}",
                "",
                "## Summary",
                str(payload.get("summary", "")).strip() or "Goethe 资产已整理完毕，可以交接给 Dante。",
            ]
        )
        self.goethe_handoff_md_path.write_text(
            compose_toml_document(
                {
                    "id": payload["id"],
                    "type": payload["type"],
                    "source_agent": payload["source_agent"],
                    "target_agent": payload["target_agent"],
                    "ready": bool(payload.get("ready")),
                    "next_stage": payload.get("next_stage", "chapter_preflight"),
                },
                body,
            ),
            encoding="utf-8",
        )
        self.goethe_handoff_yaml_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self.goethe_handoff_md_path, self.goethe_handoff_yaml_path

    def list_character_documents(self) -> list[dict[str, str]]:
        """列出当前 canonical 角色文档，供 handoff 和 planning 检查使用。"""
        character_dir = self.novel_root / "src" / "characters"
        if not character_dir.exists():
            return []

        documents: list[dict[str, str]] = []
        for path in sorted(character_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            meta, body = parse_toml_front_matter(text)
            normalized_body = strip_front_matter_padding(body if meta else text)
            title = self._extract_document_title(normalized_body, path.stem)
            documents.append(
                {
                    "id": str(meta.get("id", path.stem)).strip() or path.stem,
                    "title": title,
                    "path": str(path),
                }
            )
        return documents

    def load_story_document(self, kind: str) -> dict[str, object]:
        """Load a promoted story document and expose metadata plus body."""
        path = self.story_src_dir / f"{kind}.md"
        if not path.exists():
            return {"path": path, "meta": {}, "body": ""}

        text = path.read_text(encoding="utf-8")
        meta, body = parse_toml_front_matter(text)
        normalized_body = strip_front_matter_padding(body if meta else text)
        if not meta:
            meta = self._default_story_metadata(kind, normalized_body)
        return {"path": path, "meta": meta, "body": normalized_body}

    def read_story_document(self, kind: str, max_chars: int = 0) -> str:
        """Return a compact AI-friendly rendering of a story source document."""
        document = self.load_story_document(kind)
        meta = document["meta"] if isinstance(document["meta"], dict) else {}
        body = str(document["body"])
        parts = []
        summary = str(meta.get("summary", "")).strip()
        detail_refs = meta.get("detail_refs", [])
        if summary:
            parts.append(f"摘要：{summary}")
        if isinstance(detail_refs, list) and detail_refs:
            parts.append("细节索引：" + "、".join(str(item) for item in detail_refs))
        if body:
            parts.append(body)
        text = "\n".join(parts).strip()
        if max_chars and len(text) > max_chars:
            return text[:max_chars]
        return text

    def _normalize_story_document(self, kind: str, text: str) -> str:
        """把 story 文档规整成 `TOML front matter + Markdown body` 统一格式。"""
        meta, body = parse_toml_front_matter(text)
        normalized_body = strip_front_matter_padding(body if meta else text)
        normalized_meta = meta or self._default_story_metadata(kind, normalized_body)
        return compose_toml_document(normalized_meta, normalized_body)

    @staticmethod
    def _story_document_has_content(text: str) -> bool:
        meta, body = parse_toml_front_matter(str(text or ""))
        candidate = strip_front_matter_padding(body if meta else str(text or ""))
        for line in candidate.splitlines():
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            cleaned = re.sub(r"^[>\-*+\s]+", "", cleaned).strip()
            cleaned = re.sub(
                r"[（(]?\s*(?:待填写|待定义|TODO|TBD).*?[）)]?$",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()
            if cleaned:
                return True
        return False

    def _default_story_metadata(self, kind: str, body: str) -> dict[str, object]:
        summary = self._extract_story_summary(body)
        detail_refs = {
            "background": ["premise", "conflict", "tone"],
            "foundation": ["protagonist", "rules", "stakes"],
        }.get(kind, ["details"])
        return {
            "id": f"story_{kind}",
            "type": "story_document",
            "summary": summary,
            "detail_refs": detail_refs,
        }

    def _extract_story_summary(self, body: str) -> str:
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            return stripped[:160]
        return body.strip()[:160]

    def _extract_document_title(self, body: str, fallback: str) -> str:
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                return title or fallback
        return fallback

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
