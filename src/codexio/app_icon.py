from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ICON_BG = QColor("#FFFFFF")
ICON_MARK = QColor("#2563EB")
ICON_MARK_LIGHT = QColor("#2D6CE8")
BUNDLED_ICON = "app.ico"
MASTER_PNG = "app.png"
MASTER_SVG = "app.svg"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
MARK_FILL = 0.90
MARK_SCALE = 1.20


def _icon_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parent / "icons"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass) / "codexio" / "icons")
        roots.append(Path(meipass) / "icons")
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent / "codexio" / "icons")
        roots.append(Path(sys.executable).resolve().parent / "icons")
    return roots


def bundled_icon_path() -> Path:
    for root in _icon_roots():
        candidate = root / BUNDLED_ICON
        if candidate.is_file():
            return candidate
    return _icon_roots()[0] / BUNDLED_ICON


def master_png_path() -> Path:
    for root in _icon_roots():
        candidate = root / MASTER_PNG
        if candidate.is_file():
            return candidate
    return _icon_roots()[0] / MASTER_PNG


def master_svg_path() -> Path:
    for root in _icon_roots():
        candidate = root / MASTER_SVG
        if candidate.is_file():
            return candidate
    return _icon_roots()[0] / MASTER_SVG


def load_app_icon() -> QIcon:
    # Qt's SVG icon engine renders the requested size, including Retina Dock
    # sizes. Starting with an ICO would cap the native icon at its raster size.
    vector = master_svg_path()
    if vector.is_file() and QSvgRenderer(str(vector)).isValid():
        return QIcon(str(vector))
    path = bundled_icon_path()
    if path.is_file():
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return QIcon(render_app_pixmap(256))


def render_app_pixmap(size: int) -> QPixmap:
    return QPixmap.fromImage(render_app_image(size))


def render_app_image(size: int) -> QImage:
    """Rasterize the vector at the final pixel size; also usable by the packager."""
    renderer = QSvgRenderer(str(master_svg_path()))
    if renderer.isValid():
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        return image
    master = master_png_path()
    if master.is_file():
        source = QImage(str(master))
        if not source.isNull():
            return source.scaled(
                size,
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_app_mark(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def paint_app_mark(painter: QPainter, rect: QRectF) -> None:
    """Keep the Cobalt X identity even if the bundled raster is unavailable."""
    side = min(rect.width(), rect.height())
    painter.save()
    painter.translate(rect.center().x() - side / 2, rect.center().y() - side / 2)
    painter.scale(side / 512, side / 512)
    background = QPainterPath()
    background.addRoundedRect(QRectF(0, 0, 512, 512), 112, 112)
    painter.fillPath(background, QColor("#FFFFFF"))
    painter.translate(256, 256)
    painter.scale(MARK_SCALE, MARK_SCALE)
    painter.translate(-256, -256)
    pen = QPen(QColor("#333744"), 38)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    for direction in (-1, 1):
        path = QPainterPath()
        path.moveTo(256 + direction * 60, 136)
        path.lineTo(256 + direction * 136, 256)
        path.lineTo(256 + direction * 60, 376)
        painter.drawPath(path)
    cobalt = QLinearGradient(224, 224, 288, 288)
    for stop, color in ((0, "#2563EB"), (.38, "#2D6CE8"), (.7, "#274ED4"), (1, "#30369E")):
        cobalt.setColorAt(stop, QColor(color))
    pen = QPen(cobalt, 14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(224, 224, 288, 288)
    painter.drawLine(224, 288, 288, 224)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawEllipse(QRectF(249.5, 249.5, 13, 13))
    painter.restore()


def write_app_ico(path: Optional[Path] = None) -> Path:
    if QApplication.instance() is None:
        QApplication([])
    target = path or bundled_icon_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for size in ICON_SIZES:
        image = render_app_pixmap(size).toImage()
        blob = QByteArray()
        device = QBuffer(blob)
        device.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(device, "PNG")
        device.close()
        images.append(bytes(blob))
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    entries = b""
    offset = 6 + 16 * count
    payload = b""
    for size, png in zip(ICON_SIZES, images):
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    target.write_bytes(header + entries + payload)
    return target
