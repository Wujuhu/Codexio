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
        colors = dict(bg="#101012", surface="#1B1B1F", raised="#303035", text="#F0F0F3", inspector_surface="#292B33", inspector_border="#545866",
                      muted="#AFB0BA", border="#35353D", control_border="#44444D",
                      accent="#DDDDE5", accent_bg="#303037", action="#DDDDE5", action_hover="#F0F0F5", action_text="#1B1B20",
                      success="#83BEA0", running="#58E8A0", warning="#E5B782", money="#F0F0F3", grid="#303037", selection="#2A2A32",
                      sidebar_start="#202022", sidebar_end="#202022", header_bg="#101012", header_text="#B9B9C3",
                      card_end="#1B1B1F", blue_tint="#1B1B1F", blue_edge="#A9B4CA",
                      teal_tint="#1B1B1F", teal_edge="#A9C0B2", violet_tint="#1B1B1F", violet_edge="#BCB3CC",
                      gold_tint="#1B1B1F", gold_edge="#C5B3AA", hover="#292930",
                      scroll_track="#1B1B1F", scroll_thumb="#737380", scroll_hover="#90909E", scroll_pressed="#B7B7C3",
                      quota_progress="#82A8CA", chart_tokens="#8CDACC", chart_cost="#FFA18E", chart_input="#65D0F7",
                      chart_cache_read="#BEA7FF", chart_cache_write="#F0A9CA", chart_output="#F8D36A",
                      token_cache_read="#BEA7FF", token_input="#56CCF3", token_output="#F8D36A", token_cache_write="#F0A9CA",
                      comparison_up="#72D6A4", comparison_down="#F3A1A8",
                      quota_high="#71ECAE", quota_mid="#F8D879", quota_low="#F2A1A6",
                      activity_empty="#282B2B", activity_1="#254438", activity_2="#3B6651", activity_3="#5B9273", activity_4="#8AB99B")
    else:
        colors = dict(bg="#FCFCFE", surface="#F1F1F5", raised="#E2E2EA", text="#25252B", inspector_surface="#FFFFFF", inspector_border="#B9BDCA",
                      muted="#565661", border="#D1D1DB", control_border="#BCBCCA",
                      accent="#41414C", accent_bg="#E5E5EE", action="#35353F", action_hover="#50505E", action_text="#FFFFFF",
                      success="#2F6348", running="#00733A", warning="#87550F", money="#25252B", grid="#D5D5DF", selection="#DFDFEA",
                      sidebar_start="#EDEDF1", sidebar_end="#EDEDF1", header_bg="#FCFCFE", header_text="#53535F",
                      card_end="#F1F1F5", blue_tint="#F1F1F5", blue_edge="#59697F",
                      teal_tint="#F1F1F5", teal_edge="#4C6856", violet_tint="#F1F1F5", violet_edge="#695C80",
                      gold_tint="#F1F1F5", gold_edge="#775E57", hover="#E9E9F0",
                      scroll_track="#ECECF1", scroll_thumb="#757582", scroll_hover="#5F5F6B", scroll_pressed="#494954",
                      quota_progress="#47749A", chart_tokens="#65BFAE", chart_cost="#F07158", chart_input="#45BCEB",
                      chart_cache_read="#AB8DED", chart_cache_write="#DB8AB5", chart_output="#EDBE4C",
                      token_cache_read="#BCA2FF", token_input="#49C9F4", token_output="#FFD14F", token_cache_write="#F0A4C8",
                      comparison_up="#127B54", comparison_down="#B64B54",
                      quota_high="#69E6A6", quota_mid="#F7D577", quota_low="#F19BA0",
                      activity_empty="#E7EBE8", activity_1="#CCDFD0", activity_2="#A0C4A9", activity_3="#6B9D7A", activity_4="#3A7251")
    # Pastel data marks remain bright; their accompanying text uses readable ink.
    inks = dict(chart_tokens="#287267", chart_cost="#A44431", chart_input="#216D92",
                chart_cache_read="#7058A6", chart_cache_write="#9A4B72", chart_output="#886419")
    for key in inks:
        colors[key + "_ink"] = colors[key] if resolve_theme(name) == "dark" else inks[key]
    return colors


def dashboard_stylesheet(name: str = "system") -> str:
    c = theme_colors(name)
    for direction in ("up", "down"):
        c["arrow_" + direction] = (Path(__file__).with_name("icons") / ("chevron-%s-%s.svg" % (direction, resolve_theme(name)))).as_posix()
    return """
QMainWindow, QDialog, QWidget#dashboardRoot, QWidget#page { background: %(bg)s; }
QWidget { color: %(text)s; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
QFrame#sidebar { background: %(sidebar_start)s; border-right: 1px solid %(border)s; }
QFrame[card="true"] { background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px; }
QFrame[card="true"][tone="blue"] { background: %(surface)s; border-top: 3px solid %(blue_edge)s; }
QFrame[card="true"][tone="teal"] { background: %(surface)s; border-top: 3px solid %(teal_edge)s; }
QFrame[card="true"][tone="violet"] { background: %(surface)s; border-top: 3px solid %(violet_edge)s; }
QFrame[card="true"][tone="gold"] { background: %(surface)s; border-top: 3px solid %(gold_edge)s; }
QFrame#sessionTooltip { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px; }
QLabel { background: transparent; }
QLabel[muted="true"] { color: %(muted)s; }
QLabel[heading="true"] { font-size: 22px; font-weight: 600; }
QLabel[subheading="true"] { font-size: 16px; font-weight: 600; }
QLabel[metric="true"] { font-size: 29px; font-weight: 500; }
QLabel[money="true"] { color: %(money)s; font-size: 24px; font-weight: 500; }
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
QDateEdit { padding-right: 30px; }
QDateEdit::drop-down { subcontrol-origin: border; subcontrol-position: top right;
    width: 24px; border: none; border-left: 1px solid %(control_border)s; }
QDateEdit QLineEdit { background: transparent; border: none; padding: 0; min-height: 0; }
QComboBox::down-arrow, QDateEdit::down-arrow { image: url("%(arrow_down)s"); width: 12px; height: 8px; }
QLineEdit { placeholder-text-color: %(muted)s; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: none; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("%(arrow_up)s"); width: 10px; height: 6px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("%(arrow_down)s"); width: 10px; height: 6px; }
QComboBox QAbstractItemView { background: %(surface)s; selection-background-color: %(selection)s; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border-color: %(accent)s; }
QTableView, QListWidget { background: %(surface)s; alternate-background-color: %(raised)s;
 border: 1px solid %(border)s; border-radius: 10px; gridline-color: %(border)s;
 selection-background-color: %(selection)s; selection-color: %(text)s; outline: none; }
QTableView::item { padding: 9px 14px; border-bottom: 1px solid %(grid)s; }
QTableView#resetCreditTable::item { padding: 5px 8px; }
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

QWidget#dashboardRoot { background: %(sidebar_start)s; }
QFrame#sidebar { background: %(sidebar_start)s; border: none; }
QFrame#contentSurface { background: %(bg)s; border: 1px solid %(border)s; border-radius: 18px; }
QFrame[card="true"] { background: %(surface)s; border: none; border-radius: 14px; }
QFrame[card="true"][tone] { background: %(surface)s; border: none; }
QLabel#brandName { font-size: 18px; font-weight: 600; padding: 0 8px 5px; }
QLabel#statusText, QLabel#sidebarStatus { font-size: 11px; color: %(muted)s; }
QPushButton { padding: 7px 11px; border-radius: 8px; }
QPushButton[quiet="true"] { background: transparent; border: none; color: %(muted)s; padding: 6px 9px; }
QPushButton[quiet="true"]:hover { background: %(hover)s; color: %(text)s; }
QPushButton#navigationSearch { background: %(surface)s; color: %(muted)s; text-align: left; border: none; padding: 9px 12px; }
QPushButton#accountButton { background: transparent; border: none; text-align: left; padding: 11px 9px; }
QListWidget#navigationList { background: transparent; border: none; outline: none; }
QListWidget#navigationList::item { background: transparent; color: %(muted)s; border: none; border-radius: 8px; padding: 7px 10px; }
QListWidget#navigationList::item:selected { background: %(raised)s; color: %(text)s; }
QListWidget#navigationList::item:hover { background: %(hover)s; }
QListWidget#navigationList::item:focus { border: none; }
QWidget#segmentedControl { background: %(surface)s; border-radius: 8px; }
QPushButton[segment="true"] { border: none; background: transparent; padding: 5px 10px; font-size: 12px; color: %(muted)s; }
QPushButton[segment="true"]:checked { background: %(raised)s; color: %(text)s; }
QTableView#requestLedger { border: none; border-radius: 0; background: %(bg)s; alternate-background-color: %(bg)s; }
QTableView#requestLedger::item { border: none; border-bottom: 1px solid %(grid)s; padding: 0; }
QTableView#requestLedger QHeaderView::section { background: %(bg)s; color: %(muted)s; padding: 12px 9px; font-size: 11px; font-weight: 400; }
QFrame#logCanvas QTableView#requestLedger { background: %(surface)s; alternate-background-color: %(surface)s; }
QFrame#logCanvas QTableView#requestLedger QHeaderView::section { background: %(surface)s; }
QTableView#pricingTable::item { padding: 4px 8px; }
QFrame#requestInspector { background: %(inspector_surface)s; border: 1px solid %(inspector_border)s; border-radius: 13px; }
QFrame#requestInspector QWidget { background: transparent; }
QWidget#inspectorSessionTitle { font-size: 20px; font-weight: 600; }
QFrame#requestInspector QFrame[callSummary="true"] { border: none; border-bottom: 1px solid %(inspector_border)s; }
QFrame#requestInspector QScrollBar:vertical { background: %(inspector_surface)s; }
QFrame#requestInspector QScrollBar::handle:vertical { border-color: %(inspector_surface)s; }
QListWidget#settingsSections { background: transparent; border: none; }
QListWidget#settingsSections::item { padding: 11px 9px; border: none; border-radius: 7px; color: %(muted)s; }
QListWidget#settingsSections::item:selected { background: %(raised)s; color: %(text)s; }
QTableView#resetCreditTable { background: %(surface)s; border: none; border-radius: 0; }
QTableView#resetCreditTable QHeaderView::section { background: %(surface)s; }
QScrollBar:vertical { width: 10px; border-radius: 5px; }
QScrollBar:horizontal { height: 10px; border-radius: 5px; }
QProgressBar::chunk { background: %(accent)s; }

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
    from codexio.window_chrome import apply_window_chrome
    apply_window_chrome(widget, dark=resolve_theme(name) == "dark", colors=colors)
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
