import Foundation
import Combine
import SwiftUI

final class ScanClock: ObservableObject {
    @Published var updated: Date?
}

struct ScanStamp: View {
    @ObservedObject var clock: ScanClock
    var relative = false
    var body: some View {
        if let date = clock.updated {
            if relative { Text(date,style:.relative) }
            else { Text(dateText(date,timeOnly:true)) }
        } else { Text("—") }
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
    static func build(_ snapshot: UsageSnapshot,range: UsageRange,model: String,granularity: String) -> TrendProjection {
        let rows = snapshot.calls.filter {$0.local && range.contains($0) && (model == "all" || $0.raw.string("model") == model)}
        let ids = model == "all" ? Set<String>() : Set(rows.map(\.id))
        let requests = snapshot.requests.filter {$0.local && !$0.raw.flag("is_subagent") && $0.raw.string("record_kind") == "user_request" && range.contains($0) && (model == "all" || !ids.isDisjoint(with:$0.raw["member_ids"] as? [String] ?? []))}
        return TrendProjection(summary:UsageSummary(rows:rows,requests:requests.count),days:range.buckets(rows,granularity:granularity),recent:Array(requests.prefix(4)))
    }
}

struct LogProjection {
    var revision = UUID()
    var rows: [UsageRow] = []
    var count = 0
    var page = 0
    var pages: Int { max(1,(count+59)/60) }
    static func build(_ snapshot: UsageSnapshot,range: UsageRange,mode: String,model: String,tier: String,status: String,query: String,page: Int) -> LogProjection {
        let rows = (mode == "requests" ? snapshot.requests : snapshot.calls).filter { row in
            range.contains(row) && (model == "all" || row.raw.string("model").contains(model)) &&
            (tier == "all" || normalizedTier(row.raw.string("service_tier")) == tier) &&
            (mode == "calls" || status == "all" || row.raw.string("status","completed") == status) &&
            (query.isEmpty || [row.title,row.raw.string("prompt_preview"),row.raw.string("output_preview"),row.raw.string("session_id"),row.id].joined(separator:" ").localizedCaseInsensitiveContains(query))
        }
        let selected = min(page,max(0,(rows.count-1)/60))
        return LogProjection(rows:Array(rows.dropFirst(selected*60).prefix(60)),count:rows.count,page:selected)
    }
}
