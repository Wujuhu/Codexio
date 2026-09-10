from __future__ import annotations
import os
from datetime import datetime, timedelta
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel
from codexio.dashboard import Dashboard
from codexio.rate_limits import format_reset_date, format_reset_time
from codexio.settings import AppSettings

@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])

def test_absolute_reset_date_has_weekday_before_time():
    value=datetime(2026,12,31,23,59).astimezone()
    assert format_reset_date(value)=="2026/12/31 周四 23:59"
    assert format_reset_date(value,split_time=True)=="2026/12/31 周四\n23:59"
    assert "12月31日 周四 23:59" == format_reset_time(value,datetime(2026,9,9).astimezone())

@pytest.mark.parametrize("width",[920,1280])
def test_main_and_preview_scrollbars_reach_right_gutter_without_dismissing(app,width):
    now=datetime.now().astimezone()
    rows=[dict(id=str(i),session_id=str(i),turn_id="turn",timestamp=(now-timedelta(minutes=i)).isoformat(),
               input_tokens=100,output_tokens=10,total_tokens=110,cost_usd=1,pricing_status="priced",model="model",
               prompt_preview="请求",output_preview="用于检查滚动的回复。"*80) for i in range(130)]
    window=Dashboard(AppSettings(),{}, {})
    window.resize(width,660)
    window.apply_data(dict(records=rows))
    for name in ("overview","trends","subscription"):
        window.open_page(name)
        app.processEvents()
        area=window._pages[name]
        surface=window.findChild(QFrame,"contentSurface")
        bar=area.verticalScrollBar()
        assert bar.isVisible()
        assert surface.width()-1-bar.mapTo(surface,QPoint(bar.width()-1,0)).x() <= 10
    window.open_page("logs","all")
    window._inspect_log_row(0)
    app.processEvents()
    inspector=window._log_drawer.inspector
    bar=window._inspector_scroll.verticalScrollBar()
    assert bar.isVisible()
    assert inspector.width()-1-bar.mapTo(inspector,QPoint(bar.width()-1,0)).x() <= 6
    QTest.mouseClick(bar,Qt.MouseButton.LeftButton,pos=QPoint(bar.width()//2,bar.height()-8))
    app.processEvents()
    assert bar.value()>0 and window._log_drawer.inspector.isVisible()
    window.close()

def test_subscription_shows_reference_projection_without_right_explanation(app):
    now=datetime.now().astimezone()
    value=dict(plan_type="pro",reset_at=(now+timedelta(days=7)).timestamp(),start=(now-timedelta(hours=3)).isoformat(),
               end=(now-timedelta(hours=1)).isoformat(),delta_percent=2,estimated_total_usd=500,
               estimated_remaining_usd=400,status="estimated_prices")
    window=Dashboard(AppSettings(),{}, {})
    window.apply_data(dict(records=[],weekly_estimates=[value]))
    window.open_page("subscription")
    assert window._estimate_value.text()=="$500.00" and window._estimate_note.text()=="已采样 2 个百分点"
    assert window._subscription_history.item(0,4).text()=="参考估值"
    assert "周" in window._subscription_history.item(0,1).text()
    assert not any("基于已观测" in label.text() or "不代表固定" in label.text() for label in window._pages["subscription"].findChildren(QLabel))
    window.close()
