import SwiftUI
import AppKit

struct GlassBackground: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    var body: some View {
        if reduceTransparency || contrast == .increased { RoundedRectangle(cornerRadius:23,style:.continuous).fill(Color(nsColor:.windowBackgroundColor)) }
        else if #available(macOS 26.0, *) { Color.clear.glassEffect(.regular,in:RoundedRectangle(cornerRadius:23,style:.continuous)) }
        else { RoundedRectangle(cornerRadius:23,style:.continuous).fill(.regularMaterial) }
    }
}

struct MenuBarView: View {
    @ObservedObject var state: AppState
    var dismiss: () -> Void
    private var summary: UsageSummary {state.menuSummary}
    var body: some View {
        ScrollView {
            VStack(spacing:12) {
                HStack {
                    Button("Codexio ↗") { dismiss(); state.onOpenWindow?() }.buttonStyle(.plain).font(.system(size:13,weight:.medium)).foregroundStyle(.secondary)
                    Spacer()
                    ScanStamp(clock:state.menuClient == .codex ? state.menuQuotaClock : state.claudeQuotaClock).font(.system(size:9)).foregroundStyle(.secondary)
                }.padding(.horizontal,6).padding(.bottom,3)
                card(padding:5) {
                    HStack(alignment:.top,spacing:5) {
                        ForEach(DesktopClient.allCases,id:\.self) {client in
                            Button {state.selectMenuClient(client)} label: {
                                VStack(alignment:.leading,spacing:16) {
                                    HStack(spacing:6) {
                                        WidgetAgentLogo(agent:client.widgetAgent).frame(width:18,height:18)
                                        Text(client.surfaceTitle).font(.system(size:14,weight:.semibold))
                                        Spacer(minLength:2)
                                        Image(systemName:state.menuClient == client ? "checkmark.circle.fill" : "circle")
                                            .font(.system(size:12)).foregroundStyle(state.menuClient == client ? Color.blue : Color.secondary.opacity(0.3))
                                    }
                                    meter(state.menuWindow(client,week:false),title:L("5 小时额度", "5-hour limit"),week:false)
                                    meter(state.menuWindow(client,week:true),title:L("周额度", "Weekly limit"),week:true)
                                }.padding(.horizontal,10).padding(.vertical,12).frame(maxWidth:.infinity,alignment:.leading)
                                    .background(state.menuClient == client ? Color.blue.opacity(0.055) : Color.clear,in:RoundedRectangle(cornerRadius:12))
                                    .overlay(RoundedRectangle(cornerRadius:12).stroke(state.menuClient == client ? Color.blue.opacity(0.22) : .clear,lineWidth:0.7))
                                    .contentShape(RoundedRectangle(cornerRadius:12))
                            }.buttonStyle(.plain).accessibilityLabel(client.surfaceTitle)
                                .accessibilityAddTraits(state.menuClient == client ? .isSelected : [])
                        }
                    }
                }
                if state.menuClient == .codex {
                    if state.menuQuota.retained {StatusNote(text:L("显示上次成功获取的额度", "Showing the last successfully retrieved limits"))}
                    if let error = state.quota.error {StatusNote(text:error)}
                } else if let warning = state.claudeWarning {StatusNote(text:warning)}
                card {
                    HStack { Text(L("今天", "Today")).fontWeight(.medium); Spacer(); Text(Date().formatted(.dateTime.month().day())).foregroundStyle(.secondary) }.font(.system(size:12))
                    HStack(alignment:.firstTextBaseline) {
                        VStack(alignment:.leading,spacing:5) { Text(compact(summary.tokens.map(Double.init))).font(.system(size:28,weight:.medium)).monospacedDigit(); Text("Token").font(.system(size:10)).foregroundStyle(.secondary) }
                        Spacer()
                        VStack(alignment:.trailing,spacing:5) { Text(money(summary.cost)).font(.system(size:28,weight:.medium)).monospacedDigit(); Text(L("费用", "Cost")).font(.system(size:10)).foregroundStyle(.secondary) }
                    }
                    GeometryReader { geometry in
                        let total = max(1,summary.input+summary.output)
                        HStack(spacing:1) {
                            Color.blue.opacity(0.45).frame(width:max(0,geometry.size.width*Double(summary.input-summary.cached)/Double(total)))
                            Color.blue.opacity(0.8).frame(width:max(0,geometry.size.width*Double(summary.cached)/Double(total)))
                            Color.blue.frame(width:summary.tokens == nil ? 0 : geometry.size.width*Double(summary.output)/Double(total))
                            Spacer(minLength:0)
                        }.background(Color.secondary.opacity(0.12)).clipShape(Capsule())
                    }.frame(height:5)
                    VStack(spacing:9) {
                        tokenRow(L("未缓存输入", "Uncached input"),summary.tokens == nil ? nil : max(0,summary.input-summary.cached))
                        tokenRow(L("缓存输入", "Cached input"),summary.tokens == nil ? nil : summary.cached)
                        tokenRow(L("输出", "Output"),summary.tokens == nil ? nil : summary.output)
                    }
                }
                card {
                    HStack { Text(L("最近 7 天", "Last 7 days")).fontWeight(.medium); Spacer(); Text(L("费用", "Cost")).foregroundStyle(.secondary) }.font(.system(size:12))
                    TrendChart(days:state.menuDays,compactStyle:true)
                }
            }.padding(14)
        }.scrollContentBackground(.hidden).frame(width:420).background { GlassBackground() }
            .clipShape(RoundedRectangle(cornerRadius:23,style:.continuous))
            .preferredColorScheme(state.theme == "dark" ? .dark : state.theme == "light" ? .light : nil)
    }
    private func card<Content:View>(padding: CGFloat = 18,@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment:.leading,spacing:17,content:content).padding(padding).frame(maxWidth:.infinity,alignment:.leading).background(Color.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:16)).overlay(RoundedRectangle(cornerRadius:16).stroke(.primary.opacity(0.12),lineWidth:0.5))
    }
    private func meter(_ window: QuotaWindow?,title: String,week: Bool) -> some View {
        VStack(alignment:.leading,spacing:5) {
            HStack(alignment:.firstTextBaseline,spacing:2) {
                Text(title).font(.system(size:11)).lineLimit(1)
                Spacer(minLength:2)
                Text(percent(window?.remaining)).font(.system(size:19,weight:.semibold)).monospacedDigit().fixedSize()
                if window?.remaining != nil {Text(L("剩余", "remaining")).font(.system(size:9)).foregroundStyle(.secondary)}
            }
            GeometryReader {geometry in
                Capsule().fill(Color.secondary.opacity(0.16)).overlay(alignment:.leading) {
                    Capsule().fill(Color.blue).frame(width:geometry.size.width*max(0,min(100,window?.remaining ?? 0))/100)
                }
            }.frame(height:6)
            HStack(spacing:3) {Text(L("重置", "Resets")); Text(logDateText(window?.reset,timeOnly:!week,showYear:window?.reset.map {Calendar.current.component(.year,from:$0) != Calendar.current.component(.year,from:Date())} ?? false)).monospacedDigit()}
                .font(.system(size:9)).foregroundStyle(.secondary).lineLimit(1).help(dateText(window?.reset))
        }
    }
    private func tokenRow(_ name: String,_ value: Int?) -> some View { HStack { Circle().fill(.blue.opacity(0.7)).frame(width:4,height:4); Text(name).foregroundStyle(.secondary); Spacer(); Text(compact(value.map(Double.init))).monospacedDigit() }.font(.system(size:11)) }
}

private final class StatusPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { orderOut(nil) }
}

final class MenuBarController {
    private let state: AppState
    private var item: NSStatusItem?
    private var panel: NSPanel?
    private var localMonitor: Any?
    private var globalMonitor: Any?
    private var readout: MenuBarReadoutHost?
    private var frameObserver: NSObjectProtocol?
    private var displayed = ""
    init(_ state: AppState) { self.state = state; update() }
    func update() {
        if !state.menuVisible {
            removeItem(); close(); return
        }
        guard Branding.smallImagesReady else { return }
        if item == nil {
            item = NSStatusBar.system.statusItem(withLength:NSStatusItem.variableLength)
            item?.button?.target = self; item?.button?.action = #selector(toggle)
            if let button = item?.button {
                button.image = nil; button.title = ""
                let view = MenuBarReadoutHost(rootView:MenuBarReadout(state:state))
                button.addSubview(view); readout = view
                button.postsFrameChangedNotifications = true
                frameObserver = NotificationCenter.default.addObserver(forName:NSView.frameDidChangeNotification,object:button,queue:.main) { [weak self] _ in self?.layoutReadout() }
            }
        }
        let text = MenuBarField.text(state), running = state.menuFields.contains("task") && state.menuTaskRunning == true
        let key = state.menuClient.rawValue+"|"+state.menuFields.joined(separator:",")+"|"+text+"|"+String(running)
        guard key != displayed else { return }; displayed = key
        readout?.rootView = MenuBarReadout(state:state)
        readout?.layoutSubtreeIfNeeded()
        item?.length = state.menuFields == ["logo"] ? NSStatusItem.squareLength : ceil(readout?.fittingSize.width ?? 18)+12
        item?.button?.setAccessibilityLabel("Codexio · "+state.menuFields.map {MenuBarField.title($0)+" "+MenuBarField.value($0,state:state)}.joined(separator:" · "))
        layoutReadout()
    }
    private func layoutReadout() {
        guard let button = item?.button, let readout else { return }
        let size = readout.fittingSize
        readout.frame = NSRect(x:(button.bounds.width-size.width)/2,y:(button.bounds.height-size.height)/2,width:size.width,height:size.height)
    }
    private func removeItem() {
        readout?.removeFromSuperview(); readout = nil
        if let frameObserver { NotificationCenter.default.removeObserver(frameObserver) }; frameObserver = nil
        if let item { NSStatusBar.system.removeStatusItem(item) }; item = nil; displayed = ""
    }
    @objc func toggle() {
        if panel?.isVisible == true { close(); return }
        guard let button = item?.button, let window = button.window else { return }
        let screen = window.screen ?? NSScreen.main
        let height = min(724,(screen?.visibleFrame.height ?? 900)-36)
        let panel = StatusPanel(contentRect:NSRect(x:0,y:0,width:420,height:height),styleMask:[.borderless,.nonactivatingPanel],backing:.buffered,defer:false)
        panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = true; panel.level = .popUpMenu; panel.hidesOnDeactivate = false; panel.isReleasedWhenClosed = false
        panel.contentView = NSHostingView(rootView:MenuBarView(state:state,dismiss:{[weak self] in self?.close()}))
        let anchor = window.convertToScreen(button.convert(button.bounds,to:nil)), frame = screen?.visibleFrame ?? anchor
        let x = max(frame.minX+8,min(anchor.midX-210,frame.maxX-428))
        panel.setFrameOrigin(NSPoint(x:x,y:max(frame.minY+8,anchor.minY-height-7)))
        self.panel = panel; panel.makeKeyAndOrderFront(nil)
        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching:[.leftMouseDown,.rightMouseDown]) { [weak self] _ in self?.close() }
        localMonitor = NSEvent.addLocalMonitorForEvents(matching:[.leftMouseDown,.rightMouseDown,.keyDown]) { [weak self] event in
            guard let self else { return event }
            if event.type == .keyDown && event.keyCode == 53 { self.close(); return nil }
            if event.window !== self.panel && event.window !== self.item?.button?.window { self.close() }
            return event
        }
    }
    func close() {
        panel?.orderOut(nil); panel = nil
        if let localMonitor { NSEvent.removeMonitor(localMonitor) }; localMonitor = nil
        if let globalMonitor { NSEvent.removeMonitor(globalMonitor) }; globalMonitor = nil
    }
    static func icon() -> NSImage {
        Branding.menuIcon()
    }
    deinit { close(); removeItem() }
}
