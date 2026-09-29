import AppKit
import SwiftUI
import WidgetKit

// On-demand review exports from production views, using only the isolated mock state.
final class MockGallery {
    let state: AppState
    let window: NSWindow
    let directory: URL
    private var timer: Timer?
    private var index = 0
    private var prepared = false
    private var names = ["overview","logs","logs-detail","usage-activity","usage-trends","usage-threads","subscription","pricing","settings-appearance","settings-data","settings-app","menu","widgets-quota","widgets-request"]
    init(state: AppState,window: NSWindow,directory: URL) {
        self.state = state; self.window = window; self.directory = directory
        if let index = CommandLine.arguments.firstIndex(of:"--gallery-only"), CommandLine.arguments.indices.contains(index+1) {
            let selected = CommandLine.arguments[index+1]
            names = ["settings-menubar", "report-window"].contains(selected) ? [selected] : names.filter {$0 == selected}
        }
    }
    func start() {
        guard state.paths.mock else { return }
        try? FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        timer = Timer.scheduledTimer(withTimeInterval:0.8,repeats:true) { [weak self] _ in self?.advance() }
    }
    private func advance() {
        guard !state.loading, state.quota.week != nil else { return }
        guard index < names.count else {
            timer?.invalidate(); state.stop()
            if ProcessInfo.processInfo.environment["CODEXIO_GALLERY_HOLD"] == "1" { return }
            NSApp.stop(nil); exit(0)
        }
        let name = names[index]
        if !prepared {
            state.theme = name == "menu" ? "dark" : "light"
            NSApp.appearance = NSAppearance(named:name == "menu" ? .darkAqua : .aqua)
            var view: AnyView, size = NSSize(width:1380,height:900)
            switch name {
            case "report-window":
                ReportArtworkResources.prepare(style: .garden)
                let model = UsageReportViewModel()
                model.documents = [.day: UsageReportRenderer.previewData]
                model.style = .garden
                view = AnyView(UsageReportReader(model: model)); size = NSSize(width:540,height:690)
            case "menu": view = AnyView(menuPreview); size = NSSize(width:620,height:940)
            case "widgets-quota": view = AnyView(quotaWidgets); size = NSSize(width:740,height:350)
            case "widgets-request": view = AnyView(requestWidgets); size = NSSize(width:1060,height:530)
            default:
                state.selectedPage = name.hasPrefix("usage") ? "trends" : name.hasPrefix("settings") ? "settings" : name.hasPrefix("logs") ? "logs" : name
                if name.hasPrefix("usage-") { state.usageSection = String(name.dropFirst(6)) == "trends" ? "trend" : String(name.dropFirst(6)) }
                if name.hasPrefix("settings-") { state.settingsSection = String(name.dropFirst(9)) }
                if name == "logs-detail", let row = state.usage.requests.first {
                    let members = state.usage.calls.filter {(row.raw["member_ids"] as? [String] ?? []).contains($0.id)}
                    view = AnyView(MainView(state:state).overlay(alignment:.trailing) {
                        LogDetail(row:row,members:members).background(.regularMaterial,in:RoundedRectangle(cornerRadius:13)).overlay(RoundedRectangle(cornerRadius:13).stroke(.secondary.opacity(0.18))).shadow(color:.black.opacity(0.15),radius:20,y:5).padding(.trailing,90)
                    })
                } else { view = AnyView(MainView(state:state)) }
            }
            if let requested = ProcessInfo.processInfo.environment["CODEXIO_GALLERY_SIZE"] {
                let dimensions = requested.split(separator:"x").compactMap {Double($0)}
                if dimensions.count == 2, dimensions.allSatisfy({$0.isFinite && $0 >= 320 && $0 <= 4000}) {
                    size = NSSize(width:dimensions[0],height:dimensions[1])
                }
            }
            window.contentView = NSHostingView(rootView:view)
            window.setContentSize(size)
            window.center(); window.makeKeyAndOrderFront(nil)
            prepared = true; return
        }
        if let view = window.contentView {
            view.layoutSubtreeIfNeeded(); view.displayIfNeeded()
            if let bitmap = view.bitmapImageRepForCachingDisplay(in:view.bounds) {
                view.cacheDisplay(in:view.bounds,to:bitmap)
                let language = Bundle.main.preferredLocalizations.first?.hasPrefix("zh") == true ? "zh" : "en"
                do { try bitmap.representation(using:.png,properties:[:])?.write(to:directory.appendingPathComponent(name+"-"+language+".png")) }
                catch { fputs(error.localizedDescription+"\n",stderr); exit(1) }
            }
        }
        index += 1; prepared = false
    }
    private var menuPreview: some View {
        ZStack {
            LinearGradient(colors:[Color(red:0.08,green:0.14,blue:0.22),Color(red:0.17,green:0.27,blue:0.39),Color(red:0.05,green:0.07,blue:0.10)],startPoint:.topLeading,endPoint:.bottomTrailing)
            VStack(spacing:22) {
                HStack(spacing:9) { Image(nsImage:MenuBarController.icon()); Text("23%").monospacedDigit() }.font(.system(size:14,weight:.medium)).padding(.horizontal,14).padding(.vertical,8).background(.regularMaterial,in:Capsule())
                MenuBarView(state:state,dismiss:{}).frame(height:770)
            }.padding(30)
        }.environment(\.colorScheme,.dark)
    }
    private var snapshot: Snapshot {
        let now = Date().timeIntervalSince1970
        return Snapshot(schema:1,updated_at:now,request:RequestSnapshot(prompt:L("优化 Codexio 的日志详情与额度显示", "Refine Codexio log details and allowance display"),model:"GPT-6 Astra",reasoning_effort:"Max",service_tier:"priority",model_context_window:828000,cost_usd:0.52,duration_ms:138000,duration_started_at:nil,duration_running:false,input_tokens:32000,output_tokens:6800,cached_input_tokens:18000,cache_hit_rate:0.5625),quota:QuotaSnapshot(applicable:true,five_hour:74,week:23,has_five_hour:true,has_week:true,five_hour_reset_at:now+7200,week_reset_at:now+432000,reset_count:2,updated_at:now),today:TodaySnapshot(cost_usd:23.54,tokens:14179000,requests:18,cache_hit_rate:0.73))
    }
    private func quota(_ style: QuotaStyle) -> some View {
        return QuotaWidgetView(entry:QuotaEntry(date:Date(),snapshot:snapshot,style:style)).preview.frame(width:170,height:170).clipShape(RoundedRectangle(cornerRadius:24))
    }
    private func caption<V:View>(_ title: String,@ViewBuilder content: ()->V) -> some View {
        VStack(alignment:.leading,spacing:14) { content(); Text(title).font(.system(size:13)).foregroundStyle(.secondary) }
    }
    private var quotaWidgets: some View {
        VStack(alignment:.leading,spacing:26) {
            Text(L("额度小组件 · 三种样式", "Allowance widgets · three styles")).font(.system(size:23,weight:.medium))
            HStack(alignment:.top,spacing:36) {
                caption(L("单额度", "Single limit")) { quota(.single) }
                caption(L("双额度", "Dual limits")) { quota(.dual) }
                caption(L("双额度刻度条", "Segmented limits")) { quota(.segmented) }
            }
        }.padding(42).frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading).background(Color(red:0.91,green:0.925,blue:0.94)).environment(\.colorScheme,.light)
    }
    private var requestWidgets: some View {
        VStack(alignment:.leading,spacing:24) {
            Text(L("原请求小组件 · 小 / 中 / 大", "Existing request widgets · small / medium / large")).font(.system(size:23,weight:.medium))
            HStack(alignment:.top,spacing:22) {
                requestWidgetPreview(snapshot,family:.systemSmall).frame(width:170,height:170).clipShape(RoundedRectangle(cornerRadius:24))
                requestWidgetPreview(snapshot,family:.systemMedium).frame(width:364,height:170).clipShape(RoundedRectangle(cornerRadius:24))
                requestWidgetPreview(snapshot,family:.systemLarge).frame(width:364,height:382).clipShape(RoundedRectangle(cornerRadius:24))
            }
        }.padding(32).frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading).background(Color(red:0.91,green:0.925,blue:0.94)).environment(\.colorScheme,.light)
    }
}
