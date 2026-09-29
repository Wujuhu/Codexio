"""Windows-native report reader and share card using the same data and artwork."""
from __future__ import annotations

import sys
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QImageReader, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from codexio.i18n import tr
from codexio.usage_reports import PERIODS, cost_label, token_label
from codexio.usage_reports import ReportStore, preferred_period

PALETTES = {
    "bookmark": ("#FFFBF4", "#4E4032", "#78634D", "#C99B65", "#E8D7AF", "#EBAF86", "#F7EFDD"),
    "garden": ("#FFFDF7", "#173C31", "#4C6959", "#7CA88B", "#C7DCC8", "#F1BAA1", "#F1F7EE"),
    "afternoon": ("#FFFCF7", "#493027", "#785746", "#DA855A", "#F2C5AA", "#D67650", "#FEF1E8"),
}
STYLE_NAMES = {"bookmark": tr("奶油猫尾书签"), "garden": tr("薄荷猫咪花园"), "afternoon": tr("杏色时段图")}
TITLES = {"day": tr("AI日报"), "week": tr("AI周报"), "month": tr("AI月报")}
PERIOD_WORDS = {"day": tr("昨天"), "week": tr("上周"), "month": tr("上月")}


def _asset(name: str) -> Path:
    roots = []
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        roots.append(Path(bundled) / "codexio" / "report-cards")
    roots.append(Path(__file__).resolve().parents[2] / "macos" / "Resources" / "ReportCards")
    return next((root / name for root in roots if (root / name).is_file()), roots[0] / name)


def _image(name: str, maximum: int) -> QImage:
    reader = QImageReader(str(_asset(name)))
    size = reader.size()
    if size.isValid():
        ratio = min(1.0, maximum / max(size.width(), size.height()))
        reader.setScaledSize(QSize(max(1, round(size.width()*ratio)), max(1, round(size.height()*ratio))))
    reader.setAutoTransform(True)
    return reader.read()


def _font(size: int, *, serif=False, bold=False) -> QFont:
    family = ("Songti SC" if sys.platform == "darwin" else "SimSun") if serif else ("Microsoft YaHei UI" if sys.platform == "win32" else "Arial")
    font = QFont(family)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def _text(p: QPainter, value, x, y, width, height, *, size=13, color="#173C31", serif=False, bold=False, align=Qt.AlignmentFlag.AlignLeft):
    p.setPen(QColor(color))
    font = _font(size, serif=serif, bold=bold)
    p.setFont(font)
    text = str(value)
    text = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(width)))
    p.drawText(QRectF(x, y, width, height), align | Qt.AlignmentFlag.AlignVCenter, text)


def _rule(p: QPainter, y: int, color: str, x: int = 42, width: int = 416):
    p.setPen(QPen(QColor(color), 1))
    p.drawLine(x, y, x+width, y)


def _rect(p: QPainter, x, y, width, height, radius, color):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(x, y, width, height), radius, radius)


def _cat_path(height: int) -> QPainterPath:
    width = 500
    path = QPainterPath()
    path.moveTo(18, 80)
    path.cubicTo(16, 58, 28, 10, 42, 19)
    path.cubicTo(57, 11, 96, 67, 123, 75)
    path.quadTo(width/2, 53, width-123, 75)
    path.cubicTo(width-96, 67, width-57, 11, width-42, 19)
    path.cubicTo(width-28, 10, width-16, 58, width-18, 80)
    path.lineTo(width-18, height-62)
    path.quadTo(width-20, height-18, width-72, height-18)
    path.lineTo(72, height-18)
    path.quadTo(20, height-18, 18, height-62)
    path.closeSubpath()
    return path


class ReportCard(QWidget):
    def __init__(self, document: dict, style: str, parent=None):
        super().__init__(parent)
        self.document = document
        self.style = style
        self.brand = QImage()
        self.cat = QImage()
        self.load_artwork(style)

    @property
    def logical_height(self) -> int:
        count = min(3, len(self.document.get("models") or []))
        return max(820, 952 - max(0, 3-count)*50)

    def load_artwork(self, style: str):
        self.brand = _image("brand-mark.png", 128)
        self.cat = _image("cat-" + style + ".png", 384)
        self.style = style
        self.update()

    def release_artwork(self):
        self.brand = QImage()
        self.cat = QImage()

    def set_document(self, document: dict):
        self.document = document
        self.update()

    def render_image(self) -> QImage:
        image = QImage(1000, self.logical_height*2, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(PALETTES[self.style][6]))
        painter = QPainter(image)
        painter.scale(2, 2)
        self.draw(painter)
        painter.end()
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.scale(self.width()/500, self.height()/self.logical_height)
        self.draw(painter)

    def draw(self, p: QPainter):
        paper, ink, muted, accent, secondary, warm, surface = PALETTES[self.style]
        height = self.logical_height
        p.fillRect(QRectF(0, 0, 500, height), QColor(surface))
        path = _cat_path(height)
        p.setPen(QPen(QColor(secondary), 1))
        p.setBrush(QColor(paper))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(warm))
        for left in (37, 500-93):
            ear = QPainterPath()
            ear.moveTo(left, 70)
            ear.quadTo(left+11, 34, left+23, 44)
            ear.quadTo(left+56, 72, left, 70)
            p.drawPath(ear)
        if not self.brand.isNull():
            p.drawImage(QRectF(49, 89, 36, 36), self.brand)
        _text(p, "Codexio", 87, 84, 190, 38, size=27, color=ink, serif=True, bold=True)
        _text(p, TITLES[self.document["period"]], 87, 120, 170, 18, size=12, color=muted)
        _text(p, self.document["date_label"], 310, 92, 145, 28, size=13, color=muted, align=Qt.AlignmentFlag.AlignRight)
        _rule(p, 145, secondary)

        peak = self.document.get("peak")
        slices = self.document.get("time_slices") or []
        requests = self.document["requests"]
        hero = (PERIOD_WORDS[self.document["period"]] + tr("，") + slices[peak]["name"] + tr("最热闹")
                if peak is not None and peak < len(slices) else PERIOD_WORDS[self.document["period"]] + tr("没有请求记录"))
        natural_width = QFontMetrics(_font(42, serif=True, bold=True)).horizontalAdvance(hero)
        headline_size = min(42, max(29, round(42*420/max(1, natural_width))))
        _text(p, hero, 42, 170, 425, 48, size=headline_size, color=ink, serif=True, bold=True)
        if peak is not None:
            busiest = slices[peak]["requests"]
            _text(p, f"{busiest} / {requests}", 42, 220, 185, 39, size=32, color=ink, serif=True)
            _text(p, tr("次请求"), 232, 225, 73, 28, size=12, color=ink)
            _text(p, f"{slices[peak]['name']} · {round(busiest*100/requests)}%", 42, 261, 220, 23, size=14, color=accent)
        if not self.cat.isNull():
            p.drawImage(QRectF(365, 225, 91, 70), self.cat)
        _rule(p, 315, secondary)

        tokens = self.document["total_tokens"]
        hit = round(self.document["cached_tokens"]*100/self.document["input_tokens"]) if self.document["input_tokens"] else None
        metrics = ((cost_label(self.document["cost"], self.document["cost_complete"]), tr("费用")),
                   (("" if self.document["tokens_complete"] else "≥")+token_label(tokens), tr("总 Token")),
                   (str(requests), tr("用户请求")),
                   ((str(hit)+"%") if hit is not None else "—", tr("命中率")))
        for index, (value, label) in enumerate(metrics):
            x = 42+index*104
            _text(p, value, x, 329, 104, 37, size=27, color=ink, serif=True, bold=True, align=Qt.AlignmentFlag.AlignHCenter)
            _text(p, label, x, 366, 104, 20, size=11, color=muted, align=Qt.AlignmentFlag.AlignHCenter)
            if index < 3:
                p.setPen(QPen(QColor(secondary), 1)); p.drawLine(x+104, 342, x+104, 382)
        _rule(p, 402, secondary)

        _text(p, tr("使用节奏"), 42, 415, 190, 35, size=25, color=ink, serif=True, bold=True)
        if peak is not None:
            _text(p, slices[peak]["name"]+tr("最活跃"), 295, 418, 163, 26, size=11, color=accent, align=Qt.AlignmentFlag.AlignRight)
        largest = max((row["requests"] for row in slices), default=1) or 1
        for index, row in enumerate(slices[:4]):
            x = 42+index*107
            count = row["requests"]
            _text(p, count, x, 452, 95, 20, size=12, color=ink, serif=True, align=Qt.AlignmentFlag.AlignHCenter)
            _rect(p, x+5, 515-(count/largest)*38, 90, max(1, (count/largest)*38), 5, accent if peak == index else secondary)
            _text(p, row["name"], x, 519, 100, 20, size=11, color=muted, align=Qt.AlignmentFlag.AlignHCenter)
        for first, position, align in ((True, 42, Qt.AlignmentFlag.AlignLeft), (False, 255, Qt.AlignmentFlag.AlignRight)):
            seconds = self.document["first" if first else "last"]
            if seconds is None:
                continue
            hour = time.localtime(seconds).tm_hour
            title = (tr("最早") if first else tr("最晚")) + " · " + time.strftime("%H:%M", time.localtime(seconds))
            note = ((tr("早早开工，继续加油喵") if 5 <= hour < 9 else tr("按自己的节奏来，喵")) if first else
                    (tr("辛苦啦，早点休息喵") if hour >= 22 or hour < 5 else tr("努力收好，好好放松喵")))
            _text(p, title, position, 541, 203, 20, size=11, color=ink, bold=True, align=align)
            _text(p, note, position, 560, 203, 19, size=10, color=muted, align=align)
        _rule(p, 593, secondary)

        _text(p, tr("模型使用"), 42, 612, 193, 35, size=25, color=ink, serif=True, bold=True)
        _text(p, f"{self.document['model_calls']} " + tr("次调用"), 301, 616, 157, 27,
              size=11, color=muted, align=Qt.AlignmentFlag.AlignRight)
        models = self.document.get("models") or []
        if not models:
            _text(p, tr("暂无模型调用"), 42, 653, 250, 28, size=13, color=muted)
        for index, model in enumerate(models[:3]):
            y = 650 + index*48
            _text(p, model["name"], 42, y, 270, 25, size=15, color=ink, bold=True)
            _text(p, cost_label(model["cost"], model["complete"]) + " · " + token_label(model["tokens"]) + " Token",
                  42, y+23, 276, 21, size=12, color=muted)
            percent = round(model["calls"]*100/self.document["model_calls"]) if self.document["model_calls"] else 0
            _text(p, f"{model['calls']} {tr('次')} · {percent}%", 302, y, 156, 25,
                  size=12, color=ink, align=Qt.AlignmentFlag.AlignRight)

        note = ((PERIOD_WORDS[self.document["period"]] + tr("的请求在") + slices[peak]["name"]
                 + tr("最多，共 ") + str(slices[peak]["requests"]) + tr(" 次。")) if peak is not None else
                PERIOD_WORDS[self.document["period"]] + tr("没有请求记录"))
        _rect(p, 42, height-141, 416, 61, 18, surface)
        _text(p, tr("小猫发现：") + note, 68, height-135, 376, 50, size=13, color=ink)
        _text(p, tr("专注 · 与 AI 共成长"), 130, height-69, 240, 25, size=10,
              color=muted, align=Qt.AlignmentFlag.AlignHCenter)


class ReportDialog(QDialog):
    def __init__(self, documents: dict[str, dict], period: str, style: str, directory: Path, parent=None, on_style=None):
        super().__init__(parent)
        self.documents, self.period, self.style = documents, period, style
        self.directory, self.on_style = directory, on_style
        self.setWindowTitle(tr("AI 使用报告"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(530, 620)
        self.card = ReportCard(documents[period], style)
        self.reader = QScrollArea()
        self.reader.setWidget(self.card)
        self.reader.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reader.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.reader.setWidgetResizable(False)
        self.reader.setFrameShape(QFrame.Shape.NoFrame)
        self.reader.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.rail = QFrame()
        self.rail.setFixedWidth(115)
        self.rail.setStyleSheet("QFrame { background: #F1F7EE; border: none; } QPushButton { border: none; color: #173C31; background: transparent; border-radius: 12px; padding: 5px; } QPushButton:hover { background: #E2ECE0; } QToolButton { border: none; background: transparent; color: #173C31; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addWidget(self.reader, 1)
        layout.addWidget(self.rail)
        side = QVBoxLayout(self.rail)
        side.setContentsMargins(7, 6, 7, 9)
        side.setSpacing(10)
        close = QPushButton("×")
        close.setAccessibleName(tr("关闭报告"))
        close.setFixedSize(26, 26)
        close.clicked.connect(self.close)
        side.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.period_buttons = {}
        for key, label in (("day", tr("日报")), ("week", tr("周报")), ("month", tr("月报"))):
            button = QPushButton(label)
            button.setFixedHeight(36)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, value=key: self.select_period(value))
            side.addWidget(button)
            self.period_buttons[key] = button
        self.style_button = QToolButton()
        self.style_button.setText(tr("样式"))
        self.style_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.style_button)
        for key, name in STYLE_NAMES.items():
            action = menu.addAction(name)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=key: self.select_style(value))
        self.style_button.setMenu(menu)
        self.style_button.setFixedHeight(32)
        side.addWidget(self.style_button)
        side.addStretch()
        share = QPushButton(tr("分享"))
        share.setAccessibleName(tr("分享报告"))
        share.setFixedHeight(32)
        share.clicked.connect(self.share)
        side.addWidget(share)
        self.note = QLabel(tr("把小进展分享出去，喵"))
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setStyleSheet("color: #173C31; font-size: 10px;")
        side.addWidget(self.note)
        self.setStyleSheet("QDialog { background: #F1F7EE; }")
        self._refresh_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_card()

    def _fit_card(self):
        viewport = self.reader.viewport().size()
        if viewport.width() < 1 or viewport.height() < 1:
            return
        scale = max(0.38, min(0.92, (viewport.width()-12)/500, (viewport.height()-12)/self.card.logical_height))
        self.card.setFixedSize(round(500*scale), round(self.card.logical_height*scale))

    def _refresh_buttons(self):
        for key, button in self.period_buttons.items():
            button.setStyleSheet("background: #173C31; color: white;" if key == self.period else "")
        for action in self.style_button.menu().actions():
            action.setChecked(action.text() == STYLE_NAMES[self.style])

    def select_period(self, period: str):
        if period not in self.documents or period == self.period:
            return
        self.period = period
        self.card.set_document(self.documents[period])
        self._refresh_buttons()
        self._fit_card()
        self.reader.verticalScrollBar().setValue(0)

    def select_style(self, style: str):
        if style not in STYLE_NAMES or style == self.style:
            return
        self.style = style
        self.card.load_artwork(style)
        paper, ink, muted, accent, secondary, warm, surface = PALETTES[style]
        self.setStyleSheet("QDialog { background: %s; }" % surface)
        self._refresh_buttons()
        if self.on_style:
            self.on_style(style)

    def share(self):
        document = self.documents[self.period]
        folder = self.directory / document["source_id"] / "shared"
        folder.mkdir(parents=True, exist_ok=True)
        suggested = folder / ("Codexio-%s-%s-%s.png" % (self.period, document["start"], self.style))
        path, _ = QFileDialog.getSaveFileName(self, tr("分享报告"), str(suggested), "PNG (*.png)")
        if path:
            self.card.render_image().save(path, "PNG")

    def closeEvent(self, event):
        self.card.release_artwork()
        super().closeEvent(event)


class UsageReportController(QObject):
    loaded = Signal(object)

    def __init__(self, parent, config_provider, save_style, *, can_present=lambda: True, mock=False):
        super().__init__(parent)
        self.store = ReportStore()
        self.config_provider = config_provider
        self.save_style = save_style
        self.can_present = can_present
        self.mock = mock
        self.dialog = None
        self._eligible_day = None
        self._busy = False
        self._request = 0
        self.loaded.connect(self._show_result)

    def opened(self, window, data):
        now = datetime.now().astimezone()
        self._eligible_day = now.date() if now.hour >= 8 else None
        self.data_available(window, data)

    def data_available(self, window, data):
        now = datetime.now().astimezone()
        if (not self.mock and self.can_present() and self._eligible_day == now.date() and now.hour >= 8
                and self.config_provider().get("usage_report_auto", True) and not self.store.seen(now.date())):
            self._prepare(window, data, manual=False)

    def open_manual(self, window, data):
        if window is not None:
            self._prepare(window, data, manual=True)

    def _prepare(self, window, data, *, manual):
        if self._busy or self.dialog is not None or window is None or not window.isVisible() or not window.isActiveWindow() or not data:
            return
        path = data.get("query_path")
        generation = data.get("query_generation")
        if not path or not isinstance(generation, int):
            return
        self._busy = True
        self._request += 1
        request = self._request
        def work():
            try:
                documents = self.store.load(path, generation)
                self.loaded.emit((request, window, manual, documents, None))
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                self.loaded.emit((request, window, manual, None, str(error)))
        threading.Thread(target=work, name="Codexio-report", daemon=True).start()

    def _show_result(self, result):
        from shiboken6 import isValid
        from PySide6.QtWidgets import QMessageBox
        request, window, manual, documents, error = result
        if request != self._request:
            return
        self._busy = False
        if window is None or not isValid(window) or not window.isVisible() or not window.isActiveWindow():
            return
        if error:
            if manual:
                QMessageBox.warning(window, tr("AI 使用报告"), error)
            return
        now = datetime.now().astimezone()
        if not manual and (not self.can_present() or QApplication.activeModalWidget() is not None
                           or self._eligible_day != now.date() or now.hour < 8 or self.store.seen(now.date())):
            return
        period = preferred_period(now.date())
        style = self.config_provider().get("usage_report_style", "garden")
        if style not in STYLE_NAMES:
            style = "garden"
        dialog = ReportDialog(documents, period, style, self.store.directory, window, self.save_style)
        self.dialog = dialog
        available = window.size()
        height = min(820, max(620, available.height()-12))
        width = min(620, max(530, min(available.width()-24, round(height*0.55+160))))
        dialog.resize(width, height)
        dialog.finished.connect(lambda *_: self._closed(dialog))
        dialog.show()
        QTimer.singleShot(0, dialog._fit_card)
        if now.hour >= 8:
            self.store.mark_seen(now.date())
            self._eligible_day = None

    def _closed(self, dialog):
        if self.dialog is dialog:
            self.dialog = None
