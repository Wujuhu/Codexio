import Foundation
import Combine
import WidgetKit
import AppKit

final class AppState: ObservableObject {
    let paths: AppPaths
    let preferences: Preferences
    let database: Database
    let catalog: PricingCatalog
    let claudeCatalog: ClaudePricingCatalog
    @Published private(set) var claudePrices: [PriceRow] = []
    @Published private(set) var claudePricesRevision = ""
    @Published private(set) var claudePriceUpdated: Date?
    @Published private(set) var claudePriceWarning: String?
    let claudeAllowance: ClaudeAllowance
    let claudeEstimator: WeeklyEstimator
    @Published private(set) var claudeQuota = QuotaState()
    @Published private(set) var claudeWeeklyEstimates: [Object] = []
    let claudeQuotaClock = ScanClock()
    private var claudeQuotaKey = ""
    private var claudeLastSample: String?
    private var claudeContextKey = ""
    private var sleepObserver: NSObjectProtocol?
    private var claudeEstimateRevision = -1
    private var claudeObserved: Date?
    var claudeQuotaFresh: Bool {claudeObserved.map {Date().timeIntervalSince($0) >= 0 && Date().timeIntervalSince($0) < 900} == true}
    let claudeLedger: ClaudeLedger
    let claudeClock = ScanClock()
    @Published var pageClient: DesktopClient = .codex
    var logFilters: [DesktopClient:LogFilterMemory] = [:]
    @Published private(set) var claudeRevision = UUID()
    @Published private(set) var claudeModels: [String] = []
    @Published private(set) var claudeWarning: String?
    private var claudePublishedKey = "" // dataQueue-owned dependency key.
    private var claudeNextBoundary = Date.distantFuture
    private var claudeLastScan = Date.distantPast
    @Published private(set) var claudeSurface = AgentSurfaceData()
    private var claudeSurfaceCache = AgentSurfaceData() // dataQueue-owned.
    private var claudeSurfaceKey = ""
    private var claudeSurfaceRevision = -1
    private var claudeTaskBoundary = Date.distantFuture
    let indexer: UsageIndexer
    let estimator: WeeklyEstimator
    let client = CodexClient()
    let clock = ScanClock()
    let fetchActivity = FetchActivity()
    let quotaClock = ScanClock()
    let menuQuotaClock = ScanClock()
    lazy var mobileSync = MobileSync(paths: paths, detailProvider: { [weak self] id in
        guard let self else{return nil}
        return try self.dataQueue.sync {id.hasPrefix("claude_code:") ? try self.claudeLedger.mobileDetail(id) : try self.database.requestMessageDetail(id)}
    }, detailPreparer: { [weak self] ids in try self?.dataQueue.sync {try self?.database.prepareRequestMessageDetails(ids.filter{!$0.hasPrefix("claude_code:")})} })
    private var claudeMobilePending=false
    private var claudeMobileKey=""
    private var claudeMobileNext=Date.distantPast
    private var claudeMobileTimeBoundary=Date.distantFuture
    private var claudeMobileLastObserved=Date.distantPast
    private var claudeMobileDayKey=""
    private var claudeMobileSnapshot:ClaudeMobileSnapshot?
    var usageReportDirectory: URL { paths.data.appendingPathComponent("Reports", isDirectory: true) }
    private lazy var usageReportStore = UsageReportStore(directory: usageReportDirectory)
    private let brandingQueue = DispatchQueue(label:"com.wujuhu.codexio.branding",qos:.utility)
    private lazy var usageCache = UsageSnapshotCache(database:database,catalog:catalog)
    lazy var journeyReader = JourneyReader(database)
    lazy var claudeJourney = ClaudeJourney(claudeLedger)
    let journeyCatalog = JourneyCatalog()
    private let objectFiles = ObjectFileCache()
    let overviewProjection = AsyncProjection(TrendProjection())
    let trendProjection = AsyncProjection(TrendProjection())
    let logProjection = AsyncProjection(LogProjection())
    private let snapshotQueue = DispatchQueue(label:"com.wujuhu.codexio.widget-snapshot",qos:.utility)
    private(set) var pricesRevision = ""
    private var displayedEstimates = -1
    let dataQueue = DispatchQueue(label:"com.wujuhu.codexio.data",qos:.utility)
    let accountQueue = DispatchQueue(label:"com.wujuhu.codexio.account",qos:.utility)
    let reportQueue = DispatchQueue(label:"com.wujuhu.codexio.reports",qos:.utility)
    @Published var usage = UsageSnapshot()
    private(set) var quota = QuotaState()
    private(set) var menuQuota = MenuQuota()
    private var menuQuotaCacheLoaded = false // Owned by accountQueue.
    private var menuQuotaWritten = Date.distantPast
    private var displayedQuotaKey = ""
    private var displayedQuotaFresh = false
    @Published var prices: [PriceRow] = []
    @Published var modelIDs = Set<String>()
    @Published var priceUpdated: Date?
    @Published var priceWarning: String?
    @Published var weeklyEstimates: [Object] = []
    @Published var selectedPage = "overview"
    @Published var settingsSection = "appearance"
    @Published var usageSection = "activity"
    @Published var theme = "system"
    @Published private(set) var appIconStyle = "main"
    @Published private(set) var appLogoImage: NSImage?
    @Published private(set) var appIconApplying = false
    @Published var sidebarVisible = true
    @Published var sidebarWidth: Double = 238
    @Published var menuVisible = true
    @Published private(set) var menuFields = MenuBarField.defaults
    @Published private(set) var menuClient: DesktopClient = .codex
    var menuSummary: UsageSummary {menuSummary(for:menuClient)}
    func menuSummary(for client: DesktopClient) -> UsageSummary {client == .codex ? usage.summaries["today"] ?? UsageSummary() : claudeSurface.today}
    var menuDays: [DayUsage] {menuClient == .codex ? Array(usage.activity.days.suffix(7)) : claudeSurface.days}
    func menuRequest(for client: DesktopClient) -> UsageRow? {client == .codex ? usage.widgetRequest : claudeSurface.request}
    func menuTaskRunning(for client: DesktopClient) -> Bool? {client == .codex ? taskRunning : claudeSurface.ready ? claudeSurface.hasRunningTask : nil}
    func menuWindow(_ client: DesktopClient,week: Bool) -> QuotaWindow? {
        let window = client == .codex ? (week ? menuQuota.week : menuQuota.five) : (claudeQuotaFresh ? (week ? claudeQuota.week : claudeQuota.five) : nil)
        return window.flatMap {window in window.reset.map {$0 <= Date()} == true ? nil : window}
    }
    func selectMenuClient(_ client: DesktopClient) {setPreference("menu_bar_agent",client.rawValue)}
    @Published var loading = true
    @Published var scanProgress = ""
    @Published var errorMessage: String?
    @Published var actionMessage: String?
    @Published var resetBusy = false
    @Published var pendingResets: Set<String> = []
    @Published var planHistory: Object = [:]
    @Published var chatUsage: Object = [:]
    @Published var reportError: String?
    @Published var reportsLoading = false
    @Published var updateStatus = ""
    @Published var updateAvailable = false
    @Published var upstreamStatus = ""
    private var scanTimer: Timer?
    private var quotaTimer: Timer?
    private var priceTimer: Timer?
    private var scanning = false
    private var refreshing = false
    private var stopped = false
    private var quitGeneration = UUID()
    var mainWindowVisible = false
    private var reportGeneration = UUID()
    private var reportLoadedAt: Date?
    private var reportRetryAt = Date.distantPast
    private var reportedCandidates = 0
    private var reportInFlight = false
    private var snapshotSignature = ""
    private var snapshotWritten = Date.distantPast
    var onQuotaChange: (() -> Void)?
    var onMenuDataChange: (() -> Void)?
    private(set) var taskRunning: Bool?
    var onSettingsChange: (() -> Void)?
    var onCheckUpdate: (() -> Void)?
    var onInstallUpdate: (() -> Void)?
    var onUpstreamChange: ((Bool) -> Void)?
    var onOpenWindow: (() -> Void)?
    var onOpenUsageReport: (() -> Void)?
    var onQuit: (() -> Void)?
    var onCancelQuit: (() -> Void)?
    var onRestartCodex: ((@escaping () -> Void) -> Void)?
    var accountRoot: URL { URL(fileURLWithPath:ProcessInfo.processInfo.environment["CODEX_HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex").path) }

    init(paths: AppPaths) throws {
        self.paths = paths; preferences = Preferences(paths)
        claudeCatalog = ClaudePricingCatalog(paths.pricing)
        claudeLedger = try ClaudeLedger(paths:paths,pricing:claudeCatalog)
        claudePrices = claudeCatalog.rows; claudePricesRevision = claudeCatalog.version
        claudeAllowance = ClaudeAllowance(paths:paths,root:claudeLedger.root)
        let ledger = claudeLedger
        claudeEstimator = try WeeklyEstimator(ledger.database,limitID:"claude",usage:{ledger.subscriptionConsumption(start:$0,end:$1)})
        database = try Database(paths.database); catalog = PricingCatalog(paths.pricing); indexer = UsageIndexer(database); estimator = try WeeklyEstimator(database)
        theme = preferences.analytics.string("theme","system")
        appIconStyle = Branding.iconID(preferences.analytics.string("app_icon","main"))
        sidebarVisible = !preferences.analytics.flag("sidebar_collapsed")
        sidebarWidth = min(320,max(140,preferences.analytics.number("native_sidebar_width") ?? preferences.analytics.number("sidebar_width") ?? 238))
        menuVisible = preferences.analytics.flag("menu_bar_visible",true)
        menuFields = MenuBarField.normalize(preferences.analytics["menu_bar_fields"] as? [String] ?? MenuBarField.defaults)
        menuClient = DesktopClient(rawValue:preferences.analytics.string("menu_bar_agent")) ?? .codex
        if !paths.mock {
            sleepObserver = NSWorkspace.shared.notificationCenter.addObserver(forName:NSWorkspace.willSleepNotification,object:nil,queue:.main) { [weak self] _ in
                self?.dataQueue.async { [weak self] in self?.estimator.invalidate(); self?.claudeEstimator.invalidate() }
            }
        }
        client.onNotification = { [weak self] method, _ in
            guard ["account/rateLimits/updated","account/updated"].contains(method) else { return }
            DispatchQueue.main.async {
                if method == "account/updated" { self?.invalidateReports() }
                self?.refreshQuota()
            }
        }
    }
    deinit {if let sleepObserver {NSWorkspace.shared.notificationCenter.removeObserver(sleepObserver)}}
    func start() {
        if paths.mock { seedMock(); return }
        if UserDefaults.standard.bool(forKey:"codexio.mobile.enabled") { mobileSync.start() }
        claudeAllowance.onSample = { [weak self] sample in self?.dataQueue.async { [weak self] in self?.receiveClaudeQuota(sample)} }
        claudeAllowance.start()
        refreshUsage(); refreshQuota()
        configureTimers()
        let priceFetch = fetchActivity.begin()
        dataQueue.async { [weak self] in
            guard let self else { return }
            defer { DispatchQueue.main.async {self.fetchActivity.end(priceFetch)} }
            guard self.preferences.analytics.flag("auto_sync_prices",true) else { return }
            do { try self.catalog.sync(); self.publishUsage() }
            catch { DispatchQueue.main.async { self.errorMessage = L("价格同步失败，保留已有价格", "Price sync failed; saved prices are retained") } }
            do {try self.claudeCatalog.sync(); try self.claudeLedger.refreshPricing(); self.publishClaude()}
            catch { DispatchQueue.main.async { self.errorMessage = L("价格同步失败，保留已有价格", "Price sync failed; saved prices are retained") } }
        }
    }
    func configureTimers() {
        scanTimer?.invalidate(); quotaTimer?.invalidate(); priceTimer?.invalidate()
        guard !paths.mock else { return }
        scanTimer = Timer.scheduledTimer(withTimeInterval:max(5,preferences.analytics.number("usage_refresh_interval_seconds") ?? 10),repeats:true) { [weak self] _ in self?.refreshUsage() }
        quotaTimer = Timer.scheduledTimer(withTimeInterval:max(30,preferences.general.number("refresh_interval_seconds") ?? 60),repeats:true) { [weak self] _ in self?.refreshQuota() }
        scanTimer?.tolerance = 1
        quotaTimer?.tolerance = 5
        priceTimer = Timer.scheduledTimer(withTimeInterval:3600,repeats:true) { [weak self] _ in
            guard let self, self.preferences.analytics.flag("auto_sync_prices",true) else { return }
            let operation = self.fetchActivity.begin()
            self.dataQueue.async { defer {DispatchQueue.main.async {self.fetchActivity.end(operation)}}; try? self.catalog.sync(); self.publishUsage(); try? self.claudeCatalog.sync(); try? self.claudeLedger.refreshPricing(); self.publishClaude() }
        }
        priceTimer?.tolerance = 180
    }
    func stop() {
        if !paths.mock { mobileSync.stop() }
        stopped = true; claudeAllowance.stop(); scanTimer?.invalidate(); quotaTimer?.invalidate(); priceTimer?.invalidate(); indexer.cancel(); claudeLedger.cancel(); client.close()
        fetchActivity.clear()
        reportGeneration = UUID()
    }
    func finishQuit(completion: @escaping (Error?) -> Void) {
        quitGeneration = UUID()
        stop()
        snapshotQueue.async { [self] in
            client.shutdown()
            dataQueue.sync { journeyReader.closeSources(); journeyCatalog.closeSources() }
            do {
                if !paths.mock {
                    try writeWidget(["schema":1,"updated_at":Date().timeIntervalSince1970,"host_running":false,"host_pid":NSNull(),"request":NSNull(),"quota":["applicable":true],"today":NSNull()])
                    try Installation.retireLegacyWidgetServices(paths:paths)
                }
                performQuitCallback { completion(nil) }
            } catch { performQuitCallback { completion(error) } }
        }
    }
    func resumeAfterCancelledQuit() {
        guard stopped else { return }
        let generation = quitGeneration
        // Resume behind the old shutdown work, so it cannot close a new client
        // or leave a late offline snapshot after cancellation.
        let pending = DispatchGroup()
        for queue in [snapshotQueue,dataQueue,accountQueue,reportQueue] {
            pending.enter(); queue.async { pending.leave() }
        }
        pending.notify(queue:.main) { [self] in
            guard stopped, quitGeneration == generation else { return }
            client.resumeAfterCancelledShutdown()
            claudeLedger.resume()
            stopped = false; scanning = false; refreshing = false; reportInFlight = false
            loading = false; reportsLoading = false
            configureTimers()
            if !paths.mock, UserDefaults.standard.bool(forKey:"codexio.mobile.enabled") { mobileSync.start() }
            announceWidgetHost()
        }
    }
    func announceWidgetHost() {
        guard !paths.mock else { return }
        snapshotSignature = ""; publishWidget()
    }
    private func writeWidget(_ snapshot: Object) throws {
        guard !paths.mock else { return }
        guard try jsonData(snapshot).count <= 32768 else { throw AppFailure("Widget snapshot is too large") }
        try atomicJSON(snapshot,to:paths.snapshot)
        let canonical = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Codexio/widget_snapshot.json")
        if paths.snapshot != canonical, ProcessInfo.processInfo.environment["CODEXIO_DATA_DIR"] == nil { try atomicJSON(snapshot,to:canonical) }
        WidgetCenter.shared.reloadAllTimelines()
    }
    func refresh() { if selectedPage == "pricing" {syncPrices();return}; refreshUsage(); if pageClient == .codex || !["overview","logs","trends","subscription"].contains(selectedPage) { refreshQuota() }; if selectedPage == "subscription" && pageClient == .codex { refreshReports(force:true) } }
    private func usageReportSourceKey(for client: DesktopClient) -> String {
        client == .codex ? preferences.roots.map(\.path).sorted().joined(separator:"\n")+"\n"+accountRoot.path : "claude_code\n"+claudeLedger.root.path
    }
    func prepareUsageReports(client: DesktopClient = .codex,completion: @escaping (Result<UsageReportCollection, Error>) -> Void) {
        let sourceKey = usageReportSourceKey(for:client)
        dataQueue.async { [weak self] in
            guard let self else {return}
            let result: Result<UsageReportCollection,Error>
            do {
                if client == .claudeCode {
                    result = .success(try self.usageReportStore.load(claude:self.claudeLedger,sourceKey:sourceKey))
                } else {
                    let input = try self.database.transaction { () throws -> (UsageSnapshot,Int) in
                        let snapshot = try self.usageCache.load()
                        let generation = try self.database.query("SELECT revision FROM usage_revisions WHERE kind='ledger'").first?.integer("revision") ?? 0
                        return (snapshot,generation)
                    }
                    result = .success(try self.usageReportStore.load(snapshot:input.0,ledger:input.1,priceVersion:self.catalog.version,sourceKey:sourceKey))
                }
            } catch {result = .failure(error)}
            DispatchQueue.main.async {
                guard !self.stopped else {return}
                guard sourceKey == self.usageReportSourceKey(for:client) else {completion(.failure(AppFailure(L("数据源已变化，请重新打开报告", "The data source changed. Reopen the report."))));return}
                completion(result)
            }
        }
    }
    func markUsageReportPresented(_ day: String) {
        dataQueue.async { [weak self] in
            guard let self else { return }
            do { try self.usageReportStore.markPresented(day) }
            catch { DispatchQueue.main.async { self.errorMessage = error.localizedDescription } }
        }
    }
    func refreshUsage() {
        guard !scanning, !stopped, !paths.mock else { return }
        scanning = true; let roots = preferences.roots; let initial = loading; let operation = fetchActivity.begin()
        dataQueue.async { [weak self] in
            guard let self else { return }
            defer { DispatchQueue.main.async {self.fetchActivity.end(operation)} }
            if initial { self.publishUsage(finishLoading:false) }
            do {
                try self.indexer.scan(roots) { current,total in
                    if initial && (current == total || current % 50 == 0) { DispatchQueue.main.async { let text = "\(current) / \(total)"; if self.scanProgress != text { self.scanProgress = text } } }
                }
                self.publishUsage()
            } catch {self.estimator.interrupt(); DispatchQueue.main.async { self.errorMessage = error.localizedDescription; self.loading = false } }
            do {
                try self.claudeLedger.scan()
                self.publishClaude(); self.receiveClaudeQuota(self.claudeAllowance.snapshot())
            } catch {self.claudeEstimator.interrupt(); DispatchQueue.main.async { self.claudeWarning = error.localizedDescription } }
            DispatchQueue.main.async { self.scanning = false }
        }
    }
    private func receiveClaudeQuota(_ sample: Object) {
        guard !stopped, !paths.mock else {return}
        let now = Date(), observed = sample.number("captured_at").map {Date(timeIntervalSince1970:$0)}
        let fresh = observed.map {now.timeIntervalSince($0) >= 0 && now.timeIntervalSince($0) < 900} == true
        var quota = QuotaState(); quota.account = sample.object("account"); quota.updated = observed
        quota.windows = sample.objects("windows").compactMap {window in
            let reset = parsedDate(window["reset"])
            guard reset == nil || reset! > now else {return nil}
            return QuotaWindow(id:window.string("kind"),minutes:window.string("kind") == "five_hour" ? 300 : 10080,used:window.number("used"),reset:reset)
        }
        quota.applicable = !quota.windows.isEmpty
        if let stamp = sample.number("captured_at"), String(stamp)+quota.account.string("identityKey") != claudeLastSample {
            claudeLastSample = String(stamp)+quota.account.string("identityKey")
            if fresh, let week = quota.week, let used = week.used, let reset = week.reset {
                claudeEstimator.add(["timestamp":stamp,"used_percent":used,"reset_at":reset.timeIntervalSince1970,"account_key":quota.account.string("identityKey"),"plan_type":quota.account.string("planType").isEmpty ? "claude" : quota.account.string("planType"),"limit_id":"claude","sample_interval_seconds":60,"max_gap_seconds":90],intervalMinutes:max(1,preferences.analytics.integer("week_estimate_interval_minutes") ?? 10))
            } else {claudeEstimator.invalidate()}
        }
        let contexts = sample.objects("contexts")
        // Observation timestamps renew quota freshness, not request metadata.
        let contextKey = identity(contexts.map {point -> Object in var value = point; value.removeValue(forKey:"observed_at"); return value})
        if contextKey != claudeContextKey {
            do {
                let changed = try claudeLedger.observeContexts(contexts)
                // The append event may precede the normal file-change check.
                // scan() reuses unchanged cursors and reads only changed files.
                if changed {try claudeLedger.scan()}
                claudeContextKey = contextKey
                publishClaude()
            } catch {DispatchQueue.main.async {self.claudeWarning = error.localizedDescription}}
        }
        let estimates = (try? claudeEstimator.process(calls:[],priceVersion:claudeCatalog.version,accountKey:quota.account.string("identityKey"))) ?? []
        let estimateRevision = claudeEstimator.revision
        let key = quota.contentKey+String(fresh)
        DispatchQueue.main.async { [weak self] in
            guard let self, !self.stopped else {return}
            self.claudeObserved = observed
            if self.claudeQuotaKey != key {self.claudeQuotaKey = key; self.claudeQuota = quota}
            if self.claudeEstimateRevision != estimateRevision {self.claudeEstimateRevision = estimateRevision; self.claudeWeeklyEstimates = estimates}
            if let observed {self.claudeQuotaClock.updated = observed}
            self.onMenuDataChange?()
            self.publishWidget()
        }
    }
    private func publishClaude() {
        let now = Date(), calendar = Calendar.current
        let key = "claude_code|"+claudeLedger.root.path+"|"+String(claudeLedger.revision)+"|"+String(calendar.startOfDay(for:now).timeIntervalSince1970)+"|"+TimeZone.current.identifier+"|"+String(TimeZone.current.secondsFromGMT(for:now))
        let changed = key != claudePublishedKey || now >= claudeNextBoundary || now < claudeLastScan
        let surfaceChanged = changed || claudeSurfaceRevision != claudeLedger.surfaceRevision || now >= claudeTaskBoundary || now < claudeLastScan
        claudeLastScan = now
        if changed {
            claudePublishedKey = key
            let next = try? claudeLedger.database.query("SELECT min(timestamp) AS t FROM (SELECT timestamp FROM claude_calls UNION ALL SELECT timestamp FROM claude_requests) WHERE timestamp>?",[now.timeIntervalSince1970]).first?.number("t")
            claudeNextBoundary = next.map {Date(timeIntervalSince1970:$0)} ?? .distantFuture
        }
        let models = changed ? (try? claudeLedger.models()) : nil, warning = claudeLedger.warning
        var surfaceUpdate: AgentSurfaceData?
        if surfaceChanged, var next = try? claudeLedger.surface(now:now,includeUsage:changed) {
            if !changed {next.today = claudeSurfaceCache.today; next.days = claudeSurfaceCache.days}
            claudeTaskBoundary = next.nextBoundary; claudeSurfaceRevision = claudeLedger.surfaceRevision
            let signature = next.contentKey
            if signature != claudeSurfaceKey {claudeSurfaceKey = signature; surfaceUpdate = next}
            claudeSurfaceCache = next
        }
        let preparedSurface = surfaceUpdate
        let prices = claudeCatalog.rows, priceVersion = claudeCatalog.version, updated = claudeCatalog.updated, priceWarning = claudeCatalog.warning
        DispatchQueue.main.async { [weak self] in
            guard let self, !self.stopped else { return }
            if changed { self.claudeRevision = UUID(); if let models {self.claudeModels = models} }
            if self.claudeWarning != warning {self.claudeWarning = warning}
            if self.claudePricesRevision != priceVersion {self.claudePrices = prices; self.claudePricesRevision = priceVersion}
            if self.claudePriceUpdated != updated {self.claudePriceUpdated = updated}
            if self.claudePriceWarning != priceWarning {self.claudePriceWarning = priceWarning}
            if let preparedSurface {self.claudeSurface = preparedSurface; self.onMenuDataChange?()}
            self.claudeClock.updated = now
            self.publishWidget()
        }
    }
    private func publishUsage(finishLoading: Bool = true) {
        do {
            let result = try usageCache.load()
            let prices = catalog.rows, priceUpdated = catalog.updated, priceWarning = catalog.warning
            let models = paths.mock ? Set(["gpt-6-astra","gpt-5.6-sol","gpt-6-luna"]) : Set(objectFiles.read(accountRoot.appendingPathComponent("models_cache.json")).objects("models").filter { row in
                !row.flag("hidden") && row.string("visibility") != "hide" && (row["hidden"] is Bool || row.string("visibility") == "list")
            }.map {$0.string("slug",$0.string("model",$0.string("id")))}.filter {!$0.isEmpty})
            let estimates = try estimator.process(calls:result.calls,priceVersion:catalog.version)
            let estimatesKey = estimator.revision, priceVersion = catalog.version
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.stopped else { return }
                let changed = self.usage.revision != result.revision
                if changed {
                    self.overviewProjection.invalidate(for:result.revision)
                    self.trendProjection.invalidate(for:result.revision)
                    self.logProjection.invalidate(for:result.revision)
                    self.usage = result
                }
                self.clock.updated = result.updated
                if self.pricesRevision != priceVersion { self.prices = prices; self.pricesRevision = priceVersion }
                if self.modelIDs != models { self.modelIDs = models }
                if self.priceUpdated != priceUpdated { self.priceUpdated = priceUpdated }
                if self.priceWarning != priceWarning { self.priceWarning = priceWarning }
                if self.displayedEstimates != estimatesKey { self.weeklyEstimates = estimates; self.displayedEstimates = estimatesKey }
                if finishLoading && self.loading { self.loading = false }
                let taskChanged = finishLoading && self.taskRunning != result.hasRunningTask
                if taskChanged { self.taskRunning = result.hasRunningTask }
                if changed || taskChanged { self.onMenuDataChange?() }
                self.publishWidget()
                if self.reportsVisible && self.reportedCandidates == 0 && !result.chatCandidates.isEmpty { self.refreshReports(force:true) }
            }
        } catch { DispatchQueue.main.async { [weak self] in self?.errorMessage = error.localizedDescription; self?.loading = false } }
    }
    func refreshQuota() {
        guard !refreshing, !stopped, !paths.mock else { return }
        refreshing = true
        let operation = fetchActivity.begin()
        let hint = preferences.general.string("codex_path"), accountRoot = self.accountRoot
        accountQueue.async { [weak self] in
            guard let self else { return }
            defer {DispatchQueue.main.async {self.fetchActivity.end(operation)}}
            let identityBefore = try? BackendCredentials.load(root:accountRoot).account
            let savedMenuQuota = self.menuQuotaCacheLoaded ? nil : readObject(self.paths.data.appendingPathComponent("menu_quota_cache.json"))
            self.menuQuotaCacheLoaded = true
            var accountSupportsQuota: Bool?
            do {
                try self.client.start(hint:hint)
                let account = try self.client.request("account/read",["refreshToken":false]).object("account")
                var next = QuotaState(); next.account = account
                if let identityBefore { next.account["identityKey"] = identityBefore.key }
                accountSupportsQuota = account.string("type") == "chatgpt"
                let config = try self.client.request("config/read",["includeLayers":false]).object("config")
                let provider = config.string("model_provider","openai")
                next.applicable = account.string("type") == "chatgpt" && ["openai","codexio-upstream"].contains(provider)
                accountSupportsQuota = next.applicable
                if next.applicable {
                    let limits = try self.client.request("account/rateLimits/read",["excludeResetCreditDetails":false]); next.update(limits)
                    let identityAfter = try? BackendCredentials.load(root:accountRoot).account
                    guard identityBefore == identityAfter, limits.string("accountId").isEmpty || identityAfter == nil || limits.string("accountId") == identityAfter?.accountID else { self.client.close(); throw AppFailure(L("账户已变更，请重新刷新", "The account changed. Refresh again.")) }
                    if let identityAfter { next.account["identityKey"] = identityAfter.key }
                    if let week = next.week, let used = week.used, let reset = week.reset, let identity = identityAfter {
                        let buckets = limits.object("rateLimitsByLimitId")
                        let sampleInterval = max(30,min(300,self.preferences.general.number("refresh_interval_seconds") ?? 60))
                        let sample: Object = ["timestamp":Date().timeIntervalSince1970,"used_percent":used,"reset_at":reset.timeIntervalSince1970,"account_key":identity.key,"plan_type":account.string("planType"),"limit_id":"codex","sole_codex_pool":Set(buckets.keys) == Set(["codex"]),"sample_interval_seconds":sampleInterval,"max_gap_seconds":max(45,min(330,sampleInterval+30))]
                        let interval = self.preferences.analytics.integer("week_estimate_interval_minutes") ?? 10
                        self.dataQueue.async { self.estimator.add(sample,intervalMinutes:interval) }
                    }
                }
                else { next.error = L("此账户不提供 ChatGPT 额度", "ChatGPT limits are not available for this account"); self.dataQueue.async { self.estimator.invalidate() } }
                let pending = self.pendingResetOperations(account:next.account.string("identityKey"))
                for operation in pending where !next.credits.contains(where:{$0.id == operation.string("creditId")}) {
                    var credit = operation.object("credit"); credit["id"] = operation.string("creditId"); credit["status"] = "pending_confirmation"
                    next.credits.append(ResetCredit(raw:credit))
                }
                DispatchQueue.main.async {
                    guard !self.stopped else { return }
                    if self.quota.account.string("identityKey") != next.account.string("identityKey") || self.quota.account.string("planType") != next.account.string("planType") { self.invalidateReports(); self.pendingResets.removeAll() }
                    self.applyQuota(next,savedMenuQuota:savedMenuQuota)
                    let pendingIDs = Set(pending.map {$0.string("creditId")})
                    if self.pendingResets != pendingIDs { self.pendingResets = pendingIDs }
                    self.refreshing = false; self.publishWidget()
                    if self.reportsVisible { self.refreshReports() }
                }
            } catch {
                self.dataQueue.async {self.estimator.invalidate()}
                if !self.client.running { self.client.close() }
                let currentIdentity = try? BackendCredentials.load(root:accountRoot).account
                let owner = currentIdentity?.key ?? ""
                let applicable = identityBefore == currentIdentity ? accountSupportsQuota : nil
                DispatchQueue.main.async {
                    guard !self.stopped else { return }
                    var next = self.quota
                    if next.account.string("identityKey") != owner {
                        next = QuotaState(); next.account["identityKey"] = owner
                        self.invalidateReports(); self.pendingResets.removeAll()
                    }
                    if let applicable { next.applicable = applicable }
                    next.error = error.localizedDescription
                    self.applyQuota(next,savedMenuQuota:savedMenuQuota); self.refreshing = false; self.publishWidget()
                }
            }
        }
    }
    private func applyQuota(_ next: QuotaState,savedMenuQuota: Object? = nil) {
        var nextMenu = savedMenuQuota.map(MenuQuota.init(saved:)) ?? menuQuota
        nextMenu.receive(next)
        let menuChanged = menuQuota.contentKey != nextMenu.contentKey
        let key = next.contentKey, fresh = next.fresh
        let changed = key != displayedQuotaKey || fresh != displayedQuotaFresh || menuChanged
        if changed { objectWillChange.send() }
        quota = next; displayedQuotaKey = key; displayedQuotaFresh = fresh
        menuQuota = nextMenu
        if menuQuotaClock.updated != nextMenu.updated { menuQuotaClock.updated = nextMenu.updated }
        if !paths.mock, menuChanged || (!nextMenu.retained && nextMenu.updated != nil && Date().timeIntervalSince(menuQuotaWritten) >= 300) {
            menuQuotaWritten = Date()
            let payload = nextMenu.json, file = paths.data.appendingPathComponent("menu_quota_cache.json")
            snapshotQueue.async { try? atomicJSON(payload,to:file) }
        }
        if quotaClock.updated != next.updated { quotaClock.updated = next.updated }
        if changed { onQuotaChange?() }
    }
    func consumeReset(_ selected: ResetCredit,expectedAccount: String) {
        guard !paths.mock, !resetBusy, quota.fresh, selected.available || pendingResets.contains(selected.id), quota.credits.contains(where:{$0.id == selected.id}) else { return }
        resetBusy = true; actionMessage = nil
        let accountRoot = self.accountRoot
        accountQueue.async { [weak self] in
            guard let self else { return }
            var operationKey = ""
            do {
                let account = try BackendCredentials.load(root:accountRoot).account
                guard !expectedAccount.isEmpty, account.key == expectedAccount else { throw AppFailure(L("账户已变更，请重新选择重置", "The account changed. Select the reset again.")) }
                operationKey = "reset-operation:"+account.key+":"+identity(selected.id)
                let prior = try self.database.query("SELECT key FROM usage_meta WHERE key LIKE ?",["reset-operation:"+account.key+":%"])
                guard !prior.contains(where:{$0.string("key") != operationKey}) else { throw AppFailure(L("请先确认上一次重置的结果", "Confirm the previous reset result first")) }
                var operation = self.database.object("usage_meta",key:operationKey)
                let limits = try self.client.request("account/rateLimits/read",["excludeResetCreditDetails":false])
                guard limits.string("accountId").isEmpty || limits.string("accountId") == account.accountID else { self.client.close(); throw AppFailure(L("账户已变更，请重新选择重置", "The account changed. Select the reset again.")) }
                if operation.isEmpty {
                    let available = limits.object("rateLimitResetCredits").objects("credits").map(ResetCredit.init)
                    guard available.contains(where:{$0.id == selected.id && $0.available}) else { throw AppFailure(L("这次重置已不可用", "This reset is no longer available")) }
                }
                if operation.isEmpty { operation = ["idempotencyKey":UUID().uuidString,"creditId":selected.id,"credit":selected.raw,"account":account.key,"createdAt":iso()]; try self.database.put("usage_meta",key:operationKey,value:operation) }
                guard try BackendCredentials.load(root:accountRoot).account == account else { throw AppFailure(L("账户已变更，请重新选择", "The account changed. Select the reset again.")) }
                let result = try self.client.request("account/rateLimitResetCredit/consume",["creditId":selected.id,"idempotencyKey":operation.string("idempotencyKey")])
                let outcome = result.string("outcome")
                guard ["reset","alreadyRedeemed","nothingToReset","noCredit"].contains(outcome) else { throw AppFailure(L("结果尚未确认，可重试本次操作", "The result is not confirmed. Retry this operation.")) }
                try self.database.run("DELETE FROM usage_meta WHERE key=?",[operationKey])
                DispatchQueue.main.async {
                    guard !self.stopped else { return }; self.resetBusy = false
                    guard self.quota.account.string("identityKey") == expectedAccount else { self.refreshQuota(); return }
                    self.pendingResets.remove(selected.id)
                    self.actionMessage = outcome == "nothingToReset" ? L("当前没有可重置的额度窗口", "No rate-limit window needs a reset") : outcome == "noCredit" ? L("这次重置已不可用", "This reset is no longer available") : L("重置已提交，正在同步额度", "Reset submitted. Refreshing limits.")
                    self.invalidateReports(); self.refreshQuota(); self.refreshReports(force:true)
                }
            } catch {
                let pending = !operationKey.isEmpty && !self.database.object("usage_meta",key:operationKey).isEmpty
                DispatchQueue.main.async {
                    guard !self.stopped else { return }; self.resetBusy = false
                    guard self.quota.account.string("identityKey") == expectedAccount else { self.refreshQuota(); return }
                    if pending { self.pendingResets.insert(selected.id) }; self.actionMessage = error.localizedDescription
                }
            }
        }
    }
    func invalidateReports() {
        reportGeneration = UUID(); reportLoadedAt = nil; reportedCandidates = 0; reportInFlight = false
        if !planHistory.isEmpty { planHistory = [:] }; if !chatUsage.isEmpty { chatUsage = [:] }
        if reportError != nil { reportError = nil }; if reportsLoading { reportsLoading = false }
    }
    private var reportsVisible: Bool { mainWindowVisible && selectedPage == "subscription" && pageClient == .codex }
    func refreshVisibleReports() { if reportsVisible { refreshReports() } }
    private func pendingResetOperations(account: String) -> [Object] {
        guard !account.isEmpty else { return [] }
        return ((try? database.query("SELECT data FROM usage_meta WHERE key LIKE ?",["reset-operation:"+account+":%"])) ?? []).map {jsonObject(Data($0.string("data").utf8))}.filter {!$0.string("creditId").isEmpty}
    }
    func refreshReports(force: Bool = false) {
        guard !paths.mock, mainWindowVisible, !reportInFlight, !stopped, !quota.account.isEmpty else { return }
        guard Date() >= reportRetryAt else { return }
        if !force, let date = reportLoadedAt, Date().timeIntervalSince(date) < 60 { return }
        guard quota.applicable else { return }
        reportInFlight = true
        let fetch = fetchActivity.begin()
        if force || (planHistory.isEmpty && chatUsage.isEmpty) { reportsLoading = true }
        if force && reportError != nil { reportError = nil }
        let generation = UUID(); reportGeneration = generation
        let candidates = usage.chatCandidates, root = accountRoot, expectedAccount = quota.account.string("identityKey")
        reportQueue.async { [weak self] in
            guard let self else { return }
            defer {DispatchQueue.main.async {self.fetchActivity.end(fetch)}}
            let backend = AccountAnalytics(root:root)
            var history: Object = [:], chats: Object = [:], errors: [String] = [], retryAt = Date.distantPast
            do {
                guard try BackendCredentials.load(root:root).account.key == expectedAccount else { throw AppFailure(L("账户已变更，请重新刷新", "The account changed. Refresh again.")) }
                history = try backend.plan()
            } catch { errors.append(error.localizedDescription); if let failure = error as? HTTPFailure, failure.code == 429 { retryAt = Date().addingTimeInterval(max(60,failure.retryAfter ?? 60)) } }
            if retryAt == .distantPast {
                do { if !candidates.isEmpty { chats = try backend.chats(candidates) } } catch { errors.append(error.localizedDescription); if let failure = error as? HTTPFailure, failure.code == 429 { retryAt = Date().addingTimeInterval(max(60,failure.retryAfter ?? 60)) } }
            }
            let identityMatches = (try? BackendCredentials.load(root:root).account.key) == expectedAccount
            DispatchQueue.main.async {
                guard !self.stopped, self.reportGeneration == generation, self.quota.account.string("identityKey") == expectedAccount else { return }
                guard identityMatches else { self.invalidateReports(); self.refreshQuota(); return }
                if !history.isEmpty && !NSDictionary(dictionary:history).isEqual(to:self.planHistory) { self.planHistory = history }
                if !chats.isEmpty && !NSDictionary(dictionary:chats).isEqual(to:self.chatUsage) { self.chatUsage = chats }
                if self.reportsLoading { self.reportsLoading = false }; self.reportInFlight = false; self.reportLoadedAt = Date()
                self.reportRetryAt = retryAt; self.reportedCandidates = candidates.count
                let error = errors.isEmpty ? nil : Array(Set(errors)).sorted().joined(separator:" · ")
                if self.reportError != error { self.reportError = error }
            }
        }
    }
    func setPreference(_ key: String, _ value: Any, general: Bool = false) {
        let value: Any = key == "menu_bar_fields" && !general ? MenuBarField.normalize(value as? [String] ?? MenuBarField.defaults) : value
        let previous = general ? preferences.general[key] : preferences.analytics[key]
        if let previous, jsonString(previous) == jsonString(value) { return }
        if general { preferences.general[key] = value } else { preferences.analytics[key] = value }
        do { try preferences.save() } catch { errorMessage = error.localizedDescription }
        if key == "theme" { theme = preferences.analytics.string("theme","system") }
        if key == "menu_bar_visible" { menuVisible = preferences.analytics.flag("menu_bar_visible",true) }
        if key == "menu_bar_fields" { menuFields = MenuBarField.normalize(preferences.analytics["menu_bar_fields"] as? [String] ?? MenuBarField.defaults) }
        if key == "menu_bar_agent" {menuClient = DesktopClient(rawValue:preferences.analytics.string("menu_bar_agent")) ?? .codex; onMenuDataChange?()}
        if ["usage_refresh_interval_seconds","refresh_interval_seconds"].contains(key) { configureTimers() }
        if key == "week_estimate_interval_minutes" {dataQueue.async {self.estimator.invalidate();self.claudeEstimator.invalidate()}}
        if ["theme","menu_bar_visible","menu_bar_fields"].contains(key) { onSettingsChange?() }
        objectWillChange.send()
    }
    func applyAppIcon(_ selected: String, persist: Bool = true) {
        let id = Branding.iconID(selected)
        guard !appIconApplying, !persist || id != appIconStyle else { return }
        appIconApplying = true
        brandingQueue.async { [weak self] in
            guard let self else { return }
            do {
                Branding.prepareSmallImages()
                let image = try Branding.appIcon(id), dock = Branding.dockIcon(image,id:id)
                if persist {
                    let old = self.preferences.analytics.string("app_icon","main")
                    self.preferences.analytics["app_icon"] = id
                    do { try self.preferences.save() }
                    catch { self.preferences.analytics["app_icon"] = old; throw error }
                }
                DispatchQueue.main.async {
                    guard !self.stopped else { return }
                    self.appIconStyle = id; self.appLogoImage = image; self.appIconApplying = false
                    NSApp.applicationIconImage = dock
                    self.onMenuDataChange?()
                }
            } catch {
                DispatchQueue.main.async { self.appIconApplying = false; self.errorMessage = error.localizedDescription; self.onMenuDataChange?() }
            }
        }
    }
    func toggleSidebar() { sidebarVisible.toggle(); persistSidebar() }
    func persistSidebar() {
        preferences.analytics["native_sidebar_width"] = sidebarWidth
        preferences.analytics["sidebar_collapsed"] = !sidebarVisible
        do { try preferences.save() } catch { errorMessage = error.localizedDescription }
    }
    func rescan() {
        guard !paths.mock, !scanning else { return }
        dataQueue.async { [weak self] in
            do { try self?.indexer.rescan(); DispatchQueue.main.async { self?.refreshUsage() } }
            catch { DispatchQueue.main.async { self?.errorMessage = error.localizedDescription } }
        }
    }
    func syncPrices() {
        guard !paths.mock, !stopped else { return }
        let operation = fetchActivity.begin(), selectedClient = pageClient
        dataQueue.async { [weak self] in
            guard let self else { return }
            defer {DispatchQueue.main.async {self.fetchActivity.end(operation)}}
            do {
                if selectedClient == .claudeCode {try self.claudeCatalog.sync(force:true); try self.claudeLedger.refreshPricing(); self.publishClaude()}
                else {try self.catalog.sync(force:true); self.publishUsage()}
            }
            catch { DispatchQueue.main.async { self.errorMessage = error.localizedDescription } }
        }
    }
    func overridePrice(model: String, rates: Object?) {
        let selectedClient = pageClient
        dataQueue.async { [weak self] in
            guard let self else {return}
            do {
                if selectedClient == .claudeCode {try self.claudeCatalog.setOverride(model:model,rates:rates); try self.claudeLedger.refreshPricing(); self.publishClaude()}
                else {try self.catalog.setOverride(model:model,rates:rates); self.publishUsage()}
            }
            catch { DispatchQueue.main.async { self.errorMessage = error.localizedDescription } }
        }
    }
    func publishWidget() {
        guard !paths.mock, !stopped else { return }
        mobileSync.update(usage,quota:menuQuota,observed:clock.updated)
        if mobileSync.enabled,!claudeMobilePending,!paths.mock {
            claudeMobilePending=true
            let sync=mobileSync,name=mobileSync.deviceName,surface=claudeSurface,quota=claudeQuota,fresh=claudeQuotaFresh,observed=claudeClock.updated
            dataQueue.async{[weak self] in
                guard let self else{return}
                let now=Date(),dayKey=String(Calendar.current.startOfDay(for:now).timeIntervalSince1970)+TimeZone.current.identifier
                let key=String(self.claudeLedger.revision)+"|"+dayKey
                let timeChanged=dayKey != self.claudeMobileDayKey || now < self.claudeMobileLastObserved || now >= self.claudeMobileTimeBoundary
                self.claudeMobileLastObserved=now
                if self.claudeMobileSnapshot == nil || timeChanged || (key != self.claudeMobileKey && now >= self.claudeMobileNext){
                    if var prepared=try? self.claudeLedger.mobileSnapshot(){prepared.revision=key+"|"+String(now.timeIntervalSince1970);self.claudeMobileSnapshot=prepared;self.claudeMobileKey=key;self.claudeMobileDayKey=dayKey;self.claudeMobileTimeBoundary=self.claudeNextBoundary;self.claudeMobileNext=now.addingTimeInterval(60)}
                }
                if let snapshot=self.claudeMobileSnapshot {sync.updateClaude(snapshot,surface:surface,quota:quota,fresh:fresh,observed:observed,name:name)}
                DispatchQueue.main.async{self.claudeMobilePending=false}
            }
        }
        let codex = AgentWidgetPayload.make(request:usage.widgetRequest,today:usage.summaries["today"],quota:quota,fresh:quota.fresh,observed:clock.updated)
        let claude = AgentWidgetPayload.make(request:claudeSurface.request,today:claudeSurface.ready ? claudeSurface.today : nil,quota:claudeQuota,fresh:claudeQuotaFresh,observed:claudeClock.updated)
        // Retain the Codex root fields for older extensions during atomic host
        // replacement. Both new source variants come from this one host snapshot.
        var snapshot = codex
        snapshot["schema"] = 1; snapshot["updated_at"] = Date().timeIntervalSince1970
        snapshot["host_running"] = true; snapshot["host_pid"] = Int(getpid())
        snapshot["agents"] = ["chatgpt":codex,"claude":claude]
        var content = snapshot; content.removeValue(forKey:"updated_at")
        var quotaContent = content.object("quota"); quotaContent.removeValue(forKey:"updated_at"); content["quota"] = quotaContent
        var agents = content.object("agents")
        for key in ["chatgpt","claude"] {
            var agent = agents.object(key); agent.removeValue(forKey:"updated_at")
            var allowance = agent.object("quota"); allowance.removeValue(forKey:"updated_at"); agent["quota"] = allowance; agents[key] = agent
        }
        content["agents"] = agents
        let signature = identity(content)
        guard signature != snapshotSignature || Date().timeIntervalSince(snapshotWritten) >= 300 else { return }
        snapshotSignature = signature; snapshotWritten = Date()
        snapshotQueue.async { [weak self] in
            do {
                try self?.writeWidget(snapshot)
            } catch { DispatchQueue.main.async { self?.errorMessage = error.localizedDescription } }
        }
    }
    private func seedMock() {
        dataQueue.async { [weak self] in
            guard let self else { return }
            do {
                let today = Calendar.current.startOfDay(for:Date()), now = Date()
                let titles = [L("完成 Codexio 原生重构", "Finish the Codexio native rebuild"),L("优化日志详情与用量统计", "Refine log details and usage statistics"),L("适配新版 Codex 客户端", "Support the updated Codex client"),L("整理菜单栏和小组件设计", "Refine the menu bar and widgets")]
                for day in 0..<365 where day < 6 || day % 7 != 3 {
                    for call in 0..<(day == 0 ? 4 : 1) {
                        let date = Calendar.current.date(byAdding:.day,value:-day,to:today)!.addingTimeInterval(day == 0 ? max(1,min(now.timeIntervalSince(today)-10,Double(call+1)*1800)) : Double(9+day%10)*3600)
                        let session = "mock-chat-\(day == 0 ? call : day%4)", turn = "mock-turn-\(day)-\(call)", title = titles[day == 0 ? call : day%4]
                        let input = 30000+(day*1543)%270000+call*13000, output = 6000+(day*357)%42000+call*1500
                        try self.database.writeRecord(["id":"response:\(session):\(turn)","session_id":session,"turn_id":turn,"timestamp":iso(date),"model":(day+call)%2 == 0 ? "gpt-6-astra" : "gpt-5.6-sol","provider":"openai","reasoning_effort":day%4 == 0 ? "max" : "high","service_tier":(day+call)%3 == 0 ? "priority" : "default","quality":"response","input_tokens":input,"cached_input_tokens":input/2,"cache_write_input_tokens":0,"output_tokens":output,"reasoning_output_tokens":output/3,"total_tokens":input+output,"prompt_preview":title,"output_preview":L("已完成原生界面与本机统计调整，保留每条调用的模型、速度、推理强度和费用明细。", "Native UI and local statistics are updated, with model, speed, reasoning and cost details retained for every call."),"source_id":"local","duration_ms":138000])
                        try self.database.writeTurn(["id":"turn:\(session):\(turn)","session_id":session,"turn_id":turn,"started_at":iso(date.addingTimeInterval(-138)),"ended_at":iso(date),"started_inferred":false,"verified":true,"status":"completed","prompt_preview":title,"output_preview":L("页面和统计已完成。", "Pages and statistics are ready."),"source_ids":["local"]])
                    }
                }
                self.publishUsage()
                DispatchQueue.main.async {
                    var quota = QuotaState(); quota.account = ["type":"chatgpt","email":"demo@example.invalid","planType":"pro","identityKey":"mock-account"]
                    quota.update(["rateLimits":["primary":["usedPercent":26,"windowDurationMins":300,"resetsAt":now.addingTimeInterval(7200).timeIntervalSince1970],"secondary":["usedPercent":77,"windowDurationMins":10080,"resetsAt":now.addingTimeInterval(432000).timeIntervalSince1970]],"rateLimitResetCredits":["availableCount":2,"credits":[["id":"mock-reset-1","title":L("重置 1", "Reset 1"),"status":"available","resetType":"codexRateLimits","expiresAt":now.addingTimeInterval(864000).timeIntervalSince1970],["id":"mock-reset-2","title":L("重置 2", "Reset 2"),"status":"available","resetType":"codexRateLimits","expiresAt":now.addingTimeInterval(1728000).timeIntervalSince1970]]]])
                    self.applyQuota(quota); self.priceUpdated = now
                    var claudeQuota = QuotaState()
                    claudeQuota.account = ["identityKey":"mock-claude-account","planType":"max"]
                    claudeQuota.updated = now; claudeQuota.applicable = true
                    claudeQuota.windows = [QuotaWindow(id:"five_hour",minutes:300,used:18,reset:now.addingTimeInterval(4200)),QuotaWindow(id:"seven_day",minutes:10080,used:37,reset:now.addingTimeInterval(556800))]
                    self.claudeQuota = claudeQuota; self.claudeObserved = now; self.claudeQuotaClock.updated = now; self.claudeClock.updated = now
                    var summary = UsageSummary(); summary.tokens = 36_180_000; summary.cost = 42.86; summary.requests = 9; summary.calls = 12; summary.input = 35_360_000; summary.cached = 33_170_000; summary.output = 824_000
                    var days = Array(self.usage.activity.days.suffix(7)).map {day in DayUsage(date:day.date,tokens:day.tokens.map {$0/2},cost:day.cost.map {$0*0.4},calls:day.calls,requests:day.requests)}
                    if let last = days.last {days[days.count-1] = DayUsage(date:last.date,tokens:summary.tokens,cost:summary.cost,calls:summary.calls,requests:summary.requests)}
                    let request = UsageRow(raw:["id":"mock-claude-request","timestamp":iso(now.addingTimeInterval(-128)),"prompt_preview":L("完善 Claude 任务、小组件与菜单栏数据", "Connect Claude tasks, widgets and menu usage"),"model":"Opus 5.5","reasoning_effort":"high","model_context_window":1_000_000,"input_tokens":814000,"output_tokens":28600,"total_tokens":842600,"cached_input_tokens":763500,"cost_usd":0.28,"duration_ms":128000,"status":"completed","local_origin":true])
                    self.claudeSurface = AgentSurfaceData(ready:true,request:request,today:summary,days:days)
                    self.preferences.analytics["subscription_profile"] = ["plan":"ChatGPT Pro","price_usd":200.0,"renewal_date":"2026-10-27"]
                    self.planHistory = ["data_as_of":iso(now),"coverage_complete":true,"approximate":false,"periods":[["id":"mock-period","window_minutes":10080,"starts_at":iso(now.addingTimeInterval(-172800)),"ends_at":iso(now.addingTimeInterval(432000)),"accounting_complete":true,"used_basis_points":7700,"breakdowns":[["dimension":"model","rows":[["key":"gpt-6-astra","basis_points":4160],["key":"gpt-5.6-sol","basis_points":2580],["key":"gpt-6-luna","basis_points":960]]]]]]]
                    self.chatUsage = ["data_as_of":iso(now),"threads":(0..<4).map { index -> Object in
                        let percent = [11.52,8.31,5.83,0.0077][index]
                        return ["thread_id":"mock-chat-\(index)","data_status":index == 3 ? "partial" : "available","weekly_limit_percent":percent,"balance_usage_credits":"0","groups":[["model":"gpt-6-astra","reasoning_effort":"max","speed":"fast","weekly_limit_percent":percent*0.87,"balance_usage_credits":"0"],["model":"gpt-5.6-sol","reasoning_effort":"low","speed":"standard","weekly_limit_percent":percent*0.13,"balance_usage_credits":"0"]]]
                    }]
                    self.onQuotaChange?()
                }
            } catch {self.estimator.interrupt(); DispatchQueue.main.async { self.errorMessage = error.localizedDescription; self.loading = false } }
        }
    }
}
