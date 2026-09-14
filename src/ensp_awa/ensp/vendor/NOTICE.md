# Vendor Notice: grbj-ensp-mcp (Simon-Ensp-Mcp-Pro fork)

## 代码来源

- 上游项目：**grbj-ensp-mcp v0.2.2**（广然笔记发布，MIT 协议）
- Fork 补丁：**Simon-Ensp-Mcp-Pro**（<https://github.com/cg689/Simon-Ensp-Mcp-Pro>，cg689，MIT 协议）
- 本目录代码拷贝自 Simon-Ensp-Mcp-Pro 的 `main` 分支（`src/grbj_ensp_mcp/` 包）

## 许可

MIT。上游 LICENSE 全文见本目录的 [LICENSE.upstream](LICENSE.upstream)
（Copyright (c) 2026 grbj-ensp-mcp contributors）。

## 文件清单

按上游文件名原样保留：

| 文件 | 说明 |
|---|---|
| `__init__.py` | 包元信息（`__version__ = "0.2.1"`） |
| `config.py` | 全局配置 `EnspConfig` / `load_config()` |
| `telnet_client.py` | 基于 socket 的 Telnet 客户端 |
| `session_manager.py` | 多设备会话管理（`DeviceSession` / `SessionManager`） |
| `port_scanner.py` | eNSP Telnet 端口并发扫描 |
| `topo_parser.py` | `.topo` 拓扑解析（XML / ZIP，defusedxml） |
| `topo_builder.py` | eNSP 原生 `.topo` XML 生成器 |
| `server.py` | MCP Server 入口（FastMCP，31 个工具） |
| `diagnostics.py` | 命令错误规则诊断引擎（`server.py` 依赖，补抓） |
| `lldp_verifier.py` | LLDP 邻居采集与拓扑交叉验证（`server.py` 依赖，补抓） |

注：任务清单未包含 `diagnostics.py` / `lldp_verifier.py`，但 `server.py`
直接 `from .diagnostics import ...` / `from .lldp_verifier import ...`，
缺少这两个模块包无法 import，故一并 vendor。

## 本地改动说明

- 仅做包名适配（如需），核心逻辑不变。
- 当前拷贝为上游原样字节（未经任何修改）；包内模块间一律使用相对导入
  （`from .config import ...` 等），把整个目录作为 Python 包嵌入即可，
  无需改动 import 语句。
