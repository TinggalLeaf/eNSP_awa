"""eNSP 设备服务：Telnet 会话管理、命令下发、端口扫描的线程安全封装。

底层复用 vendor 的 SessionManager / port_scanner（grbj-ensp-mcp, MIT）。
所有网络操作均为阻塞 IO，调用方应放在工作线程中执行。
"""

from __future__ import annotations

import threading
from typing import Any

from .vendor.port_scanner import scan_ensp_devices, scan_single_port
from .vendor.session_manager import SessionError, get_session_manager

_LOCK = threading.RLock()


def scan_devices(port_start: int = 2000, port_end: int = 2100, host: str = "127.0.0.1") -> dict:
    """扫描 eNSP Telnet 端口，返回可达端口列表。"""
    try:
        found = scan_ensp_devices(host, port_range=(port_start, port_end))
        ports = [
            {"port": p.port, "host": p.host, "reachable": p.reachable}
            for p in found
        ]
        return {"ok": True, "host": host, "range": [port_start, port_end], "ports": ports}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def check_port(port: int, host: str = "127.0.0.1") -> dict:
    try:
        r = scan_single_port(host, port)
        return {"ok": True, "port": port, "reachable": r.reachable}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def connect(name: str, port: int, host: str = "127.0.0.1") -> dict:
    """建立（或复用）到某端口的 Telnet 会话。"""
    try:
        sm = get_session_manager()
        session = sm.create(host=host, port=port, name=name, reuse=True)
        snap = session.snapshot()
        return {"ok": True, "session": _public_snapshot(snap)}
    except SessionError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"连接失败: {e}"}


def list_sessions() -> dict:
    sm = get_session_manager()
    sessions = [_public_snapshot(s) for s in sm.list_sessions()]
    return {"ok": True, "sessions": sessions}


def disconnect(name_or_id: str) -> dict:
    try:
        sm = get_session_manager()
        sm.close(name_or_id)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def disconnect_all() -> dict:
    sm = get_session_manager()
    sm.close_all()
    return {"ok": True}


def send_command(name_or_id: str, command: str, timeout: float = 15.0) -> dict:
    try:
        sm = get_session_manager()
        session = sm.get(name_or_id)
        result = session.send_command(command, timeout=timeout)
        return {
            "ok": True,
            "device": session.name,
            "command": result.command,
            "output": result.output,
            "errored": result.errored,
            "duration": result.duration,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def send_commands(name_or_id: str, commands: list[str], stop_on_error: bool = False) -> dict:
    try:
        sm = get_session_manager()
        session = sm.get(name_or_id)
        results = session.send_commands(commands, stop_on_error=stop_on_error)
        return {
            "ok": True,
            "device": session.name,
            "results": [
                {
                    "command": r.command,
                    "output": r.output,
                    "errored": r.errored,
                    "duration": r.duration,
                }
                for r in results
            ],
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def save_config(name_or_id: str) -> dict:
    try:
        sm = get_session_manager()
        session = sm.get(name_or_id)
        results = session.save()
        return {
            "ok": True,
            "device": session.name,
            "results": [
                {"command": r.command, "output": r.output, "errored": r.errored}
                for r in results
            ],
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def get_device_info(name_or_id: str) -> dict:
    try:
        sm = get_session_manager()
        session = sm.get(name_or_id)
        info = session.refresh_info()
        return {
            "ok": True,
            "device": session.name,
            "info": {
                "hostname": getattr(info, "hostname", ""),
                "model": getattr(info, "model", ""),
                "version": getattr(info, "version", ""),
                "uptime": getattr(info, "uptime", ""),
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def get_running_config(name_or_id: str) -> dict:
    return send_command(name_or_id, "display current-configuration", timeout=20.0)


def get_recent_output(name_or_id: str, lines: int = 200) -> dict:
    try:
        sm = get_session_manager()
        session = sm.get(name_or_id)
        return {"ok": True, "device": session.name, "output": session.get_recent_output(lines)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _public_snapshot(snap: dict[str, object]) -> dict[str, Any]:
    keys = (
        "session_id",
        "name",
        "host",
        "port",
        "status",
        "in_system_view",
        "idle_seconds",
        "device_info",
        "last_error",
    )
    return {k: snap.get(k) for k in keys if k in snap}
