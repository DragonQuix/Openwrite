"""ReAct Agent 实现

真正的 Agent 循环：
- 接收自然语言指令
- LLM 决定调用哪些工具
- 执行工具，返回结果
- 循环直到 LLM 确认完成
"""

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _redact_debug_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<max-depth>"
    if isinstance(value, dict):
        payload = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(
                token in lowered
                for token in ("api_key", "apikey", "authorization", "password", "secret", "token")
            ):
                payload[key_text] = "<redacted>"
            elif lowered in {"content", "message", "source", "prompt", "guidance"}:
                payload[key_text] = _debug_string_preview(item)
            else:
                payload[key_text] = _redact_debug_value(item, depth=depth + 1)
        return payload
    if isinstance(value, list):
        return [_redact_debug_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return _debug_string_preview(value)
    return value


def _debug_string_preview(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").replace("\n", "\\n")
    return text[:limit] + ("..." if len(text) > limit else "")


def _debug_json(value: Any) -> str:
    return json.dumps(
        _redact_debug_value(value),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


@dataclass
class ToolDefinition:
    """工具定义"""

    name: str
    description: str
    parameters: dict
    required: list[str] = field(default_factory=list)


@dataclass
class ToolCall:
    """工具调用"""

    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """工具执行结果"""

    tool_call_id: str
    result: str
    error: str | None = None


class ReActAgent:
    """ReAct Agent

    真正的 Agent 循环：
    1. 构建 system prompt（包含工具定义）
    2. 循环（最多 max_turns）：
       - 调用 LLM（带工具）
       - LLM 返回 content 或 tool_calls
       - 如果有 content，打印并检查是否结束
       - 如果有 tool_calls，执行并添加结果到消息
    3. 返回最终结果

    用法:
        agent = ReActAgent(
            client=llm_client,
            model="gpt-4o-mini",
            tools=MY_TOOLS,
            system_prompt=SYSTEM_PROMPT,
        )
        result = await agent.run("写第五章")
    """

    def __init__(
        self,
        client: Any,
        model: str,
        tools: list[ToolDefinition],
        system_prompt: str,
        max_turns: int = 20,
        max_tool_failures: int = 2,
        activity_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.client = client
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.max_tool_failures = max(1, int(max_tool_failures))
        self.activity_callback = activity_callback

    async def run(
        self,
        instruction: str,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
        on_message: Callable[[str], None] | None = None,
        context_messages: Sequence[Any] | None = None,
        context_message_factory: Callable[[], Sequence[Any]] | None = None,
    ) -> str:
        """运行 Agent

        Args:
            instruction: 用户指令
            on_tool_call: 工具调用回调 (name, args)
            on_tool_result: 工具结果回调 (name, result)
            on_message: LLM 消息回调 (content)

        Returns:
            最终回复内容
        """
        from ..llm import Message

        messages = [Message("system", self.system_prompt)]
        factory_messages = context_message_factory() if context_message_factory else None
        messages.extend(self._coerce_messages(factory_messages))
        messages.extend(self._coerce_messages(context_messages))
        messages.append(Message("user", instruction))

        final_content = ""
        saw_tool_calls = False
        failed_tool_counts: dict[str, int] = {}
        self._emit_activity("run_started", instruction_chars=len(instruction))

        for turn in range(self.max_turns):
            logger.debug(f"Turn {turn + 1}/{self.max_turns}")
            self._emit_activity(
                "model_started",
                turn=turn + 1,
                max_turns=self.max_turns,
            )

            response = self._chat_with_tools(messages)
            self._emit_activity(
                "model_completed",
                turn=turn + 1,
                tool_count=len(response.tool_calls or []),
                has_content=bool(response.content),
            )

            if response.tool_calls:
                saw_tool_calls = True
                assistant_msg = Message("assistant", response.content or "")
                # 为兼容 OpenAI/兼容接口，显式保留 assistant 的 tool_calls。
                setattr(assistant_msg, "tool_calls", response.tool_calls)
                messages.append(assistant_msg)

            if response.content:
                if not response.tool_calls:
                    final_content = response.content
                    on_message and on_message(response.content)
                    logger.debug("Agent finished (no more tool calls)")
                    self._emit_activity(
                        "response_ready",
                        turn=turn + 1,
                        content_chars=len(response.content),
                    )
                    break

            for tool_call in response.tool_calls:
                tc_id = tool_call.get("id", "")
                tc_name = tool_call.get("name", "")
                tc_args = (
                    json.loads(tool_call.get("arguments", "{}"))
                    if tool_call.get("arguments")
                    else {}
                )
                on_tool_call and on_tool_call(tc_name, tc_args)
                self._emit_activity(
                    "tool_started",
                    turn=turn + 1,
                    tool=tc_name,
                    tool_call_id=tc_id,
                )
                logger.debug(
                    "react.tool_call %s %s",
                    tc_name,
                    _debug_json(
                        {
                            "turn": turn + 1,
                            "tool_call_id": tc_id,
                            "arguments": tc_args,
                        }
                    ),
                )

                try:
                    result = await asyncio.to_thread(
                        self._execute_tool,
                        tc_name,
                        tc_args,
                    )
                    on_tool_result and on_tool_result(tc_name, result)
                    logger.debug(
                        "react.tool_result %s %s",
                        tc_name,
                        _debug_json(
                            {
                                "turn": turn + 1,
                                "tool_call_id": tc_id,
                                "result_chars": len(result),
                                "result_preview": result,
                            }
                        ),
                    )
                    failure_reason = self._tool_failure_reason(result)
                    self._emit_activity(
                        "tool_completed",
                        turn=turn + 1,
                        tool=tc_name,
                        tool_call_id=tc_id,
                        ok=not bool(failure_reason),
                        reason=failure_reason,
                    )
                    if failure_reason:
                        failed_tool_counts[tc_name] = (
                            failed_tool_counts.get(tc_name, 0) + 1
                        )
                        if failed_tool_counts[tc_name] >= self.max_tool_failures:
                            logger.warning(
                                "react.tool_failure_limit %s %s",
                                tc_name,
                                _debug_json(
                                    {
                                        "turn": turn + 1,
                                        "tool_call_id": tc_id,
                                        "failures": failed_tool_counts[tc_name],
                                        "reason": failure_reason,
                                    }
                                ),
                            )
                            self._emit_activity(
                                "run_failed",
                                turn=turn + 1,
                                tool=tc_name,
                                reason=failure_reason,
                                failure_count=failed_tool_counts[tc_name],
                            )
                            return (
                                f"工具 {tc_name} 连续失败 "
                                f"{failed_tool_counts[tc_name]} 次，"
                                "我已停止本轮以避免继续重复消耗。"
                                f"最后原因：{failure_reason}"
                            )
                    else:
                        failed_tool_counts.pop(tc_name, None)
                    messages.append(
                        Message(
                            role="tool",
                            content=result,
                            tool_call_id=tc_id,
                        )
                    )
                except Exception as e:
                    failure_reason = str(e)
                    error_result = json.dumps({"error": failure_reason})
                    on_tool_result and on_tool_result(tc_name, error_result)
                    logger.debug(
                        "react.tool_error %s %s",
                        tc_name,
                        _debug_json(
                            {
                                "turn": turn + 1,
                                "tool_call_id": tc_id,
                                "error": str(e),
                            }
                        ),
                    )
                    self._emit_activity(
                        "tool_completed",
                        turn=turn + 1,
                        tool=tc_name,
                        tool_call_id=tc_id,
                        ok=False,
                        reason=failure_reason,
                    )
                    failed_tool_counts[tc_name] = failed_tool_counts.get(tc_name, 0) + 1
                    if failed_tool_counts[tc_name] >= self.max_tool_failures:
                        logger.warning(
                            "react.tool_failure_limit %s %s",
                            tc_name,
                            _debug_json(
                                {
                                    "turn": turn + 1,
                                    "tool_call_id": tc_id,
                                    "failures": failed_tool_counts[tc_name],
                                    "reason": failure_reason,
                                }
                            ),
                        )
                        self._emit_activity(
                            "run_failed",
                            turn=turn + 1,
                            tool=tc_name,
                            reason=failure_reason,
                            failure_count=failed_tool_counts[tc_name],
                        )
                        return (
                            f"工具 {tc_name} 连续失败 "
                            f"{failed_tool_counts[tc_name]} 次，"
                            "我已停止本轮以避免继续重复消耗。"
                            f"最后原因：{failure_reason}"
                        )
                    messages.append(
                        Message(
                            role="tool",
                            content=error_result,
                            tool_call_id=tc_id,
                        )
                    )
        else:
            logger.warning(f"Reached max turns ({self.max_turns})")
            self._emit_activity(
                "run_failed",
                reason="max_turns_reached",
                turn=self.max_turns,
            )

        if final_content:
            self._emit_activity("run_completed", content_chars=len(final_content))
            return final_content
        if saw_tool_calls:
            self._emit_activity("run_completed", partial=True)
            return (
                "我已经完成了部分工具操作，但本轮还没有整理出最终结果。"
                "请发送“继续”让我基于刚才的工具结果收尾，或把本轮修改范围缩小。"
            )
        self._emit_activity("run_completed", content_chars=0)
        return ""

    def _emit_activity(self, event: str, **payload: Any) -> None:
        callback = self.activity_callback
        if callback is None:
            return
        try:
            callback({"event": event, **payload})
        except Exception:
            logger.debug("react.activity_callback_failed", exc_info=True)

    @staticmethod
    def _tool_failure_reason(result: str) -> str:
        try:
            payload = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        from .confirmation import is_confirmation_policy_block

        # Soft confirmation gates must not abort the ReAct turn.
        if is_confirmation_policy_block(payload):
            return ""
        is_failure = payload.get("ok") is False or bool(payload.get("error"))
        if not is_failure:
            return ""
        reason = (
            payload.get("message")
            or payload.get("reason")
            or payload.get("error")
            or payload.get("code")
            or "工具返回失败"
        )
        return str(reason)

    def _coerce_messages(self, messages: Sequence[Any] | None) -> list[Any]:
        from ..llm import Message

        if not messages:
            return []

        normalized: list[Any] = []
        for message in messages:
            if isinstance(message, Message):
                normalized.append(message)
            elif isinstance(message, dict):
                normalized.append(
                    Message(
                        role=message.get("role", "assistant"),
                        content=str(message.get("content", "")),
                        tool_call_id=str(message.get("tool_call_id", "")),
                    )
                )
            else:
                normalized.append(Message("assistant", str(message)))
        return normalized

    def _chat_with_tools(self, messages: list) -> Any:
        """调用 LLM（带工具）"""

        llm_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.tools
        ]

        return self.client.chat_with_tools(messages, llm_tools)

    def _execute_tool(self, name: str, args: dict) -> str:
        """执行工具"""
        # 查找工具
        tool = next((t for t in self.tools if t.name == name), None)
        if not tool:
            return json.dumps({"error": f"Unknown tool: {name}"})

        # 验证参数
        for req in tool.required:
            if req not in args:
                return json.dumps({"error": f"Missing required argument: {req}"})

        # 调用注册的执行器
        if hasattr(self, f"_tool_{name}"):
            result = getattr(self, f"_tool_{name}")(args)
            return json.dumps(result) if isinstance(result, dict) else str(result)

        return json.dumps({"error": f"Tool '{name}' not implemented"})

    def _register_tool_executors(self, executors: dict):
        """注册工具执行器

        用法:
            agent._register_tool_executors({
                'write_draft': lambda args: pipeline.write_draft(...),
                'audit_chapter': lambda args: pipeline.audit_chapter(...),
            })
        """
        for name, fn in executors.items():
            setattr(self, f"_tool_{name}", fn)


class SimpleResponse:
    """简单响应（用于不支持工具调用时）"""

    def __init__(self, content: str, tool_calls: list):
        self.content = content
        self.tool_calls = tool_calls


# === OpenWrite 内置工具 ===

OPENWRITE_TOOLS = [
    ToolDefinition(
        name="write_chapter",
        description="写一章草稿。根据当前大纲和上下文生成章节正文。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节 ID（如 ch_005）"},
                "guidance": {"type": "string", "description": "创作指导（可选，自然语言）"},
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="review_chapter",
        description="审查章节。检查逻辑、风格、AI痕迹等问题。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节 ID"},
                "strict": {"type": "boolean", "description": "严格模式"},
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="get_status",
        description="获取项目状态概览。",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDefinition(
        name="get_context",
        description=(
            "获取指定章节的写作上下文及发送前压缩报告。"
            "压缩报告包含消息预算、L1-L4 触发级别及 packet 文档裁剪记录。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节 ID"},
                "window_size": {"type": "integer", "description": "大纲窗口大小"},
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="search_project",
        description="搜索小说项目中的大纲、人物、世界设定、故事资产和正文。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "scope": {
                    "type": "string",
                    "description": "范围：all/outline/story/characters/world/chapters",
                },
                "limit": {"type": "integer", "description": "返回数量，默认 20"},
            },
            "required": ["query"],
        },
        required=["query"],
    ),
    ToolDefinition(
        name="read_project_document",
        description=(
            "读取小说项目内的源资产或正文文档，返回 revision 与内容。"
            "支持 src、data/manuscript、data/foreshadowing 下的文件。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对小说根目录路径，如 src/characters/hero.md",
                },
                "max_chars": {"type": "integer", "description": "最大返回字符数"},
            },
            "required": ["path"],
        },
        required=["path"],
    ),
    ToolDefinition(
        name="edit_project_document",
        description=(
            "用精确 old_text/new_text 补丁安全修改小说文档。默认只预览 diff；"
            "用户明确确认后传入 revision/base_revision 并设置 confirm=true 才写入。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "read_project_document 返回的 path"},
                "revision": {
                    "type": "string",
                    "description": "read_project_document 或预览返回的 revision",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_text": {"type": "string", "description": "原文精确片段"},
                            "new_text": {"type": "string", "description": "替换后的内容"},
                            "replace_all": {
                                "type": "boolean",
                                "description": "是否替换所有匹配；默认 false",
                            },
                        },
                        "required": ["old_text", "new_text"],
                    },
                },
                "confirm": {"type": "boolean", "description": "默认 false 只预览 diff"},
            },
            "required": ["path", "edits"],
        },
        required=["path", "edits"],
    ),
    ToolDefinition(
        name="list_chapters",
        description="列出所有章节。",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDefinition(
        name="create_outline",
        description="创建或更新大纲。",
        parameters={
            "type": "object",
            "properties": {
                "outline_content": {"type": "string", "description": "大纲内容（Markdown）"},
            },
            "required": ["outline_content"],
        },
    ),
    ToolDefinition(
        name="get_outline_structure",
        description="读取与 Studio 同源的卷/幕/节/章树，并推荐下一章；只读，不修改大纲或正文。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "可选的指定章纲 ID；留空时推荐最早尚未生成正文的章节",
                },
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="edit_outline_structure",
        description=(
            "按 revision 增量编辑 Studio 同源的卷/幕/节/章树；支持重命名、"
            "修改节点内容、新增同级/下级和删除。修改内容只替换当前标题下、"
            "子标题前的正文块。删除会让后续卷/幕/节/章连续补位并在 diff 中"
            "完整预览。默认只返回 "
            "diff，用户确认后设置 confirm=true 才写入；若补位会改变已有正文的章节号或 "
            "revision 陈旧则拒绝。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["rename", "update_summary", "add_child", "add_after", "delete"],
                    "description": (
                        "结构编辑动作；update_summary 修改节点内容，delete 会删除子树"
                        "并连续重编号后续同类节点"
                    ),
                },
                "revision": {
                    "type": "string",
                    "description": "get_outline_structure 返回的 revision",
                },
                "node_id": {"type": "string", "description": "目标节点 ID；根新增卷时可留空"},
                "kind": {
                    "type": "string",
                    "enum": ["volume", "act", "section", "chapter"],
                    "description": "新增节点层级",
                },
                "title": {"type": "string", "description": "新标题；删除时留空"},
                "summary": {
                    "type": "string",
                    "description": "update_summary 使用的新节点内容；不能包含 Markdown 标题",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "默认 false 只预览 diff；用户明确确认后才设为 true",
                },
            },
        },
        required=["operation", "revision"],
    ),
    ToolDefinition(
        name="create_character",
        description="创建角色。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "角色名"},
                "description": {"type": "string", "description": "角色描述"},
            },
            "required": ["name"],
        },
    ),
    ToolDefinition(
        name="get_truth_files",
        description="读取真相文件（世界状态、伏笔、摘要等）。",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDefinition(
        name="update_truth_file",
        description="更新真相文件。",
        parameters={
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "文件名（current_state/ledger/relationships）",
                },
                "content": {"type": "string", "description": "新内容"},
            },
            "required": ["file_name", "content"],
        },
    ),
    # 伏笔管理
    ToolDefinition(
        name="create_foreshadowing",
        description="创建伏笔节点。",
        parameters={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "伏笔ID（如 f001）"},
                "content": {"type": "string", "description": "伏笔内容描述"},
                "weight": {"type": "integer", "description": "权重 1-10，默认5"},
                "layer": {"type": "string", "description": "层级（主线/支线/彩蛋）"},
                "created_at": {"type": "string", "description": "埋设章节（如 ch_001）"},
                "target_chapter": {"type": "string", "description": "预期回收章节（如 ch_015）"},
            },
            "required": ["node_id", "content"],
        },
    ),
    ToolDefinition(
        name="list_foreshadowing",
        description="列出伏笔节点。可按状态/权重/层级过滤。",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "状态过滤（埋伏/待收/已收/废弃）"},
                "min_weight": {"type": "integer", "description": "最小权重过滤"},
                "layer": {"type": "string", "description": "层级过滤（主线/支线）"},
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="update_foreshadowing",
        description="更新伏笔状态。",
        parameters={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "伏笔ID"},
                "status": {"type": "string", "description": "新状态（埋伏/待收/已收/废弃）"},
            },
            "required": ["node_id", "status"],
        },
    ),
    ToolDefinition(
        name="validate_foreshadowing",
        description="验证伏笔DAG，检查环和引用错误。",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    # 状态验证
    ToolDefinition(
        name="validate_truth",
        description="验证真相文件与章节内容的一致性。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "要验证的章节ID（默认最新章节）"},
            },
            "required": [],
        },
    ),
    # 世界查询
    ToolDefinition(
        name="query_world",
        description="查询世界观实体。可列出所有实体或获取单个实体详情。",
        parameters={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体ID（不填则列出所有）"},
                "type": {
                    "type": "string",
                    "description": "类型过滤（location/person/technique/item等）",
                },
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="get_world_relations",
        description=(
            "获取与 Studio 同源的关系图谱；包含 front matter、关联段落和人物关系段落。"
            "需要按名称、ID 或关系文字定位时，先读取完整图谱再在结果中搜索。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDefinition(
        name="search_relation_targets",
        description=(
            "搜索可连入关系图的人物、地点、组织、能力、物品或概念候选。"
            "适合把人物与出身地点、能力体系、阵营、物品等设定联系起来。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，如人物名、地点、能力"},
                "type": {"type": "string", "description": "可选类型过滤，如 人物/地点/能力"},
                "limit": {"type": "integer", "description": "返回数量，默认 20"},
            },
            "required": ["query"],
        },
        required=["query"],
    ),
    ToolDefinition(
        name="edit_world_relation",
        description=(
            "增量维护一个正式关系。默认仅预览 diff；只有用户确认后才能携带 "
            "base_revision 和 confirm=true 写入。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "关系源实体 ID 或名称"},
                "target_id": {"type": "string", "description": "关系目标实体 ID 或名称"},
                "description": {"type": "string", "description": "关系说明"},
                "action": {
                    "type": "string",
                    "enum": ["upsert", "remove"],
                    "description": "upsert（新增/更新）或 remove（删除）",
                },
                "base_revision": {
                    "type": "string",
                    "description": "预览返回的源文件 revision；确认写入时必填",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "默认 false 仅预览；用户明确确认后才设为 true",
                },
            },
            "required": ["source_id", "target_id"],
        },
        required=["source_id", "target_id"],
    ),
    ToolDefinition(
        name="edit_world_relations",
        description=(
            "批量新增、更新或删除正式关系。默认只预览所有源文件 diff；"
            "用户确认后传入 source_revisions/base_revisions 并设置 confirm=true 才写入。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "relations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_id": {"type": "string", "description": "源实体 ID 或名称"},
                            "target_id": {"type": "string", "description": "目标实体 ID 或名称"},
                            "description": {"type": "string", "description": "关系说明"},
                            "action": {
                                "type": "string",
                                "enum": ["upsert", "remove"],
                                "description": "默认 upsert；删除用 remove",
                            },
                            "base_revision": {
                                "type": "string",
                                "description": "可选；预览返回的源文件 revision",
                            },
                        },
                        "required": ["source_id", "target_id"],
                    },
                },
                "base_revisions": {
                    "type": "object",
                    "description": "预览返回的 source_revisions；确认写入时传回",
                },
                "confirm": {"type": "boolean", "description": "默认 false 只预览 diff"},
            },
            "required": ["relations"],
        },
        required=["relations"],
    ),
    # 对话质量
    ToolDefinition(
        name="extract_dialogue_fingerprint",
        description="提取角色对话风格指纹，分析口头禅、用词习惯等。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID（默认最新）"},
                "character_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要分析的角色名列表",
                },
            },
            "required": [],
        },
    ),
    # 后置验证
    ToolDefinition(
        name="validate_post_write",
        description="零成本规则检测，检查禁止句式、AI味、敏感词等。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID（默认最新）"},
            },
            "required": [],
        },
    ),
    # 工作流
    ToolDefinition(
        name="get_workflow_status",
        description="获取工作流状态，查看写作流程进度。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID（不填则列出所有）"},
            },
            "required": [],
        },
    ),
    ToolDefinition(
        name="start_workflow",
        description="为指定章节启动写作工作流。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID"},
            },
            "required": ["chapter_id"],
        },
    ),
    ToolDefinition(
        name="advance_workflow",
        description="推进工作流到下一阶段。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID"},
                "stage_name": {"type": "string", "description": "目标阶段（可选）"},
            },
            "required": ["chapter_id"],
        },
    ),
    # 文本处理
    ToolDefinition(
        name="chunk_text",
        description="将大文本文件按章节边界切割为chunk。",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "chunk_size": {"type": "integer", "description": "chunk大小（默认30000）"},
            },
            "required": ["file_path"],
        },
    ),
    ToolDefinition(
        name="compress_section",
        description="压缩节/篇的章节摘要。",
        parameters={
            "type": "object",
            "properties": {
                "arc_id": {"type": "string", "description": "篇ID"},
                "section_id": {"type": "string", "description": "节ID（不填则压缩整篇）"},
            },
            "required": [],
        },
    ),
]


OPENWRITE_SYSTEM_PROMPT = """你是 OpenWrite 小说创作引擎的 Agent。

你的职责是帮用户完成小说创作任务，包括：
- 写章节、审查章节
- 管理大纲、角色、世界观
- 跟踪伏笔和真相文件
- 回答创作相关问题

## 可用工具

| 工具 | 作用 |
|------|------|
| write_chapter | 写一章草稿 |
| review_chapter | 审查章节 |
| get_status | 查看项目状态 |
| get_context | 获取写作上下文 |
| search_project | 搜索项目资产 |
| read_project_document | 读取小说资产文档 |
| edit_project_document | 预览或确认修改小说资产文档 |
| list_chapters | 列出章节 |
| create_outline | 创建/更新大纲 |
| get_outline_structure | 读取卷/幕/节/章树并推荐下一章 |
| edit_outline_structure | 按 revision 增量改名、增删卷幕节章 |
| create_character | 创建角色 |
| get_truth_files | 读取真相文件 |
| update_truth_file | 更新真相文件 |
| create_foreshadowing | 创建伏笔 |
| list_foreshadowing | 列出伏笔 |
| update_foreshadowing | 更新伏笔状态 |
| validate_foreshadowing | 验证伏笔DAG |
| query_world | 查询世界观实体 |
| get_world_relations | 获取关系图谱 |
| search_relation_targets | 搜索关系图候选节点 |
| edit_world_relation | 预览或确认一个增量关系修改 |
| edit_world_relations | 批量预览或确认关系修改 |
| validate_truth | 验证真相文件一致性 |
| extract_dialogue_fingerprint | 提取对话风格指纹 |
| validate_post_write | 后置规则验证 |
| get_workflow_status | 查看工作流进度 |
| start_workflow | 启动工作流 |
| advance_workflow | 推进工作流 |
| chunk_text | 切割大文本 |
| compress_section | 压缩摘要 |

## 工作流程

1. 用户给出指令后，先了解当前状态
2. 根据需要调用工具
3. 向用户汇报进展

模型选择和凭据由用户通过 Studio“模型设置”或启动环境配置；OpenAI / Anthropic
在该页面表示接口格式，Base URL 与模型名由用户填写。Agent 不得索取、读取、
回显或保存 API Key；ReAct 工具层不提供模型凭据操作。
面向用户的回复可使用 CommonMark 标题、列表、引用、链接和代码块；Studio 会安全渲染。
4. 直到任务完成

## 规则

- 每完成一步，简要汇报
- 如果缺少必要信息，先询问用户
- 遵循项目的大纲和设定
- 修改已有大纲树时先读取 revision，并以 edit_outline_structure(confirm=false)
  预览 diff；只有用户明确确认后才以相同 revision 和 confirm=true 写入
- 修改人物、故事、世界实体、正文等文档时先 read_project_document，再
  edit_project_document(confirm=false) 预览 diff；只有用户明确确认后才写入
- 连接人物、出身地点、能力设定、组织或物品时先 search_relation_targets /
  get_world_relations 定位候选，再 edit_world_relations(confirm=false) 预览 diff；
  只有用户明确确认后才 confirm=true 写入
"""
