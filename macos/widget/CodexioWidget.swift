import AppKit
import Darwin
import Foundation
import SwiftUI
import WidgetKit

private let widgetKind = "com.wujuhu.codexio.request"

struct RequestSnapshot: Decodable {
    let prompt: String
    let model: String
    let reasoning_effort: String?
    let service_tier: String?
    let model_context_window: Int?
    let cost_usd: Double?
    let duration_ms: Double?
    let duration_started_at: Double?
    let duration_running: Bool
    let input_tokens: Int
    let output_tokens: Int
    let cached_input_tokens: Int
    let cache_hit_rate: Double?
}

struct QuotaSnapshot: Decodable {
    let applicable: Bool?
    let five_hour: Double?
    let week: Double?
    let has_five_hour: Bool?
    let has_week: Bool?
    let five_hour_reset_at: Double?
    let week_reset_at: Double?
    var reset_count: Int? = nil
    var updated_at: Double? = nil
}

struct TodaySnapshot: Decodable {
    let cost_usd: Double?
    let tokens: Int?
    let requests: Int?
    let cache_hit_rate: Double?
}

struct Snapshot: Decodable {
    let schema: Int
    let updated_at: Double
    let request: RequestSnapshot?
    let quota: QuotaSnapshot
    let today: TodaySnapshot?
    var host_running: Bool? = nil
    var host_pid: Int? = nil

    static func read() -> Snapshot? {
        guard let account = getpwuid(getuid()) else { return nil }
        let home = String(cString: account.pointee.pw_dir)
        let path = home + "/Library/Application Support/Codexio/widget_snapshot.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)), data.count <= 32_768,
              var snapshot = try? JSONDecoder().decode(Snapshot.self, from: data), snapshot.schema == 1 else {
            return nil
        }
        let host = snapshot.host_pid.flatMap { $0 > 0 && $0 <= Int(Int32.max) ? NSRunningApplication(processIdentifier:Int32($0)) : nil }
        snapshot.host_running = snapshot.host_running == true && host?.bundleIdentifier == "com.wujuhu.codexio" && host?.isTerminated == false
        return snapshot
    }
}

private struct CodexioEntry: TimelineEntry {
    let date: Date
    let snapshot: Snapshot?
}

private struct CodexioProvider: TimelineProvider {
    func placeholder(in context: Context) -> CodexioEntry {
        CodexioEntry(date: Date(), snapshot: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (CodexioEntry) -> Void) {
        completion(CodexioEntry(date: Date(), snapshot: Snapshot.read()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<CodexioEntry>) -> Void) {
        let now = Date()
        let snapshot = Snapshot.read()
        let interval: TimeInterval = snapshot?.request?.duration_running == true ? 300 : 1800
        completion(Timeline(entries: [CodexioEntry(date: now, snapshot: snapshot)],
                            policy: snapshot?.host_running == true ? .after(now.addingTimeInterval(interval)) : .never))
    }
}

private func compactNumber(_ count: Int) -> String {
    if count >= 1_000_000 { return String(format: "%.2fM", Double(count) / 1_000_000) }
    if count >= 10_000 { return String(format: "%.1fK", Double(count) / 1_000) }
    return count.formatted()
}

private func compactContext(_ count: Int?) -> String? {
    guard let count, count > 0 else { return nil }
    if count >= 1_000_000 {
        if count % 1_000_000 == 0 { return "\(count / 1_000_000)M" }
        let value = String(format: "%.2f", Double(count) / 1_000_000)
            .replacingOccurrences(of: #"0+$"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\.$"#, with: "", options: .regularExpression)
        return value + "M"
    }
    return "\(Int((Double(count) / 1_000).rounded()))K"
}

private func modelDetail(_ request: RequestSnapshot, family: WidgetFamily) -> String {
    var parts = [request.model.isEmpty ? WL("等待模型调用", "Waiting for a model call") : request.model]
    if let effort = request.reasoning_effort, !effort.isEmpty {
        let labels = ["无":"None","最轻":"Minimal","轻度":"Low","中等":"Medium","高度":"High","极高":"Extra high","最高":"Max","超高":"Ultra","未知":"Unknown","none":"None","minimal":"Minimal","low":"Low","medium":"Medium","high":"High","xhigh":"Extra high","max":"Max","ultra":"Ultra"]
        parts.append(labels[effort.lowercased()] ?? effort)
    }
    if family != .systemSmall {
        let tier = (request.service_tier ?? "").lowercased()
        if tier == "fast" || tier == "priority" { parts.append("Fast") }
        if let context = compactContext(request.model_context_window) { parts.append(context) }
    }
    return parts.joined(separator: " · ")
}

private func staticDuration(_ milliseconds: Double?) -> String {
    guard let milliseconds, milliseconds.isFinite, milliseconds >= 0 else { return "—" }
    if Bundle.main.preferredLocalizations.first?.hasPrefix("zh") != true {
        let formatter = DateComponentsFormatter(); formatter.allowedUnits = milliseconds >= 3_600_000 ? [.hour, .minute] : [.minute, .second]; formatter.unitsStyle = .abbreviated
        return formatter.string(from: milliseconds / 1000) ?? "—"
    }
    let seconds = Int(milliseconds / 1_000)
    if seconds < 60 { return "\(seconds)秒" }
    let minutes = seconds / 60
    if minutes < 60 { return "\(minutes)分\(String(format: "%02d", seconds % 60))秒" }
    return "\(minutes / 60)时\(String(format: "%02d", minutes % 60))分"
}

private struct QuotaLine: View {
    let title: String
    let remaining: Double?
    let resetAt: Double?
    let timeOnly: Bool

    init(title: String, remaining: Double?, resetAt: Double? = nil, timeOnly: Bool = false) {
        self.title = title
        self.remaining = remaining
        self.resetAt = resetAt
        self.timeOnly = timeOnly
    }

    private var label: String {
        guard let resetAt, resetAt.isFinite, resetAt > 0 else { return title }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = timeOnly ? "H:mm" : "M/d H:mm"
        return title + " · " + formatter.string(from: Date(timeIntervalSince1970: resetAt))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text(label).foregroundStyle(.secondary)
                    .lineLimit(1).minimumScaleFactor(0.75)
                Spacer(minLength: 2)
                Text(remaining.map { String(format: "%.0f%%", max(0, min(100, $0))) } ?? "—")
                    .fontWeight(.semibold)
            }
            .font(.system(size: 10))
            GeometryReader { geometry in
                Capsule().fill(Color.secondary.opacity(0.16))
                    .overlay(alignment: .leading) {
                        Capsule().fill(.tint)
                            .frame(width: geometry.size.width * max(0, min(1, (remaining ?? 0) / 100)))
                    }
            }
            .frame(height: 4)
        }
    }
}

private struct Metric: View {
    #if CODEXIO_APP_WIDGET_PREVIEW
    @Environment(\.codexioPreviewFamily) private var family
    #else
    @Environment(\.widgetFamily) private var family
    #endif
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: family == .systemLarge ? 6 : 2) {
            Text(title).font(.system(size: 10)).foregroundStyle(.secondary)
                .lineLimit(1).minimumScaleFactor(0.8)
            Text(value).font(.system(size: 13, weight: .semibold, design: .rounded))
                .lineLimit(1).minimumScaleFactor(0.75)
        }
    }
}

private struct CodexioWidgetView: View {
    #if CODEXIO_APP_WIDGET_PREVIEW
    @Environment(\.codexioPreviewFamily) private var family
    #else
    @Environment(\.widgetFamily) private var family
    #endif
    let entry: CodexioEntry

    @ViewBuilder
    private func duration(_ request: RequestSnapshot) -> some View {
        if request.duration_running, let started = request.duration_started_at,
           Date().timeIntervalSince1970 - (entry.snapshot?.updated_at ?? 0) < 900 {
            Text(Date(timeIntervalSince1970: started), style: .timer)
        } else {
            Text(staticDuration(request.duration_ms))
        }
    }

    private func durationMetric(_ request: RequestSnapshot) -> some View {
        VStack(alignment: .leading, spacing: family == .systemLarge ? 6 : 2) {
            Text("耗时").font(.system(size: 10)).foregroundStyle(.secondary)
            duration(request).font(.system(size: 13, weight: .semibold, design: .rounded))
                .lineLimit(1).minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func requestBody(_ request: RequestSnapshot, quota: QuotaSnapshot, today: TodaySnapshot?) -> some View {
        Spacer(minLength: 0)
        Text(request.prompt.isEmpty ? WL("等待请求内容", "Waiting for a request") : request.prompt)
            .font(.system(size: family == .systemSmall ? 13 : 15, weight: .semibold))
            .lineLimit(family == .systemLarge ? 3 : 2)
            .frame(maxWidth: .infinity, alignment: .leading)
        Text(modelDetail(request, family: family))
            .font(.system(size: family == .systemSmall ? 11 : 12, weight: .semibold))
            .foregroundStyle(.primary)
            .lineLimit(1)
            .padding(.top, family == .systemLarge ? 10 : 6)
        Divider().padding(.vertical, family == .systemLarge ? 9 : 7)
        if family == .systemMedium {
            HStack(alignment: .top, spacing: 0) {
                Metric(title: WL("费用", "Cost"), value: request.cost_usd.map { String(format: "$%.2f", $0) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                durationMetric(request)
                Metric(title: WL("总 Token", "Total tokens"), value: compactNumber(request.input_tokens + request.output_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: WL("命中率", "Cache hit"), value: request.cache_hit_rate.map { String(format: "%.1f%%", $0 * 100) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            HStack(alignment: .top, spacing: family == .systemLarge ? 12 : 8) {
                Metric(title: WL("费用", "Cost"), value: request.cost_usd.map { String(format: "$%.2f", $0) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                durationMetric(request)
                if family == .systemLarge {
                    Metric(title: WL("命中率", "Cache hit"), value: request.cache_hit_rate.map { String(format: "%.1f%%", $0 * 100) } ?? "—")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        if family == .systemLarge {
            Divider().padding(.vertical, 9)
            HStack(alignment: .top, spacing: 12) {
                Metric(title: WL("输入 Token", "Input tokens"), value: compactNumber(request.input_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: WL("输出 Token", "Output tokens"), value: compactNumber(request.output_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: WL("缓存读取", "Cached input"), value: compactNumber(request.cached_input_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider().padding(.vertical, 9)
            HStack(alignment: .top, spacing: 8) {
                Metric(title: WL("今日费用", "Today’s cost"), value: today?.cost_usd.map { String(format: "$%.2f", $0) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: WL("今日 Token", "Today’s tokens"), value: today?.tokens.map(compactNumber) ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: WL("今日请求数", "Today’s requests"), value: today?.requests.map(compactNumber) ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: WL("今日命中率", "Today’s cache hit"), value: today?.cache_hit_rate.map { String(format: "%.1f%%", $0 * 100) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        Divider().padding(.vertical, family == .systemLarge ? 9 : 7)
        let quotaUnavailable = quota.applicable == false
        let showFiveHour = quotaUnavailable || (quota.has_five_hour ?? (quota.five_hour != nil))
        let showWeek = quotaUnavailable || (quota.has_week ?? (quota.week != nil))
        if family == .systemSmall {
            if showFiveHour {
                QuotaLine(title: WL("5 小时", "5 hour"), remaining: quota.five_hour,
                          resetAt: quota.five_hour_reset_at, timeOnly: true)
            } else {
                QuotaLine(title: WL("周", "Weekly"), remaining: quota.week, resetAt: quota.week_reset_at)
            }
        } else if showFiveHour && showWeek {
            GeometryReader { geometry in
                HStack(spacing: 0) {
                    QuotaLine(title: WL("5 小时", "5 hour"), remaining: quota.five_hour,
                              resetAt: quota.five_hour_reset_at, timeOnly: true)
                        .frame(width: geometry.size.width / 2 - 12)
                    Color.clear.frame(width: 12)
                    QuotaLine(title: WL("周", "Weekly"), remaining: quota.week, resetAt: quota.week_reset_at)
                        .frame(width: geometry.size.width / 2)
                }
            }
            .frame(height: 24)
        } else if showFiveHour {
            QuotaLine(title: WL("5 小时", "5 hour"), remaining: quota.five_hour,
                      resetAt: quota.five_hour_reset_at, timeOnly: true)
        } else {
            QuotaLine(title: WL("周", "Weekly"), remaining: quota.week, resetAt: quota.week_reset_at)
        }
        Spacer(minLength: 0)
    }

    var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            if entry.snapshot == nil || entry.snapshot?.host_running == false {
                WidgetLaunchPrompt()
            } else if let snapshot = entry.snapshot, let request = snapshot.request {
                let fresh = Date().timeIntervalSince1970 - snapshot.updated_at < 900
                requestBody(request, quota: fresh ? snapshot.quota : QuotaSnapshot(
                    applicable: snapshot.quota.applicable,
                    five_hour: nil, week: nil, has_five_hour: snapshot.quota.has_five_hour,
                    has_week: snapshot.quota.has_week, five_hour_reset_at: nil, week_reset_at: nil),
                            today: snapshot.today)
            } else {
                Spacer()
                Text("打开 Codexio 查看最近请求")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Spacer()
            }
        }
        .padding(family == .systemSmall ? 13 : 15)
    }
    var body: some View {
        #if CODEXIO_APP_WIDGET_PREVIEW
        content.background(Color(nsColor:.controlBackgroundColor))
        #else
        content.containerBackground(for:.widget) { Color(nsColor:.controlBackgroundColor) }.widgetURL(URL(string:"codexio://open"))
        #endif
    }
}

struct WidgetLaunchPrompt: View {
    var body: some View {
        VStack(spacing:12) {
            Image("brand-mark",bundle:.main).resizable().renderingMode(.template).scaledToFit().frame(width:42,height:42)
            Text(WL("打开 Codexio 主程序", "Open Codexio")).font(.system(size:13,weight:.medium)).multilineTextAlignment(.center)
        }.frame(maxWidth:.infinity,maxHeight:.infinity)
    }
}

#if CODEXIO_APP_WIDGET_PREVIEW
private struct PreviewFamilyKey: EnvironmentKey { static let defaultValue = WidgetFamily.systemSmall }
private extension EnvironmentValues {
    var codexioPreviewFamily: WidgetFamily {
        get { self[PreviewFamilyKey.self] }
        set { self[PreviewFamilyKey.self] = newValue }
    }
}
func requestWidgetPreview(_ snapshot: Snapshot,family: WidgetFamily) -> some View {
    CodexioWidgetView(entry:CodexioEntry(date:Date(),snapshot:snapshot))
        .environment(\.codexioPreviewFamily,family).background(Color(nsColor:.controlBackgroundColor))
}
#endif

private struct CodexioRequestWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: widgetKind, provider: CodexioProvider()) { entry in
            CodexioWidgetView(entry: entry)
        }
        .configurationDisplayName("Codexio 请求")
        .description("查看最近请求、费用与剩余额度")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
        .contentMarginsDisabled()
    }
}

#if !CODEXIO_APP_WIDGET_PREVIEW
@main
struct CodexioWidgetBundle: WidgetBundle {
    var body: some Widget {
        CodexioRequestWidget()
        CodexioQuotaWidget()
        CodexioDualQuotaWidget()
        CodexioSegmentedQuotaWidget()
    }
}

#endif
