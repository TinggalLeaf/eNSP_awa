"""OpenAI 两种兼容格式接口客户端。

支持:
- ``chat_completions``: 经典 ``/chat/completions``（function calling + SSE 流式）
- ``responses``: 新版 ``/responses``（function tools + SSE 流式）

对上层暴露统一的消息结构与流式事件流，自动处理:
- SSE 解析与增量合并（content / reasoning / tool_call arguments）
- 流式失败自动降级为非流式重试
- 401 / 404 / 超时 / 连接错误的中文友好提示
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

FORMAT_CHAT = "chat_completions"
FORMAT_RESPONSES = "responses"

DEFAULT_TIMEOUT = 120.0


class AIError(Exception):
    """带用户可读消息的 AI 接口错误。"""


# ---------------------------------------------------------------------- #
# 统一消息结构（中性格式，按接口各自转换）
# ---------------------------------------------------------------------- #
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string

    def parsed_arguments(self) -> dict[str, Any]:
        try:
            v = json.loads(self.arguments or "{}")
            return v if isinstance(v, dict) else {"_value": v}
        except json.JSONDecodeError:
            return {}


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""  # tool 名（responses 格式需要）
    reasoning: str = ""


# ---------------------------------------------------------------------- #
# 流式事件
# ---------------------------------------------------------------------- #
def ev_text(delta: str, reasoning: bool = False) -> dict:
    return {"type": "reasoning" if reasoning else "text", "delta": delta}


def ev_done(usage: dict | None, model: str) -> dict:
    return {"type": "done", "usage": usage or {}, "model": model}


def ev_error(message: str) -> dict:
    return {"type": "error", "message": message}


# ---------------------------------------------------------------------- #
# 客户端
# ---------------------------------------------------------------------- #
class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        fmt: str = FORMAT_CHAT,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.fmt = fmt if fmt in (FORMAT_CHAT, FORMAT_RESPONSES) else FORMAT_CHAT
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.last_model: str = model

    # ------------------------------------------------------------------ #
    @classmethod
    def from_profile(cls, p: dict[str, Any]) -> "OpenAICompatClient":
        return cls(
            base_url=p.get("base_url", ""),
            api_key=p.get("api_key", ""),
            model=p.get("model", ""),
            fmt=p.get("format", FORMAT_CHAT),
            temperature=float(p.get("temperature", 0.3)),
            max_tokens=int(p.get("max_tokens", 4096)),
        )

    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise AIError("未配置接口地址（Base URL）")
        return f"{self.base_url}{path}"

    @staticmethod
    def _friendly_error(e: httpx.HTTPError | httpx.HTTPStatusError) -> AIError:
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            if code == 401:
                return AIError("接口鉴权失败（401）：请检查 API Key 是否正确")
            if code == 403:
                return AIError("接口拒绝访问（403）：请检查 Key 权限或配额")
            if code == 404:
                return AIError("接口不存在（404）：请检查 Base URL 与模型名")
            if code == 429:
                return AIError("请求过于频繁或配额不足（429），请稍后重试")
            if code == 400:
                body = e.response.text[:300]
                return AIError(f"接口参数错误（400）：{body}")
            return AIError(f"接口返回错误（HTTP {code}）：{e.response.text[:300]}")
        if isinstance(e, httpx.ConnectError):
            return AIError(f"无法连接到接口服务器：{e}")
        if isinstance(e, httpx.TimeoutException):
            return AIError("接口请求超时，请检查网络或稍后重试")
        return AIError(f"网络错误：{e}")

    # ------------------------------------------------------------------ #
    # 对外主入口：流式对话（自动降级非流式）
    # ------------------------------------------------------------------ #
    def stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system_prompt: str = "",
    ) -> Iterator[dict]:
        try:
            if self.fmt == FORMAT_RESPONSES:
                yield from self._stream_responses(messages, tools, system_prompt)
            else:
                yield from self._stream_chat(messages, tools, system_prompt)
        except AIError:
            raise
        except (httpx.HTTPError, httpx.HTTPStatusError) as e:
            raise self._friendly_error(e) from e
        except Exception as e:  # noqa: BLE001
            raise AIError(f"调用失败：{e}") from e

    # ================================================================== #
    # Chat Completions 格式
    # ================================================================== #
    def _chat_body(self, messages: list[Message], tools, stream: bool) -> dict:
        msgs: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue  # system 由调用方拼接在头部
            if m.role == "tool":
                msgs.append(
                    {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
                )
            elif m.role == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
                if m.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in m.tool_calls
                    ]
                msgs.append(item)
            else:
                msgs.append({"role": "user", "content": m.content})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": t["function"]} for t in tools
            ]
            body["tool_choice"] = "auto"
        return body

    def _stream_chat(self, messages, tools, system_prompt) -> Iterator[dict]:
        msgs = list(messages)
        if system_prompt and (not msgs or msgs[0].role != "system"):
            msgs = [Message(role="system", content=system_prompt)] + msgs

        body = self._chat_body(msgs, tools, stream=True)
        content_acc: list[str] = []
        reasoning_acc: list[str] = []
        tc_acc: dict[int, dict[str, Any]] = {}

        try:
            with httpx.Client(timeout=self.timeout) as cli:
                with cli.stream(
                    "POST", self._url("/chat/completions"), json=body, headers=self._headers()
                ) as r:
                    if r.status_code != 200:
                        raise httpx.HTTPStatusError(
                            f"HTTP {r.status_code}", request=r.request, response=r
                        )
                    for line in r.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        self.last_model = chunk.get("model", self.last_model)
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("reasoning_content"):
                                reasoning_acc.append(delta["reasoning_content"])
                                yield ev_text(delta["reasoning_content"], reasoning=True)
                            if delta.get("content"):
                                content_acc.append(delta["content"])
                                yield ev_text(delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                slot = tc_acc.setdefault(
                                    idx, {"id": "", "name": "", "arguments": ""}
                                )
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]
        except (httpx.HTTPError, httpx.HTTPStatusError) as e:
            raise self._friendly_error(e) from e

        tool_calls = [
            ToolCall(id=v["id"] or f"call_{i}", name=v["name"], arguments=v["arguments"])
            for i, v in sorted(tc_acc.items())
        ]
        if not content_acc and not tool_calls:
            # 可能是不支持流式的网关：降级为非流式
            yield from self._chat_nonstream(msgs, tools)
            return
        yield {
            "type": "assistant",
            "content": "".join(content_acc),
            "reasoning": "".join(reasoning_acc),
            "tool_calls": tool_calls,
        }
        yield ev_done({}, self.last_model)

    def _chat_nonstream(self, messages, tools) -> Iterator[dict]:
        body = self._chat_body(messages, tools, stream=False)
        with httpx.Client(timeout=self.timeout) as cli:
            r = cli.post(self._url("/chat/completions"), json=body, headers=self._headers())
            if r.status_code != 200:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            data = r.json()
        self.last_model = data.get("model", self.last_model)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        tcs = [
            ToolCall(
                id=t.get("id", f"call_{i}"),
                name=(t.get("function") or {}).get("name", ""),
                arguments=(t.get("function") or {}).get("arguments", ""),
            )
            for i, t in enumerate(msg.get("tool_calls") or [])
        ]
        yield {
            "type": "assistant",
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "tool_calls": tcs,
        }
        yield ev_done(data.get("usage") or {}, self.last_model)

    # ================================================================== #
    # Responses 格式
    # ================================================================== #
    def _responses_body(self, messages: list[Message], tools, stream: bool) -> dict:
        input_items: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "assistant" and m.tool_calls:
                input_items.append(
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": m.content or ""}
                        ],
                    }
                )
                for tc in m.tool_calls:
                    input_items.append(
                        {
                            "type": "function_call",
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    )
            elif m.role == "tool":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.tool_call_id,
                        "output": m.content,
                    }
                )
            elif m.role == "assistant":
                input_items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": m.content or ""}],
                    }
                )
            else:
                input_items.append(
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": m.content}],
                    }
                )
        body: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "temperature": self.temperature,
            "max_output_tokens": self.max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {}),
                }
                for t in tools
            ]
        return body

    def _stream_responses(self, messages, tools, system_prompt) -> Iterator[dict]:
        msgs = list(messages)
        if system_prompt and (not msgs or msgs[0].role != "system"):
            msgs = [Message(role="system", content=system_prompt)] + msgs

        body = self._responses_body(msgs, tools, stream=True)
        if msgs and msgs[0].role == "system":
            body["instructions"] = msgs[0].content

        content_acc: list[str] = []
        reasoning_acc: list[str] = []
        fc_acc: dict[str, dict[str, Any]] = {}
        usage: dict = {}

        try:
            with httpx.Client(timeout=self.timeout) as cli:
                with cli.stream(
                    "POST", self._url("/responses"), json=body, headers=self._headers()
                ) as r:
                    if r.status_code != 200:
                        raise httpx.HTTPStatusError(
                            f"HTTP {r.status_code}", request=r.request, response=r
                        )
                    for line in r.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type", "")
                        if etype == "response.output_text.delta":
                            d = event.get("delta", "")
                            content_acc.append(d)
                            yield ev_text(d)
                        elif etype == "response.reasoning_summary_text.delta":
                            d = event.get("delta", "")
                            reasoning_acc.append(d)
                            yield ev_text(d, reasoning=True)
                        elif etype == "response.function_call_arguments.delta":
                            item_id = event.get("item_id", "")
                            slot = fc_acc.setdefault(item_id, {"id": item_id, "name": "", "arguments": ""})
                            slot["arguments"] += event.get("delta", "")
                        elif etype == "response.output_item.done":
                            item = event.get("item") or {}
                            if item.get("type") == "function_call":
                                slot = fc_acc.setdefault(
                                    item.get("id", ""), {"id": item.get("id", ""), "name": "", "arguments": ""}
                                )
                                slot["name"] = item.get("name", slot["name"])
                                slot["arguments"] = item.get("arguments", slot["arguments"])
                        elif etype == "response.completed":
                            resp = event.get("response") or {}
                            usage = resp.get("usage") or {}
                            self.last_model = resp.get("model", self.last_model)
                        elif etype == "response.failed":
                            error = (event.get("response") or {}).get("error") or {}
                            raise AIError(f"模型响应失败：{error.get('message', '未知错误')}")
                        elif etype == "error":
                            raise AIError(f"接口错误：{event.get('message', '未知错误')}")
        except (httpx.HTTPError, httpx.HTTPStatusError) as e:
            raise self._friendly_error(e) from e

        if not content_acc and not fc_acc:
            yield from self._responses_nonstream(msgs, tools)
            return

        tool_calls = [
            ToolCall(id=v["id"] or f"fc_{i}", name=v["name"], arguments=v["arguments"])
            for i, v in enumerate(fc_acc.values())
        ]
        yield {
            "type": "assistant",
            "content": "".join(content_acc),
            "reasoning": "".join(reasoning_acc),
            "tool_calls": tool_calls,
        }
        yield ev_done(usage, self.last_model)

    def _responses_nonstream(self, messages, tools) -> Iterator[dict]:
        body = self._responses_body(messages, tools, stream=False)
        if messages and messages[0].role == "system":
            body["instructions"] = messages[0].content
        with httpx.Client(timeout=self.timeout) as cli:
            r = cli.post(self._url("/responses"), json=body, headers=self._headers())
            if r.status_code != 200:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            data = r.json()
        self.last_model = data.get("model", self.last_model)
        content: list[str] = []
        tcs: list[ToolCall] = []
        for item in data.get("output") or []:
            if item.get("type") == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        content.append(c.get("text", ""))
            elif item.get("type") == "function_call":
                tcs.append(
                    ToolCall(
                        id=item.get("id", f"fc_{len(tcs)}"),
                        name=item.get("name", ""),
                        arguments=item.get("arguments", ""),
                    )
                )
        yield {
            "type": "assistant",
            "content": "".join(content),
            "reasoning": "",
            "tool_calls": tcs,
        }
        yield ev_done(data.get("usage") or {}, self.last_model)

    # ------------------------------------------------------------------ #
    def close(self) -> None:  # httpx.Client 均为即用即弃，无需长连接
        pass
