import Foundation

struct UsageRow: Identifiable {
    let raw: Object
    let id: String
    let date: Date?
    let runningSince: Date?
    let tokens: Int?
    let cost: Double?
    let local: Bool
    let confirmedCall: Bool
    let inputTokens: Int?
    let cachedTokens: Int?
    let outputTokens: Int?
    init(raw: Object) {
        var raw = raw
        for key in ["prompt_preview","session_title"] {
            let text = raw.string(key)
            if text.contains("Files mentioned by the user:") || text.contains("## My request") || text.contains("<image") {
                raw[key] = requestPreview(text)
            }
        }
        self.raw = raw; id = raw.string("id"); date = parsedDate(raw["timestamp"]); runningSince = parsedDate(raw["duration_started_at"])
        tokens = Self.tokenCount(raw)
        if ["","priced","estimated"].contains(raw.string("pricing_status")), let value = raw.number("cost_usd"), value >= 0 { cost = value } else { cost = nil }
        local = raw.flag("local_origin") || raw.string("source_id").hasPrefix("local")
        let quality = String(raw.string("quality").split(separator:":").first ?? "")
        confirmedCall = ["","response","legacy_last"].contains(quality) || (quality == "cumulative_delta" && id.hasPrefix("response:"))
        inputTokens = raw.integer("input_tokens"); cachedTokens = raw.integer("cached_input_tokens"); outputTokens = raw.integer("output_tokens")
    }
    var title: String {
        if raw.string("record_kind") == "user_request", !raw.string("prompt_preview").isEmpty { return raw.string("prompt_preview") }
        return raw.string("session_title").isEmpty ? raw.string("prompt_preview",raw.string("session_id")) : raw.string("session_title")
    }
    var modelLabel: String {
        let requested = raw.string("model"), observed = raw.string("upstream_model")
        return modelName(requested)+(observed.isEmpty ? "" : " → "+modelName(observed))
    }
    private static func tokenCount(_ raw: Object) -> Int? {
        guard let total = raw.integer("total_tokens"), !raw.string("quality").hasPrefix("invalid") else { return nil }
        if raw["input_tokens"] != nil || raw["output_tokens"] != nil {
            guard let input = raw.integer("input_tokens"), let output = raw.integer("output_tokens"), total == input+output else { return nil }
        }
        return total
    }
    var duration: Double? {
        if raw.flag("duration_running"), let start = runningSince { return max(0,Date().timeIntervalSince(start)) + (raw.number("duration_base_ms") ?? 0)/1000 }
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
            if let i = row.inputTokens, let c = row.cachedTokens, c <= i { input += i; cached += c }
            output += row.outputTokens ?? 0
        }
    }
}

struct DayUsage: Identifiable, Equatable {
    var date: Date
    var tokens: Int?
    var cost: Double?
    var calls: Int
    var requests: Int = 0
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
    var revision = UUID()
    var calls: [UsageRow] = []
    var requests: [UsageRow] = []
    var summaries: [String:UsageSummary] = [:]
    var activity = ActivityStats()
    var allDays: [DayUsage] = []
    var chatCandidates: [Object] = []
    var chatTitles: [String:String] = [:]
    var updated: Date?
    var callsByID: [String:UsageRow] = [:]
    var models: [String] = []
    var localChatRows: [Object] = []
    var hasRunningTask = false
    func members(of row: UsageRow) -> [UsageRow] { (row.raw["member_ids"] as? [String] ?? []).compactMap {callsByID[$0]} }
    var widgetRequest: UsageRow?
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
    static func build(records: [Object], turns inputTurns: [Object], links: [Object] = [], catalog: PricingCatalog, now: Date = Date(), preparedCalls: [UsageRow]? = nil) -> UsageSnapshot {
        var result = UsageSnapshot()
        var seenCalls = Set<String>()
        result.calls = (preparedCalls ?? records.map { UsageRow(raw:catalog.price($0)) }).filter { seenCalls.insert($0.id).inserted }.sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
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
        var turnMap = Dictionary(turns.map { ($0.string("id"),$0) },uniquingKeysWith: { old,new in
            old.merging(new.filter { !($0.value is NSNull) && !($0.value is String && ($0.value as? String) == "") },uniquingKeysWith: { _,new in new })
        })
        func reference(_ value: String,session: String) -> String { value.hasPrefix("turn:") ? value : "turn:"+session+":"+value }
        func callTurn(_ row: UsageRow) -> String {
            let requested = row.raw.string("request_turn_id")
            return requested.isEmpty ? row.raw.string("turn_id") : requested
        }
        for call in result.calls {
            let session = call.raw.string("session_id"), turn = callTurn(call)
            guard !session.isEmpty, !turn.isEmpty else { continue }
            let id = reference(turn,session:session)
            if turnMap[id] == nil { turnMap[id] = ["id":id,"session_id":session,"turn_id":turn,"started_inferred":true,"verified":true] }
        }
        var rootCache: [String:String] = [:]
        func root(_ id: String) -> String {
            if let known = rootCache[id] { return known }
            var cursor = id, seen = Set<String>()
            for _ in 0..<64 {
                guard seen.insert(cursor).inserted, let row = turnMap[cursor] else { break }
                var parent = ""
                if !row.string("alias_of").isEmpty { parent = reference(row.string("alias_of"),session:row.string("session_id")) }
                else if !row.string("continuation_of").isEmpty { parent = reference(row.string("continuation_of"),session:row.string("session_id")) }
                else if !row.string("root_turn_id").isEmpty && row.string("root_turn_id") != row.string("turn_id") { parent = reference(row.string("root_turn_id"),session:row.string("session_id")) }
                else if row.flag("is_subagent"), !row.string("parent_session_id").isEmpty, !row.string("parent_turn_id").isEmpty { parent = reference(row.string("parent_turn_id"),session:row.string("parent_session_id")) }
                if parent.isEmpty || turnMap[parent] == nil { break }
                cursor = parent
            }
            for key in seen { rootCache[key] = cursor }
            return cursor
        }
        var grouped: [String:[UsageRow]] = [:], groupTurns: [String:[Object]] = [:]
        for row in turns where row.flag("verified",true) && row.string("alias_of").isEmpty { groupTurns[root(row.string("id")),default:[]].append(row) }
        for row in result.calls {
            let turn = callTurn(row), session = row.raw.string("session_id")
            guard !session.isEmpty, !turn.isEmpty else {
                var raw = row.raw; raw["record_kind"] = "unassigned"; result.requests.append(UsageRow(raw:raw)); continue
            }
            grouped[root(reference(turn,session:session)),default:[]].append(row)
        }
        for id in Set(grouped.keys).union(groupTurns.keys) {
            let calls = grouped[id] ?? [], members = groupTurns[id] ?? []
            guard let own = turnMap[id] else { continue }
            guard !calls.isEmpty || !own.string("prompt_preview").isEmpty || members.contains(where: { !$0.string("prompt_preview").isEmpty }) else { continue }
            var row = own
            let prompt = ([own]+members).map { requestPreview($0.string("prompt_preview")) }.first { !$0.isEmpty }
                ?? calls.map { $0.raw.string("prompt_preview") }.first { !$0.isEmpty }
            row["prompt_preview"] = prompt ?? L("任务记录", "Task record")
            let summary = UsageSummary(rows:calls)
            row["id"] = id; row["record_kind"] = "user_request"
            row["timestamp"] = own["started_at"] ?? calls.last?.raw["timestamp"]
            row["total_tokens"] = summary.tokens; row["cost_usd"] = summary.cost; row["pricing_status"] = summary.unknownCosts > 0 ? "estimated" : "priced"
            row["call_count"] = summary.calls; row["cache_hit_rate"] = summary.cacheRate
            let ownSources = (own["source_ids"] as? [String] ?? [])+[own.string("source_id")]
            row["local_origin"] = calls.contains(where:{$0.local}) || ownSources.contains {$0 == "local" || $0.hasPrefix("local:")}
            row["is_subagent"] = own.flag("is_subagent")
            row["member_ids"] = calls.map(\.id)
            for key in ["input_tokens","cached_input_tokens","cache_write_input_tokens","output_tokens","reasoning_output_tokens"] { row[key] = calls.reduce(0) {$0+($1.raw.integer(key) ?? 0)} }
            let models = Array(Set(calls.map {$0.raw.string("model")}.filter {!$0.isEmpty})).sorted()
            row["model"] = models.count == 1 ? models[0] : models.isEmpty ? own.string("model") : models.joined(separator:" + ")
            for key in ["reasoning_effort","service_tier"] {
                let values = Set(calls.map {$0.raw.string(key)}.filter {!$0.isEmpty})
                row[key] = values.count == 1 ? values.first : values.isEmpty ? own[key] : nil
            }
            row["model_context_window"] = calls.compactMap {$0.raw.integer("model_context_window")}.max() ?? own.integer("model_context_window")
            let upstreams = Set(calls.map {$0.raw.string("upstream_model")}.filter {!$0.isEmpty})
            if !upstreams.isEmpty { row["upstream_model"] = upstreams.sorted().joined(separator:" + ") }
            row["upstream_mismatched"] = calls.contains { !$0.raw.string("upstream_model").isEmpty && !$0.raw.string("model").isEmpty && $0.raw.string("upstream_model") != $0.raw.string("model") }
            row["session_title"] = calls.first?.raw["session_title"]
            row["output_preview"] = members.sorted {$0.string("observed_at") > $1.string("observed_at")}.first(where:{!$0.string("output_preview").isEmpty})? ["output_preview"]
            let intervals = members.compactMap(interval)
            let completed = !members.isEmpty && members.allSatisfy {["completed","aborted"].contains($0.string("status"))}
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
        let mainRequests = result.requests.filter {$0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") && $0.local}
        result.widgetRequest = mainRequests.first(where:{$0.raw.string("status") == "running"}) ?? mainRequests.first
        let calendar = Calendar.current, startToday = calendar.startOfDay(for:now)
        let periods: [(String,Date)] = [("today",startToday),("week",calendar.date(byAdding:.day,value:-6,to:startToday)!),("month",calendar.date(byAdding:.day,value:-29,to:startToday)!),("all",.distantPast)]
        let local = result.calls.filter {$0.local && ($0.date ?? .distantFuture) <= now}
        for (key,start) in periods {
            result.summaries[key] = UsageSummary(rows:local.filter {($0.date ?? .distantPast) >= start},requests:result.requests.filter {$0.local && $0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") && ($0.date ?? .distantPast) >= start && ($0.date ?? .distantFuture) <= now}.count)
        }
        var daily: [Date:[UsageRow]] = [:]
        for row in local { if let date = row.date { daily[calendar.startOfDay(for:date),default:[]].append(row) } }
        let dailyRequests = Dictionary(grouping:mainRequests.filter {($0.date ?? .distantFuture) <= now},by:{calendar.startOfDay(for:$0.date!)}).mapValues(\.count)
        result.allDays = Set(daily.keys).union(dailyRequests.keys).map { day in
            let rows = daily[day] ?? [], summary = UsageSummary(rows:rows)
            return DayUsage(date:day,tokens:rows.isEmpty ? 0 : summary.tokens,cost:rows.isEmpty ? 0 : summary.cost,calls:summary.calls,requests:dailyRequests[day] ?? 0)
        }.sorted {$0.date < $1.date}
        result.activity.total = result.summaries["all"]?.tokens
        result.activity.peak = result.allDays.compactMap(\.tokens).max()
        let localPairs = Set(local.map {$0.raw.string("session_id")+":"+$0.raw.string("turn_id")})
        result.hasRunningTask = turns.contains { turn in
            guard turn.string("status") == "running", turn.flag("verified",true),
                  let stamp = parsedDate(turn["observed_at"]), stamp <= now, now.timeIntervalSince(stamp) < 900 else { return false }
            let sources = (turn["source_ids"] as? [String] ?? [])+[turn.string("source_id")]
            return sources.contains {$0 == "local" || $0.hasPrefix("local:")} || localPairs.contains(turn.string("session_id")+":"+turn.string("turn_id"))
        }
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
        result.callsByID = Dictionary(result.calls.map {($0.id,$0)},uniquingKeysWith:{$1})
        result.models = Set((result.calls+result.requests).map {$0.raw.string("model")}.filter {!$0.isEmpty}).sorted()
        var localTotals: [String:Int] = [:]
        for row in local { localTotals[row.raw.string("session_id"),default:0] += row.tokens ?? 0 }
        result.localChatRows = result.chatCandidates.map { candidate in
            let ids = [candidate.string("thread_id")] + (candidate["descendant_thread_ids"] as? [String] ?? [])
            return ["thread_id":candidate.string("thread_id"),"local_tokens":ids.reduce(0) {$0+(localTotals[$1] ?? 0)}] as Object
        }.sorted {($0.integer("local_tokens") ?? 0) > ($1.integer("local_tokens") ?? 0)}
        result.updated = now
        return result
    }
}

struct UsageRange {
    let start: Date
    let end: Date
    init(period: String,from: Date = Date(),through: Date = Date()) {
        let calendar = Calendar.current, today = calendar.startOfDay(for:Date())
        switch period {
        case "all": start = .distantPast
        case "custom": start = calendar.startOfDay(for:min(from,through))
        default: start = calendar.date(byAdding:.day,value:period == "today" ? 0 : period == "week" ? -6 : -29,to:today)!
        }
        end = period == "custom" ? min(Date(),calendar.date(byAdding:.day,value:1,to:calendar.startOfDay(for:max(from,through)))!) : Date()
    }
    func contains(_ row: UsageRow) -> Bool { row.date.map {$0 >= start && $0 <= end} ?? false }
    func buckets(_ rows: [UsageRow],requests: [UsageRow] = [],granularity: String) -> [DayUsage] {
        var calendar = Calendar.current; calendar.firstWeekday = 2
        let component: Calendar.Component = granularity == "hour" ? .hour : granularity == "week" ? .weekOfYear : .day
        let begin = start == .distantPast ? (rows+requests).compactMap(\.date).min() ?? calendar.startOfDay(for:end) : start
        var groups: [Date:[UsageRow]] = [:]
        for row in rows { if let date = row.date, let lower = calendar.dateInterval(of:component,for:date)?.start { groups[lower,default:[]].append(row) } }
        let requestCounts = Dictionary(grouping:requests,by:{calendar.dateInterval(of:component,for:$0.date!)!.start}).mapValues(\.count)
        var cursor = calendar.dateInterval(of:component,for:begin)!.start, result: [DayUsage] = []
        while cursor <= end {
            let entries = groups[cursor] ?? [], total = UsageSummary(rows:entries)
            result.append(DayUsage(date:cursor,tokens:entries.isEmpty ? 0 : total.tokens,cost:entries.isEmpty ? 0 : total.cost,calls:total.calls,requests:requestCounts[cursor] ?? 0))
            guard let next = calendar.date(byAdding:component,value:1,to:cursor), next > cursor else { break }; cursor = next
        }
        return result
    }
}
