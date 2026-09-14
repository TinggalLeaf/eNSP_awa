"""Agent 循环端到端测试：mock 服务强制一轮工具调用，验证完整编排。"""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ensp_awa.ai.agent import Agent
from ensp_awa.config import AppConfig

PORT = 18322


class Handler(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        n = type(self).calls
        if n == 1:
            # 第一轮：要求调用 list_supported_models
            chunks = [
                json.dumps({"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "list_supported_models", "arguments": "{}"}}
                ]}}]}),
            ]
        else:
            chunks = [
                json.dumps({"choices": [{"delta": {"content": "已查到"}}]}),
                json.dumps({"choices": [{"delta": {"content": "16 个型号。"}}]}),
            ]
        data = ("".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    Handler.calls = 0
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tmp = Path(tempfile.mkdtemp())
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps({
        "profiles": [{"name": "t", "base_url": f"http://127.0.0.1:{PORT}/v1",
                      "api_key": "k", "model": "m", "format": "chat_completions"}],
        "active_profile": 0,
        "system_prompt": "sys",
    }), encoding="utf-8")
    config = AppConfig(cfg_path)

    events: list[dict] = []
    agent = Agent(config, events.append)
    import threading as th
    added = agent.run_turn([], "有哪些型号？", th.Event())

    types = [e["type"] for e in events]
    assert "tool_start" in types and "tool_end" in types, types
    te = [e for e in events if e["type"] == "tool_end"][0]
    assert te["name"] == "list_supported_models" and te["ok"], te
    roles = [m.role for m in added]
    assert roles == ["user", "assistant", "tool", "assistant"], roles
    assert added[-1].content == "已查到16 个型号。"
    print("PASS agent loop:", roles)
    print("PASS tool exec summary:", te["summary"][:60])
    srv.shutdown()
    print("AGENT TEST PASSED")


if __name__ == "__main__":
    main()
