import AppKit
import Darwin
import Foundation
import SwiftUI
import WidgetKit

private let widgetKind = "com.wujuhu.codexio.request"

private struct RequestSnapshot: Decodable {
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

private struct QuotaSnapshot: Decodable {
    let applicable: Bool?
    let five_hour: Double?
    let week: Double?
    let has_five_hour: Bool?
    let has_week: Bool?
    let five_hour_reset_at: Double?
    let week_reset_at: Double?
}

private struct TodaySnapshot: Decodable {
    let cost_usd: Double?
    let tokens: Int?
    let requests: Int?
    let cache_hit_rate: Double?
}

private struct Snapshot: Decodable {
    let schema: Int
    let updated_at: Double
    let request: RequestSnapshot?
    let quota: QuotaSnapshot
    let today: TodaySnapshot?

    static func read() -> Snapshot? {
        guard let account = getpwuid(getuid()) else { return nil }
        let home = String(cString: account.pointee.pw_dir)
        let path = home + "/Library/Application Support/Codexio/widget_snapshot.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)), data.count <= 32_768,
              let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data), snapshot.schema == 1 else {
            return nil
        }
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
                            policy: .after(now.addingTimeInterval(interval))))
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
    var parts = [request.model.isEmpty ? "等待模型调用" : request.model]
    if let effort = request.reasoning_effort, !effort.isEmpty { parts.append(effort) }
    if family != .systemSmall {
        let tier = (request.service_tier ?? "").lowercased()
        if tier == "fast" || tier == "priority" { parts.append("Fast") }
        if let context = compactContext(request.model_context_window) { parts.append(context) }
    }
    return parts.joined(separator: " · ")
}

private func staticDuration(_ milliseconds: Double?) -> String {
    guard let milliseconds, milliseconds.isFinite, milliseconds >= 0 else { return "—" }
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
    @Environment(\.widgetFamily) private var family
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
    @Environment(\.widgetFamily) private var family
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
        Text(request.prompt.isEmpty ? "等待请求内容" : request.prompt)
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
                Metric(title: "费用", value: request.cost_usd.map { String(format: "$%.2f", $0) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                durationMetric(request)
                Metric(title: "总 Token", value: compactNumber(request.input_tokens + request.output_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: "命中率", value: request.cache_hit_rate.map { String(format: "%.1f%%", $0 * 100) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            HStack(alignment: .top, spacing: family == .systemLarge ? 12 : 8) {
                Metric(title: "费用", value: request.cost_usd.map { String(format: "$%.2f", $0) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                durationMetric(request)
                if family == .systemLarge {
                    Metric(title: "命中率", value: request.cache_hit_rate.map { String(format: "%.1f%%", $0 * 100) } ?? "—")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        if family == .systemLarge {
            Divider().padding(.vertical, 9)
            HStack(alignment: .top, spacing: 12) {
                Metric(title: "输入 Token", value: compactNumber(request.input_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: "输出 Token", value: compactNumber(request.output_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: "缓存读取", value: compactNumber(request.cached_input_tokens))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider().padding(.vertical, 9)
            HStack(alignment: .top, spacing: 8) {
                Metric(title: "今日费用", value: today?.cost_usd.map { String(format: "$%.2f", $0) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: "今日 Token", value: today?.tokens.map(compactNumber) ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: "今日请求数", value: today?.requests.map(compactNumber) ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Metric(title: "今日命中率", value: today?.cache_hit_rate.map { String(format: "%.1f%%", $0 * 100) } ?? "—")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        if quota.applicable != false {
            Divider().padding(.vertical, family == .systemLarge ? 9 : 7)
            let showFiveHour = quota.has_five_hour ?? (quota.five_hour != nil)
            let showWeek = quota.has_week ?? (quota.week != nil)
            if family == .systemSmall {
                if showFiveHour {
                    QuotaLine(title: "5 小时", remaining: quota.five_hour,
                              resetAt: quota.five_hour_reset_at, timeOnly: true)
                } else {
                    QuotaLine(title: "周", remaining: quota.week, resetAt: quota.week_reset_at)
                }
            } else if showFiveHour && showWeek {
                GeometryReader { geometry in
                    HStack(spacing: 0) {
                        QuotaLine(title: "5 小时", remaining: quota.five_hour,
                                  resetAt: quota.five_hour_reset_at, timeOnly: true)
                            .frame(width: geometry.size.width / 2 - 12)
                        Color.clear.frame(width: 12)
                        QuotaLine(title: "周", remaining: quota.week, resetAt: quota.week_reset_at)
                            .frame(width: geometry.size.width / 2)
                    }
                }
                .frame(height: 24)
            } else if showFiveHour {
                QuotaLine(title: "5 小时", remaining: quota.five_hour,
                          resetAt: quota.five_hour_reset_at, timeOnly: true)
            } else {
                QuotaLine(title: "周", remaining: quota.week, resetAt: quota.week_reset_at)
            }
        }
        Spacer(minLength: 0)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let snapshot = entry.snapshot, let request = snapshot.request {
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
        .containerBackground(for: .widget) {
            Color(nsColor: .controlBackgroundColor)
        }
    }
}

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

@main
struct CodexioWidgetBundle: WidgetBundle {
    var body: some Widget {
        CodexioRequestWidget()
    }
}
