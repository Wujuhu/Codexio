// Adapted from user-provided report-cards-234-source.zip.
import AppKit
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
        switch self {
        case .bookmark: 1190
        case .garden: 1160
        case .afternoon: 1190
        }
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
                .padding(.horizontal, 55)
                .padding(.top, 94)
                .padding(.bottom, 65)
        }
        .frame(width: 560, height: style.height)
        .shadow(color: palette.ink.opacity(0.14), radius: 23, y: 12)
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder private var cardContent: some View {
        switch style {
        case .bookmark: ReportArtBookmarkReport(data: data, palette: palette)
        case .garden: ReportArtGardenReport(data: data, palette: palette)
        case .afternoon: ReportArtAfternoonReport(data: data, palette: palette)
        }
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
            Text(value).font(reportEditorial(27)).fontWeight(.semibold).monospacedDigit()
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
    // Called by report preparation on AppState.dataQueue, before presentation.
    static func prepare() {
        for name in ["brand-mark", "cat-bookmark", "cat-garden", "cat-afternoon"] {
            lock.lock(); let loaded = images[name] != nil; lock.unlock()
            if loaded { continue }
            guard let url = Bundle.main.url(forResource: name, withExtension: "png", subdirectory: "ReportCards"), let image = NSImage(contentsOf: url) else { continue }
            lock.lock(); images[name] = image; lock.unlock()
        }
    }
    static func image(_ name: String) -> Image {
        lock.lock(); let image = images[name]; lock.unlock()
        return image.map { Image(nsImage: $0) } ?? Image(systemName: "cat")
    }
}
