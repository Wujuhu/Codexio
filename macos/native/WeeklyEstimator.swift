import Foundation
import SwiftUI

final class WeeklyEstimator {
    let database: Database
    private let limitID: String
    private let intervalUsage: ((Double,Double)->(Int,Double,Bool))?
    private var anchor: Object?
    private var latest: Object?
    private var completed: [(Object,Object,Int)] = []
    private var interval = 10
    private var acceptAfter = Date().timeIntervalSince1970
    private var lastCheck: Date?
    private var lastUptime: TimeInterval?
    private var cachedRows: [Object]?
    private var cachedPrices = ""
    private var cachedAccount: String?
    private var databaseVersion: Int?
    private(set) var revision = 0
    private static let boundaryTolerance = 5.0
    init(_ database: Database, limitID: String = "codex", usage: ((Double,Double)->(Int,Double,Bool))? = nil) throws {
        self.database = database; self.limitID = limitID; intervalUsage = usage
        try database.script("CREATE TABLE IF NOT EXISTS usage_week_intervals(id INTEGER PRIMARY KEY AUTOINCREMENT,end_at TEXT NOT NULL,data TEXT NOT NULL); CREATE INDEX IF NOT EXISTS week_intervals_end ON usage_week_intervals(end_at DESC,id DESC);")
    }
    // Only unfinished observation windows are discarded. Completed windows may
    // still wait for their local usage records to mature.
    func invalidate() {anchor = nil; latest = nil; acceptAfter = Date().timeIntervalSince1970}
    func interrupt() {invalidate(); completed.removeAll()}
    func add(_ input: Object, intervalMinutes: Int) {
        let minutes = [10,30,60].contains(intervalMinutes) ? intervalMinutes : 10
        if interval != minutes {interval = minutes; anchor = nil; latest = nil}
        var sample = input
        if limitID == "claude", sample.string("plan_type").isEmpty {sample["plan_type"] = "claude"}
        guard let stamp = sample.number("timestamp"), stamp >= acceptAfter,
              stamp <= Date().timeIntervalSince1970+Self.boundaryTolerance,
              let used = sample.number("used_percent"), used.isFinite, (0...100).contains(used),
              let reset = sample.number("reset_at"), reset > stamp,
              !sample.string("account_key").isEmpty, !sample.string("plan_type").isEmpty,
              sample.string("limit_id") == limitID else {invalidate(); return}
        if let old = latest {
            let previous = old.number("timestamp") ?? 0
            if stamp <= previous {return}
            let changed = ["account_key","plan_type","limit_id"].contains {old.string($0) != sample.string($0)} || old.flag("sole_codex_pool") != sample.flag("sole_codex_pool")
            let gap = max(45,min(330,sample.number("max_gap_seconds") ?? 90))
            if changed || abs(reset-(old.number("reset_at") ?? 0)) > 1 || used < (old.number("used_percent") ?? used) || stamp-previous > gap {
                anchor = nil
            }
        }
        latest = sample
        let eligible = sample.flag("boundary_sample",true)
        guard let start = anchor, let beginning = start.number("timestamp") else {if eligible {anchor = sample}; return}
        let due = beginning+Double(interval)*60
        if stamp > due+Self.boundaryTolerance {anchor = eligible ? sample : nil; return}
        if eligible && abs(stamp-due) <= Self.boundaryTolerance {
            completed.append((start,sample,interval)); if completed.count > 12 {completed.removeFirst(completed.count-12)}
            // Every window advances, including zero-consumption / unchanged-quota
            // windows. They must never be folded into a longer observation.
            anchor = sample
        }
    }
    private func usage(_ calls: [UsageRow], start: Double,end: Double,sole: Bool) -> (Int,Double,Bool) {
        if let intervalUsage {return intervalUsage(start,end)}
        var tokens = 0, dollars = 0.0, invalid = false
        for row in calls where row.local && ["openai","codexio-upstream"].contains(row.raw.string("provider")) {
            guard let stamp = row.date?.timeIntervalSince1970, stamp > start, stamp <= end else { continue }
            let bucket = row.raw.string("limit_id")
            guard bucket == "codex" || (sole && bucket.isEmpty), let count = row.tokens, let cost = row.cost else { invalid = true; continue }
            tokens += count; dollars += cost
        }
        return (tokens,dollars,invalid)
    }
    func process(calls: [UsageRow],priceVersion: String,now: Date = Date(),accountKey: String? = nil) throws -> [Object] {
        let uptime = ProcessInfo.processInfo.systemUptime
        if let previous = lastCheck, let previousUptime = lastUptime {
            let wall = now.timeIntervalSince(previous), elapsed = uptime-previousUptime
            if wall < 0 || wall > 90 || abs(wall-elapsed) > 5 {interrupt()}
        }
        lastCheck = now; lastUptime = uptime
        if let last = latest?.number("timestamp"), now.timeIntervalSince1970-last > max(45,min(330,latest?.number("max_gap_seconds") ?? 90)) {invalidate()}
        while let window = completed.first, let end = window.1.number("timestamp"), end <= now.timeIntervalSince1970-120 {
            completed.removeFirst()
            let start = window.0, sample = window.1, beginning = start.number("timestamp") ?? end
            let delta = (sample.number("used_percent") ?? 0)-(start.number("used_percent") ?? 0)
            guard delta > 0, abs(end-beginning-Double(window.2)*60) <= Self.boundaryTolerance else {continue}
            let (tokens,dollars,invalid) = usage(calls,start:beginning,end:end,sole:start.flag("sole_codex_pool"))
            if !invalid && dollars > 0 {
                let row: Object = ["start":iso(Date(timeIntervalSince1970:beginning)),"end":iso(Date(timeIntervalSince1970:end)),"account_key":sample.string("account_key"),"plan_type":sample.string("plan_type"),"limit_id":limitID,"reset_at":sample["reset_at"]!,"sole_codex_pool":start.flag("sole_codex_pool"),"start_percent":start["used_percent"]!,"end_percent":sample["used_percent"]!,"delta_percent":delta,"consumed_tokens":tokens,"consumed_usd":dollars,"estimated_total_usd":100*dollars/delta,"price_version":priceVersion,"interval_minutes":window.2,"duration_seconds":end-beginning,"fixed_window_version":1]
                try database.run("INSERT INTO usage_week_intervals(end_at,data) VALUES(?,?)",[row.string("end"),jsonString(row)])
                cachedRows = nil
            }
        }
        let version = try database.query("PRAGMA data_version").first?.integer("data_version")
        if let cachedRows, cachedPrices == priceVersion, databaseVersion == version, cachedAccount == accountKey { return cachedRows }
        let rows = try database.query("SELECT id,data FROM usage_week_intervals"+(accountKey == nil ? "" : " WHERE json_extract(data,'$.account_key')=?")+" ORDER BY end_at DESC,id DESC LIMIT 10",accountKey.map {[$0]} ?? [])
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
        cachedRows = result; cachedPrices = priceVersion; cachedAccount = accountKey; databaseVersion = version; revision += 1
        return result
    }
}

struct EstimateHistoryView: View {
    @ObservedObject var state: AppState
    var rows: [Object]? = nil
    private var values: [Object] {rows ?? state.weeklyEstimates}
    var body: some View {
        LazyVStack(alignment:.leading,spacing:10) {
            if values.isEmpty { StatusNote(text:L("等待同一周期内足够的额度与本机消费记录", "Waiting for enough allowance and local usage records within one period")) }
            ForEach(Array(values.prefix(10)),id:\.estimateIdentity) { row in
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
