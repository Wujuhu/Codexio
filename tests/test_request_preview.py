"""User-visible request text and interactive hover previews."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QPushButton, QWidget

from codexio.analytics_config import DEFAULT_NAVIGATION_ORDER, NAVIGATION_PAGES, load_analytics_config, save_analytics_config
from codexio.dashboard import Dashboard, LineLimitedText, RequestPreviewText
from codexio.desktop_widgets import NavigationList, preview_title
from codexio.settings import AppSettings
from codexio.usage_collector import _user_event_content, _user_preview


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("value,expected", [
    (r"[$impeccable](C:\Users\WJH\.codex\skills\impeccable\SKILL.md) 请调整布局。", "@Impeccable 请调整布局。"),
    ("请使用 [$app-name](app://connector_id) 检查页面。", "请使用 @app-name 检查页面。"),
    ("[@Impeccable](skill://impeccable) 处理 [文档](https://example.com/doc)", "@Impeccable 处理 [文档](https://example.com/doc)"),
    (r"@Impeccable (C:\Users\WJH\.codex\skills\impeccable\SKILL.md) 帮我整理", "@Impeccable 帮我整理"),
    ('前面 [image 1] 继续文字 <image name=[Image #2] path="C:/temp/b.png">[image 2]</image> 最后一句。', "前面 继续文字 最后一句。\n[image] x 2"),
    ("![截图](C:/temp/with(image).png) 对比图片 [image] x 2 后继续说明", "对比图片 后继续说明\n[image] x 3"),
    ("[image 1]", "[image] x 1"),
    ("# Files mentioned by the user:\n\n## image.png: C:/temp/image.png\n\n## My request:\n请检查图中内容。[image 1]", "请检查图中内容。\n[image] x 1"),
    ("<recommended_plugins>catalog</recommended_plugins><environment_context>cwd</environment_context>检查布局", "检查布局"),
    ("请保留 C:/project/app.py、@同事，以及 [参考](https://example.com/ref)。", "请保留 C:/project/app.py、@同事，以及 [参考](https://example.com/ref)。"),
])
def test_preview_keeps_only_question_short_mentions_and_trailing_image_count(value, expected):
    assert _user_preview(value) == expected
    assert _user_preview(expected) == expected


def test_native_and_xml_image_representations_are_counted_once_before_truncation():
    value = [dict(type="input_text", text="<recommended_plugins>" + "generated " * 500 + "</recommended_plugins>"),
             dict(type="input_text", text="[$impeccable](C:/skills/impeccable/SKILL.md) " + "需要调整的真实问题 " * 100)]
    for number in range(2):
        value.extend([dict(type="input_text", text=f'<image name=[Image #{number + 1}] path="C:/image.png">'),
                      dict(type="input_image", image_url="data:image/png;base64,unused"),
                      dict(type="input_text", text="</image>")])
    preview = _user_preview(value)
    assert preview.startswith("@Impeccable 需要调整的真实问题")
    assert len(preview) <= 600 and preview.endswith("…\n[image] x 2")
    assert not any(text in preview for text in ("generated", "C:/", "base64", "SKILL.md", "<image"))
    assert _user_preview(preview) == preview


def test_legacy_user_image_arrays_and_structured_inputs_have_the_same_preview():
    payload = dict(message="检查图片", images=["image-a", "image-b"], local_images=[])
    assert _user_preview(_user_event_content(payload)) == "检查图片\n[image] x 2"
    payload["message"] = [dict(type="text", text="检查图片"), dict(type="local_image", path="image-a"), dict(type="image", path="image-b")]
    assert _user_preview(_user_event_content(payload)) == "检查图片\n[image] x 2"
    assert _user_preview([dict(type="skill", name="impeccable", path="C:/skills/impeccable/SKILL.md"),
                          dict(type="text", text="检查布局")]) == "@Impeccable 检查布局"


def sample_rows(count=21):
    now = datetime.now(timezone.utc)
    prompt = "[$impeccable](C:/skills/impeccable/SKILL.md) " + "请调整用户请求预览的信息顺序和交互。" * 40 + " [image 1]"
    return [dict(id=f"call-{i:03d}", session_id="session-1", session_title="UI设计", turn_id="turn-1",
                 timestamp=(now - timedelta(minutes=2) + timedelta(seconds=i)).isoformat(),
                 model="gpt-6-astra", service_tier="default", input_tokens=1000, cached_input_tokens=100,
                 output_tokens=100, total_tokens=1100, cost_usd=.02, pricing_status="priced",
                 prompt_preview=prompt, output_preview="已经完成页面调整。" * 20) for i in range(count)]


@pytest.fixture
def window(app):
    result = Dashboard(AppSettings(), {"theme": "dark"}, {})
    result.resize(1280, 850)
    result.apply_data({"records": sample_rows()})
    result.open_page("logs", "all")
    app.processEvents()
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_default_navigation_order_migrates_once_and_then_preserves_drag_choices(app, tmp_path):
    path = tmp_path / "analytics.json"
    assert load_analytics_config(path)["navigation_order"] == list(DEFAULT_NAVIGATION_ORDER)
    path.write_text(json.dumps(dict(navigation_order=list(NAVIGATION_PAGES), theme="dark")), encoding="utf-8")
    config = load_analytics_config(path)
    assert config["navigation_order"] == list(DEFAULT_NAVIGATION_ORDER)
    nav = NavigationList()
    assert nav.order() == list(DEFAULT_NAVIGATION_ORDER)
    nav.move_page("settings", 1)
    config["navigation_order"] = nav.order()
    save_analytics_config(config, path)
    assert load_analytics_config(path)["navigation_order"] == nav.order()
    config["navigation_order"] = list(NAVIGATION_PAGES)
    save_analytics_config(config, path)
    assert load_analytics_config(path)["navigation_order"] == list(NAVIGATION_PAGES)
    nav.close()


@pytest.mark.parametrize("width", [920, 1280, 1600])
def test_inspector_uses_session_heading_three_message_lines_and_an_extra_image_line(app, window, width):
    window.resize(width, 660)
    window._inspect_log_row(0)
    app.processEvents()
    content = window._inspector_scroll.widget()
    sections = [content.layout().itemAt(i).widget() for i in range(content.layout().count()) if content.layout().itemAt(i).widget()]
    assert [section.objectName() for section in sections] == ["inspectorRequestHeader", "inspectorUsageSummary", "inspectorReplyPreview", "inspectorIdentifiers", "inspectorComposedCalls"]
    assert all(before.geometry().bottom() < after.geometry().top() for before, after in zip(sections, sections[1:]))
    title = window._log_drawer.inspector.findChild(LineLimitedText, "inspectorSessionTitle")
    request = content.findChild(RequestPreviewText)
    assert title is window._inspector_heading and title.text == "UI设计"
    assert title.font().pixelSize() >= request.body.font().pixelSize() + 4
    assert not content.isAncestorOf(title)
    assert not any(label.text() in ("用户请求", "UI设计") for label in content.findChildren(QLabel))
    assert request.body.text.startswith("@Impeccable") and request.images.text == "[image] x 1"
    assert request.body.max_lines == 3 and request.images.max_lines == 1
    assert request.body.last_line_count == 3
    assert request.body.font().weight() == QFont.Weight.Bold and request.images.font().weight() == QFont.Weight.Bold
    assert "C:/" not in request.text and "SKILL.md" not in request.text
    assert request.geometry().bottom() < sections[0].height()
    assert [field.accessibleName() for field in sections[3].findChildren(QLineEdit)] == ["Turn ID", "Session ID"]
    assert any(label.text() == "调用组成 · 21" for label in sections[4].findChildren(QLabel))
    assert not any(button.text() in ("上一页", "下一页") for button in content.findChildren(QPushButton))
    assert any(label.text() == "gpt-6-astra × 21" for label in sections[4].findChildren(QLabel))
    assert window._inspector_scroll.horizontalScrollBar().maximum() == 0


def test_short_message_does_not_reserve_three_lines_and_resizes_with_content(app, window):
    record = dict(window._rendered_log_rows[0], prompt_preview="检查布局。[image 1]")
    window._show_inspector(record)
    app.processEvents()
    request = window._inspector_scroll.widget().findChild(RequestPreviewText)
    assert request.body.last_line_count == 1 and request.images.last_line_count == 1
    short_height = request.height()
    assert request.body.height() < request.body.fontMetrics().lineSpacing() * 2
    assert request.images.y() == request.body.height()
    record["prompt_preview"] = "请检查长问题的显示以及图片数量。" * 20 + "[image 1]"
    window._show_inspector(record)
    app.processEvents()
    request = window._inspector_scroll.widget().findChild(RequestPreviewText)
    assert request.body.last_line_count == 3 and request.height() > short_height
    record["prompt_preview"] = "[image 1]"
    window._show_inspector(record)
    app.processEvents()
    request = window._inspector_scroll.widget().findChild(RequestPreviewText)
    assert request.body is None and request.images.text == "[image] x 1"
    assert request.height() < short_height
    # The same widget recomputes its height when the available width changes.
    label = LineLimitedText("需要根据宽度重新测量的问题内容。" * 3, max_lines=3, fit_content=True)
    label.resize(1000, label.height())
    label.show()
    app.processEvents()
    wide_height = label.height()
    label.resize(180, label.height())
    app.processEvents()
    assert label.last_line_count == 3 and label.height() > wide_height
    label.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_inspector_surface_is_visibly_distinct_from_ledger_and_survives_theme_changes(app, window, theme):
    from codexio.theme import theme_colors
    window._inspect_log_row(0)
    window.config_updated({"theme": theme})
    app.processEvents()
    inspector = window._log_drawer.inspector
    image = inspector.grab().toImage()
    colors = theme_colors(theme)
    assert colors["inspector_surface"] != colors["surface"]
    assert image.pixelColor(8, inspector.height() // 2).name() == colors["inspector_surface"].lower()
    assert window._inspector_heading.text == "UI设计"
    content = window._inspector_scroll.widget()
    groups = [row for row in content.findChildren(QFrame) if row.property("callSummary")]
    assert len(groups) == 1
    request = content.findChild(RequestPreviewText)
    assert window._inspector_heading.font().pixelSize() >= request.body.font().pixelSize() + 4


def hover_first(window):
    table = window._log_table
    QTest.mouseMove(table.viewport(), table.visualItemRect(table.item(0, 1)).center())
    QTest.mouseMove(table.viewport(), table.visualItemRect(table.item(0, 0)).center())
    QTest.qWait(280)


@pytest.mark.parametrize("width", [920, 1280, 1600])
def test_details_default_to_first_row_and_keep_geometry_when_cleared(app, window, width):
    window.resize(width, 660)
    app.processEvents()
    host = window._log_drawer
    assert host.inspector.isVisible()
    assert host.primary.geometry().right() < host.inspector.geometry().left()
    assert window._inspector_origin == window._rendered_log_rows[0]["id"]
    assert window._log_table.currentRow() == 0
    assert window._inspector_stack.currentWidget() is window._inspector_scroll
    before = host.primary.geometry(), host.inspector.geometry()
    window._close_inspector()
    assert window._inspector_stack.currentWidget() is window._inspector_empty
    assert "点击左侧请求" in window._inspector_empty.text()
    hover_first(window)
    assert window._inspected_record is None
    point = window._log_table.visualItemRect(window._log_table.item(0, 0)).center()
    QTest.mouseClick(window._log_table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert window._inspected_record is not None
    assert window._inspector_stack.currentWidget() is window._inspector_scroll
    assert (host.primary.geometry(), host.inspector.geometry()) == before


def test_details_keep_scroll_on_hover_and_same_row_click_and_escape_returns_empty(app, window):
    table, scroll = window._log_table, window._inspector_scroll
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(table.item(0, 0)).center())
    assert window._log_drawer.inspector.isVisible()
    assert table.item(0, 0).toolTip() == ""
    ident = window._inspected_record["id"]
    QTest.mouseMove(table.viewport(), table.visualItemRect(table.item(0, 2)).center())
    QTest.mouseMove(scroll.viewport(), QPoint(50, 70))
    QTest.qWait(280)
    assert window._log_drawer.inspector.isVisible() and window._inspected_record["id"] == ident
    wheel = QWheelEvent(QPointF(50, 70), QPointF(scroll.viewport().mapToGlobal(QPoint(50, 70))),
                        QPoint(), QPoint(0, -360), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(scroll.viewport(), wheel)
    app.processEvents()
    assert scroll.verticalScrollBar().value() > 0 and window._log_drawer.inspector.isVisible()
    # Returning over the same request must not reset the scroll position.
    previous = scroll.verticalScrollBar().value()
    hover_first(window)
    assert scroll.verticalScrollBar().value() == previous
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(table.item(0, 0)).center())
    assert window._inspected_record["id"] == ident and scroll.verticalScrollBar().value() == previous
    QTest.keyClick(table, Qt.Key.Key_Escape)
    assert window._log_drawer.inspector.isVisible() and not window._escape_inspector.isEnabled()
    assert window._inspected_record is None and window._inspector_empty.isVisible()


def test_details_follow_selected_id_through_filters_refresh_and_page_changes(app, window):
    window._inspect_log_row(0)
    ident = window._inspected_record["id"]
    window._log_search.setText("UI设计")
    QTest.qWait(260)
    assert window._inspected_record["id"] == ident
    window.open_page("overview")
    assert not window._escape_inspector.isEnabled()
    window.open_page("logs", "all")
    assert window._log_drawer.inspector.isVisible() and window._inspected_record["id"] == ident
    window.hide()
    window.show()
    app.processEvents()
    assert window._inspected_record["id"] == ident
    updated = [dict(row, session_id="session-replaced", turn_id="turn-replaced") for row in sample_rows()]
    window.apply_data({"records": updated})
    assert window._inspected_record["session_id"] == "session-replaced"
    assert window._log_table.currentRow() == 0
    window._log_search.setText("没有匹配的请求")
    QTest.qWait(260)
    assert window._log_table.rowCount() == 0 and window._inspected_record is None
    assert window._log_drawer.inspector.isVisible() and window._inspector_empty.isVisible()


def test_missing_question_does_not_present_session_title_as_the_users_words(app, window):
    record = dict(window._rendered_log_rows[0], prompt_preview="<recommended_plugins>catalog</recommended_plugins>")
    window._show_inspector(record)
    request = window._inspector_scroll.widget().findChild(RequestPreviewText)
    assert request.text == "未记录用户输入"
    assert preview_title(record) == "会话：UI设计"


def test_unassigned_call_details_keep_the_selected_table_identity_during_refresh(app, window):
    row = dict(sample_rows(1)[0], turn_id="")
    window.apply_data({"records": [row]})
    assert window._rendered_log_rows[0]["record_kind"] == "unassigned"
    window._inspect_log_row(0)
    origin = window._inspector_origin
    assert origin != window._inspected_record["id"]
    window.apply_data({"records": [dict(row, cost_usd=.5, output_tokens=300)]})
    assert window._inspector_origin == origin
    assert window._inspected_record["cost_usd"] == .5
    assert window._inspector_stack.currentWidget() is window._inspector_scroll
