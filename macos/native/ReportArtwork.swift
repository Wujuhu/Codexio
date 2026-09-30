// Adapted from user-provided report-cards-234-source.zip.
import AppKit
import ImageIO
import SwiftUI

enum UsageReportStyle: String, CaseIterable, Identifiable {
    case bookmark = "bookmark"
    case garden = "garden"
    case afternoon = "afternoon"

    var id: String { rawValue }
    var fileStem: String {
        switch self {
        case .bookmark: "design-2"
        case .garden: "design-3"
        case .afternoon: "design-4"
        }
    }
    var height: CGFloat {
        980
    }
    var width: CGFloat {
        500
    }
    var palette: ReportArtCardPalette {
        switch self {
        case .bookmark:
            ReportArtCardPalette(paper: Color(hex: 0xFFFBF4), ink: Color(hex: 0x4E4032), muted: Color(hex: 0x78634D), accent: Color(hex: 0xC99B65), secondary: Color(hex: 0xE8D7AF), warm: Color(hex: 0xEBAF86), surface: Color(hex: 0xF7EFDD))
        case .garden:
            ReportArtCardPalette(paper: Color(hex: 0xFFFDF7), ink: Color(hex: 0x173C31), muted: Color(hex: 0x4C6959), accent: Color(hex: 0x7CA88B), secondary: Color(hex: 0xC7DCC8), warm: Color(hex: 0xF1BAA1), surface: Color(hex: 0xF1F7EE))
        case .afternoon:
            ReportArtCardPalette(paper: Color(hex: 0xFFFCF7), ink: Color(hex: 0x493027), muted: Color(hex: 0x785746), accent: Color(hex: 0xDA855A), secondary: Color(hex: 0xF2C5AA), warm: Color(hex: 0xD67650), surface: Color(hex: 0xFEF1E8))
        }
    }
}

extension UsageReportData {
    var cardHeight: CGFloat {
        max(820, 952 - CGFloat(max(0, 3-min(3, models.count)))*50)
    }
}

struct ReportArtCardPalette {
    let paper: Color
    let ink: Color
    let muted: Color
    let accent: Color
    let secondary: Color
    let warm: Color
    let surface: Color
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255, blue: Double(hex & 0xff) / 255)
    }
}

func reportEditorial(_ size: CGFloat) -> Font { .custom("Songti SC", fixedSize: size) }

struct ReportArtCatCardShape: Shape {
    func path(in rect: CGRect) -> Path {
        let w = rect.width, h = rect.height
        var p = Path()
        p.move(to: CGPoint(x: 18, y: 80))
        p.addCurve(to: CGPoint(x: 42, y: 19), control1: CGPoint(x: 16, y: 58), control2: CGPoint(x: 28, y: 10))
        p.addCurve(to: CGPoint(x: 123, y: 75), control1: CGPoint(x: 57, y: 11), control2: CGPoint(x: 96, y: 67))
        p.addQuadCurve(to: CGPoint(x: w - 123, y: 75), control: CGPoint(x: w / 2, y: 53))
        p.addCurve(to: CGPoint(x: w - 42, y: 19), control1: CGPoint(x: w - 96, y: 67), control2: CGPoint(x: w - 57, y: 11))
        p.addCurve(to: CGPoint(x: w - 18, y: 80), control1: CGPoint(x: w - 28, y: 10), control2: CGPoint(x: w - 16, y: 58))
        p.addLine(to: CGPoint(x: w - 18, y: h - 62))
        p.addQuadCurve(to: CGPoint(x: w - 72, y: h - 18), control: CGPoint(x: w - 20, y: h - 18))
        p.addLine(to: CGPoint(x: 72, y: h - 18))
        p.addQuadCurve(to: CGPoint(x: 18, y: h - 62), control: CGPoint(x: 20, y: h - 18))
        p.closeSubpath()
        return p
    }
}

struct ReportArtEarInsets: Shape {
    func path(in rect: CGRect) -> Path {
        let w = rect.width
        var p = Path()
        p.move(to: CGPoint(x: 37, y: 70))
        p.addQuadCurve(to: CGPoint(x: 48, y: 34), control: CGPoint(x: 39, y: 40))
        p.addQuadCurve(to: CGPoint(x: 93, y: 72), control: CGPoint(x: 63, y: 36))
        p.closeSubpath()
        p.move(to: CGPoint(x: w - 37, y: 70))
        p.addQuadCurve(to: CGPoint(x: w - 48, y: 34), control: CGPoint(x: w - 39, y: 40))
        p.addQuadCurve(to: CGPoint(x: w - 93, y: 72), control: CGPoint(x: w - 63, y: 36))
        p.closeSubpath()
        return p
    }
}

struct ReportArtTimeTail: Shape {
    func path(in rect: CGRect) -> Path {
        let w = rect.width, h = rect.height
        var p = Path()
        p.move(to: CGPoint(x: w * 0.38, y: 10))
        p.addCurve(to: CGPoint(x: w * 0.54, y: h * 0.58), control1: CGPoint(x: w, y: h * 0.22), control2: CGPoint(x: w * 0.74, y: h * 0.39))
        p.addCurve(to: CGPoint(x: w * 0.69, y: h - 17), control1: CGPoint(x: w * 0.24, y: h * 0.76), control2: CGPoint(x: w * 0.26, y: h - 5))
        p.addQuadCurve(to: CGPoint(x: w * 0.75, y: h - 34), control: CGPoint(x: w * 0.95, y: h - 16))
        return p
    }
}

struct ReportArtTailLine: Shape {
    func path(in rect: CGRect) -> Path {
        let w = rect.width, h = rect.height
        var p = Path()
        p.move(to: CGPoint(x: w - 43, y: h * 0.19))
        p.addCurve(to: CGPoint(x: w - 34, y: h * 0.58), control1: CGPoint(x: w - 15, y: h * 0.29), control2: CGPoint(x: w - 80, y: h * 0.43))
        p.addCurve(to: CGPoint(x: w - 59, y: h - 70), control1: CGPoint(x: w - 10, y: h * 0.72), control2: CGPoint(x: w - 12, y: h - 38))
        p.addCurve(to: CGPoint(x: w - 77, y: h - 55), control1: CGPoint(x: w - 101, y: h - 99), control2: CGPoint(x: w - 106, y: h - 50))
        return p
    }
}

struct UsageReportCard: View {
    let style: UsageReportStyle
    let data: UsageReportData
    private var palette: ReportArtCardPalette { style.palette }

    var body: some View {
        ZStack {
            ReportArtCatCardShape().fill(palette.paper)
            ReportArtEarInsets().fill(palette.warm.opacity(style == .bookmark ? 0.11 : 0.28))
            ReportArtCatCardShape().stroke(palette.accent.opacity(0.15), lineWidth: 1)
            if style == .bookmark {
                ReportArtTailLine().stroke(palette.ink.opacity(0.72), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .padding(.trailing, 10)
                    .allowsHitTesting(false)
            }
            cardContent
                .padding(.horizontal, 42)
                .padding(.top, 76)
                .padding(.bottom, 42)
        }
        .frame(width: style.width, height: data.cardHeight)
        .shadow(color: palette.ink.opacity(0.14), radius: 23, y: 12)
        .accessibilityElement(children: .contain)
    }

    private var cardContent: some View {
        ReportArtCompactReport(data: data, style: style, palette: palette)
    }
}

private struct ReportArtCompactReport: View {
    let data: UsageReportData
    let style: UsageReportStyle
    let palette: ReportArtCardPalette

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ReportArtBrandHeader(data: data, palette: palette, centered: false)
            ReportArtFineRule(color: palette.accent).padding(.top, 16)
            hero.padding(.top, 10)
            ReportArtFineRule(color: palette.accent).padding(.top, 10)
            metrics.padding(.vertical, 18)
            ReportArtFineRule(color: palette.accent)
            ReportArtTimeRhythm(data: data, palette: palette).padding(.top, 20)
            ReportArtFineRule(color: palette.accent).padding(.top, 20)
            ReportArtModelRows(data: data, palette: palette).padding(.top, 18)
            Spacer(minLength: 20)
            ReportArtCatInsight(text: data.activityInsight, palette: palette)
            Text(L("专注 · 与 AI 共成长", "Focus · Grow with AI"))
                .font(.system(size: 10)).tracking(3).foregroundStyle(palette.muted)
                .frame(maxWidth: .infinity).padding(.top, 14)
        }
    }

    private var hero: some View {
        ZStack(alignment: .bottomTrailing) {
            VStack(alignment: .leading, spacing: 7) {
                Text(style == .bookmark ? data.modelHeadline : data.activityHeadline)
                    .font(reportEditorial(43)).fontWeight(.semibold)
                    .lineSpacing(1).lineLimit(2).minimumScaleFactor(0.72)
                if let peak = data.peakTimeSlice {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        ReportArtNumber(value: "\(peak.requests) / \(data.requests)")
                            .font(reportEditorial(32)).fontWeight(.semibold)
                        Text(L("次请求", "requests")).font(.system(size: 12))
                    }
                    Text(peak.name + " · " + "\(data.percent(peak.requests, of: data.requests))%")
                        .font(.system(size: 14, weight: .medium)).foregroundStyle(palette.accent)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            reportResourceImage(style.catAsset).resizable().scaledToFit()
                .frame(width: 100, height: 75)
                .padding(.trailing, 3)
        }
        .foregroundStyle(palette.ink)
        .frame(height: 150)
    }

    private var metrics: some View {
        HStack(spacing: 0) {
            ReportArtSmallMetric(value: data.costLabel, label: L("费用", "Cost"), palette: palette)
            divider
            ReportArtSmallMetric(value: data.tokenSummary, label: L("总 Token", "Total tokens"), palette: palette)
            divider
            ReportArtSmallMetric(value: "\(data.requests)", label: L("用户请求", "Requests"), palette: palette)
            divider
            ReportArtSmallMetric(value: data.cacheLabel, label: L("命中率", "Cache hit"), palette: palette)
        }
    }

    private var divider: some View {
        Rectangle().fill(palette.secondary.opacity(0.65)).frame(width: 1, height: 35)
    }
}

private struct ReportArtTimeRhythm: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L("使用节奏", "Usage rhythm")).font(reportEditorial(25)).fontWeight(.semibold)
                Spacer()
                if let peak = data.peakTimeSlice {
                    Text(peak.name + L("最活跃", " was busiest"))
                        .font(.system(size: 11, weight: .medium)).foregroundStyle(palette.accent)
                }
            }
            HStack(alignment: .bottom, spacing: 14) {
                ForEach(data.timeSlices) { slice in
                    VStack(spacing: 3) {
                        ReportArtNumber(value: "\(slice.requests)").font(reportEditorial(12)).fontWeight(.semibold)
                        RoundedRectangle(cornerRadius: 5)
                            .fill(slice.requests == data.peakTimeSlice?.requests ? palette.accent : palette.secondary)
                            .frame(height: slice.requests == 0 ? 1 : max(3, CGFloat(slice.requests) / CGFloat(max(1, data.peakTimeSlice?.requests ?? 1)) * 52))
                        Text(slice.name).font(.system(size: 11)).foregroundStyle(palette.muted)
                    }.frame(maxWidth: .infinity)
                }
            }.frame(height: 82, alignment: .bottom)
            HStack(alignment: .top, spacing: 10) {
                timeNote(data.firstRequest, first: true)
                Spacer(minLength: 0)
                timeNote(data.lastRequest, first: false)
            }
        }.foregroundStyle(palette.ink)
    }

    private func timeNote(_ date: Date?, first: Bool) -> some View {
        let hour = date.map { Calendar.current.component(.hour, from: $0) }
        let message = first
            ? ((hour ?? 12) >= 5 && (hour ?? 12) < 9 ? L("早早开工，继续加油喵", "An early start—keep it up") : L("按自己的节奏来，喵", "Keep your own pace"))
            : ((hour ?? 12) >= 22 || (hour ?? 12) < 5 ? L("辛苦啦，早点休息喵", "Rest a little earlier") : L("努力收好，好好放松喵", "Time to unwind"))
        return VStack(alignment: first ? .leading : .trailing, spacing: 1) {
            Text((first ? L("最早", "First") : L("最晚", "Last")) + " · " + (date.map { $0.formatted(.dateTime.hour().minute()) } ?? "—"))
                .font(.system(size: 10, weight: .medium)).monospacedDigit()
            if date != nil { Text(message).font(.system(size: 10)).foregroundStyle(palette.muted).lineLimit(1) }
        }
    }
}

private struct ReportArtModelRows: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(L("模型使用", "Model usage")).font(reportEditorial(26)).fontWeight(.semibold)
                Spacer()
                Text("\(data.modelCalls) " + L("次调用", "calls"))
                    .font(.system(size: 11)).foregroundStyle(palette.muted)
            }
            if data.models.isEmpty { Text(L("暂无模型调用", "No model calls")).font(.system(size: 10)).foregroundStyle(palette.muted) }
            ForEach(Array(data.models.prefix(3))) { model in
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(modelName(model.name)).font(.system(size: 15, weight: .medium)).lineLimit(1)
                        Text(model.costLabel + " · " + reportTokenLabel(model.tokens) + " Token")
                            .font(.system(size: 12)).foregroundStyle(palette.muted).lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    ReportArtNumber(value: "\(model.calls) " + L("次", "calls") + " · \(data.percent(model.calls, of: data.modelCalls))%")
                        .font(.system(size: 13))
                }
            }
        }.foregroundStyle(palette.ink)
    }
}

struct ReportArtBrandHeader: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette
    let centered: Bool

    var body: some View {
        HStack(spacing: 10) {
            reportResourceImage("brand-mark")
                .resizable().scaledToFit().frame(width: 36, height: 36)
            VStack(alignment: .leading, spacing: 1) {
                Text("Codexio").font(reportEditorial(27)).fontWeight(.semibold)
                Text(data.title).font(.system(size: 12, weight: .medium)).tracking(3)
                    .foregroundStyle(palette.muted)
            }
            Spacer(minLength: 5)
            if !centered {
                Text(data.dateLabel).font(.system(size: 12)).foregroundStyle(palette.muted)
            }
        }
        .foregroundStyle(palette.ink)
    }
}

struct ReportArtFineRule: View {
    let color: Color
    var body: some View { Rectangle().fill(color.opacity(0.4)).frame(height: 1) }
}

struct ReportArtPercentBar: View {
    let fraction: CGFloat
    let color: Color
    let track: Color
    let height: CGFloat
    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(track)
                Capsule().fill(color).frame(width: max(0, min(geometry.size.width, geometry.size.width * fraction)))
            }
        }.frame(height: height)
    }
}

struct ReportArtProjectRows: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette
    let showBars: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(L("项目使用情况", "Project usage")).font(reportEditorial(25)).fontWeight(.semibold)
                Spacer()
                EmptyView()
            }
            ForEach(Array(data.projects.enumerated()), id: \.element.id) { index, project in
                HStack(spacing: 10) {
                    Circle().fill(projectColor(index)).frame(width: 10, height: 10)
                    Text(project.name).font(.system(size: 15, weight: .medium))
                        .lineLimit(1).frame(width: 92, alignment: .leading)
                    if showBars {
                        ReportArtPercentBar(fraction: CGFloat(project.requests) / CGFloat(max(data.requests, 1)), color: projectColor(index), track: palette.secondary.opacity(0.25), height: 8)
                    } else { Spacer(minLength: 4) }
                    Text("\(project.requests) " + L("次", "requests")).font(.system(size: 14, weight: .medium)).monospacedDigit()
                        .frame(width: 43, alignment: .trailing)
                    Text("\(data.percent(project.requests, of: data.requests))%")
                        .font(.system(size: 12)).foregroundStyle(palette.muted).monospacedDigit()
                        .frame(width: 34, alignment: .trailing)
                }
                .padding(.vertical, 6)
                if index < data.projects.count - 1 { ReportArtFineRule(color: palette.secondary).padding(.leading, 20) }
            }
        }
        .foregroundStyle(palette.ink)
    }

    private func projectColor(_ index: Int) -> Color {
        switch index {
        case 0: palette.accent
        case 1: palette.warm
        default: palette.secondary
        }
    }
}

struct ReportArtModelSpotlight: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette
    var body: some View {
        if let top = data.topModel {
            HStack(alignment: .center, spacing: 12) {
                Image(systemName: "sparkle").font(.system(size: 24)).foregroundStyle(palette.accent)
                VStack(alignment: .leading, spacing: 5) {
                    Text(L("最常用模型", "Most-used model")).font(reportEditorial(21)).fontWeight(.semibold)
                    Text(top.name).font(.system(size: 18, weight: .semibold, design: .rounded))
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    Text("\(top.calls) / \(data.modelCalls) " + L("次", "calls"))
                        .font(.system(size: 16, weight: .medium)).monospacedDigit()
                    Text(L("按模型调用计", "Model calls") + " · \(data.percent(top.calls, of: data.modelCalls))%")
                        .font(.system(size: 11)).foregroundStyle(palette.muted)
                }
            }
            .foregroundStyle(palette.ink)
        }
    }
}

struct ReportArtTinyCacheNote: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "square.stack.3d.up").font(.system(size: 12))
            Text(L("缓存命中率 ", "Cache hit rate ") + data.cacheLabel)
        }
        .font(.system(size: 11))
        .foregroundStyle(palette.muted)
    }
}

struct ReportArtCatInsight: View {
    let text: String
    let palette: ReportArtCardPalette
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "pawprint.fill").font(.system(size: 18)).foregroundStyle(palette.warm)
            Text(L("小猫发现：", "Cat note: ") + text).font(.system(size: 13)).foregroundStyle(palette.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(palette.surface, in: RoundedRectangle(cornerRadius: 18))
    }
}

struct ReportArtSmallMetric: View {
    let value: String
    let label: String
    let palette: ReportArtCardPalette
    var body: some View {
        VStack(spacing: 4) {
            ReportArtNumber(value: value).font(reportEditorial(27)).fontWeight(.semibold).lineLimit(1).minimumScaleFactor(0.65)
            Text(label).font(.system(size: 11)).foregroundStyle(palette.muted)
        }
        .foregroundStyle(palette.ink)
        .frame(maxWidth: .infinity)
    }
}

func reportResourceImage(_ name: String) -> Image {
    ReportArtworkResources.image(name)
}

enum ReportArtworkResources {
    private static let lock = NSLock()
    private static var images: [String: NSImage] = [:]
    // Only the currently visible card is decoded. The original artwork is much
    // larger than its 2x rendering size, so downsampling avoids a permanent
    // full-resolution CGImage cache.
    static func prepare(style: UsageReportStyle) {
        var loaded: [String: NSImage] = [:]
        for (name, pixels) in [("brand-mark", 128), (style.catAsset, 384)] {
            guard let url = Bundle.main.url(forResource: name, withExtension: "png", subdirectory: "ReportCards"),
                  let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { continue }
            let options: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: pixels,
                kCGImageSourceShouldCacheImmediately: true
            ]
            guard let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { continue }
            loaded[name] = NSImage(cgImage: thumbnail, size: NSSize(width: thumbnail.width, height: thumbnail.height))
        }
        lock.lock(); images = loaded; lock.unlock()
    }
    static func release() {
        lock.lock(); images.removeAll(keepingCapacity: false); lock.unlock()
    }
    static func image(_ name: String) -> Image {
        lock.lock(); let image = images[name]; lock.unlock()
        return image.map { Image(nsImage: $0) } ?? Image(systemName: name == "brand-mark" ? "c.circle.fill" : "cat")
    }
}

// Live cards retain their fade-in; numbers switch directly to avoid retaining
// glyph bitmaps for every intermediate numeric-text transition.
private struct ReportArtNumbersVisibleKey: EnvironmentKey { static let defaultValue = true }
extension EnvironmentValues {
    var reportArtNumbersVisible: Bool {
        get { self[ReportArtNumbersVisibleKey.self] }
        set { self[ReportArtNumbersVisibleKey.self] = newValue }
    }
}
struct ReportArtNumber: View {
    let value: String
    @Environment(\.reportArtNumbersVisible) private var visible
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        Text(visible || reduceMotion ? value : "0").monospacedDigit().contentTransition(.identity)
    }
}
