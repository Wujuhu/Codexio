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
    @Published var page: Int? = 0
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
    private var palette: ReportArtCardPalette { model.style.palette }
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                reportResourceImage("brand-mark").resizable().scaledToFit().frame(width: 24, height: 24)
                Text("Codexio").font(.system(size: 14, weight: .semibold))
                Spacer(minLength: 8)
                Picker("", selection: $model.period) { ForEach(UsageReportPeriod.allCases) { Text($0.tabTitle).tag($0) } }
                    .labelsHidden().pickerStyle(.segmented).frame(width: 195)
                Button { model.onClose?() } label: { Image(systemName: "xmark").font(.system(size: 11, weight: .medium)) }
                    .buttonStyle(.plain).accessibilityLabel(L("关闭报告", "Close report"))
            }.padding(.horizontal, 20).padding(.vertical, 12)
            Divider().overlay(palette.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(model.period.title).font(.system(size: 21, weight: .semibold, design: .rounded))
                if let data = model.documents[model.period] { Text(data.dateLabel).font(.system(size: 18, weight: .medium, design: .rounded)).monospacedDigit() }
                Spacer(minLength: 6)
                Menu {
                    Picker(L("报告样式", "Report style"), selection: $model.style) { ForEach(UsageReportStyle.allCases) { Text($0.title).tag($0) } }
                    Button(L("重新整理报告", "Refresh report")) { model.onRefresh?() }.disabled(model.busy)
                } label: { Image(systemName: "paintpalette").font(.system(size: 14)) }.menuStyle(.borderlessButton).fixedSize()
            }.padding(.horizontal, 22).padding(.top, 14).padding(.bottom, 8)
            if let data = model.documents[model.period] {
                GeometryReader { geometry in
                    ScrollView(.vertical) {
                        LazyVStack(spacing: 0) {
                            ForEach(0..<2) { index in
                                ReportReaderPage(data: data, style: model.style, index: index, compact: geometry.size.height < 300, share: model.onShare)
                                    .frame(width: geometry.size.width, height: geometry.size.height)
                                    .id(index)
                            }
                        }.scrollTargetLayout()
                    }.scrollIndicators(.hidden).scrollTargetBehavior(.paging).scrollPosition(id: $model.page)
                }
            } else {
                VStack(spacing: 12) {
                    if model.busy { ProgressView(); Text(L("小猫正在整理报告…", "Your report is being prepared…")) }
                    else { Text(model.error ?? L("暂无报告数据", "No report data")) }
                }.font(.system(size: 13)).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if let error = model.error, !model.documents.isEmpty { Text(error).font(.caption).foregroundStyle(.red).lineLimit(2).padding(.horizontal, 20) }
            Divider().overlay(palette.secondary)
            HStack(spacing: 10) {
                if (model.page ?? 0) == 0 {
                    Button {
                        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.35)) { model.page = 1 }
                    } label: {
                        HStack(spacing: 8) {
                            reportResourceImage(model.style.catAsset).resizable().scaledToFit().frame(width: 44, height: 30)
                            Text(L("轻轻下滑，还有小发现喵", "Scroll down for more little discoveries")).font(.system(size: 12, weight: .medium))
                            Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                        }
                    }.buttonStyle(.plain)
                } else { Text(L("把这份小记录收好，喵", "Keep this little record")).font(.system(size: 12, weight: .medium)) }
                Spacer(minLength: 10)
                ForEach(0..<2) { index in
                    Button { withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.35)) { model.page = index } } label: {
                        Capsule().fill((model.page ?? 0) == index ? palette.ink : palette.accent.opacity(0.45)).frame(width: (model.page ?? 0) == index ? 18 : 7, height: 6).frame(width: 26, height: 28)
                    }.buttonStyle(.plain).accessibilityLabel(L("第", "Page ") + "\(index+1)")
                }
            }.foregroundStyle(palette.ink).padding(.horizontal, 20).padding(.vertical, 7).frame(minHeight: 43)
        }
        .foregroundStyle(palette.ink).background(palette.paper)
        .onChange(of: model.period) { _, _ in model.page = 0 }
        .onChange(of: model.style) { _, style in model.onStyle?(style) }
    }
}

private struct ReportReaderPage: View {
    let data: UsageReportData
    let style: UsageReportStyle
    let index: Int
    let compact: Bool
    let share: ((NSView) -> Void)?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false
    private var palette: ReportArtCardPalette { style.palette }
    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 9 : 15) {
            if index == 0 {
                summary
                Divider().overlay(palette.secondary)
                switch style {
                case .garden: projects
                case .bookmark: models
                case .afternoon: activity
                }
            } else {
                switch style {
                case .garden: models; Divider().overlay(palette.secondary); activity
                case .bookmark: projects; Divider().overlay(palette.secondary); activity
                case .afternoon: models; Divider().overlay(palette.secondary); projects
                }
                Spacer(minLength: 0)
                HStack {
                    Text("Codexio").font(.system(size: 12, weight: .semibold))
                    Spacer()
                    ReportNativeShareButton(title: L("分享报告", "Share report"), action: share)
                        .frame(width: 104, height: 30)
                }
            }
            if index == 0 { Spacer(minLength: 0) }
        }
        .padding(.horizontal, compact ? 20 : 24).padding(.vertical, compact ? 10 : 15)
        .opacity(appeared || reduceMotion ? 1 : 0)
        .offset(y: appeared || reduceMotion ? 0 : 7)
        .onAppear { withAnimation(reduceMotion ? nil : .easeOut(duration: 0.4)) { appeared = true } }
        .onDisappear { appeared = false }
    }
    private func heading(_ value: String) -> some View { Text(value).font(.system(size: compact ? 14 : 17, weight: .semibold, design: .rounded)).lineLimit(1) }
    private var summary: some View {
        VStack(alignment: .leading, spacing: compact ? 7 : 10) {
            HStack { heading(L("用量小结", "Usage summary")); Spacer(); Image(systemName: "pawprint.fill").foregroundStyle(palette.accent) }
            HStack(alignment: .top, spacing: 12) {
                metric(L("费用", "Cost"), data.costLabel)
                metric(L("总 Token", "Total tokens"), data.tokenSummary)
                metric(L("用户请求", "User requests"), "\(data.requests)")
                metric(L("命中率", "Cache hit rate"), data.cacheLabel)
            }
            Text(data.comparisonText).font(.system(size: compact ? 10 : 12)).foregroundStyle(palette.ink.opacity(0.85)).lineLimit(2)
        }
    }
    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 11)).foregroundStyle(palette.muted)
            Text(appeared || reduceMotion ? value : "0").font(.system(size: compact ? 19 : 24, weight: .medium, design: .rounded)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.85).contentTransition(.numericText())
                .animation(reduceMotion ? nil : .easeOut(duration: 0.45), value: value)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private var projects: some View {
        VStack(alignment: .leading, spacing: compact ? 6 : 10) {
            HStack(alignment: .center) {
                heading(data.projectHeadline.replacingOccurrences(of: "\n", with: ""))
                Spacer(minLength: 8)
                if !compact { reportResourceImage(style.catAsset).resizable().scaledToFit().frame(width: 58, height: 39) }
            }
            if data.projects.isEmpty { Text(data.emptyActivityText).font(.caption).foregroundStyle(palette.muted) }
            ForEach(Array(data.projects.prefix(compact ? 3 : 4))) { project in
                HStack(spacing: 10) {
                    Circle().fill(palette.accent).frame(width: 6, height: 6)
                    Text(project.name).lineLimit(1).help(project.id == "unassigned" || project.id == "other" ? project.name : project.id)
                    Spacer(minLength: 8)
                    Text("\(project.requests) " + L("次", "requests")).monospacedDigit()
                    Text("\(data.percent(project.requests, of: data.requests))%").foregroundStyle(palette.muted).frame(width: 35, alignment: .trailing)
                }.font(.system(size: compact ? 11 : 13))
            }
        }
    }
    private var models: some View {
        VStack(alignment: .leading, spacing: compact ? 5 : 8) {
            HStack { heading(L("模型使用分布", "Model usage")); Spacer(); Text("\(data.modelCalls) " + L("次调用", "calls")).font(.system(size: 11)).foregroundStyle(palette.muted) }
            if data.models.isEmpty { Text(L("暂无模型调用", "No model calls")).font(.caption).foregroundStyle(palette.muted) }
            ForEach(Array(data.models.prefix(3))) { model in
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(modelName(model.name)).font(.system(size: compact ? 11 : 13, weight: .medium)).lineLimit(1)
                        Text(model.costLabel + " · " + reportTokenLabel(model.tokens) + " Token").font(.system(size: compact ? 10 : 11)).foregroundStyle(palette.muted).lineLimit(1)
                    }
                    Spacer(minLength: 6)
                    Text("\(model.calls) " + L("次", "calls") + " · \(data.percent(model.calls, of: data.modelCalls))%").font(.system(size: compact ? 10 : 12)).monospacedDigit()
                }
            }
        }
    }
    private var activity: some View {
        VStack(alignment: .leading, spacing: compact ? 5 : 8) {
            heading(data.activityHeadline)
            HStack(alignment: .bottom, spacing: 14) {
                ForEach(data.timeSlices) { slice in
                    VStack(spacing: 3) {
                        Text("\(slice.requests)").font(.system(size: 10)).monospacedDigit()
                        RoundedRectangle(cornerRadius: 4).fill(palette.accent.opacity(slice.requests == data.peakTimeSlice?.requests ? 1 : 0.5))
                            .frame(height: slice.requests == 0 ? 1 : max(3, CGFloat(slice.requests) / CGFloat(max(1, data.peakTimeSlice?.requests ?? 1)) * (compact ? 28 : 43)))
                        Text(slice.name).font(.system(size: 10)).foregroundStyle(palette.muted)
                    }.frame(maxWidth: .infinity)
                }
            }.frame(height: compact ? 57 : 72, alignment: .bottom)
            HStack(alignment: .top, spacing: 15) {
                timeNote(data.firstRequest, first: true)
                Spacer(minLength: 0)
                timeNote(data.lastRequest, first: false)
            }
        }
    }
    private func timeNote(_ date: Date?, first: Bool) -> some View {
        let calendar = Calendar.current
        let hour = date.map { calendar.component(.hour, from: $0) }
        let message: String = first ? ((hour ?? 12) >= 5 && (hour ?? 12) < 9 ? L("早早开工，今天也加油喵", "An early start—keep it up") : L("按自己的节奏来，喵", "Find your own pace")) : ((hour ?? 12) >= 22 || (hour ?? 12) < 5 ? L("辛苦啦，今晚早点休息喵", "Take an earlier rest tonight") : L("努力收好，好好放松喵", "Keep your progress and unwind"))
        return VStack(alignment: first ? .leading : .trailing, spacing: 2) {
            Text((first ? L("最早请求", "First request") : L("最晚请求", "Last request")) + " · " + (date.map { $0.formatted(.dateTime.hour().minute()) } ?? "—")).font(.system(size: compact ? 10 : 11, weight: .medium)).monospacedDigit()
            if date != nil, !compact { Text(message).font(.system(size: 10, weight: .medium)).lineLimit(1) }
        }.foregroundStyle(palette.ink)
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
