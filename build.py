"""Nuitka 一键打包脚本。

用法:
    python build.py            # 正式版（无控制台窗口）
    python build.py --check    # 自检版（保留控制台，设 ENSP_AWA_CHECK=1 可自动冒烟）

产物: build/dist/eNSP-AWA.exe（onefile 单文件）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ASSETS = SRC / "ensp_awa" / "assets"
VENDOR = SRC / "ensp_awa" / "ensp" / "vendor"


def main() -> int:
    check = "--check" in sys.argv
    out_dir = ROOT / "build" / ("dist_check" if check else "dist")
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--assume-yes-for-downloads",
        "--jobs=4",
        f"--output-dir={out_dir}",
        "--output-filename=eNSP-AWA.exe",
        f"--windows-icon-from-ico={ASSETS / 'icon.ico'}",
        # 前端与资源按包布局内嵌
        f"--include-data-dir={ASSETS / 'web'}=ensp_awa/assets/web",
        f"--include-data-file={ASSETS / 'icon.png'}=ensp_awa/assets/icon.png",
        f"--include-data-file={VENDOR / 'NOTICE.md'}=ensp_awa/ensp/vendor/NOTICE.md",
        f"--include-data-file={VENDOR / 'LICENSE.upstream'}=ensp_awa/ensp/vendor/LICENSE.upstream",
        # markdown 以字符串形式加载扩展，显式打包
        "--include-package=markdown.extensions",
        # reportlab 动态加载 CID 字体数据
        "--include-package=reportlab.pdfbase",
        "--include-package=reportlab.rl_config",
        "--remove-output",
        "--no-pyi-file",
    ]
    if check:
        cmd.append("--windows-console-mode=force")
    else:
        cmd.append("--windows-console-mode=disable")
    cmd.append(str(SRC / "entry.py"))

    print("[build] ", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("[build] FAILED")
        return r.returncode
    exe = out_dir / "eNSP-AWA.exe"
    print(f"[build] OK -> {exe} ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    print("[build] 自检:  set ENSP_AWA_CHECK=1 && eNSP-AWA.exe" if check
          else "[build] 直接双击 eNSP-AWA.exe 运行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
