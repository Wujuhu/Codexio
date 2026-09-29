"""The same selectable icon artwork used by the Mac appearance page."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget, QLabel

from codexio.i18n import tr

ICON_IDS = ("main", "01-teal-blue-gradient", "02-violet-gradient-tile", "03-graphite-relief",
            "04-honey-orange", "05-mint-ceramic", "06-deep-ocean-aurora", "07-ice-blue-glass",
            "08-champagne-metal", "09-cream-deboss", "10-burgundy-enamel", "11-obsidian-copper",
            "12-moonlight-pearl")


def _asset_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    return (Path(bundled) / "codexio" / "icons" if bundled else Path(__file__).resolve().parent / "icons")


def app_icon(style: str) -> QIcon:
    from codexio.app_icon import load_app_icon
    if style not in ICON_IDS or style == "main":
        return load_app_icon()
    root = _asset_root()
    artwork = root / "app-icons" / (style + ".png")
    return QIcon(str(artwork)) if artwork.is_file() else load_app_icon()


class AppIconPicker(QWidget):
    def __init__(self, current: str, on_apply, parent=None):
        super().__init__(parent)
        self.current = current if current in ICON_IDS else "main"
        self.pending = self.current
        self.on_apply = on_apply
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(QLabel(tr("应用图标")))
        grid = QGridLayout()
        grid.setSpacing(10)
        root = _asset_root()
        self.buttons = {}
        for index, style in enumerate(ICON_IDS):
            button = QPushButton()
            button.setCheckable(True)
            button.setFixedSize(76, 76)
            preview = root / "app-icons" / ("main-preview.png" if style == "main" else style + "-preview.png")
            button.setIcon(QIcon(str(preview)) if preview.is_file() else app_icon(style))
            button.setIconSize(QSize(58, 58))
            button.setAccessibleName(tr("图标") + " " + str(index+1))
            button.clicked.connect(lambda _checked=False, value=style: self.select(value))
            grid.addWidget(button, index // 5, index % 5)
            self.buttons[style] = button
        layout.addLayout(grid)
        actions = QHBoxLayout()
        self.apply = QPushButton(tr("确认应用"))
        self.apply.clicked.connect(self.commit)
        actions.addWidget(self.apply)
        cancel = QPushButton(tr("取消选择"))
        cancel.clicked.connect(lambda: self.select(self.current))
        actions.addWidget(cancel)
        actions.addStretch()
        layout.addLayout(actions)
        self._refresh()

    def select(self, style):
        if style in self.buttons:
            self.pending = style
            self._refresh()

    def commit(self):
        if self.pending != self.current:
            self.on_apply(self.pending)
            self.current = self.pending
            self._refresh()

    def _refresh(self):
        for style, button in self.buttons.items():
            button.setChecked(style == self.pending)
            button.setStyleSheet("border: 2px solid #7CA88B; border-radius: 15px;" if style == self.pending else "")
        self.apply.setEnabled(self.pending != self.current)
