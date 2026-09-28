import Foundation
import SwiftUI
import Network
import UIKit

struct PairedMac: Codable, Identifiable {
    var code: MobilePairCode
    var reader: MobileReader
    var id: String { code.host }
}
private struct MobileDiskCache: Codable {
    var versions: [String:Int64]
    var digests: [String:String]
    var live: MobileLive?
    var recent: [MobileRequest]
    var trends: MobileTrends
}

@MainActor final class MobileStore: ObservableObject {
    @Published var devices: [PairedMac] = []
    @Published var selected = ""
    @Published var live: MobileLive?
    @Published var recent: [MobileRequest] = []
    @Published var trends = MobileTrends(daily:[],periods:[])
    @Published var status = "尚未配对"
    @Published var updated: Date?
    @Published private(set) var refreshing = false
    @Published private(set) var phoneName = UIDevice.current.name
    @Published var pairing = false
    @Published var error: String?
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
    var connectionLabel: String {
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
    }
    func select(_ id: String) {
        let active = foreground; deactivate(); foreground = active; selected = id
        UserDefaults.standard.set(id,forKey:"selected-mac"); cloudDenied = false; nextCloud = .distantPast
        live = nil; recent = []; trends = MobileTrends(daily:[],periods:[]); envelopes = [:]; updated = nil
        guard device != nil else { status = "扫描 Mac 上的二维码开始配对"; return }
        let stamp = generation, url = cacheDirectory.appendingPathComponent(id+".json")
        files.async {
            let data = try? Data(contentsOf:url)
            let cache = data.flatMap {try? JSONDecoder().decode(MobileDiskCache.self,from:$0)}
            Task { @MainActor in
                guard stamp == self.generation, self.envelopes.isEmpty, let cache else { return }
                self.live = cache.live; self.recent = cache.recent.filter {$0.started >= Date().timeIntervalSince1970-7*86400}; self.trends = cache.trends.singleModelsOnly()
                self.envelopes = cache.versions.mapValues {_ in MobileEnvelope(dataset:"",revision:0,digest:"",payload:"")}
                for (key,revision) in cache.versions { self.envelopes[key] = MobileEnvelope(dataset:key,revision:revision,digest:cache.digests[key] ?? "",payload:"") }
                self.status = "已显示缓存"; self.persistCache()
            }
        }
        if foreground { connect(); startTimer() }
    }
    func beginPair(_ data: String) {
        guard devices.count < 3 else { error = "最多配对 3 台 Mac"; return }
        do {
            guard data.utf8.count <= 4096 else { throw MobileError.message("无效二维码") }
            let code = try JSONDecoder().decode(MobilePairCode.self,from:Data(data.utf8))
            guard code.version == 1, UUID(uuidString:code.host) != nil, code.pin.count == 64, code.ticket.count == 43,
                  code.expires > Date().timeIntervalSince1970, code.expires < Date().timeIntervalSince1970+600,
                  code.cloud == nil || code.cloud == MobileProtocol.cloudOrigin else { throw MobileError.message("二维码无效或已经过期") }
            let reader = MobileReader(id:UUID().uuidString,name:String(phoneName.prefix(40)),localSecret:try MobileProtocol.secret(),cloudSecret:try MobileProtocol.secret())
            deactivate(); foreground = true
            attempt = PairedMac(code:code,reader:reader); selected = code.host; envelopes = [:]
            live = nil; recent = []; trends = MobileTrends(daily:[],periods:[])
            pairing = true; status = "正在寻找 Mac，请保持同一局域网"; error = nil
            connect(); startTimer()
        } catch { self.error = error.localizedDescription }
    }
    func cancelPair() { attempt = nil; pairing = false; select(devices.first?.id ?? "") }
    func remove() {
        let id = selected; deactivate(); devices.removeAll {$0.id == id}; attempt = nil
        let saved = devices
        files.async { do { try MobileKeychain.save(MobileProtocol.encode(saved),key:"ios-devices-v1") } catch { Task { @MainActor in self.error = error.localizedDescription } } }
        let url = cacheDirectory.appendingPathComponent(id+".json")
        files.async { try? FileManager.default.removeItem(at:url) }
        foreground = true; select(devices.first?.id ?? "")
    }
    private func startTimer() {
        timer = Timer.scheduledTimer(withTimeInterval:3,repeats:true) { [weak self] _ in Task { @MainActor in
            guard let self, self.foreground else { return }
            if self.recent.contains(where:{$0.started < Date().timeIntervalSince1970-7*86400}) { self.recent.removeAll {$0.started < Date().timeIntervalSince1970-7*86400}; self.persistCache() }
            if let waiting = self.localWaiting, Date().timeIntervalSince(waiting) > 8 {
                self.connection?.cancel(); self.connection = nil; self.localReady = false; self.finishLocalSync()
            }
            if let attempt = self.attempt, Date().timeIntervalSince1970 > attempt.code.expires { self.error = "配对超时，请在 Mac 重新生成二维码"; self.cancelPair(); return }
            if self.pairing { if self.connection == nil && Date().timeIntervalSince(self.lastProbe) >= 3 { self.connect() } else { self.sendLocal() } }
            else if self.localReady { if Date().timeIntervalSince(self.lastProbe) >= 30 { self.lastProbe = Date(); self.sendLocal() } }
            else { self.cloudRefresh(); if Date().timeIntervalSince(self.lastProbe) > 60 { self.connect() } }
        }}
        timer?.tolerance = 1
    }
    private func connect() {
        guard foreground, let device else { return }
        lastProbe = Date(); browser?.cancel(); connection?.cancel(); connection = nil; localReady = false
        finishLocalSync()
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
            case .failed: self.localReady = false; self.connection = nil; value.cancel(); self.finishLocalSync(); self.cloudRefresh()
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
            if error != nil { Task { @MainActor in guard let self, stamp == self.generation, self.connection === connection else { return }; self.localReady = false; self.finishLocalSync() } }
        }
    }
    private func receive(_ value: NWConnection,stamp: UUID) {
        value.receiveMessage { [weak self,weak value] data,_,_,failure in
            let parsed = data.flatMap {bytes in bytes.count <= MobileProtocol.limit ? try? JSONDecoder().decode(MobileMessage.self,from:bytes) : nil}
            Task { @MainActor in
            guard let self, let value, stamp == self.generation, self.connection === value else { return }
            guard failure == nil, let message = parsed else {
                self.localReady = false; self.connection = nil; value.cancel(); self.finishLocalSync(); self.cloudRefresh(); return
            }
            let processing = self.beginSync(), pendingRequest = self.localOperation
            self.localOperation = nil; self.localWaiting = nil
            defer { self.endSync(processing); self.endSync(pendingRequest) }
            if message.action == "pending" { self.status = "请在 Mac 点击“确认配对”" }
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
                await self.apply(message.datasets ?? [])
                guard stamp == self.generation else { return }
                let stale = self.envelopes.contains { key,value in (message.known?[key] ?? 0) < value.revision }
                self.localReady = !stale; self.status = stale ? "Mac 数据版本较旧 · 保留较新缓存" : "局域网"
                if !stale { self.updated = Date() }
            } else if message.action == "error" { self.error = message.error; self.localReady = false; value.cancel(); self.connection = nil; if self.pairing { self.cancelPair() }; return }
            self.receive(value,stamp:stamp)
        }}
    }
    func refresh() {
        error = nil; cloudDenied = false
        if localReady { sendLocal() } else { nextCloud = .distantPast; cloudRefresh(); connect() }
    }
    private func cloudRefresh() {
        guard foreground, !pairing, !localReady, !cloudDenied, !cloudInFlight, Date() >= nextCloud, let device, device.code.cloud != nil else { return }
        cloudInFlight = true; let operation = beginSync(), stamp = generation, known = envelopes.mapValues(\.revision)
        let query = (["reader=\(device.reader.id)"]+known.map {"\($0.key)=\($0.value)"}).joined(separator:"&")
        task = Task {
            defer { endSync(operation); if stamp == generation { cloudInFlight = false } }
            do {
                let data = try await http.request("/v1/hosts/\(device.id)/sync?"+query,token:device.reader.cloudSecret)
                let response = try await Task.detached(priority:.utility) {try JSONDecoder().decode(MobileMessage.self,from:data)}.value
                guard stamp == generation, !Task.isCancelled else { return }
                if !localReady { await apply(response.datasets ?? []); guard stamp == generation else { return }; if !localReady { status = "云同步"; updated = response.seen.map {Date(timeIntervalSince1970:$0)} } }
                failures = 0; nextCloud = Date().addingTimeInterval(live?.runningCount ?? 0 > 0 ? 15 : 60)
            } catch {
                guard stamp == generation, !Task.isCancelled else { return }
                failures += 1; nextCloud = Date().addingTimeInterval(min(900,Double(30 * (1 << min(failures,5)))))
                if case MobileError.http(let code,let retry) = error {
                    if let retry { nextCloud = max(nextCloud,Date().addingTimeInterval(retry)) }
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
                case "recent": guard let recentValue else { throw MobileError.message("invalid recent") }; recent = recentValue.filter {$0.started >= Date().timeIntervalSince1970-7*86400}.prefix(200).map {$0}
                case "trends": guard let trendsValue else { throw MobileError.message("invalid trends") }; trends = trendsValue
                default: break
                }
                envelopes[value.dataset] = value; changed = true
            } catch { self.error = "同步数据格式不兼容，请更新 Codexio" }
        }
        if renamed { do {try await saveDevices()} catch {self.error = error.localizedDescription}; guard stamp == generation else { return } }
        if changed { persistCache() }
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
