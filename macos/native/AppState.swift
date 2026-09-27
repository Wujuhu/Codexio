import Foundation
import Combine
import WidgetKit

final class AppState: ObservableObject {
    let paths: AppPaths
    let preferences: Preferences
    let database: Database
    let catalog: PricingCatalog
    let indexer: UsageIndexer
    let estimator: WeeklyEstimator
    let client = CodexClient()
    let dataQueue = DispatchQueue(label:"com.wujuhu.codexio.data",qos:.utility)
    let accountQueue = DispatchQueue(label:"com.wujuhu.codexio.account",qos:.utility)
    let reportQueue = DispatchQueue(label:"com.wujuhu.codexio.reports",qos:.utility)
    @Published var usage = UsageSnapshot()
    @Published var quota = QuotaState()
    @Published var prices: [PriceRow] = []
    @Published var modelIDs = Set<String>()
    @Published var priceUpdated: Date?
    @Published var priceWarning: String?
    @Published var weeklyEstimates: [Object] = []
    @Published var selectedPage = "overview"
    @Published var settingsSection = "appearance"
    @Published var usageSection = "activity"
    @Published var theme = "system"
    @Published var sidebarVisible = true
    @Published var menuVisible = true
    @Published var menuContent = "week"
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
    private var reportGeneration = UUID()
    private var reportLoadedAt: Date?
    private var reportRetryAt = Date.distantPast
    private var reportedCandidates = 0
    private var snapshotSignature = ""
    private var snapshotWritten = Date.distantPast
    private var computationKey = ""
    private var computedUsage: UsageSnapshot?
    private var computationExpires = Date.distantPast
    var onQuotaChange: (() -> Void)?
    var onSettingsChange: (() -> Void)?
    var onCheckUpdate: (() -> Void)?
    var onInstallUpdate: (() -> Void)?
    var onUpstreamChange: ((Bool) -> Void)?
    var onOpenWindow: (() -> Void)?
    var onQuit: (() -> Void)?
    var onCancelQuit: (() -> Void)?
    var onRestartCodex: ((@escaping () -> Void) -> Void)?
    var accountRoot: URL { URL(fileURLWithPath:ProcessInfo.processInfo.environment["CODEX_HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex").path) }

    init(paths: AppPaths) throws {
        self.paths = paths; preferences = Preferences(paths)
        database = try Database(paths.database); catalog = PricingCatalog(paths.pricing); indexer = UsageIndexer(database); estimator = try WeeklyEstimator(database)
        theme = preferences.analytics.string("theme","system")
        sidebarVisible = !preferences.analytics.flag("sidebar_collapsed")
        menuVisible = preferences.analytics.flag("menu_bar_visible",true)
        menuContent = preferences.analytics.string("menu_bar_content","week")
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
        refreshUsage(); refreshQuota()
        configureTimers()
        dataQueue.async { [weak self] in
            guard let self, self.preferences.analytics.flag("auto_sync_prices",true) else { return }
            do { try self.catalog.sync(); self.publishUsage() }
            catch { DispatchQueue.main.async { self.errorMessage = L("价格同步失败，保留已有价格", "Price sync failed; saved prices are retained") } }
        }
    }
    func configureTimers() {
        scanTimer?.invalidate(); quotaTimer?.invalidate(); priceTimer?.invalidate()
        guard !paths.mock else { return }
        scanTimer = Timer.scheduledTimer(withTimeInterval:max(5,preferences.analytics.number("usage_refresh_interval_seconds") ?? 10),repeats:true) { [weak self] _ in self?.refreshUsage() }
        quotaTimer = Timer.scheduledTimer(withTimeInterval:max(30,preferences.general.number("refresh_interval_seconds") ?? 60),repeats:true) { [weak self] _ in self?.refreshQuota() }
        priceTimer = Timer.scheduledTimer(withTimeInterval:3600,repeats:true) { [weak self] _ in
            guard let self, self.preferences.analytics.flag("auto_sync_prices",true) else { return }
            self.dataQueue.async { try? self.catalog.sync(); self.publishUsage() }
        }
    }
    func stop() {
        stopped = true; scanTimer?.invalidate(); quotaTimer?.invalidate(); priceTimer?.invalidate(); indexer.cancel(); client.close()
        reportGeneration = UUID()
    }
    func refresh() { refreshUsage(); refreshQuota(); if selectedPage == "subscription" || usageSection == "threads" { refreshReports(force:true) } }
    func refreshUsage() {
        guard !scanning, !stopped, !paths.mock else { return }
        scanning = true; let roots = preferences.roots; let initial = loading
        dataQueue.async { [weak self] in
            guard let self else { return }
            if initial { self.publishUsage(finishLoading:false) }
            do {
                try self.indexer.scan(roots) { current,total in
                    if current == total || current % 50 == 0 { DispatchQueue.main.async { self.scanProgress = "\(current) / \(total)" } }
                }
                self.publishUsage()
            } catch { DispatchQueue.main.async { self.errorMessage = error.localizedDescription; self.loading = false } }
            DispatchQueue.main.async { self.scanning = false }
        }
    }
    private func publishUsage(finishLoading: Bool = true) {
        do {
            let revision = try database.query("SELECT SUM(revision) AS value FROM usage_revisions").first?.integer("value") ?? 0
            let upstreamFile = paths.data.appendingPathComponent("upstream.sqlite")
            let upstream = try? Database(upstreamFile,readOnly:true)
            let upstreamRevision = (try? upstream?.query("SELECT COUNT(*) AS count, MAX(observed_at) AS stamp FROM observations").first) ?? [:]
            let key = "\(revision):\(identity(upstreamRevision)):\(catalog.version):\(Calendar.current.startOfDay(for:Date()).timeIntervalSince1970)"
            var result: UsageSnapshot
            if key == computationKey, Date() < computationExpires, let cached = computedUsage { result = cached }
            else {
                var records = try database.records()
                if let upstream, let models = try? upstream.query("SELECT response_id,model FROM observations") {
                    let detected = Dictionary(models.map {($0.string("response_id"),$0.string("model"))},uniquingKeysWith:{$1})
                    for index in records.indices { records[index]["upstream_model"] = detected[records[index].string("response_id")] }
                }
                let turns = try database.metadata("usage_turns")
                result = Analytics.build(records:records,turns:turns,links:try database.metadata("usage_agent_links"),catalog:catalog)
                let statusExpiry = turns.filter {$0.string("status") == "running"}.compactMap { parsedDate($0["observed_at"])?.addingTimeInterval(901) }.filter {$0 > Date()}.min()
                let futureRecord = records.compactMap {parsedDate($0["timestamp"])}.filter {$0 > Date()}.min()
                computationExpires = [statusExpiry,futureRecord,Calendar.current.date(byAdding:.day,value:1,to:Calendar.current.startOfDay(for:Date()))].compactMap {$0}.min() ?? .distantFuture
                computationKey = key; computedUsage = result
            }
            result.updated = Date()
            let prices = catalog.rows, priceUpdated = catalog.updated, priceWarning = catalog.warning
            let models = paths.mock ? Set(["gpt-6-astra","gpt-5.6-sol","gpt-6-luna"]) : Set(readObject(accountRoot.appendingPathComponent("models_cache.json")).objects("models").filter { row in
                !row.flag("hidden") && row.string("visibility") != "hide" && (row["hidden"] is Bool || row.string("visibility") == "list")
            }.map {$0.string("slug",$0.string("model",$0.string("id")))}.filter {!$0.isEmpty})
            let estimates = try estimator.process(calls:result.calls,priceVersion:catalog.version)
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.stopped else { return }
                self.usage = result; self.prices = prices; self.modelIDs = models; self.priceUpdated = priceUpdated; self.priceWarning = priceWarning; self.weeklyEstimates = estimates; if finishLoading { self.loading = false }; self.publishWidget()
                if self.reportsVisible && self.reportedCandidates == 0 && !result.chatCandidates.isEmpty { self.refreshReports(force:true) }
            }
        } catch { DispatchQueue.main.async { [weak self] in self?.errorMessage = error.localizedDescription; self?.loading = false } }
    }
    func refreshQuota() {
        guard !refreshing, !stopped, !paths.mock else { return }
        refreshing = true
        let hint = preferences.general.string("codex_path"), accountRoot = self.accountRoot
        accountQueue.async { [weak self] in
            guard let self else { return }
            do {
                try self.client.start(hint:hint)
                let identityBefore = try? BackendCredentials.load(root:accountRoot).account
                let account = try self.client.request("account/read",["refreshToken":false]).object("account")
                var next = QuotaState(); next.account = account
                let config = try self.client.request("config/read",["includeLayers":false]).object("config")
                let provider = config.string("model_provider","openai")
                next.applicable = account.string("type") == "chatgpt" && ["openai","codexio-upstream"].contains(provider)
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
                DispatchQueue.main.async {
                    guard !self.stopped else { return }
                    if self.quota.account.string("identityKey") != next.account.string("identityKey") || self.quota.account.string("planType") != next.account.string("planType") { self.invalidateReports(); self.pendingResets.removeAll() }
                    self.quota = next; self.restorePendingResets(); self.refreshing = false; self.onQuotaChange?(); self.publishWidget()
                    if self.reportsVisible { self.refreshReports() }
                }
            } catch {
                if !self.client.running { self.client.close() }
                DispatchQueue.main.async { guard !self.stopped else { return }; self.quota.error = error.localizedDescription; self.refreshing = false; self.onQuotaChange?(); self.publishWidget() }
            }
        }
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
        reportGeneration = UUID(); reportLoadedAt = nil; reportedCandidates = 0; planHistory = [:]; chatUsage = [:]; reportError = nil; reportsLoading = false
    }
    private var reportsVisible: Bool { selectedPage == "subscription" || (selectedPage == "trends" && usageSection == "threads") }
    private func restorePendingResets() {
        let account = quota.account.string("identityKey"); guard !account.isEmpty else { return }
        let rows = (try? database.query("SELECT data FROM usage_meta WHERE key LIKE ?",["reset-operation:"+account+":%"])) ?? []
        pendingResets.removeAll()
        for row in rows {
            let operation = jsonObject(Data(row.string("data").utf8)), id = operation.string("creditId")
            guard !id.isEmpty else { continue }; pendingResets.insert(id)
            if !quota.credits.contains(where:{$0.id == id}) {
                var credit = operation.object("credit"); credit["id"] = id; credit["status"] = "pending_confirmation"
                quota.credits.append(ResetCredit(raw:credit))
            }
        }
    }
    func refreshReports(force: Bool = false) {
        guard !paths.mock, !reportsLoading, !stopped, !quota.account.isEmpty else { return }
        guard Date() >= reportRetryAt else { return }
        if !force, let date = reportLoadedAt, Date().timeIntervalSince(date) < 60 { return }
        guard quota.applicable else { return }
        reportsLoading = true; reportError = nil
        let generation = UUID(); reportGeneration = generation
        let candidates = usage.chatCandidates, root = accountRoot, expectedAccount = quota.account.string("identityKey")
        reportQueue.async { [weak self] in
            guard let self else { return }
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
                if !history.isEmpty { self.planHistory = history }
                if !chats.isEmpty { self.chatUsage = chats }
                self.reportsLoading = false; self.reportLoadedAt = Date()
                self.reportRetryAt = retryAt; self.reportedCandidates = candidates.count
                self.reportError = errors.isEmpty ? nil : Array(Set(errors)).joined(separator:" · ")
            }
        }
    }
    func setPreference(_ key: String, _ value: Any, general: Bool = false) {
        if general { preferences.general[key] = value } else { preferences.analytics[key] = value }
        do { try preferences.save() } catch { errorMessage = error.localizedDescription }
        theme = preferences.analytics.string("theme","system"); menuVisible = preferences.analytics.flag("menu_bar_visible",true); menuContent = preferences.analytics.string("menu_bar_content","week")
        configureTimers(); onSettingsChange?(); objectWillChange.send()
    }
    func toggleSidebar() { sidebarVisible.toggle(); setPreference("sidebar_collapsed",!sidebarVisible) }
    func rescan() {
        guard !paths.mock, !scanning else { return }
        dataQueue.async { [weak self] in
            do { try self?.indexer.rescan(); DispatchQueue.main.async { self?.refreshUsage() } }
            catch { DispatchQueue.main.async { self?.errorMessage = error.localizedDescription } }
        }
    }
    func syncPrices() {
        guard !paths.mock, !stopped else { return }
        dataQueue.async { [weak self] in
            do { try self?.catalog.sync(force:true); self?.publishUsage() }
            catch { DispatchQueue.main.async { self?.errorMessage = error.localizedDescription } }
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
        var request: Any = NSNull()
        if let selected = usage.widgetRequest {
            var value: Object = ["id":selected.id,"prompt":String(selected.raw.string("prompt_preview").prefix(240)),"model":modelName(selected.raw.string("model")),"reasoning_effort":effortName(selected.raw.string("reasoning_effort")),"duration_running":selected.raw.flag("duration_running")]
            for key in ["cost_usd","duration_ms","input_tokens","output_tokens","cached_input_tokens","cache_hit_rate","service_tier","model_context_window"] { value[key] = selected.raw[key] ?? NSNull() }
            if let start = parsedDate(selected.raw["duration_started_at"]) { value["duration_started_at"] = start.timeIntervalSince1970-(selected.raw.number("duration_base_ms") ?? 0)/1000 }
            for key in ["input_tokens","output_tokens","cached_input_tokens"] where value[key] is NSNull { value[key] = 0 }
            request = value
        }
        let today = usage.summaries["today"] ?? UsageSummary()
        let snapshot: Object = ["schema":1,"updated_at":Date().timeIntervalSince1970,"request":request,"today":["cost_usd":today.cost as Any? ?? NSNull(),"tokens":today.tokens as Any? ?? NSNull(),"requests":today.requests,"cache_hit_rate":today.cacheRate as Any? ?? NSNull()],"quota":["applicable":quota.applicable,"updated_at":quota.updated?.timeIntervalSince1970 as Any? ?? NSNull(),"five_hour":(quota.fresh ? quota.five?.remaining : nil) as Any? ?? NSNull(),"week":(quota.fresh ? quota.week?.remaining : nil) as Any? ?? NSNull(),"has_five_hour":quota.five != nil,"has_week":quota.week != nil,"five_hour_reset_at":(quota.fresh ? quota.five?.reset?.timeIntervalSince1970 : nil) as Any? ?? NSNull(),"week_reset_at":(quota.fresh ? quota.week?.reset?.timeIntervalSince1970 : nil) as Any? ?? NSNull(),"reset_count":(quota.fresh ? quota.availableCount : nil) as Any? ?? NSNull()]]
        var content = snapshot; content.removeValue(forKey:"updated_at")
        let signature = identity(content)
        guard signature != snapshotSignature || Date().timeIntervalSince(snapshotWritten) >= 300 else { return }
        do {
            guard try jsonData(snapshot).count <= 32768 else { return }
            try atomicJSON(snapshot,to:paths.snapshot)
            let canonical = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Codexio/widget_snapshot.json")
            if paths.snapshot != canonical, ProcessInfo.processInfo.environment["CODEXIO_DATA_DIR"] == nil { try atomicJSON(snapshot,to:canonical) }
            snapshotSignature = signature; snapshotWritten = Date()
            WidgetCenter.shared.reloadTimelines(ofKind:BuildInfo.requestKind); WidgetCenter.shared.reloadTimelines(ofKind:BuildInfo.quotaKind)
        } catch { errorMessage = error.localizedDescription }
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
                    self.quota = quota; self.priceUpdated = now
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
