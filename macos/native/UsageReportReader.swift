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
        HStack(spacing: 0) {
            if let data = model.documents[model.period] {
                GeometryReader { geometry in
                    let widthScale = (geometry.size.width-24)/model.style.width
                    let heightScale = (geometry.size.height-12)/data.cardHeight
                    let scale = max(0.38, min(0.92, min(widthScale, heightScale)))
                    ScrollViewReader { proxy in
                        ScrollView(.vertical) {
                            UsageReportLiveCard(data: data, style: model.style)
                                .frame(width: model.style.width, height: data.cardHeight)
                                .scaleEffect(scale, anchor: .topLeading)
                                .frame(width: model.style.width*scale, height: data.cardHeight*scale, alignment: .topLeading)
                                .id("report-top")
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 6)
                        }
                        .onScrollGeometryChange(for: Bool.self) { value in value.visibleRect.maxY >= value.contentSize.height-8 } action: { _, value in atBottom = value }
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
            VStack(spacing: 14) {
                Button { model.onClose?() } label: {
                    Image(systemName: "xmark").font(.system(size: 12, weight: .medium)).frame(width: 30, height: 30)
                }.buttonStyle(.plain).accessibilityLabel(L("关闭报告", "Close report"))
                    .frame(maxWidth: .infinity, alignment: .trailing)
                VStack(spacing: 5) {
                    ForEach(UsageReportPeriod.allCases) { period in
                        Button {
                            withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.25)) { model.period = period }
                        } label: {
                            Text(period.tabTitle).font(.system(size: 13, weight: .medium))
                                .foregroundStyle(model.period == period ? palette.paper : palette.ink)
                                .frame(maxWidth: .infinity).frame(height: 36)
                                .background(model.period == period ? palette.ink : .clear, in: RoundedRectangle(cornerRadius: 12))
                                .contentShape(RoundedRectangle(cornerRadius: 12))
                        }
                        .buttonStyle(.plain).accessibilityAddTraits(model.period == period ? .isSelected : [])
                    }
                }.padding(4).background(palette.secondary.opacity(0.35), in: RoundedRectangle(cornerRadius: 16))
                Menu {
                    Picker(L("报告样式", "Report style"), selection: Binding(get: { model.style }, set: { model.onStyle?($0) })) { ForEach(UsageReportStyle.allCases) { Text($0.title).tag($0) } }
                } label: {
                    Label(L("样式", "Style"), systemImage: "paintpalette").font(.system(size: 12))
                }
                .menuStyle(.borderlessButton).fixedSize()
                Button { model.onRefresh?() } label: {
                    Label(L("重新整理报告", "Refresh report"), systemImage: "arrow.clockwise")
                        .font(.system(size: 11)).frame(maxWidth: .infinity).frame(height: 28)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(model.busy)
                .help(L("重新整理报告", "Refresh report"))
                Spacer(minLength: 0)
                if !atBottom, model.documents[model.period] != nil {
                    reportResourceImage(model.style.catAsset).resizable().scaledToFit().frame(width: 52, height: 34)
                    Text(L("下滑看完整报告", "Scroll for more")).font(.system(size: 10)).multilineTextAlignment(.center)
                }
                ReportNativeShareButton(title: L("分享", "Share"), action: model.onShare)
                    .frame(width: 102, height: 31).disabled(model.documents[model.period] == nil)
                Text(L("把小进展分享出去，喵", "Share your little progress"))
                    .font(.system(size: 10)).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
            }
            .frame(width: 96)
            .padding(.leading, 6).padding(.trailing, 14).padding(.vertical, 14)
            if let error = model.error, !model.documents.isEmpty {
                Text(error).font(.caption).foregroundStyle(.red).lineLimit(2).frame(width: 100)
            }
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
