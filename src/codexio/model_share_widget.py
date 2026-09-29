"""Model usage share for the native Windows overview."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from codexio.charts import compact_number
from codexio.i18n import tr
from codexio.money import usd
from codexio.theme import theme_colors
from codexio.usage_reports import token_label

METRICS = (("cost", tr("费用")), ("tokens", "Token"), ("requests", tr("请求")))


class ModelSharePanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self._rows = []
        self._totals = {}
        self._metric = "cost"
        self._visible = 5
        self._theme = "system"
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 18, 18, 18)
        self._layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel(tr("模型占比"))
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        heading.addWidget(title, 1)
        self._buttons = []
        for metric, text in METRICS:
            button = QPushButton(text)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, value=metric: self._select(value))
            heading.addWidget(button)
            self._buttons.append((metric, button))
        self._layout.addLayout(heading)
        self._items = QVBoxLayout()
        self._items.setSpacing(8)
        self._layout.addLayout(self._items)
        self._more = QPushButton(tr("显示更多"))
        self._more.clicked.connect(self._show_more)
        self._layout.addWidget(self._more, alignment=Qt.AlignmentFlag.AlignLeft)
        self.set_theme("system")
        self._render()

    def set_theme(self, theme):
        self._theme = theme
        self._render()

    def set_data(self, rows: list[dict], summary: dict):
        self._rows = [row for row in rows if row.get("id") and row.get("name")]
        self._totals = dict(cost=summary.get("usd"), tokens=summary.get("tokens"), requests=summary.get("user_requests"))
        self._visible = 5
        self._render()

    def _select(self, metric):
        if metric != self._metric:
            self._metric = metric
            self._visible = 5
            self._render()

    def _show_more(self):
        self._visible += 5
        self._render()

    def _share(self, row, metric):
        total = self._totals.get(metric) or 0
        return max(0.0, min(1.0, (row.get(metric) or 0)/total)) if total > 0 else 0.0

    def _amount(self, row):
        value = row.get(self._metric)
        if self._metric == "cost":
            return usd(value) if value is not None else "—"
        if self._metric == "tokens":
            return token_label(value or 0)
        return str(value or 0)

    def _render(self):
        if not hasattr(self, "_items"):
            return
        c = theme_colors(self._theme)
        for metric, button in self._buttons:
            button.setChecked(metric == self._metric)
            button.setProperty("primary", metric == self._metric)
            button.style().unpolish(button)
            button.style().polish(button)
        while self._items.count():
            item = self._items.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        rows = sorted(self._rows, key=lambda row: (-(row.get(self._metric) or 0), row["id"]))
        if not rows:
            empty = QLabel(tr("暂无单模型数据"))
            self._items.addWidget(empty)
        for row in rows[:self._visible]:
            widget = QFrame()
            widget.setStyleSheet("QFrame { background: %s; border-radius: 12px; }" % c["surface"])
            content = QVBoxLayout(widget)
            content.setContentsMargins(12, 10, 12, 10)
            content.setSpacing(6)
            header = QHBoxLayout()
            name = QLabel(row["name"])
            name.setStyleSheet("font-weight: 600;")
            header.addWidget(name, 1)
            header.addWidget(QLabel(self._amount(row)))
            content.addLayout(header)
            progress = QProgressBar()
            progress.setRange(0, 1000)
            progress.setValue(round(self._share(row, self._metric)*1000))
            progress.setTextVisible(False)
            progress.setFixedHeight(8)
            progress.setStyleSheet("QProgressBar { background: %s; border: none; border-radius: 4px; } "
                                   "QProgressBar::chunk { background: %s; border-radius: 4px; }" % (c["raised"], c["quota_progress"]))
            content.addWidget(progress)
            parts = "   ".join("%s %.1f%%" % (label, self._share(row, metric)*100) for metric, label in METRICS)
            caption = QLabel(parts)
            caption.setStyleSheet("color: %s; font-size: 11px;" % c["muted"])
            content.addWidget(caption)
            self._items.addWidget(widget)
        self._more.setVisible(self._visible < len(rows))
