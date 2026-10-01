import Foundation
import Combine
import WidgetKit
import AppKit

final class AppState: ObservableObject {
    let paths: AppPaths
    let preferences: Preferences
    let database: Database
    let catalog: PricingCatalog
    let indexer: UsageIndexer
    let estimator: WeeklyEstimator
    let client = CodexClient()
    let clock = ScanClock()
    let fetchActivity = FetchActivity()
    let quotaClock = ScanClock()
    let menuQuotaClock = ScanClock()
    lazy var mobileSync = MobileSync(paths: paths, detailProvider: { [weak self] id in try self?.database.requestMessageDetail(id) }, detailPreparer: { [weak self] ids in try self?.database.prepareRequestMessageDetails(ids) })
    var usageReportDirectory: URL { paths.data.appendingPathComponent("Reports", isDirectory: true) }
    private lazy var usageReportStore = UsageReportStore(directory: usageReportDirectory)
    private let brandingQueue = DispatchQueue(label:"com.wujuhu.codexio.branding",qos:.utility)
    private lazy var usageCache = UsageSnapshotCache(database:database,catalog:catalog)
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
        database = try Database(paths.database); catalog = PricingCatalog(paths.pricing); indexer = UsageIndexer(database); estimator = try WeeklyEstimator(database)
        theme = preferences.analytics.string("theme","system")
        appIconStyle = Branding.iconID(preferences.analytics.string("app_icon","main"))
        sidebarVisible = !preferences.analytics.flag("sidebar_collapsed")
        sidebarWidth = min(320,max(140,preferences.analytics.number("native_sidebar_width") ?? preferences.analytics.number("sidebar_width") ?? 238))
        menuVisible = preferences.analytics.flag("menu_bar_visible",true)
        menuFields = MenuBarField.normalize(preferences.analytics["menu_bar_fields"] as? [String] ?? MenuBarField.defaults)
        client.onNotification = { [weak self] method, _ in
            guard ["account/rateLimits/updated","account/updated"].contains(method) else { return }
            DispatchQueue.main.async {
                if method == "account/updated" { self?.invalidateReports() }
                self?.refreshQuota()
            }
        }
    }
    func start() {
        if paths.mock { seedMock(); return }
        if UserDefaults.standard.bool(forKey:"codexio.mobile.enabled") { mobileSync.start() }
        refreshUsage(); refreshQuota()
        configureTimers()
        let priceFetch = fetchActivity.begin()
        dataQueue.async { [weak self] in
            guard let self else { return }
            defer { DispatchQueue.main.async {self.fetchActivity.end(priceFetch)} }
            guard self.preferences.analytics.flag("auto_sync_prices",true) else { return }
            do { try self.catalog.sync(); self.publishUsage() }
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
            self.dataQueue.async { defer {DispatchQueue.main.async {self.fetchActivity.end(operation)}}; try? self.catalog.sync(); self.publishUsage() }
        }
        priceTimer?.tolerance = 180
    }
    func stop() {
        if !paths.mock { mobileSync.stop() }
        stopped = true; scanTimer?.invalidate(); quotaTimer?.invalidate(); priceTimer?.invalidate(); indexer.cancel(); client.close()
        fetchActivity.clear()
        reportGeneration = UUID()
    }
    func finishQuit(completion: @escaping (Error?) -> Void) {
        quitGeneration = UUID()
        stop()
        snapshotQueue.async { [self] in
            client.shutdown()
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
    func refresh() { refreshUsage(); refreshQuota(); if selectedPage == "subscription" || usageSection == "threads" { refreshReports(force:true) } }
    private var usageReportSourceKey: String { preferences.roots.map(\.path).sorted().joined(separator: "\n") + "\n" + accountRoot.path }
    func prepareUsageReports(completion: @escaping (Result<UsageReportCollection, Error>) -> Void) {
        let sourceKey = usageReportSourceKey
        dataQueue.async { [weak self] in
            guard let self else { return }
            let result: Result<UsageReportCollection, Error>
            do {
                let input = try self.database.transaction { () throws -> (UsageSnapshot, Int) in
                    let snapshot = try self.usageCache.load()
                    let generation = try self.database.query("SELECT revision FROM usage_revisions WHERE kind='ledger'").first?.integer("revision") ?? 0
                    return (snapshot, generation)
                }
                result = .success(try self.usageReportStore.load(snapshot: input.0, ledger: input.1, priceVersion: self.catalog.version, sourceKey: sourceKey))
            } catch { result = .failure(error) }
            DispatchQueue.main.async {
                guard !self.stopped else { return }
                guard sourceKey == self.usageReportSourceKey else { completion(.failure(AppFailure(L("数据源已变化，请重新打开报告", "The data source changed. Reopen the report.")))); return }
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
            } catch { DispatchQueue.main.async { self.errorMessage = error.localizedDescription; self.loading = false } }
            DispatchQueue.main.async { self.scanning = false }
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
                        let sample: Object = ["timestamp":Date().timeIntervalSince1970,"used_percent":used,"reset_at":reset.timeIntervalSince1970,"account_key":identity.key,"plan_type":account.string("planType"),"limit_id":"codex","sole_codex_pool":Set(buckets.keys) == Set(["codex"])]
                        let interval = self.preferences.analytics.integer("week_estimate_interval_minutes") ?? 30
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
    private var reportsVisible: Bool { mainWindowVisible && (selectedPage == "subscription" || (selectedPage == "trends" && usageSection == "threads")) }
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
        if ["usage_refresh_interval_seconds","refresh_interval_seconds"].contains(key) { configureTimers() }
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
        let operation = fetchActivity.begin()
        dataQueue.async { [weak self] in
            guard let self else { return }
            defer {DispatchQueue.main.async {self.fetchActivity.end(operation)}}
            do { try self.catalog.sync(force:true); self.publishUsage() }
            catch { DispatchQueue.main.async { self.errorMessage = error.localizedDescription } }
        }
    }
    func overridePrice(model: String, rates: Object?) {
        dataQueue.async { [weak self] in
            do { try self?.catalog.setOverride(model:model,rates:rates); self?.publishUsage() }
            catch { DispatchQueue.main.async { self?.errorMessage = error.localizedDescription } }
        }
    }
    func publishWidget() {
        guard !paths.mock, !stopped else { return }
        mobileSync.update(usage,quota:menuQuota,observed:clock.updated)
        var request: Any = NSNull()
        if let selected = usage.widgetRequest {
            var value: Object = ["id":selected.id,"prompt":String(selected.raw.string("prompt_preview").prefix(240)),"model":modelName(selected.raw.string("model")),"reasoning_effort":logEffortName(selected.raw.string("reasoning_effort")),"duration_running":selected.raw.flag("duration_running")]
            for key in ["cost_usd","duration_ms","input_tokens","output_tokens","cached_input_tokens","cache_hit_rate","service_tier","model_context_window"] { value[key] = selected.raw[key] ?? NSNull() }
            if let start = parsedDate(selected.raw["duration_started_at"]) { value["duration_started_at"] = start.timeIntervalSince1970-(selected.raw.number("duration_base_ms") ?? 0)/1000 }
            for key in ["input_tokens","output_tokens","cached_input_tokens"] where value[key] is NSNull { value[key] = 0 }
            request = value
        }
        let today = usage.summaries["today"] ?? UsageSummary()
        let snapshot: Object = ["schema":1,"updated_at":Date().timeIntervalSince1970,"host_running":true,"host_pid":Int(getpid()),"request":request,"today":["cost_usd":today.cost as Any? ?? NSNull(),"tokens":today.tokens as Any? ?? NSNull(),"requests":today.requests,"cache_hit_rate":today.cacheRate as Any? ?? NSNull()],"quota":["applicable":quota.applicable,"updated_at":quota.updated?.timeIntervalSince1970 as Any? ?? NSNull(),"five_hour":(quota.fresh ? quota.five?.remaining : nil) as Any? ?? NSNull(),"week":(quota.fresh ? quota.week?.remaining : nil) as Any? ?? NSNull(),"has_five_hour":quota.five != nil,"has_week":quota.week != nil,"five_hour_reset_at":(quota.fresh ? quota.five?.reset?.timeIntervalSince1970 : nil) as Any? ?? NSNull(),"week_reset_at":(quota.fresh ? quota.week?.reset?.timeIntervalSince1970 : nil) as Any? ?? NSNull(),"reset_count":(quota.fresh ? quota.availableCount : nil) as Any? ?? NSNull()]]
        var content = snapshot; content.removeValue(forKey:"updated_at")
        var quotaContent = content.object("quota"); quotaContent.removeValue(forKey:"updated_at"); content["quota"] = quotaContent
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
                    self.preferences.analytics["subscription_profile"] = ["plan":"ChatGPT Pro","price_usd":200.0,"renewal_date":"2026-10-27"]
                    self.planHistory = ["data_as_of":iso(now),"coverage_complete":true,"approximate":false,"periods":[["id":"mock-period","window_minutes":10080,"starts_at":iso(now.addingTimeInterval(-172800)),"ends_at":iso(now.addingTimeInterval(432000)),"accounting_complete":true,"used_basis_points":7700,"breakdowns":[["dimension":"model","rows":[["key":"gpt-6-astra","basis_points":4160],["key":"gpt-5.6-sol","basis_points":2580],["key":"gpt-6-luna","basis_points":960]]]]]]]
                    self.chatUsage = ["data_as_of":iso(now),"threads":(0..<4).map { index -> Object in
                        let percent = [11.52,8.31,5.83,0.0077][index]
                        return ["thread_id":"mock-chat-\(index)","data_status":index == 3 ? "partial" : "available","weekly_limit_percent":percent,"balance_usage_credits":"0","groups":[["model":"gpt-6-astra","reasoning_effort":"max","speed":"fast","weekly_limit_percent":percent*0.87,"balance_usage_credits":"0"],["model":"gpt-5.6-sol","reasoning_effort":"low","speed":"standard","weekly_limit_percent":percent*0.13,"balance_usage_credits":"0"]]]
                    }]
                    self.onQuotaChange?()
                }
            } catch { DispatchQueue.main.async { self.errorMessage = error.localizedDescription; self.loading = false } }
        }
    }
}
