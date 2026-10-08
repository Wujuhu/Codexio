import Foundation
import Combine

// Reuses Analytics' priced calls and explicit root/descendant membership.
// The materialized rows follow v0.2.10 usage_queries.py; views read 25 rows.
enum LocalChatRankingStore {
    static func update(_ snapshot: UsageSnapshot, database: Database) throws {
        var roots: [String:String] = [:]
        for row in snapshot.chatCandidates {
            let root = row.string("thread_id"); roots[root] = root
            for child in row["descendant_thread_ids"] as? [String] ?? [] { roots[child] = root }
        }
        var calls: [String:[UsageRow]] = [:]
        for row in snapshot.calls where row.local && (row.date ?? .distantFuture) <= (snapshot.updated ?? Date()) {
            let id = row.raw.string("session_id"); guard !id.isEmpty else { continue }
            calls[roots[id] ?? id,default:[]].append(row)
        }
        let requests = Dictionary(grouping:snapshot.requests.filter {$0.local && !$0.raw.flag("is_subagent") && $0.raw.string("record_kind") == "user_request"},by:{$0.raw.string("session_id")})
        let names=Dictionary(try database.query("SELECT session_id,title FROM usage_session_titles").map {($0.string("session_id"),$0.string("title"))},uniquingKeysWith:{$1})
        try database.transaction {
            let old = Set(try database.query("SELECT thread_id FROM usage_local_chat_rank").map {$0.string("thread_id")})
            for (id,values) in calls {
                let total = UsageSummary(rows:values,requests:requests[id]?.count ?? 0)
                let grouped = Dictionary(grouping:values) { row in
                    [row.raw.string("model"),row.raw.string("reasoning_effort"),normalizedTier(row.raw.string("service_tier"))]
                }
                let groups: [Object] = grouped.map { key,rows in
                    let sum = UsageSummary(rows:rows)
                    return ["model":key[0],"reasoning_effort":key[1],"speed":key[2],"cost_usd":sum.cost as Any? ?? NSNull(),"tokens":sum.tokens as Any? ?? NSNull()]
                }.sorted {jsonString($0) < jsonString($1)}
                let row: Object = ["thread_id":id,"title":names[id] ?? snapshot.chatTitles[id] ?? id,"tokens":total.tokens as Any? ?? NSNull(),"cost_usd":total.cost as Any? ?? NSNull(),"cost_complete":total.unknownCosts == 0,"data_status":total.unknownCosts == 0 ? "complete" : "partial","calls":total.calls,"requests":total.requests,"groups":groups]
                try database.run("INSERT INTO usage_local_chat_rank VALUES(?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET tokens=excluded.tokens,cost=excluded.cost,data=excluded.data WHERE data<>excluded.data",[id,total.tokens as Any? ?? NSNull(),total.cost as Any? ?? NSNull(),jsonString(row)])
            }
            for id in old.subtracting(calls.keys) { try database.run("DELETE FROM usage_local_chat_rank WHERE thread_id=?",[id]) }
            let prior = Set(try database.query("SELECT id FROM usage_journey_requests").map {$0.string("id")})
            var retained=Set<String>()
            for row in snapshot.requests where row.local && !row.raw.flag("is_subagent") && row.raw.string("record_kind")=="user_request" {
                retained.insert(row.id)
                try database.run("INSERT INTO usage_journey_requests VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,timestamp=excluded.timestamp,data=excluded.data WHERE data<>excluded.data",[row.id,row.raw.string("session_id"),row.raw.string("timestamp"),jsonString(row.raw)])
            }
            for id in prior.subtracting(retained) {try database.run("DELETE FROM usage_journey_requests WHERE id=?",[id])}
        }
    }
    static func page(_ database: Database, metric: String, page: Int) throws -> Object {
        let count = try database.query("SELECT count(*) AS n FROM usage_local_chat_rank").first?.integer("n") ?? 0
        let pages = max(1,(count+24)/25), index = min(max(0,page),pages-1), column = metric == "tokens" ? "tokens" : "cost"
        let rows = try database.query("SELECT json_remove(data,'$.groups') AS data FROM usage_local_chat_rank ORDER BY \(column) IS NULL,\(column) DESC,thread_id LIMIT 25 OFFSET ?",[index*25]).map {jsonObject(Data($0.string("data").utf8))}
        return ["threads":rows,"page":index,"pages":pages,"total":count]
    }
}

final class LocalChatRankingModel: ObservableObject {
    @Published private(set) var rows: [Object] = []
    @Published private(set) var groups: [String:[Object]] = [:]
    @Published private(set) var pages = 1
    @Published private(set) var total = 0
    @Published private(set) var error: String?
    private var key = ""
    private var generation = 0
    private var loadingDetails = Set<String>()
    func load(_ state: AppState,metric: String,page: Int) {
        let next = "\(state.usage.revision)|\(metric)|\(page)"; guard next != key else { return }
        key = next; generation += 1; let ticket = generation, snapshot = state.usage
        groups = [:]; loadingDetails = []
        state.dataQueue.async { [weak self] in
            do {
                if state.paths.mock { try LocalChatRankingStore.update(snapshot,database:state.database) }
                let result = try LocalChatRankingStore.page(state.database,metric:metric,page:page)
                DispatchQueue.main.async {
                    guard let self, self.generation == ticket else { return }
                    self.rows = result.objects("threads"); self.pages = result.integer("pages") ?? 1; self.total = result.integer("total") ?? 0; self.error = nil
                }
            } catch { let message = error.localizedDescription; DispatchQueue.main.async { if self?.generation == ticket { self?.error = message } } }
        }
    }
    func details(_ state: AppState,id: String) {
        guard groups[id] == nil, loadingDetails.insert(id).inserted else { return }
        let ticket = generation
        state.dataQueue.async { [weak self] in
            let row = try? state.database.query("SELECT data FROM usage_local_chat_rank WHERE thread_id=?",[id]).first
            let result = jsonObject(Data((row?.string("data") ?? "").utf8)).objects("groups")
            DispatchQueue.main.async {
                guard let self, self.generation == ticket else { return }
                self.loadingDetails.remove(id); self.groups[id] = result
            }
        }
    }
}
