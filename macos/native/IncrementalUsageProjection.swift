import Foundation

struct UsageDay {
    let summary: UsageSummary
    let hasCalls: Bool

    init(rows: [UsageRow], requests: Int) {
        summary = UsageSummary(rows:rows,requests:requests)
        hasCalls = !rows.isEmpty
    }

    func bucket(on day: Date) -> DayUsage {
        DayUsage(date:day,tokens:hasCalls ? summary.tokens : 0,cost:hasCalls ? summary.cost : 0,calls:summary.calls,requests:summary.requests)
    }

    static func isRequest(_ row: UsageRow) -> Bool {
        row.local && !row.raw.flag("is_subagent") && row.raw.string("record_kind") == "user_request"
    }
}

extension UsageSummary {
    mutating func merge(_ other: UsageSummary) {
        if let value = other.tokens { tokens = (tokens ?? 0) + value }
        if let value = other.cost { cost = (cost ?? 0) + value }
        calls += other.calls
        requests += other.requests
        input += other.input
        cached += other.cached
        output += other.output
        unknownCosts += other.unknownCosts
        incompleteRecords += other.incompleteRecords
    }
}

// A thread is the conservative request scope: its turns can alias or resume
// each other. Keep cross-thread and unresolved ownership in the full path.
struct IncrementalUsageProjection {
    let normalizedTurns: [Object]
    let excludedSessions: Set<String>

    struct Update {
        let snapshot: UsageSnapshot
        let scoped: UsageSnapshot
        let sessions: [String]
        let rawTurns: [Object]
        let projection: IncrementalUsageProjection
    }

    init(turns: [Object], normalizedTurns: [Object], calls: [UsageRow], links: [Object]) {
        self.normalizedTurns = normalizedTurns
        excludedSessions = Self.exclusions(turns:turns,calls:calls,links:links)
    }

    private init(normalizedTurns: [Object], excludedSessions: Set<String>) {
        self.normalizedTurns = normalizedTurns
        self.excludedSessions = excludedSessions
    }

    private static func exclusions(turns: [Object], calls: [UsageRow], links: [Object]) -> Set<String> {
        var blocked = Set<String>()
        for link in links {
            blocked.insert(link.string("parent_session_id"))
            blocked.insert(link.string("child_session_id"))
        }
        func inspect(_ row: Object) {
            let session = row.string("session_id"), parent = row.string("parent_session_id")
            if row.flag("is_subagent") || (!parent.isEmpty && parent != session) {
                blocked.insert(session)
                blocked.insert(parent)
            }
            // The canonical turn reference contains the owning thread. An old
            // reference can become resolvable when its target is inserted.
            for key in ["id","turn_id","request_turn_id","alias_of","continuation_of","root_turn_id","parent_turn_id"] {
                let value = row.string(key)
                guard value.hasPrefix("turn:"), !value.hasPrefix("turn:"+session+":") else { continue }
                blocked.insert(session)
                if let other = value.split(separator:":",maxSplits:2,omittingEmptySubsequences:false).dropFirst().first {
                    blocked.insert(String(other))
                }
            }
        }
        for turn in turns {
            let session = turn.string("session_id")
            if !turn.string("id").hasPrefix("turn:"+session+":") { blocked.insert(session) }
            inspect(turn)
        }
        for row in calls { inspect(row.raw) }
        return blocked
    }

    func prepare(changes: [Object], old: UsageSnapshot, rawTurns: [Object], database: Database, catalog: PricingCatalog, now: Date, price: (Object) -> UsageRow) throws -> Update? {
        var sessions = Set<String>()
        var records = Set<String>()
        for change in changes {
            let value = change.string("value")
            guard !value.isEmpty else { return nil }
            switch change.string("kind") {
            case "session": sessions.insert(value)
            case "record": records.insert(value)
            default: return nil
            }
        }

        // Origin edits and record moves need both the old and the new thread.
        var located = Set<String>()
        for id in records {
            if let row = old.callsByID[id] {
                sessions.insert(row.raw.string("session_id"))
                located.insert(id)
            }
        }
        if !records.isEmpty {
            let ids = records.sorted(), parameters = ids.map { _ in "?" }.joined(separator:",")
            for row in try database.query("SELECT id,session_id FROM usage_records WHERE id IN (\(parameters))",ids) {
                sessions.insert(row.string("session_id"))
                located.insert(row.string("id"))
            }
        }

        guard records == located, !sessions.isEmpty, sessions.count <= 8,
              !sessions.contains(where: { $0.isEmpty || $0.contains(":") }),
              sessions.isDisjoint(with:excludedSessions) else { return nil }

        let ids = sessions.sorted()
        let entries = try database.recordPayloads(sessions:ids,limit:4097)
        let turns = try database.turnMetadata(sessions:ids,limit:1025)
        guard entries.count <= 4096, turns.count <= 1024,
              turns.allSatisfy({sessions.contains($0.string("session_id"))}) else { return nil }
        let calls = entries.map { entry in autoreleasepool { price(entry) } }
        guard zip(entries,calls).allSatisfy({ entry,row in
                  !row.id.isEmpty && row.id == entry.string("id") && row.raw.string("session_id") == entry.string("session_id")
              }),
              sessions.isDisjoint(with:Self.exclusions(turns:turns,calls:calls,links:[])) else { return nil }

        var scopedTurns: [Object] = []
        let scoped = Analytics.build(records:[],turns:turns,catalog:catalog,now:now,preparedCalls:calls,captureTurns:{scopedTurns = $0})
        let inScope: (UsageRow)->Bool = { sessions.contains($0.raw.string("session_id")) }
        let oldCalls = old.calls.filter(inScope), oldRequests = old.requests.filter(inScope)
        let requestIDs = Set(scoped.requests.map(\.id))
        guard Set(oldRequests.map(\.id)).count == oldRequests.count, requestIDs.count == scoped.requests.count,
              !old.requests.contains(where:{requestIDs.contains($0.id) && !inScope($0)}) else { return nil }
        var merged = UsageSnapshot()
        merged.calls = Self.merge(old.calls,scoped.calls,removing:inScope)
        merged.requests = Self.merge(old.requests,scoped.requests,removing:inScope)

        let calendar = Calendar.current
        func localDay(_ row: UsageRow) -> Date? {
            guard row.local, let date = row.date, date <= now else { return nil }
            return calendar.startOfDay(for:date)
        }
        let callDays = Self.changedDays(oldCalls,scoped.calls,day:localDay) { a,b in
            a.tokens == b.tokens && a.cost == b.cost && a.confirmedCall == b.confirmedCall &&
            a.inputTokens == b.inputTokens && a.cachedTokens == b.cachedTokens && a.outputTokens == b.outputTokens
        }
        let requestDays = Self.changedDays(oldRequests,scoped.requests,day:{ UsageDay.isRequest($0) ? localDay($0) : nil }) { _,_ in true }
        var daily = old.dailyUsage
        for day in callDays.union(requestDays) {
            guard let end = calendar.date(byAdding:.day,value:1,to:day) else { return nil }
            let dayCalls = Self.rows(merged.calls,from:day,to:end).filter { localDay($0) == day }
            let requests = Self.rows(merged.requests,from:day,to:end).filter { UsageDay.isRequest($0) && localDay($0) == day }.count
            daily[day] = dayCalls.isEmpty && requests == 0 ? nil : UsageDay(rows:dayCalls,requests:requests)
        }

        let normalized = normalizedTurns.filter {!sessions.contains($0.string("session_id"))} + scopedTurns
        let result = Analytics.finish(merged,turns:normalized,now:now,dailyUsage:daily)
        let next = IncrementalUsageProjection(normalizedTurns:normalized,excludedSessions:excludedSessions)
        return Update(snapshot:result,scoped:scoped,sessions:ids,
                      rawTurns:rawTurns.filter {!sessions.contains($0.string("session_id"))} + turns,projection:next)
    }

    private static func merge(_ old: [UsageRow], _ incoming: [UsageRow], removing: (UsageRow)->Bool) -> [UsageRow] {
        var result: [UsageRow] = []
        result.reserveCapacity(old.count+incoming.count)
        var index = 0
        for row in old where !removing(row) {
            while index < incoming.count, UsageRow.newestFirst(incoming[index],row) {
                result.append(incoming[index]); index += 1
            }
            result.append(row)
        }
        result.append(contentsOf:incoming[index...])
        return result
    }

    private static func changedDays(_ before: [UsageRow], _ after: [UsageRow], day: (UsageRow)->Date?, same: (UsageRow,UsageRow)->Bool) -> Set<Date> {
        var prior = Dictionary(uniqueKeysWithValues:before.map { ($0.id,$0) })
        var changed = Set<Date>()
        for row in after {
            let old = prior.removeValue(forKey:row.id)
            let previous = old.flatMap(day), next = day(row)
            if previous == next, previous == nil || old.map({same($0,row)}) == true { continue }
            if let previous { changed.insert(previous) }
            if let next { changed.insert(next) }
        }
        for row in prior.values {
            if let date = day(row) { changed.insert(date) }
        }
        return changed
    }

    private static func rows(_ values: [UsageRow], from start: Date, to end: Date) -> ArraySlice<UsageRow> {
        func first(before date: Date) -> Int {
            var low = 0, high = values.count
            while low < high {
                let mid = low + (high-low)/2
                if (values[mid].date ?? .distantPast) >= date { low = mid+1 }
                else { high = mid }
            }
            return low
        }
        return values[first(before:end)..<first(before:start)]
    }
}
