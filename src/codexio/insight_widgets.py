"""Local activity and service allowance details for the Windows dashboard."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
import uuid

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QHeaderView

from codexio.charts import compact_number, parse_timestamp
from codexio.durations import duration_text
from codexio.model_display import display_model, display_effort
from codexio.i18n import tr


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def percentage(value, digits=1):
    value = number(value)
    if value is None:
        return "—"
    if 0 < value < Decimal(10) ** -digits:
        return "<" + format(Decimal(10) ** -digits, ".%df" % digits) + "%"
    return format(value, ".%df" % digits) + "%"


def label(text="", muted=False):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    if muted:
        value.setProperty("muted", True)
    return value


class LocalActivityMetrics(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 16)
        for key, title in (("total_tokens", tr("累计 Token 数")), ("peak_daily_tokens", tr("单日峰值 Token")),
                           ("longest_chat_seconds", tr("最长聊天时长")), ("current_streak_days", tr("当前连续天数")),
                           ("longest_streak_days", tr("最长连续天数"))):
            column = QVBoxLayout()
            value, caption = label("—"), label(tr(title), True)
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setStyleSheet("font-size: 22px; font-weight: 500;")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(value); column.addWidget(caption)
            layout.addLayout(column, 1)
            self.values[key] = value

    def apply(self, stats):
        for key, field in self.values.items():
            value = stats.get(key)
            if value is None:
                text = "—"
            elif key.endswith("tokens"):
                text = compact_number(value)
            elif key == "longest_chat_seconds":
                text = duration_text({"duration_ms": value * 1000})
            else:
                text = tr("%d 天") % value
            field.setText(text)


class LocalInsights(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        heading = label(tr("活动洞察")); heading.setProperty("subheading", True); layout.addWidget(heading)
        row = QHBoxLayout()
        row.addWidget(label(tr("快速模式"), True)); self.fast = label("—"); row.addWidget(self.fast)
        row.addSpacing(35); row.addWidget(label(tr("最常用的推理强度"), True)); self.effort = label("—"); row.addWidget(self.effort)
        layout.addLayout(row)
        self.note = label(tr("本机记录 · 按模型调用次数统计模式占比"), True)
        self.note.setWordWrap(True); layout.addWidget(self.note)

    def apply(self, stats):
        self.fast.setText(percentage(stats.get("fast_percent"), 0))
        effort = stats.get("most_used_effort")
        self.effort.setText((display_effort(effort) + " · " + percentage(stats.get("effort_percent"), 0)) if effort else "—")
        notes = [tr("本机记录 · 按模型调用次数统计模式占比")]
        if stats.get("unknown_speed"):
            notes.append(tr("速度未知 %d 次") % stats["unknown_speed"])
        if stats.get("unknown_effort"):
            notes.append(tr("推理强度未知 %d 次") % stats["unknown_effort"])
        if stats.get("duration_partial"):
            notes.append(tr("时长仅含已记录区间"))
        self.note.setText(" · ".join(notes))


class PlanUsagePanel(QWidget):
    def __init__(self, refresh, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout(); title = label(tr("套餐用量历史")); title.setProperty("subheading", True)
        heading.addWidget(title, 1); heading.addWidget(label(tr("按模型"), True))
        reload = QPushButton(tr("刷新")); reload.clicked.connect(refresh); heading.addWidget(reload); layout.addLayout(heading)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels([tr("周期"), tr("已使用限额百分比")])
        self.tree.setRootIsDecorated(True); self.tree.setAlternatingRowColors(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 170); self.tree.setMinimumHeight(230); layout.addWidget(self.tree)
        self.note = label("", True); self.note.setWordWrap(True); layout.addWidget(self.note)

    def apply(self, report, error=""):
        self.tree.clear()
        periods = sorted((row for row in report.get("periods", []) if row.get("window_minutes") == 10080), key=lambda row: row.get("starts_at", ""), reverse=True)
        for index, period in enumerate(periods):
            start, end = (parse_timestamp(period.get(key)) for key in ("starts_at", "ends_at"))
            name = (start.strftime("%m/%d") if start else "—") + " – " + (end.strftime("%m/%d") if end else "—")
            used = number(period.get("used_basis_points"))
            text = percentage(used / 100 if used is not None else None)
            if report.get("approximate", True):
                text = tr("约 ") + text
            item = QTreeWidgetItem([name, text]); self.tree.addTopLevelItem(item)
            for group in period.get("breakdowns") or []:
                if group.get("dimension") != "model":
                    continue
                for row in group.get("rows", []):
                    amount = number(row.get("basis_points"))
                    item.addChild(QTreeWidgetItem([str(row.get("key") or "—"), percentage(amount / 100 if amount is not None else None)]))
            item.setExpanded(index == 0)
        self.tree.setVisible(bool(periods))
        text = tr("统计截至 ") + str(report.get("data_as_of") or "—") if periods else error or tr("当前账户尚未提供周期明细")
        if periods and (not report.get("coverage_complete") or any(not row.get("accounting_complete") for row in periods)):
            text += " · " + tr("部分数据")
        self.note.setText(text + (" · " + error if periods and error else ""))


class RankItem(QTreeWidgetItem):
    def __init__(self, values, amounts):
        super().__init__(values)
        self.amounts = amounts

    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        if column == 0 or not isinstance(other, RankItem):
            return super().__lt__(other)
        empty = Decimal("-Infinity") if self.treeWidget().header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder else Decimal("Infinity")
        return (self.amounts[column] if self.amounts[column] is not None else empty) < (other.amounts[column] if other.amounts[column] is not None else empty)


class ChatUsagePanel(QWidget):
    def __init__(self, refresh, parent=None):
        super().__init__(parent)
        self.report, self.threads, self.local, self.error = {}, [], False, ""
        layout = QVBoxLayout(self); heading = QHBoxLayout()
        self.title = label(tr("聊天用量排行")); self.title.setProperty("subheading", True); heading.addWidget(self.title, 1)
        self.toggle = QPushButton(tr("本机 Token")); self.toggle.clicked.connect(self._toggle); heading.addWidget(self.toggle)
        reload = QPushButton(tr("刷新")); reload.clicked.connect(refresh); heading.addWidget(reload); layout.addLayout(heading)
        self.tree = QTreeWidget(); self.tree.setColumnCount(3); self.tree.setMinimumHeight(340)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(False); self.tree.header().setMinimumSectionSize(70)
        self.tree.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        for column, width in enumerate((380, 190, 150)):
            self.tree.setColumnWidth(column, width)
        self.tree.setIndentation(22); self.tree.itemClicked.connect(lambda item, _column: item.setExpanded(not item.isExpanded()) if item.childCount() else None)
        layout.addWidget(self.tree); self.note = label("", True); self.note.setWordWrap(True); layout.addWidget(self.note)

    def apply(self, report, threads, error=""):
        self.report, self.threads, self.error = report, threads, error
        self.render()

    def _toggle(self):
        self.local = not self.local; self.render()

    def render(self):
        self.tree.setSortingEnabled(False); self.tree.clear()
        local = {row["thread_id"]: row for row in self.threads}
        rows = self.threads if self.local else self.report.get("threads", [])
        self.title.setText(tr("本机 Token 排行") if self.local else tr("聊天用量排行"))
        self.toggle.setText(tr("查看额度排行") if self.local else tr("本机 Token"))
        self.tree.setHeaderLabels([tr("聊天"), "Token" if self.local else tr("占每周限额的 %"), "" if self.local else tr("已用 Credits")])
        for column in range(3):
            self.tree.headerItem().setTextAlignment(column, Qt.AlignmentFlag.AlignCenter)
        self.tree.setColumnHidden(2, self.local)
        for row in rows:
            identity = row.get("thread_id", "")
            title = local.get(identity, {}).get("title") or identity
            if row.get("data_status") in ("partial", "unavailable"):
                title += " · " + (tr("部分数据") if row["data_status"] == "partial" else tr("暂不可用"))
            amount = number(row.get("local_tokens" if self.local else "weekly_limit_percent"))
            credits = number(row.get("balance_usage_credits"))
            item = RankItem([str(title), compact_number(int(amount)) if self.local and amount is not None else percentage(amount, 4 if amount is not None and 0 < amount < Decimal("0.01") else 2), ("0" if credits == 0 else format(credits, "f").rstrip("0").rstrip(".") if "." in format(credits, "f") else format(credits, "f")) if credits is not None else "—"], [None, amount, credits])
            self.tree.addTopLevelItem(item)
            for column in range(3):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignCenter)
            detail = QTreeWidgetItem(); item.addChild(detail); detail.setFirstColumnSpanned(True)
            panel = QWidget(); grid = QGridLayout(panel); grid.setContentsMargins(18, 12, 18, 18)
            if not self.local:
                for index, (key, name) in enumerate((("model", tr("模型")), ("reasoning_effort", tr("推理强度")), ("speed", tr("速度")))):
                    values = {}
                    for group in row.get("groups", []):
                        part = number(group.get("weekly_limit_percent"))
                        if part is not None:
                            group_name = str(group.get(key) or tr("未知"))
                            values[group_name] = values.get(group_name, Decimal(0)) + part
                    parts = []
                    for key_value, part in sorted(values.items(), key=lambda entry: entry[1], reverse=True):
                        shown = display_model(key_value) if key == "model" else display_effort(key_value) if key == "reasoning_effort" else tr("快速模式") if key_value in ("fast", "priority") else tr("标准") if key_value in ("default", "standard") else key_value
                        parts.append(shown + " " + percentage(part / amount * 100, 1) if amount and amount > 0 else shown + " —")
                    if amount and amount > sum(values.values()):
                        parts.append(tr("未归类") + " " + percentage((amount - sum(values.values())) / amount * 100, 1))
                    text = label("    ".join(parts) or "—"); text.setWordWrap(True); text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    grid.addWidget(label(tr(name), True), index, 0); grid.addWidget(text, index, 1)
            button = QPushButton(tr("打开聊天 ↗")); button.setFlat(True)
            try:
                uuid.UUID(identity)
            except (ValueError, TypeError):
                button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, thread=identity: QDesktopServices.openUrl(QUrl("codex://threads/" + thread)))
            grid.addWidget(button, 3, 1, alignment=Qt.AlignmentFlag.AlignLeft)
            self.tree.setItemWidget(detail, 0, panel); detail.setSizeHint(0, QSize(0, max(145, panel.sizeHint().height())))
        self.tree.setSortingEnabled(True); self.tree.sortItems(1, Qt.SortOrder.DescendingOrder)
        self.tree.setVisible(bool(rows))
        note = tr("仅统计本机记录") if self.local else (tr("当前周额度 · 本机可用聊天") + " · " + tr("统计截至 ") + str(self.report.get("data_as_of") or "—")) if rows else self.error or tr("当前账户尚未提供这项明细")
        if rows and self.error and not self.local:
            note += " · " + self.error
        self.note.setText(note)
