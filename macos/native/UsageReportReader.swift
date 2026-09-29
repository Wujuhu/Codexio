import AppKit
import SwiftUI
import Combine

extension UsageReportStyle {
    var title: String { switch self { case .bookmark: L("奶油猫尾书签", "Cream bookmark"); case .garden: L("薄荷猫咪花园", "Mint garden"); case .afternoon: L("杏色时段图", "Apricot afternoon") } }
    var catAsset: String { switch self { case .bookmark: "cat-bookmark"; case .garden: "cat-garden"; case .afternoon: "cat-afternoon" } }
}

final class UsageReportViewModel: ObservableObject {
    @Published var documents: [UsageReportPeriod: UsageReportData] = [:]
    @Published var period: UsageReportPeriod = .day
    @Published var style: UsageReportStyle = .garden
    @Published var busy = false
    @Published var error: String?
    var onClose: (() -> Void)?
    var onRefresh: (() -> Void)?
    var onStyle: ((UsageReportStyle) -> Void)?
    var onShare: ((NSView) -> Void)?
}

struct UsageReportReader: View {
    @ObservedObject var model: UsageReportViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var atBottom = false
    private var palette: ReportArtCardPalette { model.style.palette }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Menu {
                    Picker(L("报告样式", "Report style"), selection: Binding(get: { model.style }, set: { model.onStyle?($0) })) { ForEach(UsageReportStyle.allCases) { Text($0.title).tag($0) } }
                    Button(L("重新整理报告", "Refresh report")) { model.onRefresh?() }.disabled(model.busy)
                } label: { Image(systemName: "paintpalette").font(.system(size: 14)) }
                    .menuStyle(.borderlessButton).fixedSize().help(L("报告样式", "Report style"))
                Spacer(minLength: 10)
                HStack(spacing: 3) {
                    ForEach(UsageReportPeriod.allCases) { period in
                        Button {
                            withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.25)) { model.period = period }
                        } label: {
                            Text(period.tabTitle).font(.system(size: 12, weight: .medium))
                                .foregroundStyle(model.period == period ? palette.paper : palette.ink)
                                .padding(.horizontal, 15).padding(.vertical, 6)
                                .background(model.period == period ? palette.ink : .clear, in: Capsule())
                        }.buttonStyle(.plain).accessibilityAddTraits(model.period == period ? .isSelected : [])
                    }
                }.padding(3).background(palette.secondary.opacity(0.35), in: Capsule())
                Spacer(minLength: 10)
                Button { model.onClose?() } label: { Image(systemName: "xmark").font(.system(size: 12, weight: .medium)).frame(width: 25, height: 25) }
                    .buttonStyle(.plain).accessibilityLabel(L("关闭报告", "Close report"))
            }.padding(.horizontal, 24).padding(.top, 10).padding(.bottom, 3)
            if let data = model.documents[model.period] {
                GeometryReader { geometry in
                    let widthScale = (geometry.size.width-44)/model.style.width
                    let heightScale = (geometry.size.height-82)/model.style.height
                    let scale = max(0.38, min(0.92, min(widthScale, heightScale)))
                    let needsScroll = model.style.height*scale+82 > geometry.size.height+1
                    ScrollViewReader { proxy in
                        ZStack(alignment: .bottom) {
                            ScrollView(.vertical) {
                                VStack(spacing: 10) {
                                    UsageReportLiveCard(data: data, style: model.style)
                                        .frame(width: model.style.width, height: model.style.height)
                                        .scaleEffect(scale, anchor: .topLeading)
                                        .frame(width: model.style.width*scale, height: model.style.height*scale, alignment: .topLeading)
                                        .id("report-top")
                                    VStack(spacing: 8) {
                                        ReportNativeShareButton(title: L("分享报告", "Share report"), action: model.onShare).frame(width: 118, height: 32)
                                        Text(L("把小进展分享出去，喵", "Share your little progress")).font(.system(size: 11, weight: .medium))
                                    }.padding(.bottom, 12)
                                }.padding(.top, 5).frame(maxWidth: .infinity)
                            }
                            .onScrollGeometryChange(for: Bool.self) { value in value.visibleRect.maxY >= value.contentSize.height-8 } action: { _, value in atBottom = value }
                            if needsScroll && !atBottom {
                                HStack(spacing: 9) {
                                    reportResourceImage(model.style.catAsset).resizable().scaledToFit().frame(width: 46, height: 29)
                                    Text(L("往下滑，看看完整的小记录喵", "Scroll down for your full little story")).font(.system(size: 12, weight: .medium))
                                    Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                                }
                                .padding(.horizontal, 16).padding(.vertical, 5)
                                .background(palette.surface.opacity(0.96), in: Capsule())
                                .padding(.bottom, 4)
                            }
                        }
                        .onChange(of: model.period) { _, _ in atBottom = false; proxy.scrollTo("report-top", anchor: .top) }
                        .onChange(of: model.style) { _, _ in atBottom = false; proxy.scrollTo("report-top", anchor: .top) }
                    }
                }
            } else {
                VStack(spacing: 12) {
                    if model.busy { ProgressView(); Text(L("小猫正在整理报告…", "Your report is being prepared…")) }
                    else { Text(model.error ?? L("暂无报告数据", "No report data")) }
                }.font(.system(size: 13)).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if let error = model.error, !model.documents.isEmpty { Text(error).font(.caption).foregroundStyle(.red).lineLimit(2).padding(.horizontal, 20) }
        }
        .foregroundStyle(palette.ink).background(palette.surface)
        .preferredColorScheme(.light)
    }
}

private struct UsageReportLiveCard: View {
    let data: UsageReportData
    let style: UsageReportStyle
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false
    var body: some View {
        UsageReportCard(style: style, data: data)
            .environment(\.reportArtNumbersVisible, appeared || reduceMotion)
            .opacity(appeared || reduceMotion ? 1 : 0)
            .onAppear { withAnimation(reduceMotion ? nil : .easeOut(duration: 0.4)) { appeared = true } }
    }
}

private struct ReportNativeShareButton: NSViewRepresentable {
    var title: String
    var action: ((NSView) -> Void)?
    final class Coordinator: NSObject { var action: ((NSView) -> Void)?; @objc func clicked(_ sender: NSButton) { action?(sender) } }
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context: Context) -> NSButton {
        let button = NSButton(title: title, target: context.coordinator, action: #selector(Coordinator.clicked(_:)))
        button.bezelStyle = .rounded; button.image = NSImage(systemSymbolName: "square.and.arrow.up", accessibilityDescription: nil); button.imagePosition = .imageLeading
        return button
    }
    func updateNSView(_ view: NSButton, context: Context) { context.coordinator.action = action; view.title = title }
}
