import Foundation

// Small, precomputed display data for the menu and WidgetKit snapshot. Owned by
// AppState's data queue; no widget or view queries the ledger or source logs.
struct AgentSurfaceData {
    var ready = false
    var request: UsageRow?
    var today = UsageSummary()
    var days: [DayUsage] = []
    var hasRunningTask = false
    var nextBoundary = Date.distantFuture
    var contentKey: String {
        let fields = ["id","prompt_preview","model","reasoning_effort","service_tier","model_context_window","status","duration_running","duration_started_at","duration_ms","input_tokens","output_tokens","cached_input_tokens","total_tokens","cost_usd"]
        let selected = request.map {row in Dictionary(uniqueKeysWithValues:fields.map {($0,row.raw[$0] ?? NSNull())})} ?? [:]
        return identity(["ready":ready,"running":hasRunningTask,"request":selected,"today":today.json,"input":today.input,"cached":today.cached,"output":today.output,"days":days.map { ["date":$0.date.timeIntervalSince1970,"tokens":$0.tokens as Any? ?? NSNull(),"cost":$0.cost as Any? ?? NSNull(),"requests":$0.requests] as Object }] as Object)
    }
}

extension DesktopClient {
    var surfaceTitle: String {self == .codex ? "ChatGPT" : "Claude"}
    var widgetAgent: WidgetAgent {self == .codex ? .chatgpt : .claude}
}

enum AgentWidgetPayload {
    static func make(request: UsageRow?,today: UsageSummary?,quota: QuotaState,fresh: Bool,observed: Date?) -> Object {
        var selected: Any = NSNull()
        if let request {
            var value: Object = ["id":request.id,"prompt":String(request.raw.string("prompt_preview").prefix(240)),"model":modelName(request.raw.string("model")),"reasoning_effort":logEffortName(request.raw.string("reasoning_effort")),"duration_running":request.raw.flag("duration_running")]
            for key in ["cost_usd","duration_ms","input_tokens","output_tokens","total_tokens","cached_input_tokens","cache_hit_rate","service_tier","model_context_window"] {value[key] = request.raw[key] ?? NSNull()}
            if let start = parsedDate(request.raw["duration_started_at"]) {value["duration_started_at"] = start.timeIntervalSince1970-(request.raw.number("duration_base_ms") ?? 0)/1000}
            selected = value
        }
        let summary: Any = today.map {["cost_usd":$0.cost as Any? ?? NSNull(),"tokens":$0.tokens as Any? ?? NSNull(),"requests":$0.requests,"cache_hit_rate":$0.cacheRate as Any? ?? NSNull()] as Object} ?? NSNull()
        return ["updated_at":observed?.timeIntervalSince1970 as Any? ?? NSNull(),"request":selected,"today":summary,"quota":["applicable":quota.applicable,"updated_at":quota.updated?.timeIntervalSince1970 as Any? ?? NSNull(),"five_hour":(fresh ? quota.five?.remaining : nil) as Any? ?? NSNull(),"week":(fresh ? quota.week?.remaining : nil) as Any? ?? NSNull(),"has_five_hour":quota.five != nil,"has_week":quota.week != nil,"five_hour_reset_at":(fresh ? quota.five?.reset?.timeIntervalSince1970 : nil) as Any? ?? NSNull(),"week_reset_at":(fresh ? quota.week?.reset?.timeIntervalSince1970 : nil) as Any? ?? NSNull(),"reset_count":(fresh ? quota.availableCount : nil) as Any? ?? NSNull()]]
    }
}
