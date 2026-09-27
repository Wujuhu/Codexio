import Foundation
import SwiftUI

final class WeeklyEstimator {
    let database: Database
    private var anchor: Object?
    private var checkpoint: Object?
    private var latest: Object?
    private var pending: [Object] = []
    private var interval = 30
    init(_ database: Database) throws {
        self.database = database
        try database.script("CREATE TABLE IF NOT EXISTS usage_week_intervals(id INTEGER PRIMARY KEY AUTOINCREMENT,end_at TEXT NOT NULL,data TEXT NOT NULL); CREATE INDEX IF NOT EXISTS week_intervals_end ON usage_week_intervals(end_at DESC,id DESC);")
    }
    func invalidate() { anchor = nil; checkpoint = nil; latest = nil; pending.removeAll() }
    func add(_ sample: Object, intervalMinutes: Int) {
        if interval != intervalMinutes { interval = intervalMinutes; invalidate() }
        guard let stamp = sample.number("timestamp"), let used = sample.number("used_percent"), used >= 0, used <= 100, let reset = sample.number("reset_at"), reset > stamp, !sample.string("account_key").isEmpty, !sample.string("plan_type").isEmpty, sample.string("limit_id") == "codex" else { invalidate(); return }
        if let old = latest {
            let changed = ["account_key","plan_type","limit_id"].contains { old.string($0) != sample.string($0) } || old.flag("sole_codex_pool") != sample.flag("sole_codex_pool")
            if changed || abs(reset-(old.number("reset_at") ?? 0)) > 60 || stamp <= (old.number("timestamp") ?? 0) || stamp-(old.number("timestamp") ?? 0) > 600 { invalidate() }
        }
        latest = sample
        if anchor == nil { anchor = sample; checkpoint = sample } else { pending.append(sample); if pending.count > 180 { pending.removeFirst(pending.count-180) } }
    }
    private func usage(_ calls: [UsageRow], start: Double,end: Double,sole: Bool) -> (Int,Double,Bool) {
        var tokens = 0, dollars = 0.0, invalid = false
        for row in calls where row.local && ["openai","codexio-upstream"].contains(row.raw.string("provider")) {
            guard let stamp = row.date?.timeIntervalSince1970, stamp > start, stamp <= end else { continue }
            let bucket = row.raw.string("limit_id")
            guard bucket == "codex" || (sole && bucket.isEmpty), let count = row.tokens, let cost = row.cost else { invalid = true; continue }
            tokens += count; dollars += cost
        }
        return (tokens,dollars,invalid)
    }
    func process(calls: [UsageRow],priceVersion: String,now: Date = Date()) throws -> [Object] {
        if let checkpoint, let start = anchor, let sample = pending.last(where:{ ($0.number("timestamp") ?? .infinity) <= now.timeIntervalSince1970-120 && ($0.number("timestamp") ?? 0)-(checkpoint.number("timestamp") ?? 0) >= Double(interval)*60 }) {
            self.checkpoint = sample
            let end = sample.number("timestamp") ?? 0, beginning = start.number("timestamp") ?? 0
            pending.removeAll { ($0.number("timestamp") ?? 0) <= end }
            let delta = (sample.number("used_percent") ?? 0)-(start.number("used_percent") ?? 0)
            if delta != 0 { anchor = sample }
            if delta > 0 {
                let (tokens,dollars,invalid) = usage(calls,start:beginning,end:end,sole:start.flag("sole_codex_pool"))
                if !invalid && dollars > 0 {
                    let row: Object = ["start":iso(Date(timeIntervalSince1970:beginning)),"end":iso(Date(timeIntervalSince1970:end)),"account_key":sample.string("account_key"),"plan_type":sample.string("plan_type"),"limit_id":"codex","reset_at":sample["reset_at"]!,"sole_codex_pool":start.flag("sole_codex_pool"),"start_percent":start["used_percent"]!,"end_percent":sample["used_percent"]!,"delta_percent":delta,"consumed_tokens":tokens,"consumed_usd":dollars,"estimated_total_usd":100*dollars/delta,"price_version":priceVersion]
                    try database.run("INSERT INTO usage_week_intervals(end_at,data) VALUES(?,?)",[row.string("end"),jsonString(row)])
                    try database.run("DELETE FROM usage_week_intervals WHERE id NOT IN (SELECT id FROM usage_week_intervals ORDER BY end_at DESC,id DESC LIMIT 100)")
                }
            }
        }
        let rows = try database.query("SELECT id,data FROM usage_week_intervals ORDER BY end_at DESC,id DESC LIMIT 100")
        var result: [Object] = []
        for stored in rows {
            var row = jsonObject(Data(stored.string("data").utf8))
            if row.string("price_version") != priceVersion, let start = parsedDate(row["start"]), let end = parsedDate(row["end"]), let delta = row.number("delta_percent"), delta > 0 {
                let (tokens,cost,invalid) = usage(calls,start:start.timeIntervalSince1970,end:end.timeIntervalSince1970,sole:row.flag("sole_codex_pool"))
                row["consumed_tokens"] = tokens; row["consumed_usd"] = invalid ? NSNull() : cost as Any
                row["estimated_total_usd"] = !invalid && cost > 0 ? 100*cost/delta as Any : NSNull(); row["price_version"] = priceVersion
                try database.run("UPDATE usage_week_intervals SET data=? WHERE id=?",[jsonString(row),stored.integer("id") ?? 0])
            }
            row["id"] = stored["id"]; result.append(row)
        }
        return result
    }
}

struct EstimateHistoryView: View {
    @ObservedObject var state: AppState
    var body: some View {
        LazyVStack(alignment:.leading,spacing:10) {
            if state.weeklyEstimates.isEmpty { StatusNote(text:L("等待同一周期内足够的额度与本机消费记录", "Waiting for enough allowance and local usage records within one period")) }
            ForEach(state.weeklyEstimates,id:\.estimateIdentity) { row in
                HStack {
                    Text(dateText(parsedDate(row["start"]))+" – "+dateText(parsedDate(row["end"]))).foregroundStyle(.secondary)
                    Spacer()
                    Text(percent(row.number("delta_percent"),digits:2)).monospacedDigit()
                    Text(money(row.number("estimated_total_usd"))).fontWeight(.medium).monospacedDigit()
                }.font(.system(size:12))
                Divider()
            }
            StatusNote(text:L("整周估值依据本机消费与账户额度变化，仅供参考", "Full-week estimates compare local cost with account allowance changes"))
        }.padding(.top,15)
    }
}
extension Dictionary where Key == String, Value == Any { var estimateIdentity: Int { integer("id") ?? 0 } }
