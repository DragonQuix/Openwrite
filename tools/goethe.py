"""Goethe 长会话规划 Shell。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .agent.goethe_session_state import GoetheSessionState, GoetheSessionStateStore, GoetheSessionTurn
from .agent.react import OPENWRITE_TOOLS, ReActAgent, ToolDefinition
from .agent.toolkits import GOETHE_DIRECT_TOOLKIT

logger = logging.getLogger(__name__)

EXIT_COMMANDS = {"退出", "quit", "exit", "q"}

GOETHE_TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_status": "读取当前书籍状态与运行信息。",
    "get_context": "读取章节上下文与近期材料。",
    "list_chapters": "列出已存在章节。",
    "get_truth_files": "读取运行态真相文件。",
    "query_world": "查询世界观、角色或实体资料。",
    "get_world_relations": "查询世界关系与关联。",
    "edit_world_relation": "预览或确认一个带 revision 校验的增量关系修改。",
    "get_outline_structure": "读取卷、幕、节、章树和下一章建议，不修改文件。",
    "edit_outline_structure": "按 revision 增量修改卷、幕、节、章；confirm=false 预览 diff，用户明确确认后才以 confirm=true 写入。",
    "summarize_ideation": "汇总当前灵感与讨论，形成共识摘要。",
    "generate_foundation_draft": "生成背景与基础设定草案。",
    "generate_character_draft": "生成角色草案。",
    "generate_outline_draft": "只在尚无大纲时生成首版完整草稿；不会直接写入 src。",
    "read_outline": "读取已确认大纲的原文窗口和 revision；局部修改前必须先调用。",
    "stage_outline_edits": "用精确 old_text/new_text 补丁暂存局部修改并返回 diff；不会写入 src。",
    "confirm_outline_edits": "仅在用户明确确认后，将待确认大纲补丁写入 src/outline.md。",
    "discard_outline_edits": "用户拒绝修改时丢弃待确认大纲补丁，不改变 src。",
    "extract_style_source": "从用户提供文本提取风格来源。",
    "extract_setting_source": "从用户提供文本提取设定来源。",
    "review_source_pack": "审阅已提取的来源包。",
    "promote_source_pack": "将来源包晋升到可写资产。",
    "prepare_dante_handoff": "检查当前资产是否满足切换到 Dante 的条件，并生成交接产物。",
}


def is_exit_command(text: str) -> bool:
    return text.strip().lower() in EXIT_COMMANDS


def build_prompt_session(history=None, *, prompt_style: dict[str, str] | None = None):
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.styles import Style
    except ImportError:
        logger.warning("prompt_toolkit not installed, falling back to basic input() shell")

        class FallbackPromptSession:
            def prompt(self, text: str) -> str:
                return input(text)

        return FallbackPromptSession()

    history = history or InMemoryHistory()
    style = Style.from_dict(prompt_style or {"prompt": "#ansibrightblue bold"})
    return PromptSession(history=history, style=style)


def build_goethe_prompt_session(history=None):
    return build_prompt_session(history=history)


def build_goethe_tool_layers(project_root: Path, novel_id: str | None = None) -> dict[str, object]:
    """构建 Goethe 工具分层视图，便于测试与 shell 复用。"""
    from .agent.tool_layers import build_goethe_tool_layers as _build

    return _build(project_root, novel_id)


@dataclass
class GoetheResult:
    """Goethe 运行结果。"""

    success: bool
    project_path: Optional[Path] = None
    novel_id: Optional[str] = None
    error: Optional[str] = None
    exit_reason: str = ""
    turns_processed: int = 0
    startup: object | None = None


@dataclass
class GoetheStartupSnapshot:
    session_state: GoetheSessionState
    recovery_prompt: str


DEFAULT_GOETHE_SYSTEM_PROMPT = """你是 OpenWrite 的 Goethe，长期会话规划 Agent。

你的职责是汇总灵感、提出建议、收敛人物/设定/大纲，并把确认后的资产整理成可写内容。
正文推进交给 Dante。

大纲修改必须遵守以下 ReAct 流程：
1. 普通问答、讨论、分析和征求建议只回复用户，不调用任何会修改大纲的工具。
2. 只有用户明确要求“生成大纲”且当前没有已确认大纲时，才能调用 generate_outline_draft。
3. 已有大纲时绝不整篇重写。先调用 read_outline 读取目标附近原文和 revision，再调用
   stage_outline_edits，以精确 old_text/new_text 修改最小必要片段；未提及内容必须逐字保留。
4. stage_outline_edits 只暂存草稿。调用后向用户展示 diff 摘要并等待确认，不得在同一轮自动调用
   confirm_outline_edits，除非用户在原始请求中明确说“直接应用/无需确认”。
5. 只有用户明确说“确认应用、采用这版、写入大纲”等肯定表达时，才能调用
   confirm_outline_edits。否定、犹豫或继续讨论时不得调用。
6. 用户说取消、不要这版或放弃修改时，调用 discard_outline_edits。
7. 工具返回 revision 冲突或 old_text 不唯一时，重新 read_outline；不得退回整篇生成覆盖。
8. 判断卷/幕/节/章位置或下一章时使用 get_outline_structure；它只读，不替代 src/outline.md。

关系修改遵守同样的确认边界：先调用 edit_world_relation 且 confirm=false 展示 diff；只有用户明确
确认后，才可使用预览返回的 base_revision 再次调用并设置 confirm=true。普通讨论不得写入关系。
"""


def _build_goethe_tool_definitions() -> list[ToolDefinition]:
    direct_tool_defs = [
        tool for tool in OPENWRITE_TOOLS if tool.name in GOETHE_DIRECT_TOOLKIT
    ]

    action_tool_defs = [
        ToolDefinition(
            name="summarize_ideation",
            description=GOETHE_TOOL_DESCRIPTIONS["summarize_ideation"],
            parameters={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="generate_foundation_draft",
            description=GOETHE_TOOL_DESCRIPTIONS["generate_foundation_draft"],
            parameters={
                "type": "object",
                "properties": {
                    "request_text": {"type": "string", "description": "规划请求"},
                },
            },
        ),
        ToolDefinition(
            name="generate_character_draft",
            description=GOETHE_TOOL_DESCRIPTIONS["generate_character_draft"],
            parameters={
                "type": "object",
                "properties": {
                    "request_text": {"type": "string", "description": "角色生成请求"},
                },
            },
        ),
        ToolDefinition(
            name="generate_outline_draft",
            description=GOETHE_TOOL_DESCRIPTIONS["generate_outline_draft"],
            parameters={
                "type": "object",
                "properties": {
                    "request_text": {"type": "string", "description": "大纲生成请求"},
                },
            },
        ),
        ToolDefinition(
            name="read_outline",
            description=GOETHE_TOOL_DESCRIPTIONS["read_outline"],
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "目标标题或关键词，例如“第六章”；优先使用",
                    },
                    "start_line": {"type": "integer", "description": "可选起始行"},
                    "end_line": {"type": "integer", "description": "可选结束行"},
                },
            },
        ),
        ToolDefinition(
            name="stage_outline_edits",
            description=GOETHE_TOOL_DESCRIPTIONS["stage_outline_edits"],
            parameters={
                "type": "object",
                "properties": {
                    "base_revision": {
                        "type": "string",
                        "description": "read_outline 返回的当前编辑版本 revision",
                    },
                    "edits": {
                        "type": "array",
                        "description": "按顺序执行的最小精确文本替换",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "从 read_outline 原样复制的唯一原文",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "替换后的文本；空字符串表示删除",
                                },
                                "replace_all": {
                                    "type": "boolean",
                                    "description": "仅在明确需要替换所有匹配时设为 true",
                                },
                            },
                            "required": ["old_text", "new_text"],
                        },
                    },
                },
                "required": ["base_revision", "edits"],
            },
            required=["base_revision", "edits"],
        ),
        ToolDefinition(
            name="confirm_outline_edits",
            description=GOETHE_TOOL_DESCRIPTIONS["confirm_outline_edits"],
            parameters={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="discard_outline_edits",
            description=GOETHE_TOOL_DESCRIPTIONS["discard_outline_edits"],
            parameters={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="extract_style_source",
            description=GOETHE_TOOL_DESCRIPTIONS["extract_style_source"],
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "来源 ID"},
                    "source": {"type": "string", "description": "来源文本或文件路径"},
                },
                "required": ["source_id", "source"],
            },
            required=["source_id", "source"],
        ),
        ToolDefinition(
            name="extract_setting_source",
            description=GOETHE_TOOL_DESCRIPTIONS["extract_setting_source"],
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "来源 ID"},
                    "source": {"type": "string", "description": "来源文本或文件路径"},
                },
                "required": ["source_id", "source"],
            },
            required=["source_id", "source"],
        ),
        ToolDefinition(
            name="review_source_pack",
            description=GOETHE_TOOL_DESCRIPTIONS["review_source_pack"],
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "来源 ID"},
                },
                "required": ["source_id"],
            },
            required=["source_id"],
        ),
        ToolDefinition(
            name="promote_source_pack",
            description=GOETHE_TOOL_DESCRIPTIONS["promote_source_pack"],
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "来源 ID"},
                    "target": {
                        "type": "string",
                        "description": "晋升目标: style, setting, world, all",
                    },
                },
                "required": ["source_id"],
            },
            required=["source_id"],
        ),
        ToolDefinition(
            name="prepare_dante_handoff",
            description=GOETHE_TOOL_DESCRIPTIONS["prepare_dante_handoff"],
            parameters={"type": "object", "properties": {}},
        ),
    ]
    return direct_tool_defs + action_tool_defs


class GoetheChatAgent:
    """Goethe 长会话规划 Agent。"""

    def __init__(
        self,
        project_root: Path | None = None,
        novel_id: str | None = None,
        *,
        session_store: GoetheSessionStateStore | None = None,
        prompt_session_factory: Callable[[], Any] | None = None,
        llm_client_factory: Callable[[], Any] | None = None,
        react_agent: Any | None = None,
        tool_layer_factory: Callable[[Path], dict[str, object]] | None = None,
        prompt_text: str = "\n🌿 Goethe> ",
    ):
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.novel_id = novel_id or self._load_novel_id()
        self.session_store = session_store or GoetheSessionStateStore(
            self.project_root, self.novel_id
        )
        self.prompt_session_factory = (
            prompt_session_factory
            or (lambda: build_goethe_prompt_session(history=None))
        )
        self.llm_client_factory = llm_client_factory or self._build_default_llm_client
        self.tool_layer_factory = tool_layer_factory or build_goethe_tool_layers
        self.prompt_text = prompt_text
        self._react_agent = react_agent
        self._react_agent_factory = (
            self._build_default_react_agent if react_agent is None else None
        )
        self._tool_layers: dict[str, object] | None = None
        self.session_state: GoetheSessionState | None = None
        self.recovery_prompt: str = ""
        self.startup_snapshot: GoetheStartupSnapshot | None = None
        self._active_user_instruction = ""

        if self._react_agent is not None:
            self._ensure_react_agent_surface(self._react_agent)

    def startup(self) -> GoetheStartupSnapshot:
        session_state = self.session_store.load_or_create()
        self.session_state = session_state
        self.recovery_prompt = self.build_recovery_prompt()
        self.startup_snapshot = GoetheStartupSnapshot(
            session_state=session_state,
            recovery_prompt=self.recovery_prompt,
        )
        return self.startup_snapshot

    def build_recovery_prompt(self) -> str:
        session_state = self._require_session_state()

        lines = [
            "Goethe 已恢复，可以继续上次的长期规划会话。",
            f"会话: {session_state.session_id} / active_agent={session_state.active_agent}",
        ]
        if session_state.conversation_summary:
            lines.append(f"会话摘要: {session_state.conversation_summary}")
        if session_state.working_memory:
            memory_bits = ", ".join(
                f"{key}={value}" for key, value in session_state.working_memory.items()
            )
            lines.append(f"工作记忆: {memory_bits}")
        if session_state.recent_turns:
            recent_lines = [
                f"{turn.role}: {turn.content}"
                for turn in session_state.recent_turns[-4:]
            ]
            lines.append("最近轮次:\n" + "\n".join(recent_lines))
        if session_state.open_questions:
            lines.append("未决问题: " + "；".join(session_state.open_questions))
        if session_state.recent_files:
            lines.append("最近文件: " + "；".join(session_state.recent_files))
        if session_state.last_action:
            lines.append(f"最近动作: {session_state.last_action}")
        return "\n".join(lines)

    def run(self) -> GoetheResult:
        startup = self.startup()
        session = self.prompt_session_factory()
        react_agent = self._get_react_agent()

        print("\n" + "=" * 50)
        print("   OpenWrite Goethe 长会话规划 Agent")
        print("   (输入 '退出'、'quit'、'exit' 或 'q' 可结束对话)")
        print("=" * 50)
        print(startup.recovery_prompt)

        turns_processed = 0
        while True:
            try:
                user_input = session.prompt(self.prompt_text).strip()
            except KeyboardInterrupt:
                state = self._require_session_state()
                state.last_action = "keyboard_interrupt"
                self.session_store.save(state)
                return GoetheResult(
                    success=True,
                    exit_reason="keyboard_interrupt",
                    turns_processed=turns_processed,
                    startup=startup,
                    project_path=self._project_path(),
                    novel_id=self.novel_id,
                )

            if not user_input:
                continue

            if is_exit_command(user_input):
                state = self._require_session_state()
                state.last_action = "exit"
                self.session_store.save(state)
                print("\n好的，随时欢迎回来！")
                return GoetheResult(
                    success=True,
                    exit_reason=user_input,
                    turns_processed=turns_processed,
                    startup=startup,
                    project_path=self._project_path(),
                    novel_id=self.novel_id,
                )

            if self._looks_like_handoff_request(user_input):
                handoff = self.prepare_dante_handoff()
                if handoff.get("ok"):
                    print(f"\n✅ Goethe 已完成交接: {handoff.get('handoff_markdown_path')}")
                    return GoetheResult(
                        success=True,
                        exit_reason="handoff_dante",
                        turns_processed=turns_processed,
                        startup=startup,
                        project_path=self._project_path(),
                        novel_id=self.novel_id,
                    )
                blocked_items = handoff.get("missing_items", [])
                blocked_text = "、".join(str(item) for item in blocked_items) if blocked_items else "未知"
                print(f"\n⚠️ 还不能切到 Dante，缺少: {blocked_text}")
                continue

            self._append_user_turn(user_input)
            state = self._require_session_state()
            state.last_action = "chat"
            self.session_store.save(state)

            try:
                response_text = self._run_react_agent(react_agent, user_input)
            except Exception:
                state.last_action = "react_error"
                self.session_store.save(state)
                raise

            if response_text:
                self._append_assistant_turn(response_text)
                print(f"\n🤖 Goethe: {response_text}")
            self.session_store.save(self._require_session_state())
            turns_processed += 1

    def respond(self, user_input: str) -> str:
        """Process one persisted Goethe turn for non-terminal clients."""
        text = str(user_input or "").strip()
        if not text:
            raise ValueError("消息不能为空")
        if self.session_state is None:
            self.startup()
        if self._looks_like_handoff_request(text):
            handoff = self.prepare_dante_handoff()
            if handoff.get("ok"):
                return "Goethe 已完成交接，可以切换到 Dante 继续正文创作。"
            missing = "、".join(
                str(item) for item in handoff.get("missing_items", [])
            ) or "必要资产"
            return f"暂时不能交接给 Dante，还缺少：{missing}。"
        self._append_user_turn(text)
        state = self._require_session_state()
        state.last_action = "chat"
        self.session_store.save(state)
        try:
            response_text = self._run_react_agent(self._get_react_agent(), text)
        except Exception:
            state.last_action = "react_error"
            self.session_store.save(state)
            raise
        if response_text:
            self._append_assistant_turn(response_text)
        self.session_store.save(self._require_session_state())
        return response_text

    def prepare_dante_handoff(self) -> dict[str, Any]:
        layers = self._load_tool_layers()
        action_executors = layers.get("action_tool_executors", {})
        if isinstance(action_executors, dict) and "prepare_dante_handoff" in action_executors:
            payload = action_executors["prepare_dante_handoff"]({})
        else:
            payload = {
                "action": "prepare_dante_handoff",
                "ok": False,
                "blocked": True,
                "error": "handoff_action_unavailable",
                "message": "未找到 Goethe handoff action。",
                "next_action": "continue_planning",
                "missing_items": [],
            }

        if self.session_state is not None and payload.get("ok"):
            self.session_state.last_action = "handoff_dante"
            self.session_store.save(self.session_state)
        return payload

    def _build_default_llm_client(self) -> Any:
        from .llm import LLMClient, LLMConfig

        return LLMClient(LLMConfig.from_env())

    def _build_default_react_agent(self) -> ReActAgent:
        client = self.llm_client_factory()
        react_agent = ReActAgent(
            client=client,
            model=client.config.model,
            tools=_build_goethe_tool_definitions(),
            system_prompt=DEFAULT_GOETHE_SYSTEM_PROMPT,
            max_turns=20,
        )
        combined = self._combined_tool_executors()
        if combined:
            react_agent._register_tool_executors(combined)
        return react_agent

    def _get_react_agent(self) -> Any:
        if self._react_agent is None:
            self._react_agent = self._react_agent_factory()
        self._ensure_react_agent_surface(self._react_agent)
        return self._react_agent

    def _ensure_react_agent_surface(self, react_agent: Any) -> None:
        if react_agent is None:
            return

        canonical_tools = {tool.name: tool for tool in _build_goethe_tool_definitions()}
        if hasattr(react_agent, "tools"):
            existing_tools = list(getattr(react_agent, "tools", []) or [])
            merged_tools = []
            seen: set[str] = set()
            for tool in existing_tools:
                tool_name = getattr(tool, "name", "")
                if not tool_name:
                    merged_tools.append(tool)
                    continue
                canonical_tool = canonical_tools.get(tool_name)
                if canonical_tool is not None:
                    merged_tools.append(canonical_tool)
                    seen.add(tool_name)
                else:
                    merged_tools.append(tool)
            for tool_name, canonical_tool in canonical_tools.items():
                if tool_name not in seen and all(
                    getattr(tool, "name", "") != tool_name for tool in merged_tools
                ):
                    merged_tools.append(canonical_tool)
            react_agent.tools = merged_tools

        if self._combined_tool_executors() and hasattr(react_agent, "_register_tool_executors"):
            react_agent._register_tool_executors(self._combined_tool_executors())

    def _run_react_agent(self, react_agent: Any, instruction: str) -> str:
        self._active_user_instruction = instruction
        try:
            result = react_agent.run(
                instruction,
                context_messages=self._build_context_messages(include_recent_turns=False),
            )
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            if result is None:
                return ""
            if isinstance(result, str):
                return result.strip()
            if hasattr(result, "content"):
                return str(getattr(result, "content", "")).strip()
            if isinstance(result, dict):
                return str(result.get("content", "")).strip()
            return str(result).strip()
        finally:
            self._active_user_instruction = ""

    def _build_context_messages(self, *, include_recent_turns: bool = True) -> list[Any]:
        from .llm import Message

        session_state = self._require_session_state()
        context_messages: list[Any] = []

        if session_state.conversation_summary:
            context_messages.append(
                Message("assistant", f"会话摘要: {session_state.conversation_summary}")
            )

        if session_state.working_memory:
            memory_bits = ", ".join(
                f"{key}={value}" for key, value in session_state.working_memory.items()
            )
            context_messages.append(Message("assistant", f"工作记忆: {memory_bits}"))

        recent_turns = session_state.recent_turns
        if not include_recent_turns and recent_turns:
            recent_turns = recent_turns[:-1]

        if recent_turns:
            recent_lines = [f"{turn.role}: {turn.content}" for turn in recent_turns]
            context_messages.append(
                Message("assistant", "最近轮次:\n" + "\n".join(recent_lines))
            )

        if session_state.open_questions:
            context_messages.append(
                Message("assistant", "未决问题: " + "；".join(session_state.open_questions))
            )

        if session_state.recent_files:
            context_messages.append(
                Message("assistant", "最近文件: " + "；".join(session_state.recent_files))
            )

        if session_state.last_action:
            context_messages.append(Message("assistant", f"最近动作: {session_state.last_action}"))

        return context_messages

    def _combined_tool_executors(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        layers = self._load_tool_layers()
        combined: dict[str, Callable[[dict[str, Any]], Any]] = {}
        tool_executors = layers.get("tool_executors", {})
        action_tool_executors = layers.get("action_tool_executors", {})
        if isinstance(tool_executors, dict):
            combined.update(tool_executors)
        if isinstance(action_tool_executors, dict):
            combined.update(action_tool_executors)
        confirm_executor = combined.get("confirm_outline_edits")
        if confirm_executor is not None:
            combined["confirm_outline_edits"] = lambda args: self._confirm_outline_if_explicit(
                confirm_executor, args
            )
        return combined

    def _confirm_outline_if_explicit(
        self,
        executor: Callable[[dict[str, Any]], Any],
        args: dict[str, Any],
    ) -> Any:
        if not self._looks_like_explicit_outline_confirmation(
            self._active_user_instruction
        ):
            return {
                "action": "confirm_outline_edits",
                "ok": False,
                "blocked": True,
                "error": "explicit_user_confirmation_required",
                "message": "尚未收到用户对待确认大纲 diff 的明确应用指令，src 未修改。",
                "next_action": "request_outline_confirmation",
            }
        return executor(args)

    @staticmethod
    def _looks_like_explicit_outline_confirmation(text: str) -> bool:
        normalized = "".join(str(text or "").strip().lower().split())
        if not normalized:
            return False
        if any(
            token in normalized
            for token in ("不确认", "先不", "暂不", "不要", "别", "取消", "放弃")
        ):
            return False
        if normalized in {"确认", "同意", "可以", "应用", "保存", "提交", "就这样"}:
            return True
        return any(
            token in normalized
            for token in (
                "确认应用",
                "确认修改",
                "确认大纲",
                "应用这版",
                "应用修改",
                "写入大纲",
                "保存大纲",
                "采用这版",
                "就按这版",
                "提交修改",
                "直接应用",
                "无需确认",
            )
        )

    def _load_tool_layers(self) -> dict[str, object]:
        if self._tool_layers is None:
            try:
                self._tool_layers = dict(
                    self.tool_layer_factory(self.project_root, self.novel_id)
                )
            except TypeError:
                self._tool_layers = dict(self.tool_layer_factory(self.project_root))
        return self._tool_layers

    def _append_user_turn(self, content: str) -> None:
        state = self._require_session_state()
        state.recent_turns.append(GoetheSessionTurn(role="user", content=content))
        self.session_store.append_turn("user", content)

    def _append_assistant_turn(self, content: str) -> None:
        state = self._require_session_state()
        state.recent_turns.append(GoetheSessionTurn(role="assistant", content=content))
        self.session_store.append_turn("assistant", content)

    def _project_path(self) -> Path:
        return self.project_root / "data" / "novels" / self.novel_id

    def _load_novel_id(self) -> str:
        config = self._load_config()
        novel_id = str(config.get("novel_id", "current")).strip()
        return novel_id or "current"

    def _load_config(self) -> dict[str, Any]:
        config_path = self.project_root / "novel_config.yaml"
        if not config_path.exists():
            fallback = self.project_root / "data" / "novels" / "current" / "novel_config.yaml"
            if not fallback.exists():
                return {}
            config_path = fallback
        try:
            import yaml

            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _require_session_state(self) -> GoetheSessionState:
        if self.session_state is None:
            raise RuntimeError("Goethe session has not been started")
        return self.session_state

    def _looks_like_handoff_request(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        return any(
            token in lowered
            for token in ("切到 dante", "切换到 dante", "开始写正文", "handoff", "交接给 dante")
        )

def run_goethe() -> int:
    """运行 Goethe 长会话规划 Shell。"""
    agent = GoetheChatAgent()
    result = agent.run()

    if result.success:
        if result.novel_id:
            print(f"\n✨ Goethe 会话已结束: {result.novel_id}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(run_goethe())
