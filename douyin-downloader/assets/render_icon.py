"""Render the editable application icon using the existing desktop dependencies.

Run from any directory: .venv/Scripts/python.exe assets/render_icon.py
The SVG is the source of truth. PNG corners retain real transparency; ICO embeds
separate, supersampled frames for the Windows title bar, taskbar and shortcuts.
"""
import io
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ASSETS = Path(__file__).resolve().parent
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def render_svg(svg: bytes, size: int) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        raise ValueError("Invalid application icon SVG")
    scale = 4 if size < 256 else 2
    canvas = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, canvas.width(), canvas.height()))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.ReadWrite)
    if not canvas.save(buffer, "PNG"):
        raise RuntimeError("Could not encode the application icon")
    with Image.open(io.BytesIO(bytes(buffer.data()))) as rendered:
        return rendered.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)


def make_preview(svg: bytes) -> None:
    board = Image.new("RGBA", (1200, 820), "#F4F4F5")
    draw = ImageDraw.Draw(board)
    draw.text((64, 46), "APP ICON / V2", font=font(15, True), fill="#52525B")
    draw.text((64, 82), "视频下载器", font=font(42, True), fill="#18181B")
    draw.text((66, 154), "深海蓝 · 播放符号 × 下载箭头", font=font(18), fill="#71717A")
    board.alpha_composite(render_svg(svg, 464), (55, 220))

    draw.rounded_rectangle((576, 222, 1136, 448), radius=24, fill="#FFFFFF")
    draw.text((608, 246), "浅色背景 / 实际像素尺寸", font=font(14, True), fill="#71717A")
    draw.rounded_rectangle((576, 472, 1136, 698), radius=24, fill="#18181B")
    draw.text((608, 496), "深色背景 / 实际像素尺寸", font=font(14, True), fill="#A1A1AA")
    for base_y, caption in ((335, "#71717A"), (585, "#A1A1AA")):
        for size, x in ((16, 624), (24, 706), (32, 792), (48, 880), (64, 996)):
            board.alpha_composite(render_svg(svg, size), (x - size // 2, base_y - size // 2))
            draw.text((x, base_y + 48), f"{size}px", font=font(13), fill=caption, anchor="mt")
    draw.line((64, 746, 1136, 746), fill="#D4D4D8", width=1)
    draw.text((64, 770), "SVG / PNG / Windows ICO / Android 自适应图标", font=font(15), fill="#52525B")
    draw.text((1136, 770), "#1A4A8E + #FAFAFA", font=font(15), fill="#52525B", anchor="rt")
    board.convert("RGB").save(ASSETS / "app-v2-preview.png", optimize=True)


def make_android_resources(svg: bytes) -> None:
    """Keep the same master artwork for raster, adaptive and themed icons."""
    resources = ASSETS.parents[1] / "android/app/src/main/res"
    if not resources.is_dir():
        return
    android_ns = "http://schemas.android.com/apk/res/android"
    ET.register_namespace("android", android_ns)

    def attr(name: str) -> str:
        return f"{{{android_ns}}}{name}"

    def save_xml(element: ET.Element, relative_path: str) -> None:
        target = resources / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(element, space="    ")
        ET.ElementTree(element).write(target, encoding="utf-8", xml_declaration=True)

    foreground = ET.Element("vector", {
        attr("width"): "108dp", attr("height"): "108dp",
        attr("viewportWidth"): "108", attr("viewportHeight"): "108",
    })
    # Keep the complete mark within Android's central 66dp safe circle.
    group = ET.SubElement(foreground, "group", {
        attr("scaleX"): "0.0859375", attr("scaleY"): "0.0859375",
        attr("translateX"): "10", attr("translateY"): "10",
    })
    for path in ET.fromstring(svg).findall("{http://www.w3.org/2000/svg}path"):
        attributes = {attr("pathData"): path.attrib["d"]}
        for svg_name, android_name in (
            ("fill", "fillColor"), ("stroke", "strokeColor"),
            ("stroke-width", "strokeWidth"), ("stroke-linecap", "strokeLineCap"),
            ("stroke-linejoin", "strokeLineJoin"),
        ):
            if svg_name in path.attrib:
                value = path.attrib[svg_name]
                attributes[attr(android_name)] = "#00000000" if value == "none" else value
        if path.attrib.get("fill-rule") == "evenodd":
            attributes[attr("fillType")] = "evenOdd"
        ET.SubElement(group, "path", attributes)
    save_xml(foreground, "drawable/ic_launcher_v2_foreground.xml")

    colors = ET.Element("resources")
    ET.SubElement(colors, "color", {"name": "launcher_v2_background"}).text = "#1A4A8E"
    save_xml(colors, "values/icon_v2_colors.xml")
    for version in (26, 33):
        adaptive = ET.Element("adaptive-icon")
        ET.SubElement(adaptive, "background", {attr("drawable"): "@color/launcher_v2_background"})
        ET.SubElement(adaptive, "foreground", {attr("drawable"): "@drawable/ic_launcher_v2_foreground"})
        if version >= 33:
            ET.SubElement(adaptive, "monochrome", {attr("drawable"): "@drawable/ic_launcher_v2_foreground"})
        for name in ("ic_launcher_v2", "ic_launcher_v2_round"):
            save_xml(adaptive, f"mipmap-anydpi-v{version}/{name}.xml")

    round_master = ET.fromstring(svg)
    tile = round_master.find("{http://www.w3.org/2000/svg}rect")
    round_master.remove(tile)
    ET.SubElement(round_master, "{http://www.w3.org/2000/svg}circle", {
        "cx": "512", "cy": "512", "r": "480", "fill": "#1A4A8E",
    })
    # The circle is a background: place it before the foreground artwork.
    round_master.insert(0, round_master[-1])
    del round_master[-1]
    round_svg = ET.tostring(round_master, encoding="utf-8")
    for density, size in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
        target = resources / f"mipmap-{density}"
        target.mkdir(parents=True, exist_ok=True)
        render_svg(svg, size).save(target / "ic_launcher_v2.png", optimize=True)
        render_svg(round_svg, size).save(target / "ic_launcher_v2_round.png", optimize=True)


def main() -> None:
    svg = (ASSETS / "app-v2.svg").read_bytes()
    icon = render_svg(svg, 1024)
    icon.save(ASSETS / "app-v2.png", optimize=True)
    frames = [render_svg(svg, size) for size in SIZES]
    icon.save(ASSETS / "app-v2.ico", format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames)
    make_preview(svg)
    make_android_resources(svg)
    print("Rendered desktop PNG/ICO/preview and Android launcher resources from app-v2.svg")


if __name__ == "__main__":
    main()
