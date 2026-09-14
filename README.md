# eNSP-AWA · eNSP AI 助手

基于 PyWebview 的 eNSP（华为网络模拟器）AI 助手桌面应用：AI 对话 + eNSP 工具箱 + 实验报告生成，Nuitka 编译为原生单文件 exe。

## 功能

- **AI 助手**：支持 OpenAI 两种兼容格式接口（Chat Completions `/chat/completions` 与 Responses `/responses`），双接口档案可切换；内置工具调用（函数调用），流式输出 + 思考链展示。
- **拓扑工坊**：可视化编辑设备/连线并生成 `.topo` 文件，**GBK 编码落盘三要素**保证中文设备名不乱码；自动布局、com_port 自动规划；解析已有 `.topo`（自动识别 GBK/UTF-8）+ GBK 回读校验。
- **设备终端**：扫描 eNSP Telnet 端口（2000-2100）、按 console 端口建连、单条/批量 VRP 命令下发、快捷 display 命令、保存配置。
- **实验报告**：一键采集拓扑 + 运行配置 + 关键 display 校验输出，导出 Markdown / PDF（Edge headless 打印，reportlab 兜底）。
- **工具化 AI**：13 个工具（生成/解析拓扑、扫描/连接/命令/保存/采集、报告生成），AI 可自主完成「生成拓扑 → 启动后下发配置 → 出报告」全流程。

## 开发运行

```bash
uv sync
uv run ensp-awa          # 或 .venv/Scripts/python -m ensp_awa
```

## Nuitka 打包

```bash
.venv/Scripts/python build.py          # 正式版 build/dist/eNSP-AWA.exe
.venv/Scripts/python build.py --check  # 自检版（保留控制台）
# 自检：ENSP_AWA_CHECK=1 时启动 5 秒后 dump 前端状态并退出
```

## GBK 落盘三要素（关键坑）

eNSP 是老牌 Win32 程序，`.topo` 实为单字节 ANSI/GBK：

1. XML 声明恒为 `encoding="UNICODE"`（假声明，照抄）
2. 换行 CRLF
3. 实际编码 GBK，无 BOM

写错任意一条，中文设备名必乱码。本项目的 `ensp/topo_service.py::to_ensp_bytes` 统一处理，生成后自动「GBK 回读 → UTF-8 临时副本 → parser 解析比对」自检。

## 架构

```
src/ensp_awa/
├── app.py            # PyWebview 窗口引导 + 事件桥
├── api.py            # JS API 桥（前端 ↔ 后端）
├── config.py         # 配置（双接口档案、JSON 落盘 %APPDATA%/eNSP-AWA）
├── ai/
│   ├── client.py     # OpenAI 双格式客户端（SSE 流式、工具调用、降级）
│   ├── agent.py      # Agent 循环（工具编排、可取消）
│   └── tools.py      # 13 个工具的 schema + 分发
├── ensp/
│   ├── topo_service.py   # .topo 生成/解析/GBK 三要素/自检/布局
│   ├── device_service.py # Telnet 会话/命令/扫描
│   └── vendor/           # grbj-ensp-mcp v0.2.2 + Simon-Ensp-Mcp-Pro 补丁（MIT）
├── report/report_service.py  # 报告采集 + Markdown/PDF
└── assets/           # 前端（HTML/CSS/JS + GSAP/tsParticles/marked 本地库）与自绘图标
```

## 来源与许可

- 上游核心 [grbj-ensp-mcp v0.2.2](https://grbj.cn)（广然笔记，MIT）及社区增强 fork [Simon-Ensp-Mcp-Pro](https://github.com/cg689/Simon-Ensp-Mcp-Pro)（cg689，MIT），vendored 于 `ensp/vendor/`，许可文本见 `vendor/LICENSE.upstream`。
- 本仓库改动部分同样以 MIT 提供。
