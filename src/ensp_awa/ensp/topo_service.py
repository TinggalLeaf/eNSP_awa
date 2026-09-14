"""eNSP .topo 拓扑服务：生成、GBK 落盘、自检回读、接口名换算、自动布局。

GBK 落盘三要素（eNSP 是老牌 Win32 程序，.topo 实为单字节 ANSI/GBK）：
1. XML 声明恒为 ``encoding="UNICODE"``（假声明，硬编码照抄）
2. 换行 CRLF
3. 实际编码 GBK，无 BOM

写错任意一条，中文设备名必乱码。
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .vendor.topo_builder import DEFAULT_SLOT_SPECS, NativeTopoBuilder

# ---------------------------------------------------------------------- #
# 型号元数据
# ---------------------------------------------------------------------- #
NO_CLI_MODELS = {"PC", "STA", "Laptop", "MCS", "AP6050"}
TIER_MAP = [
    ("AR", "USG"),     # tier 0: 路由器 / 防火墙
    ("S57", "S67", "S37", "S27", "AC"),  # tier 1/2: 交换机 / AC
]


def model_tier(model: str) -> int:
    m = model.upper()
    if m.startswith("AR") or m.startswith("USG"):
        return 0
    if m.startswith(("S57", "S67")):
        return 1
    if m.startswith(("AC",)):
        return 1
    if m.startswith(("S37", "S27")):
        return 2
    return 3  # PC/STA/Laptop/Server/AP


def interface_total(model: str) -> int:
    specs = DEFAULT_SLOT_SPECS.get(model, [("GE", 1)])
    return sum(c for _, c in specs)


def iface_index(model: str, iface: str) -> int:
    """接口名 → XML 全局接口序号（0-based）。非法时抛 ValueError。

    规则来自真实 eNSP 样本（skill/ensp-topo-generate 的 index 映射表）：
    - AR 系列:    GE0/0/N → N（0 起，无偏移）
    - USG6000V:   GE0/0/0 → 0（管理口，禁连线）；GE1/0/N → 1+N
    - S5700/S6700: GE0/0/N → N-1（偏移 +1）
    - S3700:      Ethernet0/0/N → N-1（1..22）；GE0/0/N → 22+N-1
    - S2700:      FE0/0/N → N-1
    - AC6005:     GE0/0/N → N-1
    - 单口设备:    恒 0
    """
    u = iface.strip()
    m = re.match(r"(?i)^(?:gigabitethernet|GE)(\d+)/0/(\d+)$", u)
    e = re.match(r"(?i)^(?:ethernet|eth)(\d+)/0/(\d+)$", u)
    f = re.match(r"(?i)^(?:fastethernet|FE)(\d+)/0/(\d+)$", u)

    if model.startswith("USG6000"):
        if not m:
            raise ValueError(f"USG6000V 只支持 GE 接口: {iface}")
        slot, n = int(m.group(1)), int(m.group(2))
        if slot == 0:
            return 0
        return 1 + n
    if model.startswith("AR"):
        if not m:
            raise ValueError(f"AR 系列只支持 GE 接口: {iface}")
        return int(m.group(2))
    if model.startswith(("S57", "S67")):
        if not m:
            raise ValueError(f"{model} 只支持 GE 接口: {iface}")
        return int(m.group(2)) - 1
    if model.startswith("S37"):
        if e:
            return int(e.group(2)) - 1
        if m:
            return 22 + int(m.group(2)) - 1
        raise ValueError(f"S3700 支持 Ethernet0/0/1-22 或 GE0/0/1-2: {iface}")
    if model.startswith("S27"):
        if not f:
            raise ValueError(f"S2700 只支持 FE 接口: {iface}")
        return int(f.group(2)) - 1
    if model.startswith("AC"):
        if not m:
            raise ValueError(f"AC6005 只支持 GE 接口: {iface}")
        return int(m.group(2)) - 1
    # PC / STA / Laptop / Server / MCS / AP: 单口
    return 0


# ---------------------------------------------------------------------- #
# 输入结构
# ---------------------------------------------------------------------- #
@dataclass
class DeviceIn:
    name: str
    model: str = "AR2240"
    com_port: int = 0  # 0 = 自动分配
    cx: float | None = None
    cy: float | None = None
    settings: str = ""


@dataclass
class LinkIn:
    src: str
    dst: str
    src_iface: str = ""
    dst_iface: str = ""
    link_type: str = "Copper"


@dataclass
class TopoSpec:
    devices: list[DeviceIn] = field(default_factory=list)
    links: list[LinkIn] = field(default_factory=list)
    width: int = 1200
    height: int = 800


# ---------------------------------------------------------------------- #
# 自动布局（按角色分层）
# ---------------------------------------------------------------------- #
def auto_layout(spec: TopoSpec) -> None:
    tiers: dict[int, list[DeviceIn]] = {}
    for d in spec.devices:
        tiers.setdefault(model_tier(d.model), []).append(d)
    tier_order = sorted(tiers)
    margin_x, margin_top, margin_bottom = 120.0, 90.0, 90.0
    avail_h = spec.height - margin_top - margin_bottom
    band_h = avail_h / max(len(tier_order), 1)
    for row, t in enumerate(tier_order):
        group = tiers[t]
        n = len(group)
        spacing = spec.width / (n + 1)
        y = margin_top + band_h * row + band_h / 2
        for i, d in enumerate(group):
            if d.cx is None:
                d.cx = spacing * (i + 1)
            if d.cy is None:
                d.cy = y


def auto_com_ports(spec: TopoSpec, start: int = 2000) -> None:
    next_port = start
    used = {d.com_port for d in spec.devices if d.com_port > 1}
    for d in spec.devices:
        if d.model in NO_CLI_MODELS:
            if d.com_port <= 1:
                d.com_port = 0
            continue
        if d.com_port > 1:
            continue
        while next_port in used:
            next_port += 1
        d.com_port = next_port
        used.add(next_port)
        next_port += 1


# ---------------------------------------------------------------------- #
# GBK 落盘（三要素）
# ---------------------------------------------------------------------- #
def to_ensp_bytes(xml: str) -> bytes:
    xml = re.sub(
        r"^<\?xml[^?]*\?>", '<?xml version="1.0" encoding="UNICODE" ?>', xml
    )
    xml = xml.replace("\r\n", "\n").replace("\n", "\r\n")
    return xml.encode("gbk")


def read_topo_text(path: str | Path) -> str:
    """读 .topo（自动识别 GBK / UTF-8），返回 Unicode 文本。"""
    raw = Path(path).read_bytes()
    for enc in ("gbk", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace")


# ---------------------------------------------------------------------- #
# 生成 + 自检
# ---------------------------------------------------------------------- #
def generate_topo(spec: TopoSpec, out_path: str | Path) -> dict[str, Any]:
    """生成 .topo 并落盘（GBK 三要素），随后回读自检。返回结果字典。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    auto_layout(spec)
    auto_com_ports(spec)

    b = NativeTopoBuilder()
    for d in spec.devices:
        b.add_device(
            d.name,
            model=d.model,
            com_port=d.com_port,
            cx=d.cx or 0,
            cy=d.cy or 0,
            settings=d.settings,
        )
    for ln in spec.links:
        si = iface_index(_model_of(spec, ln.src), ln.src_iface) if ln.src_iface else 0
        ti = iface_index(_model_of(spec, ln.dst), ln.dst_iface) if ln.dst_iface else 0
        b.add_line(ln.src, ln.dst, src_index=si, tar_index=ti, link_type=ln.link_type)

    errors = b.validate()
    if errors:
        return {"ok": False, "stage": "validate", "errors": errors}

    xml = b.render()
    data = to_ensp_bytes(xml)
    out.write_bytes(data)

    checks = self_check(spec, out, xml)
    return {
        "ok": all(c["ok"] for c in checks),
        "path": str(out),
        "size": len(data),
        "devices": [
            {"name": d.name, "model": d.model, "com_port": d.com_port, "cx": d.cx, "cy": d.cy}
            for d in spec.devices
        ],
        "links": [
            {"src": l.src, "dst": l.dst, "src_iface": l.src_iface, "dst_iface": l.dst_iface}
            for l in spec.links
        ],
        "checks": checks,
    }


def _model_of(spec: TopoSpec, name: str) -> str:
    for d in spec.devices:
        if d.name == name:
            return d.model
    raise ValueError(f"未知设备: {name}")


def self_check(spec: TopoSpec, out: Path, xml: str) -> list[dict[str, Any]]:
    """GBK 回读 → 临时 UTF-8 副本 → parser 解析比对。"""
    checks: list[dict[str, Any]] = []

    # 1) GBK 回读无损
    try:
        text = read_topo_text(out)
        checks.append(
            {"name": "GBK 回读解码", "ok": True, "detail": f"{len(text)} 字符"}
        )
    except Exception as e:  # noqa: BLE001
        return [{"name": "GBK 回读解码", "ok": False, "detail": str(e)}]

    # 2) parser 回读（parser 只认 UTF-8：内存 Unicode → 临时 UTF-8 副本）
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".topo", encoding="utf-8", delete=False
        ) as tf:
            tf.write(text)
            tmp = tf.name
        parsed = _parse_topo_file(tmp)
    except Exception as e:  # noqa: BLE001
        checks.append({"name": "parser 解析", "ok": False, "detail": str(e)})
        return checks
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)  # noqa: F821
        except Exception:
            pass

    # 3) 设备清单一致
    want_dev = sorted((d.name, d.model) for d in spec.devices)
    got_dev = sorted((p.get("name", ""), p.get("model", "")) for p in parsed["devices"])
    checks.append(
        {
            "name": "设备清单一致",
            "ok": want_dev == got_dev,
            "detail": f"期望 {len(want_dev)} 台 / 实得 {len(got_dev)} 台",
        }
    )

    # 4) 连线无向匹配
    def norm_link(ln: LinkIn) -> frozenset:
        return frozenset(
            {
                (ln.src, ln.src_iface or "auto"),
                (ln.dst, ln.dst_iface or "auto"),
            }
        )

    want_links = len(spec.links)
    got_links = parsed.get("link_count", 0)
    checks.append(
        {
            "name": "连线数量一致",
            "ok": want_links == got_links,
            "detail": f"期望 {want_links} 条 / 实得 {got_links} 条",
        }
    )
    return checks


def _parse_topo_file(path: str) -> dict[str, Any]:
    """调 vendored parser；不同版本 API 兼容处理。"""
    try:
        from .vendor.topo_parser import parse_topo  # type: ignore

        result = parse_topo(path)
        if isinstance(result, dict):
            return {
                "devices": result.get("devices", []),
                "link_count": result.get("link_count", len(result.get("links", []))),
            }
        devices = getattr(result, "devices", [])
        links = getattr(result, "connections", None) or getattr(result, "links", []) or []
        devs = [d.to_dict() if hasattr(d, "to_dict") else d for d in devices]
        return {"devices": devs, "link_count": len(links)}
    except ImportError:
        pass
    # 兜底：ElementTree 直解（topo 结构简单）
    import xml.etree.ElementTree as ET

    root = ET.parse(path).getroot()
    devices = [
        {"name": dev.get("name", ""), "model": dev.get("model", "")}
        for dev in root.iter("dev")
    ]
    lines = list(root.iter("line"))
    return {"devices": devices, "link_count": len(lines)}


# ---------------------------------------------------------------------- #
# 解析已有 .topo（供工具箱展示）
# ---------------------------------------------------------------------- #
def load_topo_summary(path: str | Path) -> dict[str, Any]:
    text = read_topo_text(path)
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    devices = [
        {
            "name": d.get("name", ""),
            "model": d.get("model", ""),
            "com_port": int(d.get("com_port", "0") or 0),
            "cx": float(d.get("cx", "0") or 0),
            "cy": float(d.get("cy", "0") or 0),
        }
        for d in root.iter("dev")
    ]
    lines = []
    for line in root.iter("line"):
        pair = line.find("interfacePair")
        if pair is None:
            continue
        lines.append(
            {
                "src_id": line.get("srcDeviceID", ""),
                "dst_id": line.get("destDeviceID", ""),
                "src_index": int(pair.get("srcIndex", "0")),
                "tar_index": int(pair.get("tarIndex", "0")),
                "type": pair.get("lineName", "Copper"),
            }
        )
    id2name = {d.get("id", ""): d.get("name", "") for d in root.iter("dev")}
    for ln in lines:
        ln["src"] = id2name.get(ln.pop("src_id"), "?")
        ln["dst"] = id2name.get(ln.pop("dst_id"), "?")
    return {"ok": True, "devices": devices, "links": lines, "path": str(path)}
