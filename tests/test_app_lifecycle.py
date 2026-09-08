from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_close_to_tray_then_explicit_exit_does_not_leave_event_loop_running(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", LOCALAPPDATA=str(tmp_path))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    script = '''
from PySide6.QtWidgets import QApplication as BaseApp
from PySide6.QtCore import QTimer
import aiquota.__main__ as entry
import aiquota.dashboard as dashboard_module
original = dashboard_module.Dashboard
windows = []
def create(*args, **kwargs):
    window = original(*args, **kwargs)
    windows.append(window)
    return window
class TestApp(BaseApp):
    def exec(self):
        def finish():
            window = windows[0]
            quit_callback = window._callbacks["quit"]
            window.close()
            assert not window.isVisible()
            QTimer.singleShot(200, quit_callback)
        QTimer.singleShot(200, finish)
        return super().exec()
dashboard_module.Dashboard = create
entry.QApplication = TestApp
assert entry.main(["--mock"]) == 0
print("lifecycle-ok")
'''
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"lifecycle-ok" in result.stdout
