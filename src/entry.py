"""Nuitka 打包入口（位于 src/ 下，使 ensp_awa 包可被解析）。"""

from ensp_awa.app import main

if __name__ == "__main__":
    raise SystemExit(main())
