import Foundation

enum UsageReportPeriod: String, CaseIterable, Codable, Identifiable, Sendable {
    case day, week, month
    var id: String { rawValue }
    var title: String { switch self { case .day: L("AI日报", "AI Daily"); case .week: L("AI周报", "AI Weekly"); case .month: L("AI月报", "AI Monthly") } }
    var tabTitle: String { switch self { case .day: L("日报", "Daily"); case .week: L("周报", "Weekly"); case .month: L("月报", "Monthly") } }
    var periodWord: String { switch self { case .day: L("昨天", "Yesterday"); case .week: L("上周", "Last week"); case .month: L("上月", "Last month") } }
    var previousWord: String { switch self { case .day: L("前天", "the preceding day"); case .week: L("再前一周", "the preceding week"); case .month: L("再前一月", "the preceding month") } }
    static func preferred(on date: Date = Date()) -> UsageReportPeriod {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = .current
        if calendar.component(.day, from: date) == 1 { return .month }
        return calendar.component(.weekday, from: date) == 2 ? .week : .day
    }
    func interval(now: Date, calendar: Calendar) -> DateInterval {
        let today = calendar.startOfDay(for: now)
        switch self {
        case .day: return DateInterval(start: calendar.date(byAdding: .day, value: -1, to: today)!, end: today)
        case .week:
            let end = calendar.dateInterval(of: .weekOfYear, for: today)!.start
            return DateInterval(start: calendar.date(byAdding: .day, value: -7, to: end)!, end: end)
        case .month:
            let end = calendar.dateInterval(of: .month, for: today)!.start
            return DateInterval(start: calendar.date(byAdding: .month, value: -1, to: end)!, end: end)
        }
    }
    func previous(_ range: DateInterval, calendar: Calendar) -> DateInterval {
        let start = calendar.date(byAdding: self == .month ? .month : .day, value: self == .week ? -7 : -1, to: range.start)!
        return DateInterval(start: start, end: range.start)
    }
}

struct UsageReportModel: Codable, Identifiable, Sendable {
    var name: String
    var calls: Int
    var tokens: Int
    var costUSD: Double?
    var costComplete = true
    var id: String { name }
    var costLabel: String { (costUSD != nil && !costComplete ? "≥" : "") + reportUSDLabel(costUSD) }
}
struct UsageReportProject: Codable, Identifiable, Sendable {
    var id: String
    var name: String
    var requests: Int
}
struct UsageReportTimeSlice: Codable, Identifiable, Sendable {
    var name: String
    var requests: Int
    var id: String { name }
}

struct UsageReportData: Codable, Sendable {
    var schema = 1
    var period: UsageReportPeriod
    var start: Date
    var end: Date
    var timeZone: String
    var dateLabel: String
    var fileDate: String
    var sourceKey: String
    var priceVersion: String
    var requests: Int
    var modelCalls: Int
    var inputTokens: Int
    var outputTokens: Int
    var cachedInputTokens: Int
    var totalTokens: Int
    var tokensComplete: Bool
    var costUSD: Double?
    var costComplete: Bool
    var models: [UsageReportModel]
    var projects: [UsageReportProject]
    var timeSlices: [UsageReportTimeSlice]
    var firstRequest: Date?
    var lastRequest: Date?
    var previousTokens: Int
    var previousRequests: Int
    var previousCostUSD: Double?
    var comparisonComplete = true

    var title: String { period.title }
    var cachePercent: Int { percent(cachedInputTokens, of: inputTokens) }
    var cacheLabel: String { inputTokens > 0 ? "\(cachePercent)%" : "—" }
    var costLabel: String { (costUSD != nil && !costComplete ? "≥" : "") + reportUSDLabel(costUSD) }
    var tokenSummary: String { (tokensComplete ? "" : "≥") + reportTokenLabel(totalTokens) }
    var topModel: UsageReportModel? { models.filter { $0.name != L("其他模型", "Other models") }.max { $0.calls < $1.calls } }
    var topProject: UsageReportProject? { projects.filter { $0.id != "other" }.max { $0.requests < $1.requests } }
    var peakTimeSlice: UsageReportTimeSlice? { timeSlices.filter { $0.requests > 0 }.max { $0.requests < $1.requests } }
    var emptyActivityText: String { period.periodWord + L("没有请求记录", " had no recorded requests") }
    var modelHeadline: String { period.periodWord + L("，\n最常陪你思考的是它", ",\nyour most-used model") }
    var projectHeadline: String {
        guard requests > 0, let top = topProject else { return period.periodWord + L("，\n没有项目请求", ",\nno project requests") }
        if top.id != "unassigned", top.requests * 2 > requests { return period.periodWord + "，\n" + top.name + L("是主角", " took the lead") }
        let known = projects.filter { !["unassigned", "other"].contains($0.id) }
        if known.count == 3, let largest = known.map(\.requests).max(), let smallest = known.map(\.requests).min(), largest-smallest <= max(1, requests/20), projects.count == known.count {
            return period.periodWord + L("，\n三个项目平分秋色", ",\nthree projects in balance")
        }
        return period.periodWord + L("，\n项目使用各有侧重", ",\nactivity across projects")
    }
    var activityHeadline: String { peakTimeSlice.map { period.periodWord + "，" + $0.name + L("最热闹", " was busiest") } ?? emptyActivityText }
    var activityInsight: String { peakTimeSlice.map { period.periodWord + L("的请求在", " was busiest in ") + $0.name + L("最多，共 ", ", with ") + "\($0.requests)" + L(" 次。", " requests.") } ?? emptyActivityText }
    var comparisonText: String {
        guard comparisonComplete else { return "" }
        guard previousTokens > 0 else { return period.previousWord + (totalTokens > 0 ? L("没有用量记录，新的小脚印已记好喵。", " had no usage; your new activity is recorded.") : L("也没有用量记录。", " also had no recorded usage.")) }
        let delta = totalTokens - previousTokens
        let change = String(format: "%.1f%%", abs(Double(delta) / Double(previousTokens)) * 100)
        return L("较", "Compared with ") + period.previousWord + (delta >= 0 ? L("多 ", ": +") : L("少 ", ": −")) + reportTokenLabel(abs(delta)) + " Token · " + change
    }
    func percent(_ amount: Int, of total: Int) -> Int { total > 0 ? Int((100 * Double(amount) / Double(total)).rounded()) : 0 }
    func projectInsight() -> String {
        guard requests > 0, let top = topProject else { return period.periodWord + L("没有项目请求，休息也很重要喵。", " had no project requests. Rest matters too.") }
        if top.id != "unassigned", top.requests * 2 > requests { return period.periodWord + L("过半的请求都在 ", ": more than half of requests were in ") + top.name + L(" 项目。", ".") }
        if top.id == "unassigned" { return L("部分记录未提供工作目录，小猫没有猜测项目归属。", "Some records have no working directory; their project is unknown.") }
        return period.periodWord + L("的请求分布在不同项目，稳稳前进喵。", " had requests across several projects. Steady progress.")
    }
}

func reportTokenLabel(_ count: Int) -> String {
    let value = Double(max(0, count)), chinese = L("日报", "Daily") == "日报"
    let unit: Double, suffix: String
    if chinese { (unit, suffix) = value >= 100_000_000 ? (100_000_000, "亿") : value >= 10_000 ? (10_000, "万") : (1, "") }
    else { (unit, suffix) = value >= 1_000_000_000 ? (1_000_000_000, "B") : value >= 1_000_000 ? (1_000_000, "M") : value >= 1_000 ? (1_000, "K") : (1, "") }
    if unit == 1 { return String(max(0, count)) }
    var number = String(format: "%.2f", value/unit)
    while number.last == "0" { number.removeLast() }; if number.last == "." { number.removeLast() }
    return number + suffix
}
func reportUSDLabel(_ value: Double?) -> String { value.map { String(format: "$%.2f", $0) } ?? "—" }

struct UsageReportCollection {
    let documents: [UsageReportPeriod: UsageReportData]
    let presentationDay: String
    let alreadyPresented: Bool
}

// Owned by AppState.dataQueue. Existing ledger generations, rather than polling
// timestamps, control invalidation; only three small projections stay in memory.
final class UsageReportStore {
    let directory: URL
    private var cachedKey = ""
    private var cached: [UsageReportPeriod: UsageReportData] = [:]
    private var seen: Set<String>?
    private let encoder: JSONEncoder = { let e = JSONEncoder(); e.outputFormatting = [.sortedKeys, .prettyPrinted]; e.dateEncodingStrategy = .millisecondsSince1970; return e }()
    private let dayFormatter: DateFormatter = { let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"; return f }()
    private let labelFormatter = DateFormatter()
    init(directory: URL) { self.directory = directory }

    private struct Aggregate {
        var requests = 0, modelCalls = 0, input = 0, output = 0, cached = 0, total = 0, unknownTokens = 0, unknownCosts = 0, priced = 0
        var cost = 0.0
        var models: [String: UsageReportModel] = [:]
        var projects: [String: Int] = [:]
        var times = [0,0,0,0]
        var first: Date?, last: Date?
        mutating func call(_ row: UsageRow) {
            if let n = row.tokens { total += n } else { unknownTokens += 1 }
            output += row.outputTokens ?? 0
            if let i = row.inputTokens, let c = row.cachedTokens, c <= i { input += i; cached += c }
            if let amount = row.cost { cost += amount; priced += 1 } else { unknownCosts += 1 }
            guard row.confirmedCall else { return }
            modelCalls += 1
            let name = row.raw.string("model", L("未知模型", "Unknown model"))
            var value = models[name] ?? UsageReportModel(name: name, calls: 0, tokens: 0, costUSD: nil)
            value.calls += 1; value.tokens += row.tokens ?? 0
            if let amount = row.cost { value.costUSD = (value.costUSD ?? 0) + amount }
            else { value.costComplete = false }
            models[name] = value
        }
        mutating func request(_ row: UsageRow, date: Date, hour: Int) {
            requests += 1; first = min(first ?? date, date); last = max(last ?? date, date)
            times[min(3, max(0, hour/6))] += 1
            let rawPath = ["cwd", "turn_cwd", "session_cwd"].map { row.raw.string($0) }.first { !$0.isEmpty }
            let path = rawPath.map { URL(fileURLWithPath: $0).standardizedFileURL.path } ?? "unassigned"
            projects[path, default: 0] += 1
        }
        var costValue: Double? { priced > 0 ? cost : nil }
    }

    func load(snapshot: UsageSnapshot, ledger: Int, priceVersion: String, sourceKey: String, now: Date = Date()) throws -> UsageReportCollection {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = .current; calendar.firstWeekday = 2; calendar.minimumDaysInFirstWeek = 4
        dayFormatter.timeZone = calendar.timeZone
        let todayKey = dayFormatter.string(from: now)
        let sourceID = String(digest(Data(sourceKey.utf8)).prefix(16))
        let inputKey = "\(ledger)|\(priceVersion)|\(sourceID)|\(todayKey)|\(calendar.timeZone.identifier)|\(calendar.timeZone.secondsFromGMT(for: now))|\(L("日报", "Daily"))"
        if seen == nil { seen = Set(readObject(directory.appendingPathComponent("presentation.json"))["days"] as? [String] ?? []) }
        if inputKey != cachedKey {
            let periods = UsageReportPeriod.allCases
            let ranges = periods.map { $0.interval(now: now, calendar: calendar) }
            let prior = zip(periods, ranges).map { $0.0.previous($0.1, calendar: calendar) }
            let allRanges = ranges + prior
            let floor = allRanges.map(\.start).min()!, ceiling = calendar.startOfDay(for: now)
            var aggregates = Array(repeating: Aggregate(), count: allRanges.count)
            for row in snapshot.calls where row.local {
                guard let date = row.date, date >= floor, date < ceiling else { continue }
                for index in allRanges.indices where date >= allRanges[index].start && date < allRanges[index].end { aggregates[index].call(row) }
            }
            for row in snapshot.requests where row.local && row.raw.string("record_kind") == "user_request" && !row.raw.flag("is_subagent") {
                guard let date = row.date, date >= floor, date < ceiling else { continue }
                let hour = calendar.component(.hour, from: date)
                for index in allRanges.indices where date >= allRanges[index].start && date < allRanges[index].end { aggregates[index].request(row, date: date, hour: hour) }
            }
            var documents: [UsageReportPeriod: UsageReportData] = [:]
            for index in periods.indices {
                let period = periods[index], range = ranges[index], value = aggregates[index], previous = aggregates[index+periods.count]
                var models = value.models.values.sorted { $0.calls == $1.calls ? $0.name < $1.name : $0.calls > $1.calls }
                if models.count > 3 {
                    let remaining = models.dropFirst(3)
                    let amounts = remaining.compactMap(\.costUSD)
                    models = Array(models.prefix(3)) + [UsageReportModel(name: L("其他模型", "Other models"), calls: remaining.reduce(0) {$0+$1.calls}, tokens: remaining.reduce(0) {$0+$1.tokens}, costUSD: amounts.isEmpty ? nil : amounts.reduce(0,+), costComplete: remaining.allSatisfy(\.costComplete))]
                }
                var projects: [UsageReportProject] = []
                for (path, count) in value.projects {
                    let name = path == "unassigned" ? L("未归属", "Unassigned") : URL(fileURLWithPath: path).lastPathComponent
                    projects.append(UsageReportProject(id: path, name: name, requests: count))
                }
                projects.sort { left, right in left.requests == right.requests ? left.id < right.id : left.requests > right.requests }
                let names: [String: [UsageReportProject]] = Dictionary(grouping: projects, by: { $0.name })
                projects = projects.map { row in var row = row; if names[row.name, default: []].count > 1 { row.name += " · " + URL(fileURLWithPath: row.id).deletingLastPathComponent().lastPathComponent }; return row }
                if projects.count > 3 { let rest = projects.dropFirst(3); projects = Array(projects.prefix(3)) + [UsageReportProject(id: "other", name: L("其他项目", "Other projects"), requests: rest.reduce(0) {$0+$1.requests})] }
                let namesOfTimes = [L("凌晨", "Night"), L("上午", "Morning"), L("午后", "Afternoon"), L("晚间", "Evening")]
                labelFormatter.locale = Locale(identifier: L("zh_CN", "en_US")); labelFormatter.timeZone = calendar.timeZone
                labelFormatter.dateFormat = period == .month ? "yyyy.MM" : "yyyy.MM.dd"
                var label = labelFormatter.string(from: range.start)
                if period == .week { labelFormatter.dateFormat = "MM.dd"; label += " — " + labelFormatter.string(from: calendar.date(byAdding: .day, value: -1, to: range.end)!) }
                let data = UsageReportData(period: period, start: range.start, end: range.end, timeZone: calendar.timeZone.identifier, dateLabel: label, fileDate: dayFormatter.string(from: range.start), sourceKey: sourceID, priceVersion: priceVersion, requests: value.requests, modelCalls: value.modelCalls, inputTokens: value.input, outputTokens: value.output, cachedInputTokens: value.cached, totalTokens: value.total, tokensComplete: value.unknownTokens == 0, costUSD: value.costValue, costComplete: value.unknownCosts == 0, models: models, projects: projects, timeSlices: (0..<4).map { UsageReportTimeSlice(name: namesOfTimes[$0], requests: value.times[$0]) }, firstRequest: value.first, lastRequest: value.last, previousTokens: previous.total, previousRequests: previous.requests, previousCostUSD: previous.costValue, comparisonComplete: value.unknownTokens == 0 && previous.unknownTokens == 0)
                let folder = directory.appendingPathComponent(sourceID).appendingPathComponent(period.rawValue)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                let destination = folder.appendingPathComponent(data.fileDate + ".json"), bytes = try encoder.encode(data)
                if (try? Data(contentsOf: destination)) != bytes { try bytes.write(to: destination, options: .atomic) }
                documents[period] = data
            }
            cached = documents; cachedKey = inputKey
        }
        return UsageReportCollection(documents: cached, presentationDay: todayKey, alreadyPresented: seen?.contains(todayKey) == true)
    }

    func markPresented(_ day: String) throws {
        if seen == nil { seen = Set(readObject(directory.appendingPathComponent("presentation.json"))["days"] as? [String] ?? []) }
        seen?.insert(day)
        let days = Array((seen ?? []).sorted().suffix(90)); seen = Set(days)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try atomicJSON(["days": days], to: directory.appendingPathComponent("presentation.json"))
    }
}
