"""Local, novel-only web workbench for OpenWrite."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import tempfile
import time
import uuid
import webbrowser
from collections import deque
from collections.abc import Callable
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any, cast
from urllib.parse import parse_qs, quote, urlparse

import yaml
from markdown_it import MarkdownIt

from tools.context_manifest import build_context_manifest
from tools.git_checkpoint import GitCheckpointManager
from tools.novel_service import NovelApplicationService, NovelServiceError
from tools.novel_workspace import (
    list_chapters,
    novel_root,
)
from tools.outline_tree import (
    OutlineEditError,
    build_outline_structure,
    mutate_outline_structure,
)
from tools.project_registry import (
    ProjectRegistry,
    is_framework_root,
    write_content_project_metadata,
)
from tools.project_search import ProjectSearchIndex
from tools.studio_preferences import StudioModelSettingsStore
from tools.version import __version__

STATIC_ROOT = Path(__file__).parent / "studio_assets"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
WRITE_HEADER = "X-OpenWrite-Studio"
DEBUG_ENV = "OPENWRITE_DEBUG"
DEBUG_LOGGER_NAMES = (
    "tools.studio",
    "tools.agent.react",
    "tools.agent.dante",
    "tools.agent.dante_actions",
    "tools.agent.orchestrator",
    "tools.goethe",
)
AGENT_TOOL_LABELS = {
    "get_status": "读取作品状态",
    "get_context": "组装章节上下文",
    "search_project": "搜索作品资料",
    "read_project_document": "读取项目文档",
    "edit_project_document": "预览或写入文档修改",
    "get_outline_structure": "读取大纲结构",
    "edit_outline_structure": "预览或写入大纲修改",
    "get_world_relations": "读取关系图谱",
    "search_relation_targets": "搜索关系目标",
    "edit_world_relation": "预览或写入关系",
    "edit_world_relations": "批量处理关系",
    "run_chapter_preflight": "执行章节预检",
    "delegate_chapter_write": "生成并结算章节",
    "delegate_chapter_review": "审查章节",
}
logger = logging.getLogger(__name__)
CHAT_MARKDOWN = MarkdownIt(
    "commonmark",
    {
        "html": False,
        "linkify": False,
        "typographer": False,
    },
)


def _truthy_env(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "debug"}


def _debug_log_path(project_root: Path, novel_root: Path, initialized: bool) -> Path:
    if initialized:
        return novel_root / "data" / "logs" / "studio-debug.log"
    return project_root / ".openwrite" / "logs" / "studio-debug.log"


def _configure_debug_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for logger_name in DEBUG_LOGGER_NAMES:
        target = logging.getLogger(logger_name)
        target.setLevel(logging.DEBUG)
        target.propagate = False
        for handler in list(target.handlers):
            if getattr(handler, "_openwrite_studio_debug", False):
                target.removeHandler(handler)
                handler.close()
        handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler._openwrite_studio_debug = True  # type: ignore[attr-defined]
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            )
        )
        target.addHandler(handler)


def _sanitize_debug_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<max-depth>"
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(
                token in lowered
                for token in ("api_key", "apikey", "authorization", "password", "secret", "token")
            ):
                sanitized[key_text] = "<redacted>"
            else:
                sanitized[key_text] = _sanitize_debug_payload(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_debug_payload(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, tuple):
        return tuple(_sanitize_debug_payload(item, depth=depth + 1) for item in value[:20])
    if isinstance(value, str):
        compact = value.replace("\n", "\\n")
        return compact[:800] + ("..." if len(compact) > 800 else "")
    return value


def _debug_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        _sanitize_debug_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def render_chat_markdown(content: str) -> str:
    """Render model-authored CommonMark without allowing raw HTML."""
    return CHAT_MARKDOWN.render(content)


class StudioError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


class StudioApplication:
    """Filesystem and writing operations exposed to the local HTTP layer."""

    def __init__(
        self,
        project_root: Path,
        writer_executor: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
        review_executor: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
        chat_executor: Callable[[Path, str, str, str], dict[str, Any]] | None = None,
        source_executor: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
        model_test_executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        project_registry: ProjectRegistry | None = None,
        model_settings_store: StudioModelSettingsStore | None = None,
        debug: bool = False,
    ):
        self.launch_root = Path(project_root).resolve()
        self.project_root = self.launch_root
        self.debug_enabled = bool(debug or _truthy_env(os.environ.get(DEBUG_ENV, "")))
        self.debug_log_path: Path | None = None
        self._project_registry = project_registry
        self._writer_executor = writer_executor
        self._review_executor = review_executor
        self._chat_executor = chat_executor
        self._source_executor = source_executor
        self._model_test_executor = model_test_executor
        self._model_settings_store = model_settings_store or StudioModelSettingsStore()
        self._saved_model_settings = self._model_settings_store.restore_environment()
        self._write_lock = Lock()
        self._agent_activity_lock = Lock()
        self._agent_activities: dict[str, dict[str, Any]] = {}
        self._activate_project(self.project_root)

    def _activate_project(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.config_path = self.project_root / "novel_config.yaml"
        self.initialized = self.config_path.exists()
        self.config = self._load_config() if self.initialized else {}
        self.novel_id = str(self.config.get("novel_id") or "")
        if self.initialized and not self.novel_id:
            raise StudioError("novel_config.yaml 缺少 novel_id")
        self.novel_root = (
            novel_root(self.project_root, self.novel_id).resolve()
            if self.initialized
            else self.project_root
        )
        self._novel_service = self._build_novel_service() if self.initialized else None
        if self.initialized and self._project_registry is not None:
            self._project_registry.remember(self.project_root)
        self._configure_debug_mode()
        self._debug_event(
            "project_activated",
            project_root=str(self.project_root),
            novel_id=self.novel_id,
            initialized=self.initialized,
            log_path=str(self.debug_log_path or ""),
        )

    def _configure_debug_mode(self) -> None:
        if not self.debug_enabled:
            return
        self.debug_log_path = _debug_log_path(
            self.project_root,
            self.novel_root,
            self.initialized,
        )
        _configure_debug_logging(self.debug_log_path)

    def _debug_event(self, event: str, **payload: Any) -> None:
        if not self.debug_enabled:
            return
        logger.debug("studio.%s %s", event, _debug_json(payload))

    def agent_activity(self, run_id: str) -> dict[str, Any]:
        clean_id = self._normalize_activity_run_id(run_id)
        with self._agent_activity_lock:
            activity = self._agent_activities.get(clean_id)
            if activity is None:
                raise StudioError("未找到 AI 运行记录", HTTPStatus.NOT_FOUND)
            payload = dict(activity)
            payload["events"] = [dict(item) for item in activity.get("events", [])]
        payload["elapsed_seconds"] = max(
            0,
            int((payload.get("finished_at") or time.time()) - payload["started_at"]),
        )
        return payload

    def _start_agent_activity(
        self,
        run_id: str,
        *,
        agent: str,
        session_id: str,
    ) -> None:
        now = time.time()
        label = "Goethe" if agent == "goethe" else "Dante"
        activity = {
            "run_id": run_id,
            "agent": agent,
            "agent_label": label,
            "session_id": session_id,
            "status": "running",
            "phase": "reading_context",
            "step_index": 0,
            "title": f"{label} 正在读取项目",
            "note": "正在恢复会话、作品状态和本轮上下文。",
            "tool": "",
            "turn": 0,
            "started_at": now,
            "updated_at": now,
            "finished_at": 0.0,
            "events": [],
        }
        with self._agent_activity_lock:
            self._agent_activities[run_id] = activity
            if len(self._agent_activities) > 50:
                oldest = sorted(
                    self._agent_activities.values(),
                    key=lambda item: float(item.get("updated_at") or 0),
                )[: len(self._agent_activities) - 50]
                for item in oldest:
                    self._agent_activities.pop(str(item["run_id"]), None)

    def _record_agent_activity(
        self,
        run_id: str,
        event: dict[str, Any],
    ) -> None:
        event_name = str(event.get("event") or "")
        now = time.time()
        with self._agent_activity_lock:
            activity = self._agent_activities.get(run_id)
            if activity is None:
                return
            label = str(activity["agent_label"])
            turn = int(event.get("turn") or activity.get("turn") or 0)
            tool = str(event.get("tool") or activity.get("tool") or "")
            activity["updated_at"] = now
            activity["turn"] = turn
            activity["tool"] = tool

            if event_name == "run_started":
                activity.update(
                    phase="reading_context",
                    step_index=0,
                    title=f"{label} 已读取项目",
                    note="会话与作品状态已载入，准备分析本轮目标。",
                )
            elif event_name == "model_started":
                activity.update(
                    phase="thinking",
                    step_index=1,
                    title=f"{label} 正在思考",
                    note=f"第 {turn} 轮：等待模型决定下一步。",
                )
            elif event_name == "model_completed":
                tool_count = int(event.get("tool_count") or 0)
                activity.update(
                    phase="tool_selection" if tool_count else "response",
                    step_index=2 if tool_count else 3,
                    title=(
                        f"{label} 已选择 {tool_count} 个工具"
                        if tool_count
                        else f"{label} 正在整理回复"
                    ),
                    note=(
                        "模型已返回，准备执行并校验工具结果。"
                        if tool_count
                        else "模型已完成本轮分析，正在整理回复。"
                    ),
                )
            elif event_name == "tool_started":
                tool_label = AGENT_TOOL_LABELS.get(tool, tool or "项目工具")
                activity.update(
                    phase="tool_running",
                    step_index=2,
                    title=f"{label} 正在调用工具",
                    note=f"第 {turn} 轮：{tool_label}",
                )
            elif event_name == "tool_completed":
                tool_label = AGENT_TOOL_LABELS.get(tool, tool or "项目工具")
                ok = bool(event.get("ok", True))
                reason = str(event.get("reason") or "")
                activity.update(
                    phase="tool_result",
                    step_index=2,
                    title=f"{tool_label}{'已完成' if ok else '失败'}",
                    note=(
                        "结果已返回给模型，等待下一步判断。"
                        if ok
                        else (reason or "工具返回失败，Agent 正在判断是否可以恢复。")
                    ),
                )
            elif event_name == "response_ready":
                activity.update(
                    phase="response",
                    step_index=3,
                    title=f"{label} 正在整理回复",
                    note="工具与状态已经核对，正在写入本轮回复和会话历史。",
                )
            elif event_name == "run_completed":
                activity.update(
                    phase="complete",
                    step_index=4,
                    title=f"{label} 本轮已完成",
                    note="回复与会话状态已经保存。",
                )
            elif event_name == "run_failed":
                reason = str(event.get("reason") or "Agent 本轮执行失败")
                activity.update(
                    phase="error",
                    title=f"{label} 本轮已停止",
                    note=reason,
                )

            events = activity.setdefault("events", [])
            events.append(
                {
                    "event": event_name,
                    "turn": turn,
                    "tool": tool,
                    "ok": event.get("ok"),
                    "reason": str(event.get("reason") or ""),
                    "timestamp": now,
                }
            )
            del events[:-12]

    def _finish_agent_activity(
        self,
        run_id: str,
        *,
        status: str,
        message: str = "",
    ) -> None:
        now = time.time()
        with self._agent_activity_lock:
            activity = self._agent_activities.get(run_id)
            if activity is None:
                return
            label = str(activity["agent_label"])
            activity.update(
                status=status,
                phase="complete" if status == "complete" else "error",
                step_index=4 if status == "complete" else activity.get("step_index", 0),
                title=(
                    f"{label} 本轮已完成"
                    if status == "complete"
                    else f"{label} 本轮已中断"
                ),
                note=(
                    "回复与会话状态已经保存。"
                    if status == "complete"
                    else (message or "请求未完成，请检查模型连接或 debug 日志。")
                ),
                updated_at=now,
                finished_at=now,
            )

    @staticmethod
    def _normalize_activity_run_id(value: Any) -> str:
        run_id = str(value or "").strip()
        if not run_id:
            return uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,95}", run_id):
            raise StudioError("AI 运行 ID 无效")
        return run_id

    def _debug_book_state(self) -> dict[str, Any]:
        if not self.initialized:
            return {}
        path = self.novel_root / "data" / "workflows" / "book_state.yaml"
        if not path.is_file():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return {"error": "unreadable"}
        if not isinstance(data, dict):
            return {"error": "invalid"}
        return {
            key: data.get(key, "")
            for key in (
                "stage",
                "pending_confirmation",
                "current_arc",
                "current_section",
                "current_chapter",
                "blocking_reason",
                "last_agent_action",
            )
        }

    def workspace(self) -> dict[str, Any]:
        if not self.initialized:
            return {
                "version": __version__,
                "initialized": False,
                "project": self._project_payload(),
                "snapshot": {
                    "novel_id": "",
                    "title": "新小说",
                    "current_arc": "arc_001",
                    "current_chapter": "ch_001",
                    "stage": "discovery",
                    "chapters": 0,
                    "writing_units": 0,
                    "target_units": 0,
                    "characters": 0,
                    "world_documents": 0,
                    "pending_foreshadowing": 0,
                    "total_tokens": 0,
                    "reviewed_chapters": 0,
                    "average_review_score": 0,
                    "creative_focus": {
                        "goal": "",
                        "must_keep": [],
                        "must_avoid": [],
                        "notes": [],
                    },
                    "readiness": {
                        "author_intent": False,
                        "background": False,
                        "foundation": False,
                        "characters": False,
                        "outline": False,
                        "creative_focus": False,
                    },
                    "next_actions": ["先创建小说项目"],
                    "next_action_items": [
                        {
                            "id": "init_project",
                            "label": "先创建小说项目",
                            "cli": "openwrite init <novel_id>",
                            "studio_action": "open_project_dialog",
                            "seed": "",
                        }
                    ],
                },
                "documents": {
                    "outline": [],
                    "story": [],
                    "characters": [],
                    "world": [],
                    "chapters": [],
                },
                "model": self._model_payload(),
                "operations": {
                    "sync": {"needs_sync": False},
                    "source_packs": [],
                    "diagnostics": [
                        {"name": "项目配置", "ok": False, "detail": "尚未创建"}
                    ],
                },
            }
        self.config = self._load_config()
        snapshot = self._service().workspace_snapshot()
        chapters = list_chapters(self.project_root, self.novel_id)
        return {
            "version": __version__,
            "initialized": True,
            "project": self._project_payload(),
            "snapshot": snapshot,
            "documents": self._document_groups(chapters),
            "model": self._model_payload(),
            "operations": self.operation_status(),
        }

    def initialize_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_target = str(payload.get("project_path") or "").strip()
        target = Path(raw_target).expanduser().resolve() if raw_target else self.project_root
        self._debug_event(
            "project_init_requested",
            target=str(target),
            novel_id=payload.get("novel_id"),
            title=payload.get("title"),
        )
        if self.initialized and target == self.project_root:
            raise StudioError("当前目录已经是小说项目", HTTPStatus.CONFLICT)
        if target == self.launch_root and is_framework_root(target):
            raise StudioError("框架仓库不能直接保存私人作品，请选择独立作品目录")
        if target.exists() and not target.is_dir():
            raise StudioError("作品路径不是目录")
        target.mkdir(parents=True, exist_ok=True)
        if (target / "novel_config.yaml").exists():
            raise StudioError("目标目录已经是小说项目", HTTPStatus.CONFLICT)
        novel_id = str(payload.get("novel_id") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}", novel_id):
            raise StudioError("小说 ID 需为 2-64 位字母、数字、横线或下划线")
        if not title or len(title) > 120:
            raise StudioError("书名不能为空且不能超过 120 字")
        try:
            NovelApplicationService.initialize(target, novel_id, title)
        except NovelServiceError as exc:
            raise self._translate_service_error(exc) from exc
        write_content_project_metadata(target)
        self._activate_project(target)
        self._debug_event("project_init_completed", target=str(target), novel_id=novel_id)
        return self.workspace()

    def open_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(payload.get("project_path") or "").strip()
        if not raw_path:
            raise StudioError("请输入作品目录")
        target = Path(raw_path).expanduser().resolve()
        self._debug_event("project_open_requested", target=str(target))
        if not target.is_dir() or not (target / "novel_config.yaml").is_file():
            raise StudioError("所选目录不是有效的 OpenWrite 作品项目")
        with self._write_lock:
            self._activate_project(target)
        self._debug_event("project_open_completed", target=str(target), novel_id=self.novel_id)
        return self.workspace()

    def _project_payload(self) -> dict[str, Any]:
        recent = self._project_registry.list() if self._project_registry is not None else []
        return {
            "root": str(self.project_root),
            "launch_root": str(self.launch_root),
            "framework_root": is_framework_root(self.launch_root),
            "requires_external_location": (
                not self.initialized and is_framework_root(self.project_root)
            ),
            "recent": recent,
        }

    def require_project(self) -> None:
        if not self.initialized:
            raise StudioError("请先创建小说项目", HTTPStatus.PRECONDITION_REQUIRED)

    def _build_novel_service(self) -> NovelApplicationService:
        return NovelApplicationService(
            self.project_root,
            writer_executor=self._writer_executor,
            review_executor=self._review_executor,
            source_executor=self._source_executor,
            task_lock=self._write_lock,
        )

    def _service(self) -> NovelApplicationService:
        self.require_project()
        if self._novel_service is None:
            self._novel_service = self._build_novel_service()
        return self._novel_service

    @staticmethod
    def _translate_service_error(exc: NovelServiceError) -> StudioError:
        status = {
            "PROJECT_BUSY": HTTPStatus.CONFLICT,
            "CONFLICT": HTTPStatus.CONFLICT,
            "NOT_FOUND": HTTPStatus.NOT_FOUND,
            "INVALID_PROJECT": HTTPStatus.PRECONDITION_FAILED,
            "INVALID_INPUT": HTTPStatus.BAD_REQUEST,
        }.get(exc.code, HTTPStatus.BAD_GATEWAY)
        return StudioError(str(exc), status)

    def operation_status(self) -> dict[str, Any]:
        sources_root = self.novel_root / "data" / "sources"
        source_packs = []
        if sources_root.exists():
            for path in sorted(sources_root.iterdir()):
                if not path.is_dir():
                    continue
                source_packs.append(
                    {
                        "source_id": path.name,
                        "review_ready": (path / "source.md").exists(),
                        "style_ready": (path / "style").is_dir(),
                        "setting_ready": (path / "setting_profile.md").exists(),
                    }
                )
        sync = self._service().sync_status()
        diagnostics = [
            {
                "name": "项目配置",
                "ok": self.config_path.is_file() and bool(self.novel_id),
                "detail": self.novel_id,
            },
            {
                "name": "模型连接",
                "ok": bool(os.environ.get("LLM_API_KEY", "").strip()),
                "detail": os.environ.get("LLM_MODEL", "").strip() or "未配置",
            },
            {
                "name": "源文件同步",
                "ok": not bool(sync.get("needs_sync")),
                "detail": "待同步" if sync.get("needs_sync") else "已同步",
            },
            {
                "name": "作品写入",
                "ok": os.access(self.novel_root, os.W_OK),
                "detail": "可写" if os.access(self.novel_root, os.W_OK) else "只读",
            },
        ]
        return {
            "sync": sync,
            "source_packs": source_packs,
            "diagnostics": diagnostics,
            "git_checkpoint": GitCheckpointManager(self.project_root).status(),
        }

    def read_document(self, relative_path: str) -> dict[str, Any]:
        path = self._resolve_document(relative_path, write=False)
        if not path.is_file():
            raise StudioError("文档不存在", HTTPStatus.NOT_FOUND)
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise StudioError(
                "文档超过 2 MB，Studio 不直接打开",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        return {
            "path": self._relative(path),
            "title": self._document_title(path),
            "content": path.read_text(encoding="utf-8"),
            "version": str(path.stat().st_mtime_ns),
        }

    def write_document(
        self,
        relative_path: str,
        content: str,
        version: str | int | None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        path = self._resolve_document(relative_path, write=True)
        encoded = content.encode("utf-8")
        self._debug_event(
            "document_write_requested",
            path=self._relative(path),
            bytes=len(encoded),
            version=version,
            force=force,
        )
        if len(encoded) > MAX_DOCUMENT_BYTES:
            raise StudioError("文档超过 2 MB，已拒绝保存", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if "\x00" in content:
            raise StudioError("文档包含无效字符")

        with self._write_lock:
            if path.exists() and version is not None and not force:
                current_version = str(path.stat().st_mtime_ns)
                if current_version != str(version):
                    raise StudioError("文档已在其他位置修改，请重新载入", HTTPStatus.CONFLICT)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                temp_path = Path(handle.name)
            temp_path.replace(path)
        saved = self.read_document(self._relative(path))
        checkpoint_path = path.resolve().relative_to(self.project_root).as_posix()
        checkpoint = GitCheckpointManager(self.project_root).checkpoint(
            [checkpoint_path], self._checkpoint_message(path)
        )
        saved["checkpoint"] = checkpoint.to_dict()
        self._debug_event(
            "document_write_completed",
            path=saved.get("path"),
            version=saved.get("version"),
            checkpoint=saved["checkpoint"],
        )
        return saved

    def _checkpoint_message(self, path: Path) -> str:
        relative = self._relative(path)
        if relative == "src/outline.md":
            prefix = "outline"
        elif relative.startswith("src/characters/"):
            prefix = "character"
        elif relative.startswith("src/world/"):
            prefix = "world"
        elif relative.startswith("data/manuscript/"):
            prefix = "chapter"
        else:
            prefix = "checkpoint"
        return f"{prefix}: save {path.stem} from Studio"

    def update_focus(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            self._service().update_focus(
                goal=str(payload.get("goal") or ""),
                must_keep=self._string_list(payload.get("must_keep")),
                must_avoid=self._string_list(payload.get("must_avoid")),
                notes=self._string_list(payload.get("notes")),
            )
        except NovelServiceError as exc:
            raise self._translate_service_error(exc) from exc
        return self.workspace()

    def configure_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self._validated_model_settings(payload)
        supplied_api_key = str(payload.get("api_key") or "").strip()
        os.environ["LLM_PROVIDER"] = settings["provider"]
        os.environ["LLM_MODEL"] = settings["model"]
        os.environ["LLM_API_FORMAT"] = settings["api_format"]
        os.environ["LLM_BASE_URL"] = settings["base_url"]
        os.environ["OPENWRITE_CONTEXT_TOKENS"] = str(settings["context_tokens"])
        os.environ["LLM_MAX_TOKENS"] = str(settings["max_tokens"])
        if settings["api_key"]:
            os.environ["LLM_API_KEY"] = settings["api_key"]
        remember_api_key = bool(payload.get("remember_api_key", True))
        persisted = {
            key: settings[key]
            for key in (
                "provider",
                "model",
                "api_format",
                "base_url",
                "context_tokens",
                "max_tokens",
            )
        }
        persisted["remember_api_key"] = remember_api_key
        try:
            self._model_settings_store.save_settings(persisted)
            if not remember_api_key:
                self._model_settings_store.clear_credential()
            elif supplied_api_key:
                self._model_settings_store.save_credential(supplied_api_key)
        except OSError as exc:
            raise StudioError("模型已应用，但本机持久化失败，请检查配置目录权限") from exc
        self._saved_model_settings = persisted
        return self.workspace()

    def test_model_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a candidate connection without replacing active settings."""
        settings = self._validated_model_settings(payload)
        started = time.monotonic()
        try:
            if self._model_test_executor is not None:
                result = self._model_test_executor(dict(settings))
            else:
                result = self._default_model_connection_test(settings)
        except Exception as exc:
            raise StudioError(self._safe_model_connection_error(exc)) from exc
        return {
            "ok": True,
            "provider": settings["provider"],
            "model": settings["model"],
            "latency_ms": max(1, int((time.monotonic() - started) * 1000)),
            "reply": str(result.get("reply") or "OK")[:120]
            if isinstance(result, dict)
            else "OK",
        }

    def _validated_model_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = str(payload.get("provider") or "openai").strip().lower()
        model = str(payload.get("model") or "").strip()
        base_url = str(payload.get("base_url") or "").strip()
        api_format = str(payload.get("api_format") or "chat").strip().lower()
        api_key = str(payload.get("api_key") or "").strip()
        if provider not in {"openai", "anthropic", "custom"}:
            raise StudioError("模型提供方无效")
        if api_format not in {"chat", "responses"}:
            raise StudioError("API 格式无效")
        if not model or len(model) > 120:
            raise StudioError("模型名称不能为空且不能超过 120 字")
        defaults = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
        }
        base_url = base_url or defaults.get(provider, "")
        if provider == "custom" and not base_url:
            raise StudioError("自定义模型必须填写 Base URL")
        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise StudioError("Base URL 必须是有效的 HTTP(S) 地址")
        api_key = api_key or os.environ.get("LLM_API_KEY", "").strip()
        if not api_key:
            raise StudioError("API Key 不能为空")
        context_tokens = self._bounded_int(
            payload.get("context_tokens"),
            default=self._env_int("OPENWRITE_CONTEXT_TOKENS", 64000),
            minimum=12000,
            maximum=512000,
            label="上下文预算",
        )
        max_tokens = self._bounded_int(
            payload.get("max_tokens"),
            default=self._env_int("LLM_MAX_TOKENS", 24000),
            minimum=256,
            maximum=131072,
            label="最大输出",
        )
        return {
            "provider": provider,
            "model": model,
            "base_url": base_url.rstrip("/"),
            "api_format": api_format,
            "api_key": api_key,
            "context_tokens": context_tokens,
            "max_tokens": max_tokens,
        }

    def _model_payload(self) -> dict[str, Any]:
        provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower() or "openai"
        base_url = os.environ.get("LLM_BASE_URL", "").strip()
        if not base_url:
            base_url = (
                "https://api.anthropic.com"
                if provider == "anthropic"
                else "https://api.openai.com/v1"
            )
        return {
            "configured": bool(os.environ.get("LLM_API_KEY", "").strip()),
            "provider": provider,
            "base_url": base_url,
            "name": os.environ.get("LLM_MODEL", "").strip() or "gpt-4o-mini",
            "api_format": os.environ.get("LLM_API_FORMAT", "chat").strip() or "chat",
            "context_tokens": self._env_int("OPENWRITE_CONTEXT_TOKENS", 64000),
            "max_tokens": self._env_int("LLM_MAX_TOKENS", 24000),
            "persistence": {
                "settings_saved": self._model_settings_store.settings_persisted,
                "credential_saved": self._model_settings_store.credential_persisted,
                "remember_api_key": bool(
                    self._saved_model_settings.get("remember_api_key", False)
                ),
            },
        }

    @staticmethod
    def _safe_model_connection_error(exc: Exception) -> str:
        """Map provider errors to useful messages without echoing credentials."""
        message = str(exc).lower()
        if any(term in message for term in ("401", "unauthorized", "authentication", "api key")):
            return "连接测试失败：认证失败，请检查 API Key。"
        if any(term in message for term in ("404", "not found", "model_not_found")):
            return "连接测试失败：模型或 API 地址不存在，请检查模型名称和 Base URL。"
        if any(term in message for term in ("429", "rate limit", "too many requests")):
            return "连接测试失败：服务商限流或额度不足，请稍后重试并检查账户额度。"
        if any(term in message for term in ("timeout", "timed out")):
            return "连接测试失败：请求超时，请检查网络和 Base URL。"
        if any(term in message for term in ("connection", "dns", "name resolution")):
            return "连接测试失败：无法连接到服务商，请检查网络和 Base URL。"
        if "empty model reply" in message or "模型返回空内容" in message:
            return "连接测试失败：模型返回空内容，请调大最大输出后重试。"
        return f"连接测试失败：{type(exc).__name__}。请检查模型配置。"

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            return default

    @staticmethod
    def _bounded_int(
        value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int,
        label: str,
    ) -> int:
        try:
            parsed = int(value) if value not in {None, ""} else default
        except (TypeError, ValueError) as exc:
            raise StudioError(f"{label}必须是整数") from exc
        if not minimum <= parsed <= maximum:
            raise StudioError(f"{label}必须在 {minimum}-{maximum} 之间")
        return parsed

    @staticmethod
    def _default_model_connection_test(settings: dict[str, Any]) -> dict[str, Any]:
        from tools.llm import LLMClient, LLMConfig, Message

        config = LLMConfig(
            provider=settings["provider"],
            api_key=settings["api_key"],
            base_url=settings["base_url"],
            model=settings["model"],
            max_tokens=settings["max_tokens"],
            api_format=settings["api_format"],
            stream=False,
            timeout_seconds=30,
            max_retries=0,
        )
        response = LLMClient(config).chat(
            [Message("user", "这是连接测试。请只回复 OK。")],
            temperature=0,
            max_tokens=32,
            stream=False,
        )
        reply = response.content.strip()
        if not reply:
            raise RuntimeError("empty model reply")
        return {"reply": reply}

    def sync_project(self) -> dict[str, Any]:
        try:
            result = self._service().sync()
        except NovelServiceError as exc:
            raise self._translate_service_error(exc) from exc
        return {**result, "workspace": self.workspace()}

    def create_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        name = str(payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        self._debug_event(
            "document_create_requested",
            kind=kind,
            name=name,
            description=description,
        )
        with self._write_lock:
            try:
                path = self._service().create_document(
                    kind=kind,
                    name=name,
                    description=description,
                )
            except NovelServiceError as exc:
                raise self._translate_service_error(exc) from exc
        document = self.read_document(self._relative(path))
        self._debug_event(
            "document_create_completed",
            kind=kind,
            name=name,
            path=document.get("path"),
        )
        return {"document": document, "workspace": self.workspace()}

    def import_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        filename = str(payload.get("filename") or "import.md").strip()
        content = str(payload.get("content") or "")
        if not content.strip():
            raise StudioError("导入内容不能为空")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".txt", ".md", ".markdown"}:
            raise StudioError("当前仅支持 TXT 和 Markdown 导入")
        arc_id = str(payload.get("arc_id") or self.config.get("current_arc") or "arc_001")
        if not re.fullmatch(r"arc_\d+", arc_id):
            raise StudioError("篇 ID 必须形如 arc_001")
        start_number = payload.get("start_number")
        if start_number in {None, ""}:
            start = None
        else:
            try:
                start = int(start_number)
            except (TypeError, ValueError) as exc:
                raise StudioError("起始章节必须是整数") from exc
        with self._write_lock, tempfile.TemporaryDirectory(prefix="openwrite-import-") as temp_dir:
            source = Path(temp_dir) / f"source{suffix}"
            source.write_text(content, encoding="utf-8")
            try:
                result = self._service().import_book(
                    source,
                    arc_id=arc_id,
                    start_number=start,
                    force=bool(payload.get("force")),
                )
            except NovelServiceError as exc:
                raise self._translate_service_error(exc) from exc
        return {
            "imported": result["imported"],
            "workspace": self.workspace(),
        }

    def context_preview(self, chapter_id: str) -> dict[str, Any]:
        try:
            result = self._service().context_preview(chapter_id)
        except NovelServiceError as exc:
            raise self._translate_service_error(exc) from exc
        packet = result.pop("packet", None)
        if isinstance(packet, dict):
            result["manifest"] = build_context_manifest(self.novel_root, packet)
        return result

    def outline_structure(self, chapter_id: str = "") -> dict[str, Any]:
        """Return the live outline tree and an optional chapter recommendation."""
        self.require_project()
        return build_outline_structure(self.novel_root, chapter_id=chapter_id)

    def edit_outline_structure(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply one atomic tree edit while preserving the Markdown source of truth."""
        self.require_project()
        document = self.read_document("src/outline.md")
        self._debug_event(
            "outline_edit_requested",
            operation=payload.get("operation"),
            node_id=payload.get("node_id"),
            kind=payload.get("kind"),
            title=payload.get("title"),
            summary=payload.get("summary"),
            revision=payload.get("revision"),
        )
        try:
            edit = mutate_outline_structure(
                self.novel_root,
                operation=str(payload.get("operation") or ""),
                revision=str(payload.get("revision") or ""),
                node_id=str(payload.get("node_id") or ""),
                title=str(payload.get("title") or ""),
                summary=str(payload.get("summary") or ""),
                kind=str(payload.get("kind") or ""),
            )
        except OutlineEditError as exc:
            status = HTTPStatus.CONFLICT if exc.code == "conflict" else HTTPStatus.BAD_REQUEST
            raise StudioError(str(exc), status) from exc
        saved = self.write_document(
            "src/outline.md",
            str(edit["content"]),
            document["version"],
        )
        outline = self.outline_structure()
        selected_node_id = self._resolve_outline_selection(
            outline,
            edit.get("selection_hint", {}),
        )
        result = {
            "outline": outline,
            "selected_node_id": selected_node_id,
            "message": str(edit["message"]),
            "renumbered": edit.get("renumbered", []),
            "skipped_renumbering": edit.get("skipped_renumbering", []),
            "checkpoint": saved.get("checkpoint", {}),
        }
        self._debug_event(
            "outline_edit_completed",
            operation=payload.get("operation"),
            node_id=payload.get("node_id"),
            selected_node_id=selected_node_id,
            message=result["message"],
            renumbered=result["renumbered"],
            skipped_renumbering=result["skipped_renumbering"],
        )
        return result

    @staticmethod
    def _resolve_outline_selection(
        outline: dict[str, Any], hint: dict[str, Any]
    ) -> str:
        nodes: list[dict[str, Any]] = []
        pending = list(reversed(outline.get("roots", [])))
        while pending:
            node = pending.pop()
            nodes.append(node)
            pending.extend(reversed(node.get("children", [])))
        parent_id = str(hint.get("parent_id") or "")
        if parent_id and any(node["id"] == parent_id for node in nodes):
            return parent_id
        matches = [
            node
            for node in nodes
            if node.get("kind") == hint.get("kind")
            and node.get("title") == hint.get("title")
        ]
        if matches:
            expected_line = int(hint.get("line") or matches[0]["line"])
            return min(matches, key=lambda node: abs(int(node["line"]) - expected_line))["id"]
        return str(outline.get("recommendation", {}).get("chapter_id") or "")

    def search_project(
        self, query: str, scope: str = "all", limit: int = 20
    ) -> dict[str, Any]:
        self.require_project()
        try:
            return ProjectSearchIndex(self.novel_root).search(
                query, scope=scope, limit=limit
            )
        except (OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc

    def continuity(self) -> dict[str, Any]:
        try:
            return self._service().continuity()
        except NovelServiceError as exc:
            raise self._translate_service_error(exc) from exc

    def manage_foreshadowing(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._service().manage_foreshadowing(payload)
        except NovelServiceError as exc:
            raise self._translate_service_error(exc) from exc
        return {**result, "workspace": self.workspace()}

    def agent_surface(
        self,
        agent_name: str,
        limit: int = 200,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Expose the live Agent tool catalog and persisted transcript to Studio."""
        self.require_project()
        agent_name = self._normalize_agent_name(agent_name)
        active_session_id = self._normalize_agent_session_id(session_id)
        limit = max(1, min(int(limit), 500))
        return {
            "agent": agent_name,
            "active_session_id": active_session_id,
            "sessions": self._agent_session_catalog(agent_name),
            "tools": self._agent_tool_catalog(agent_name),
            "history": self._agent_history(agent_name, limit, active_session_id),
        }

    def create_agent_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.require_project()
        agent_name = self._normalize_agent_name(payload.get("agent") or "goethe")
        session_id = self._new_agent_session_id(agent_name)
        self._debug_event(
            "agent_session_create_requested",
            agent=agent_name,
            session_id=session_id,
        )
        self._agent_session_store(agent_name, session_id).load_or_create()
        result = {
            **self.agent_surface(agent_name, limit=80, session_id=session_id),
            "created": True,
        }
        self._debug_event(
            "agent_session_create_completed",
            agent=agent_name,
            session_id=session_id,
        )
        return result

    def delete_agent_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.require_project()
        agent_name = self._normalize_agent_name(payload.get("agent") or "goethe")
        session_id = self._normalize_agent_session_id(payload.get("session_id"))
        if session_id == "default":
            raise StudioError("默认会话不能删除")
        store = self._agent_session_store(agent_name, session_id)
        session_root = self.novel_root / "data" / "workflows" / "sessions" / agent_name
        self._debug_event(
            "agent_session_delete_requested",
            agent=agent_name,
            session_id=session_id,
            state_path=self._project_relative_path(store.path),
            transcript_path=self._project_relative_path(store.transcript_path),
        )
        candidates = {
            store.path,
            store.transcript_path,
            session_root / f"{store.path.stem}.yml",
        }
        deleted_files = []
        for path in sorted(candidates):
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError as exc:
                raise StudioError(f"删除会话失败: {path.name}") from exc
            deleted_files.append(self._project_relative_path(path))
        result = {
            **self.agent_surface(agent_name, limit=80, session_id="default"),
            "deleted": True,
            "deleted_session_id": session_id,
            "deleted_files": deleted_files,
        }
        self._debug_event(
            "agent_session_delete_completed",
            agent=agent_name,
            session_id=session_id,
            deleted_files=deleted_files,
        )
        return result

    @staticmethod
    def _normalize_agent_name(agent_name: Any) -> str:
        normalized = str(agent_name or "goethe").strip().lower()
        if normalized not in {"goethe", "dante"}:
            raise StudioError("Agent 仅支持 goethe 或 dante")
        return normalized

    @staticmethod
    def _normalize_agent_session_id(session_id: Any) -> str:
        normalized = str(session_id or "default").strip()
        if normalized in {"", "default"}:
            return "default"
        if len(normalized) > 128 or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", normalized
        ):
            raise StudioError("会话 ID 格式无效")
        return normalized

    @staticmethod
    def _new_agent_session_id(agent_name: str) -> str:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return f"{agent_name}-{timestamp}-{uuid.uuid4().hex[:6]}"

    def _agent_session_store(self, agent_name: str, session_id: str | None):
        normalized = self._normalize_agent_session_id(session_id)
        store_session_id = None if normalized == "default" else normalized
        if agent_name == "goethe":
            from tools.agent.goethe_session_state import GoetheSessionStateStore

            return GoetheSessionStateStore(
                self.project_root,
                self.novel_id,
                session_id=store_session_id,
            )
        from tools.agent.session_state import SessionStateStore

        return SessionStateStore(
            self.project_root,
            self.novel_id,
            session_id=store_session_id,
        )

    def _agent_session_catalog(self, agent_name: str) -> list[dict[str, Any]]:
        sessions = [
            self._agent_session_summary(
                agent_name,
                "default",
                self._agent_session_store(agent_name, "default"),
                is_default=True,
            )
        ]
        session_root = self.novel_root / "data" / "workflows" / "sessions" / agent_name
        if session_root.is_dir():
            stems = {
                path.stem
                for path in session_root.iterdir()
                if path.is_file() and path.suffix in {".jsonl", ".yaml", ".yml"}
            }
            for stem in sorted(stems):
                sessions.append(
                    self._agent_session_summary(
                        agent_name,
                        stem,
                        self._agent_session_store(agent_name, stem),
                        is_default=False,
                    )
                )
        default = sessions[:1]
        named = sorted(
            sessions[1:],
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        return default + named

    def _agent_session_summary(
        self,
        agent_name: str,
        session_id: str,
        store: Any,
        *,
        is_default: bool,
    ) -> dict[str, Any]:
        transcript = self._read_agent_transcript(store.transcript_path, limit=1)
        state_updated = self._read_agent_state_updated_at(store.path)
        updated_at = transcript.get("updated_at") or state_updated
        first_user = str(transcript.get("first_user") or "").strip()
        last_preview = str(transcript.get("last_preview") or "").strip()
        label = "默认会话" if is_default else "新会话"
        title_source = first_user or last_preview
        title = self._agent_session_title(title_source, label)
        return {
            "id": session_id,
            "title": title,
            "agent": agent_name,
            "messages": int(transcript.get("total") or 0),
            "updated_at": updated_at,
            "preview": self._agent_session_preview(last_preview),
            "is_default": is_default,
            "state_path": self._project_relative_path(store.path),
            "transcript_path": self._project_relative_path(store.transcript_path),
            "exists": store.path.exists() or store.transcript_path.exists(),
        }

    @staticmethod
    def _agent_session_title(content: str, fallback: str) -> str:
        compact = " ".join(str(content or "").split())
        if not compact:
            return fallback
        return compact[:28] + ("..." if len(compact) > 28 else "")

    @staticmethod
    def _agent_session_preview(content: str) -> str:
        compact = " ".join(str(content or "").split())
        return compact[:64] + ("..." if len(compact) > 64 else "")

    @staticmethod
    def _read_agent_state_updated_at(path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return ""
        if not isinstance(data, dict):
            return ""
        return str(data.get("updated_at") or "")

    def _project_relative_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.novel_root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _agent_tool_catalog(agent_name: str) -> list[dict[str, str]]:
        if agent_name == "goethe":
            from tools.agent.toolkits import GOETHE_ACTION_TOOLKIT
            from tools.goethe import _build_goethe_tool_definitions

            definitions = _build_goethe_tool_definitions()
            action_tools = GOETHE_ACTION_TOOLKIT
        else:
            from tools.agent.dante import _build_dante_tool_definitions
            from tools.agent.toolkits import DANTE_ACTION_TOOLKIT

            definitions = _build_dante_tool_definitions()
            action_tools = DANTE_ACTION_TOOLKIT
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "kind": "action" if tool.name in action_tools else "direct",
            }
            for tool in definitions
        ]

    def _read_agent_transcript(self, path: Path, limit: int) -> dict[str, Any]:
        records: deque[dict[str, Any]] = deque(maxlen=limit)
        total = 0
        first_user = ""
        last_preview = ""
        updated_at = ""
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(record, dict):
                            continue
                        role = str(record.get("role") or "").strip().lower()
                        content = str(record.get("content") or "")
                        if role not in {"user", "assistant"} or not content:
                            continue
                        total += 1
                        if role == "user" and not first_user:
                            first_user = content
                        last_preview = content
                        updated_at = str(record.get("timestamp") or updated_at)
                        records.append(
                            {
                                "role": role,
                                "content": content,
                                "content_html": (
                                    render_chat_markdown(content)
                                    if role == "assistant"
                                    else ""
                                ),
                                "timestamp": str(record.get("timestamp") or ""),
                            }
                        )
            except (OSError, UnicodeDecodeError):
                records.clear()
                total = 0
                first_user = ""
                last_preview = ""
                updated_at = ""
        return {
            "messages": list(records),
            "shown": len(records),
            "total": total,
            "has_more": total > len(records),
            "first_user": first_user,
            "last_preview": last_preview,
            "updated_at": updated_at,
        }

    def _agent_history(
        self,
        agent_name: str,
        limit: int,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        store = self._agent_session_store(agent_name, session_id)
        transcript = self._read_agent_transcript(store.transcript_path, limit)
        session_id = self._normalize_agent_session_id(session_id)
        return {
            **transcript,
            "session_id": session_id,
            "path": self._project_relative_path(store.transcript_path),
        }

    def chat_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not os.environ.get("LLM_API_KEY", "").strip():
            raise StudioError("未配置 LLM_API_KEY", HTTPStatus.PRECONDITION_FAILED)
        agent_name = self._normalize_agent_name(payload.get("agent") or "dante")
        session_id = self._normalize_agent_session_id(payload.get("session_id"))
        run_id = self._normalize_activity_run_id(payload.get("run_id"))
        message = str(payload.get("message") or "").strip()
        if not message or len(message) > 12000:
            raise StudioError("消息不能为空且不能超过 12000 字")
        self._start_agent_activity(
            run_id,
            agent=agent_name,
            session_id=session_id,
        )
        if not self._write_lock.acquire(blocking=False):
            self._finish_agent_activity(
                run_id,
                status="error",
                message="已有 AI 任务正在运行。",
            )
            raise StudioError("已有 AI 任务正在运行", HTTPStatus.CONFLICT)
        self._debug_event(
            "chat_turn_started",
            agent=agent_name,
            session_id=session_id,
            run_id=run_id,
            message_chars=len(message),
            message_preview=message,
            book_state=self._debug_book_state(),
        )
        try:
            if self._chat_executor is not None:
                self._record_agent_activity(
                    run_id,
                    {"event": "model_started", "turn": 1},
                )
                result = self._chat_executor(
                    self.project_root, self.novel_id, agent_name, message
                )
                self._record_agent_activity(
                    run_id,
                    {"event": "response_ready", "turn": 1},
                )
            elif agent_name == "goethe":
                from tools.goethe import GoetheChatAgent

                session_store = self._agent_session_store(agent_name, session_id)
                response = GoetheChatAgent(
                    self.project_root,
                    self.novel_id,
                    session_store=session_store,
                    activity_callback=lambda event: self._record_agent_activity(
                        run_id, event
                    ),
                ).respond(message)
                result = {"content": response}
            else:
                from tools.agent.dante import DanteChatAgent
                from tools.agent.tool_layers import build_dante_tool_layers

                layers = build_dante_tool_layers(self.project_root)
                session_store = self._agent_session_store(agent_name, session_id)
                agent = DanteChatAgent(
                    self.project_root,
                    self.novel_id,
                    session_store=session_store,
                    tool_executors=layers.get("direct_tool_executors", {}),
                    action_executors=layers.get("action_tool_executors", {}),
                    activity_callback=lambda event: self._record_agent_activity(
                        run_id, event
                    ),
                )
                result = {"content": agent.respond(message)}
        except Exception as exc:
            self._finish_agent_activity(
                run_id,
                status="error",
                message=str(exc),
            )
            raise
        finally:
            self._write_lock.release()
        if result.get("error"):
            self._debug_event(
                "chat_turn_failed",
                agent=agent_name,
                session_id=session_id,
                error=str(result["error"]),
                book_state=self._debug_book_state(),
            )
            self._finish_agent_activity(
                run_id,
                status="error",
                message=str(result["error"]),
            )
            raise StudioError(str(result["error"]), HTTPStatus.BAD_GATEWAY)
        content = str(result.get("content") or "")
        self._finish_agent_activity(run_id, status="complete")
        self._debug_event(
            "chat_turn_completed",
            agent=agent_name,
            session_id=session_id,
            content_chars=len(content),
            content_preview=content,
            book_state=self._debug_book_state(),
        )
        return {
            "agent": agent_name,
            "session_id": session_id,
            "run_id": run_id,
            "content": content,
            "content_html": render_chat_markdown(content),
            "workspace": self.workspace(),
        }

    def source_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        try:
            if action == "extract":
                if not os.environ.get("LLM_API_KEY", "").strip():
                    raise StudioError("未配置 LLM_API_KEY", HTTPStatus.PRECONDITION_FAILED)
                text = str(payload.get("content") or "")
                if not text.strip():
                    raise StudioError("来源文本不能为空")
                with tempfile.TemporaryDirectory(prefix="openwrite-source-") as temp_dir:
                    source = Path(temp_dir) / "source.txt"
                    source.write_text(text, encoding="utf-8")
                    result = self._service().extract_source(
                        source_id=source_id,
                        source_file=source,
                        focus=str(payload.get("focus") or "style"),
                    )
            elif action == "review":
                result = self._service().review_source(source_id)
            elif action == "promote":
                result = self._service().promote_source(
                    source_id,
                    str(payload.get("target") or "all"),
                )
            elif action == "synthesize":
                result = self._service().synthesize_style(source_id)
            else:
                raise StudioError("未知来源操作")
        except NovelServiceError as exc:
            raise self._translate_service_error(exc) from exc
        return {"result": result, "workspace": self.workspace()}

    def write_next_chapter(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not os.environ.get("LLM_API_KEY", "").strip():
            raise StudioError("未配置 LLM_API_KEY", HTTPStatus.PRECONDITION_FAILED)
        try:
            target_words = int(payload.get("target_words") or 3000)
        except (TypeError, ValueError) as exc:
            raise StudioError("目标字数必须是整数") from exc
        if not 200 <= target_words <= 12000:
            raise StudioError("目标字数必须在 200 到 12000 之间")
        guidance = str(payload.get("guidance") or "").strip()
        requested_chapter = str(payload.get("chapter_id") or "").strip()
        outline = self.outline_structure(requested_chapter)
        expected_revision = str(payload.get("outline_revision") or "").strip()
        if expected_revision and expected_revision != outline["revision"]:
            raise StudioError(
                "大纲已变化，请重新查看章节建议后再创建",
                HTTPStatus.CONFLICT,
            )
        recommendation = outline.get("recommendation")
        if requested_chapter:
            if not recommendation or recommendation["chapter_id"] != requested_chapter:
                raise StudioError("所选章节不在当前大纲中")
            if recommendation["status"] == "drafted":
                raise StudioError("该章节已有正文，请从正文列表打开", HTTPStatus.CONFLICT)
        chapter_id = (
            str(recommendation["chapter_id"])
            if isinstance(recommendation, dict)
            else "next"
        )
        self._debug_event(
            "write_chapter_requested",
            chapter_id=chapter_id,
            requested_chapter=requested_chapter,
            target_words=target_words,
            guidance=guidance,
            outline_revision=outline.get("revision"),
            book_state=self._debug_book_state(),
        )
        try:
            result = self._service().write_chapter(
                {
                    "chapter_id": chapter_id,
                    "guidance": guidance,
                    "target_words": target_words,
                    "temperature": float(payload.get("temperature") or 0.7),
                }
            )
        except NovelServiceError as exc:
            self._debug_event(
                "write_chapter_failed",
                chapter_id=chapter_id,
                error=str(exc),
                code=getattr(exc, "code", ""),
                book_state=self._debug_book_state(),
            )
            raise self._translate_service_error(exc) from exc
        self._debug_event(
            "write_chapter_completed",
            chapter_id=result.get("chapter_id", chapter_id),
            title=result.get("title", ""),
            word_count=result.get("word_count", 0),
            draft_path=result.get("draft_path", ""),
            book_state=self._debug_book_state(),
        )
        return {"result": result, "workspace": self.workspace()}

    def review_chapter(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not os.environ.get("LLM_API_KEY", "").strip():
            raise StudioError("未配置 LLM_API_KEY", HTTPStatus.PRECONDITION_FAILED)
        relative_path = str(payload.get("path") or "")
        path = self._resolve_document(relative_path, write=False)
        manuscript_root = (self.novel_root / "data" / "manuscript").resolve()
        if manuscript_root not in path.parents or not re.fullmatch(r"ch_\d+", path.stem):
            raise StudioError("只能审查正文章节")

        try:
            result = self._service().review_chapter(path.stem)
        except NovelServiceError as exc:
            self._debug_event(
                "review_chapter_failed",
                chapter_id=path.stem,
                error=str(exc),
                code=getattr(exc, "code", ""),
            )
            raise self._translate_service_error(exc) from exc
        self._debug_event(
            "review_chapter_completed",
            chapter_id=path.stem,
            passed=result.get("passed"),
            score=result.get("score"),
            issues=result.get("issues"),
        )
        return {"result": result, "workspace": self.workspace()}

    def export_download(self, format_name: str) -> tuple[str, bytes, str]:
        if format_name not in {"md", "txt"}:
            raise StudioError("导出格式仅支持 md 或 txt")
        title = str(self.config.get("title") or self.novel_id)
        with tempfile.TemporaryDirectory(prefix="openwrite-export-") as temp_dir:
            output = Path(temp_dir) / f"{self.novel_id}.{format_name}"
            try:
                self._service().export_book(
                    output,
                    format_name=format_name,
                    title=title,
                )
            except NovelServiceError as exc:
                raise self._translate_service_error(exc) from exc
            content = output.read_bytes()
        mime = (
            "text/markdown; charset=utf-8"
            if format_name == "md"
            else "text/plain; charset=utf-8"
        )
        return f"{self.novel_id}.{format_name}", content, mime

    def _load_config(self) -> dict[str, Any]:
        try:
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise StudioError(f"项目配置无法读取: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def _document_groups(self, chapters: list[Any]) -> dict[str, list[dict[str, Any]]]:
        src = self.novel_root / "src"
        groups = {
            "outline": [],
            "story": self._collect_documents(src / "story", recursive=False),
            "characters": self._collect_documents(src / "characters", recursive=False),
            "world": self._collect_documents(src / "world", recursive=True),
            "chapters": [self._chapter_summary(item) for item in chapters],
        }
        outline = src / "outline.md"
        if outline.exists():
            groups["outline"].append(self._document_summary(outline))
        return groups

    def _chapter_summary(self, item: Any) -> dict[str, Any]:
        review = self._load_review_result(item.chapter_id)
        subtitle = f"{item.chapter_id} · {item.writing_units:,} 字"
        if review:
            subtitle += f" · {float(review.get('score', 0)):.0f} 分"
        return {
            "path": self._relative(item.path),
            "title": item.title,
            "subtitle": subtitle,
            "review": review,
        }

    def _load_review_result(self, chapter_id: str) -> dict[str, Any] | None:
        from tools.review_store import ReviewStore

        data = ReviewStore(self.project_root, self.novel_id).load(chapter_id)
        if data is None:
            return None
        return {
            "score": float(data.get("score") or 0),
            "passed": bool(data.get("passed")),
            "issues": int(data.get("issues") or 0),
            "reviewed_at": str(data.get("reviewed_at") or ""),
        }

    def _collect_documents(self, root: Path, *, recursive: bool) -> list[dict[str, Any]]:
        if not root.exists():
            return []
        iterator = root.rglob("*.md") if recursive else root.glob("*.md")
        return [self._document_summary(path) for path in sorted(iterator) if path.is_file()]

    def _document_summary(self, path: Path) -> dict[str, Any]:
        return {
            "path": self._relative(path),
            "title": self._document_title(path),
            "subtitle": str(path.relative_to(self.novel_root).parent),
        }

    def _document_title(self, path: Path) -> str:
        try:
            head = path.read_text(encoding="utf-8")[:2000]
        except OSError:
            return path.stem
        match = re.search(r"^#\s+(.+?)\s*$", head, re.MULTILINE)
        return match.group(1).strip() if match else path.stem.replace("_", " ")

    def _resolve_document(self, relative_path: str, *, write: bool) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise StudioError("缺少文档路径")
        candidate = (self.novel_root / relative_path).resolve()
        allowed_roots = [
            (self.novel_root / "src").resolve(),
            (self.novel_root / "data" / "manuscript").resolve(),
        ]
        if not any(candidate == root or root in candidate.parents for root in allowed_roots):
            raise StudioError("文档路径不在 Studio 可访问范围", HTTPStatus.FORBIDDEN)
        if candidate.suffix.lower() != ".md":
            raise StudioError("Studio 仅编辑 Markdown 文档")
        if write and candidate.is_symlink():
            raise StudioError("不允许写入符号链接", HTTPStatus.FORBIDDEN)
        return candidate

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.novel_root).as_posix()

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            values = value.splitlines()
        elif isinstance(value, list):
            values = value
        else:
            return []
        return [str(item).strip().removeprefix("- ") for item in values if str(item).strip()]


class StudioRequestHandler(SimpleHTTPRequestHandler):
    server_version = "OpenWriteStudio/5.8"

    @property
    def app(self) -> StudioApplication:
        return cast(StudioApplication, getattr(self.server, "app"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._json({"ok": True})
                return
            if parsed.path == "/api/workspace":
                self._json(self.app.workspace())
                return
            if parsed.path == "/api/continuity":
                self.app.require_project()
                self._json(self.app.continuity())
                return
            if parsed.path == "/api/agents":
                params = parse_qs(parsed.query)
                agent_name = params.get("agent", ["goethe"])[0]
                session_id = params.get("session_id", ["default"])[0]
                try:
                    limit = int(params.get("limit", ["200"])[0])
                except ValueError as exc:
                    raise StudioError("历史消息数量必须是整数") from exc
                self._json(self.app.agent_surface(agent_name, limit, session_id))
                return
            if parsed.path == "/api/agent/activity":
                run_id = parse_qs(parsed.query).get("run_id", [""])[0]
                self._json(self.app.agent_activity(run_id))
                return
            if parsed.path == "/api/context":
                self.app.require_project()
                chapter_id = parse_qs(parsed.query).get("chapter", ["next"])[0]
                self._json(self.app.context_preview(chapter_id))
                return
            if parsed.path == "/api/outline":
                chapter_id = parse_qs(parsed.query).get("chapter", [""])[0]
                self._json(self.app.outline_structure(chapter_id))
                return
            if parsed.path == "/api/search":
                self.app.require_project()
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                scope = params.get("scope", ["all"])[0]
                try:
                    limit = int(params.get("limit", ["20"])[0])
                except ValueError as exc:
                    raise StudioError("搜索数量必须是整数") from exc
                self._json(self.app.search_project(query, scope, limit))
                return
            if parsed.path == "/api/document":
                self.app.require_project()
                path = parse_qs(parsed.query).get("path", [""])[0]
                self._json(self.app.read_document(path))
                return
            if parsed.path == "/api/export":
                self.app.require_project()
                format_name = parse_qs(parsed.query).get("format", ["md"])[0]
                filename, content, mime = self.app.export_download(format_name)
                self.send_response(HTTPStatus.OK)
                self._security_headers()
                self.send_header("Content-Type", mime)
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{quote(filename)}",
                )
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if parsed.path in {"/brand/logo.svg", "/brand/logo-dark.svg"}:
                self._serve_brand_logo(parsed.path.endswith("dark.svg"))
                return
            self._serve_static(parsed.path)
        except StudioError as exc:
            self._debug_http_error(exc.status, str(exc))
            self._json({"error": str(exc)}, status=exc.status)
        except Exception as exc:
            self._debug_http_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                exc.__class__.__name__,
            )
            self._json({"error": "Studio 内部错误"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        try:
            self._require_write_header()
            if urlparse(self.path).path != "/api/document":
                raise StudioError("接口不存在", HTTPStatus.NOT_FOUND)
            self.app.require_project()
            payload = self._body_json()
            result = self.app.write_document(
                str(payload.get("path") or ""),
                str(payload.get("content") or ""),
                (
                    payload.get("version")
                    if isinstance(payload.get("version"), (str, int))
                    else None
                ),
                force=bool(payload.get("force")),
            )
            self._json(result)
        except StudioError as exc:
            self._debug_http_error(exc.status, str(exc))
            self._json({"error": str(exc)}, status=exc.status)

    def do_POST(self) -> None:
        try:
            self._require_write_header()
            route = urlparse(self.path).path
            payload = self._body_json()
            if route == "/api/focus":
                self._json(self.app.update_focus(payload))
                return
            if route == "/api/model":
                self._json(self.app.configure_model(payload))
                return
            if route == "/api/model/test":
                self._json(self.app.test_model_connection(payload))
                return
            if route == "/api/project/init":
                self._json(self.app.initialize_project(payload))
                return
            if route == "/api/project/open":
                self._json(self.app.open_project(payload))
                return
            self.app.require_project()
            if route == "/api/write":
                self._json(self.app.write_next_chapter(payload))
                return
            if route == "/api/outline/edit":
                self._json(self.app.edit_outline_structure(payload))
                return
            if route == "/api/review":
                self._json(self.app.review_chapter(payload))
                return
            if route == "/api/sync":
                self._json(self.app.sync_project())
                return
            if route == "/api/document/create":
                self._json(self.app.create_document(payload))
                return
            if route == "/api/import":
                self._json(self.app.import_text(payload))
                return
            if route == "/api/foreshadowing":
                self._json(self.app.manage_foreshadowing(payload))
                return
            if route == "/api/chat":
                self._json(self.app.chat_turn(payload))
                return
            if route == "/api/agent/session":
                self._json(self.app.create_agent_session(payload))
                return
            if route == "/api/agent/session/delete":
                self._json(self.app.delete_agent_session(payload))
                return
            if route == "/api/source":
                self._json(self.app.source_action(payload))
                return
            raise StudioError("接口不存在", HTTPStatus.NOT_FOUND)
        except StudioError as exc:
            self._debug_http_error(exc.status, str(exc))
            self._json({"error": str(exc)}, status=exc.status)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self._security_headers()
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        if self.path.startswith("/api/") and not self.path.startswith("/api/health"):
            super().log_message(format, *args)

    def _debug_http_error(self, status: HTTPStatus | int, message: str) -> None:
        if not self.app.debug_enabled:
            return
        self.app._debug_event(
            "http_error",
            method=self.command,
            path=urlparse(self.path).path,
            status=int(status),
            message=message,
        )

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in path.parents and path != STATIC_ROOT.resolve():
            raise StudioError("资源不存在", HTTPStatus.NOT_FOUND)
        if not path.is_file():
            path = STATIC_ROOT / "index.html"
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_brand_logo(self, dark: bool) -> None:
        path = STATIC_ROOT / ("logo-dark.svg" if dark else "logo.svg")
        if not path.is_file():
            raise StudioError("品牌资源不存在", HTTPStatus.NOT_FOUND)
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _body_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise StudioError("无效请求长度") from exc
        if length <= 0 or length > MAX_DOCUMENT_BYTES + 65536:
            raise StudioError("无效请求体", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StudioError("请求 JSON 无效") from exc
        if not isinstance(payload, dict):
            raise StudioError("请求必须是 JSON 对象")
        return payload

    def _require_write_header(self) -> None:
        if self.headers.get(WRITE_HEADER) != "1":
            raise StudioError("缺少 Studio 写入凭证", HTTPStatus.FORBIDDEN)

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")


class OpenWriteStudioServer(ThreadingHTTPServer):
    app: StudioApplication


def create_server(
    project_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 4567,
    writer_executor: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
    review_executor: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
    chat_executor: Callable[[Path, str, str, str], dict[str, Any]] | None = None,
    source_executor: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
    project_registry: ProjectRegistry | None = None,
    model_settings_store: StudioModelSettingsStore | None = None,
    debug: bool = False,
) -> OpenWriteStudioServer:
    if not STATIC_ROOT.is_dir():
        raise StudioError(f"Studio 静态资源缺失: {STATIC_ROOT}")
    app = StudioApplication(
        project_root,
        writer_executor=writer_executor,
        review_executor=review_executor,
        chat_executor=chat_executor,
        source_executor=source_executor,
        project_registry=project_registry or ProjectRegistry(),
        model_settings_store=model_settings_store,
        debug=debug,
    )
    handler = partial(StudioRequestHandler, directory=str(STATIC_ROOT))
    server = OpenWriteStudioServer((host, port), handler)
    server.app = app
    return server


def run_studio(
    project_root: Path,
    *,
    port: int = 4567,
    open_browser: bool = True,
    debug: bool = False,
) -> int:
    server = create_server(project_root, port=port, debug=debug)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"OpenWrite Studio: {url}")
    if server.app.debug_enabled and server.app.debug_log_path is not None:
        print(f"Debug log: {server.app.debug_log_path}")
    print("按 Ctrl+C 停止")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStudio 已停止")
    finally:
        server.server_close()
    return 0
