"""生成 WOFO Login 自有 Wi-Fi 重连图标与 Windows 多分辨率图标。

运行：.venv/Scripts/python.exe scripts/generate_icon.py
只依赖项目已有的 Pillow；SVG 与位图共享同一套几何坐标。
"""

from pathlib import Path
import math

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "ui" / "assets"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
MINT = "#69e4c0"
BLUE = "#7ec4ff"
WHITE = "#dbfff3"


def arc_points(center, radius, start, end):
    return [
        (center[0] + radius * math.cos(math.radians(degree)),
         center[1] + radius * math.sin(math.radians(degree)))
        for degree in range(start, end + 1)
    ]


def draw_icon(size=512):
    # 同一几何形状超采样，兼顾 16px 标题栏与高 DPI 任务栏。
    scale = 4
    canvas = 256 * scale
    image = Image.new("RGBA", (canvas, canvas))
    draw = ImageDraw.Draw(image)

    def box(values):
        return tuple(round(value * scale) for value in values)

    def line(points, fill, width):
        pixels = [(round(x * scale), round(y * scale)) for x, y in points]
        draw.line(pixels, fill=fill, width=round(width * scale), joint="curve")
        for x, y in (points[0], points[-1]):
            radius = width / 2
            draw.ellipse(box((x - radius, y - radius, x + radius, y + radius)), fill=fill)

    draw.rounded_rectangle(box((8, 8, 248, 248)), radius=56 * scale, fill="#111d27")
    draw.rounded_rectangle(box((9, 9, 247, 247)), radius=55 * scale,
                           outline="#304347", width=2 * scale)

    # 开放的环形箭头表示断线重连，中心为 Wi-Fi 信号。
    line(arc_points((128, 128), 88, -40, 270), MINT, 14)
    draw.polygon([box(point) for point in ((152, 40), (125, 24), (125, 56))], fill=BLUE)
    for radius in (52, 30):
        line(arc_points((128, 158), radius, 220, 320), WHITE, 13)
    draw.ellipse(box((119, 152, 137, 170)), fill=MINT)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-labelledby="title desc">
  <title id="title">WOFO Login</title>
  <desc id="desc">深色圆角底上的 Wi-Fi 信号与开放式重连箭头，代表校园网断线重连。</desc>
  <rect x="8" y="8" width="240" height="240" rx="56" fill="#111d27"/>
  <rect x="9" y="9" width="238" height="238" rx="55" fill="none" stroke="#304347" stroke-width="2"/>
  <path d="M195.41 71.43A88 88 0 1 1 128 40" fill="none" stroke="#69e4c0" stroke-width="14" stroke-linecap="round"/>
  <path d="M152 40 125 24 125 56Z" fill="#7ec4ff"/>
  <g fill="none" stroke="#dbfff3" stroke-width="13" stroke-linecap="round">
    <path d="M88.17 124.58A52 52 0 0 1 167.83 124.58"/>
    <path d="M105.02 138.72A30 30 0 0 1 150.98 138.72"/>
  </g>
  <circle cx="128" cy="161" r="9" fill="#69e4c0"/>
</svg>
'''
    (ASSETS / "wofo-login.svg").write_text(svg, encoding="utf-8")
    image = draw_icon()
    image.save(ASSETS / "wofo-login.png")
    image.save(ASSETS / "wofo-login.ico", sizes=[(size, size) for size in SIZES])
    print("Generated SVG, 512px PNG and ICO (" + ", ".join(map(str, SIZES)) + "px)")


if __name__ == "__main__":
    main()
