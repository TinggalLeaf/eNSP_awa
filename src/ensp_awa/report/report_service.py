"""实验报告生成：拓扑 + 设备配置 + 关键 display 校验输出 → Markdown / PDF。

PDF 双通道：
1. 首选 Edge/Chrome headless `--print-to-pdf`（Windows 必带 Edge，渲染质量高、中文无虞）
2. 兜底 reportlab + 内置 CID 中文字体 STSong-Light（零外部依赖）
"""

from __future__ import annotations

import datetime as dt
import html
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import markdown
import markdown.extensions.fenced_code  # noqa: F401 显式导入，便于 Nuitka 跟踪
import markdown.extensions.nl2br  # noqa: F401
import markdown.extensions.tables  # noqa: F401
import markdown.extensions.toc  # noqa: F401

from ..ensp import device_service, topo_service

KEY_DISPLAY_COMMANDS = [
    "display version",
    "display ip interface brief",
    "display ip routing-table",
    "display ospf peer brief",
    "display vlan",
]

REPORT_CSS = """
@page { size: A4; margin: 18mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
       color: #1a2233; line-height: 1.65; max-width: 880px; margin: 0 auto; padding: 24px; }
h1 { font-size: 26px; border-bottom: 3px solid #2563eb; padding-bottom: 8px; }
h2 { font-size: 20px; margin-top: 32px; color: #1e40af; border-left: 5px solid #2563eb; padding-left: 10px; }
h3 { font-size: 16px; margin-top: 22px; color: #334155; }
h4 { font-size: 14px; color: #475569; }
code, pre { font-family: "JetBrains Mono", Consolas, "Courier New", monospace; font-size: 12px; }
pre { background: #0f172a; color: #e2e8f0; padding: 12px 14px; border-radius: 8px;
      overflow-x: auto; white-space: pre-wrap; word-break: break-all; }
code { background: #eef2ff; color: #1e40af; padding: 1px 5px; border-radius: 4px; }
pre code { background: none; color: inherit; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
th, td { border: 1px solid #cbd5e1; padding: 6px 10px; text-align: left; }
th { background: #eff6ff; }
tr:nth-child(even) { background: #f8fafc; }
.meta { color: #64748b; font-size: 13px; margin-bottom: 24px; }
.badge { display: inline-block; background: #2563eb; color: #fff; border-radius: 4px;
         padding: 1px 8px; font-size: 12px; margin-right: 6px; }
"""


def default_report_dir() -> Path:
    d = Path.home() / "Documents" / "eNSP-AWA" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------- #
# 采集 + 渲染
# ---------------------------------------------------------------------- #
def collect_and_render(
    title: str,
    topo_path: str | None = None,
    devices: list[str] | None = None,
    out_dir: str | None = None,
    author: str = "",
    extra: str = "",
) -> dict[str, Any]:
    """采集证据并渲染 Markdown 报告。返回 {"markdown_path": ...}。"""
    sessions = device_service.list_sessions().get("sessions", [])
    if devices:
        wanted = {d.lower() for d in devices}
        sessions = [s for s in sessions if str(s.get("name", "")).lower() in wanted]
    if not sessions:
        # 无会话时也允许仅拓扑报告
        sessions = []

    collected: list[dict[str, Any]] = []
    for s in sessions:
        name = str(s.get("name", ""))
        entry: dict[str, Any] = {"name": name, "info": None, "config": "", "displays": []}
        info = device_service.get_device_info(name)
        if info.get("ok"):
            entry["info"] = info.get("info")
        cfg = device_service.get_running_config(name)
        if cfg.get("ok"):
            entry["config"] = cfg.get("output", "")
        for cmd in KEY_DISPLAY_COMMANDS:
            if cmd == "display version":
                continue  # info 已含
            r = device_service.send_command(name, cmd, timeout=20.0)
            entry["displays"].append(
                {"command": cmd, "ok": r.get("ok", False), "output": r.get("output", r.get("error", ""))}
            )
        collected.append(entry)

    topo_summary = None
    if topo_path:
        try:
            topo_summary = topo_service.load_topo_summary(topo_path)
        except Exception as e:  # noqa: BLE001
            topo_summary = {"error": str(e)}

    out = Path(out_dir) if out_dir else default_report_dir()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = _safe_filename(title) or "experiment_report"
    md_path = out / f"{safe_title}_{stamp}.md"

    md_text = render_markdown(
        title=title,
        author=author,
        extra=extra,
        topo=topo_summary,
        devices=collected,
    )
    md_path.write_text(md_text, encoding="utf-8")
    return {"markdown_path": str(md_path), "devices": len(collected)}


def render_markdown(
    title: str,
    author: str = "",
    extra: str = "",
    topo: dict[str, Any] | None = None,
    devices: list[dict[str, Any]] = None,
) -> str:
    devices = devices or []
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [f"# {title}", ""]
    meta = [f"生成时间：{now}"]
    if author:
        meta.append(f"作者：{author}")
    if extra:
        meta.append(extra)
    lines.append(" · ".join(meta))
    lines.append("")

    lines.append("## 一、拓扑信息")
    lines.append("")
    if topo and not topo.get("error"):
        lines.append("### 设备清单")
        lines.append("")
        lines.append("| 设备名 | 型号 | Console 端口 |")
        lines.append("|---|---|---|")
        for d in topo.get("devices", []):
            lines.append(f"| {d.get('name','')} | {d.get('model','')} | {d.get('com_port',0)} |")
        lines.append("")
        lines.append("### 连线清单")
        lines.append("")
        lines.append("| 源设备 | 源接口序号 | 目标设备 | 目标接口序号 | 介质 |")
        lines.append("|---|---|---|---|---|")
        for l in topo.get("links", []):
            lines.append(
                f"| {l.get('src','')} | {l.get('src_index',0)} | {l.get('dst','')} "
                f"| {l.get('tar_index',0)} | {l.get('type','Copper')} |"
            )
    elif topo and topo.get("error"):
        lines.append(f"> 拓扑解析失败：{topo['error']}")
    else:
        lines.append("> 未提供拓扑文件。")
    lines.append("")

    lines.append("## 二、设备配置")
    lines.append("")
    for entry in devices:
        head = entry["name"]
        if entry.get("info"):
            info = entry["info"]
            head += f"（{info.get('model','')} / VRP {info.get('version','')}）"
        lines.append(f"### {head}")
        lines.append("")
        if entry.get("config"):
            lines.append("```text")
            lines.append(entry["config"].rstrip())
            lines.append("```")
        else:
            lines.append("> 未采集到运行配置。")
        lines.append("")

    lines.append("## 三、关键 display 校验输出")
    lines.append("")
    for entry in devices:
        lines.append(f"### {entry['name']}")
        lines.append("")
        for d in entry.get("displays", []):
            status = "✅" if d["ok"] else "⚠️"
            lines.append(f"#### {status} `{d['command']}`")
            lines.append("")
            lines.append("```text")
            lines.append((d["output"] or "").rstrip()[:8000])
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# PDF 导出
# ---------------------------------------------------------------------- #
def export_pdf(md_path: str, out_path: str | None = None) -> str:
    md_path = str(md_path)
    md_text = Path(md_path).read_text(encoding="utf-8")
    body = markdown.markdown(
        md_text, extensions=["tables", "fenced_code", "toc", "nl2br"]
    )
    doc = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{REPORT_CSS}</style></head><body>{body}</body></html>"
    )
    html_path = Path(md_path).with_suffix(".html")
    html_path.write_text(doc, encoding="utf-8")

    pdf_path = out_path or str(Path(md_path).with_suffix(".pdf"))
    exe = _find_browser()
    if exe:
        try:
            _print_with_browser(exe, str(html_path), pdf_path)
            return pdf_path
        except Exception:  # noqa: BLE001 — 兜底 reportlab
            pass
    _render_pdf_reportlab(md_text, pdf_path)
    return pdf_path


def _find_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return shutil.which("msedge") or shutil.which("chrome")


def _print_with_browser(exe: str, html_path: str, pdf_path: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            exe,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f'--user-data-dir={tmp}',
            f'--print-to-pdf={pdf_path}',
            Path(html_path).as_uri(),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def _render_pdf_reportlab(md_text: str, pdf_path: str) -> None:
    """兜底：轻量 Markdown → PDF（标题/代码块/表格按等宽排版）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    font = "STSong-Light"

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
    )
    h1 = ParagraphStyle("h1", fontName=font, fontSize=16, leading=22, spaceAfter=8)
    h2 = ParagraphStyle("h2", fontName=font, fontSize=13, leading=18, spaceBefore=10, spaceAfter=6)
    h3 = ParagraphStyle("h3", fontName=font, fontSize=11.5, leading=16, spaceBefore=8, spaceAfter=4)
    body_st = ParagraphStyle("body", fontName=font, fontSize=9.5, leading=14)
    code_st = ParagraphStyle(
        "code", fontName=font, fontSize=8, leading=10.5, backColor="#f1f5f9",
        borderPadding=4, leftIndent=6,
    )

    story: list[Any] = []
    in_code = False
    for raw in md_text.splitlines():
        line = raw.rstrip("\n")
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            story.append(Preformatted(line if line else " ", code_st))
            continue
        s = line.strip()
        if not s:
            story.append(Spacer(1, 3))
        elif s.startswith("#### "):
            story.append(Paragraph(html.escape(s[5:]), h3))
        elif s.startswith("### "):
            story.append(Paragraph(html.escape(s[4:]), h3))
        elif s.startswith("## "):
            story.append(Paragraph(html.escape(s[3:]), h2))
        elif s.startswith("# "):
            story.append(Paragraph(html.escape(s[2:]), h1))
        elif s.startswith("|"):
            story.append(Preformatted(s, code_st))
        elif s.startswith(">"):
            story.append(Paragraph(html.escape(s.lstrip("> ")), body_st))
        else:
            story.append(Paragraph(html.escape(s), body_st))
    doc.build(story)


def _safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", " "):
            keep.append(ch)
    return "".join(keep).strip().replace(" ", "_")[:60]
