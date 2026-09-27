import Foundation

// The ledger-generation and time-boundary policy follows v0.2.10 UsageWorker.
final class UsageSnapshotCache {
    private struct Input: Equatable {
        let ledger: Int
        let upstream: Int
        let prices: String
        let day: Date
        let zone: String
        let offset: Int
    }
    let database: Database
    let catalog: PricingCatalog
    private var input: Input?
    private var cached: UsageSnapshot?
    private var expires = Date.distantPast
    private var checked = Date.distantPast
    private var hour: Date?
    private var upstreamStamps: [FileStamp]?
    private var upstreamModels: [String:String] = [:]
    private var upstreamGeneration = 0
    private var upstreamPresent = false
    private struct PricedRecord {
        let payload: String
        let title: String
        let local: Bool
        let upstreamPresent: Bool
        let row: UsageRow
    }
    private var pricedRecords: [String:PricedRecord] = [:]
    private var turns: [Object] = []
    private var links: [Object] = []
    init(database: Database,catalog: PricingCatalog) { self.database = database; self.catalog = catalog }
    func load(now: Date = Date()) throws -> UsageSnapshot {
        let calendar = Calendar.current
        let revision = try database.query("SELECT revision FROM usage_revisions WHERE kind='ledger'").first?.integer("revision") ?? 0
        refreshUpstream()
        let next = Input(ledger:revision,upstream:upstreamGeneration,prices:catalog.version,day:calendar.startOfDay(for:now),zone:calendar.timeZone.identifier,offset:calendar.timeZone.secondsFromGMT(for:now))
        let nextHour = calendar.dateInterval(of:.hour,for:now)?.start
        var result: UsageSnapshot
        if next == input, now >= checked, now < expires, let cached {
            result = cached
            if hour != nextHour { result.revision = UUID() }
        } else {
            if input?.ledger != next.ledger {
                turns = try database.metadata("usage_turns"); links = try database.metadata("usage_agent_links")
            }
            let reuseCalls = input?.ledger == next.ledger && input?.prices == next.prices && input?.upstream == next.upstream
            let calls = reuseCalls ? cached?.calls ?? [] : try prepareCalls()
            result = Analytics.build(records:[],turns:turns,links:links,catalog:catalog,now:now,preparedCalls:calls)
            let statusExpiry = turns.filter {$0.string("status") == "running"}.compactMap {parsedDate($0["observed_at"])?.addingTimeInterval(900)}.filter {$0 > now}.min()
            let futureRecord = result.calls.compactMap(\.date).filter {$0 > now}.min()
            expires = [statusExpiry,futureRecord,calendar.date(byAdding:.day,value:1,to:next.day)].compactMap {$0}.min() ?? .distantFuture
            input = next
        }
        result.updated = now; cached = result; checked = now; hour = nextHour
        return result
    }
    private func prepareCalls() throws -> [UsageRow] {
        var calls: [UsageRow] = [], retained: [String:PricedRecord] = [:], retainedBytes = 0
        for entry in try database.recordPayloads() {
            let id = entry.string("id"), payload = entry.string("data"), title = entry.string("title"), local = entry.integer("local_origin") == 1
            let value: UsageRow
            if let old = pricedRecords[id], old.payload == payload, old.title == title, old.local == local,
               old.upstreamPresent == upstreamPresent,
               old.row.raw.string("price_version") == catalog.version,
               (!upstreamPresent || old.row.raw.string("upstream_model") == (upstreamModels[old.row.raw.string("response_id")] ?? "")) {
                value = old.row
            } else {
                var raw = jsonObject(Data(payload.utf8))
                if !title.isEmpty { raw["session_title"] = title }
                raw["local_origin"] = local || raw.string("source_id").hasPrefix("local")
                if upstreamPresent { raw["upstream_model"] = upstreamModels[raw.string("response_id")] }
                value = UsageRow(raw:catalog.price(raw))
            }
            calls.append(value)
            let size = payload.utf8.count
            if retained.count < 8192 && retainedBytes+size <= 8_388_608 {
                retained[id] = PricedRecord(payload:payload,title:title,local:local,upstreamPresent:upstreamPresent,row:value); retainedBytes += size
            }
        }
        pricedRecords = retained
        return calls
    }
    private func refreshUpstream() {
        let file = database.url.deletingLastPathComponent().appendingPathComponent("upstream.sqlite")
        let stamps = [FileStamp(file),FileStamp(URL(fileURLWithPath:file.path+"-wal"))]
        guard upstreamStamps != stamps else { return }
        var models: [String:String] = [:]
        if stamps[0].exists {
            guard let db = try? Database(file,readOnly:true), let rows = try? db.query("SELECT response_id,model FROM observations") else { return }
            models = Dictionary(rows.map {($0.string("response_id"),$0.string("model"))},uniquingKeysWith:{$1})
        }
        if models != upstreamModels || upstreamPresent != stamps[0].exists { upstreamModels = models; upstreamGeneration += 1 }
        upstreamPresent = stamps[0].exists
        upstreamStamps = stamps
    }
}
