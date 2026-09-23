import AppKit
import Darwin
import Foundation
import SwiftUI
import WidgetKit

private let widgetKind = "com.wujuhu.codexio.request"

private struct RequestSnapshot: Decodable {
    let prompt: String
    let model: String
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
    let five_hour: Double?
    let week: Double?
}

private struct Snapshot: Decodable {
    let schema: Int
    let updated_at: Double
    let request: RequestSnapshot?
    let quota: QuotaSnapshot

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

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text(title).foregroundStyle(.secondary)
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
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 10)).foregroundStyle(.secondary)
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

    @ViewBuilder
    private func requestBody(_ request: RequestSnapshot, quota: QuotaSnapshot) -> some View {
        Text(request.prompt.isEmpty ? "等待请求内容" : request.prompt)
            .font(.system(size: family == .systemSmall ? 13 : 15, weight: .semibold))
            .lineLimit(family == .systemSmall ? 2 : 3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .privacySensitive()
        Text(request.model.isEmpty ? "等待模型调用" : request.model)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(.secondary)
            .lineLimit(1)
        HStack(alignment: .top, spacing: 12) {
            Metric(title: "估算费用", value: request.cost_usd.map { String(format: "$%.2f", $0) } ?? "—")
            VStack(alignment: .leading, spacing: 2) {
                Text("耗时").font(.system(size: 10)).foregroundStyle(.secondary)
                duration(request).font(.system(size: 13, weight: .semibold, design: .rounded))
                    .lineLimit(1).minimumScaleFactor(0.75)
            }
            Spacer(minLength: 0)
        }
        Spacer(minLength: family == .systemSmall ? 0 : 4)
        if family == .systemSmall {
            if let fiveHour = quota.five_hour {
                QuotaLine(title: "5 小时额度", remaining: fiveHour)
            } else {
                QuotaLine(title: "周额度", remaining: quota.week)
            }
        } else {
            HStack(spacing: 16) {
                QuotaLine(title: "5 小时额度", remaining: quota.five_hour)
                QuotaLine(title: "周额度", remaining: quota.week)
            }
        }
        if family == .systemLarge {
            Divider().padding(.vertical, 4)
            HStack(spacing: 12) {
                Metric(title: "输入 Token", value: compactNumber(request.input_tokens))
                Metric(title: "输出 Token", value: compactNumber(request.output_tokens))
            }
            HStack(spacing: 12) {
                Metric(title: "缓存读取", value: compactNumber(request.cached_input_tokens))
                Metric(title: "命中率", value: request.cache_hit_rate.map { String(format: "%.1f%%", $0 * 100) } ?? "—")
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: family == .systemSmall ? 6 : 9) {
            HStack(spacing: 6) {
                Image(systemName: "chevron.left.forwardslash.chevron.right")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.tint)
                Text("Codexio").font(.system(size: 12, weight: .semibold))
                Spacer()
            }
            if let snapshot = entry.snapshot, let request = snapshot.request {
                let fresh = Date().timeIntervalSince1970 - snapshot.updated_at < 900
                requestBody(request, quota: fresh ? snapshot.quota : QuotaSnapshot(five_hour: nil, week: nil))
            } else {
                Spacer()
                Text("打开 Codexio 查看最近请求")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Spacer()
            }
        }
        .padding(family == .systemSmall ? 13 : 17)
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
        .description("查看最近请求、估算费用与剩余额度")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

@main
struct CodexioWidgetBundle: WidgetBundle {
    var body: some Widget {
        CodexioRequestWidget()
    }
}
