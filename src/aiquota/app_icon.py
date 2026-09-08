from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication

ICON_BG = QColor("#171C24")
ICON_MARK = QColor("#3DDC97")
ICON_MARK_LIGHT = QColor("#8AF0C2")
BUNDLED_ICON = "app.ico"
MASTER_PNG = "app.png"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
MARK_FILL = 0.90


def _icon_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parent / "icons"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass) / "aiquota" / "icons")
        roots.append(Path(meipass) / "icons")
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent / "aiquota" / "icons")
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


def load_app_icon() -> QIcon:
    path = bundled_icon_path()
    if path.is_file():
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return QIcon(render_app_pixmap(256))


def render_app_pixmap(size: int) -> QPixmap:
    master = master_png_path()
    if master.is_file():
        source = QPixmap(str(master))
        if not source.isNull():
            return source.scaled(
                size,
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_app_mark(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def knockout_dark_background(image: QImage) -> QImage:
    source = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = source.width(), source.height()
    for y in range(height):
        for x in range(width):
            color = QColor.fromRgba(source.pixel(x, y))
            red, green, blue = color.red(), color.green(), color.blue()
            if green >= red + 18 and green >= 48:
                continue
            source.setPixelColor(x, y, QColor(0, 0, 0, 0))
    return source


def fit_mark_on_transparent_canvas(image: QImage, fill: float = MARK_FILL) -> QImage:
    bounds = _square_opaque_bounds(image)
    if bounds.isEmpty():
        return image
    side = max(image.width(), image.height(), 256)
    canvas = QImage(side, side, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    target = int(round(side * fill))
    scaled = QPixmap.fromImage(image.copy(bounds)).scaled(
        target,
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap((side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


def _square_opaque_bounds(image: QImage) -> QRect:
    bounds = _opaque_bounds(image)
    if bounds.isEmpty():
        return bounds
    side = max(bounds.width(), bounds.height())
    cx = bounds.center().x()
    cy = bounds.center().y()
    left = max(0, min(image.width() - side, cx - side // 2))
    top = max(0, min(image.height() - side, cy - side // 2))
    return QRect(left, top, side, side)


def _opaque_bounds(image: QImage) -> QRect:
    width, height = image.width(), image.height()
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            if QColor.fromRgba(image.pixel(x, y)).alpha() < 16:
                continue
            left = min(left, x)
            right = max(right, x)
            top = min(top, y)
            bottom = max(bottom, y)
    if right < left or bottom < top:
        return QRect()
    return QRect(left, top, right - left + 1, bottom - top + 1)


def write_transparent_master(source: Path, target: Optional[Path] = None) -> Path:
    if QApplication.instance() is None:
        QApplication([])
    destination = target or master_png_path()
    image = QImage(str(source))
    if image.isNull():
        raise RuntimeError("icon source image is empty: %s" % source)
    prepared = fit_mark_on_transparent_canvas(knockout_dark_background(image))
    destination.parent.mkdir(parents=True, exist_ok=True)
    prepared.save(str(destination), "PNG")
    return destination


def paint_app_mark(painter: QPainter, rect: QRectF) -> None:
    side = min(rect.width(), rect.height())
    box = QRectF(
        rect.center().x() - side / 2.0,
        rect.center().y() - side / 2.0,
        side,
        side,
    )
    cx = box.center().x()
    cy = box.center().y()
    painter.save()
    painter.translate(cx, cy)
    stroke = max(2.0, side * 0.142)
    color = ICON_MARK if side >= 24 else ICON_MARK_LIGHT
    pen = QPen(color, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index in range(6):
        painter.save()
        painter.rotate(index * 60)
        painter.drawPath(_petal_path(side))
        painter.restore()
    painter.restore()


def _petal_path(side: float) -> QPainterPath:
    scale = side * 0.76
    path = QPainterPath()
    path.moveTo(0.18 * scale, -0.62 * scale)
    path.cubicTo(
        0.52 * scale,
        -0.58 * scale,
        0.72 * scale,
        -0.28 * scale,
        0.58 * scale,
        0.02 * scale,
    )
    path.cubicTo(
        0.46 * scale,
        0.26 * scale,
        0.16 * scale,
        0.34 * scale,
        -0.02 * scale,
        0.18 * scale,
    )
    return path


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
