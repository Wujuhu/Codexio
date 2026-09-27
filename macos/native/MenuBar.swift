import SwiftUI
import AppKit

struct GlassBackground: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    var body: some View {
        if reduceTransparency || contrast == .increased { RoundedRectangle(cornerRadius:23).fill(Color(nsColor:.windowBackgroundColor)) }
        else if #available(macOS 26.0, *) { Color.clear.glassEffect(.regular,in:RoundedRectangle(cornerRadius:23)) }
        else { RoundedRectangle(cornerRadius:23).fill(.regularMaterial) }
    }
}

struct MenuBarView: View {
    @ObservedObject var state: AppState
    var dismiss: () -> Void
    private var summary: UsageSummary { state.usage.summaries["today"] ?? UsageSummary() }
    var body: some View {
        ScrollView {
            VStack(spacing:12) {
                HStack {
                    Button("Codexio ↗") { dismiss(); state.onOpenWindow?() }.buttonStyle(.plain).font(.system(size:13,weight:.medium)).foregroundStyle(.secondary)
                    Spacer()
                    Button { state.refreshQuota(); state.refreshUsage() } label: { Image(systemName:"arrow.clockwise").frame(width:24,height:24) }.buttonStyle(.borderless).accessibilityLabel(L("刷新", "Refresh"))
                }.padding(.horizontal,6).padding(.bottom,3)
                card {
                    HStack { Text("Codex").font(.system(size:18,weight:.semibold)); Spacer(); ScanStamp(clock:state.quotaClock,relative:true).font(.system(size:10)).foregroundStyle(.secondary) }
                    meter(state.quota.five,title:L("5 小时额度", "5-hour limit"))
                    meter(state.quota.week,title:L("周额度", "Weekly limit"))
                    if let error = state.quota.error { StatusNote(text:error) }
                }
                card {
                    HStack { Text(L("今天", "Today")).fontWeight(.medium); Spacer(); Text(Date().formatted(.dateTime.month().day())).foregroundStyle(.secondary) }.font(.system(size:12))
                    HStack(alignment:.firstTextBaseline) {
                        VStack(alignment:.leading,spacing:5) { Text(compact(summary.tokens.map(Double.init))).font(.system(size:28,weight:.medium)).monospacedDigit(); Text("Token").font(.system(size:10)).foregroundStyle(.secondary) }
                        Spacer()
                        VStack(alignment:.trailing,spacing:5) { Text(money(summary.cost)).font(.system(size:22,weight:.medium)).monospacedDigit(); Text(L("预估费用", "Estimated cost")).font(.system(size:10)).foregroundStyle(.secondary) }
                    }
                    GeometryReader { geometry in
                        let total = max(1,summary.input+summary.output)
                        HStack(spacing:1) {
                            Color.blue.opacity(0.45).frame(width:max(0,geometry.size.width*Double(summary.input-summary.cached)/Double(total)))
                            Color.blue.opacity(0.8).frame(width:max(0,geometry.size.width*Double(summary.cached)/Double(total)))
                            Color.blue
                        }.clipShape(Capsule())
                    }.frame(height:5)
                    VStack(spacing:9) {
                        tokenRow(L("未缓存输入", "Uncached input"),summary.input-summary.cached)
                        tokenRow(L("缓存输入", "Cached input"),summary.cached)
                        tokenRow(L("输出", "Output"),summary.output)
                    }
                }
                card {
                    HStack { Text(L("最近 7 天", "Last 7 days")).fontWeight(.medium); Spacer(); Text(L("费用估算", "Estimated cost")).foregroundStyle(.secondary) }.font(.system(size:12))
                    TrendChart(days:Array(state.usage.activity.days.suffix(7)),compactStyle:true)
                }
            }.padding(14)
        }.frame(width:420).background { GlassBackground() }
            .preferredColorScheme(state.theme == "dark" ? .dark : state.theme == "light" ? .light : nil)
    }
    private func card<Content:View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment:.leading,spacing:17,content:content).padding(18).frame(maxWidth:.infinity,alignment:.leading).background(Color.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:16)).overlay(RoundedRectangle(cornerRadius:16).stroke(.primary.opacity(0.12),lineWidth:0.5))
    }
    private func meter(_ window: QuotaWindow?,title: String) -> some View {
        VStack(spacing:9) {
            HStack { Text(title).font(.system(size:13)); Spacer(); Text(percent(state.quota.fresh ? window?.remaining : nil)).font(.system(size:17,weight:.medium)).monospacedDigit(); Text(L("剩余", "remaining")).font(.system(size:11)).foregroundStyle(.secondary) }
            SegmentedMeter(value:state.quota.fresh ? window?.remaining : nil)
            HStack { Text(L("重置时间", "Resets at")); Spacer(); Text(dateText(window?.reset,timeOnly:window?.minutes == 300)) }.font(.system(size:11)).foregroundStyle(.secondary)
        }
    }
    private func tokenRow(_ name: String,_ value: Int) -> some View { HStack { Circle().fill(.blue.opacity(0.7)).frame(width:4,height:4); Text(name).foregroundStyle(.secondary); Spacer(); Text(compact(Double(value))).monospacedDigit() }.font(.system(size:11)) }
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
    init(_ state: AppState) { self.state = state; update() }
    func update() {
        if !state.menuVisible {
            if let item { NSStatusBar.system.removeStatusItem(item) }; item = nil; close(); return
        }
        if item == nil {
            item = NSStatusBar.system.statusItem(withLength:NSStatusItem.variableLength)
            item?.button?.target = self; item?.button?.action = #selector(toggle)
            item?.button?.image = Self.icon(); item?.button?.imagePosition = .imageLeft
            item?.button?.font = .monospacedDigitSystemFont(ofSize:12,weight:.medium)
        }
        let value = state.menuContent == "five" ? state.quota.five?.remaining : state.quota.week?.remaining
        item?.length = state.menuContent == "icon" ? NSStatusItem.squareLength : 73
        item?.button?.title = state.menuContent == "icon" ? "" : " "+percent(state.quota.fresh ? value : nil)
        item?.button?.setAccessibilityLabel("Codex · "+(state.menuContent == "five" ? L("5 小时额度剩余", "5-hour allowance remaining") : L("周额度剩余", "Weekly allowance remaining"))+" "+percent(state.quota.fresh ? value : nil))
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
        let image = NSImage(size:NSSize(width:18,height:18),flipped:false) { _ in
            NSColor.labelColor.setStroke()
            let path = NSBezierPath(); path.lineWidth = 1.8; path.lineCapStyle = .round; path.lineJoinStyle = .round
            path.move(to:NSPoint(x:6,y:15)); path.line(to:NSPoint(x:2,y:9)); path.line(to:NSPoint(x:6,y:3)); path.move(to:NSPoint(x:12,y:15)); path.line(to:NSPoint(x:16,y:9)); path.line(to:NSPoint(x:12,y:3)); path.stroke()
            let cross = NSBezierPath(); cross.lineWidth = 1.2; cross.lineCapStyle = .round
            cross.move(to:NSPoint(x:7,y:11)); cross.line(to:NSPoint(x:11,y:7)); cross.move(to:NSPoint(x:7,y:7)); cross.line(to:NSPoint(x:11,y:11)); cross.stroke(); return true
        }
        image.isTemplate = true; return image
    }
    deinit { close(); if let item { NSStatusBar.system.removeStatusItem(item) } }
}
