"""自绘应用图标：圆角方牌 + 青紫渐变 + 网络节点拓扑。

生成 assets/icon.png（512）与 assets/icon.ico（多尺寸）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent
SIZE = 512


def lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def rounded_gradient(size: int, radius: int, c1: tuple, c2: tuple) -> Image.Image:
    """对角渐变圆角底板（带轻微噪点光泽感）。"""
    base = Image.new("RGB", (size, size))
    px = base.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = tuple(lerp(c1[i], c2[i], t) for i in range(3))
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (size, size))
    out.paste(base, (0, 0), mask)
    return out


def draw_icon() -> Image.Image:
    img = rounded_gradient(SIZE, 108, (10, 24, 48), (26, 16, 60))
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)

    # 网格线（科技感）
    grid = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = SIZE // 8
    for i in range(0, SIZE + 1, step):
        gd.line([(i, 0), (i, SIZE)], fill=(120, 170, 255, 16), width=2)
        gd.line([(0, i), (SIZE, i)], fill=(120, 170, 255, 16), width=2)
    img.alpha_composite(grid)

    # 连接线（发光层 + 实线层）
    cyan = (34, 211, 238)
    violet = (139, 92, 246)
    green = (52, 211, 153)
    nodes = {
        "a": (SIZE * 0.30, SIZE * 0.33),
        "b": (SIZE * 0.70, SIZE * 0.30),
        "c": (SIZE * 0.50, SIZE * 0.68),
        "d": (SIZE * 0.24, SIZE * 0.72),
    }
    edges = [("a", "b"), ("a", "c"), ("b", "c"), ("c", "d")]
    for u, v in edges:
        g.line([nodes[u], nodes[v]], fill=cyan + (110,), width=14)
    glow = glow.filter(ImageFilter.GaussianBlur(12))
    img.alpha_composite(glow)

    d = ImageDraw.Draw(img)
    for u, v in edges:
        d.line([nodes[u], nodes[v]], fill=(190, 235, 255, 235), width=6)

    # 节点（外发光 + 内核）
    ng = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    nd = ImageDraw.Draw(ng)
    r = SIZE * 0.055
    for key, (x, y) in nodes.items():
        col = green if key == "c" else (violet if key == "b" else cyan)
        nd.ellipse([x - r * 1.7, y - r * 1.7, x + r * 1.7, y + r * 1.7], fill=col + (90,))
    ng = ng.filter(ImageFilter.GaussianBlur(10))
    img.alpha_composite(ng)
    d = ImageDraw.Draw(img)
    for key, (x, y) in nodes.items():
        col = green if key == "c" else (violet if key == "b" else cyan)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,))
        d.ellipse([x - r * 0.45, y - r * 0.45, x + r * 0.45, y + r * 0.45], fill=(255, 255, 255, 230))

    # 高光描边
    d.rounded_rectangle([6, 6, SIZE - 7, SIZE - 7], radius=102, outline=(160, 220, 255, 120), width=4)
    return img


def main() -> None:
    img = draw_icon()
    png = ASSETS / "icon.png"
    ico = ASSETS / "icon.ico"
    img.save(png)
    img.resize((256, 256), Image.LANCZOS).save(
        ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    print("icon.png:", png, img.size)
    print("icon.ico:", ico)


if __name__ == "__main__":
    main()
