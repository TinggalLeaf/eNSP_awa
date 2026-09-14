"""eNSP-AWA 应用引导：PyWebview 窗口 + JS 事件桥。"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import webview

from .api import Api
from .config import APP_NAME_CN, AppConfig

WEB_DIR = Path(__file__).resolve().parent / "assets" / "web"


def web_dir() -> Path:
    """定位前端资源：开发用包内路径；Nuitka 打包后同样成立（数据文件随包布局）。"""
    if WEB_DIR.exists():
        return WEB_DIR
    base = Path(getattr(sys, "argv", ["ensp-awa"])[0]).resolve().parent
    cand = base / "assets" / "web"
    return cand if cand.exists() else WEB_DIR


def main() -> int:
    config = AppConfig()
    width = int(config.get("window.width", 1440))
    height = int(config.get("window.height", 900))

    state: dict[str, webview.Window | None] = {"win": None}

    def emit(event: dict) -> None:
        win = state["win"]
        if win is None:
            return
        try:
            payload = json.dumps(event, ensure_ascii=False)
            win.evaluate_js(f"window.__AWA__ && window.__AWA__.emit({payload});")
        except Exception:
            pass

    api = Api(config, emit)

    index = web_dir() / "index.html"
    win = webview.create_window(
        APP_NAME_CN,
        str(index),
        js_api=api,
        width=width,
        height=height,
        min_size=(1120, 700),
        background_color="#0a0e1a",
        text_select=True,
    )
    state["win"] = win

    def on_closed() -> None:
        try:
            from .ensp import device_service

            device_service.disconnect_all()
        except Exception:
            pass

    win.events.closed += on_closed

    if os.environ.get("ENSP_AWA_CHECK") == "1":
        _install_self_check(win)

    webview.start(debug=False, private_mode=False)
    return 0


def _install_self_check(win: webview.Window) -> None:
    """ENSP_AWA_CHECK=1 时：启动 5 秒后自动 dump 前端状态并退出（用于编译产物冒烟）。"""

    def check() -> None:
        time.sleep(5)
        try:
            state_json = win.evaluate_js(
                "JSON.stringify({title: document.title, gsap: !!window.gsap,"
                " particles: !!window.tsParticles, apiReady: !!window.pywebview,"
                " activeView: (document.querySelector('.view.active')||{}).id,"
                " errs: window.__errs || []})"
            )
            print("FRONTEND_STATE:", state_json, flush=True)
        except Exception as e:  # noqa: BLE001
            print("CHECK_ERROR:", e, flush=True)
        try:
            win.destroy()
        except Exception:
            pass

    threading.Thread(target=check, daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
