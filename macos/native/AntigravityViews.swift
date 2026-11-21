import SwiftUI

struct AntigravityQuotaView: View {
    @ObservedObject var state: AppState
    var body: some View {
        AdaptiveRow(spacing: 12) {
            QuotaCard(window: state.menuWindow(.antigravity, week: false), title: L("5 小时额度", "5-hour limit"), fresh: state.antigravityQuota.observed != nil, compactLayout: true)
            QuotaCard(window: state.menuWindow(.antigravity, week: true), title: L("周额度", "Weekly limit"), fresh: state.antigravityQuota.observed != nil, compactLayout: true)
        }
        if let message = state.antigravityQuota.message { StatusNote(text: message) }
    }
}

struct AntigravitySubscriptionView: View {
    @ObservedObject var state: AppState
    @State private var showLocalHistory = true
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 23) {
                AntigravityQuotaView(state: state)
                DisclosureGroup(L("周额度估算", "Weekly allowance estimates"), isExpanded: $showLocalHistory) {
                    EstimateHistoryView(state: state, rows: state.antigravityWeeklyEstimates)
                }.font(.system(size: 13))
            }.padding(.horizontal, PageLayout.inset).padding(.bottom, PageLayout.inset)
        }
    }
}

private func antigravityGridRow(_ row: UsageRow, state: AppState, timeOnly: Bool, showYear: Bool = false) -> GridRow {
    var grid = LogFields.row(row, timeOnly: timeOnly, showYear: showYear, details: { [] })
    let text = grid.text
    grid.text = { key in
        var cell = text(key)
        if key == "content", row.raw.string("prompt_preview").isEmpty {
            cell.main = "—"
        }
        return cell
    }
    grid.detail = nil
    grid.customDetail = { AnyView(AntigravityDetailView(state: state, row: row)) }
    return grid
}

struct AntigravityOverviewView: View {
    @ObservedObject var state: AppState
    var overview = true
    @StateObject private var projection = AsyncProjection(TrendProjection())
    @State private var period = "today"
    @State private var model = "all"
    @State private var from = Calendar.current.date(byAdding: .day, value: -6, to: Date()) ?? Date()
    @State private var through = Date()
    @State private var datesOpen = false

    private var key: String { "antigravity|trend|\(state.antigravityRevision)|\(period)|\(model)|\(from)|\(through)|\(TimeZone.current.identifier)" }

    private func load() {
        let queue = state.dataQueue, ledger = state.antigravityLedger
        let range = UsageRange(period: period, from: from, through: through), model = model
        let granularity = period == "today" ? "hour" : period == "all" ? "week" : "day"
        projection.load(key: key, revision: state.antigravityRevision) {
            queue.sync { (try? ledger.trend(range: range, model: model, granularity: granularity)) ?? TrendProjection() }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            if overview { AntigravityQuotaView(state: state) }
            HStack {
                if overview { SectionHeading(title: L("用量", "Usage")) }
                Spacer()
                PeriodPicker(selection: $period, custom: !overview, condensed: !overview).fixedSize()
                if !overview, period == "custom" {
                    Button { datesOpen = true } label: { Image(systemName: "calendar") }
                        .popover(isPresented: $datesOpen) { DateRangeControls(from: $from, through: $through).padding(18).frame(width: 360) }
                }
                if !overview {
                    CompactFilterPicker(title: model == "all" ? L("全部模型", "All models") : model, label: L("模型", "Model"), selection: model,
                        items: [.init(id: "all", title: L("全部模型", "All models"))] + state.antigravityModels.map { .init(id: $0, title: $0) }, choose: { model = $0 }).frame(width: 170)
                }
            }
            IncompleteUsageNotice(state: state, hasIncomplete: state.antigravityWarning != nil || projection.value.summary.incompleteRecords > 0,
                                  message: state.antigravityWarning)
            SummaryMetrics(summary: projection.value.summary)
            TrendChart(days: projection.value.days).padding(18).overlay(RoundedRectangle(cornerRadius: 14).stroke(.secondary.opacity(0.15)))
            if !projection.value.modelRows[0].isEmpty { ModelShareCard(projection: projection.value, client: .antigravity) }
            if overview {
                HStack {
                    SectionHeading(title: L("最近请求", "Recent requests")); Spacer()
                    Button(L("查看全部", "View all")) { state.selectedPage = "logs" }.buttonStyle(.plain).foregroundStyle(.secondary)
                }
                if projection.value.recent.isEmpty {
                    EmptyState(title: L("暂无 Antigravity 请求", "No Antigravity requests yet"))
                } else {
                    let keys = ["content", "model", "total", "cost", "duration", "details"]
                    CompactTable(columns: LogFields.columns(keys), rows: projection.value.recent.map { antigravityGridRow($0, state: state, timeOnly: period == "today") },
                                 revision: projection.value.revision.uuidString, preferences: state.preferences, storageKey: "antigravity-overview-recent", verticalScrolling: false)
                        .frame(height: CGFloat(projection.value.recent.count)*45 + 42)
                        .clipShape(RoundedRectangle(cornerRadius: 14)).overlay(RoundedRectangle(cornerRadius: 14).stroke(.secondary.opacity(0.15)))
                }
            }
        }.onAppear {
            if !overview { period = "week" }
            load()
        }.onChange(of: key) { _, _ in load() }
    }
}

struct AntigravityLogsView: View {
    @ObservedObject var state: AppState
    @StateObject private var projection = AsyncProjection(LogProjection())
    @State private var timingStamp: Date?
    @State private var mode: String
    @State private var period: String
    @State private var model: String
    @State private var status: String
    @State private var query: String
    @State private var page: Int
    @State private var from: Date
    @State private var through: Date

    init(state: AppState) {
        self.state = state
        let saved = state.logFilters[.antigravity] ?? LogFilterMemory()
        _mode = State(initialValue: saved.mode); _period = State(initialValue: saved.period); _model = State(initialValue: saved.model)
        _status = State(initialValue: saved.status); _query = State(initialValue: saved.query); _page = State(initialValue: saved.page)
        _from = State(initialValue: saved.from); _through = State(initialValue: saved.through)
    }
    private var fields: [String] {
        let saved = state.preferences.analytics["antigravity_log_columns"] as? [String] ?? LogFields.defaults
        return ["content"] + LogFields.all.filter { $0 != "content" && $0 != "details" && saved.contains($0) } + ["details"]
    }
    private var filter: String { [mode, period, model, status, query, String(from.timeIntervalSince1970), String(through.timeIntervalSince1970)].joined(separator: "|") }
    private var key: String { "\(state.antigravityRevision)|\(timingStamp?.timeIntervalSince1970 ?? 0)|\(filter)|\(page)|\(TimeZone.current.identifier)" }
    private func load() {
        let ledger = state.antigravityLedger, queue = state.dataQueue
        let range = UsageRange(period: period, from: from, through: through), mode = mode, model = model, status = status, query = query, page = page
        projection.load(key: key, revision: state.antigravityRevision) {
            queue.sync { (try? ledger.logPage(range: range, mode: mode, model: model, status: status, query: query, page: page)) ?? LogProjection() }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            LogFilterBar(period: $period, mode: $mode, model: $model, status: $status, query: $query, from: $from, through: $through,
                         models: state.antigravityModels, fields: fields) { state.setPreference("antigravity_log_columns", $0) }
            IncompleteUsageNotice(state: state, hasIncomplete: state.antigravityWarning != nil, message: state.antigravityWarning)
            CompactTable(columns: LogFields.columns(fields), rows: projection.value.rows.map {
                antigravityGridRow($0, state: state, timeOnly: period == "today", showYear: projection.value.showYear)
            }, revision: projection.value.revision.uuidString, preferences: state.preferences, storageKey: "antigravity-logs", scrollResetKey: filter + String(page))
                .overlay { if projection.value.rows.isEmpty { EmptyState(title: L("暂无 Antigravity 记录", "No Antigravity records yet")) } }
            HStack {
                let count = mode == "requests" ? projection.value.count - projection.value.unassigned : projection.value.count
                Text("\(count) " + (mode == "requests" ? L("请求组", "request groups") : L("模型调用", "model calls")))
                Spacer()
                AntigravityPager(page: $page, pages: projection.value.pages)
            }.font(.caption).foregroundStyle(.secondary)
        }.padding(.horizontal, PageLayout.inset).padding(.bottom, PageLayout.inset)
            .onAppear(perform: load).onChange(of: key) { _, _ in load() }
            .onReceive(state.antigravityTimingClock.$updated.dropFirst()) { timingStamp = $0 }
            .onChange(of: filter) { _, _ in page = 0 }
            .onChange(of: projection.value.pages) { _, pages in if page >= pages { page = max(0, pages - 1) } }
            .onDisappear { state.logFilters[.antigravity] = LogFilterMemory(mode: mode, period: period, model: model, status: status, query: query, page: page, from: from, through: through) }
    }
}

struct AntigravityUsageView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 28) {
                tab("activity", L("活动", "Activity")); tab("trend", L("详情", "Details")); tab("threads", L("聊天排行", "Top chats")); Spacer()
            }.padding(.horizontal, PageLayout.inset)
            Divider().padding(.horizontal, PageLayout.inset)
            ScrollView {
                Group {
                    switch state.usageSection {
                    case "threads": AntigravityRankingView(state: state)
                    case "trend": AntigravityOverviewView(state: state, overview: false)
                    default: AntigravityActivityView(state: state)
                    }
                }.padding(PageLayout.inset)
            }
        }
    }
    private func tab(_ id: String, _ title: String) -> some View {
        Button { state.usageSection = id } label: {
            Text(title).font(.system(size: 14, weight: state.usageSection == id ? .medium : .regular))
                .foregroundStyle(state.usageSection == id ? .primary : .secondary).padding(.bottom, 13)
                .overlay(alignment: .bottom) { if state.usageSection == id { Rectangle().frame(height: 2) } }
        }.buttonStyle(.plain)
    }
}

private struct AntigravityActivityView: View {
    @ObservedObject var state: AppState
    @StateObject private var projection = AsyncProjection(ActivityStats())
    var body: some View {
        LocalActivityView(scopedActivity: projection.value, includeJourney: false, state: state)
            .onAppear(perform: load).onChange(of: state.antigravityRevision) { _, _ in load() }
    }
    private func load() {
        let queue = state.dataQueue, ledger = state.antigravityLedger
        projection.load(key: state.antigravityRevision.uuidString, revision: state.antigravityRevision) {
            queue.sync { (try? ledger.activity()) ?? ActivityStats() }
        }
    }
}

private struct AntigravityRankingView: View {
    @ObservedObject var state: AppState
    @StateObject private var projection = AsyncProjection((rows: [Object](), total: 0))
    @State private var page = 0
    private var key: String { "\(state.antigravityRevision)|\(page)" }
    private var pages: Int { max(1, (projection.value.total + 49) / 50) }
    private func load() {
        let queue = state.dataQueue, ledger = state.antigravityLedger, page = page
        projection.load(key: key, revision: state.antigravityRevision) {
            queue.sync { (try? ledger.ranking(page: page)) ?? ([], 0) }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeading(title: L("聊天用量排行", "Chat usage ranking"))
            if projection.value.rows.isEmpty { EmptyState(title: L("暂无 Antigravity 聊天记录", "No Antigravity chats yet")) }
            LazyVStack(spacing: 0) {
                ForEach(projection.value.rows, id: \.antigravitySession) { row in
                    HStack {
                        Text(requestValueText(row.string("title"))).lineLimit(1); Spacer()
                        Text("\(row.integer("requests") ?? 0) " + L("次请求", "requests")).monospacedDigit().foregroundStyle(.secondary)
                    }.padding(12)
                    Divider()
                }
            }
            AntigravityPager(page: $page, pages: pages).font(.caption)
        }.onAppear(perform: load).onChange(of: key) { _, _ in load() }
    }
}

private extension Object {
    var antigravitySession: String { string("session") }
}

private struct AntigravityPager: View {
    @Binding var page: Int
    let pages: Int
    var body: some View {
        HStack {
            Button { page = max(0, page - 1) } label: { Image(systemName: "chevron.left") }.disabled(page == 0)
            Text("\(page + 1) / \(pages)")
            Button { page += 1 } label: { Image(systemName: "chevron.right") }.disabled(page + 1 >= pages)
        }
    }
}

private struct AntigravityDetailView: View {
    @ObservedObject var state: AppState
    let row: UsageRow
    @StateObject private var projection = AsyncProjection(AntigravityDetail())
    @State private var page = 0
    @State private var timingStamp: Date?
    private var key: String { "\(state.antigravityRevision)|\(timingStamp?.timeIntervalSince1970 ?? 0)|\(row.id)|\(page)" }
    private func load() {
        let queue = state.dataQueue, ledger = state.antigravityLedger, row = row, page = page
        projection.load(key: key, revision: state.antigravityRevision) {
            queue.sync { (try? ledger.detail(row, page: page)) ?? AntigravityDetail() }
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                let detail = projection.value
                Text(requestValueText(row.raw.string("model_display_name", modelName(row.raw.string("model"))))).font(.headline)
                HStack { Text(dateText(row.date)); Spacer(); Text(logStatusName(row.raw.string("status"))) }.font(.caption).foregroundStyle(.secondary)
                HStack {
                    Text(L("总 Token", "Total tokens") + " " + compact(row.tokens.map(Double.init)))
                    Spacer(); Text(L("费用", "Cost") + " " + row.costText)
                }.font(.caption).monospacedDigit()
                timing(detail.timing ?? row)
                if row.raw.string("record_kind") == "user_request" { block(L("用户请求", "User request"), detail.prompt) }
                block(L("回复", "Response"), detail.answer)
                if row.raw.flag("antigravity_truncated") || detail.answerTruncated {
                    Text(L("源记录中的正文已截断", "The source transcript truncated this text")).font(.caption).foregroundStyle(.secondary)
                }
                if !detail.counters.isEmpty {
                    Divider(); Text(L("源记录 Token", "Source token counters")).font(.subheadline.weight(.medium))
                    ForEach(detail.counters.keys.sorted(), id: \.self) { key in
                        HStack { Text(key); Spacer(); Text(compact(detail.counters.number(key))).monospacedDigit() }.font(.caption)
                    }
                }
                if !detail.members.isEmpty {
                    Divider(); Text(L("模型调用", "Model calls")).font(.subheadline.weight(.medium))
                    ForEach(detail.members) { member in
                        HStack { Text(requestValueText(member.raw.string("model_display_name"))); Spacer(); Text(compact(member.tokens.map(Double.init))).monospacedDigit(); Text(member.costText) }.font(.caption)
                        timing(member)
                    }
                    AntigravityPager(page: $page, pages: detail.pages).font(.caption)
                }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(18)
        }.frame(width: 480, height: 460).onAppear(perform: load).onChange(of: key) { _, _ in load() }
            .onReceive(state.antigravityTimingClock.$updated.dropFirst()) { timingStamp = $0 }
    }

    private func timing(_ row: UsageRow) -> some View {
        let ttft = row.firstTokenSeconds.map { String(format: "%.2f", $0) + L(" 秒", " s") } ?? "—"
        return HStack(spacing: 14) {
            Text(L("耗时", "Duration") + " " + durationText(row.duration))
            Text("TPS " + outputRateText(row.outputTokensPerSecond, unit: "tokens/s"))
            Text("TTFT " + ttft)
        }.font(.caption).monospacedDigit().foregroundStyle(.secondary)
    }
    private func block(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label).font(.subheadline.weight(.medium))
            Text(requestValueText(value)).font(.system(size: 13)).textSelection(.enabled)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
            .background(.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
    }
}
