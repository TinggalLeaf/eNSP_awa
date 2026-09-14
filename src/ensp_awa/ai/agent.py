"""AI Agent 循环：驱动「思考 → 工具调用 → 观察」直至产出最终回答。

事件通过 emitter 回调推给前端：
- {"type": "text", "delta": str}            模型增量输出
- {"type": "reasoning", "delta": str}       思考链增量（若模型支持）
- {"type": "tool_start", "name", "args"}    工具开始执行
- {"type": "tool_end", "name", "ok", "summary"} 工具结束
- {"type": "assistant", "content", "reasoning", "tool_calls"} 本轮模型完整消息
- {"type": "done", "usage"}                 本轮结束
- {"type": "error", "message"}              错误（含用户取消）
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from ..config import AppConfig
from .client import AIError, Message, OpenAICompatClient
from .tools import build_tool_schemas, execute_tool

MAX_ITERATIONS = 10


class AgentCancelled(Exception):
    pass


class Agent:
    def __init__(
        self,
        config: AppConfig,
        emitter: Callable[[dict[str, Any]], None],
        save_dialog: Callable[[str, str], str] | None = None,
    ) -> None:
        self.config = config
        self.emit = emitter
        self.save_dialog = save_dialog

    # ------------------------------------------------------------------ #
    def run_turn(
        self,
        history: list[Message],
        user_text: str,
        cancel: threading.Event,
    ) -> list[Message]:
        """执行一轮对话（含可能的多次工具调用），返回应追加进历史的消息。"""
        added: list[Message] = [Message(role="user", content=user_text)]
        messages = history + added
        profile = self.config.active_profile()
        client = OpenAICompatClient.from_profile(profile)
        tools = build_tool_schemas()
        system_prompt = self.config.get("system_prompt", "")

        for _ in range(MAX_ITERATIONS):
            if cancel.is_set():
                raise AgentCancelled()

            content = ""
            reasoning = ""
            tool_calls = []
            usage: dict[str, Any] = {}

            try:
                for ev in client.stream(messages, tools, system_prompt):
                    if cancel.is_set():
                        raise AgentCancelled()
                    t = ev.get("type")
                    if t in ("text", "reasoning"):
                        self.emit(ev)
                        if t == "text":
                            content += ev.get("delta", "")
                        else:
                            reasoning += ev.get("delta", "")
                    elif t == "assistant":
                        content = ev.get("content", content)
                        reasoning = ev.get("reasoning", reasoning)
                        tool_calls = ev.get("tool_calls", [])
                    elif t == "done":
                        usage = ev.get("usage", {})
                    elif t == "error":
                        raise AIError(ev.get("message", "未知错误"))
            except AgentCancelled:
                raise
            except AIError as e:
                self.emit({"type": "error", "message": str(e)})
                return added

            assistant_msg = Message(
                role="assistant", content=content, reasoning=reasoning, tool_calls=tool_calls
            )
            added.append(assistant_msg)
            messages.append(assistant_msg)

            if not tool_calls:
                self.emit({"type": "done", "usage": usage})
                return added

            for tc in tool_calls:
                if cancel.is_set():
                    raise AgentCancelled()
                args = tc.parsed_arguments()
                self.emit({"type": "tool_start", "name": tc.name, "args": args})
                result = execute_tool(
                    tc.name,
                    args,
                    default_topo_dir=self.config.get("topo.output_dir", ""),
                    default_report_dir=self.config.get("report.output_dir", ""),
                    save_dialog=self.save_dialog,
                )
                ok = bool(result.get("ok"))
                summary = _tool_summary(tc.name, result)
                self.emit({"type": "tool_end", "name": tc.name, "ok": ok, "summary": summary})
                tool_msg = Message(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=json.dumps(result, ensure_ascii=False),
                )
                added.append(tool_msg)
                messages.append(tool_msg)

        self.emit({"type": "error", "message": "已达到最大工具调用轮次，停止本轮对话"})
        return added


def _tool_summary(name: str, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return str(result.get("error", "执行失败"))
    if name == "generate_topology":
        checks = result.get("checks", [])
        mark = "通过" if all(c.get("ok") for c in checks) else "未通过"
        return f"{result.get('path', '')}（自检{mark}，{result.get('size', 0)} 字节）"
    if name == "send_command":
        return f"{result.get('device', '')} ← {result.get('command', '')}"
    if name == "scan_ensp_devices":
        return f"发现 {len(result.get('ports', []))} 个可达端口"
    if name == "list_sessions":
        return f"{len(result.get('sessions', []))} 个会话"
    if name == "connect_device":
        return f"{result.get('session', {}).get('name', '')} 已连接"
    if name == "generate_experiment_report":
        return f"Markdown: {result.get('markdown', '')} PDF: {result.get('pdf', '')}"
    if name == "parse_topology":
        return f"{len(result.get('devices', []))} 台设备 / {len(result.get('links', []))} 条连线"
    return "完成"
