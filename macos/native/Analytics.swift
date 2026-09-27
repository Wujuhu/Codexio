import Foundation

struct UsageRow: Identifiable {
    var raw: Object
    var id: String { raw.string("id") }
    var date: Date? { parsedDate(raw["timestamp"]) }
    var title: String { raw.string("session_title").isEmpty ? raw.string("prompt_preview",raw.string("session_id")) : raw.string("session_title") }
    var modelLabel: String {
        let requested = raw.string("model"), observed = raw.string("upstream_model")
        return modelName(requested)+(observed.isEmpty || observed == requested ? "" : " → "+modelName(observed))
    }
    var tokens: Int? {
        guard let total = raw.integer("total_tokens"), !raw.string("quality").hasPrefix("invalid") else { return nil }
        if raw["input_tokens"] != nil || raw["output_tokens"] != nil {
            guard let input = raw.integer("input_tokens"), let output = raw.integer("output_tokens"), total == input+output else { return nil }
        }
        return total
    }
    var confirmedCall: Bool {
        let quality = String(raw.string("quality").split(separator:":").first ?? "")
        return ["","response","legacy_last"].contains(quality) || (quality == "cumulative_delta" && id.hasPrefix("response:"))
    }
    var cost: Double? { guard ["","priced","estimated"].contains(raw.string("pricing_status")), let n = raw.number("cost_usd"), n >= 0 else { return nil }; return n }
    var local: Bool { raw.flag("local_origin") || raw.string("source_id").hasPrefix("local") }
    var duration: Double? {
        if raw.flag("duration_running"), let start = parsedDate(raw["duration_started_at"]) { return max(0,Date().timeIntervalSince(start)) + (raw.number("duration_base_ms") ?? 0)/1000 }
        return raw.number("duration_ms").map {$0/1000}
    }
}

struct UsageSummary {
    var tokens: Int?
    var cost: Double?
    var calls = 0
    var requests = 0
    var input = 0
    var cached = 0
    var output = 0
    var unknownCosts = 0
    var cacheRate: Double? { input > 0 ? Double(cached)/Double(input) : nil }
    var json: Object { ["tokens":tokens as Any? ?? NSNull(),"usd":cost as Any? ?? NSNull(),"user_requests":requests,"cache_hit_rate":cacheRate as Any? ?? NSNull()] }
    init(rows: [UsageRow] = [], requests: Int = 0) {
        self.requests = requests
        for row in rows {
            if let n = row.tokens { tokens = (tokens ?? 0)+n }
            if let n = row.cost { cost = (cost ?? 0)+n } else { unknownCosts += 1 }
            if row.confirmedCall { calls += 1 }
            if let i = row.raw.integer("input_tokens"), let c = row.raw.integer("cached_input_tokens"), c <= i { input += i; cached += c }
            output += row.raw.integer("output_tokens") ?? 0
        }
    }
}

struct DayUsage: Identifiable {
    var date: Date
    var tokens: Int?
    var cost: Double?
    var calls: Int
    var id: Date { date }
}

struct ActivityStats {
    var total: Int?
    var peak: Int?
    var longestChat: Double?
    var durationPartial = false
    var currentStreak = 0
    var longestStreak = 0
    var fastPercent: Double?
    var effort = ""
    var effortPercent: Double?
    var unknownSpeed = 0
    var unknownEffort = 0
    var calls = 0
    var days: [DayUsage] = []
}

struct UsageSnapshot {
    var calls: [UsageRow] = []
    var requests: [UsageRow] = []
    var summaries: [String:UsageSummary] = [:]
    var activity = ActivityStats()
    var allDays: [DayUsage] = []
    var chatCandidates: [Object] = []
    var chatTitles: [String:String] = [:]
    var updated: Date?
    var widgetRequest: UsageRow? {
        let main = requests.filter {$0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") && $0.local}
        return main.first(where:{$0.raw.string("status") == "running"}) ?? main.first
    }
}

enum Analytics {
    static func mergedSeconds(_ intervals: [(Date,Date)]) -> Double? {
        let sorted = intervals.filter {$0.1 >= $0.0}.sorted {$0.0 < $1.0}
        guard var current = sorted.first else { return nil }
        var total = 0.0
        for next in sorted.dropFirst() {
            if next.0 <= current.1 { current.1 = max(current.1,next.1) }
            else { total += current.1.timeIntervalSince(current.0); current = next }
        }
        return total + current.1.timeIntervalSince(current.0)
    }
    private static func interval(_ turn: Object) -> (Date,Date)? {
        guard ["completed","aborted"].contains(turn.string("status")), let end = parsedDate(turn["ended_at"]) else { return nil }
        if let start = parsedDate(turn["started_at"]), !turn.flag("started_inferred",true), end >= start { return (start,end) }
        if let ms = turn.number("duration_ms"), ms >= 0 { return (end.addingTimeInterval(-ms/1000),end) }
        return nil
    }
    static func build(records: [Object], turns inputTurns: [Object], links: [Object] = [], catalog: PricingCatalog, now: Date = Date()) -> UsageSnapshot {
        var result = UsageSnapshot()
        result.calls = records.map { UsageRow(raw:catalog.price($0)) }.sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
        let bySession = Dictionary(grouping:inputTurns,by:{$0.string("session_id")}).mapValues {$0.sorted {$0.string("started_at") < $1.string("started_at")}}
        let turns = inputTurns.map { original -> Object in
            var row = original
            if row.flag("is_subagent") {
                let start = row.string("started_at"), session = row.string("session_id"), turn = row.string("turn_id")
                let applicable = links.filter { link in
                    let matches = link.string("child_session_id") == session || (!row.string("agent_path").isEmpty && link.string("target") == row.string("agent_path"))
                    guard matches, link.string("timestamp") <= start else { return false }
                    if !link.string("child_turn_id").isEmpty { return link.string("child_turn_id") == turn }
                    return bySession[session]?.first(where:{$0.string("started_at") >= link.string("timestamp")})?.string("turn_id") == turn
                }.sorted {$0.string("timestamp") > $1.string("timestamp")}
                if let link = applicable.first {
                    row["parent_session_id"] = link["parent_session_id"]; row["parent_turn_id"] = link["parent_turn_id"]
                } else if bySession[session]?.first?.string("turn_id") != turn { row["parent_turn_id"] = nil
                }
            }
            return row
        }
        let turnMap = Dictionary(turns.map { ($0.string("id"),$0) },uniquingKeysWith: { _,new in new })
        var rootCache: [String:String] = [:]
        func root(_ id: String) -> String {
            if let known = rootCache[id] { return known }
            var cursor = id, seen = Set<String>()
            for _ in 0..<64 {
                guard seen.insert(cursor).inserted, let row = turnMap[cursor] else { break }
                var parent = ""
                if !row.string("continuation_of").isEmpty { parent = "turn:"+row.string("session_id")+":"+row.string("continuation_of") }
                else if row.flag("is_subagent"), !row.string("parent_session_id").isEmpty, !row.string("parent_turn_id").isEmpty { parent = "turn:"+row.string("parent_session_id")+":"+row.string("parent_turn_id") }
                if parent.isEmpty || turnMap[parent] == nil { break }
                cursor = parent
            }
            for key in seen { rootCache[key] = cursor }
            return cursor
        }
        var grouped: [String:[UsageRow]] = [:], groupTurns: [String:[Object]] = [:]
        for row in turns where row.flag("verified",true) { groupTurns[root(row.string("id")),default:[]].append(row) }
        for row in result.calls {
            let turn = row.raw.string("request_turn_id",row.raw.string("turn_id"))
            guard !turn.isEmpty else { continue }
            grouped[root("turn:"+row.raw.string("session_id")+":"+turn),default:[]].append(row)
        }
        for id in Set(grouped.keys).union(groupTurns.keys) {
            let calls = grouped[id] ?? [], members = groupTurns[id] ?? []
            guard let own = turnMap[id], !own.string("prompt_preview").isEmpty else {
                for call in calls { var row = call.raw; row["record_kind"] = "unassigned"; result.requests.append(UsageRow(raw:row)) }
                continue
            }
            var row = own
            let summary = UsageSummary(rows:calls)
            row["id"] = id; row["record_kind"] = "user_request"
            row["timestamp"] = own["started_at"] ?? calls.last?.raw["timestamp"]
            row["total_tokens"] = summary.tokens; row["cost_usd"] = summary.cost; row["pricing_status"] = summary.unknownCosts > 0 ? "estimated" : "priced"
            row["call_count"] = summary.calls; row["cache_hit_rate"] = summary.cacheRate
            row["local_origin"] = calls.contains(where:{$0.local})
            row["is_subagent"] = own.flag("is_subagent")
            row["member_ids"] = calls.map(\.id)
            for key in ["input_tokens","cached_input_tokens","cache_write_input_tokens","output_tokens","reasoning_output_tokens"] { row[key] = calls.reduce(0) {$0+($1.raw.integer(key) ?? 0)} }
            let models = Array(Set(calls.map {$0.raw.string("model")}.filter {!$0.isEmpty})).sorted()
            row["model"] = models.count == 1 ? models[0] : models.isEmpty ? "" : models.joined(separator:" + ")
            for key in ["reasoning_effort","service_tier"] {
                let values = Set(calls.map {$0.raw.string(key)}.filter {!$0.isEmpty})
                row[key] = values.count == 1 ? values.first : nil
            }
            row["model_context_window"] = calls.compactMap {$0.raw.integer("model_context_window")}.max()
            let upstreams = Set(calls.map {$0.raw.string("upstream_model")}.filter {!$0.isEmpty})
            if upstreams.count == 1 { row["upstream_model"] = upstreams.first }
            row["session_title"] = calls.first?.raw["session_title"]
            row["output_preview"] = members.sorted {$0.string("observed_at") > $1.string("observed_at")}.first(where:{!$0.string("output_preview").isEmpty})? ["output_preview"]
            let intervals = members.compactMap(interval)
            let completed = members.allSatisfy {["completed","aborted"].contains($0.string("status"))}
            let running = members.filter {$0.string("status") == "running" && (parsedDate($0["observed_at"]).map {now.timeIntervalSince($0) < 900} ?? false)}
            if !running.isEmpty, let start = running.compactMap({parsedDate($0["started_at"])}).min() {
                row["status"] = "running"; row["duration_running"] = true; row["duration_started_at"] = iso(start)
                row["duration_base_ms"] = mergedSeconds(intervals.filter {$0.1 <= start}).map {$0*1000}
            } else if completed && intervals.count == members.count {
                row["duration_running"] = false; row["duration_ms"] = mergedSeconds(intervals).map {$0*1000}
                row["status"] = own.string("status")
            } else { row["duration_running"] = false; row["duration_ms"] = nil; row["status"] = "unknown" }
            result.requests.append(UsageRow(raw:row))
        }
        result.requests.sort { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
        let calendar = Calendar.current, startToday = calendar.startOfDay(for:now)
        let periods: [(String,Date)] = [("today",startToday),("week",calendar.date(byAdding:.day,value:-6,to:startToday)!),("month",calendar.date(byAdding:.day,value:-29,to:startToday)!),("all",.distantPast)]
        let local = result.calls.filter {$0.local && ($0.date ?? .distantFuture) <= now}
        for (key,start) in periods {
            result.summaries[key] = UsageSummary(rows:local.filter {($0.date ?? .distantPast) >= start},requests:result.requests.filter {$0.local && $0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") && ($0.date ?? .distantPast) >= start && ($0.date ?? .distantFuture) <= now}.count)
        }
        var daily: [Date:[UsageRow]] = [:]
        for row in local { if let date = row.date { daily[calendar.startOfDay(for:date),default:[]].append(row) } }
        result.allDays = daily.map { day,rows in let summary = UsageSummary(rows:rows); return DayUsage(date:day,tokens:summary.tokens,cost:summary.cost,calls:summary.calls) }.sorted {$0.date < $1.date}
        result.activity.total = result.summaries["all"]?.tokens
        result.activity.peak = result.allDays.compactMap(\.tokens).max()
        let localPairs = Set(local.map {$0.raw.string("session_id")+":"+$0.raw.string("turn_id")})
        let threadParents = Dictionary(turns.filter {$0.flag("is_subagent") && !$0.string("parent_session_id").isEmpty}.map {($0.string("session_id"),$0.string("parent_session_id"))},uniquingKeysWith:{$1})
        var localIntervals: [String:[(Date,Date)]] = [:]
        for turn in turns where localPairs.contains(turn.string("session_id")+":"+turn.string("turn_id")) {
            var thread = turn.string("session_id"), seen = Set<String>()
            while seen.insert(thread).inserted, let parent = threadParents[thread] { thread = parent }
            if let range = interval(turn), range.1 <= now { localIntervals[thread,default:[]].append(range) }
            else { result.activity.durationPartial = true }
        }
        result.activity.longestChat = localIntervals.values.compactMap(mergedSeconds).max()
        let activeDays = Set(result.allDays.filter {$0.calls > 0}.map(\.date))
        var run = 0, last: Date?
        for day in activeDays.sorted() {
            run = last.flatMap {calendar.date(byAdding:.day,value:1,to:$0)} == day ? run+1 : 1
            result.activity.longestStreak = max(result.activity.longestStreak,run); last = day
        }
        var cursor = activeDays.contains(startToday) ? startToday : calendar.date(byAdding:.day,value:-1,to:startToday)!
        while activeDays.contains(cursor) { result.activity.currentStreak += 1; cursor = calendar.date(byAdding:.day,value:-1,to:cursor)! }
        let knownCalls = local.filter(\.confirmedCall)
        result.activity.calls = knownCalls.count
        if !knownCalls.isEmpty {
            result.activity.fastPercent = Double(knownCalls.filter {normalizedTier($0.raw.string("service_tier")) == "priority"}.count)/Double(knownCalls.count)*100
            result.activity.unknownSpeed = knownCalls.filter {normalizedTier($0.raw.string("service_tier")) == "unknown"}.count
            var efforts: [String:Int] = [:]
            for row in knownCalls { efforts[row.raw.string("reasoning_effort"),default:0] += 1 }
            result.activity.unknownEffort = efforts.removeValue(forKey:"") ?? 0
            if let most = efforts.max(by:{$0.value < $1.value}) { result.activity.effort = most.key; result.activity.effortPercent = Double(most.value)/Double(knownCalls.count)*100 }
        }
        let byDay = Dictionary(result.allDays.map {($0.date,$0)},uniquingKeysWith:{$1})
        result.activity.days = (-364...0).map { offset in let date = calendar.date(byAdding:.day,value:offset,to:startToday)!; return byDay[date] ?? DayUsage(date:date,tokens:0,cost:0,calls:0) }
        var parents: [String:String] = [:], created: [String:Date] = [:], seen = Set<String>()
        let localThreads = Set(local.map {$0.raw.string("session_id")})
        for turn in turns where localThreads.contains(turn.string("session_id")) {
            let id = turn.string("session_id"); guard !id.isEmpty else { continue }; seen.insert(id)
            if turn.flag("is_subagent"), !turn.string("parent_session_id").isEmpty { parents[id] = turn.string("parent_session_id") }
            if let start = parsedDate(turn["started_at"]) { created[id] = min(created[id] ?? start,start) }
        }
        for row in local {
            let id = row.raw.string("session_id"); guard !id.isEmpty else { continue }; seen.insert(id)
            if !row.title.isEmpty { result.chatTitles[id] = row.title }
        }
        func rootThread(_ id: String) -> String {
            var current = id, visited = Set<String>()
            while visited.insert(current).inserted, let parent = parents[current], seen.contains(parent) { current = parent }
            return current
        }
        var descendants: [String:[String]] = [:]
        for id in seen { let parent = rootThread(id); if parent != id { descendants[parent,default:[]].append(id) } }
        result.chatCandidates = seen.filter {rootThread($0) == $0}.sorted { (created[$0] ?? .distantPast) > (created[$1] ?? .distantPast) }.map { id in
            ["thread_id":id,"created_at":created[id].map(iso) as Any? ?? NSNull(),"descendant_thread_ids":descendants[id] ?? []]
        }
        result.updated = now
        return result
    }
}
