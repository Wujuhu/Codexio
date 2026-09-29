"""Windows settings for the existing iPhone read-only pairing flow."""
from __future__ import annotations

import qrcode
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from codexio.i18n import tr


def _qr(value: str) -> QPixmap:
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(value)
    code.make(fit=True)
    matrix = code.get_matrix()
    unit = max(3, 240//len(matrix))
    image = QImage(len(matrix)*unit, len(matrix)*unit, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#173C31"))
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                painter.drawRect(x*unit, y*unit, unit, unit)
    painter.end()
    return QPixmap.fromImage(image)


class MobileSyncSettings(QWidget):
    def __init__(self, host, enabled: bool, on_toggle, parent=None):
        super().__init__(parent)
        self.host = host
        self.on_toggle = on_toggle
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.enabled = QCheckBox(tr("同步"))
        self.enabled.setChecked(enabled)
        self.enabled.setEnabled(not host.mock)
        self.enabled.toggled.connect(on_toggle)
        layout.addWidget(self.enabled)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.content = QWidget()
        contents = QVBoxLayout(self.content)
        contents.setContentsMargins(0, 0, 0, 0)
        contents.setSpacing(12)
        self.device_name = QLineEdit(host.name)
        self.device_name.setPlaceholderText(tr("设备名称"))
        self.device_name.editingFinished.connect(lambda: host.set_name(self.device_name.text()))
        contents.addWidget(self.device_name)
        notice = QLabel(tr("首次配对请让 iPhone 与电脑处于可互通的局域网。"))
        notice.setWordWrap(True)
        contents.addWidget(notice)
        cloud = QHBoxLayout()
        self.invite = QLineEdit()
        self.invite.setEchoMode(QLineEdit.EchoMode.Password)
        self.invite.setPlaceholderText(tr("云端密钥／邀请码"))
        cloud.addWidget(self.invite, 1)
        self.enroll = QPushButton(tr("启用云同步"))
        self.enroll.clicked.connect(lambda: (host.enroll(self.invite.text()), self.invite.clear()))
        cloud.addWidget(self.enroll)
        self.remove_key = QPushButton(tr("移除密钥"))
        self.remove_key.clicked.connect(self._remove_key)
        cloud.addWidget(self.remove_key)
        contents.addLayout(cloud)
        pair = QPushButton(tr("生成二维码"))
        pair.clicked.connect(host.pair)
        contents.addWidget(pair, alignment=Qt.AlignmentFlag.AlignLeft)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setFixedSize(240, 240)
        contents.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignLeft)
        self.pending = QLabel()
        self.pending.setWordWrap(True)
        contents.addWidget(self.pending)
        decisions = QHBoxLayout()
        self.reject = QPushButton(tr("拒绝"))
        self.reject.clicked.connect(lambda: host.approve(False))
        self.accept = QPushButton(tr("确认配对"))
        self.accept.clicked.connect(lambda: host.approve(True))
        decisions.addWidget(self.reject)
        decisions.addWidget(self.accept)
        decisions.addStretch()
        self.decisions = QWidget()
        self.decisions.setLayout(decisions)
        contents.addWidget(self.decisions)
        self.readers = QVBoxLayout()
        contents.addLayout(self.readers)
        layout.addWidget(self.content)
        layout.addStretch()
        host.changed.connect(self.refresh)
        host.error.connect(self._error)
        self.refresh()

    def _error(self, message):
        self.status.setText(message)

    def _remove_key(self):
        if QMessageBox.question(self, tr("移除云端密钥？"),
                                tr("云同步将停止，再次使用需添加新的邀请码。局域网配对保留。")) == QMessageBox.StandardButton.Yes:
            self.host.remove_cloud_key()

    def refresh(self):
        host = self.host
        self.content.setVisible(host.enabled)
        self.status.setText(host.status)
        self.enroll.setVisible(not host.cloud_enabled)
        self.invite.setVisible(not host.cloud_enabled)
        self.remove_key.setVisible(host.cloud_enabled)
        if host.qr:
            self.qr.setPixmap(_qr(host.qr))
        else:
            self.qr.clear()
        self.qr.setVisible(bool(host.qr))
        self.pending.setVisible(host.pending is not None)
        self.pending.setText(tr("允许 %s 读取这台电脑的用量与请求内容？") % (host.pending or {}).get("name", ""))
        self.decisions.setVisible(host.pending is not None)
        while self.readers.count():
            item = self.readers.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for reader in host.readers:
            row = QFrame()
            content = QVBoxLayout(row)
            line = QHBoxLayout()
            name = QLabel(reader["name"])
            line.addWidget(name, 1)
            revoke = QPushButton(tr("撤销"))
            revoke.clicked.connect(lambda _checked=False, ident=reader["id"]: host.revoke(ident))
            line.addWidget(revoke)
            content.addLayout(line)
            note = QLineEdit((host.host or {}).get("notes", {}).get(reader["id"], ""))
            note.setPlaceholderText(tr("备注"))
            note.editingFinished.connect(lambda ident=reader["id"], field=note: host.set_note(ident, field.text()))
            content.addWidget(note)
            self.readers.addWidget(row)
