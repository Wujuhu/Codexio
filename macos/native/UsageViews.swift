import SwiftUI
import AppKit

struct UsageView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(alignment:.leading,spacing:0) {
            PageHeading(title:Pages.title("trends")).padding(.horizontal,32).padding(.top,32)
            HStack(spacing:28) {
                tab("activity",L("活动", "Activity")); tab("trend",L("用量趋势", "Usage trend")); tab("threads",L("聊天排行", "Top chats"))
                Spacer()
            }.padding(.horizontal,32).padding(.top,15)
            Divider().padding(.horizontal,32)
            ScrollView {
                VStack(alignment:.leading,spacing:28) {
                    switch state.usageSection {
                    case "threads": ChatRankingView(state:state)
                    case "trend": UsageTrendsView(state:state)
                    default: LocalActivityView(state:state)
                    }
                }.padding(32)
            }
        }.onChange(of:state.usageSection) { _,section in if section == "threads" { state.refreshReports() } }
    }
    private func tab(_ id: String,_ title: String) -> some View {
        Button { state.usageSection = id } label: {
            Text(title).font(.system(size:14,weight:state.usageSection == id ? .medium : .regular)).foregroundStyle(state.usageSection == id ? .primary : .secondary).padding(.bottom,13).overlay(alignment:.bottom) { if state.usageSection == id { Rectangle().frame(height:2) } }
        }.buttonStyle(.plain)
    }
}

struct LocalActivityView: View {
    @ObservedObject var state: AppState
    @State private var aggregation = "day"
    @State private var hovered: DayUsage?
    private var stats: ActivityStats { state.usage.activity }
    private var buckets: [DayUsage] {
        let days = stats.days
        if aggregation == "cumulative" {
            var total = 0
            return days.map { day in total += day.tokens ?? 0; return DayUsage(date:day.date,tokens:total,cost:nil,calls:day.calls) }
        }
        if aggregation == "week" {
            var calendar = Calendar.current; calendar.firstWeekday = 2
            var groups: [Date:DayUsage] = [:]
            for day in days {
                let start = calendar.dateInterval(of:.weekOfYear,for:day.date)!.start
                var bucket = groups[start] ?? DayUsage(date:start,tokens:0,cost:0,calls:0)
                bucket.tokens = (bucket.tokens ?? 0)+(day.tokens ?? 0); bucket.cost = (bucket.cost ?? 0)+(day.cost ?? 0); bucket.calls += day.calls
                groups[start] = bucket
            }
            return groups.values.sorted {$0.date < $1.date}
        }
        return days
    }
    var body: some View {
        VStack(alignment:.leading,spacing:30) {
            HStack(spacing:0) {
                metric(L("累计 Token 数", "Lifetime tokens"),compact(stats.total.map(Double.init)))
                Divider().frame(height:47)
                metric(L("单日峰值 Token", "Peak daily tokens"),compact(stats.peak.map(Double.init)))
                Divider().frame(height:47)
                metric(L("最长聊天时长", "Longest chat"),durationText(stats.longestChat))
                Divider().frame(height:47)
                metric(L("当前连续天数", "Current streak"),dayCount(stats.currentStreak))
                Divider().frame(height:47)
                metric(L("最长连续天数", "Longest streak"),dayCount(stats.longestStreak))
            }.padding(.vertical,22).overlay(RoundedRectangle(cornerRadius:20).stroke(.secondary.opacity(0.18)))
            HStack {
                SectionHeading(title:L("Token 活动", "Token activity"))
                Picker("",selection:$aggregation) { Text(L("每日", "Daily")).tag("day"); Text(L("每周", "Weekly")).tag("week"); Text(L("累计", "Cumulative")).tag("cumulative") }.labelsHidden().pickerStyle(.segmented).frame(width:210)
            }.padding(.top,10)
            heatmap
            Text(hovered.map {$0.date.formatted(date:.abbreviated,time:.omitted)+" · "+compact($0.tokens.map(Double.init))+" Token"} ?? " ").font(.system(size:12)).foregroundStyle(.secondary).frame(height:16)
            SectionHeading(title:L("活动洞察", "Activity insights")).padding(.top,10)
            HStack(spacing:55) {
                HStack { Text(L("快速模式", "Fast mode")).foregroundStyle(.secondary); Spacer(); Text(percent(stats.fastPercent)).monospacedDigit() }
                HStack { Text(L("最常用的推理强度", "Most used reasoning")).foregroundStyle(.secondary); Spacer(); Text(effortName(stats.effort)+" · "+percent(stats.effortPercent)).monospacedDigit() }
            }.font(.system(size:16))
            HStack {
                Text(L("本机记录 · 按模型调用次数统计模式占比", "Local records · mode shares by model calls"))
                Spacer()
                Text(L("本地扫描", "Local scan")+" "+dateText(state.usage.updated,timeOnly:true))
            }.font(.system(size:11)).foregroundStyle(.secondary)
            if stats.durationPartial || stats.unknownSpeed > 0 || stats.unknownEffort > 0 {
                StatusNote(text:[stats.durationPartial ? L("时长仅含已记录区间", "Durations include recorded intervals only") : "",stats.unknownSpeed > 0 ? L("速度未知", "Unknown speed")+" \(stats.unknownSpeed)/\(stats.calls)" : "",stats.unknownEffort > 0 ? L("推理强度未知", "Unknown reasoning")+" \(stats.unknownEffort)/\(stats.calls)" : ""].filter {!$0.isEmpty}.joined(separator:" · "))
            }
            Divider()
            Button { state.usageSection = "threads" } label: { HStack { Text(L("聊天用量排行", "Chat usage ranking")); Spacer(); Image(systemName:"arrow.right") } }.buttonStyle(.plain)
        }
    }
    private func metric(_ title: String,_ value: String) -> some View {
        VStack(spacing:9) { Text(value).font(.system(size:22,weight:.medium)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.75); Text(title).font(.system(size:12)).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.8) }.frame(maxWidth:.infinity).padding(.horizontal,8)
    }
    private func dayCount(_ count: Int) -> String { count == 1 ? L("1 天", "1 day") : "\(count) "+L("天", "days") }
    private var heatmap: some View {
        let values = buckets, maximum = max(1,values.compactMap(\.tokens).max() ?? 0)
        let offset = aggregation == "week" ? 0 : ((values.first.map {Calendar.current.component(.weekday,from:$0.date)} ?? 2)+5)%7
        let rows = aggregation == "week" ? 1 : 7
        let columns = max(1,(values.count+offset+rows-1)/rows)
        return GeometryReader { geometry in
            let side = max(4,min(16,(geometry.size.width-CGFloat(columns-1)*4)/CGFloat(columns)))
            VStack(alignment:.leading,spacing:10) {
            HStack(alignment:.top,spacing:4) {
                ForEach(0..<columns,id:\.self) { column in
                    VStack(spacing:4) {
                        ForEach(0..<rows,id:\.self) { row in
                            let index = column*rows+row-offset
                            if values.indices.contains(index) {
                                let day = values[index], amount = day.tokens ?? 0
                                RoundedRectangle(cornerRadius:3).fill(amount > 0 ? Color.blue.opacity(0.2+0.8*pow(Double(amount)/Double(maximum),0.45)) : Color.secondary.opacity(0.14)).frame(width:side,height:aggregation == "week" ? 44 : side)
                                    .onHover { inside in hovered = inside ? day : nil }
                                    .accessibilityLabel(day.date.formatted(date:.abbreviated,time:.omitted)+" "+compact(day.tokens.map(Double.init))+" Token")
                            } else { Color.clear.frame(width:side,height:side) }
                        }
                    }
                }
            }
            let months = values.indices.filter { ($0 == 0 && Calendar.current.component(.day,from:values[$0].date) <= 20) || ($0 > 0 && Calendar.current.component(.month,from:values[$0].date) != Calendar.current.component(.month,from:values[$0-1].date)) }
            ZStack(alignment:.topLeading) {
                ForEach(months,id:\.self) { index in
                    Text(values[index].date.formatted(.dateTime.month(.abbreviated)))
                        .font(.system(size:11)).foregroundStyle(.secondary)
                        .offset(x:CGFloat((index+offset)/rows)*(side+4))
                }
            }.frame(height:16)
            }
        }.frame(height:aggregation == "week" ? 70 : 164)
    }
}

struct UsageTrendsView: View {
    @ObservedObject var state: AppState
    @State private var period = "week"
    @State private var model = "all"
    @State private var granularity = "day"
    @State private var from = Calendar.current.date(byAdding:.day,value:-6,to:Date())!
    @State private var through = Date()
    private var range: UsageRange { UsageRange(period:period,from:from,through:through) }
    private var rows: [UsageRow] { state.usage.calls.filter {$0.local && range.contains($0) && (model == "all" || $0.raw.string("model") == model)} }
    private var summary: UsageSummary {
        let ids = Set(rows.map(\.id))
        let requests = state.usage.requests.filter {$0.local && !$0.raw.flag("is_subagent") && $0.raw.string("record_kind") == "user_request" && range.contains($0) && (model == "all" || !ids.isDisjoint(with: $0.raw["member_ids"] as? [String] ?? []))}.count
        return UsageSummary(rows:rows,requests:requests)
    }
    var body: some View {
        VStack(alignment:.leading,spacing:25) {
            HStack {
                PeriodPicker(selection:$period,custom:true)
                Spacer()
                Picker("",selection:$model) { Text(L("全部模型", "All models")).tag("all"); ForEach(Array(Set(state.usage.calls.map {$0.raw.string("model")})).sorted(),id:\.self) { Text($0).tag($0) } }.labelsHidden().frame(width:170)
            }
            HStack {
                if period == "custom" { DateRangeControls(from:$from,through:$through) }
                Spacer()
                Picker("",selection:$granularity) { Text(L("每小时", "Hourly")).tag("hour"); Text(L("每天", "Daily")).tag("day"); Text(L("每周", "Weekly")).tag("week") }.labelsHidden().pickerStyle(.segmented).frame(width:210)
            }
            SummaryMetrics(summary:summary)
            TrendChart(days:range.buckets(rows,granularity:granularity)).padding(20).overlay(RoundedRectangle(cornerRadius:14).stroke(.secondary.opacity(0.15)))
            StatusNote(text:L("本机记录 · 费用按模型价格估算", "Local records · cost estimated from model prices"))
            if summary.unknownCosts > 0 { StatusNote(text:L("未定价调用", "Unpriced calls")+" · \(summary.unknownCosts)") }
        }.onChange(of:period) { _,value in granularity = value == "today" ? "hour" : value == "all" ? "week" : "day" }
    }
}

struct ChatRankingView: View {
    @ObservedObject var state: AppState
    @State private var expanded: Set<String> = []
    @State private var localMode = false
    @State private var sortMetric = "weekly_limit_percent"
    @State private var page = 0
    private var rows: [Object] {
        if localMode {
            var totals: [String:Int] = [:]
            for row in state.usage.calls where row.local { totals[row.raw.string("session_id"),default:0] += row.tokens ?? 0 }
            return state.usage.chatCandidates.map { candidate in
                let ids = [candidate.string("thread_id")] + (candidate["descendant_thread_ids"] as? [String] ?? [])
                return ["thread_id":candidate.string("thread_id"),"local_tokens":ids.reduce(0) {$0+(totals[$1] ?? 0)}] as Object
            }.sorted {($0.integer("local_tokens") ?? 0) > ($1.integer("local_tokens") ?? 0)}
        }
        return state.chatUsage.objects("threads").sorted { ($0.decimal(sortMetric) ?? -1) > ($1.decimal(sortMetric) ?? -1) }
    }
    private var visible: [Object] { Array(rows.dropFirst(page*25).prefix(25)) }
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            HStack {
                SectionHeading(title:localMode ? L("本机 Token 排行", "Local token ranking") : L("聊天用量排行", "Chat usage ranking"))
                Button { localMode.toggle(); expanded.removeAll(); page = 0 } label: { Text(localMode ? L("查看额度排行", "View allowance ranking") : L("本机 Token", "Local tokens")) }.buttonStyle(.plain).foregroundStyle(.secondary)
            }
            StatusNote(text:localMode ? L("仅统计本机记录", "Computed from local records only") : L("当前周额度 · 本机可用聊天", "Current weekly allowance · local chats"))
            if state.reportsLoading && !localMode { ProgressView().controlSize(.small) }
            if let error = state.reportError, !localMode, !rows.isEmpty { StatusNote(text:error) }
            if rows.isEmpty {
                EmptyState(title:state.reportsLoading && !localMode ? L("正在读取", "Loading") : L("暂不可用", "Unavailable"),detail:localMode ? L("暂无本机聊天记录", "No local chat records") : state.reportError ?? L("当前账户尚未提供这项明细", "This account has not provided these details"))
            } else {
                VStack(spacing:0) {
                    HStack {
                        Text(L("聊天", "Chat")).frame(maxWidth:.infinity,alignment:.leading)
                        if !localMode { Button(L("占每周限额的 %", "% of weekly limit")) { sortMetric = "weekly_limit_percent" }.buttonStyle(.plain).frame(width:180,alignment:.trailing) }
                        Button(localMode ? "Token" : L("已用额度", "Credits used")) { sortMetric = "balance_usage_credits" }.buttonStyle(.plain).frame(width:115,alignment:.trailing)
                    }.font(.system(size:12)).foregroundStyle(.secondary).padding(20)
                    ForEach(visible,id:\.threadIdentity) { row in rowView(row) }
                }.clipShape(RoundedRectangle(cornerRadius:18)).overlay(RoundedRectangle(cornerRadius:18).stroke(.secondary.opacity(0.22)))
                HStack {
                    if !localMode { Text(L("统计截至", "Usage as of")+" "+dateText(parsedDate(state.chatUsage["data_as_of"]))) }
                    Spacer()
                    Button { page = max(0,page-1) } label: { Image(systemName:"chevron.left") }.disabled(page == 0)
                    Text("\(page+1) / \(max(1,(rows.count+24)/25))")
                    Button { page += 1 } label: { Image(systemName:"chevron.right") }.disabled((page+1)*25 >= rows.count)
                }.font(.system(size:11)).foregroundStyle(.secondary)
            }
        }.onAppear { if state.paths.mock && CommandLine.arguments.contains("--mock-gallery") { expanded.insert("mock-chat-0") } }
    }
    private func rowView(_ row: Object) -> some View {
        let id = row.string("thread_id"), isExpanded = expanded.contains(id)
        return VStack(spacing:0) {
            Divider()
            Button { if !expanded.insert(id).inserted { expanded.remove(id) } } label: {
                HStack(spacing:18) {
                    Image(systemName:isExpanded ? "chevron.down" : "chevron.right").font(.system(size:12)).foregroundStyle(.secondary).frame(width:15)
                    VStack(alignment:.leading,spacing:4) {
                        Text(state.usage.chatTitles[id] ?? id).lineLimit(1)
                        if row.string("data_status") == "partial" { Text(L("部分数据", "Partial data")).font(.caption).foregroundStyle(.secondary) }
                    }.frame(maxWidth:.infinity,alignment:.leading)
                    if !localMode { Text(precisePercent(row.number("weekly_limit_percent"))).monospacedDigit().frame(width:180,alignment:.trailing) }
                    Text(localMode ? compact(row.number("local_tokens")) : row.string("balance_usage_credits","—")).monospacedDigit().frame(width:115,alignment:.trailing)
                }.font(.system(size:15)).padding(20).contentShape(Rectangle())
            }.buttonStyle(.plain)
            if isExpanded {
                VStack(alignment:.leading,spacing:17) {
                    if !localMode {
                        breakdown(row,key:"model",title:L("模型", "Model"))
                        breakdown(row,key:"reasoning_effort",title:L("推理强度", "Reasoning"))
                        breakdown(row,key:"speed",title:L("速度", "Speed"))
                    }
                    Button(L("打开聊天 ↗", "Open chat ↗")) { openChat(id) }.buttonStyle(.plain).padding(.top,3)
                }.padding(.horizontal,53).padding(.bottom,24).frame(maxWidth:.infinity,alignment:.leading)
            }
        }.background(isExpanded ? Color.primary.opacity(0.025) : Color.clear)
    }
    private func breakdown(_ row: Object,key: String,title: String) -> some View {
        var groups: [String:Decimal] = [:]
        let total = row.decimal(sortMetric)
        for group in row.objects("groups") { if let amount = group.decimal(sortMetric) { groups[group.string(key,L("未知", "Unknown")),default:0] += amount } }
        let sorted = groups.sorted {$0.value > $1.value}
        return HStack(alignment:.top,spacing:15) {
            Text(title).foregroundStyle(.secondary).frame(width:100,alignment:.leading)
            if let total, total > 0, !sorted.isEmpty {
                VStack(alignment:.leading,spacing:8) {
                    ForEach(sorted,id:\.key) { name,amount in
                        HStack { Text(key == "model" ? modelName(name) : key == "reasoning_effort" ? effortName(name) : ["priority","fast"].contains(name) ? L("快速模式", "Fast mode") : ["standard","default"].contains(name) ? L("标准", "Standard") : name); Text(percent(NSDecimalNumber(decimal:amount/total*100).doubleValue,digits:1)).foregroundStyle(.secondary) }
                    }
                    let rest = total-sorted.reduce(0) {$0+$1.value}
                    if rest > max(0.000001,total*0.0001) { Text(L("未归类", "Unattributed")+" "+percent(NSDecimalNumber(decimal:rest/total*100).doubleValue,digits:1)).foregroundStyle(.secondary) }
                }
            } else { Text("—").foregroundStyle(.secondary) }
            Spacer(minLength:0)
        }.font(.system(size:14))
    }
    private func precisePercent(_ value: Double?) -> String { guard let value else { return "—" }; return percent(value,digits:value > 0 && value < 0.01 ? 4 : 2) }
    private func openChat(_ id: String) {
        guard UUID(uuidString:id) != nil, let url = URL(string:"codex://threads/"+id), NSWorkspace.shared.urlForApplication(toOpen:url) != nil else { state.errorMessage = L("无法在本机定位此聊天", "This chat cannot be opened locally"); return }
        NSWorkspace.shared.open(url)
    }
}

extension Dictionary where Key == String, Value == Any {
    var threadIdentity: String { string("thread_id") }
}
