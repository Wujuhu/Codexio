from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from codexio.rate_limits import QuotaState
from codexio.theme import apply_dark_menu


class TrayController:
    def __init__(
        self,
        parent: QObject,
        menu: QMenu,
        on_activated: Callable[[], None],
        icon: QIcon,
    ) -> None:
        apply_dark_menu(menu)
        self._tray = QSystemTrayIcon(icon, parent)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip("Codexio")
        self._tray.activated.connect(self._on_activated)
        self._on_clicked = on_activated
        self._tray.show()

    def update_state(self, state: QuotaState, show_five: bool = True) -> None:
        week = _tray_percent(state.week.remaining_percent)
        if show_five:
            five = _tray_percent(state.five_hour.remaining_percent)
            self._tray.setToolTip("Codexio · Codex 额度  5 hours %s  ·  1 week %s" % (five, week))
            return
        self._tray.setToolTip("Codexio · Codex 额度  1 week %s" % week)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_clicked()


def _tray_percent(value: Optional[int]) -> str:
    if value is None:
        return "N/A"
    return "%d%%" % value
