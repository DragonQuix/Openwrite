"""Provider-neutral response classification and structured-output parsing."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml


class ProviderResponseError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    patterns = (
        r"\bsk-[A-Za-z0-9_-]{8,}\b",
        r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?[^\s,;\"']+",
        r"(?i)(api[_-]?key|authorization|bearer)(\s*[:=]\s*|\s+)[^\s,;]+",
        r"(?i)([?&](?:api[_-]?key|access_token|key)=)[^&\s]+",
    )
    for pattern in patterns:
        text = re.sub(pattern, "[redacted]", text)
    return text


def classify_response(
    content: Any,
    *,
    finish_reason: str = "",
    reasoning: Any = "",
    require_content: bool = True,
) -> str:
    text = str(content or "").strip()
    finish = str(finish_reason or "").lower()
    if finish in {"length", "max_tokens", "incomplete"}:
        raise ProviderResponseError("MODEL_OUTPUT_TRUNCATED", "模型输出因长度限制被截断")
    if not text and str(reasoning or "").strip():
        raise ProviderResponseError("MODEL_REASONING_ONLY", "模型只返回了推理内容，没有最终答案")
    if require_content and not text:
        raise ProviderResponseError("MODEL_EMPTY_RESPONSE", "模型返回了空内容")
    return "content" if text else "empty_allowed"


def validate_tool_arguments(tool_calls: list[dict[str, Any]]) -> None:
    for index, call in enumerate(tool_calls):
        arguments = call.get("arguments", {})
        if isinstance(arguments, dict):
            continue
        try:
            parsed = json.loads(str(arguments or "{}"))
        except json.JSONDecodeError as exc:
            raise ProviderResponseError(
                "MALFORMED_TOOL_ARGUMENTS",
                f"第 {index + 1} 个工具调用参数不是有效 JSON",
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderResponseError(
                "MALFORMED_TOOL_ARGUMENTS",
                f"第 {index + 1} 个工具调用参数必须是对象",
            )


def load_structured_mapping(content: Any, *, required_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        raise ProviderResponseError("MODEL_EMPTY_RESPONSE", "模型返回了空内容")
    candidates = [
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:yaml|yml|json)?\s*\r?\n(.*?)\r?\n```",
            text,
            re.DOTALL | re.IGNORECASE,
        )
    ]
    candidates.append(text)
    if required_keys:
        keys = "|".join(re.escape(key) for key in required_keys)
        block = re.search(rf"(?ms)^(?:{keys})\s*:\s*\n?.*$", text)
        if block:
            candidates.append(block.group(0).strip())
    for candidate in candidates:
        try:
            payload = yaml.safe_load(candidate) or {}
        except yaml.YAMLError:
            continue
        if isinstance(payload, dict) and (
            not required_keys or any(key in payload for key in required_keys)
        ):
            return payload
    raise ProviderResponseError("MALFORMED_STRUCTURED_OUTPUT", "模型返回的结构化内容无法解析")
