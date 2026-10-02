import Foundation
import SwiftUI
import Network
import UIKit

struct PairedMac: Codable, Identifiable {
    var code: MobilePairCode
    var reader: MobileReader
    var invalid: Bool?
    var id: String { code.host }
}
private struct MobileDiskCache: Codable {
    var versions: [String:Int64]
    var digests: [String:String]
    var live: MobileLive?
    var recent: [MobileRequest]
    var trends: MobileTrends
}
private struct MobileDetailTransfer {
    var manifest: MobileDetailManifest
    var bytes = Data()
    var next = 0
}

@MainActor final class MobileStore: ObservableObject {
    @Published var devices: [PairedMac] = []
    @Published var selected = ""
    @Published var live: MobileLive?
    @Published var recent: [MobileRequest] = []
    @Published var trends = MobileTrends(daily:[],periods:[])
    @Published var status = "尚未配对"
    // Time this iPhone last completed a summary sync, independent of host upload time.
    @Published var updated: Date?
    @Published private(set) var refreshing = false
    @Published private(set) var phoneName = UIDevice.current.name
    @Published var pairing = false
    @Published var error: String?
    @Published var revokedPrompt: String?
    @Published var tab = 0
    @Published var recordPath: [String] = []
    @Published private(set) var detailValues: [String:MobileRequestDetail] = [:]
    @Published private(set) var detailErrors: [String:String] = [:]
    @Published private(set) var detailLoading = Set<String>()
    @Published private(set) var detailVersions: [String:Int64] = [:]
    @Published private(set) var supportsDetails = false
    @Published private(set) var imageValues: [String:Data] = [:]
    @Published private(set) var imageLoading = Set<String>()
    @Published private(set) var imageErrors: [String:String] = [:]
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.ios.network",qos:.utility)
    private let files = DispatchQueue(label:"com.wujuhu.codexio.ios.cache",qos:.utility)
    private var envelopes: [String:MobileEnvelope] = [:]
    private var browser: NWBrowser?
    private var connection: NWConnection?
    private var localReady = false
    private var foreground = false
    private var timer: Timer?
    private var generation = UUID()
    private var attempt: PairedMac?
    private var http = MobileHTTP()
    private var cloudInFlight = false
    private var nextCloud = Date.distantPast
    private var failures = 0
    private var lastProbe = Date.distantPast
    private var localWaiting: Date?
    private var cloudDenied = false
    private var task: Task<Void,Never>?
    private var syncOperations = Set<UUID>()
    private var localOperation: UUID?
    private var detailEnvelopes: [String:MobileEnvelope] = [:]
    private var detailAccess: [String:Date] = [:]
    private var detailTasks: [String:Task<Void,Never>] = [:]
    private var localDetails: [String:Bool] = [:]
    private var expandAfterLoad = Set<String>()
    private var detailTransfers: [String:MobileDetailTransfer] = [:]
    private var localImageSupport: Bool?
    private var cloudImageSupport: Bool?
    private var imageRequests: [String:MobileImageReference] = [:]
    private var imageTokens: [String:UUID] = [:]
    private var imageTasks: [String:Task<Void,Never>] = [:]
    private var imageTransfers: [String:MobileDetailTransfer] = [:]
    private var localImages = Set<String>()
    private var pendingImages: [String] = []
    private var imageWaitUntil: [String:Date] = [:]
    private var pausedImages: [MobileImageReference] = []
    private var imageCacheReferences: [String:MobileImageReference] = [:]
    private var imageDeadlines: [String:Double] = [:]
    private var imageAccess: [String:UInt64] = [:]
    private var imageAccessClock: UInt64 = 0
    private var failedImages: [String:MobileImageReference] = [:]
    private var expiredImages = Set<String>()
    init() {
        let savedName = UserDefaults.standard.string(forKey:"phone-name")?.trimmingCharacters(in:.whitespacesAndNewlines) ?? ""
        if !savedName.isEmpty { phoneName = String(savedName.prefix(40)) }
        files.async {
            do {
                let devices = try MobileKeychain.read("ios-devices-v1").map {try JSONDecoder().decode([PairedMac].self,from:$0)} ?? []
                Task { @MainActor in
                    self.devices = devices
                    let saved = UserDefaults.standard.string(forKey:"selected-mac") ?? ""
                    self.selected = devices.contains(where:{$0.id == saved}) ? saved : devices.first?.id ?? ""
                    if self.foreground { self.select(self.selected) }
                }
            } catch { Task { @MainActor in self.error = error.localizedDescription } }
        }
    }
    var device: PairedMac? { attempt ?? devices.first {$0.id == selected} }
    var overviewTask: MobileRequest? {
        if let task = live?.task { return request(task.id) ?? task }
        return recent.first {$0.status == "completed"}
    }
    func request(_ id: String) -> MobileRequest? {
        let recentRequest = recent.first {$0.id == id}
        if let task = live?.task, task.id == id,
           recentRequest == nil || (envelopes["live"]?.revision ?? 0) >= (envelopes["recent"]?.revision ?? 0) { return task }
        return recentRequest
    }
    func showRequest(_ item: MobileRequest) { tab = 2; recordPath = [item.id] }
    var connectionLabel: String {
        if device?.invalid == true { return "配对已失效" }
        if localReady { return "局域网" }
        if status == "云同步" || status == "Cloudflare 同步" { return "云同步" }
        if device == nil { return "未配对" }
        return "未连接"
    }
    private func beginSync() -> UUID {
        let id = UUID(); syncOperations.insert(id)
        if !refreshing { refreshing = true }
        return id
    }
    private func endSync(_ id: UUID?) {
        if let id { syncOperations.remove(id) }
        let active = !syncOperations.isEmpty
        if refreshing != active { refreshing = active }
    }
    private func finishLocalSync() { endSync(localOperation); localOperation = nil; localWaiting = nil }
    func renamePhone(_ value: String) {
        let name = String(value.trimmingCharacters(in:.whitespacesAndNewlines).prefix(40))
        guard !name.isEmpty, name != phoneName else { return }
        phoneName = name; UserDefaults.standard.set(name,forKey:"phone-name")
        if localReady { sendLocal() }
    }
    private var cacheDirectory: URL { FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("Codexio",isDirectory:true) }
    func activate() { foreground = true; select(selected) }
    func deactivate() {
        foreground = false; generation = UUID(); timer?.invalidate(); timer = nil; browser?.cancel(); connection?.cancel()
        browser = nil; connection = nil; localReady = false; localWaiting = nil; task?.cancel(); http.stop(); http = MobileHTTP(); cloudInFlight = false
        syncOperations.removeAll(); localOperation = nil; refreshing = false
        detailTasks.values.forEach {$0.cancel()}; detailTasks.removeAll(); localDetails.removeAll(); expandAfterLoad.removeAll(); detailLoading.removeAll(); detailTransfers.removeAll()
        for image in imageRequests.values where !pausedImages.contains(where:{$0.id == image.id}) { pausedImages.append(image) }
        if pausedImages.count > 32 { pausedImages.removeFirst(pausedImages.count-32) }
        imageTasks.values.forEach {$0.cancel()}; imageTasks.removeAll(); imageRequests.removeAll(); imageTokens.removeAll()
        localImages.removeAll(); imageTransfers.removeAll(); pendingImages.removeAll(); imageLoading.removeAll()
        imageWaitUntil.removeAll()
        localImageSupport = nil
    }
    func select(_ id: String) {
        let resuming = id == selected && (!envelopes.isEmpty || live != nil || !recordPath.isEmpty || !detailValues.isEmpty)
        let active = foreground; deactivate(); foreground = active; selected = id
        UserDefaults.standard.set(id,forKey:"selected-mac"); cloudDenied = false; nextCloud = .distantPast
        if resuming {
            pruneRetainedContent()
            if foreground, device?.invalid != true { connect(); startTimer() }
            let resume = pausedImages; pausedImages.removeAll()
            for image in resume { loadImage(image) }
            return
        }
        live = nil; recent = []; trends = MobileTrends(daily:[],periods:[]); envelopes = [:]; updated = nil
        recordPath = []; detailValues = [:]; detailErrors = [:]; detailEnvelopes = [:]; detailAccess = [:]; detailVersions = [:]; supportsDetails = false
        clearImages()
        guard device != nil else { status = "扫描电脑上的二维码开始配对"; return }
        let stamp = generation, url = cacheDirectory.appendingPathComponent(id+".json")
        files.async {
            let data = try? Data(contentsOf:url)
            let cache = data.flatMap {try? JSONDecoder().decode(MobileDiskCache.self,from:$0)}
            Task { @MainActor in
                guard stamp == self.generation, self.envelopes.isEmpty, let cache else { return }
                self.live = cache.live; self.recent = cache.recent.filter {$0.started >= Date().timeIntervalSince1970-MobileProtocol.detailRetention}.prefix(200).map {$0}; self.trends = cache.trends.singleModelsOnly()
                self.envelopes = cache.versions.mapValues {_ in MobileEnvelope(dataset:"",revision:0,digest:"",payload:"")}
                for (key,revision) in cache.versions { self.envelopes[key] = MobileEnvelope(dataset:key,revision:revision,digest:cache.digests[key] ?? "",payload:"") }
                self.pruneRetainedContent()
                self.status = "已显示缓存"; self.persistCache()
            }
        }
        loadDetailCache(id,stamp:stamp)
        if foreground, device?.invalid != true { connect(); startTimer() }
    }
    func beginPair(_ data: String) {
        guard devices.count < 3 else { error = "最多配对 3 台电脑"; return }
        do {
            guard data.utf8.count <= 4096 else { throw MobileError.message("无效二维码") }
            let code = try JSONDecoder().decode(MobilePairCode.self,from:Data(data.utf8))
            guard code.version == 1, UUID(uuidString:code.host) != nil, code.pin.count == 64, code.ticket.count == 43,
                  code.expires > Date().timeIntervalSince1970, code.expires < Date().timeIntervalSince1970+600,
                  code.cloud == nil || code.cloud == MobileProtocol.cloudOrigin else { throw MobileError.message("二维码无效或已经过期") }
            let reader = MobileReader(id:UUID().uuidString,name:String(phoneName.prefix(40)),localSecret:try MobileProtocol.secret(),cloudSecret:try MobileProtocol.secret())
            deactivate(); foreground = true
            attempt = PairedMac(code:code,reader:reader); selected = code.host; envelopes = [:]
            live = nil; recent = []; trends = MobileTrends(daily:[],periods:[]); updated = nil
            recordPath = []; detailValues = [:]; detailEnvelopes = [:]; detailVersions = [:]; supportsDetails = false; detailErrors = [:]
            detailAccess = [:]; clearImages()
            pairing = true; status = "正在寻找电脑，请保持同一局域网"; error = nil
            connect(); startTimer()
        } catch { self.error = error.localizedDescription }
    }
    func cancelPair() { attempt = nil; pairing = false; select(devices.first?.id ?? "") }
    func remove(_ target: String? = nil) {
        let id = target ?? selected
        let removingSelected = id == selected
        if removingSelected {deactivate(); attempt = nil}
        devices.removeAll {$0.id == id}; if revokedPrompt == id {revokedPrompt = nil}
        let saved = devices
        files.async { do { try MobileKeychain.save(MobileProtocol.encode(saved),key:"ios-devices-v1") } catch { Task { @MainActor in self.error = error.localizedDescription } } }
        let url = cacheDirectory.appendingPathComponent(id+".json")
        let detailURL = cacheDirectory.appendingPathComponent(id+"-details.json")
        files.async { try? FileManager.default.removeItem(at:url); try? FileManager.default.removeItem(at:detailURL) }
        if removingSelected {foreground = true; select(devices.first?.id ?? "")}
    }
    private func pairingRevoked() {
        guard let index = devices.firstIndex(where:{$0.id == selected}), devices[index].invalid != true else { return }
        devices[index].invalid = true
        let active = foreground, id = selected
        deactivate(); foreground = active; status = "配对已失效"; error = nil; revokedPrompt = id
        clearImages()
        Task {do {try await saveDevices()} catch {self.error=error.localizedDescription}}
    }
    private func startTimer() {
        timer = Timer.scheduledTimer(withTimeInterval:3,repeats:true) { [weak self] _ in Task { @MainActor in
            guard let self, self.foreground else { return }
            self.pruneRetainedContent()
            if !self.pendingImages.isEmpty { self.drainImages() }
            if let waiting = self.localWaiting, Date().timeIntervalSince(waiting) > 8 {
                self.connection?.cancel(); self.connection = nil; self.localReady = false; self.finishLocalSync()
                self.retryLocalImages()
            }
            if let attempt = self.attempt, Date().timeIntervalSince1970 > attempt.code.expires { self.error = "配对超时，请在电脑上重新生成二维码"; self.cancelPair(); return }
            if self.pairing { if self.connection == nil && Date().timeIntervalSince(self.lastProbe) >= 3 { self.connect() } else { self.sendLocal() } }
            else if self.localReady { if Date().timeIntervalSince(self.lastProbe) >= 30 { self.lastProbe = Date(); self.sendLocal() } }
            else { self.cloudRefresh(); if Date().timeIntervalSince(self.lastProbe) > 60 { self.connect() } }
        }}
        timer?.tolerance = 1
    }
    private func connect() {
        guard foreground, let device, device.invalid != true else { return }
        lastProbe = Date(); browser?.cancel(); connection?.cancel(); connection = nil; localReady = false
        finishLocalSync()
        retryLocalImages()
        let probe = beginSync()
        let stamp = generation
        let browser = NWBrowser(for:.bonjour(type:MobileProtocol.service,domain:nil),using:.tcp); self.browser = browser
        browser.browseResultsChangedHandler = { [weak self] results,_ in
            guard let endpoint = results.first(where:{ if case .service(let name,_,_,_) = $0.endpoint { return name == device.id }; return false })?.endpoint else { return }
            Task { @MainActor in guard let self, stamp == self.generation, self.connection == nil else { return }; self.open(endpoint,device:device,stamp:stamp) }
        }
        browser.start(queue:queue)
        Task { try? await Task.sleep(for:.milliseconds(1500)); endSync(probe); if stamp == generation, !localReady { cloudRefresh() } }
    }
    private func open(_ endpoint: NWEndpoint,device: PairedMac,stamp: UUID) {
        let value = NWConnection(to:endpoint,using:MobileProtocol.parameters(pin:device.code.pin,queue:queue)); connection = value
        value.stateUpdateHandler = { [weak self,weak value] state in Task { @MainActor in
            guard let self, let value, stamp == self.generation, self.connection === value else { return }
            switch state {
            case .ready: self.receive(value,stamp:stamp); self.sendLocal()
            case .failed: self.localReady = false; self.connection = nil; value.cancel(); self.finishLocalSync(); self.retryLocalImages(); self.cloudRefresh()
            default: break
            }
        }}
        value.start(queue:queue)
    }
    private func sendLocal() {
        guard let connection, let device, localWaiting == nil else { return }
        localWaiting = Date(); localOperation = beginSync()
        var reader = device.reader; reader.name = String(phoneName.prefix(40))
        let stamp = generation
        MobileProtocol.send(MobileMessage(action:pairing ? "pair" : "sync",ticket:pairing ? device.code.ticket : nil,reader:reader,known:envelopes.mapValues(\.revision)),over:connection) { [weak self] error in
            if error != nil { Task { @MainActor in guard let self, stamp == self.generation, self.connection === connection else { return }; self.localReady = false; self.finishLocalSync(); self.retryLocalImages() } }
        }
    }
    private func receive(_ value: NWConnection,stamp: UUID) {
        value.receiveMessage { [weak self,weak value] data,_,_,failure in
            let parsed = data.flatMap {bytes in bytes.count <= MobileProtocol.limit ? try? JSONDecoder().decode(MobileMessage.self,from:bytes) : nil}
            Task { @MainActor in
            guard let self, let value, stamp == self.generation, self.connection === value else { return }
            guard failure == nil, let message = parsed else {
                self.localReady = false; self.connection = nil; value.cancel(); self.finishLocalSync(); self.retryLocalImages(); self.cloudRefresh(); return
            }
            if message.action == "image" {
                guard let id = message.imageID, self.localImages.contains(id), let token = self.imageTokens[id] else { self.receive(value,stamp:stamp); return }
                let finished = await self.acceptImageResponse(message,id:id,token:token)
                guard stamp == self.generation, self.connection === value else { return }
                guard self.imageTokens[id] == token else { self.receive(value,stamp:stamp); return }
                if finished { self.finishImage(id,token:token) }
                else if let next = self.imageTransfers[id]?.next, let reader = self.device?.reader {
                    MobileProtocol.send(MobileMessage(action:"image",reader:reader,imageID:id,imagePart:next),over:value)
                }
                self.receive(value,stamp:stamp); return
            }
            if message.action == "detail", let id = message.detailID {
                guard let full = self.localDetails[id] else { self.receive(value,stamp:stamp); return }
                if await self.acceptDetailResponse(message,id:id) {
                    self.localDetails.removeValue(forKey:id); self.detailTasks[id]?.cancel(); self.finishDetail(id,full:full)
                } else if let next = self.detailTransfers[id]?.next, let reader = self.device?.reader {
                    MobileProtocol.send(MobileMessage(action:"detail",reader:reader,detailID:id,full:true,detailPart:next),over:value)
                }
                self.receive(value,stamp:stamp); return
            }
            let processing = self.beginSync(), pendingRequest = self.localOperation
            self.localOperation = nil; self.localWaiting = nil
            defer { self.endSync(processing); self.endSync(pendingRequest) }
            if message.action == "pending" { self.status = "请在电脑上点击“确认配对”" }
            else if message.action == "sync" {
                if var paired = self.attempt {
                    paired.code.ticket = ""; paired.code.cloud = nil; self.devices.removeAll {$0.id == paired.id}; self.devices.append(paired)
                    do { try await self.saveDevices(); guard stamp == self.generation else { return }; self.attempt = nil; self.pairing = false; UserDefaults.standard.set(paired.id,forKey:"selected-mac") }
                    catch { self.error = error.localizedDescription; value.cancel(); return }
                }
                if message.cloud == MobileProtocol.cloudOrigin, let index = self.devices.firstIndex(where:{$0.id == self.selected}), self.devices[index].code.cloud == nil {
                    self.devices[index].code.cloud = MobileProtocol.cloudOrigin
                    do { try await self.saveDevices(); guard stamp == self.generation else { return } } catch { self.error = error.localizedDescription }
                }
                if message.cloud == MobileProtocol.cloudOrigin {self.cloudDenied = false}
                self.supportsDetails = message.capabilities?.contains(MobileProtocol.detailCapability) == true
                self.localImageSupport = message.capabilities?.contains(MobileProtocol.imageCapability) == true
                self.setDetailVersions(message.detailVersions ?? [:])
                await self.apply(message.datasets ?? [])
                guard stamp == self.generation else { return }
                let stale = self.envelopes.contains { key,value in (message.known?[key] ?? 0) < value.revision }
                self.localReady = !stale; self.status = stale ? "电脑数据版本较旧 · 保留较新缓存" : "局域网"
                if !stale { self.updated = Date() }
                self.drainImages()
                if message.supportsAck == true {MobileProtocol.send(MobileMessage(action:"ack",known:self.envelopes.mapValues(\.revision)),over:value)}
            } else if message.action == "revoked" { self.pairingRevoked(); return
            } else if message.action == "error" { self.error = message.error; self.localReady = false; value.cancel(); self.connection = nil; self.retryLocalImages(); if self.pairing { self.cancelPair() }; return }
            self.receive(value,stamp:stamp)
        }}
    }
    func refresh() {
        error = nil; cloudDenied = false
        if localReady { sendLocal() } else { nextCloud = .distantPast; cloudRefresh(); connect() }
    }
    private func cloudRefresh() {
        guard foreground, !pairing, !localReady, !cloudDenied, !cloudInFlight, Date() >= nextCloud, let device, device.invalid != true, device.code.cloud != nil else { return }
        cloudInFlight = true; let operation = beginSync(), stamp = generation, known = envelopes.mapValues(\.revision)
        let query = (["reader=\(device.reader.id)"]+known.map {"\($0.key)=\($0.value)"}).joined(separator:"&")
        task = Task {
            defer { endSync(operation); if stamp == generation { cloudInFlight = false } }
            do {
                let data = try await http.request("/v1/hosts/\(device.id)/sync?"+query,token:device.reader.cloudSecret)
                let response = try await Task.detached(priority:.utility) {try JSONDecoder().decode(MobileMessage.self,from:data)}.value
                guard stamp == generation, !Task.isCancelled else { return }
                cloudImageSupport = response.capabilities?.contains(MobileProtocol.imageCapability) == true
                if !localReady {
                    supportsDetails = response.capabilities?.contains(MobileProtocol.detailCapability) == true
                    setDetailVersions(response.detailVersions ?? [:])
                    await apply(response.datasets ?? [])
                    guard stamp == generation else { return }
                    if !localReady { status = "云同步"; updated = Date() }
                }
                failures = 0; nextCloud = Date().addingTimeInterval(live?.runningCount ?? 0 > 0 ? 15 : 60)
                drainImages()
            } catch {
                guard stamp == generation, !Task.isCancelled else { return }
                failures += 1; nextCloud = Date().addingTimeInterval(min(900,Double(30 * (1 << min(failures,5)))))
                if case MobileError.http(let code,let retry,let reason) = error {
                    if let retry { nextCloud = max(nextCloud,Date().addingTimeInterval(retry)) }
                    if code == 403 && reason == "REVOKED" { pairingRevoked(); return }
                    if code == 401 || code == 403 { cloudDenied = true }
                }
                if !localReady { status = cloudDenied ? "云端需重新授权" : "离线 · 显示上次数据" }
            }
        }
    }
    private func saveDevices() async throws {
        let saved = devices
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void,Error>) in
            files.async { do { try MobileKeychain.save(MobileProtocol.encode(saved),key:"ios-devices-v1"); continuation.resume() } catch { continuation.resume(throwing:error) } }
        }
    }
    private func apply(_ values: [MobileEnvelope]) async {
        let stamp = generation
        let decoded = await Task.detached(priority:.utility) {
            values.filter {$0.valid()}.map { value in
                (value, value.dataset == "live" ? try? value.decode(MobileLive.self) : nil,
                 value.dataset == "recent" ? try? value.decode([MobileRequest].self) : nil,
                 value.dataset == "trends" ? try? value.decode(MobileTrends.self).singleModelsOnly() : nil)
            }
        }.value
        guard stamp == generation else { return }
        var changed = false
        var renamed = false
        for (value,liveValue,recentValue,trendsValue) in decoded where ["live","recent","trends"].contains(value.dataset) {
            if let old = envelopes[value.dataset], old.revision >= value.revision {
                if old.revision == value.revision && old.digest != value.digest { error = "同步版本冲突，已保留原数据" }
                continue
            }
            do {
                switch value.dataset {
                case "live":
                    guard let liveValue else { throw MobileError.message("invalid live") }; live = liveValue
                    if !liveValue.name.isEmpty, let index = devices.firstIndex(where:{$0.id == selected}), devices[index].code.name != liveValue.name {
                        devices[index].code.name = liveValue.name; renamed = true
                    }
                case "recent": guard let recentValue else { throw MobileError.message("invalid recent") }; recent = recentValue.filter {$0.started >= Date().timeIntervalSince1970-MobileProtocol.detailRetention}.prefix(200).map {$0}
                case "trends": guard let trendsValue else { throw MobileError.message("invalid trends") }; trends = trendsValue
                default: break
                }
                // Only revision/digest participate in subsequent syncs. Keep
                // message text in the bounded, expiring decoded stores.
                var version = value; version.payload = ""; envelopes[value.dataset] = version; changed = true
            } catch { self.error = "同步数据格式不兼容，请更新 Codexio" }
        }
        if renamed { do {try await saveDevices()} catch {self.error = error.localizedDescription}; guard stamp == generation else { return } }
        pruneRetainedContent()
        if changed { persistCache() }
    }
    private func setDetailVersions(_ values: [String:Int64]) {
        let bounded = Dictionary(uniqueKeysWithValues:values.filter {$0.key.count == 64 && $0.value > 0}.prefix(MobileProtocol.detailRows).map {($0.key,$0.value)})
        if detailVersions != bounded { detailVersions = bounded }
    }
    func loadImage(_ image: MobileImageReference, force: Bool = false) {
        let id = image.id, now = Date().timeIntervalSince1970
        guard image.valid, foreground, let device, device.invalid != true else { return }
        guard detailValues.values.contains(where:{$0.expires > now && ($0.images?.contains(image) ?? false)}) else { return }
        if image.expires <= now || image.availability == "expired" || expiredImages.contains(id) {
            imageValues.removeValue(forKey:id); imageCacheReferences.removeValue(forKey:id); imageAccess.removeValue(forKey:id); expiredImages.insert(id)
            imageErrors[id] = imageError("IMAGE_EXPIRED"); failedImages[id] = image; return
        }
        guard image.availability == "available" else { imageErrors[id] = imageError("IMAGE_UNAVAILABLE"); failedImages[id] = image; return }
        if !force, imageValues[id] != nil, imageCacheReferences[id] == image, (imageDeadlines[id] ?? 0) > now {
            touchImage(id); return
        }
        if let active = imageRequests[id] {
            if active == image { return }
            if let token = imageTokens[id] { finishImage(id,token:token,drain:false) }
        }
        if !force, failedImages[id] == image { return }
        guard imageRequests.count < 32 else { imageErrors[id] = "正在读取其他图片，请稍后重试。"; failedImages[id] = image; return }
        imageRequests[id] = image; imageTokens[id] = UUID(); imageDeadlines[id] = min(imageDeadlines[id] ?? image.expires,image.expires)
        imageWaitUntil[id] = Date().addingTimeInterval(10); pausedImages.removeAll {$0.id == id}
        imageLoading.insert(id); imageErrors.removeValue(forKey:id); failedImages.removeValue(forKey:id)
        pendingImages.append(id); drainImages()
    }
    func imageIsExpired(_ image: MobileImageReference) -> Bool {
        image.availability == "expired" || expiredImages.contains(image.id)
            || min(image.expires,imageDeadlines[image.id] ?? image.expires) <= Date().timeIntervalSince1970
    }
    private func drainImages() {
        guard foreground, let device, device.invalid != true else { return }
        while imageTasks.count < 2, !pendingImages.isEmpty {
            let id = pendingImages.removeFirst()
            guard let image = imageRequests[id], let token = imageTokens[id] else { continue }
            if min(image.expires,imageDeadlines[id] ?? image.expires) <= Date().timeIntervalSince1970 {
                imageErrors[id] = imageError("IMAGE_EXPIRED"); expiredImages.insert(id); failedImages[id] = image
                finishImage(id,token:token,drain:false); continue
            }
            let stamp = generation
            if localReady, localImageSupport == true, let connection {
                localImages.insert(id)
                imageTasks[id] = Task {
                    do { try await Task.sleep(for:.seconds(30)) } catch { return }
                    guard stamp == generation, imageTokens[id] == token else { return }
                    imageErrors[id] = "电脑未返回完整图片，请重试。"; failedImages[id] = image
                    finishImage(id,token:token)
                }
                MobileProtocol.send(MobileMessage(action:"image",reader:device.reader,imageID:id,imagePart:0),over:connection)
            } else if device.code.cloud != nil, !cloudDenied, cloudImageSupport != false {
                let client = http
                imageTasks[id] = Task {
                    var complete = false
                    do {
                        for part in 0..<24 {
                            guard stamp == generation, imageTokens[id] == token, !Task.isCancelled else { return }
                            let bytes = try await client.request("/v1/hosts/\(device.id)/images/\(id)?reader=\(device.reader.id)&part=\(part)",token:device.reader.cloudSecret)
                            let message = try await Task.detached(priority:.utility) { try JSONDecoder().decode(MobileMessage.self,from:bytes) }.value
                            guard stamp == generation, imageTokens[id] == token, !Task.isCancelled else { return }
                            if await acceptImageResponse(message,id:id,token:token) { complete = true; break }
                        }
                        guard stamp == generation, imageTokens[id] == token, !Task.isCancelled else { return }
                        if !complete { imageErrors[id] = "图片分片不完整，请重试。" }
                    } catch {
                        guard stamp == generation, imageTokens[id] == token, !Task.isCancelled else { return }
                        if case MobileError.http(let code,_,let reason) = error {
                            if code == 403 && reason == "REVOKED" { pairingRevoked(); return }
                            if code == 401 || code == 403 { cloudDenied = true }
                            imageErrors[id] = (code == 401 || code == 403) ? "云端需重新授权，暂时无法读取图片。" : imageError(reason)
                            if reason == "IMAGE_EXPIRED" { expiredImages.insert(id) }
                            if reason == "IMAGES_UNSUPPORTED" { cloudImageSupport = false }
                        } else { imageErrors[id] = "图片读取失败，请重试。" }
                    }
                    guard stamp == generation, imageTokens[id] == token else { return }
                    if imageErrors[id] != nil { failedImages[id] = image }
                    finishImage(id,token:token)
                }
            } else if !localReady, localImageSupport == nil, Date() < (imageWaitUntil[id] ?? .distantPast) {
                // Give the existing Bonjour probe/sync time to report its
                // capabilities. The ordinary store timer expires this wait.
                pendingImages.insert(id,at:0); return
            } else {
                imageErrors[id] = cloudDenied ? "云端需重新授权，暂时无法读取图片。"
                    : (localReady || cloudImageSupport == false ? imageError("IMAGES_UNSUPPORTED") : "离线，暂无图片缓存。")
                failedImages[id] = image; finishImage(id,token:token,drain:false)
            }
        }
    }
    private func acceptImageResponse(_ message: MobileMessage, id: String, token: UUID) async -> Bool {
        guard imageTokens[id] == token, let image = imageRequests[id] else { return true }
        if message.error == "REVOKED" { pairingRevoked(); return true }
        if let error = message.error {
            imageErrors[id] = imageError(error); failedImages[id] = image
            if error == "IMAGE_EXPIRED" { expiredImages.insert(id) }
            if error == "IMAGES_UNSUPPORTED" {
                if localImages.contains(id) { localImageSupport = false } else { cloudImageSupport = false }
            }
            return true
        }
        guard message.action == "image", message.imageID == id,
              let manifest = message.imageManifest, manifest.valid(for:id,limit:MobileProtocol.imageEnvelopeLimit), manifest.parts <= 24,
              let part = message.imagePart, let encoded = message.imageChunk, encoded.utf8.count <= 90_000 else {
            imageErrors[id] = "图片响应格式无效，请重试。"; failedImages[id] = image; return true
        }
        let stamp = generation
        let chunk = await Task.detached(priority:.utility) { () -> Data? in
            guard let value = Data(base64Encoded:encoded), !value.isEmpty, value.count <= MobileProtocol.imageChunkBytes else { return nil }
            return value
        }.value
        guard stamp == generation, imageTokens[id] == token else { return true }
        var transfer = imageTransfers.removeValue(forKey:id) ?? MobileDetailTransfer(manifest:manifest)
        guard let chunk, transfer.manifest == manifest, part == transfer.next,
              chunk.count == min(MobileProtocol.imageChunkBytes,manifest.bytes-transfer.bytes.count) else {
            imageErrors[id] = "图片在读取期间发生变化，请重试。"; failedImages[id] = image; return true
        }
        transfer.bytes.append(chunk); transfer.next += 1; imageTransfers[id] = transfer
        guard transfer.next == manifest.parts else { return false }
        let payloadBytes = transfer.bytes
        let decoded = await Task.detached(priority:.utility) { () -> (Data?,Double?,String?) in
            guard payloadBytes.count == manifest.bytes, MobileProtocol.hash(payloadBytes) == manifest.digest,
                  let payload = try? JSONDecoder().decode(MobileImagePayload.self,from:payloadBytes) else { return (nil,nil,nil) }
            return (payload.decoded(for:image),payload.expires,payload.request)
        }.value
        guard stamp == generation, imageTokens[id] == token else { return true }
        let now = Date().timeIntervalSince1970
        guard image.expires > now, (decoded.1 ?? image.expires) > now else {
            imageErrors[id] = imageError("IMAGE_EXPIRED"); expiredImages.insert(id); failedImages[id] = image; return true
        }
        guard let bytes = decoded.0, let expires = decoded.1, let request = decoded.2,
              let detail = detailValues[request], detail.expires > now, detail.images?.contains(image) == true else {
            imageErrors[id] = "图片完整性校验失败，请重试。"; failedImages[id] = image; return true
        }
        imageValues[id] = bytes; imageCacheReferences[id] = image; imageDeadlines[id] = min(image.expires,expires)
        touchImage(id); imageErrors.removeValue(forKey:id); failedImages.removeValue(forKey:id)
        pruneImages(); return true
    }
    private func imageError(_ code: String?) -> String {
        switch code {
        case "IMAGE_EXPIRED": return "图片已超过 3 天保留期。"
        case "IMAGE_UNAVAILABLE", "NOT_FOUND": return "图片暂不可用。"
        case "IMAGES_UNSUPPORTED": return "当前连接尚未提供图片同步。"
        case "IMAGE_CAPACITY": return "图片超出同步容量，暂不可用。"
        default: return "图片读取失败，请重试。"
        }
    }
    private func touchImage(_ id: String) {
        imageAccessClock &+= 1; imageAccess[id] = imageAccessClock
    }
    private func finishImage(_ id: String, token: UUID, drain: Bool = true) {
        guard imageTokens[id] == token else { return }
        imageTasks.removeValue(forKey:id)?.cancel(); imageRequests.removeValue(forKey:id); imageTokens.removeValue(forKey:id)
        imageTransfers.removeValue(forKey:id); localImages.remove(id); pendingImages.removeAll {$0 == id}; imageLoading.remove(id)
        imageWaitUntil.removeValue(forKey:id)
        if drain { drainImages() }
    }
    private func retryLocalImages() {
        for id in localImages {
            imageTasks.removeValue(forKey:id)?.cancel(); imageTransfers.removeValue(forKey:id); imageTokens[id] = UUID()
            if !pendingImages.contains(id) { pendingImages.append(id) }
        }
        localImages.removeAll(); drainImages()
    }
    private func clearImages() {
        imageTasks.values.forEach {$0.cancel()}; imageTasks.removeAll(); imageRequests.removeAll(); imageTokens.removeAll()
        imageTransfers.removeAll(); localImages.removeAll(); pendingImages.removeAll(); pausedImages.removeAll()
        imageWaitUntil.removeAll()
        imageValues.removeAll(); imageLoading.removeAll(); imageErrors.removeAll(); imageCacheReferences.removeAll()
        imageDeadlines.removeAll(); imageAccess.removeAll(); imageAccessClock = 0; failedImages.removeAll(); expiredImages.removeAll()
        localImageSupport = nil; cloudImageSupport = nil
    }
    private func pruneImages() {
        let now = Date().timeIntervalSince1970
        for id in imageDeadlines.filter({$0.value <= now}).map(\.key) {
            imageValues.removeValue(forKey:id); imageCacheReferences.removeValue(forKey:id); imageAccess.removeValue(forKey:id)
            imageDeadlines.removeValue(forKey:id); imageErrors[id] = imageError("IMAGE_EXPIRED"); expiredImages.insert(id)
            if let image = imageRequests[id] { failedImages[id] = image }
            if let token = imageTokens[id] { finishImage(id,token:token,drain:false) }
        }
        var bytes = 0, count = 0
        for id in imageValues.keys.sorted(by:{(imageAccess[$0] ?? 0) > (imageAccess[$1] ?? 0)}) {
            let size = imageValues[id]?.count ?? 0
            if count < 16, bytes+size <= 8*1_024*1_024 { bytes += size; count += 1 }
            else { imageValues.removeValue(forKey:id); imageCacheReferences.removeValue(forKey:id); imageAccess.removeValue(forKey:id) }
        }
        let retained = Set(detailValues.values.flatMap {$0.images ?? []}.map(\.id)).union(imageRequests.keys).union(imageValues.keys)
        for id in Set(imageDeadlines.keys).union(imageErrors.keys).union(failedImages.keys).union(expiredImages) where !retained.contains(id) {
            imageDeadlines.removeValue(forKey:id); imageErrors.removeValue(forKey:id); failedImages.removeValue(forKey:id); expiredImages.remove(id)
        }
        pausedImages.removeAll {$0.expires <= now || !retained.contains($0.id)}
        drainImages()
    }
    private func pruneRetainedContent() {
        let now = Date().timeIntervalSince1970, cutoff = Date().timeIntervalSince1970-MobileProtocol.detailRetention
        var changed = false
        if recent.contains(where:{$0.started < cutoff}) {
            recent.removeAll {$0.started < cutoff}; changed = true
            if var value = envelopes["recent"] { value.payload = ""; envelopes["recent"] = value }
        }
        if let task = live?.task, task.started < cutoff {
            live?.task = nil; changed = true
            if var value = envelopes["live"] { value.payload = ""; envelopes["live"] = value }
        }
        if changed { persistCache() }
        if detailValues.values.contains(where:{$0.expires <= now}) { pruneDetails(); persistDetails() }
        if imageDeadlines.values.contains(where:{$0 <= now}) { pruneImages() }
    }
    func loadDetail(_ id: String, full: Bool = false, force: Bool = false) {
        guard id.count == 64, foreground, let device, device.invalid != true else { return }
        let resume = pausedImages.filter { image in detailValues[id]?.images?.contains(image) == true }
        for image in resume { loadImage(image) }
        if !force, let cached = detailValues[id], cached.expires > Date().timeIntervalSince1970,
           (detailEnvelopes[id]?.revision ?? 0) >= (detailVersions[id] ?? 0), !full || cached.full {
            detailAccess[id] = Date(); return
        }
        if detailLoading.contains(id) { if full { expandAfterLoad.insert(id) }; return }
        guard detailLoading.count < 2 else { detailErrors[id] = "正在读取其他详情，请稍后重试。"; return }
        if !supportsDetails, localReady || !force {
            detailErrors[id] = localReady ? "电脑暂未提供正文详情。" : "云端正文同步尚未启用。"
            if force { refresh() }
            return
        }
        detailLoading.insert(id); detailErrors.removeValue(forKey:id)
        let stamp = generation
        if localReady, let connection {
            localDetails[id] = full
            MobileProtocol.send(MobileMessage(action:"detail",reader:device.reader,detailID:id,full:full,detailPart:0,force:force ? true : nil),over:connection)
            detailTasks[id] = Task {
                do { try await Task.sleep(for:.seconds(20)) } catch { return }
                guard stamp == generation, localDetails.removeValue(forKey:id) != nil else { return }
                detailErrors[id] = "电脑未返回详情，请重试。"; finishDetail(id,full:full)
            }
        } else {
            guard device.code.cloud != nil, !cloudDenied else { detailErrors[id] = "离线，暂无正文缓存。"; finishDetail(id,full:full); return }
            detailTasks[id] = Task {
                do {
                    for part in 0..<(full ? 16 : 1) {
                        let bytes = try await http.request("/v1/hosts/\(device.id)/details/\(id)?reader=\(device.reader.id)&full=\(full ? "1" : "0")&part=\(part)",token:device.reader.cloudSecret)
                        let message = try await Task.detached(priority:.utility) { try JSONDecoder().decode(MobileMessage.self,from:bytes) }.value
                        guard stamp == generation, !Task.isCancelled else { return }
                        if await acceptDetailResponse(message,id:id) { break }
                    }
                } catch {
                    guard stamp == generation, !Task.isCancelled else { return }
                    if case MobileError.http(let code,_,let reason) = error {
                        if code == 403 && reason == "REVOKED" { pairingRevoked(); return }
                        detailErrors[id] = detailError(reason)
                    } else { detailErrors[id] = "详情读取失败，已保留缓存。" }
                }
                guard stamp == generation else { return }
                finishDetail(id,full:full)
            }
        }
    }
    private func detailError(_ code: String?) -> String {
        switch code {
        case "DETAIL_EXPIRED": return "详情已超过保留期。"
        case "DETAILS_UNSUPPORTED", "NOT_FOUND": return "云端正文同步尚未启用。"
        case "DETAIL_CAPACITY": return "详情超过同步容量，仅显示预览。"
        case "DETAIL_UNAVAILABLE": return "正文详情暂不可用。"
        default: return "详情读取失败，请重试。"
        }
    }
    private func finishDetail(_ id: String, full: Bool) {
        detailLoading.remove(id); detailTasks.removeValue(forKey:id); detailTransfers.removeValue(forKey:id)
        if expandAfterLoad.remove(id) != nil, !full, detailErrors[id] == nil { loadDetail(id,full:true) }
    }
    func cancelDetail(_ id: String, pauseImages: Bool = true) {
        detailTasks[id]?.cancel(); detailTasks.removeValue(forKey:id); localDetails.removeValue(forKey:id)
        detailLoading.remove(id); detailTransfers.removeValue(forKey:id); expandAfterLoad.remove(id)
        let ids = Set((detailValues[id]?.images ?? []).map(\.id))
        for image in Array(imageRequests.values) where ids.contains(image.id) {
            if pauseImages, !pausedImages.contains(where:{$0.id == image.id}), pausedImages.count < 32 { pausedImages.append(image) }
            if let token = imageTokens[image.id] { finishImage(image.id,token:token,drain:false) }
        }
        if pauseImages { drainImages() }
    }
    private func acceptDetailResponse(_ message: MobileMessage, id: String) async -> Bool {
        if let envelope = message.detail { await applyDetail(envelope,id:id); return true }
        guard message.error == nil, let manifest = message.detailManifest, manifest.valid(for:id),
              let part = message.detailPart, let encoded = message.detailChunk, encoded.utf8.count <= 90_000,
              let bytes = Data(base64Encoded:encoded), bytes.count <= MobileProtocol.detailChunkBytes else {
            detailErrors[id] = detailError(message.error); return true
        }
        if detailTransfers[id] == nil { detailTransfers[id] = MobileDetailTransfer(manifest:manifest) }
        guard var transfer = detailTransfers[id], transfer.manifest == manifest, part == transfer.next,
              bytes.count == min(MobileProtocol.detailChunkBytes,manifest.bytes-transfer.bytes.count) else {
            detailErrors[id] = "详情在读取期间发生变化，请重试。"; return true
        }
        transfer.bytes.append(bytes); transfer.next += 1; detailTransfers[id] = transfer
        guard transfer.next == manifest.parts else { return false }
        let stamp = generation, payloadBytes = transfer.bytes
        let payload = await Task.detached(priority:.utility) { () -> String? in
            guard payloadBytes.count == manifest.bytes, MobileProtocol.hash(payloadBytes) == manifest.digest else { return nil }
            return String(data:payloadBytes,encoding:.utf8)
        }.value
        guard stamp == generation else { return true }
        guard let payload else { detailErrors[id] = "详情完整性校验失败，请重试。"; return true }
        await applyDetail(MobileEnvelope(dataset:"detail-"+id,revision:manifest.revision,digest:manifest.digest,payload:payload),id:id)
        return true
    }
    private func applyDetail(_ envelope: MobileEnvelope, id: String) async {
        let stamp = generation
        let value = await Task.detached(priority:.utility) { () -> MobileRequestDetail? in
            guard envelope.dataset == "detail-"+id, envelope.valid(limit:MobileProtocol.detailLimit), envelope.payload.utf8.count <= MobileProtocol.detailLimit,
                  let detail = try? envelope.decode(MobileRequestDetail.self), detail.valid(for:id), detail.expires > Date().timeIntervalSince1970 else { return nil }
            return detail
        }.value
        guard stamp == generation else { return }
        guard let value else { detailErrors[id] = "详情格式不兼容或已过期。"; return }
        if let old = detailEnvelopes[id], old.revision > envelope.revision || (old.revision == envelope.revision && detailValues[id]?.full == true && !value.full) { return }
        detailEnvelopes[id] = envelope; detailValues[id] = value; detailAccess[id] = Date(); detailErrors.removeValue(forKey:id)
        if detailLoading.contains(id) { supportsDetails = true }
        pruneDetails(); persistDetails()
    }
    private func pruneDetails() {
        let now = Date().timeIntervalSince1970
        var keep = Set<String>(), bytes = 0
        for id in detailValues.filter({$0.value.expires > now}).keys.sorted(by:{ (detailAccess[$0] ?? .distantPast) > (detailAccess[$1] ?? .distantPast) }) {
            let size = detailEnvelopes[id]?.payload.utf8.count ?? 0
            if keep.count < 16, bytes + size <= 8 * 1_024 * 1_024 { keep.insert(id); bytes += size }
        }
        for id in Array(detailValues.keys) where !keep.contains(id) {
            cancelDetail(id,pauseImages:false); detailErrors.removeValue(forKey:id)
            detailValues.removeValue(forKey:id); detailEnvelopes.removeValue(forKey:id); detailAccess.removeValue(forKey:id)
        }
        pruneImages()
    }
    private func loadDetailCache(_ id: String, stamp: UUID) {
        let url = cacheDirectory.appendingPathComponent(id+"-details.json")
        files.async {
            guard let attributes = try? FileManager.default.attributesOfItem(atPath:url.path),
                  let size = attributes[.size] as? NSNumber, size.intValue <= 18 * 1_024 * 1_024,
                  let bytes = try? Data(contentsOf:url), let values = try? JSONDecoder().decode([MobileEnvelope].self,from:bytes) else { return }
            let decoded = values.prefix(16).compactMap { envelope -> (MobileEnvelope,MobileRequestDetail)? in
                guard envelope.valid(limit:MobileProtocol.detailLimit), envelope.payload.utf8.count <= MobileProtocol.detailLimit,
                      let value = try? envelope.decode(MobileRequestDetail.self), value.valid(for:String(envelope.dataset.dropFirst(7))), value.expires > Date().timeIntervalSince1970 else { return nil }
                return (envelope,value)
            }
            Task { @MainActor in
                guard stamp == self.generation else { return }
                for (envelope,value) in decoded where self.detailValues[value.id] == nil {
                    self.detailEnvelopes[value.id] = envelope; self.detailValues[value.id] = value; self.detailAccess[value.id] = .distantPast
                }
                self.pruneDetails(); self.persistDetails()
            }
        }
    }
    private func persistDetails() {
        guard !selected.isEmpty else { return }
        let directory = cacheDirectory, url = directory.appendingPathComponent(selected+"-details.json"), values = Array(detailEnvelopes.values)
        files.async {
            do {
                try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
                let data = try MobileProtocol.encode(values)
                guard data.count <= 18 * 1_024 * 1_024 else { return }
                try data.write(to:url,options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication])
            } catch { /* A cache failure must not discard the displayed detail. */ }
        }
    }
    private func persistCache() {
            guard !selected.isEmpty else { return }
            let values = MobileDiskCache(versions:envelopes.mapValues(\.revision),digests:envelopes.mapValues(\.digest),live:live,recent:recent,trends:trends)
            let directory = cacheDirectory, url = directory.appendingPathComponent(selected+".json")
            files.async {
                do {
                    try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
                    try MobileProtocol.encode(values).write(to:url,options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication])
                } catch { /* Cache failure must not discard a valid in-memory snapshot. */ }
            }
    }
}
