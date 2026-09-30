import AppKit
import SwiftUI
import Foundation

@main
@MainActor
enum CodexioMain {
    static var delegate: ApplicationDelegate?
    static func main() {
        let arguments = CommandLine.arguments
        if arguments.contains("--version") { print(BuildInfo.version); return }
        do {
            if arguments.contains("--mock") && ["--native-update-job","--upstream-helper","--render-icon"].contains(where:arguments.contains) { throw AppFailure("Mock mode cannot run installation or background helpers") }
            if let index = arguments.firstIndex(of: "--render-report-preview") {
                guard arguments.contains("--mock"), arguments.indices.contains(index+1) else { throw AppFailure("--render-report-preview requires --mock and a PNG path") }
                let styleIndex = arguments.firstIndex(of: "--report-style")
                let style = styleIndex.flatMap { arguments.indices.contains($0+1) ? UsageReportStyle(rawValue: arguments[$0+1]) : nil } ?? .garden
                try UsageReportRenderer.preview(to: URL(fileURLWithPath: arguments[index+1]), style: style)
                return
            }
            if let index = arguments.firstIndex(of:"--render-icon"), arguments.indices.contains(index+1) { try Branding.renderIconset(to:URL(fileURLWithPath:arguments[index+1])); return }
            if let index = arguments.firstIndex(of:"--native-update-job"), arguments.indices.contains(index+1) { try NativeUpdater.installJob(URL(fileURLWithPath:arguments[index+1])); return }
            if ["--upstream-helper","--upstream-proxy","--widget-service","--widget-refresh"].contains(where:arguments.contains) { return }
            let mock = arguments.contains("--mock")
            if arguments.contains("--mock-gallery") && !mock { throw AppFailure("--mock-gallery requires --mock") }
            var smoke: URL?
            if let index = arguments.firstIndex(of:"--smoke-test") {
                guard mock, arguments.indices.contains(index+1) else { throw AppFailure("--smoke-test requires --mock and an output directory") }
                smoke = URL(fileURLWithPath:arguments[index+1]).standardizedFileURL
            }
            let paths = try AppPaths(mock:mock,smoke:smoke)
            if try Installation.takeoverIfNeeded(paths:paths) { return }
            guard let lease = FileLease(paths.data.appendingPathComponent("native-main.lock")) else {
                DistributedNotificationCenter.default().postNotificationName(.init("com.wujuhu.codexio.show"),object:nil,userInfo:nil,deliverImmediately:true); return
            }
            let application = NSApplication.shared
            application.setActivationPolicy(.regular)
            let delegate = try ApplicationDelegate(paths:paths,lease:lease,smoke:smoke)
            self.delegate = delegate; application.delegate = delegate; application.run()
        } catch {
            fputs(error.localizedDescription+"\n",stderr)
            if !arguments.contains("--mock") && !arguments.contains("--widget-service") && !arguments.contains("--native-update-job") && !arguments.contains("--upstream-helper") {
                let alert = NSAlert(); alert.messageText = "Codexio"; alert.informativeText = error.localizedDescription; alert.runModal()
            }
            exit(1)
        }
    }
}

final class ApplicationDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let state: AppState
    private let lease: FileLease
    private let smoke: URL?
    private var window: NSWindow?
    private var status: MenuBarController?
    private var updater: NativeUpdater?
    private var usageReports: UsageReportController?
    private var upstream: UpstreamCoordinator?
    private var isTerminating = false
    private var terminationGeneration = UUID()
    private var gallery: MockGallery?
    private var smokeTimer: Timer?
    private var smokeStep = 0
    private var smokeStarted = Date()
    private var smokeChecks: [String] = []
    init(paths: AppPaths,lease: FileLease,smoke: URL?) throws {
        self.lease = lease; self.smoke = smoke; state = try AppState(paths:paths)
        super.init()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        menu()
        state.onOpenWindow = { [weak self] in self?.showWindow() }
        state.onQuit = { NSApp.terminate(nil) }
        state.onCancelQuit = { [weak self] in self?.cancelPendingQuit() }
        state.onRestartCodex = { [weak self] completion in self?.promptCodexRestart(completion:completion) }
        state.onQuotaChange = { [weak self] in self?.status?.update() }
        state.onMenuDataChange = { [weak self] in self?.status?.update() }
        state.onSettingsChange = { [weak self] in self?.applyAppearance(); self?.status?.update() }
        usageReports = UsageReportController(state: state)
        usageReports?.onPresentationNeeded = { [weak self] in self?.presentPendingWindowContent() }
        usageReports?.onPresentationFinished = { [weak self] in self?.presentPendingWindowContent() }
        state.onOpenUsageReport = { [weak self] in self?.openUsageReport() }
        if !state.paths.mock {
            status = MenuBarController(state)
            updater = NativeUpdater(state:state)
            updater?.onPresentationNeeded = { [weak self] in self?.presentPendingWindowContent() }
            updater?.onPresentationFinished = { [weak self] in self?.presentPendingWindowContent() }
            updater?.onInstallRequested = { NSApp.terminate(nil) }
            updater?.onInstallationTimedOut = { [weak self] in
                guard let self, self.isTerminating else { return }
                self.cancelPendingQuit()
            }
            upstream = UpstreamCoordinator(state:state)
            state.onCheckUpdate = { [weak self] in self?.updater?.check(manual:true) }
            state.onInstallUpdate = { [weak self] in self?.updater?.requestUpdate() }
            state.onUpstreamChange = { [weak self] enabled in self?.upstream?.setEnabled(enabled) }
            state.accountQueue.async { [weak self] in
                guard let self else { return }
                do {
                    try Installation.retireLegacyWidgetServices(paths:self.state.paths)
                    try Installation.registerWidget(paths:self.state.paths)
                    DispatchQueue.main.async {
                        Installation.acknowledge(paths:self.state.paths)
                        self.state.announceWidgetHost()
                        self.state.accountQueue.async { Installation.cleanupConfirmedBackups(paths:self.state.paths) }
                    }
                }
                catch { DispatchQueue.main.async { self.state.errorMessage = error.localizedDescription } }
            }
            DistributedNotificationCenter.default().addObserver(self,selector:#selector(showWindow),name:.init("com.wujuhu.codexio.show"),object:nil)
        }
        state.applyAppIcon(state.appIconStyle,persist:false)
        applyAppearance(); state.announceWidgetHost(); state.start(); showWindow()
        if let index = CommandLine.arguments.firstIndex(of:"--mock-gallery"), CommandLine.arguments.indices.contains(index+1), state.paths.mock, let window {
            gallery = MockGallery(state:state,window:window,directory:URL(fileURLWithPath:CommandLine.arguments[index+1]))
            gallery?.start()
        } else if smoke != nil { startSmoke() }
        else {
            DispatchQueue.main.asyncAfter(deadline:.now()+4) { [weak self] in self?.upstream?.recoverAndResume() }
        }
    }
    @objc func showWindow() {
        if window == nil {
            let available = NSScreen.main?.visibleFrame ?? NSRect(x:0,y:0,width:1440,height:900)
            let geometry = state.preferences.analytics["native_geometry"] as? [Double]
            let width = min(geometry?[safe:2] ?? 1380,available.width-24), height = min(geometry?[safe:3] ?? 900,available.height-24)
            let minimum = PageLayout.minimumWindowSize
            let window = NSWindow(contentRect:NSRect(x:0,y:0,width:max(minimum.width,width),height:max(minimum.height,height)),styleMask:[.titled,.closable,.miniaturizable,.resizable,.fullSizeContentView],backing:.buffered,defer:false)
            window.title = "Codexio"; window.titleVisibility = .hidden; window.titlebarAppearsTransparent = true
            window.contentView = NSHostingView(rootView:MainView(state:state)); window.minSize = minimum; window.isReleasedWhenClosed = false; window.delegate = self; window.center()
            if let geometry, geometry.count == 4 {
                let origin = NSPoint(x:geometry[0],y:geometry[1])
                if NSScreen.screens.contains(where:{$0.visibleFrame.contains(origin)}) { window.setFrameOrigin(origin) }
            }
            self.window = window
        }
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true)
        state.mainWindowVisible = true; state.refreshVisibleReports()
        if let window { usageReports?.windowOpened(window); presentPendingWindowContent() }
    }
    func windowDidBecomeKey(_ notification: Notification) {
        if let window, !isTerminating { usageReports?.windowOpened(window); presentPendingWindowContent() }
    }
    func applicationDidBecomeActive(_ notification: Notification) {
        if let window, window.isVisible, !isTerminating { usageReports?.windowOpened(window); presentPendingWindowContent() }
    }
    private func presentPendingWindowContent() {
        guard !isTerminating, let window, window.isVisible, NSApp.isActive, window.attachedSheet == nil, updater?.isResolvingStartup != true else { return }
        if updater?.presentIfNeeded(on: window) == true { return }
        _ = usageReports?.presentIfNeeded(on: window)
    }
    @objc private func openUsageReport() {
        showWindow()
        if let window { usageReports?.requestManual(on: window) }
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { saveGeometry(); state.mainWindowVisible = false; sender.orderOut(nil); return false }
    func windowDidChangeOcclusionState(_ notification: Notification) {
        state.mainWindowVisible = window?.isVisible == true && window?.occlusionState.contains(.visible) == true
        if state.mainWindowVisible { state.refreshVisibleReports() }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication,hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }
    func application(_ application: NSApplication,open urls: [URL]) {
        if urls.contains(where:{$0.scheme == "codexio" && $0.host == "subscription"}) { state.selectedPage = "subscription" }
        showWindow()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        let menu = NSMenu(); let item = NSMenuItem(title:L("打开主界面", "Open main window"),action:#selector(showWindow),keyEquivalent:""); item.target = self; menu.addItem(item); return menu
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if state.paths.mock { return .terminateNow }
        if isTerminating { return .terminateLater }
        isTerminating = true; terminationGeneration = UUID(); let generation = terminationGeneration
        updater?.beginTermination(); usageReports?.close(); saveGeometry(); status?.close()
        let prepareAndFinish: () -> Void = { [weak self] in
            guard let self, self.isTerminating, self.terminationGeneration == generation else { return }
            let ready: (Error?) -> Void = { [weak self] error in
                guard let self, self.isTerminating, self.terminationGeneration == generation else { return }
                if error != nil { self.cancelPendingQuit(); return }
                self.state.finishQuit { [weak self] error in
                    guard let self, self.isTerminating, self.terminationGeneration == generation else { return }
                    if let error { fputs(error.localizedDescription+"\n",stderr) }
                    self.updater?.stopInstallationWatchdog()
                    NSApp.reply(toApplicationShouldTerminate:true)
                }
            }
            if let updater = self.updater { updater.prepareInstallerIfNeeded(completion: ready) } else { ready(nil) }
        }
        if let upstream { upstream.prepareQuit(completion: prepareAndFinish) } else { prepareAndFinish() }
        return .terminateLater
    }
    private func cancelPendingQuit(_ error: Error? = nil) {
        isTerminating = false; terminationGeneration = UUID(); updater?.cancelTermination(); upstream?.cancelQuit()
        state.resumeAfterCancelledQuit()
        status = MenuBarController(state)
        if let error { state.errorMessage = error.localizedDescription }
        showWindow(); NSApp.reply(toApplicationShouldTerminate:false)
    }
    func applicationWillTerminate(_ notification: Notification) { state.stop(); saveGeometry(); DistributedNotificationCenter.default().removeObserver(self) }
    private func saveGeometry() {
        guard !state.paths.mock, let frame = window?.frame else { return }
        state.preferences.analytics["native_geometry"] = [frame.minX,frame.minY,frame.width,frame.height]
        try? state.preferences.save()
    }
    private func promptCodexRestart(completion: @escaping () -> Void) {
        guard !state.paths.mock else { completion(); return }
        let clients = ["com.openai.codex","com.openai.chat"].flatMap {NSRunningApplication.runningApplications(withBundleIdentifier:$0)}.filter {!$0.isTerminated}
        guard !clients.isEmpty else { completion(); return }
        let alert = NSAlert(); alert.messageText = L("重新打开 Codex 以应用路由？", "Reopen Codex to apply the route?")
        alert.informativeText = L("路由配置已更新。可以等待当前任务结束后自行重启；现在重启会先请求客户端正常退出。", "The route configuration is updated. You can reopen after the current task finishes. Restart now requests a normal quit first.")
        alert.addButton(withTitle:L("稍后自行重启", "Reopen later")); alert.addButton(withTitle:L("现在重启", "Restart now"))
        guard alert.runModal() == .alertSecondButtonReturn else { completion(); return }
        let apps = Set(clients.compactMap(\.bundleURL))
        for client in clients { _ = client.terminate() }
        DispatchQueue.global(qos:.utility).async {
            let deadline = Date().addingTimeInterval(20)
            while clients.contains(where:{!$0.isTerminated}) && Date() < deadline { Thread.sleep(forTimeInterval:0.1) }
            DispatchQueue.main.async {
                if clients.contains(where:{!$0.isTerminated}) { self.state.upstreamStatus = L("客户端尚未退出，请在任务结束后自行重新打开", "The client is still running; reopen it when the task finishes") }
                else { for app in apps { NSWorkspace.shared.openApplication(at:app,configuration:NSWorkspace.OpenConfiguration(),completionHandler:nil) } }
                completion()
            }
        }
    }
    private func applyAppearance() {
        let next = state.theme == "dark" ? NSAppearance(named:.darkAqua) : state.theme == "light" ? NSAppearance(named:.aqua) : nil
        if NSApp.appearance?.name != next?.name { NSApp.appearance = next }
    }
    @objc private func showSettings() { state.selectedPage = "settings"; showWindow() }
    @objc private func refresh() { state.refresh() }
    @objc private func search() { state.selectedPage = "logs"; showWindow(); DispatchQueue.main.asyncAfter(deadline:.now()+0.1) { NotificationCenter.default.post(name:.init("CodexioSearch"),object:nil) } }
    private func menu() {
        let bar = NSMenu(), app = NSMenu(), view = NSMenu(), edit = NSMenu()
        func attach(_ title: String,_ submenu: NSMenu) { let item = NSMenuItem(title:title,action:nil,keyEquivalent:""); item.submenu = submenu; bar.addItem(item) }
        func item(_ menu: NSMenu,_ title: String,_ action: Selector,_ key: String,_ target: AnyObject? = nil) { let entry = NSMenuItem(title:title,action:action,keyEquivalent:key); entry.target = target; menu.addItem(entry) }
        attach("Codexio",app)
        item(app,L("关于 Codexio", "About Codexio"),#selector(NSApplication.orderFrontStandardAboutPanel(_:)),"",NSApp)
        app.addItem(.separator()); item(app,L("设置…", "Settings…"),#selector(showSettings),",",self)
        app.addItem(.separator()); item(app,L("隐藏 Codexio", "Hide Codexio"),#selector(NSApplication.hide(_:)),"h",NSApp)
        app.addItem(.separator()); item(app,L("退出 Codexio", "Quit Codexio"),#selector(NSApplication.terminate(_:)),"q",NSApp)
        attach(L("编辑", "Edit"),edit)
        item(edit,L("撤销", "Undo"),Selector(("undo:")),"z"); item(edit,L("剪切", "Cut"),#selector(NSText.cut(_:)),"x"); item(edit,L("复制", "Copy"),#selector(NSText.copy(_:)),"c"); item(edit,L("粘贴", "Paste"),#selector(NSText.paste(_:)),"v"); item(edit,L("全选", "Select All"),#selector(NSText.selectAll(_:)),"a")
        attach(L("窗口", "Window"),view)
        item(view,L("打开主界面", "Open main window"),#selector(showWindow),"0",self); item(view,L("搜索日志", "Search logs"),#selector(search),"k",self); item(view,L("刷新", "Refresh"),#selector(refresh),"r",self); item(view,L("关闭窗口", "Close window"),#selector(NSWindow.performClose(_:)),"w")
        item(view,L("AI 使用报告…", "AI usage reports…"),#selector(openUsageReport),"",self)
        NSApp.mainMenu = bar; NSApp.windowsMenu = view
    }
    private func startSmoke() {
        smokeStarted = Date()
        smokeTimer = Timer.scheduledTimer(withTimeInterval:0.2,repeats:true) { [weak self] _ in self?.advanceSmoke() }
    }
    private func advanceSmoke() {
        guard let window else { finishSmoke(false,"No window"); return }
        if state.loading {
            if Date().timeIntervalSince(smokeStarted) > 30 { finishSmoke(false,"Mock startup timed out") }; return
        }
        switch smokeStep {
        case 0:
            guard window.isVisible else { finishSmoke(false,"Window did not appear"); return }; smokeChecks.append("程序启动")
        case 1:
            guard (state.usage.summaries["today"]?.tokens ?? 0) > 0, state.quota.week?.remaining != nil else { finishSmoke(false,"Basic data missing"); return }; smokeChecks.append("基本数据显示")
        case 2:
            if ProcessInfo.processInfo.environment["CODEXIO_CAPTURE_PAGE"] == "overview", let view = window.contentView, let image = view.bitmapImageRepForCachingDisplay(in:view.bounds), let smoke { view.cacheDisplay(in:view.bounds,to:image); try? image.representation(using:.png,properties:[:])?.write(to:smoke.appendingPathComponent("overview.png")) }
            window.performClose(nil)
        case 3:
            guard !window.isVisible else { finishSmoke(false,"Window did not close"); return }; showWindow()
        default:
            guard window.isVisible else { finishSmoke(false,"Window did not reopen"); return }; smokeChecks.append("主窗口关闭与重开"); finishSmoke(true,nil); return
        }
        smokeStep += 1
    }
    private func finishSmoke(_ ok: Bool,_ error: String?) {
        smokeTimer?.invalidate()
        if let smoke { try? atomicJSON(["ok":ok,"checks":smokeChecks,"error":error as Any? ?? NSNull(),"runtime":"SwiftUI/AppKit"],to:smoke.appendingPathComponent("result.json")) }
        state.stop(); NSApp.stop(nil); exit(ok ? 0 : 1)
    }
}

extension Array { subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil } }
