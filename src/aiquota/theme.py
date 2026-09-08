"""Shared native-widget appearance for the dashboard and its dialogs."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QWidget

_fonts_ready = False


def ensure_ui_fonts() -> None:
    """Offscreen Qt on Windows omits system fonts; load the installed CJK font."""
    global _fonts_ready
    if _fonts_ready or QApplication.instance() is None:
        return
    _fonts_ready = True
    if os.name == "nt" and "Microsoft YaHei UI" not in QFontDatabase.families():
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc"):
            path = font_dir / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))


def resolve_theme(name: str = "system") -> str:
    if name in ("light", "dark"):
        return name
    app = QApplication.instance()
    if app is not None:
        try:
            scheme = app.styleHints().colorScheme()
            if scheme != Qt.ColorScheme.Unknown:
                return "dark" if scheme == Qt.ColorScheme.Dark else "light"
        except AttributeError:
            pass
        return "dark" if app.palette().window().color().lightness() < 128 else "light"
    return "light"


def theme_colors(name: str = "system") -> dict[str, str]:
    if resolve_theme(name) == "dark":
        return dict(
            bg="#0B1423", surface="#1B2D45", raised="#293F5B", text="#F2F7FF",
            muted="#B5C8DF", border="#4A6585", control_border="#6885A7",
            accent="#93C5FF", accent_bg="#243F65", action="#245BC1", action_hover="#306BCD", action_text="#FFFFFF",
            success="#73DFC3", warning="#FFD18B", money="#FFD18B", grid="#39516F", selection="#294F7F",
            sidebar_start="#12233A", sidebar_end="#0C1829", header_bg="#2A4261", header_text="#E3EDFC",
            card_end="#15263C", blue_tint="#203F67", blue_edge="#6098DE",
            teal_tint="#16413F", teal_edge="#4BAF9F", violet_tint="#333854", violet_edge="#8E99CB",
            gold_tint="#3C352D", gold_edge="#B99B60", hover="#253D5A",
            scroll_track="#142238", scroll_thumb="#7890B0", scroll_hover="#99B0CC", scroll_pressed="#BED2E8",
            chart_tokens="#8DAEFF", chart_cost="#FFD18B", chart_input="#64C4EF",
            chart_cache_read="#B9A6F7", chart_cache_write="#E6AB6C", chart_output="#64D6B4")
    return dict(
        bg="#E8EFF7", surface="#FFFFFF", raised="#DFE9F5", text="#13263F",
        muted="#455C77", border="#AFBFD2", control_border="#8297AF",
        accent="#184DAD", accent_bg="#DCE9FC", action="#205CC6", action_hover="#154BAA", action_text="#FFFFFF",
        success="#087565", warning="#93611B", money="#8E5C16", grid="#C4D1E1", selection="#D7E6FB",
        sidebar_start="#F9FCFF", sidebar_end="#EAF1FA", header_bg="#DCE7F5", header_text="#243F61",
        card_end="#F6F9FE", blue_tint="#E7F0FE", blue_edge="#527EBA",
        teal_tint="#E4F3EF", teal_edge="#448F81", violet_tint="#EDEDF8", violet_edge="#828CBC",
        gold_tint="#F7EFE1", gold_edge="#B08B46", hover="#EDF4FD",
        scroll_track="#DCE6F2", scroll_thumb="#6D829F", scroll_hover="#506C90", scroll_pressed="#365777",
        chart_tokens="#4E59B3", chart_cost="#A76509", chart_input="#0874AA",
        chart_cache_read="#845AB2", chart_cache_write="#A35618", chart_output="#007F67")


def dashboard_stylesheet(name: str = "system") -> str:
    c = theme_colors(name)
    return """
QMainWindow, QDialog, QWidget#dashboardRoot, QWidget#page { background: %(bg)s; }
QWidget { color: %(text)s; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
QFrame#sidebar { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(sidebar_start)s, stop:1 %(sidebar_end)s); border-right: 1px solid %(border)s; }
QFrame[card="true"] { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 %(surface)s, stop:1 %(card_end)s); border: 1px solid %(border)s; border-radius: 12px; }
QFrame[card="true"][tone="blue"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(blue_tint)s,stop:1 %(surface)s); border-top: 3px solid %(blue_edge)s; }
QFrame[card="true"][tone="teal"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(teal_tint)s,stop:1 %(surface)s); border-top: 3px solid %(teal_edge)s; }
QFrame[card="true"][tone="violet"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(violet_tint)s,stop:1 %(surface)s); border-top: 3px solid %(violet_edge)s; }
QFrame[card="true"][tone="gold"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(gold_tint)s,stop:1 %(surface)s); border-top: 3px solid %(gold_edge)s; }
QFrame#sessionTooltip { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px; }
QLabel { background: transparent; }
QLabel[muted="true"] { color: %(muted)s; }
QLabel[heading="true"] { font-size: 26px; font-weight: 700; }
QLabel[subheading="true"] { font-size: 16px; font-weight: 600; }
QLabel[metric="true"] { font-size: 29px; font-weight: 700; }
QLabel[money="true"] { color: %(money)s; font-size: 21px; font-weight: 600; }
QLabel[eyebrow="true"] { color: %(accent)s; font-size: 11px; font-weight: 700; }
QPushButton, QToolButton { background: %(surface)s; border: 1px solid %(control_border)s;
 border-radius: 8px; padding: 8px 13px; font-weight: 500; }
QPushButton:hover, QToolButton:hover { background: %(raised)s; border-color: %(accent)s; }
QPushButton:pressed { background: %(accent_bg)s; }
QPushButton[primary="true"] { background: %(action)s; color: %(action_text)s; border-color: %(action)s; }
QPushButton[primary="true"]:hover { background: %(action_hover)s; border-color: %(action_hover)s; }
QPushButton:focus, QToolButton:focus { border: 2px solid %(accent)s; }
QPushButton[nav="true"] { border: none; border-left: 3px solid transparent; background: transparent; text-align: left; padding: 13px 13px; }
QPushButton[nav="true"]:hover { background: %(raised)s; }
QPushButton[nav="true"]:checked { background: %(accent_bg)s; color: %(accent)s; border-left: 3px solid %(accent)s; font-weight: 600; }
QPushButton:disabled, QToolButton:disabled { color: %(muted)s; background: %(raised)s; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit { background: %(surface)s;
 border: 1px solid %(control_border)s; border-radius: 7px; padding: 7px 10px; min-height: 18px; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: %(surface)s; selection-background-color: %(selection)s; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border-color: %(accent)s; }
QTableView, QListWidget { background: %(surface)s; alternate-background-color: %(raised)s;
 border: 1px solid %(border)s; border-radius: 10px; gridline-color: %(border)s;
 selection-background-color: %(selection)s; selection-color: %(text)s; outline: none; }
QTableView::item { padding: 9px 14px; border-bottom: 1px solid %(grid)s; }
QTableView::item:hover { background: %(hover)s; }
QTableView::item:selected { background: %(selection)s; color: %(text)s; }
QHeaderView::section { background: %(header_bg)s; color: %(header_text)s; border: none;
 border-bottom: 1px solid %(border)s; padding: 13px 14px; font-weight: 600; }
QTableView#requestLogTable::item { padding: 4px 6px; }
QTableView#requestLogTable QHeaderView { background: %(header_bg)s; }
QTableView#requestLogTable QHeaderView::section { padding: 10px 6px; }
QTableView QTableCornerButton::section { background: %(header_bg)s; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: %(scroll_track)s; width: 16px; margin: 0; border: none; border-radius: 7px; }
QScrollBar::handle:vertical { background: %(scroll_thumb)s; min-height: 48px; border: 2px solid %(scroll_track)s; border-radius: 7px; }
QScrollBar::handle:vertical:hover { background: %(scroll_hover)s; }
QScrollBar::handle:vertical:pressed { background: %(scroll_pressed)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; border: none; background: transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: %(scroll_track)s; height: 16px; margin: 0; border: none; border-radius: 7px; }
QScrollBar::handle:horizontal { background: %(scroll_thumb)s; min-width: 48px; border: 2px solid %(scroll_track)s; border-radius: 7px; }
QScrollBar::handle:horizontal:hover { background: %(scroll_hover)s; }
QScrollBar::handle:horizontal:pressed { background: %(scroll_pressed)s; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; border: none; background: transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QSlider::groove:horizontal { height: 8px; background: %(scroll_track)s; border-radius: 4px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 4px; }
QSlider::handle:horizontal { background: %(accent)s; width: 20px; margin: -6px 0; border: 2px solid %(surface)s; border-radius: 10px; }
QSlider::handle:horizontal:hover { background: %(scroll_hover)s; }
QCheckBox { spacing: 8px; padding: 4px 0; }
QGroupBox { border: 1px solid %(border)s; border-radius: 12px; margin-top: 20px; padding: 16px; }
QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 5px; font-weight: 600; }
QProgressBar { background: %(raised)s; border: none; border-radius: 4px; min-height: 7px; max-height: 7px; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 %(blue_edge)s,stop:1 %(success)s); border-radius: 4px; }
QToolTip { color: %(text)s; background: %(surface)s; border: 1px solid %(border)s; padding: 9px; }
QTabWidget::pane { border: 1px solid %(border)s; background: %(surface)s; }
QTabBar::tab { background: %(raised)s; padding: 10px 18px; }
QTabBar::tab:selected { background: %(accent_bg)s; color: %(accent)s; }
QPlainTextEdit { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; padding: 8px; }
""" % c


def apply_theme(widget: QWidget, name: str = "system") -> dict[str, str]:
    ensure_ui_fonts()
    colors = theme_colors(name)
    palette = widget.palette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "surface"), (QPalette.ColorRole.AlternateBase, "raised"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "surface"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "selection"),
                      (QPalette.ColorRole.HighlightedText, "text"), (QPalette.ColorRole.ToolTipBase, "surface"),
                      (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.PlaceholderText, "muted")):
        palette.setColor(role, QColor(colors[key]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors["muted"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(colors["muted"]))
    widget.setPalette(palette)
    widget.setStyleSheet(dashboard_stylesheet(name))
    return colors


def apply_dark_menu(menu: QWidget) -> None:
    """Keep tray and widget popup menus independent from dashboard appearance."""
    colors = apply_theme(menu, "dark")
    menu.setStyleSheet("""
QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
        border-radius: 8px; padding: 6px; }
QMenu::item { color: %(text)s; background: transparent; padding: 8px 26px 8px 22px;
             border-radius: 5px; }
QMenu::item:selected { background: %(selection)s; color: %(text)s; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { background: %(border)s; height: 1px; margin: 5px 9px; }
""" % colors)
