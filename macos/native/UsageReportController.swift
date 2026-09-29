import AppKit
import SwiftUI
import Combine

final class UsageReportController: NSObject, NSWindowDelegate {
    private let state: AppState
    private let model = UsageReportViewModel()
    private var subscriptions = Set<AnyCancellable>()
    private var panel: NSPanel?
    private weak var parent: NSWindow?
    private var pendingAutomatic = false
    private var pendingManual = false
    private var preparing = false
    private var collection: UsageReportCollection?
    private var checkedDay = ""
    private var shown = Set<String>()
    private var sharePicker: NSSharingServicePicker?
    private var resourceStyle: UsageReportStyle?
    private var resourceLoading = false
    private var resourceGeneration = UUID()
    var onPresentationNeeded: (() -> Void)?
    var onPresentationFinished: (() -> Void)?
    var isPresenting: Bool { panel != nil }

    init(state: AppState) {
        self.state = state
        super.init()
        model.style = UsageReportStyle(rawValue: state.preferences.analytics.string("usage_report_style", "garden")) ?? .garden
        model.onClose = { [weak self] in self?.close() }
        model.onRefresh = { [weak self] in self?.prepare() }
        model.onStyle = { [weak self] style in self?.selectStyle(style) }
        model.onShare = { [weak self] view in Task { @MainActor in self?.share(from: view) } }
        state.$loading.removeDuplicates().sink { [weak self] loading in
            guard let self, !loading, self.pendingAutomatic || self.pendingManual else { return }
            self.prepare()
        }.store(in: &subscriptions)
        state.$prices.dropFirst().receive(on: DispatchQueue.main).sink { [weak self] _ in
            guard let self, self.isPresenting || self.pendingAutomatic || self.pendingManual else { return }
            self.prepare()
        }.store(in: &subscriptions)
        for name in [NSNotification.Name.NSCalendarDayChanged, NSNotification.Name.NSSystemTimeZoneDidChange] {
            NotificationCenter.default.publisher(for: name).receive(on: DispatchQueue.main).sink { [weak self] _ in
                guard let self else { return }; self.checkedDay = ""
                if self.isPresenting { self.prepare() }
            }.store(in: &subscriptions)
        }
    }
    private var dayKey: String {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = .current
        let parts = calendar.dateComponents([.year, .month, .day], from: Date())
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
    func windowOpened(_ window: NSWindow) {
        parent = window
        guard !state.paths.mock, !isPresenting, state.preferences.analytics.flag("usage_report_auto", true), Calendar.current.component(.hour, from: Date()) >= 8 else { return }
        guard checkedDay != dayKey, !shown.contains(dayKey) else { return }
        pendingAutomatic = true
        if !state.loading { prepare() }
    }
    func requestManual(on window: NSWindow) {
        parent = window; pendingManual = true
        if !state.loading { prepare() }
    }
    private func prepare() {
        guard !preparing else { return }
        preparing = true; collection = nil; model.busy = true; model.error = nil
        state.prepareUsageReports { [weak self] result in
            guard let self else { return }
            self.preparing = false; self.model.busy = false
            switch result {
            case .success(let collection):
                guard collection.presentationDay == self.dayKey, collection.documents[.day]?.timeZone == TimeZone.current.identifier else { self.prepare(); return }
                self.collection = collection; self.model.documents = collection.documents
                if self.pendingAutomatic || collection.alreadyPresented { self.checkedDay = collection.presentationDay }
                if collection.alreadyPresented { self.pendingAutomatic = false }
                self.onPresentationNeeded?()
            case .failure(let error):
                self.model.error = error.localizedDescription
                self.pendingAutomatic = false
                if self.pendingManual { self.state.errorMessage = error.localizedDescription; self.pendingManual = false }
            }
        }
    }
    @discardableResult func presentIfNeeded(on window: NSWindow) -> Bool {
        guard !isPresenting, !preparing, pendingManual || pendingAutomatic, let collection, collection.presentationDay == dayKey, collection.documents[.day]?.timeZone == TimeZone.current.identifier, !model.documents.isEmpty,
              window.isVisible, NSApp.isActive, window.attachedSheet == nil else { return false }
        guard pendingManual || Calendar.current.component(.hour, from: Date()) >= 8 else { return false }
        if !pendingManual, (shown.contains(collection.presentationDay) || collection.alreadyPresented) { pendingAutomatic = false; return false }
        guard resourceStyle == model.style else { loadResources(for: model.style); return false }
        model.period = UsageReportPeriod.preferred()
        let available = window.contentLayoutRect.size
        let size = NSSize(width: min(660, max(480, available.width-32)), height: min(590, max(365, available.height-32)))
        let panel = NSPanel(contentRect: NSRect(origin: .zero, size: size), styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
        panel.title = L("AI 使用报告", "AI usage report"); panel.titleVisibility = .hidden; panel.titlebarAppearsTransparent = true
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.isReleasedWhenClosed = false; panel.delegate = self
        panel.contentView = NSHostingView(rootView: UsageReportReader(model: model))
        panel.setContentSize(size)
        self.panel = panel; self.parent = window
        pendingAutomatic = false; pendingManual = false
        if Calendar.current.component(.hour, from: Date()) >= 8 {
            shown.insert(collection.presentationDay)
            state.markUsageReportPresented(collection.presentationDay)
        }
        window.beginSheet(panel) { [weak self] _ in
            panel.orderOut(nil); panel.contentView = nil
            self?.finishPresentation()
        }
        return true
    }
    func close() {
        guard let panel else { return }
        if let parent = panel.sheetParent { parent.endSheet(panel) }
        else { panel.orderOut(nil); panel.contentView = nil; finishPresentation() }
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { close(); return false }

    private func loadResources(for style: UsageReportStyle) {
        guard !resourceLoading else { return }
        resourceLoading = true
        let generation = UUID(); resourceGeneration = generation
        state.dataQueue.async { [weak self] in
            ReportArtworkResources.prepare(style: style)
            DispatchQueue.main.async {
                guard let self else { ReportArtworkResources.release(); return }
                guard generation == self.resourceGeneration else { ReportArtworkResources.release(); return }
                self.resourceLoading = false; self.resourceStyle = style
                self.onPresentationNeeded?()
            }
        }
    }

    private func selectStyle(_ style: UsageReportStyle) {
        guard style != model.style, !resourceLoading else { return }
        resourceLoading = true
        let generation = UUID(); resourceGeneration = generation
        state.dataQueue.async { [weak self] in
            ReportArtworkResources.prepare(style: style)
            DispatchQueue.main.async {
                guard let self else { ReportArtworkResources.release(); return }
                guard generation == self.resourceGeneration, self.panel != nil else { ReportArtworkResources.release(); return }
                self.resourceLoading = false; self.resourceStyle = style; self.model.style = style
                self.state.setPreference("usage_report_style", style.rawValue)
            }
        }
    }

    private func finishPresentation() {
        panel = nil; collection = nil; model.documents = [:]
        resourceGeneration = UUID(); resourceLoading = false; resourceStyle = nil
        ReportArtworkResources.release()
        onPresentationFinished?()
    }

    @MainActor private func share(from anchor: NSView) {
        guard let data = model.documents[model.period], !model.busy else { return }
        do {
            let image = try UsageReportRenderer.image(data: data, style: model.style)
            let style = model.style
            let folder = state.usageReportDirectory.appendingPathComponent(data.sourceKey).appendingPathComponent("shared")
            let queue = state.dataQueue
            Task { @MainActor [weak self, weak anchor] in
                let result: Result<URL, Error> = await withCheckedContinuation { continuation in
                    queue.async {
                        do {
                            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                            let destination = folder.appendingPathComponent("Codexio-\(data.period.rawValue)-\(data.fileDate)-\(style.rawValue).png")
                            let bitmap = NSBitmapImageRep(cgImage: image)
                            guard let png = bitmap.representation(using: .png, properties: [:]) else { throw AppFailure(L("无法生成报告图片", "Could not create the report image")) }
                            try png.write(to: destination, options: .atomic)
                            continuation.resume(returning: .success(destination))
                        } catch { continuation.resume(returning: .failure(error)) }
                    }
                }
                guard let self else { return }
                switch result {
                case .success(let destination):
                    guard let anchor, anchor.window != nil else { return }
                    let picker = NSSharingServicePicker(items: [destination]); self.sharePicker = picker
                    picker.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .minY)
                case .failure(let error): self.model.error = error.localizedDescription
                }
            }
        } catch { model.error = error.localizedDescription }
    }
}

enum UsageReportRenderer {
    @MainActor static func image(data: UsageReportData, style: UsageReportStyle) throws -> CGImage {
        let canvas = ZStack {
            style.palette.surface.opacity(0.65)
            UsageReportCard(style: style, data: data)
        }.frame(width: style.width+90, height: style.height+80)
        let renderer = ImageRenderer(content: canvas)
        renderer.scale = 2; renderer.proposedSize = ProposedViewSize(width: style.width+90, height: style.height+80)
        guard let image = renderer.cgImage else { throw AppFailure(L("无法渲染报告", "Could not render the report")) }
        return image
    }
    @MainActor static func preview(to destination: URL, style: UsageReportStyle = .garden) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        ReportArtworkResources.prepare(style: style)
        defer { ReportArtworkResources.release() }
        let image = try image(data: previewData, style: style)
        guard let bytes = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else { throw AppFailure("Could not encode report preview") }
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try bytes.write(to: destination, options: .atomic)
        print(destination.path)
    }
    static var previewData: UsageReportData {
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        let start = calendar.date(from: DateComponents(year: 2026, month: 9, day: 28))!, end = calendar.date(byAdding: .day, value: 1, to: start)!
        return UsageReportData(period: .day, start: start, end: end, timeZone: calendar.timeZone.identifier, dateLabel: "2026.09.28", fileDate: "2026-09-28", sourceKey: "isolated-preview", priceVersion: "preview", requests: 42, modelCalls: 68, inputTokens: 1_800_000, outputTokens: 380_000, cachedInputTokens: 900_000, totalTokens: 2_180_000, tokensComplete: true, costUSD: 9.84, costComplete: true,
            models: [UsageReportModel(name: "gpt-6-sol", calls: 40, tokens: 1_200_000, costUSD: 4.20), UsageReportModel(name: "gpt-6-astra", calls: 20, tokens: 780_000, costUSD: 5.12), UsageReportModel(name: "gpt-6-luna", calls: 8, tokens: 200_000, costUSD: 0.52)],
            projects: [UsageReportProject(id: "/Demo/Codexio", name: "Codexio", requests: 23), UsageReportProject(id: "/Demo/笔记整理", name: "笔记整理", requests: 13), UsageReportProject(id: "/Demo/资料库", name: "资料库", requests: 6)],
            timeSlices: [UsageReportTimeSlice(name: L("凌晨", "Night"), requests: 3), UsageReportTimeSlice(name: L("上午", "Morning"), requests: 8), UsageReportTimeSlice(name: L("午后", "Afternoon"), requests: 19), UsageReportTimeSlice(name: L("晚间", "Evening"), requests: 12)],
            firstRequest: start.addingTimeInterval(8*3600+42*60), lastRequest: start.addingTimeInterval(23*3600+16*60), previousTokens: 1_650_000, previousRequests: 35, previousCostUSD: 7.92)
    }
}
