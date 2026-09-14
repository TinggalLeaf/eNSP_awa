"""PyWebview JS API 桥：前端所有功能经此调用 Python 后端。

约定：
- 所有方法返回 dict，至少含 ok 字段；耗时操作放工作线程，结果经事件流推回。
- 事件推送: window.__AWA__.emit(event_dict)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from .ai.agent import Agent, AgentCancelled
from .ai.client import Message, OpenAICompatClient, ToolCall
from .config import AppConfig
from .ensp import device_service, topo_service
from .report import report_service


def _msg_to_dict(m: Message) -> dict:
    return {
        "role": m.role,
        "content": m.content,
        "reasoning": m.reasoning,
        "tool_call_id": m.tool_call_id,
        "name": m.name,
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls
        ],
    }


def _msg_from_dict(d: dict) -> Message:
    return Message(
        role=d.get("role", "user"),
        content=d.get("content", ""),
        reasoning=d.get("reasoning", ""),
        tool_call_id=d.get("tool_call_id", ""),
        name=d.get("name", ""),
        tool_calls=[
            ToolCall(id=tc.get("id", ""), name=tc.get("name", ""), arguments=tc.get("arguments", ""))
            for tc in d.get("tool_calls", [])
        ],
    )


class Api:
    def __init__(self, config: AppConfig, emit: Callable[[dict[str, Any]], None]) -> None:
        self.config = config
        self.emit = emit
        self._chat_lock = threading.RLock()
        self._history: list[Message] = []
        self._cancel = threading.Event()
        self._chat_busy = False
        self._load_history()

    # ------------------------------------------------------------------ #
    # 事件推送
    # ------------------------------------------------------------------ #
    def _push(self, event: dict[str, Any]) -> None:
        try:
            self.emit(event)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def get_config(self) -> dict:
        data = self.config.data
        # api_key 脱敏回显
        for p in data.get("profiles", []):
            key = p.get("api_key", "")
            if key:
                p["api_key"] = key
        data["paths"] = {
            "config_dir": str(self.config.path.parent),
            "report_dir": str(report_service.default_report_dir()),
        }
        return {"ok": True, "config": data}

    def save_config(self, data: dict) -> dict:
        try:
            for key in ("active_profile", "system_prompt"):
                if key in data:
                    self.config.set(key, data[key])
            if "profiles" in data:
                self.config.set("profiles", data["profiles"])
            if "topo" in data:
                for k, v in data["topo"].items():
                    self.config.set(f"topo.{k}", v)
            if "report" in data:
                for k, v in data["report"].items():
                    self.config.set(f"report.{k}", v)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def test_connection(self) -> dict:
        """用当前激活档案发一个最小请求验证连通性。"""
        profile = self.config.active_profile()

        def work() -> None:
            try:
                client = OpenAICompatClient.from_profile(profile)
                events = list(client.stream([Message(role="user", content="ping")], None, ""))
                ok = any(e.get("type") == "done" for e in events)
                self._push({"type": "test_result", "ok": ok, "model": client.last_model})
            except Exception as e:  # noqa: BLE001
                self._push({"type": "test_result", "ok": False, "error": str(e)})

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # AI 对话
    # ------------------------------------------------------------------ #
    def _history_file(self) -> Path:
        return self.config.path.parent / "history.json"

    def _load_history(self) -> None:
        try:
            f = self._history_file()
            if f.exists():
                raw = json.loads(f.read_text(encoding="utf-8"))
                self._history = [_msg_from_dict(d) for d in raw][-200:]
        except Exception:
            self._history = []

    def _save_history(self) -> None:
        try:
            self._history_file().write_text(
                json.dumps([_msg_to_dict(m) for m in self._history[-200:]], ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def chat_history(self) -> dict:
        with self._chat_lock:
            return {"ok": True, "history": [_msg_to_dict(m) for m in self._history]}

    def chat_clear(self) -> dict:
        with self._chat_lock:
            self._history = []
            self._save_history()
        return {"ok": True}

    def chat_stop(self) -> dict:
        self._cancel.set()
        return {"ok": True}

    def chat_send(self, text: str) -> dict:
        if self._chat_busy:
            return {"ok": False, "error": "正在回复中，请先停止或等待完成"}
        profile = self.config.active_profile()
        if not profile.get("base_url") or not profile.get("api_key"):
            return {"ok": False, "error": "请先在设置中配置接口地址与 API Key"}

        def work() -> None:
            self._chat_busy = True
            self._cancel.clear()
            agent = Agent(self.config, self._push, save_dialog=self.save_dialog)
            try:
                with self._chat_lock:
                    history_snapshot = list(self._history)
                added = agent.run_turn(history_snapshot, text, self._cancel)
                with self._chat_lock:
                    self._history.extend(added)
                    self._save_history()
            except AgentCancelled:
                self._push({"type": "cancelled"})
            except Exception as e:  # noqa: BLE001
                self._push({"type": "error", "message": str(e)})
            finally:
                self._chat_busy = False
                self._push({"type": "turn_end"})

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # 拓扑工具箱
    # ------------------------------------------------------------------ #
    def topo_models(self) -> dict:
        from .ai.tools import _list_models

        return _list_models()

    def topo_generate(self, spec: dict) -> dict:
        """spec: {filename, devices: [{name, model}], links: [...]}"""
        t = topo_service.TopoSpec(
            width=int(spec.get("width", 1200)), height=int(spec.get("height", 800))
        )
        for d in spec.get("devices", []):
            t.devices.append(
                topo_service.DeviceIn(
                    name=str(d["name"]),
                    model=str(d.get("model", "AR2240")),
                    cx=float(d["cx"]) if d.get("cx") is not None else None,
                    cy=float(d["cy"]) if d.get("cy") is not None else None,
                )
            )
        for l in spec.get("links", []):
            t.links.append(
                topo_service.LinkIn(
                    src=str(l["src"]),
                    dst=str(l["dst"]),
                    src_iface=str(l.get("src_iface", "") or ""),
                    dst_iface=str(l.get("dst_iface", "") or ""),
                    link_type=str(l.get("type", "Copper")),
                )
            )
        filename = str(spec.get("filename") or "topology.topo")
        if not filename.endswith(".topo"):
            filename += ".topo"
        out_dir = self.config.get("topo.output_dir", "") or str(
            Path.home() / "Documents" / "eNSP-AWA" / "topo"
        )
        out = Path(out_dir) / filename
        return topo_service.generate_topo(t, out)

    def topo_parse(self, path: str) -> dict:
        try:
            return {"ok": True, "summary": topo_service.load_topo_summary(path)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def topo_validate_text(self, path: str) -> dict:
        """GBK 回读校验：展示设备名是否乱码。"""
        try:
            text = topo_service.read_topo_text(path)
            head = text[:200]
            return {"ok": True, "encoding_probe": head, "device_names": _extract_names(text)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # 设备工具箱
    # ------------------------------------------------------------------ #
    def dev_scan(self, port_start: int = 2000, port_end: int = 2100) -> dict:
        return device_service.scan_devices(port_start, port_end)

    def dev_connect(self, name: str, port: int) -> dict:
        return device_service.connect(name, port)

    def dev_disconnect(self, name: str) -> dict:
        return device_service.disconnect(name)

    def dev_disconnect_all(self) -> dict:
        return device_service.disconnect_all()

    def dev_sessions(self) -> dict:
        return device_service.list_sessions()

    def dev_command(self, device: str, command: str) -> dict:
        return device_service.send_command(device, command)

    def dev_commands(self, device: str, commands: list[str]) -> dict:
        return device_service.send_commands(device, [str(c) for c in commands])

    def dev_save(self, device: str) -> dict:
        return device_service.save_config(device)

    def dev_info(self, device: str) -> dict:
        return device_service.get_device_info(device)

    # ------------------------------------------------------------------ #
    # 报告
    # ------------------------------------------------------------------ #
    def report_generate(self, title: str, topo_path: str = "", devices: list[str] | None = None,
                        export_pdf: bool = True) -> dict:
        out_dir = self.config.get("report.output_dir", "") or None
        try:
            result = report_service.collect_and_render(
                title=title or "eNSP 网络实验报告",
                topo_path=topo_path or None,
                devices=devices or None,
                out_dir=out_dir,
                author=self.config.get("report.author", ""),
            )
            if export_pdf:
                result["pdf"] = report_service.export_pdf(result["markdown_path"])
            return {"ok": True, **result}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def report_export_pdf(self, md_path: str) -> dict:
        try:
            pdf = report_service.export_pdf(md_path)
            return {"ok": True, "pdf": pdf}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # 系统对话框
    # ------------------------------------------------------------------ #
    def pick_file(self, filters: str = "拓扑文件 (*.topo)|*.topo|所有文件 (*.*)|*.*") -> str:
        try:
            import webview

            result = webview.windows[0].create_file_dialog(webview.OPEN_DIALOG, file_types=filters)
            return result[0] if result else ""
        except Exception:
            return ""

    def pick_dir(self) -> str:
        try:
            import webview

            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            return result[0] if result else ""
        except Exception:
            return ""

    def save_dialog(self, default_filename: str, filters: str) -> str:
        try:
            import webview

            result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG, save_filename=default_filename, file_types=filters
            )
            return result[0] if result else ""
        except Exception:
            return ""


def _extract_names(text: str) -> list[str]:
    import re

    return re.findall(r'<dev[^>]*\bname="([^"]+)"', text)
