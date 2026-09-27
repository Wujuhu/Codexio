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
    @State private var draggedPage: String?
    private var navigation: [String] {
        let saved = state.preferences.analytics["navigation_order"] as? [String] ?? Pages.all
        return ["overview"] + saved.filter {$0 != "overview" && Pages.all.contains($0)} + Pages.all.filter {$0 != "overview" && !saved.contains($0)}
    }
    var body: some View {
        HStack(spacing:0) {
            if state.sidebarVisible { sidebar.frame(width:238); Rectangle().fill(Color.secondary.opacity(0.15)).frame(width:1) }
            VStack(spacing:0) {
                HStack(spacing:18) {
                    Button { state.toggleSidebar() } label: { Image(systemName:"sidebar.left") }.buttonStyle(.plain).accessibilityLabel(L("切换侧边栏", "Toggle sidebar"))
                    Spacer()
                    if state.loading { ProgressView().controlSize(.small) }
                    else if let updated = state.usage.updated { Text(updated,style:.relative).font(.system(size:12)).foregroundStyle(.secondary) }
                    Button { state.refresh() } label: { Image(systemName:"arrow.clockwise") }.buttonStyle(.plain).accessibilityLabel(L("刷新", "Refresh"))
                }.padding(.horizontal,32).frame(height:48)
                Divider()
                page.frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading)
                if let error = state.errorMessage {
                    HStack { Text(error).font(.caption).foregroundStyle(.secondary).textSelection(.enabled); Spacer(); Button { state.errorMessage = nil } label: { Image(systemName:"xmark") }.buttonStyle(.plain) }.padding(12).background(.thinMaterial)
                }
            }.background(Color(nsColor:.textBackgroundColor))
        }
        .frame(minWidth:min(1000,(NSScreen.main?.visibleFrame.width ?? 1440)-24),minHeight:min(700,(NSScreen.main?.visibleFrame.height ?? 900)-24))
        .background(Color(nsColor:.windowBackgroundColor))
        .preferredColorScheme(state.theme == "dark" ? .dark : state.theme == "light" ? .light : nil)
        .onChange(of:state.selectedPage) { _,page in if page == "subscription" || (page == "trends" && state.usageSection == "threads") { state.refreshReports() } }
    }
    private var sidebar: some View {
        VStack(alignment:.leading,spacing:0) {
            HStack {
                Text("Codexio").font(.system(size:24,weight:.semibold))
                Spacer()
                Button { state.selectedPage = "logs"; NotificationCenter.default.post(name:.init("CodexioSearch"),object:nil) } label: { Image(systemName:"magnifyingglass").foregroundStyle(.secondary) }.buttonStyle(.plain).accessibilityLabel(L("搜索日志", "Search logs"))
            }.padding(.horizontal,20).padding(.top,30).padding(.bottom,27)
            ForEach(navigation,id:\.self) { page in
                Button {
                    state.selectedPage = page
                } label: {
                    HStack(spacing:14) {
                        NavigationGlyph(name:page).stroke(style:StrokeStyle(lineWidth:1.6,lineCap:.round,lineJoin:.round)).frame(width:20,height:20)
                        Text(Pages.title(page)).font(.system(size:15,weight:state.selectedPage == page ? .medium : .regular))
                        Spacer()
                    }.foregroundStyle(.primary).padding(.horizontal,13).frame(height:42).background(state.selectedPage == page ? Color.primary.opacity(0.075) : Color.clear,in:RoundedRectangle(cornerRadius:8))
                }.buttonStyle(.plain).padding(.horizontal,10).padding(.bottom,5)
                    .onDrag { draggedPage = page; return NSItemProvider(object:page as NSString) }
                    .onDrop(of:["public.text"],isTargeted:nil) { _ in
                        guard let from = draggedPage, from != "overview", page != "overview", from != page else { return false }
                        var order = navigation.filter {$0 != from}; guard let index = order.firstIndex(of:page) else { return false }
                        order.insert(from,at:index); state.setPreference("navigation_order",order); draggedPage = nil; return true
                    }
            }
            Spacer()
            Divider()
            VStack(alignment:.leading,spacing:5) {
                Text(state.quota.account.string("planType").capitalized.isEmpty ? L("本机 Codex", "Local Codex") : "ChatGPT · "+state.quota.account.string("planType").capitalized).font(.system(size:13))
                Text(state.quota.account.string("email")).font(.system(size:11)).foregroundStyle(.secondary).lineLimit(1)
            }.padding(20)
        }.background(Color(nsColor:.windowBackgroundColor))
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

struct OverviewView: View {
    @ObservedObject var state: AppState
    @State private var period = "today"
    private var range: UsageRange { UsageRange(period:period) }
    private var recent: [UsageRow] { Array(state.usage.requests.filter { range.contains($0) && $0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") }.prefix(4)) }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:25) {
                PageHeading(title:Pages.title("overview"))
                HStack(spacing:32) {
                    QuotaCard(window:state.quota.five,title:L("5 小时额度", "5-hour limit"),fresh:state.quota.fresh)
                    QuotaCard(window:state.quota.week,title:L("周额度", "Weekly limit"),fresh:state.quota.fresh)
                }
                if let error = state.quota.error { StatusNote(text:error) }
                Divider()
                HStack { SectionHeading(title:L("本机用量", "Local usage")); PeriodPicker(selection:$period) }
                SummaryMetrics(summary:state.usage.summaries[period] ?? UsageSummary())
                VStack(alignment:.leading,spacing:18) { SectionHeading(title:L("用量趋势", "Usage trend")); TrendChart(days:range.buckets(state.usage.calls.filter {$0.local && range.contains($0)},granularity:period == "today" ? "hour" : period == "all" ? "week" : "day")) }.padding(20).overlay(RoundedRectangle(cornerRadius:13).stroke(.secondary.opacity(0.15)))
                HStack { SectionHeading(title:L("最近请求", "Recent requests")); Button(L("查看全部", "View all")) { state.selectedPage = "logs" }.buttonStyle(.plain).foregroundStyle(.secondary) }
                ForEach(recent) { row in
                    HStack(spacing:20) {
                        Text(dateText(row.date,timeOnly:true)).font(.system(size:12)).foregroundStyle(.secondary).frame(width:80,alignment:.leading)
                        Text(row.raw.string("prompt_preview")).font(.system(size:13)).lineLimit(1).frame(maxWidth:.infinity,alignment:.leading)
                        Text(row.modelLabel).font(.system(size:12)).foregroundStyle(.secondary).lineLimit(1)
                        Text(money(row.cost)).font(.system(size:13)).monospacedDigit().frame(width:65,alignment:.trailing)
                        DetailsLink(row:row,members:state.usage.calls.filter { (row.raw["member_ids"] as? [String] ?? []).contains($0.id) }).frame(width:48,height:23)
                    }.padding(.vertical,5)
                    Divider()
                }
                if recent.isEmpty { EmptyState(title:state.loading ? L("正在读取本机记录", "Loading local records") : L("暂无请求", "No requests yet")) }
            }.padding(32)
        }
    }
}

struct PeriodPicker: View {
    @Binding var selection: String
    var custom = false
    var body: some View {
        Picker("",selection:$selection) { Text(L("今日", "Today")).tag("today"); Text(L("近 7 天", "Last 7 days")).tag("week"); Text(L("近 30 天", "Last 30 days")).tag("month"); Text(L("历史", "All time")).tag("all"); if custom { Text(L("自选日期", "Custom")).tag("custom") } }.pickerStyle(.segmented).labelsHidden().frame(maxWidth:custom ? 470 : 365)
    }
}

struct LogsView: View {
    @ObservedObject var state: AppState
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
    private var filtered: [UsageRow] {
        let range = UsageRange(period:period,from:from,through:through)
        return (mode == "requests" ? state.usage.requests : state.usage.calls).filter { row in
            range.contains(row) && (tier == "all" || normalizedTier(row.raw.string("service_tier")) == tier) && (model == "all" || row.raw.string("model").contains(model)) && (mode == "calls" || status == "all" || row.raw.string("status","completed") == status) && (query.isEmpty || [row.title,row.raw.string("prompt_preview"),row.raw.string("output_preview"),row.raw.string("session_id"),row.id].joined(separator:" ").localizedCaseInsensitiveContains(query))
        }
    }
    private var displayed: [UsageRow] { Array(filtered.dropFirst(min(page,max(0,(filtered.count-1)/60))*60).prefix(60)) }
    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            HStack { PageHeading(title:Pages.title("logs")); PeriodPicker(selection:$period,custom:true) }
            if period == "custom" { DateRangeControls(from:$from,through:$through) }
            HStack(spacing:12) {
                Picker("",selection:$mode) { Text(L("用户请求", "User requests")).tag("requests"); Text(L("模型调用", "Model calls")).tag("calls") }.labelsHidden().pickerStyle(.segmented).frame(width:205)
                TextField(L("搜索输入、聊天或 ID", "Search prompt, chat or ID"),text:$query).textFieldStyle(.roundedBorder).focused($searchFocused)
                Picker("",selection:$model) {
                    Text(L("全部模型", "All models")).tag("all")
                    ForEach(Array(Set(state.usage.calls.map {$0.raw.string("model")})).sorted(),id:\.self) { Text($0).tag($0) }
                }.labelsHidden().frame(width:150)
                Picker("",selection:$tier) { Text(L("全部速度", "All speeds")).tag("all"); Text("Fast").tag("priority"); Text(L("标准", "Standard")).tag("default"); Text(L("未知", "Unknown")).tag("unknown") }.labelsHidden().frame(width:100)
                if mode == "requests" { Picker("",selection:$status) { Text(L("全部状态", "All statuses")).tag("all"); Text(L("已完成", "Completed")).tag("completed"); Text(L("进行中", "In progress")).tag("running"); Text(L("未知", "Unknown")).tag("unknown") }.labelsHidden().frame(width:112) }
            }
            logTable
            HStack {
                Text("\(filtered.count) "+(mode == "requests" ? L("条请求", "requests") : L("次调用", "calls"))).foregroundStyle(.secondary)
                Spacer()
                Button { page = max(0,page-1) } label: { Image(systemName:"chevron.left") }.disabled(page == 0)
                Text("\(min(page,max(0,(filtered.count-1)/60))+1) / \(max(1,(filtered.count+59)/60))").monospacedDigit()
                Button { page += 1 } label: { Image(systemName:"chevron.right") }.disabled((page+1)*60 >= filtered.count)
            }.font(.system(size:12))
        }.padding(32)
        .onChange(of:query) { _,_ in page = 0 }.onChange(of:mode) { _,_ in page = 0 }.onChange(of:period) { _,_ in page = 0 }
        .onChange(of:from) { _,_ in page = 0 }.onChange(of:through) { _,_ in page = 0 }.onChange(of:model) { _,_ in page = 0 }.onChange(of:tier) { _,_ in page = 0 }.onChange(of:status) { _,_ in page = 0 }
        .onReceive(NotificationCenter.default.publisher(for:.init("CodexioSearch"))) { _ in searchFocused = true }
    }
    private var logTable: some View {
        Table(displayed) {
            TableColumn(L("时间", "Time")) { row in Text(dateText(row.date,timeOnly:period == "today")).font(.system(size:12)).foregroundStyle(.secondary) }.width(min:85,ideal:115,max:165)
            TableColumn(mode == "requests" ? L("用户请求", "User request") : L("输入预览", "Prompt")) { row in
                VStack(alignment:.leading,spacing:3) {
                    Text(row.raw.string("prompt_preview").isEmpty ? row.title : row.raw.string("prompt_preview")).font(.system(size:13)).lineLimit(2)
                    if row.raw.string("status") == "running" { Text(L("进行中", "In progress")).font(.caption).foregroundStyle(.secondary) }
                    else if row.raw.flag("is_subagent") { Text(L("子代理", "Subagent")).font(.caption).foregroundStyle(.secondary) }
                    else if row.raw.string("record_kind") == "unassigned" { Text(L("未归属调用", "Unassigned call")).font(.caption).foregroundStyle(.secondary) }
                }.padding(.vertical,6)
            }.width(min:200,ideal:320)
            TableColumn(L("模型", "Model")) { row in Text(row.modelLabel).font(.system(size:12)).lineLimit(2).foregroundStyle(.secondary) }.width(min:100,ideal:150,max:190)
            TableColumn("Token") { row in Text(compact(row.tokens.map(Double.init))).monospacedDigit().frame(maxWidth:.infinity,alignment:.trailing) }.width(min:65,ideal:80,max:100)
            TableColumn(L("费用", "Cost")) { row in Text(money(row.cost)).monospacedDigit().frame(maxWidth:.infinity,alignment:.trailing) }.width(min:65,ideal:80,max:100)
            TableColumn(L("详情", "Details")) { row in DetailsLink(row:row,members:state.usage.calls.filter { (row.raw["member_ids"] as? [String] ?? []).contains($0.id) }).frame(width:48,height:26) }.width(58)
        }.tableStyle(.inset(alternatesRowBackgrounds:false)).overlay(RoundedRectangle(cornerRadius:12).stroke(.secondary.opacity(0.15)))
    }
}
