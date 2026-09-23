import WidgetKit

@_cdecl("codexio_widget_reload")
public func codexioWidgetReload() {
    WidgetCenter.shared.reloadTimelines(ofKind: "com.wujuhu.codexio.request")
}
