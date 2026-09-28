import Foundation
import Combine
import SwiftUI

final class ScanClock: ObservableObject {
    @Published var updated: Date?
}

struct ScanStamp: View {
    @ObservedObject var clock: ScanClock
    var body: some View {
        Text(lastUpdateText(clock.updated))
    }
}

// Observe fetch activity only in the toolbar, not through global AppState.
final class FetchActivity: ObservableObject {
    @Published private(set) var busy = false
    private var operations = Set<UUID>()
    func begin() -> UUID { let id = UUID(); operations.insert(id); if !busy {busy = true}; return id }
    func end(_ id: UUID) { operations.remove(id); let next = !operations.isEmpty; if busy != next {busy = next} }
    func clear() {operations.removeAll(); if busy {busy = false}}
}

struct FetchRefreshControls: View {
    @ObservedObject var activity: FetchActivity
    let clock: ScanClock
    let refresh: () -> Void
    var body: some View {
        HStack(spacing:8) {
            if activity.busy {ProgressView().controlSize(.mini).frame(width:12,height:12).accessibilityLabel(L("正在抓取数据", "Fetching data"))}
            ScanStamp(clock:clock).font(.system(size:12)).foregroundStyle(.secondary)
            Button(action:refresh) {Image(systemName:"arrow.clockwise").font(.system(size:11))}.buttonStyle(.plain).disabled(activity.busy).help(L("刷新", "Refresh")).accessibilityLabel(L("刷新", "Refresh"))
        }
    }
}

final class AsyncProjection<Value>: ObservableObject {
    @Published private(set) var value: Value
    private var requested = ""
    private var generation = 0
    private var cache: [String:Value] = [:]
    private var order: [String] = []
    private var work: DispatchWorkItem?
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.presentation",qos:.userInitiated)
    init(_ initial: Value) { value = initial }
    func load(key: String,compute: @escaping () -> Value) {
        guard key != requested else { return }
        requested = key; generation += 1; let token = generation
        work?.cancel()
        if let cached = cache[key] { value = cached; return }
        let next = DispatchWorkItem { [weak self] in
            let result = compute()
            DispatchQueue.main.async {
                guard let self, self.generation == token else { return }
                self.cache[key] = result; self.order.append(key)
                while self.order.count > 12 { self.cache.removeValue(forKey:self.order.removeFirst()) }
                self.value = result
            }
        }
        work = next; queue.asyncAfter(deadline:.now()+0.03,execute:next)
    }
}

struct TrendProjection {
    var summary = UsageSummary()
    var days: [DayUsage] = []
    var recent: [UsageRow] = []
    var modelRows: [[MobileModel]] = [[],[],[]]
    static func build(_ snapshot: UsageSnapshot,range: UsageRange,model: String,granularity: String) -> TrendProjection {
        let rows = snapshot.calls.filter {$0.local && range.contains($0) && (model == "all" || $0.raw.string("model") == model)}
        let ids = model == "all" ? Set<String>() : Set(rows.map(\.id))
        let requests = snapshot.requests.filter {$0.local && !$0.raw.flag("is_subagent") && $0.raw.string("record_kind") == "user_request" && range.contains($0) && (model == "all" || !ids.isDisjoint(with:$0.raw["member_ids"] as? [String] ?? []))}
        let callsByModel = Dictionary(grouping:rows,by:{$0.raw.string("model")})
        let requestsByModel = Dictionary(grouping:requests,by:{$0.raw.string("model")})
        let models = Set(callsByModel.keys).union(requestsByModel.keys).filter(MobileTrends.isSingleModel).map { key -> MobileModel in
            let total = UsageSummary(rows:callsByModel[key] ?? [],requests:requestsByModel[key]?.count ?? 0)
            return MobileModel(id:key,name:modelName(key),metric:MobileMetric(tokens:total.tokens,cost:total.cost,requests:total.requests,costComplete:total.unknownCosts == 0,hitRate:total.cacheRate))
        }
        let sorted = (0..<3).map { metric in models.sorted { a,b in let x = ModelShareCard.amount(a.metric,metric), y = ModelShareCard.amount(b.metric,metric); return x == y ? a.id < b.id : x > y } }
        return TrendProjection(summary:UsageSummary(rows:rows,requests:requests.count),days:range.buckets(rows,requests:requests,granularity:granularity),recent:Array(requests.prefix(3)),modelRows:sorted)
    }
}

struct LogProjection {
    var revision = UUID()
    var scrollResetKey = ""
    var rows: [UsageRow] = []
    var count = 0
    var unassigned = 0
    var page = 0
    var pages: Int { max(1,(count+59)/60) }
    static func build(_ snapshot: UsageSnapshot,range: UsageRange,mode: String,model: String,tier: String,status: String,query: String,page: Int,filterKey: String = "") -> LogProjection {
        let rows = (mode == "requests" ? snapshot.requests : snapshot.calls).filter { row in
            range.contains(row) && (model == "all" || row.raw.string("model").components(separatedBy:" + ").contains(model)) &&
            (tier == "all" || normalizedTier(row.raw.string("service_tier")) == tier) &&
            (mode == "calls" || status == "all" || row.raw.string("status","completed") == status) &&
            (query.isEmpty || [row.title,row.raw.string("prompt_preview"),row.raw.string("output_preview"),row.raw.string("session_id"),row.id].joined(separator:" ").localizedCaseInsensitiveContains(query))
        }
        let selected = min(page,max(0,(rows.count-1)/60))
        return LogProjection(scrollResetKey:filterKey+":"+String(selected),rows:Array(rows.dropFirst(selected*60).prefix(60)),count:rows.count,unassigned:rows.filter { $0.raw.string("record_kind") == "unassigned" }.count,page:selected)
    }
}
