"""AI 客户端协议层测试：本地 mock OpenAI 兼容服务，覆盖两种格式。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from ensp_awa.ai.client import FORMAT_CHAT, FORMAT_RESPONSES, Message, OpenAICompatClient

PORT = 18321
AUTH = "Bearer sk-test-123"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_sse(self, chunks: list[str]):
        body = "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        if auth != AUTH:
            self._json({"error": "unauthorized"}, 401)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        if self.path in ("/v1/chat/completions", "/chat/completions"):
            if not body.get("stream"):
                self._json({
                    "model": "mock-chat",
                    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                    "usage": {"total_tokens": 3},
                })
                return
            chunks = [
                json.dumps({"model": "mock-chat", "choices": [{"delta": {"content": "你"}}]}),
                json.dumps({"model": "mock-chat", "choices": [{"delta": {"content": "好"}}]}),
                json.dumps({"model": "mock-chat", "choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "call_1", "function": {"name": "scan_", "arguments": "{\"port"}}
                ]}}]}),
                json.dumps({"model": "mock-chat", "choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": "_start\": 2000}"}}
                ]}}]}),
            ]
            self._send_sse(chunks)
        elif self.path in ("/v1/responses", "/responses"):
            if not body.get("stream"):
                self._json({
                    "model": "mock-resp",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "pong"}]}],
                    "usage": {"total_tokens": 3},
                })
                return
            chunks = [
                json.dumps({"type": "response.output_text.delta", "delta": "你"}),
                json.dumps({"type": "response.output_text.delta", "delta": "好"}),
                json.dumps({"type": "response.output_item.done", "item": {
                    "type": "function_call", "id": "fc_1", "name": "list_sessions", "arguments": "{}"}}),
                json.dumps({"type": "response.completed", "response": {
                    "model": "mock-resp", "usage": {"total_tokens": 5}}}),
            ]
            self._send_sse(chunks)
        else:
            self._json({"error": "not found"}, 404)


def run_server() -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def collect(client, messages, tools=None):
    events = list(client.stream(messages, tools, "sys"))
    return events


def main() -> None:
    srv = run_server()
    base = f"http://127.0.0.1:{PORT}/v1"
    tools = [{
        "type": "function",
        "function": {"name": "list_sessions", "description": "d", "parameters": {"type": "object"}},
    }]

    # ---- chat completions 流式 + 工具调用
    c = OpenAICompatClient(base, "sk-test-123", "m", FORMAT_CHAT)
    evs = collect(c, [Message(role="user", content="hi")], tools)
    texts = [e["delta"] for e in evs if e["type"] == "text"]
    final = [e for e in evs if e["type"] == "assistant"][-1]
    assert "".join(texts) == "你好", texts
    assert final["tool_calls"][0].name == "scan_"
    assert json.loads(final["tool_calls"][0].arguments) == {"port_start": 2000}
    print("PASS chat_completions stream + tool_calls:", "".join(texts), "→", final["tool_calls"][0].name)

    # ---- responses 流式 + 工具
    c2 = OpenAICompatClient(base, "sk-test-123", "m", FORMAT_RESPONSES)
    evs2 = collect(c2, [Message(role="user", content="hi")], tools)
    texts2 = [e["delta"] for e in evs2 if e["type"] == "text"]
    final2 = [e for e in evs2 if e["type"] == "assistant"][-1]
    assert "".join(texts2) == "你好"
    assert final2["tool_calls"][0].name == "list_sessions"
    print("PASS responses stream + tool_calls:", "".join(texts2), "→", final2["tool_calls"][0].name)

    # ---- 多轮历史转换（两种格式都不应炸）
    history = [
        Message(role="user", content="q1"),
        Message(role="assistant", content="a1", tool_calls=final["tool_calls"]),
        Message(role="tool", tool_call_id="call_1", name="scan_", content='{"ok": true}'),
        Message(role="user", content="q2"),
    ]
    collect(c, history, tools)
    collect(c2, history, tools)
    print("PASS multi-turn history for both formats")

    # ---- 401 友好错误
    bad = OpenAICompatClient(base, "wrong-key", "m", FORMAT_CHAT)
    try:
        list(bad.stream([Message(role="user", content="x")], None, ""))
        raise AssertionError("should have raised")
    except Exception as e:
        assert "401" in str(e), e
        print("PASS 401 friendly error:", e)

    # ---- 404
    c3 = OpenAICompatClient(base, "sk-test-123", "m", "responses")
    c3.base_url = f"http://127.0.0.1:{PORT}/v2"  # 无 /responses 端点
    try:
        list(c3.stream([Message(role="user", content="x")], None, ""))
        raise AssertionError("should have raised")
    except Exception as e:
        assert "404" in str(e), e
        print("PASS 404 friendly error:", e)

    srv.shutdown()
    print("ALL CLIENT TESTS PASSED")


if __name__ == "__main__":
    main()
