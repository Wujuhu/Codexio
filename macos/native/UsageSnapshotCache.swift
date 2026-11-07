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
        let sourceScope: String
        let language: String

        func allowsDelta(from prior: Input) -> Bool {
            ledger > prior.ledger && upstream == prior.upstream && prices == prior.prices &&
                day == prior.day && zone == prior.zone && offset == prior.offset &&
                sourceScope == prior.sourceScope && language == prior.language
        }
    }
    private struct Refresh {
        let input: Input
        let snapshot: UsageSnapshot
        let turns: [Object]
        let links: [Object]
        let projection: IncrementalUsageProjection?
        let dataVersion: Int?
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
        let cwd: String
        let local: Bool
        let upstreamPresent: Bool
        let row: UsageRow
    }
    private var pricedRecords: [String:PricedRecord] = [:]
    private var turns: [Object] = []
    private var links: [Object] = []
    private var journal: UsageChangeJournal?
    private var journalAttempted = false
    private var incremental: IncrementalUsageProjection?
    private var dataVersion: Int?
    private var consistentRecordScopes = true
    var committedLedgerRevision: Int { input?.ledger ?? 0 }
    private static let overviewKey = "overview-today-v1"
    private static let overviewBytes = 64 * 1024
    private struct OverviewStamp: Codable, Equatable {
        let ledger: Int
        let prices: String
        let roots: String
        let day: Date
        let zone: String
        let offset: Int
        let language: String
        let upstream: [FileStamp]
    }
    private struct OverviewArchive: Codable {
        let stamp: OverviewStamp
        let updated: Date
        let validUntil: Date
        let summary: UsageSummary
        let days: [DayUsage]
        let modelRows: [[MobileModel]]
        let recent: [Data]
    }
    private var preparedOverview: (revision: UUID, roots: String, value: TrendProjection)?
    init(database: Database,catalog: PricingCatalog) { self.database = database; self.catalog = catalog }

    private func overviewStamp(ledger: Int, roots: String, now: Date, upstream: [FileStamp]) -> OverviewStamp {
        let calendar = Calendar.current, zone = calendar.timeZone
        return OverviewStamp(ledger:ledger,prices:catalog.version,roots:roots,day:calendar.startOfDay(for:now),zone:zone.identifier,offset:zone.secondsFromGMT(for:now),language:L("概览", "Overview"),upstream:upstream)
    }

    // The archive lives alongside its committed ledger generation, in SQLite.
    // A miss falls back to the existing background projection.
    func restoreOverview(roots: [URL], now: Date = Date()) -> (value: TrendProjection, updated: Date)? {
        guard let row = try? database.query("SELECT data FROM usage_meta WHERE key=? AND length(CAST(data AS BLOB))<=?",[Self.overviewKey,Self.overviewBytes]).first,
              let archive = try? JSONDecoder().decode(OverviewArchive.self,from:Data(row.string("data").utf8)),
              now >= archive.updated, now < archive.validUntil,
              archive.modelRows.count == 3, archive.days.count <= 26, archive.recent.count <= 3,
              let ledger = try? database.query("SELECT revision FROM usage_revisions WHERE kind='ledger'").first?.integer("revision"),
              archive.stamp == overviewStamp(ledger:ledger,roots:identity(roots.map(\.path)),now:now,upstream:observationStamps()) else { return nil }
        let recent = archive.recent.map { UsageRow(raw:jsonObject($0)) }
        guard recent.allSatisfy({!$0.id.isEmpty}) else { return nil }
        let stored = Dictionary(archive.days.map { ($0.date,$0) },uniquingKeysWith:{ $1 })
        let days = UsageRange(period:"today",now:now).buckets([],granularity:"hour").map { stored[$0.date] ?? $0 }
        return (TrendProjection(summary:archive.summary,days:days,recent:recent,modelRows:archive.modelRows),archive.updated)
    }

    func prepareOverview(_ snapshot: UsageSnapshot, roots: [URL]) -> TrendProjection {
        let scope = identity(roots.map(\.path))
        if let cached = preparedOverview, cached.revision == snapshot.revision, cached.roots == scope { return cached.value }
        let now = snapshot.updated ?? Date()
        let value = TrendProjection.build(snapshot,range:UsageRange(period:"today",now:now),model:"all",granularity:"hour")
        preparedOverview = (snapshot.revision,scope,value)
        guard let input, input.ledger > 0, let upstreamStamps else { return value }
        let recent = value.recent.compactMap { try? Self.overviewRow($0) }
        guard recent.count == value.recent.count else { return value }
        let stamp = overviewStamp(ledger:input.ledger,roots:scope,now:now,upstream:upstreamStamps)
        let archive = OverviewArchive(stamp:stamp,updated:now,validUntil:expires,summary:value.summary,days:value.days,modelRows:value.modelRows,recent:recent)
        if let data = try? JSONEncoder().encode(archive), data.count <= Self.overviewBytes {
            try? database.run("INSERT INTO usage_meta(key,data) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data WHERE data<>excluded.data",[Self.overviewKey,String(decoding:data,as:UTF8.self)])
        }
        return value
    }

    private static let overviewFields: Set<String> = [
        "id", "timestamp", "session_id", "prompt_preview", "session_title", "model", "model_display_name",
        "record_kind", "is_subagent", "reasoning_effort", "service_tier", "model_context_window", "quality",
        "input_tokens", "output_tokens", "total_tokens", "cost_usd", "pricing_status", "pricing_models",
        "status", "duration_ms", "duration_started_at", "duration_running", "duration_base_ms"
    ]
    private static func overviewRow(_ row: UsageRow) throws -> Data {
        var fields = row.raw.filter { overviewFields.contains($0.key) }
        for key in ["prompt_preview", "session_title"] {
            if let text = fields[key] as? String { fields[key] = String(text.prefix(320)) }
        }
        if let ttft = row.firstTokenSeconds { fields["ttft_ms"] = ttft * 1000 }
        let pricedModel = row.raw.object("price_rates").string("model")
        if !pricedModel.isEmpty { fields["price_rates"] = ["model":pricedModel] }
        return try jsonData(fields)
    }

    func load(now: Date = Date(), roots: [URL] = []) throws -> UsageSnapshot {
        let calendar = Calendar.current
        let sourceScope = identity(roots.map(\.path))
        refreshUpstream()
        let next = try currentInput(now:now,calendar:calendar,sourceScope:sourceScope)
        let nextHour = calendar.dateInterval(of:.hour,for:now)?.start
        var result: UsageSnapshot
        if next == input, now >= checked, now < expires, let cached {
            result = cached
            if hour != nextHour { result.revision = UUID() }
        } else {
            if !journalAttempted {
                journalAttempted = true
                journal = try? database.transaction { try UsageChangeJournal(database:database) }
            }
            let refresh: Refresh
            if let prior = input, next.allowsDelta(from:prior), now >= checked, now < expires,
               let scoped = try? refreshIncrementally(now:now,calendar:calendar,sourceScope:sourceScope) {
                refresh = scoped
            } else {
                refresh = try rebuild(now:now,calendar:calendar,sourceScope:sourceScope)
            }
            result = refresh.snapshot
            turns = refresh.turns; links = refresh.links
            incremental = refresh.projection; dataVersion = refresh.dataVersion
            let statusExpiry = turns.filter {$0.string("status") == "running"}.compactMap {parsedDate($0["observed_at"])?.addingTimeInterval(900)}.filter {$0 > now}.min()
            let futureRecord = result.calls.compactMap(\.date).filter {$0 > now}.min()
            let futureTurn = turns.compactMap {parsedDate($0["observed_at"])}.filter {$0 > now}.min()
            expires = [statusExpiry,futureRecord,futureTurn,calendar.date(byAdding:.day,value:1,to:refresh.input.day)].compactMap {$0}.min() ?? .distantFuture
            input = refresh.input
        }
        result.updated = now; cached = result; checked = now; hour = nextHour
        return result
    }
    private func currentInput(now: Date, calendar: Calendar, sourceScope: String) throws -> Input {
        let revision = try database.query("SELECT revision FROM usage_revisions WHERE kind='ledger'").first?.integer("revision") ?? 0
        return Input(ledger:revision,upstream:upstreamGeneration,prices:catalog.version,day:calendar.startOfDay(for:now),zone:calendar.timeZone.identifier,offset:calendar.timeZone.secondsFromGMT(for:now),sourceScope:sourceScope,language:L("概览", "Overview"))
    }

    private func refreshIncrementally(now: Date, calendar: Calendar, sourceScope: String) throws -> Refresh? {
        guard let journal, let incremental, let old = cached, let prior = input, let dataVersion else { return nil }
        return try database.transaction {
            let next = try currentInput(now:now,calendar:calendar,sourceScope:sourceScope)
            // TEMP triggers only see this connection. An external commit can
            // change ownership or derived rows without a local journal entry.
            guard next.allowsDelta(from:prior),
                  try database.query("PRAGMA data_version").first?.integer("data_version") == dataVersion,
                  let changes = try journal.changes(expected:next.ledger-prior.ledger),
                  let update = try incremental.prepare(changes:changes,old:old,rawTurns:turns,database:database,catalog:catalog,now:now,price:pricePayload) else { return nil }
            try LocalChatRankingStore.write(update.scoped,database:database,sessions:update.sessions)
            try journal.clear()
            return Refresh(input:next,snapshot:update.snapshot,turns:update.rawTurns,links:links,projection:update.projection,dataVersion:dataVersion)
        }
    }

    private func rebuild(now: Date, calendar: Calendar, sourceScope: String) throws -> Refresh {
        try database.transaction {
            let next = try currentInput(now:now,calendar:calendar,sourceScope:sourceScope)
            let version = journal == nil ? nil : (try? database.query("PRAGMA data_version").first?.integer("data_version"))
            let sameLedger = input?.ledger == next.ledger && dataVersion == version
            let rawTurns = sameLedger ? turns : try database.metadata("usage_turns")
            let rawLinks = sameLedger ? links : try database.metadata("usage_agent_links")
            let reuseCalls = sameLedger && input?.prices == next.prices && input?.upstream == next.upstream
            let calls = reuseCalls ? cached?.calls ?? [] : try prepareCalls()
            var normalized: [Object] = []
            let result = Analytics.build(records:[],turns:rawTurns,links:rawLinks,catalog:catalog,now:now,preparedCalls:calls,captureTurns:version == nil ? nil : {normalized = $0})
            try LocalChatRankingStore.write(result,database:database)
            try journal?.clear()
            let projection = version != nil && consistentRecordScopes ? IncrementalUsageProjection(turns:rawTurns,normalizedTurns:normalized,calls:result.calls,links:rawLinks) : nil
            return Refresh(input:next,snapshot:result,turns:rawTurns,links:rawLinks,projection:projection,dataVersion:version)
        }
    }

    private func pricePayload(_ entry: Object) -> UsageRow {
        var raw = jsonObject(Data(entry.string("data").utf8))
        let title = entry.string("title"), cwd = entry.string("cwd")
        if !title.isEmpty { raw["session_title"] = title }
        if !cwd.isEmpty { raw["session_cwd"] = cwd }
        raw["local_origin"] = entry.integer("local_origin") == 1 || raw.string("source_id").hasPrefix("local")
        if upstreamPresent { raw["upstream_model"] = upstreamModels[raw.string("response_id")] }
        return UsageRow(raw:catalog.price(raw))
    }

    private func prepareCalls() throws -> [UsageRow] {
        var calls: [UsageRow] = [], retained: [String:PricedRecord] = [:], retainedBytes = 0
        var scopesMatch = true
        try database.forEachRecordPayload { entry in
            autoreleasepool {
                let id = entry.string("id"), payload = entry.string("data"), title = entry.string("title"), cwd = entry.string("cwd"), local = entry.integer("local_origin") == 1
                let value: UsageRow
                if let old = pricedRecords[id], old.payload == payload, old.title == title, old.cwd == cwd, old.local == local,
                   old.upstreamPresent == upstreamPresent,
                   old.row.raw.string("price_version") == catalog.version,
                   (!upstreamPresent || old.row.raw.string("upstream_model") == (upstreamModels[old.row.raw.string("response_id")] ?? "")) {
                    value = old.row
                } else {
                    value = pricePayload(entry)
                }
                if value.id != id || value.raw.string("session_id") != entry.string("session_id") { scopesMatch = false }
                calls.append(value)
                let size = payload.utf8.count
                if retained.count < 8192 && retainedBytes+size <= 8_388_608 {
                    retained[id] = PricedRecord(payload:payload,title:title,cwd:cwd,local:local,upstreamPresent:upstreamPresent,row:value); retainedBytes += size
                }
            }
        }
        pricedRecords = retained
        consistentRecordScopes = scopesMatch
        return calls
    }
    private func observationStamps() -> [FileStamp] {
        let file = database.url.deletingLastPathComponent().appendingPathComponent("upstream.sqlite")
        return [FileStamp(file),FileStamp(URL(fileURLWithPath:file.path+"-wal"))]
    }
    private func refreshUpstream() {
        let file = database.url.deletingLastPathComponent().appendingPathComponent("upstream.sqlite")
        let stamps = observationStamps()
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
