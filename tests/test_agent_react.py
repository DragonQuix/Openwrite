from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tools.agent.react import ReActAgent, ToolDefinition
from tools.llm import Message


class RecordingClient:
    def __init__(self, responses: list[object]):
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def chat_with_tools(self, messages, tools, **kwargs):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools),
                "kwargs": dict(kwargs),
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return SimpleNamespace(content="", tool_calls=[])


def _tool_response(content: str = "", tool_calls: list[dict] | None = None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def test_react_agent_direct_chat_uses_injected_context_messages():
    client = RecordingClient([_tool_response("继续")])
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[],
        system_prompt="系统提示",
    )

    result = asyncio.run(
        agent.run(
            "继续写",
            context_messages=[
                Message("assistant", "会话摘要: 已确认都市职场异能。"),
                Message("assistant", "最近轮次: user->我想写一个普通上班族觉醒术式的故事"),
            ],
        )
    )

    assert result == "继续"
    assert [message.role for message in client.calls[0]["messages"]] == [
        "system",
        "assistant",
        "assistant",
        "user",
    ]
    assert "会话摘要" in client.calls[0]["messages"][1].content
    assert "最近轮次" in client.calls[0]["messages"][2].content


def test_react_agent_supports_context_message_factory_callback():
    client = RecordingClient([_tool_response("继续")])
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[],
        system_prompt="系统提示",
    )
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        return [Message("assistant", "会话摘要: 由工厂注入。")]

    result = asyncio.run(
        agent.run(
            "继续写",
            context_message_factory=factory,
        )
    )

    assert result == "继续"
    assert calls["count"] == 1
    assert client.calls[0]["messages"][1].content == "会话摘要: 由工厂注入。"


def test_react_agent_direct_tool_call_uses_injected_context_messages():
    client = RecordingClient(
        [
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "get_status",
                        "arguments": "{}",
                    }
                ]
            ),
            _tool_response("状态正常"),
        ]
    )
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="get_status",
                description="获取状态",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
    )
    captured: list[dict[str, object]] = []
    agent._register_tool_executors(
        {
            "get_status": lambda args: captured.append(args)
            or {"ok": True, "stage": "rolling_outline"}
        }
    )

    result = asyncio.run(
        agent.run(
            "查看状态",
            context_messages=[Message("assistant", "会话摘要: 已确认章节范围。")],
        )
    )

    assert result == "状态正常"
    assert captured == [{}]
    assert len(client.calls) == 2
    assert client.calls[0]["messages"][1].content.startswith("会话摘要")
    assert any(message.role == "tool" for message in client.calls[1]["messages"])


def test_react_agent_action_tool_call_uses_injected_context_messages():
    client = RecordingClient(
        [
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "summarize_ideation",
                        "arguments": "{}",
                    }
                ]
            ),
            _tool_response("已汇总"),
        ]
    )
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="summarize_ideation",
                description="汇总想法",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
    )
    captured: list[dict[str, object]] = []
    agent._register_tool_executors(
        {
            "summarize_ideation": lambda args: captured.append(args) or {
                "ok": True,
                "action": "summarize_ideation",
            }
        }
    )

    result = asyncio.run(
        agent.run(
            "先帮我汇总一下当前想法",
            context_messages=[Message("assistant", "最近会话摘要: 设定已稳定。")],
        )
    )

    assert result == "已汇总"
    assert captured == [{}]
    assert len(client.calls) == 2
    assert client.calls[0]["messages"][1].content.startswith("最近会话摘要")
    assert any(message.role == "tool" for message in client.calls[1]["messages"])


def test_react_agent_does_not_return_intermediate_tool_call_content():
    client = RecordingClient(
        [
            _tool_response(
                "清理残留太碎。我丢弃重来，用更大的 old_text 块一次性完成。",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "stage_outline_edits",
                        "arguments": "{}",
                    }
                ],
            )
        ]
    )
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="stage_outline_edits",
                description="暂存大纲修改",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
        max_turns=1,
    )
    agent._register_tool_executors(
        {
            "stage_outline_edits": lambda args: {
                "ok": True,
                "message": "大纲增量修改已暂存。",
            }
        }
    )

    result = asyncio.run(agent.run("修改大纲"))

    assert "old_text" not in result
    assert "清理残留" not in result
    assert "还没有整理出最终结果" in result


def test_react_agent_runs_sync_tools_outside_the_active_event_loop():
    client = RecordingClient(
        [
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "write_like_tool",
                        "arguments": "{}",
                    }
                ]
            ),
            _tool_response("写作完成"),
        ]
    )
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="write_like_tool",
                description="模拟内部使用 asyncio.run 的同步写作入口",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
    )

    def sync_tool(args):
        async def nested_write():
            return "nested-ok"

        return {"ok": True, "value": asyncio.run(nested_write())}

    agent._register_tool_executors({"write_like_tool": sync_tool})

    result = asyncio.run(agent.run("写一章"))

    assert result == "写作完成"
    tool_messages = [
        message.content
        for message in client.calls[1]["messages"]
        if message.role == "tool"
    ]
    assert tool_messages == ['{"ok": true, "value": "nested-ok"}']


def test_react_agent_emits_real_model_tool_and_completion_activity():
    client = RecordingClient(
        [
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "get_status",
                        "arguments": "{}",
                    }
                ]
            ),
            _tool_response("状态正常"),
        ]
    )
    events: list[dict] = []
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="get_status",
                description="获取状态",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
        activity_callback=events.append,
    )
    agent._register_tool_executors(
        {"get_status": lambda args: {"ok": True, "stage": "chapter_preflight"}}
    )

    result = asyncio.run(agent.run("查看状态"))

    assert result == "状态正常"
    assert [event["event"] for event in events] == [
        "run_started",
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
        "model_started",
        "model_completed",
        "response_ready",
        "run_completed",
    ]
    assert events[3]["tool"] == "get_status"
    assert events[4]["ok"] is True


def test_react_agent_stops_after_repeated_tool_failures():
    client = RecordingClient(
        [
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "delegate_chapter_write",
                        "arguments": '{"chapter_id": "ch_007"}',
                    }
                ]
            ),
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_2",
                        "name": "delegate_chapter_write",
                        "arguments": '{"chapter_id": "ch_007"}',
                    }
                ]
            ),
            _tool_response("不应该继续"),
        ]
    )
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="delegate_chapter_write",
                description="写章",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
    )
    calls = {"count": 0}

    def failing_tool(args):
        calls["count"] += 1
        return {"ok": False, "reason": "writer backend failed"}

    agent._register_tool_executors({"delegate_chapter_write": failing_tool})

    result = asyncio.run(agent.run("写下一章"))

    assert calls["count"] == 2
    assert len(client.calls) == 2
    assert "delegate_chapter_write 连续失败 2 次" in result
    assert "writer backend failed" in result


def test_react_agent_stops_after_repeated_tool_exceptions():
    client = RecordingClient(
        [
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "unstable_tool",
                        "arguments": "{}",
                    }
                ]
            ),
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_2",
                        "name": "unstable_tool",
                        "arguments": "{}",
                    }
                ]
            ),
            _tool_response("不应该继续"),
        ]
    )
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="unstable_tool",
                description="不稳定工具",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
    )

    def failing_tool(args):
        raise RuntimeError("boom")

    agent._register_tool_executors({"unstable_tool": failing_tool})

    result = asyncio.run(agent.run("运行工具"))

    assert len(client.calls) == 2
    assert "unstable_tool 连续失败 2 次" in result
    assert "boom" in result


def test_react_agent_does_not_abort_on_confirmation_policy_blocks():
    client = RecordingClient(
        [
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "edit_project_document",
                        "arguments": '{"confirm": true}',
                    }
                ]
            ),
            _tool_response(
                tool_calls=[
                    {
                        "id": "call_2",
                        "name": "edit_project_document",
                        "arguments": '{"confirm": true}',
                    }
                ]
            ),
            _tool_response("请明确回复“确认应用”后再写入。"),
        ]
    )
    agent = ReActAgent(
        client=client,
        model="demo",
        tools=[
            ToolDefinition(
                name="edit_project_document",
                description="编辑文档",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="系统提示",
        max_tool_failures=2,
    )

    def blocked_tool(args):
        return {
            "ok": False,
            "blocked": True,
            "error": "explicit_user_confirmation_required",
            "message": "尚未收到用户对本次 diff 的明确应用指令，项目文件未修改。",
            "next_action": "request_mutation_confirmation",
        }

    agent._register_tool_executors({"edit_project_document": blocked_tool})
    result = asyncio.run(agent.run("应用修改"))

    assert "连续失败" not in result
    assert "确认应用" in result
    assert len(client.calls) == 3
