"""应用配置：双 OpenAI 兼容接口档案 + 应用设置，落盘 JSON。"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

APP_NAME = "eNSP-AWA"
APP_NAME_CN = "eNSP AI 助手"

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "主接口",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    # OpenAI 两种兼容格式: chat_completions | responses
    "format": "chat_completions",
    "temperature": 0.3,
    "max_tokens": 4096,
}

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "active_profile": 0,
    "profiles": [deepcopy(DEFAULT_PROFILE), {**deepcopy(DEFAULT_PROFILE), "name": "备用接口"}],
    "system_prompt": (
        "你是 eNSP-AWA 内置的华为 eNSP 网络实验助手。你精通华为 VRP 命令行、"
        "eNSP 模拟器拓扑设计、IP 网络规划与网络工程实验。你可以调用工具直接生成 "
        ".topo 拓扑文件（GBK 编码）、解析拓扑、管理设备会话、下发配置并生成实验报告。"
        "回答使用中文，技术内容准确、条理清晰。涉及配置时优先给出完整可粘贴的命令序列。"
    ),
    "topo": {
        "output_dir": "",
        "com_port_start": 2000,
        "canvas_width": 1200,
        "canvas_height": 800,
    },
    "report": {
        "output_dir": "",
        "author": "",
        "school": "",
        "class_name": "",
    },
    "window": {"width": 1440, "height": 900},
}

_LOCK = threading.RLock()


def app_base() -> Path:
    """应用基准目录：开发时取项目根（src 上级）；Nuitka 编译后取 exe 所在目录。"""
    import sys

    exe = Path(sys.executable)
    if exe.name.lower().startswith(("python", "pypy")):
        return Path(__file__).resolve().parent.parent.parent
    return exe.parent


def config_dir() -> Path:
    """配置文件目录：%APPDATA%\\eNSP-AWA；exe 旁放 portable.txt 则走便携目录。"""
    if (app_base() / "portable.txt").exists():
        d = app_base() / "config"
    else:
        d = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


class AppConfig:
    """线程安全的 JSON 配置存取，带深合并默认值。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "config.json")
        self._data: dict[str, Any] = deepcopy(DEFAULTS)
        self._listeners: list[Callable[[], None]] = []
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        with _LOCK:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self._data = self._merge(deepcopy(DEFAULTS), raw)
                except Exception:
                    self._data = deepcopy(DEFAULTS)
            else:
                self.save()

    def save(self) -> None:
        with _LOCK:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.path)

    @staticmethod
    def _merge(base: dict, over: dict) -> dict:
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = AppConfig._merge(base[k], v)
            else:
                base[k] = v
        return base

    # ------------------------------------------------------------------ #
    def get(self, key: str, default: Any = None) -> Any:
        with _LOCK:
            cur: Any = self._data
            for part in key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return cur

    def set(self, key: str, value: Any) -> None:
        with _LOCK:
            parts = key.split(".")
            cur = self._data
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
            self.save()
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def on_change(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    @property
    def data(self) -> dict[str, Any]:
        with _LOCK:
            return deepcopy(self._data)

    # ------------------------------------------------------------------ #
    def profile(self, index: int | None = None) -> dict[str, Any]:
        idx = self.get("active_profile", 0) if index is None else index
        profiles = self.get("profiles", [])
        if 0 <= idx < len(profiles):
            return profiles[idx]
        return deepcopy(DEFAULT_PROFILE)

    def active_profile(self) -> dict[str, Any]:
        p = self.profile()
        merged = deepcopy(DEFAULT_PROFILE)
        merged.update({k: v for k, v in p.items() if v not in (None, "")})
        return merged
