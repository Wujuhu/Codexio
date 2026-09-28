import SwiftUI
import AppKit

enum Pages {
    static let all = ["overview","logs","trends","subscription","pricing","settings"]
    static func title(_ page: String) -> String {
        switch page {
        case "overview": return L("概览", "Overview")
        case "logs": return L("日志", "Logs")
        case "trends": return L("用量", "Usage")
        case "subscription": return L("订阅", "Subscription")
        case "pricing": return L("定价", "Pricing")
        default: return L("设置", "Settings")
        }
    }
}

struct MainView: View {
    @ObservedObject var state: AppState
    var body: some View {
        HStack(spacing:0) {
            SidebarView(state:state)
            GeometryReader { geometry in
            VStack(spacing:0) {
                HStack(spacing:18) {
                    Button { state.toggleSidebar() } label: { Image(systemName:"sidebar.left") }.buttonStyle(.plain).accessibilityLabel(L("切换侧边栏", "Toggle sidebar"))
                    Spacer()
                    ScanStamp(clock:state.clock).font(.system(size:12)).foregroundStyle(.secondary)
                }.padding(.horizontal,PageLayout.inset).frame(height:48)
                Divider()
                HStack(alignment:.firstTextBaseline) {
                    PageHeading(title:Pages.title(state.selectedPage))
                    if state.selectedPage == "pricing" {
                        Button(L("立即同步", "Sync now")) { state.syncPrices() }
                    }
                }.padding(.horizontal,PageLayout.inset).padding(.top,PageLayout.inset).padding(.bottom,10)
                page.frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading)
                if let error = state.errorMessage {
                    HStack { Text(error).font(.caption).foregroundStyle(.secondary).textSelection(.enabled); Spacer(); Button { state.errorMessage = nil } label: { Image(systemName:"xmark") }.buttonStyle(.plain) }.padding(12).background(.thinMaterial)
                }
            }.background(Color(nsColor:.textBackgroundColor))
                .environment(\.compactPage,geometry.size.width < 560)
            }
        }
        .background(Color(nsColor:.windowBackgroundColor))
        .preferredColorScheme(state.theme == "dark" ? .dark : state.theme == "light" ? .light : nil)
        .onChange(of:state.selectedPage) { _,page in if page == "subscription" || (page == "trends" && state.usageSection == "threads") { state.refreshReports() } }
    }
    @ViewBuilder private var page: some View {
        switch state.selectedPage {
        case "overview": OverviewView(state:state)
        case "logs": LogsView(state:state)
        case "trends": UsageView(state:state)
        case "subscription": SubscriptionView(state:state)
        case "pricing": PricingView(state:state)
        default: SettingsView(state:state)
        }
    }
}

private struct SidebarView: View {
    @ObservedObject var state: AppState
    @State private var draggedPage: String?
    @State private var resizingFrom: CGFloat?
    @State private var previewWidth: CGFloat?
    private var navigation: [String] {
        let saved = state.preferences.analytics["navigation_order"] as? [String] ?? Pages.all
        return ["overview"] + saved.filter {$0 != "overview" && Pages.all.contains($0)} + Pages.all.filter {$0 != "overview" && !saved.contains($0)}
    }
    var body: some View {
        VStack(alignment:.leading,spacing:0) {
            if state.sidebarVisible {
                HStack {
                    Image(nsImage:Branding.sidebarWordmark()).resizable().scaledToFit()
                        .frame(width:min(138,max(72,(previewWidth ?? state.sidebarWidth)-72)),height:28,alignment:.leading)
                        .accessibilityLabel("Codexio")
                    Spacer(minLength:4)
                    Button { state.selectedPage = "logs"; NotificationCenter.default.post(name:.init("CodexioSearch"),object:nil) } label: { Image(systemName:"magnifyingglass").foregroundStyle(.secondary) }.buttonStyle(.plain).accessibilityLabel(L("搜索日志", "Search logs"))
                }.padding(.horizontal,16).padding(.top,30).padding(.bottom,24)
            } else {
                SidebarBrand(state:state).frame(maxWidth:.infinity)
                    .padding(.top,30).padding(.bottom,24)
            }
            ForEach(navigation,id:\.self) { page in
                SidebarNavigationRow(page:page,selected:state.selectedPage == page,expanded:state.sidebarVisible,draggedPage:$draggedPage) { if state.selectedPage != page { state.selectedPage = page } }
                    .padding(.horizontal,8).padding(.bottom,4)
                    .onDrop(of:["public.text"],isTargeted:nil) { _ in
                        guard let from = draggedPage, from != "overview", page != "overview", from != page else { return false }
                        var order = navigation.filter {$0 != from}; guard let index = order.firstIndex(of:page) else { return false }
                        order.insert(from,at:index); state.setPreference("navigation_order",order); draggedPage = nil; return true
                    }
            }
            Spacer(); Divider()
            if state.sidebarVisible {
                Button { state.selectedPage = "subscription" } label: {
                    VStack(alignment:.leading,spacing:5) {
                        Text(state.quota.account.string("planType").isEmpty ? L("本机 Codex", "Local Codex") : "ChatGPT · "+planName(state.quota.account.string("planType"))).font(.system(size:12)).lineLimit(1)
                        Text(state.quota.account.string("email")).font(.system(size:10)).foregroundStyle(.secondary).lineLimit(1)
                    }.frame(maxWidth:.infinity,alignment:.leading).padding(16).contentShape(Rectangle())
                }.buttonStyle(.plain)
            } else { Button { state.selectedPage = "subscription" } label: { Image(systemName:"person.crop.circle").frame(maxWidth:.infinity).frame(height:48) }.buttonStyle(.plain).accessibilityLabel(Pages.title("subscription")) }
        }.frame(width:previewWidth ?? (state.sidebarVisible ? state.sidebarWidth : 62))
        .background(Color(nsColor:.windowBackgroundColor))
        .overlay(alignment:.trailing) {
            Rectangle().fill(Color.secondary.opacity(0.16)).frame(width:1).allowsHitTesting(false)
            HorizontalResizeHandle(begin:{ resizingFrom = state.sidebarVisible ? state.sidebarWidth : 62 },change:{ delta in
                guard let resizingFrom else { return }
                previewWidth = max(state.sidebarVisible ? 140 : 62,min(320,resizingFrom+delta))
            },end:{ delta in
                guard let resizingFrom else { return }
                let width = resizingFrom+delta
                if width < 140 { state.sidebarVisible = false }
                else { state.sidebarWidth = min(320,width); state.sidebarVisible = true }
                previewWidth = nil; self.resizingFrom = nil; state.persistSidebar()
            }).frame(width:8)
        }
    }
}

private struct SidebarBrand: View {
    @ObservedObject var state: AppState
    var body: some View {
        Group {
            if state.appIconStyle == "main" { Image(nsImage:Branding.menuIcon()).resizable() }
            else { Image(nsImage:state.appLogoImage ?? Branding.logo(dark:false)).resizable() }
        }.scaledToFit().frame(width:28,height:28).accessibilityLabel("Codexio")
    }
}

private struct SidebarNavigationRow: View {
    let page: String
    let selected: Bool
    let expanded: Bool
    @Binding var draggedPage: String?
    let action: () -> Void
    @State private var hovered = false
    var body: some View {
        NavigationButton(title:Pages.title(page),selected:selected,action:action)
            .overlay {
                HStack(spacing:12) {
                    NavigationGlyph(name:page).stroke(style:StrokeStyle(lineWidth:1.6,lineCap:.round,lineJoin:.round)).frame(width:20,height:20)
                    if expanded { Text(Pages.title(page)).font(.system(size:14,weight:selected ? .medium : .regular)).lineLimit(1) }
                }.foregroundStyle(.primary).padding(.leading,13).padding(.trailing,expanded ? 26 : 13)
                    .frame(maxWidth:.infinity,alignment:.leading).allowsHitTesting(false)
            }
            .overlay(alignment:.trailing) {
                if expanded && page != "overview" {
                    Image(systemName:"line.3.horizontal").font(.system(size:10)).foregroundStyle(.secondary)
                        .frame(width:24,height:40).contentShape(Rectangle()).opacity(hovered ? 0.6 : 0)
                        .onDrag { draggedPage = page; return NSItemProvider(object:page as NSString) }
                        .accessibilityLabel(L("调整导航顺序", "Reorder navigation"))
                }
            }.frame(height:40).onHover { hovered = $0 }
    }
}

struct OverviewView: View {
    @ObservedObject var state: AppState
    @ObservedObject private var projection: AsyncProjection<TrendProjection>
    @State private var period = "today"
    init(state: AppState) { self.state = state; projection = state.overviewProjection }
    private var key: String { state.usage.revision.uuidString+":"+period }
    private func load() {
        let snapshot = state.usage, range = UsageRange(period:period), granularity = period == "today" ? "hour" : period == "all" ? "week" : "day"
        projection.load(key:key) { TrendProjection.build(snapshot,range:range,model:"all",granularity:granularity) }
    }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                AdaptiveRow(spacing:12) {
                    QuotaCard(window:state.quota.five,title:L("5 小时额度", "5-hour limit"),fresh:state.quota.fresh)
                    QuotaCard(window:state.quota.week,title:L("周额度", "Weekly limit"),fresh:state.quota.fresh)
                }
                if let error = state.quota.error { StatusNote(text:error) }
                Divider()
                HStack { SectionHeading(title:L("本机用量", "Local usage")); Spacer(); PeriodPicker(selection:$period).fixedSize() }
                SummaryMetrics(summary:projection.value.summary)
                VStack(alignment:.leading,spacing:16) { SectionHeading(title:L("用量趋势", "Usage trend")); TrendChart(days:projection.value.days) }.padding(18).overlay(RoundedRectangle(cornerRadius:13).stroke(.secondary.opacity(0.15)))
                HStack { SectionHeading(title:L("最近请求", "Recent requests")); Button(L("查看全部", "View all")) { state.selectedPage = "logs" }.buttonStyle(.plain).foregroundStyle(.secondary) }
                if !projection.value.recent.isEmpty {
                    CompactTable(columns:LogFields.columns(["content","model","total","cost","duration","details"]).map { column in
                        var column = column; column.maximum = column.width+300; return column
                    },
                        rows:projection.value.recent.map { row in LogFields.row(row,timeOnly:true) { state.usage.members(of:row) } },
                        revision:key,preferences:state.preferences,storageKey:"overview-recent",verticalScrolling:false)
                        .frame(height:CGFloat(projection.value.recent.count)*45+42)
                        .clipShape(RoundedRectangle(cornerRadius:14))
                        .overlay(RoundedRectangle(cornerRadius:14).stroke(.secondary.opacity(0.16)))
                }
                if projection.value.recent.isEmpty { EmptyState(title:state.loading ? L("正在读取本机记录", "Loading local records") : L("暂无请求", "No requests yet")) }
            }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
        }.onAppear(perform:load).onChange(of:key) { _,_ in load() }
    }
}

struct PeriodPicker: View {
    @Environment(\.compactPage) private var compact
    @Binding var selection: String
    var custom = false
    private var picker: some View {
        Picker("",selection:$selection) { Text(L("今日", "Today")).tag("today"); Text(L("近 7 天", "Last 7 days")).tag("week"); Text(L("近 30 天", "Last 30 days")).tag("month"); Text(L("历史", "All time")).tag("all"); if custom { Text(L("自选日期", "Custom")).tag("custom") } }.labelsHidden()
    }
    var body: some View {
        if compact { picker.pickerStyle(.menu).fixedSize() }
        else { picker.pickerStyle(.segmented).fixedSize() }
    }
}

struct LogsView: View {
    @Environment(\.compactPage) private var compact
    @ObservedObject var state: AppState
    @ObservedObject private var projection: AsyncProjection<LogProjection>
    @State private var mode = "requests"
    @State private var period = "today"
    @State private var model = "all"
    @State private var query = ""
    @State private var page = 0
    @State private var status = "all"
    @State private var tier = "all"
    @State private var from = Calendar.current.date(byAdding:.day,value:-6,to:Date())!
    @State private var through = Date()
    @FocusState private var searchFocused: Bool
    init(state: AppState) { self.state = state; projection = state.logProjection }
    private var fields: [String] {
        let saved = state.preferences.analytics["native_log_columns"] as? [String] ?? LogFields.defaults
        return ["content"]+LogFields.all.filter {$0 != "content" && $0 != "details" && saved.contains($0)}+["details"]
    }
    private var filterKey: String { [mode,period,model,tier,status,query,period == "custom" ? String(Calendar.current.startOfDay(for:from).timeIntervalSince1970) : "",period == "custom" ? String(Calendar.current.startOfDay(for:through).timeIntervalSince1970) : ""].joined(separator:"|") }
    private var key: String { state.usage.revision.uuidString+filterKey+String(page) }
    private func load() {
        let snapshot = state.usage, range = UsageRange(period:period,from:from,through:through)
        let mode = mode, model = model, tier = tier, status = status, query = query, page = page, filterKey = filterKey
        projection.load(key:key) { LogProjection.build(snapshot,range:range,mode:mode,model:model,tier:tier,status:status,query:query,page:page,filterKey:filterKey) }
    }
    private func toggle(_ key: String,_ enabled: Bool) {
        var values = Set(fields); if enabled { values.insert(key) } else { values.remove(key) }
        state.setPreference("native_log_columns",LogFields.all.filter {values.contains($0)})
    }
    private var modePicker: some View {
        Picker("",selection:$mode) { Text(L("用户请求", "User requests")).tag("requests"); Text(L("模型调用", "Model calls")).tag("calls") }.labelsHidden()
    }
    var body: some View {
        VStack(alignment:.leading,spacing:14) {
            HStack { PeriodPicker(selection:$period,custom:true); Spacer() }
            if period == "custom" { DateRangeControls(from:$from,through:$through) }
            HStack(spacing:10) {
                if compact { modePicker.pickerStyle(.menu).fixedSize() }
                else { modePicker.pickerStyle(.segmented).fixedSize() }
                TextField(L("搜索输入、聊天或 ID", "Search prompt, chat or ID"),text:$query).textFieldStyle(.roundedBorder).focused($searchFocused)
                Menu(L("显示字段", "Columns")) {
                    ForEach(LogFields.all.filter {$0 != "content" && $0 != "details"},id:\.self) { field in
                        Toggle(LogFields.title(field),isOn:Binding(get:{fields.contains(field)},set:{toggle(field,$0)}))
                    }
                    Divider(); Button(L("恢复默认字段", "Reset columns")) { state.setPreference("native_log_columns",LogFields.defaults) }
                }.fixedSize()
            }
            HStack(spacing:10) {
                Picker("",selection:$model) { Text(L("全部模型", "All models")).tag("all"); ForEach(state.usage.models,id:\.self) { Text($0).tag($0) } }.labelsHidden().fixedSize(horizontal:!compact,vertical:true)
                Picker("",selection:$tier) { Text(L("全部速度", "All speeds")).tag("all"); Text("Fast").tag("priority"); Text(L("标准", "Standard")).tag("default"); Text(L("未知", "Unknown")).tag("unknown") }.labelsHidden().fixedSize(horizontal:!compact,vertical:true)
                if mode == "requests" { Picker("",selection:$status) { Text(L("全部状态", "All statuses")).tag("all"); Text(L("已完成", "Completed")).tag("completed"); Text(L("进行中", "In progress")).tag("running"); Text(L("未知", "Unknown")).tag("unknown") }.labelsHidden().fixedSize(horizontal:!compact,vertical:true) }
                if !compact { Spacer() }
            }
            CompactTable(columns:LogFields.columns(fields),rows:projection.value.rows.map { row in LogFields.row(row,timeOnly:period == "today") {state.usage.members(of:row)} },revision:projection.value.revision.uuidString+period,preferences:state.preferences,storageKey:"logs",scrollResetKey:projection.value.scrollResetKey)
                .overlay(RoundedRectangle(cornerRadius:10).stroke(.secondary.opacity(0.16)))
            HStack {
                Text(mode == "requests" ? "\(projection.value.count-projection.value.unassigned) "+L("条请求", "requests")+(projection.value.unassigned > 0 ? " · \(projection.value.unassigned) "+L("次未归属调用", "unassigned calls") : "") : "\(projection.value.count) "+L("次调用", "calls")).foregroundStyle(.secondary)
                Spacer()
                Button { page = max(0,projection.value.page-1) } label: { Image(systemName:"chevron.left") }.disabled(projection.value.page == 0)
                Text("\(projection.value.page+1) / \(projection.value.pages)").monospacedDigit()
                Button { page = projection.value.page+1 } label: { Image(systemName:"chevron.right") }.disabled(projection.value.page+1 >= projection.value.pages)
            }.font(.system(size:12))
        }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
        .onAppear(perform:load).onChange(of:key) { _,_ in load() }
        .onChange(of:filterKey) { _,_ in page = 0 }
        .onReceive(NotificationCenter.default.publisher(for:.init("CodexioSearch"))) { _ in searchFocused = true }
    }
}
