"""AI 可调用的工具集：OpenAI function schema + 统一分发执行。

覆盖四类能力：
- 拓扑: 生成 .topo（GBK 落盘）/ 解析已有 .topo / 型号接口查询
- 设备: 扫描 / 建连 / 命令下发 / 配置保存 / 信息采集
- 报告: 一键收集拓扑+配置+display 输出，导出 Markdown/PDF
- 系统: 选择保存路径等
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..ensp import device_service, topo_service

MAX_TOOL_OUTPUT = 6000  # 单个工具结果注入上下文的长度上限


def _trunc(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（已截断，共 {len(text)} 字符）"


# ---------------------------------------------------------------------- #
# Schema 定义
# ---------------------------------------------------------------------- #
def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def build_tool_schemas() -> list[dict]:
    return [
        _schema(
            "list_supported_models",
            "列出 eNSP 拓扑生成支持的设备型号、默认接口表与接口名→index 映射规则。"
            "在生成拓扑前若不确定型号写法或接口序号，先调用本工具查询。",
            {},
            [],
        ),
        _schema(
            "generate_topology",
            "生成 eNSP .topo 拓扑文件并以 GBK 编码落盘（中文设备名不乱码）。"
            "自动完成画布布局与 com_port 分配；生成后自检并返回结果。",
            {
                "filename": {
                    "type": "string",
                    "description": "输出文件名，如 'ospf_lab.topo'",
                },
                "devices": {
                    "type": "array",
                    "description": "设备清单",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "设备名（可用中文）"},
                            "model": {
                                "type": "string",
                                "description": "型号，如 AR2220/S5700/USG6000V/PC",
                            },
                        },
                        "required": ["name", "model"],
                    },
                },
                "links": {
                    "type": "array",
                    "description": "连线清单",
                    "items": {
                        "type": "object",
                        "properties": {
                            "src": {"type": "string"},
                            "dst": {"type": "string"},
                            "src_iface": {
                                "type": "string",
                                "description": "源接口名，如 GE0/0/1；缺省取 0 号口",
                            },
                            "dst_iface": {"type": "string"},
                        },
                        "required": ["src", "dst"],
                    },
                },
            },
            ["filename", "devices", "links"],
        ),
        _schema(
            "parse_topology",
            "解析已有 .topo 文件（自动识别 GBK/UTF-8），返回设备与连线清单。",
            {"path": {"type": "string", "description": ".topo 文件绝对路径"}},
            ["path"],
        ),
        _schema(
            "scan_ensp_devices",
            "扫描本机 eNSP Telnet 端口（默认 2000-2100），发现已启动的设备。",
            {
                "port_start": {"type": "integer", "description": "起始端口，默认 2000"},
                "port_end": {"type": "integer", "description": "结束端口，默认 2100"},
            },
            [],
        ),
        _schema(
            "connect_device",
            "与 eNSP 中已启动的设备建立 Telnet 会话（按 console 端口）。",
            {
                "name": {"type": "string", "description": "会话名，建议与设备名一致，如 R1"},
                "port": {"type": "integer", "description": "console 端口号"},
            },
            ["name", "port"],
        ),
        _schema(
            "list_sessions",
            "列出当前全部 Telnet 会话及其状态。",
            {},
            [],
        ),
        _schema(
            "disconnect_device",
            "关闭指定会话。",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _schema(
            "send_command",
            "向设备发送单条 VRP 命令并返回输出（自动进入对应视图上下文）。",
            {
                "device": {"type": "string", "description": "会话名/设备名"},
                "command": {"type": "string", "description": "VRP 命令，如 display ip interface brief"},
            },
            ["device", "command"],
        ),
        _schema(
            "send_commands",
            "向设备批量下发配置命令序列（如 system-view 起的整段配置）。",
            {
                "device": {"type": "string"},
                "commands": {"type": "array", "items": {"type": "string"}},
                "stop_on_error": {"type": "boolean", "description": "出错即停，默认 false"},
            },
            ["device", "commands"],
        ),
        _schema(
            "save_config",
            "保存设备当前配置（quit → save → y）。",
            {"device": {"type": "string"}},
            ["device"],
        ),
        _schema(
            "get_device_info",
            "读取设备 display version 信息（主机名/型号/VRP 版本/运行时长）。",
            {"device": {"type": "string"}},
            ["device"],
        ),
        _schema(
            "get_running_config",
            "读取设备当前运行配置 display current-configuration。",
            {"device": {"type": "string"}},
            ["device"],
        ),
        _schema(
            "generate_experiment_report",
            "生成实验报告：抓取当前会话设备的版本/运行配置/关键 display 校验输出，"
            "连同拓扑信息一起导出 Markdown 与 PDF 文件。",
            {
                "title": {"type": "string", "description": "报告标题，默认‘eNSP 网络实验报告’"},
                "topo_path": {
                    "type": "string",
                    "description": "可选，.topo 文件路径；缺省使用当前活动会话",
                },
                "devices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，指定要采集的设备（会话名）；缺省采集全部会话",
                },
                "export_pdf": {"type": "boolean", "description": "是否同时导出 PDF，默认 true"},
            },
            [],
        ),
    ]


# ---------------------------------------------------------------------- #
# 执行分发
# ---------------------------------------------------------------------- #
def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    default_topo_dir: str = "",
    default_report_dir: str = "",
    save_dialog: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """执行工具，返回 dict（将序列化为 JSON 注入对话）。"""
    try:
        result = _dispatch(name, args, default_topo_dir, default_report_dir, save_dialog)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return _json_safe(result)


def _dispatch(
    name: str,
    args: dict[str, Any],
    topo_dir: str,
    report_dir: str,
    save_dialog: Callable[[str, str], str] | None,
) -> dict[str, Any]:
    if name == "list_supported_models":
        return _list_models()

    if name == "generate_topology":
        spec = topo_service.TopoSpec()
        for d in args.get("devices", []):
            spec.devices.append(
                topo_service.DeviceIn(name=str(d["name"]), model=str(d.get("model", "AR2240")))
            )
        for l in args.get("links", []):
            spec.links.append(
                topo_service.LinkIn(
                    src=str(l["src"]),
                    dst=str(l["dst"]),
                    src_iface=str(l.get("src_iface", "") or ""),
                    dst_iface=str(l.get("dst_iface", "") or ""),
                )
            )
        filename = str(args.get("filename") or "topology.topo")
        if not filename.endswith(".topo"):
            filename += ".topo"
        if topo_dir:
            out = Path(topo_dir) / filename
        else:
            from pathlib import Path as _P

            out = _P.home() / "Documents" / "eNSP-AWA" / "topo" / filename
        return topo_service.generate_topo(spec, out)

    if name == "parse_topology":
        return topo_service.load_topo_summary(str(args["path"]))

    if name == "scan_ensp_devices":
        return device_service.scan_devices(
            int(args.get("port_start", 2000)), int(args.get("port_end", 2100))
        )

    if name == "connect_device":
        return device_service.connect(str(args["name"]), int(args["port"]))

    if name == "list_sessions":
        return device_service.list_sessions()

    if name == "disconnect_device":
        return device_service.disconnect(str(args["name"]))

    if name == "send_command":
        r = device_service.send_command(str(args["device"]), str(args["command"]))
        if r.get("ok") and "output" in r:
            r["output"] = _trunc(r["output"])
        return r

    if name == "send_commands":
        cmds = [str(c) for c in args.get("commands", [])]
        r = device_service.send_commands(
            str(args["device"]), cmds, bool(args.get("stop_on_error", False))
        )
        if r.get("ok"):
            for item in r.get("results", []):
                item["output"] = _trunc(item["output"], 2000)
        return r

    if name == "save_config":
        return device_service.save_config(str(args["device"]))

    if name == "get_device_info":
        return device_service.get_device_info(str(args["device"]))

    if name == "get_running_config":
        r = device_service.get_running_config(str(args["device"]))
        if r.get("ok") and "output" in r:
            r["output"] = _trunc(r["output"])
        return r

    if name == "generate_experiment_report":
        from ..report import report_service

        title = str(args.get("title") or "eNSP 网络实验报告")
        devices = [str(d) for d in args.get("devices", [])] or None
        out_dir = report_dir or None
        md = report_service.collect_and_render(
            title=title, topo_path=args.get("topo_path") or None, devices=devices, out_dir=out_dir
        )
        result: dict[str, Any] = {"ok": True, "markdown": str(md.get("markdown_path", "")), "title": title}
        if args.get("export_pdf", True):
            pdf = report_service.export_pdf(md["markdown_path"])
            result["pdf"] = pdf
        return result

    return {"ok": False, "error": f"未知工具: {name}"}


def _list_models() -> dict[str, Any]:
    from ..ensp.vendor.topo_builder import DEFAULT_SLOT_SPECS

    models = []
    for model, specs in DEFAULT_SLOT_SPECS.items():
        models.append({"model": model, "slots": [{"type": t, "count": c} for t, c in specs]})
    return {
        "ok": True,
        "models": models,
        "iface_index_rules": (
            "接口名→XML全局序号(0-based): AR系列 GE0/0/N→N; USG6000V GE0/0/0→0(管理口禁连线),"
            " GE1/0/N→1+N; S5700/S6700 GE0/0/N→N-1; S3700 Ethernet0/0/N→N-1(1..22),"
            " GE0/0/N→22+N-1; S2700 FE0/0/N→N-1; AC6005 GE0/0/N→N-1; PC/STA/Laptop/Server 恒0。"
            " PC/STA/Laptop/AP 无 CLI，com_port 填 0。"
        ),
        "gbk_rules": (
            ".topo 落盘三要素: XML声明 encoding=\"UNICODE\"(假声明照抄) + CRLF 换行 + GBK 编码无 BOM。"
            " UTF-8 写盘中文设备名必乱码。"
        ),
    }


def _json_safe(obj: Any) -> Any:
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except (TypeError, ValueError):
        return json.loads(json.dumps(obj, ensure_ascii=False, default=str))
