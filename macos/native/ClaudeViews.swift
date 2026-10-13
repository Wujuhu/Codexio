import SwiftUI
import AppKit

struct ClientSwitcher: View {
    @ObservedObject var state: AppState
    private static let images: [DesktopClient:NSImage] = Dictionary(uniqueKeysWithValues:DesktopClient.allCases.compactMap { client in
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("client-icons/"+client.logo+".svg"), let image = NSImage(contentsOf:url) else {return nil}
        image.isTemplate = true; return (client,image)
    })
    var body: some View {
        HStack(spacing:2) {
            ForEach(DesktopClient.allCases,id:\.self) { client in
                Button { state.pageClient = client } label: {
                    Group {
                        if let image = Self.images[client] {Image(nsImage:image).resizable().scaledToFit()}
                        else {Color.clear}
                    }.frame(width:19,height:19).padding(6)
                        .background(state.pageClient == client ? Color.primary.opacity(0.08) : .clear,in:RoundedRectangle(cornerRadius:7))
                }.buttonStyle(.plain).help(client.title).accessibilityLabel(client.title)
                    .accessibilityAddTraits(state.pageClient == client ? .isSelected : [])
            }
        }
    }
}

private func claudeGridRow(_ row: UsageRow,state: AppState,showYear: Bool,timeOnly: Bool = false) -> GridRow {
    var grid = LogFields.row(row,timeOnly:timeOnly,showYear:showYear,details:{[]})
    let original = grid.text
    grid.text = {key in
        if key == "content" && row.raw.string("prompt_preview").isEmpty {
            let suffix = [logDateText(row.date,timeOnly:timeOnly,showYear:showYear),logEffortName(row.raw.string("reasoning_effort")),row.raw.flag("is_subagent") ? L("子代理", "Subagent") : row.raw.string("record_kind") == "unassigned" ? L("未归属调用", "Unassigned call") : ""].filter {!$0.isEmpty}.joined(separator:" · ")
            return GridText(main:"—",secondary:suffix)
        }
        if key == "cache_rate" {
            let rate = row.raw.number("input_tokens").flatMap {input in input > 0 ? row.raw.number("cached_input_tokens").map {$0/input*100} : nil}
            return GridText(main:percent(rate,digits:1))
        }
        if key == "context" {return GridText(main:compact(row.raw.string("record_kind") == "user_request" ? row.raw.number("context_input_tokens") : row.inputTokens.map(Double.init)))}
        if key == "status" && row.raw.string("status") == "failed" {return GridText(main:L("失败", "Failed"))}
        return original(key)
    }
    grid.detail = nil
    grid.customDetail = {AnyView(ClaudeDetailView(state:state,row:row))}
    return grid
}

struct ClaudeOverviewView: View {
    @ObservedObject var state: AppState
    @StateObject private var projection = AsyncProjection(TrendProjection())
    @State private var period = "today"
    private var key: String {"claude_code|overview|\(state.claudeRevision)|\(period)|\(TimeZone.current.identifier)"}
    private func load() {
        let queue = state.dataQueue, ledger = state.claudeLedger, range = UsageRange(period:period), granularity = period == "today" ? "hour" : period == "all" ? "week" : "day"
        projection.load(key:key,revision:state.claudeRevision) {queue.sync {(try? ledger.trend(range:range,granularity:granularity)) ?? TrendProjection()}}
    }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                if let warning = state.claudeWarning {StatusNote(text:warning)}
                HStack {SectionHeading(title:L("用量", "Usage")); Spacer(); PeriodPicker(selection:$period).fixedSize()}
                SummaryMetrics(summary:projection.value.summary)
                VStack(alignment:.leading,spacing:16) {SectionHeading(title:L("详情", "Details")); TrendChart(days:projection.value.days)}.padding(18).overlay(RoundedRectangle(cornerRadius:13).stroke(.secondary.opacity(0.15)))
                ModelShareCard(projection:projection.value,client:.claudeCode)
                HStack {SectionHeading(title:L("最近请求", "Recent requests")); Button(L("查看全部", "View all")) {state.selectedPage = "logs"}.buttonStyle(.plain).foregroundStyle(.secondary)}
                if projection.value.recent.isEmpty {EmptyState(title:L("暂无 Claude 请求", "No Claude requests yet"))}
                else {
                    CompactTable(columns:LogFields.columns(["content","model","total","cost","duration","details"]),rows:projection.value.recent.map {claudeGridRow($0,state:state,showYear:false,timeOnly:period == "today")},revision:projection.value.revision.uuidString+period,preferences:state.preferences,storageKey:"claude-overview-recent",verticalScrolling:false)
                        .frame(height:CGFloat(projection.value.recent.count)*45+42).clipShape(RoundedRectangle(cornerRadius:14)).overlay(RoundedRectangle(cornerRadius:14).stroke(.secondary.opacity(0.16)))
                }
            }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
        }.onAppear(perform:load).onChange(of:key) {_,_ in load()}
    }
}

struct ClaudeLogsView: View {
    @ObservedObject var state: AppState
    @StateObject private var projection = AsyncProjection(LogProjection())
    @State private var mode = "requests"
    @State private var period = "today"
    @State private var model = "all"
    @State private var status = "all"
    @State private var query = ""
    @State private var page = 0
    @State private var from = Calendar.current.date(byAdding:.day,value:-6,to:Date())!
    @State private var through = Date()
    @State private var datesOpen = false
    @FocusState private var searchFocused: Bool
    private var fields: [String] {
        let saved = state.preferences.analytics["native_log_columns"] as? [String] ?? LogFields.defaults
        return ["content"]+LogFields.all.filter {$0 != "content" && $0 != "details" && saved.contains($0)}+["details"]
    }
    private var filterKey: String {[mode,period,model,status,query,period == "custom" ? String(from.timeIntervalSince1970) : "",period == "custom" ? String(through.timeIntervalSince1970) : ""].joined(separator:"|")}
    private var key: String {"claude_code|logs|\(state.claudeRevision)|\(filterKey)|\(page)|\(TimeZone.current.identifier)"}
    private func load() {
        let queue = state.dataQueue, ledger = state.claudeLedger, range = UsageRange(period:period,from:from,through:through), mode = mode, model = model, status = status, query = query, page = page
        projection.load(key:key,revision:state.claudeRevision) {queue.sync {(try? ledger.logPage(range:range,mode:mode,model:model,status:status,query:query,page:page)) ?? LogProjection()}}
    }
    var body: some View {
        VStack(alignment:.leading,spacing:14) {
            HStack(spacing:10) {
                PeriodPicker(selection:$period,custom:true,condensed:true)
                if period == "custom" {Button {datesOpen = true} label:{Image(systemName:"calendar")}.popover(isPresented:$datesOpen) {DateRangeControls(from:$from,through:$through).padding(18).frame(width:360)}}
                Spacer()
                CompactFilterPicker(title:mode == "requests" ? L("用户请求", "User requests") : L("模型调用", "Model calls"),label:L("日志类型", "Log type"),selection:mode,items:[.init(id:"requests",title:L("用户请求", "User requests")),.init(id:"calls",title:L("模型调用", "Model calls"))],choose:{mode = $0}).frame(width:140)
            }
            HStack(spacing:10) {
                CompactFilterPicker(title:model == "all" ? L("全部模型", "All models") : model,label:L("模型", "Model"),selection:model,items:[.init(id:"all",title:L("全部模型", "All models"))]+state.claudeModels.map {.init(id:$0,title:$0)},choose:{model = $0}).frame(width:170)
                CompactFilterPicker(title:status == "all" ? L("全部状态", "All statuses") : status == "completed" ? L("已完成", "Completed") : status == "failed" ? L("失败", "Failed") : "—",label:L("状态", "Status"),selection:status,items:[.init(id:"all",title:L("全部状态", "All statuses")),.init(id:"completed",title:L("已完成", "Completed")),.init(id:"failed",title:L("失败", "Failed")),.init(id:"",title:"—")],choose:{status = $0}).frame(width:140)
                TextField(L("搜索正文或摘要", "Search content or summaries"),text:$query).textFieldStyle(.roundedBorder).focused($searchFocused).frame(maxWidth:.infinity).frame(height:30)
                Menu {ForEach(LogFields.all.filter {$0 != "content" && $0 != "details"},id:\.self) {field in Toggle(LogFields.title(field),isOn:Binding(get:{fields.contains(field)},set:{enabled in
                    var values = Set(fields); if enabled {values.insert(field)} else {values.remove(field)}
                    state.setPreference("native_log_columns",LogFields.all.filter {values.contains($0)})
                }))}} label:{Image(systemName:"slider.horizontal.3")}.menuStyle(.borderlessButton).fixedSize().frame(width:30,height:30)
            }
            if let warning = state.claudeWarning {StatusNote(text:warning)}
            // Reload when the asynchronous result arrives, not only when its query changes.
            CompactTable(columns:LogFields.columns(fields),rows:projection.value.rows.map {claudeGridRow($0,state:state,showYear:projection.value.showYear,timeOnly:period == "today")},revision:projection.value.revision.uuidString+period,preferences:state.preferences,storageKey:"claude-logs",scrollResetKey:filterKey+String(page))
                .overlay {if projection.value.rows.isEmpty {EmptyState(title:L("暂无 Claude 记录", "No Claude records yet"))}}
            HStack {
                Text("\(mode == "requests" ? projection.value.count-projection.value.unassigned : projection.value.count) "+(mode == "requests" ? L("请求组", "request groups") : L("模型调用", "model calls"))+" · \(projection.value.unassigned) "+L("未归属调用", "unassigned calls"))
                Spacer(); Button {page = max(0,page-1)} label:{Image(systemName:"chevron.left")}.disabled(page == 0)
                Text("\(page+1) / \(projection.value.pages)")
                Button {page += 1} label:{Image(systemName:"chevron.right")}.disabled(page+1 >= projection.value.pages)
            }.font(.caption).foregroundStyle(.secondary)
        }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
            .onAppear(perform:load).onChange(of:key) {_,_ in load()}.onChange(of:filterKey) {_,_ in page = 0; load()}
            .onChange(of:projection.value.pages) {_,count in if page >= count {page = max(0,count-1)}}
            .onReceive(NotificationCenter.default.publisher(for:.init("CodexioSearch"))) {_ in searchFocused = true}
    }
}

struct ClaudeUsageView: View {
    @ObservedObject var state: AppState
    @StateObject private var activity = AsyncProjection(ActivityStats())
    var body: some View {
        VStack(alignment:.leading,spacing:0) {
            HStack(spacing:28) {tab("activity",L("活动", "Activity")); tab("trend",L("详情", "Details")); tab("threads",L("聊天排行", "Top chats")); Spacer()}.padding(.horizontal,PageLayout.inset)
            Divider().padding(.horizontal,PageLayout.inset)
            ScrollView {
                VStack(alignment:.leading,spacing:28) {
                    if let warning = state.claudeWarning {StatusNote(text:warning)}
                    switch state.usageSection {
                    case "threads": ClaudeRankingView(state:state)
                    case "trend": ClaudeTrendsView(state:state)
                    default: LocalActivityView(scopedActivity:activity.value,state:state)
                    }
                }.padding(PageLayout.inset)
            }
        }.onAppear(perform:load).onChange(of:state.claudeRevision) {_,_ in load()}
    }
    private func load() {
        let queue = state.dataQueue, ledger = state.claudeLedger
        activity.load(key:"claude_code|activity|\(state.claudeRevision)",revision:state.claudeRevision) {queue.sync {(try? ledger.activity()) ?? ActivityStats()}}
    }
    private func tab(_ id: String,_ title: String) -> some View {
        Button {state.usageSection = id} label: {Text(title).font(.system(size:14,weight:state.usageSection == id ? .medium : .regular)).foregroundStyle(state.usageSection == id ? .primary : .secondary).padding(.bottom,13).overlay(alignment:.bottom) {if state.usageSection == id {Rectangle().frame(height:2)}}}.buttonStyle(.plain)
    }
}

struct ClaudeTrendsView: View {
    @ObservedObject var state: AppState
    @StateObject private var projection = AsyncProjection(TrendProjection())
    @State private var period = "week"
    @State private var model = "all"
    @State private var granularity = "day"
    @State private var from = Calendar.current.date(byAdding:.day,value:-6,to:Date())!
    @State private var through = Date()
    @State private var datesOpen = false
    private var key: String {"claude_code|trend|\(state.claudeRevision)|\(period)|\(model)|\(granularity)|\(from)|\(through)|\(TimeZone.current.identifier)"}
    private func load() {
        let queue = state.dataQueue, ledger = state.claudeLedger, range = UsageRange(period:period,from:from,through:through), model = model, granularity = granularity
        projection.load(key:key,revision:state.claudeRevision) {queue.sync {(try? ledger.trend(range:range,model:model,granularity:granularity)) ?? TrendProjection()}}
    }
    var body: some View {
        VStack(alignment:.leading,spacing:22) {
            ScrollView(.horizontal) {HStack(spacing:12) {
                PeriodPicker(selection:$period,custom:true,condensed:true)
                if period == "custom" {Button {datesOpen = true} label:{Image(systemName:"calendar")}.popover(isPresented:$datesOpen) {DateRangeControls(from:$from,through:$through).padding(18).frame(width:360)}}
                CompactFilterPicker(title:model == "all" ? L("全部模型", "All models") : model,label:L("模型", "Model"),selection:model,items:[.init(id:"all",title:L("全部模型", "All models"))]+state.claudeModels.map {.init(id:$0,title:$0)},choose:{model = $0}).frame(width:170)
                Picker("",selection:$granularity) {Text(L("每小时", "Hourly")).tag("hour"); Text(L("每天", "Daily")).tag("day"); Text(L("每周", "Weekly")).tag("week")}.labelsHidden().pickerStyle(.segmented).fixedSize()
            }}.scrollIndicators(.hidden).frame(height:30)
            SummaryMetrics(summary:projection.value.summary)
            TrendChart(days:projection.value.days).padding(18).overlay(RoundedRectangle(cornerRadius:14).stroke(.secondary.opacity(0.15)))
            ModelShareCard(projection:projection.value,client:.claudeCode)
        }.onAppear(perform:load).onChange(of:key) {_,_ in load()}.onChange(of:period) {_,period in granularity = period == "today" ? "hour" : period == "all" ? "week" : "day"}
    }
}

private struct ClaudeRankingPage {var rows: [Object] = []; var pages = 1; var total = 0}
struct ClaudeRankingView: View {
    @ObservedObject var state: AppState
    @StateObject private var projection = AsyncProjection(ClaudeRankingPage())
    @State private var page = 0
    @State private var visible = 5
    @State private var expanded = Set<String>()
    private var key: String {"claude_code|ranking|\(state.claudeRevision)|\(page)"}
    private func load() {
        let queue = state.dataQueue, ledger = state.claudeLedger, page = page
        projection.load(key:key,revision:state.claudeRevision) {
            let data = queue.sync {(try? ledger.ranking(page:page)) ?? [:]}
            return ClaudeRankingPage(rows:data.objects("threads"),pages:data.integer("pages") ?? 1,total:data.integer("total") ?? 0)
        }
    }
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            HStack {SectionHeading(title:L("聊天用量排行", "Chat usage ranking")); Spacer(); Text(L("按 Token 排序", "Sort by tokens")).font(.system(size:12)).foregroundStyle(.secondary)}
            if projection.value.rows.isEmpty {EmptyState(title:L("暂无 Claude 聊天记录", "No Claude chats yet"))}
            VStack(spacing:0) {
                HStack {Text(L("聊天", "Chat")).frame(maxWidth:.infinity,alignment:.leading); Text(L("费用", "Cost")).frame(width:110); Text("Token").frame(width:120)}.font(.system(size:12)).foregroundStyle(.secondary).padding(18)
                ForEach(Array(projection.value.rows.prefix(visible)),id:\.threadIdentity) { row in
                    let id = row.string("thread_id")
                    Divider()
                    Button {if !expanded.insert(id).inserted {expanded.remove(id)}} label: {
                        HStack {
                            Image(systemName:expanded.contains(id) ? "chevron.down" : "chevron.right").font(.system(size:12)).foregroundStyle(.secondary)
                            Text(requestValueText(row.string("title"))).lineLimit(1).frame(maxWidth:.infinity,alignment:.leading)
                            Text("—").frame(width:110); Text(compact(row.number("tokens"))).monospacedDigit().frame(width:120)
                        }.font(.system(size:14)).padding(18).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                    if expanded.contains(id) {ClaudeRankingDetails(state:state,id:id).padding(18)}
                }
            }.clipShape(RoundedRectangle(cornerRadius:18)).overlay(RoundedRectangle(cornerRadius:18).stroke(.secondary.opacity(0.22)))
            if visible < projection.value.rows.count {Button(L("显示更多", "Show more")) {visible = min(25,visible+5)}}
            HStack {Text("\(projection.value.total) "+L("聊天", "chats")); Spacer(); Button {page = max(0,page-1)} label:{Image(systemName:"chevron.left")}.disabled(page == 0); Text("\(page+1) / \(projection.value.pages)"); Button {page += 1} label:{Image(systemName:"chevron.right")}.disabled(page+1 >= projection.value.pages)}.font(.caption).foregroundStyle(.secondary)
        }.onAppear(perform:load).onChange(of:key) {_,_ in expanded = []; visible = 5; load()}
    }
}

private struct ClaudeRankingDetails: View {
    @ObservedObject var state: AppState
    let id: String
    @StateObject private var projection = AsyncProjection([Object]())
    @State private var page = 0
    private var key: String {"claude_code|rank-detail|\(state.claudeRevision)|\(id)|\(page)"}
    private func load() {let queue = state.dataQueue, ledger = state.claudeLedger, id = id, page = page; projection.load(key:key,revision:state.claudeRevision) {queue.sync {(try? ledger.rankingDetails(id,page:page)) ?? []}}}
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            ForEach(Array(projection.value.enumerated()),id:\.offset) {_,row in
                HStack {VStack(alignment:.leading,spacing:4) {Text(requestValueText(modelName(row.string("model")))); Text(logEffortName(row.string("effort"))+" · —").font(.caption).foregroundStyle(.secondary)}; Spacer(); Text("—").frame(width:110); Text(compact(row.number("tokens"))).frame(width:120)}
            }
            HStack {Button {page = max(0,page-1)} label:{Image(systemName:"chevron.left")}.disabled(page == 0); Text("\(page+1)"); Button {page += 1} label:{Image(systemName:"chevron.right")}.disabled(projection.value.count < 25)}.font(.caption)
        }.onAppear(perform:load).onChange(of:key) {_,_ in load()}
    }
}

private struct ClaudeOriginalPayload {var text = "—"; var images: [NSImage] = []}
private func claudeOriginalPayload(_ rows: [Object]) -> ClaudeOriginalPayload {
    var texts: [String] = [], images: [NSImage] = [], used = 0
    for row in rows {
        let content = row.object("message")["content"]
        if let text = content as? String, used < 4_000_000 {let bounded = String(text.prefix(4_000_000-used)); texts.append(bounded); used += bounded.utf8.count}
        for block in content as? [Object] ?? [] {
            if ["text","thinking"].contains(block.string("type")), used < 4_000_000 {let text = String(block.string(block.string("type") == "thinking" ? "thinking" : "text").prefix(4_000_000-used)); texts.append(text); used += text.utf8.count}
            if block.string("type") == "image", images.count < 8 {
                let source = block.object("source"), encoded = source.string("data")
                if source.string("type") == "base64", encoded.utf8.count <= 10_000_000, let data = Data(base64Encoded:encoded), let image = NSImage(data:data) {images.append(image)}
            }
            if block.string("type") == "tool_use" {texts.append(block.string("name")+"\n"+jsonString(block.object("input")))}
        }
    }
    return ClaudeOriginalPayload(text:texts.isEmpty ? "—" : texts.joined(separator:"\n\n"),images:images)
}

struct ClaudeDetailView: View {
    @ObservedObject var state: AppState
    let row: UsageRow
    @StateObject private var originals = AsyncProjection(ClaudeOriginalPayload())
    @StateObject private var members = AsyncProjection([UsageRow]())
    @State private var page = 0
    private var key: String {"claude_code|detail|\(state.claudeRevision)|\(row.id)|\(page)"}
    private func load() {
        let queue = state.dataQueue, ledger = state.claudeLedger, id = row.id, page = page
        originals.discardVisible(ClaudeOriginalPayload())
        originals.load(key:key,revision:state.claudeRevision) {queue.sync {claudeOriginalPayload((try? ledger.original(id)) ?? [])}}
        members.load(key:key,revision:state.claudeRevision) {queue.sync {(try? ledger.members(id,page:page)) ?? []}}
    }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:15) {
                Text(L("请求详情", "Request details")).font(.system(size:16,weight:.semibold))
                Text(requestValueText(row.raw.string("prompt_preview"))).font(.system(size:14,weight:.medium))
                Text(dateText(row.date)).font(.caption).foregroundStyle(.secondary)
                Divider()
                field(L("模型", "Model"),requestValueText(modelName(row.raw.string("model"))))
                field(L("响应返回模型", "Response model"),requestValueText(row.raw.string("upstream_model")))
                field(L("推理强度", "Reasoning"),logEffortName(row.raw.string("reasoning_effort")))
                field(L("速度", "Speed"),"—"); field(L("费用", "Cost"),"—")
                field(L("状态", "Status"),row.raw.string("status") == "failed" ? L("失败", "Failed") : row.raw.string("status") == "completed" ? L("已完成", "Completed") : "—")
                field(L("耗时", "Duration"),durationText(row.duration))
                field("TPS",outputRateText(row.outputTokensPerSecond,unit:"tokens/s")); field("TTFT",row.firstTokenSeconds.map {String(format:"%.2f",$0)+L(" 秒", " s")} ?? "—")
                field(L("普通输入", "Uncached input"),compact(row.raw.number("raw_input_tokens")))
                field(L("输入 Token", "Input tokens"),compact(row.raw.number("input_tokens")))
                field(L("缓存读取", "Cached input"),compact(row.raw.number("cached_input_tokens")))
                field(L("缓存写入", "Cache write"),compact(row.raw.number("cache_write_input_tokens")))
                field(L("输出 Token", "Output tokens"),compact(row.raw.number("output_tokens")))
                field(L("推理输出", "Reasoning output"),compact(row.raw.number("reasoning_output_tokens")))
                field(L("上下文", "Context"),compact(row.raw.string("record_kind") == "user_request" ? row.raw.number("context_input_tokens") : row.inputTokens.map(Double.init)))
                field(L("上下文容量", "Context window"),"—")
                if row.raw.flag("usage_conflict") {StatusNote(text:L("计量片段存在冲突", "Conflicting usage observations"))}
                else if row.raw.integer("total_tokens") == nil && row.raw.integer("call_count") != 0 {StatusNote(text:L("计量信息不完整", "Incomplete usage information"))}
                Divider(); Text(L("原始正文", "Original content")).foregroundStyle(.secondary)
                Text(originals.value.text)
                ForEach(Array(originals.value.images.enumerated()),id:\.offset) {_,image in Image(nsImage:image).resizable().scaledToFit().frame(maxHeight:200)}
                if row.raw.string("record_kind") == "user_request" {
                    Divider(); Text(L("模型调用", "Model calls")+" (\(row.raw.integer("call_count") ?? 0))").fontWeight(.medium)
                    ForEach(members.value) {member in
                        DisclosureGroup(requestValueText(member.modelLabel)+" · "+logEffortName(member.raw.string("reasoning_effort"))+" · "+compact(member.tokens.map(Double.init))) {
                            ClaudeMemberOriginal(state:state,row:member)
                        }
                    }
                    HStack {Button {page = max(0,page-1)} label:{Image(systemName:"chevron.left")}.disabled(page == 0); Text("\(page+1)"); Button {page += 1} label:{Image(systemName:"chevron.right")}.disabled(members.value.count < 60)}
                }
                Divider(); Text(row.raw.string("session_id")).font(.caption).foregroundStyle(.secondary)
            }.font(.system(size:12)).padding(20).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
        }.frame(width:360,height:500).onAppear(perform:load).onChange(of:key) {_,_ in load()}
    }
    private func field(_ label: String,_ value: String) -> some View {HStack(alignment:.firstTextBaseline) {Text(label).foregroundStyle(.secondary); Spacer(minLength:12); Text(value).multilineTextAlignment(.trailing)}}
}
private struct ClaudeMemberOriginal: View {
    @ObservedObject var state: AppState
    let row: UsageRow
    @StateObject private var projection = AsyncProjection(ClaudeOriginalPayload())
    private var key: String {"claude_code|member|\(state.claudeRevision)|\(row.id)"}
    private func load() {let queue = state.dataQueue, ledger = state.claudeLedger, id = row.id; projection.discardVisible(ClaudeOriginalPayload()); projection.load(key:key,revision:state.claudeRevision) {queue.sync {claudeOriginalPayload((try? ledger.original(id)) ?? [])}}}
    var body: some View {
        VStack(alignment:.leading,spacing:10) {
            Text("— · —").foregroundStyle(.secondary)
            Text(projection.value.text)
            ForEach(Array(projection.value.images.enumerated()),id:\.offset) {_,image in Image(nsImage:image).resizable().scaledToFit().frame(maxHeight:200)}
        }.onAppear(perform:load).onChange(of:key) {_,_ in load()}
    }
}
