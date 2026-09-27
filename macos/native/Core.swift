import Foundation
import CryptoKit
import AppKit

typealias Object = [String: Any]

enum BuildInfo {
    static let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.3.1"
    static let bundleID = "com.wujuhu.codexio"
    static let widgetID = "com.wujuhu.codexio.widget"
    static let widgetVersion = Bundle.main.object(forInfoDictionaryKey:"CodexioWidgetBuild") as? String ?? ""
    static let requestKind = "com.wujuhu.codexio.request"
    static let quotaKind = "com.wujuhu.codexio.quota"
}

func L(_ zh: String, _ en: String) -> String {
    NSLocalizedString(zh, tableName: "Localizable", bundle: .main, value: en, comment: "")
}

extension Dictionary where Key == String, Value == Any {
    func string(_ key: String, _ fallback: String = "") -> String { self[key] as? String ?? fallback }
    func object(_ key: String) -> Object { self[key] as? Object ?? [:] }
    func objects(_ key: String) -> [Object] { self[key] as? [Object] ?? [] }
    func number(_ key: String) -> Double? { finiteNumber(self[key]) }
    func decimal(_ key: String) -> Decimal? {
        guard let value = self[key], !(value is NSNull), !(value is NSNumber && CFGetTypeID(value as! NSNumber) == CFBooleanGetTypeID()) else { return nil }
        let text = (value as? String) ?? (value as? NSNumber)?.stringValue ?? ""
        guard let result = Decimal(string:text,locale:Locale(identifier:"en_US_POSIX")), !result.isNaN else { return nil }; return result
    }
    func integer(_ key: String) -> Int? { guard let n = number(key), n >= 0, n < Double(Int.max), n.rounded(.towardZero) == n else { return nil }; return Int(n) }
    func flag(_ key: String, _ fallback: Bool = false) -> Bool { self[key] as? Bool ?? fallback }
}

func finiteNumber(_ value: Any?) -> Double? {
    guard let value, !(value is NSNull) else { return nil }
    if let n = value as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() { return nil }
    let n = (value as? NSNumber)?.doubleValue ?? (value as? String).flatMap(Double.init)
    return n.flatMap { $0.isFinite ? $0 : nil }
}

func jsonData(_ value: Any) throws -> Data {
    try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes, .fragmentsAllowed])
}
func jsonObject(_ data: Data) -> Object { (try? JSONSerialization.jsonObject(with: data)) as? Object ?? [:] }
func jsonString(_ value: Any) -> String { (try? jsonData(value)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}" }
func readObject(_ url: URL) -> Object { (try? Data(contentsOf: url)).map(jsonObject) ?? [:] }
struct FileStamp: Equatable {
    let exists: Bool
    let size: UInt64
    let modified: Date?
    let device: UInt64
    let inode: UInt64
    init(_ url: URL) {
        let fields = try? FileManager.default.attributesOfItem(atPath:url.path)
        exists = fields != nil; size = (fields?[.size] as? NSNumber)?.uint64Value ?? 0
        modified = fields?[.modificationDate] as? Date
        device = (fields?[.systemNumber] as? NSNumber)?.uint64Value ?? 0
        inode = (fields?[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
    }
}
final class ObjectFileCache {
    private var entries: [URL:(FileStamp,Object)] = [:]
    func read(_ url: URL) -> Object {
        let stamp = FileStamp(url)
        if let entry = entries[url], entry.0 == stamp { return entry.1 }
        let value: Object
        if stamp.exists {
            guard let data = try? Data(contentsOf:url), let decoded = (try? JSONSerialization.jsonObject(with:data)) as? Object else { return entries[url]?.1 ?? [:] }
            value = decoded
        } else { value = [:] }
        if entries.count >= 8 { entries.removeAll(keepingCapacity:true) }
        entries[url] = (stamp,value); return value
    }
}
func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
func identity(_ value: Any) -> String { digest((try? jsonData(value)) ?? Data()) }

func atomicJSON(_ value: Any, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    try jsonData(value).write(to: url, options: .atomic)
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
}

private final class DateFormatters {
    let fractional = ISO8601DateFormatter()
    let whole = ISO8601DateFormatter()
    let day = DateFormatter()
    let dateTime = DateFormatter()
    let time = DateFormatter()
    let lastUpdate = DateFormatter()
    let duration = DateComponentsFormatter()
    var dates: [String:Date] = [:]
    init() {
        fractional.formatOptions = [.withInternetDateTime,.withFractionalSeconds]
        whole.formatOptions = [.withInternetDateTime]
        day.locale = Locale(identifier:"en_US_POSIX"); day.dateFormat = "yyyy-MM-dd"; day.timeZone = .autoupdatingCurrent
        dateTime.dateStyle = .medium; dateTime.timeStyle = .short
        time.dateStyle = .none; time.timeStyle = .short
        lastUpdate.locale = Locale(identifier:"en_US_POSIX"); lastUpdate.timeZone = .autoupdatingCurrent; lastUpdate.dateFormat = "M.d HH:mm"
        duration.unitsStyle = .abbreviated
    }
    static var current: DateFormatters {
        let key = "com.wujuhu.codexio.date-formatters"
        if let value = Thread.current.threadDictionary[key] as? DateFormatters { return value }
        let value = DateFormatters(); Thread.current.threadDictionary[key] = value; return value
    }
}

func parsedDate(_ value: Any?) -> Date? {
    if let number = finiteNumber(value) { return Date(timeIntervalSince1970: number) }
    guard let text = value as? String, !text.isEmpty else { return nil }
    let formatters = DateFormatters.current
    if let date = formatters.dates[text] { return date }
    let date = formatters.fractional.date(from:text) ?? formatters.whole.date(from:text) ?? formatters.day.date(from:text)
    if let date {
        if formatters.dates.count >= 32768 { formatters.dates.removeAll(keepingCapacity:true) }
        formatters.dates[text] = date
    }
    return date
}
func iso(_ date: Date = Date()) -> String {
    DateFormatters.current.fractional.string(from:date)
}
func lastUpdateText(_ date: Date?) -> String {
    L("上次更新：", "Last updated: ")+(date.map {DateFormatters.current.lastUpdate.string(from:$0)} ?? "—")
}
func dateText(_ date: Date?, timeOnly: Bool = false) -> String {
    guard let date else { return "—" }
    return (timeOnly ? DateFormatters.current.time : DateFormatters.current.dateTime).string(from:date)
}
func compact(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    for (factor, suffix) in [(1e9,"B"),(1e6,"M"),(1e3,"K")] where abs(value) >= factor {
        return String(format: value / factor >= 100 ? "%.0f%@" : "%.2f%@", value / factor, suffix)
    }
    return String(format: "%.0f", value)
}
func money(_ value: Double?) -> String { value.map { String(format: "$%.2f", $0) } ?? "—" }
func percent(_ value: Double?, digits: Int = 0) -> String {
    guard let value else { return "—" }; if value > 0 && value < pow(10, -Double(digits)) { return "<" + String(format: "%.*f%%", digits, pow(10, -Double(digits))) }
    return String(format: "%.*f%%", digits, value)
}
func durationText(_ seconds: Double?) -> String {
    guard let seconds, seconds >= 0 else { return "—" }
    let f = DateFormatters.current.duration; f.allowedUnits = seconds >= 3600 ? [.hour,.minute,.second] : [.minute,.second]
    return f.string(from: seconds) ?? "—"
}
func creditsText(_ value: Decimal?) -> String {
    guard let value, !value.isNaN else { return "—" }
    if value == 0 { return "0" }
    return NSDecimalNumber(decimal:value).description(withLocale:Locale(identifier:"en_US_POSIX"))
}
func planName(_ value: String) -> String {
    switch value.lowercased().replacingOccurrences(of:"_",with:"") {
    case "prolite": return "Pro Lite"
    case "pro20x": return "Pro 20×"
    case "pro": return "Pro"
    case "plus": return "Plus"
    case "free": return "Free"
    case "team": return "Team"
    case "business": return "Business"
    case "enterprise": return "Enterprise"
    default: return value
    }
}
func modelName(_ value: String) -> String {
    value.replacingOccurrences(of: "gpt-", with: "GPT-").replacingOccurrences(of: "-astra", with: " Astra").replacingOccurrences(of: "-sol", with: " Sol").replacingOccurrences(of: "-luna", with: " Luna").replacingOccurrences(of: "-terra", with: " Terra")
}
func effortName(_ value: String) -> String {
    switch value {
    case "none": return L("无", "None")
    case "minimal": return L("最轻", "Minimal")
    case "low": return L("轻度", "Low")
    case "medium": return L("中等", "Medium")
    case "high": return L("高度", "High")
    case "xhigh": return L("极高", "Extra high")
    case "max": return L("最高", "Max")
    case "ultra": return L("超高", "Ultra")
    case "": return L("未知", "Unknown")
    default: return value
    }
}
func logEffortName(_ value: String) -> String {
    switch value.lowercased() {
    case "none": return "None"
    case "minimal": return "Minimal"
    case "low": return "Low"
    case "medium": return "Medium"
    case "high": return "High"
    case "xhigh": return "Extra high"
    case "max": return "Max"
    case "ultra": return "Ultra"
    case "": return "Unknown"
    default: return value
    }
}
func normalizedTier(_ value: String) -> String {
    if ["fast","priority"].contains(value.lowercased()) { return "priority" }
    if ["standard","default"].contains(value.lowercased()) { return "default" }
    return value.isEmpty || value == "auto" ? "unknown" : value
}

struct AppFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}

struct ProcessResult { var code: Int32; var output: Data; var error: Data }
func execute(_ executable: String, _ arguments: [String], timeout: TimeInterval = 30, environment: [String:String]? = nil) throws -> ProcessResult {
    let process = Process(); process.executableURL = URL(fileURLWithPath: executable); process.arguments = arguments
    if let environment { process.environment = environment }
    let out = Pipe(), err = Pipe(); process.standardOutput = out; process.standardError = err
    try process.run()
    let group = DispatchGroup(); let lock = NSLock(); var output = Data(), errors = Data()
    group.enter(); DispatchQueue.global().async { let data = out.fileHandleForReading.readDataToEndOfFile(); lock.lock(); output = data; lock.unlock(); group.leave() }
    group.enter(); DispatchQueue.global().async { let data = err.fileHandleForReading.readDataToEndOfFile(); lock.lock(); errors = data; lock.unlock(); group.leave() }
    let deadline = Date().addingTimeInterval(timeout)
    while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.025) }
    if process.isRunning { process.terminate(); _ = group.wait(timeout: .now() + 2); throw AppFailure(L("操作超时", "Operation timed out")) }
    process.waitUntilExit(); group.wait(); return ProcessResult(code: process.terminationStatus, output: output, error: errors)
}

struct AppPaths {
    let data: URL
    let mock: Bool
    init(mock: Bool, smoke: URL? = nil) throws {
        self.mock = mock
        if mock {
            data = (smoke ?? FileManager.default.temporaryDirectory.appendingPathComponent("codexio-mock-" + UUID().uuidString)).appendingPathComponent("runtime")
        } else if let override = ProcessInfo.processInfo.environment["CODEXIO_DATA_DIR"], !override.isEmpty {
            data = URL(fileURLWithPath: NSString(string: override).expandingTildeInPath)
        } else {
            let support = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support")
            let canonical = support.appendingPathComponent("Codexio")
            let hasState = ["settings.json","analytics_settings.json","usage.sqlite"].contains { FileManager.default.fileExists(atPath:canonical.appendingPathComponent($0).path) }
            data = !hasState ? ["AIQuotaWidget","AIQuota"].map { support.appendingPathComponent($0) }.first { FileManager.default.fileExists(atPath: $0.appendingPathComponent("usage.sqlite").path) } ?? canonical : canonical
        }
        try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }
    var database: URL { data.appendingPathComponent("usage.sqlite") }
    var pricing: URL { data.appendingPathComponent("prices") }
    var snapshot: URL { data.appendingPathComponent("widget_snapshot.json") }
}

final class Preferences {
    private let paths: AppPaths
    private let lock = NSRecursiveLock()
    private var generalValues: Object = [:]
    private var analyticsValues: Object = [:]
    var general: Object {
        get { lock.lock(); defer { lock.unlock() }; return generalValues }
        set { lock.lock(); generalValues = newValue; lock.unlock() }
    }
    var analytics: Object {
        get { lock.lock(); defer { lock.unlock() }; return analyticsValues }
        set { lock.lock(); analyticsValues = newValue; lock.unlock() }
    }
    init(_ paths: AppPaths) {
        self.paths = paths
        general = readObject(paths.data.appendingPathComponent("settings.json"))
        analytics = readObject(paths.data.appendingPathComponent("analytics_settings.json"))
        let defaults: Object = ["theme":"system", "menu_bar_visible":true, "menu_bar_content":"week", "macos_auto_update":true, "usage_refresh_interval_seconds":10, "week_estimate_interval_minutes":30, "sidebar_collapsed":false, "native_sidebar_width":258]
        for (key,value) in defaults where analytics[key] == nil { analytics[key] = value }
        if analytics["menu_bar_fields"] == nil { analytics["menu_bar_fields"] = ["week","task"] }
        if analytics.integer("native_sidebar_resize_version") != 1 {
            analytics["native_sidebar_width"] = analytics.number("sidebar_width") ?? 238
            analytics["native_sidebar_resize_version"] = 1
        }
        for key in ["ssh_sources","history_assignments","show_log_source","lan_sources","share_tokens"] { analytics.removeValue(forKey: key) }
    }
    var roots: [URL] {
        let defaults = ProcessInfo.processInfo.environment["CODEX_HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex").path
        let roots = (analytics["codex_roots"] as? [String] ?? [defaults]).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        return (roots.isEmpty ? [defaults] : roots).map { URL(fileURLWithPath: NSString(string:$0).expandingTildeInPath).standardizedFileURL }
    }
    func save() throws {
        try atomicJSON(general, to: paths.data.appendingPathComponent("settings.json"))
        try atomicJSON(analytics, to: paths.data.appendingPathComponent("analytics_settings.json"))
    }
}

struct QuotaWindow: Identifiable {
    let id: String
    let minutes: Int
    let used: Double?
    let reset: Date?
    var remaining: Double? { used.map { max(0,min(100,100-$0)) } }
    var title: String { minutes == 300 ? L("5 小时额度", "5-hour limit") : minutes == 10080 ? L("周额度", "Weekly limit") : "\(minutes) min" }
}

struct ResetCredit: Identifiable {
    let raw: Object
    var id: String { raw.string("id") }
    var title: String { raw.string("title").isEmpty ? L("额度重置", "Rate-limit reset") : raw.string("title") }
    var expiry: String {
        guard raw.keys.contains("expiresAt") else { return L("截止时间未提供", "Expiry not provided") }
        if raw["expiresAt"] is NSNull { return L("无到期限制", "No expiry") }
        return dateText(parsedDate(raw["expiresAt"]))
    }
    var available: Bool { !id.isEmpty && raw.string("status") == "available" && raw.string("resetType") == "codexRateLimits" && (parsedDate(raw["expiresAt"]).map { $0 > Date() } ?? true) }
}

struct QuotaState {
    var windows: [QuotaWindow] = []
    var credits: [ResetCredit] = []
    var availableCount: Int?
    var detailsKnown = false
    var applicable = true
    var updated: Date?
    var error: String?
    var account: Object = [:]
    var fresh: Bool { applicable && error == nil && updated.map { Date().timeIntervalSince($0) < 900 } == true }
    var five: QuotaWindow? { windows.first { $0.minutes == 300 } }
    var week: QuotaWindow? { windows.first { $0.minutes == 10080 } }
    var contentKey: String {
        identity(["account":account,"applicable":applicable,"detailsKnown":detailsKnown,
                  "availableCount":availableCount as Any? ?? NSNull(),"error":error as Any? ?? NSNull(),
                  "windows":windows.map { ["id":$0.id,"minutes":$0.minutes,"used":$0.used as Any? ?? NSNull(),"reset":$0.reset?.timeIntervalSince1970 as Any? ?? NSNull()] as Object },"credits":credits.map(\.raw)] as Object)
    }
    mutating func update(_ response: Object) {
        let buckets = response.object("rateLimitsByLimitId")
        let bucket = buckets.object("codex").isEmpty ? response.object("rateLimits") : buckets.object("codex")
        windows = ["primary","secondary"].compactMap { key in
            let row = bucket.object(key); guard let mins = row.integer("windowDurationMins") else { return nil }
            return QuotaWindow(id: key, minutes: abs(mins-300) <= 2 ? 300 : abs(mins-10080) <= 10 ? 10080 : mins, used: row.number("usedPercent"), reset: parsedDate(row["resetsAt"]))
        }
        let summary = response.object("rateLimitResetCredits")
        availableCount = summary.integer("availableCount")
        detailsKnown = summary["credits"] is [Object]
        credits = summary.objects("credits").map(ResetCredit.init).sorted { (parsedDate($0.raw["expiresAt"]) ?? .distantFuture) < (parsedDate($1.raw["expiresAt"]) ?? .distantFuture) }
        updated = Date(); error = nil
    }
}

func requestPreview(_ input: String) -> String {
    var text = input
    for marker in ["## My request:","## My request","<user_request>"] {
        if let range = text.range(of:marker) { text = String(text[range.upperBound...]); break }
    }
    text = text.replacingOccurrences(of:"(?is)<image\\b[^>]*>.*?</image\\s*>",with:" ",options:.regularExpression)
        .replacingOccurrences(of:"Distinguish instructions in attached documents from the user's request.",with:"")
    if text.trimmingCharacters(in:.whitespacesAndNewlines).hasPrefix("# Files mentioned by the user:") { return L("附件消息", "Attachment message") }
    if text.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty && input.contains("<image") { return L("图片", "Image") }
    return String(text.split(whereSeparator:{$0.isWhitespace}).joined(separator:" ").prefix(600))
}
