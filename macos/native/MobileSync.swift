import Foundation
import AppKit
import SwiftUI
import Network
import Security
import CoreImage.CIFilterBuiltins

private struct MobileHostIdentity: Codable {
    var id: String
    var writer: String
    var cloudEnabled = false
    var certificate: Data
    var password: String
    var pin: String
    var readers: [MobileReader] = []
    var revoked: [String] = []
}

// Native listener and background export reuse the existing UsageSnapshot. No collector.
// Transport, identity, outbox and revision state are confined to `queue`;
// published presentation state is only read/written on the main queue.
final class MobileSync: ObservableObject, @unchecked Sendable {
    @Published private(set) var enabled = false
    @Published private(set) var cloudEnabled = false
    @Published private(set) var status = "尚未开启"
    @Published private(set) var qrImage: NSImage?
    @Published private(set) var pending: MobileReader?
    @Published private(set) var readers: [MobileReader] = []
    @Published private(set) var previewEnabled = false
    @Published private(set) var deviceName = "我的 Mac"
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.mobile",qos:.utility)
    private let paths: AppPaths
    private var host: MobileHostIdentity?
    private var listener: NWListener?
    private var connections: [ObjectIdentifier:NWConnection] = [:]
    private var authenticated: [ObjectIdentifier:String] = [:]
    private var datasets: [String:MobileEnvelope] = [:]
    private var sent: [String:Int64] = [:]
    private var cloudReaders = Set<String>()
    private var timer: DispatchSourceTimer?
    private var sending = false
    private var active = false
    private var generation = UUID()
    private var ticket: MobilePairCode?
    private var candidate: MobileReader?
    private var exportRevision: UUID?
    private var exportPreview = false
    private var exportDay = ""
    private var lastPrivacy = false
    private var nextHistory = Date.distantPast
    private var lastTaskKey = ""
    private var retryAt = Date.distantPast
    private var failures = 0
    private var urgentPending = false
    private var transport = MobileHTTP()
    init(paths: AppPaths) { self.paths = paths; if !paths.mock { previewEnabled = UserDefaults.standard.bool(forKey:"codexio.mobile.preview"); deviceName = UserDefaults.standard.string(forKey:"codexio.mobile.name") ?? "我的 Mac" } }
    private var stateURL: URL { paths.data.appendingPathComponent("mobile-projection.json") }
    private func report(_ text: String) { DispatchQueue.main.async { if self.status != text { self.status = text } } }
    private func saveHost() throws {
        guard let host else { return }
        try MobileKeychain.save(MobileProtocol.encode(host),key:"mac-host-v1")
    }
    private func persist() throws {
        let bytes = try MobileProtocol.encode(datasets)
        try bytes.write(to:stateURL,options:.atomic)
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:stateURL.path)
    }
    private func identity() throws -> SecIdentity {
        if host == nil {
            if let data = try MobileKeychain.read("mac-host-v1") { host = try JSONDecoder().decode(MobileHostIdentity.self,from:data) }
            else {
                let temp = FileManager.default.temporaryDirectory.appendingPathComponent("codexio-tls-"+UUID().uuidString,isDirectory:true)
                try FileManager.default.createDirectory(at:temp,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
                defer { try? FileManager.default.removeItem(at:temp) }
                let password = try MobileProtocol.secret()
                func openssl(_ args: [String]) throws {
                    let process = Process(); process.executableURL = URL(fileURLWithPath:"/usr/bin/openssl"); process.arguments = args; process.currentDirectoryURL = temp
                    process.environment = ["CODEXIO_CERT_PASSWORD":password,"PATH":"/usr/bin:/bin"]
                    process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
                    try process.run(); process.waitUntilExit()
                    guard process.terminationStatus == 0 else { throw MobileError.message("无法创建本地 TLS 证书") }
                }
                try openssl(["req","-x509","-newkey","rsa:2048","-nodes","-keyout","key.pem","-out","cert.pem","-days","3650","-subj","/CN=Codexio Local"])
                try openssl(["pkcs12","-export","-inkey","key.pem","-in","cert.pem","-out","host.p12","-passout","env:CODEXIO_CERT_PASSWORD"])
                try openssl(["x509","-in","cert.pem","-outform","DER","-out","cert.der"])
                host = MobileHostIdentity(id:UUID().uuidString,writer:try MobileProtocol.secret(),certificate:try Data(contentsOf:temp.appendingPathComponent("host.p12")),password:password,pin:MobileProtocol.hash(try Data(contentsOf:temp.appendingPathComponent("cert.der"))))
                try saveHost()
            }
        }
        guard let host else { throw MobileError.message("身份不可用") }
        var imported: CFArray?
        let options = [kSecImportExportPassphrase as String:host.password,kSecImportToMemoryOnly as String:true] as [String:Any]
        guard SecPKCS12Import(host.certificate as CFData,options as CFDictionary,&imported) == errSecSuccess,
              let rows = imported as? [[String:Any]], let raw = rows.first?[kSecImportItemIdentity as String] else { throw MobileError.message("TLS 身份读取失败") }
        return raw as! SecIdentity
    }
    func start() {
        guard !paths.mock else { return }
        let allowPreview = previewEnabled
        queue.async {
            guard !self.active else { return }
            do {
                let identity = try self.identity()
                if let data = try? Data(contentsOf:self.stateURL) { self.datasets = try JSONDecoder().decode([String:MobileEnvelope].self,from:data) }
                if !allowPreview {
                    if let value = self.datasets["recent"], let rows = try? value.decode([MobileRequest].self) { try self.put("recent",rows.map { row in var copy = row; copy.preview = nil; return copy }) }
                    if let value = self.datasets["live"], var live = try? value.decode(MobileLive.self) { live.task?.preview = nil; try self.put("live",live) }
                    try self.persist()
                }
                self.active = true; self.generation = UUID(); self.transport = MobileHTTP()
                UserDefaults.standard.set(true,forKey:"codexio.mobile.enabled")
                let listener = try NWListener(using:MobileProtocol.parameters(identity:identity,queue:self.queue))
                listener.service = .init(name:self.host!.id,type:MobileProtocol.service)
                listener.newConnectionHandler = { [weak self = self] in self?.accept($0) }
                listener.stateUpdateHandler = { [weak self = self] state in
                    if case .ready = state { self?.report("局域网服务已开启") }
                    if case .failed(let error) = state { self?.report(error.localizedDescription) }
                }
                self.listener = listener; listener.start(queue:self.queue)
                let timer = DispatchSource.makeTimerSource(queue:self.queue)
                timer.schedule(deadline:.now()+2,repeating:30,leeway:.seconds(3)); timer.setEventHandler { [weak self = self] in self?.upload() }; timer.resume(); self.timer = timer
                let cloud = self.host?.cloudEnabled ?? false, readers = self.host?.readers ?? []
                DispatchQueue.main.async { self.enabled = true; self.cloudEnabled = cloud; self.readers = readers }
            } catch { self.report(error.localizedDescription); self.active = false }
        }
    }
    func stop(disable: Bool = false) {
        guard !paths.mock else { return }
        if disable { UserDefaults.standard.set(false,forKey:"codexio.mobile.enabled") }
        queue.async {
            self.active = false; self.generation = UUID(); self.timer?.cancel(); self.timer = nil
            self.listener?.cancel(); self.listener = nil
            self.connections.values.forEach {$0.cancel()}; self.connections.removeAll(); self.authenticated.removeAll()
            self.transport.stop(); self.sending = false; self.ticket = nil; self.candidate = nil
            DispatchQueue.main.async { self.enabled = false; self.qrImage = nil; self.pending = nil }
            self.report("同步已停止；云端保留最后已上传的数据")
        }
    }
    func setPreview(_ value: Bool) { previewEnabled = value; if !paths.mock { UserDefaults.standard.set(value,forKey:"codexio.mobile.preview") }; queue.async { self.exportRevision = nil } }
    func rename(_ value: String) { let name = String(value.trimmingCharacters(in:.whitespacesAndNewlines).prefix(30)); guard !name.isEmpty else { return }; deviceName = name; if !paths.mock { UserDefaults.standard.set(name,forKey:"codexio.mobile.name") } }
    func pair() {
        let name = deviceName
        queue.async {
            guard self.active, let host = self.host else { return }
            do {
                self.ticket = MobilePairCode(host:host.id,name:name,pin:host.pin,ticket:try MobileProtocol.secret(),expires:Date().timeIntervalSince1970+300,cloud:host.cloudEnabled ? MobileProtocol.cloudOrigin : nil)
                self.candidate = nil
                let filter = CIFilter.qrCodeGenerator(); filter.message = try MobileProtocol.encode(self.ticket!); filter.correctionLevel = "M"
                guard let output = filter.outputImage?.transformed(by:.init(scaleX:6,y:6)), let cg = CIContext().createCGImage(output,from:output.extent) else { return }
                let image = NSImage(cgImage:cg,size:NSSize(width:220,height:220))
                DispatchQueue.main.async { self.qrImage = image; self.pending = nil }
                let issued = self.ticket?.ticket
                self.queue.asyncAfter(deadline:.now()+300) {
                    guard self.ticket?.ticket == issued else { return }
                    self.ticket = nil; self.candidate = nil
                    DispatchQueue.main.async { self.qrImage = nil; self.pending = nil }
                }
            } catch { self.report(error.localizedDescription) }
        }
    }
    func approve(_ allowed: Bool) {
        queue.async {
            guard let candidate = self.candidate, let ticket = self.ticket, ticket.expires > Date().timeIntervalSince1970 else { return }
            if allowed, (self.host?.readers.count ?? 3) < 3 {
                self.host?.readers.append(candidate)
                do { try self.saveHost(); self.upload() } catch { self.report(error.localizedDescription) }
            }
            self.ticket = nil; self.candidate = nil
            let readers = self.host?.readers ?? []
            DispatchQueue.main.async { self.pending = nil; self.qrImage = nil; self.readers = readers }
        }
    }
    func revoke(_ id: String) {
        queue.async {
            self.host?.readers.removeAll {$0.id == id}; self.host?.revoked.append(id)
            for (key,reader) in self.authenticated where reader == id { self.connections[key]?.cancel() }
            do { try self.saveHost(); self.upload() } catch { self.report(error.localizedDescription) }
            let readers = self.host?.readers ?? []
            DispatchQueue.main.async { self.readers = readers }
        }
    }
    func enroll(_ invite: String) {
        queue.async {
            guard self.active, let host = self.host, !self.sending else { return }
            self.sending = true; let generation = self.generation, transport = self.transport
            let body = try? JSONSerialization.data(withJSONObject:["host":host.id,"writer":host.writer,"name":"我的 Mac"])
            Task {
                do {
                    _ = try await transport.request("/v1/enroll",method:"POST",token:invite.trimmingCharacters(in:.whitespacesAndNewlines),body:body)
                    self.queue.async {
                        guard self.active, self.generation == generation else { return }
                        self.sending = false; self.host?.cloudEnabled = true
                        do { try self.saveHost() } catch { self.report(error.localizedDescription); return }
                        DispatchQueue.main.async { self.cloudEnabled = true }
                        self.report("云端已启用"); self.upload()
                    }
                } catch { self.queue.async { guard generation == self.generation else { return }; self.sending = false; self.report(error.localizedDescription) } }
            }
        }
    }
    private func accept(_ connection: NWConnection) {
        guard active, connections.count < 8 else { connection.cancel(); return }
        let key = ObjectIdentifier(connection); connections[key] = connection
        connection.stateUpdateHandler = { [weak self,weak connection] state in
            guard let self, let connection else { return }
            if case .ready = state { self.receive(connection) }
            if case .failed = state { self.connections.removeValue(forKey:key); self.authenticated.removeValue(forKey:key); connection.cancel() }
            if case .cancelled = state { self.connections.removeValue(forKey:key); self.authenticated.removeValue(forKey:key) }
        }
        connection.start(queue:queue)
        queue.asyncAfter(deadline:.now()+10) { [weak self,weak connection] in
            if self?.authenticated[key] == nil { connection?.cancel() }
        }
    }
    private func receive(_ connection: NWConnection) {
        connection.receiveMessage { [weak self,weak connection] data,_,_,error in
            guard let self, let connection, self.active, error == nil, let data, data.count <= MobileProtocol.limit,
                  let message = try? JSONDecoder().decode(MobileMessage.self,from:data) else { connection?.cancel(); return }
            let key = ObjectIdentifier(connection)
            if let reader = message.reader, self.host?.readers.contains(where:{$0.id == reader.id && MobileProtocol.equal($0.localSecret,reader.localSecret)}) == true {
                self.authenticated[key] = reader.id
                let known = message.known ?? [:]
                MobileProtocol.send(MobileMessage(action:"sync",known:self.datasets.mapValues(\.revision),datasets:self.datasets.values.filter {$0.revision > known[$0.dataset,default:0]},seen:Date().timeIntervalSince1970,cloud:self.cloudReaders.contains(reader.id) ? MobileProtocol.cloudOrigin : nil),over:connection)
            } else if message.action == "pair", let reader = message.reader,
                      reader.id.count <= 64, reader.name.count <= 40, reader.localSecret.count == 43, reader.cloudSecret.count == 43,
                      let ticket = self.ticket, ticket.expires > Date().timeIntervalSince1970,
                      MobileProtocol.equal(message.ticket ?? "",ticket.ticket), self.candidate == nil || self.candidate?.id == reader.id {
                self.candidate = reader
                DispatchQueue.main.async { self.pending = reader }
                MobileProtocol.send(MobileMessage(action:"pending"),over:connection)
            } else { MobileProtocol.send(MobileMessage(action:"error",error:"配对已过期、被拒绝或设备已撤销"),over:connection) }
            self.receive(connection)
        }
    }
    private func put<T: Encodable>(_ dataset: String,_ value: T) throws {
        let data = try MobileProtocol.encode(value)
        let limit = dataset == "live" ? 8000 : dataset == "recent" ? 120000 : 80000
        guard data.count < limit else { throw MobileError.message("手机摘要超过容量限制") }
        let hash = MobileProtocol.hash(data)
        guard datasets[dataset]?.digest != hash else { return }
        let revision = max(datasets[dataset]?.revision ?? 0,Int64(Date().timeIntervalSince1970*1000))+1
        datasets[dataset] = MobileEnvelope(dataset:dataset,revision:revision,digest:hash,payload:String(decoding:data,as:UTF8.self))
    }
    func update(_ snapshot: UsageSnapshot,quota: MenuQuota,observed: Date? = nil) {
        guard !paths.mock, enabled else { return }
        let previews = previewEnabled, name = deviceName
        queue.async {
            guard self.active else { return }
            do {
                func metric(_ value: UsageSummary) -> MobileMetric { MobileMetric(tokens:value.tokens,cost:value.cost,requests:value.requests,costComplete:value.unknownCosts == 0,hitRate:value.cacheRate) }
                func request(_ value: UsageRow) -> MobileRequest {
                    var preview = previews ? String(value.raw.string("prompt_preview").prefix(80)) : ""
                    while preview.utf8.count > 240 { preview.removeLast() }
                    return MobileRequest(id:MobileProtocol.hash(Data(value.id.utf8)),started:value.date?.timeIntervalSince1970 ?? 0,status:value.raw.string("status",value.raw.flag("duration_running") ? "running" : "completed"),preview:preview.isEmpty ? nil : preview,model:modelName(value.raw.string("model")),effort:logEffortName(value.raw.string("reasoning_effort")),speed:value.raw.string("service_tier"),tokens:value.tokens,cost:value.cost,duration:value.raw.number("duration_ms").map {$0/1000})
                }
                func window(_ value: QuotaWindow?) -> MobileQuota { MobileQuota(remaining:value?.remaining,reset:value?.reset?.timeIntervalSince1970,observed:quota.updated?.timeIntervalSince1970,retained:quota.retained) }
                let sample = (observed ?? snapshot.updated).map {floor($0.timeIntervalSince1970/300)*300}
                let live = MobileLive(name:name,timeZone:TimeZone.current.identifier,observed:sample,task:snapshot.widgetRequest?.raw.flag("duration_running") == true ? snapshot.widgetRequest.map(request) : nil,runningCount:snapshot.requests.filter {$0.local && $0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") && $0.raw.flag("duration_running")}.count,today:metric(snapshot.summaries["today"] ?? UsageSummary()),five:window(quota.five),week:window(quota.week))
                let old = self.datasets
                try self.put("live",live)
                let dayKey = String(Calendar.current.startOfDay(for:Date()).timeIntervalSince1970)+TimeZone.current.identifier
                let taskKey = (live.task?.id ?? "")+"/"+String(live.runningCount)
                let urgent = self.exportPreview != previews || self.exportDay != dayKey || self.lastTaskKey != taskKey
                if urgent || (self.exportRevision != snapshot.revision && Date() >= self.nextHistory) {
                    let floor = Date().addingTimeInterval(-7*86400)
                    let main = snapshot.requests.filter {$0.local && $0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") && ($0.date ?? .distantFuture) <= Date()}
                    try self.put("recent",main.lazy.filter {($0.date ?? .distantPast) >= floor}.prefix(200).map(request))
                    let calendar = Calendar.current, today = calendar.startOfDay(for:Date())
                    let oldest = calendar.date(byAdding:.day,value:-89,to:today)!
                    let days = snapshot.allDays.filter {$0.date >= oldest && $0.date <= today}.map {MobileDay(id:String($0.date.timeIntervalSince1970),start:$0.date.timeIntervalSince1970,metric:MobileMetric(tokens:$0.tokens,cost:$0.cost,requests:$0.requests))}
                    let periods = [7,30,90].map { count -> MobilePeriod in
                        let start = calendar.date(byAdding:.day,value:1-count,to:today)!
                        let calls = snapshot.calls.filter {$0.local && ($0.date ?? .distantPast) >= start && ($0.date ?? .distantFuture) <= Date()}
                        let requests = main.filter {($0.date ?? .distantPast) >= start}
                        let grouped = Dictionary(grouping:calls,by:{$0.raw.string("model","unknown")})
                        let requestModels = Dictionary(grouping:requests,by:{$0.raw.string("model","unknown")})
                        let keys = Set(grouped.keys).union(requestModels.keys).sorted()
                        let models = keys.map {key in MobileModel(id:key,name:modelName(key),metric:metric(UsageSummary(rows:grouped[key] ?? [],requests:requestModels[key]?.count ?? 0)))}
                        return MobilePeriod(days:count,total:metric(UsageSummary(rows:calls,requests:requests.count)),models:models)
                    }
                    try self.put("trends",MobileTrends(daily:days,periods:periods))
                    self.exportRevision = snapshot.revision; self.exportPreview = previews; self.exportDay = dayKey
                    self.nextHistory = Date().addingTimeInterval(60)
                }
                if old != self.datasets {
                    try self.persist()
                    for (key,connection) in self.connections where self.authenticated[key] != nil {
                        MobileProtocol.send(MobileMessage(action:"sync",known:self.datasets.mapValues(\.revision),datasets:self.datasets.values.filter {old[$0.dataset] != $0},seen:Date().timeIntervalSince1970),over:connection)
                    }
                }
                if urgent || self.lastPrivacy != previews { self.lastPrivacy = previews; self.lastTaskKey = taskKey; self.urgentPending = true; self.upload() }
            } catch { self.report(error.localizedDescription) }
        }
    }
    private func upload() {
        guard active, let host, host.cloudEnabled, !sending, Date() >= retryAt else { return }
        let changed = datasets.values.filter {sent[$0.dataset] != $0.revision}
        let readers = host.readers.filter {!cloudReaders.contains($0.id)}
        guard !changed.isEmpty || !readers.isEmpty || !host.revoked.isEmpty else { return }
        sending = true; urgentPending = false; let generation = generation, transport = transport
        Task {
            do {
                for id in host.revoked { _ = try await transport.request("/v1/hosts/\(host.id)/readers/\(id)",method:"DELETE",token:host.writer) }
                for reader in readers {
                    let body = try JSONSerialization.data(withJSONObject:["id":reader.id,"secret":reader.cloudSecret])
                    _ = try await transport.request("/v1/hosts/\(host.id)/readers",method:"PUT",token:host.writer,body:body)
                }
                for value in changed { _ = try await transport.request("/v1/hosts/\(host.id)/data/\(value.dataset)",method:"PUT",token:host.writer,body:MobileProtocol.encode(value)) }
                self.queue.async {
                    guard self.generation == generation else { return }
                    for value in changed { self.sent[value.dataset] = value.revision }
                    readers.forEach {self.cloudReaders.insert($0.id)}
                    let granted = Set(readers.map(\.id))
                    for (key,connection) in self.connections where granted.contains(self.authenticated[key] ?? "") {
                        MobileProtocol.send(MobileMessage(action:"sync",known:self.datasets.mapValues(\.revision),datasets:[],seen:Date().timeIntervalSince1970,cloud:MobileProtocol.cloudOrigin),over:connection)
                    }
                    self.host?.revoked.removeAll {host.revoked.contains($0)}
                    try? self.saveHost(); self.sending = false; self.failures = 0
                    let pending = self.datasets.values.contains {self.sent[$0.dataset] != $0.revision}
                    self.report(pending ? "局域网优先 · 云端有更新待同步" : "局域网优先 · 云端已同步")
                    if self.urgentPending { self.upload() }
                }
            } catch {
                self.queue.async {
                    guard self.generation == generation else { return }
                    self.sending = false; self.failures += 1
                    self.retryAt = Date().addingTimeInterval(min(900,pow(2,Double(min(6,self.failures)))*15)+Double.random(in:0...10))
                    self.report(error.localizedDescription)
                }
            }
        }
    }
}

struct MobileSyncSettings: View {
    @ObservedObject var sync: MobileSync
    let refresh: () -> Void
    @State private var invite = ""
    @State private var name = ""
    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            Toggle("iPhone 同步",isOn:Binding(get:{sync.enabled},set:{$0 ? sync.start() : sync.stop(disable:true)}))
            Text(sync.status).font(.caption).foregroundStyle(.secondary)
            if sync.enabled {
                TextField("设备名称",text:$name).onAppear { name = sync.deviceName }.onSubmit { sync.rename(name); refresh() }
                Toggle("允许同步请求短预览",isOn:Binding(get:{sync.previewEnabled},set:{sync.setPreview($0);refresh()}))
                Text("仅同步额度、汇总和最近请求简表。短预览可能包含敏感内容；关闭后不会上传正文。首次配对请让 iPhone 与 Mac 处于可互通的局域网。").font(.caption).foregroundStyle(.secondary)
                if !sync.cloudEnabled {
                    SecureField("云端邀请码（可选）",text:$invite)
                    Button("启用 Cloudflare 同步") { sync.enroll(invite); invite = "" }.disabled(invite.isEmpty)
                }
                Button("添加 iPhone · 生成二维码") { sync.pair(); refresh() }.disabled(sync.readers.count >= 3)
                if let image = sync.qrImage { Image(nsImage:image).interpolation(.none).resizable().scaledToFit().frame(width:220,height:220); Text("二维码 5 分钟有效，请勿转发").font(.caption).foregroundStyle(.secondary) }
                if let pending = sync.pending {
                    Text("允许 \(pending.name) 读取这台 Mac 的摘要？")
                    HStack { Button("拒绝") { sync.approve(false) }; Button("确认配对") { sync.approve(true) } }
                }
                ForEach(sync.readers) { reader in HStack { Text(reader.name); Spacer(); Button("撤销") { sync.revoke(reader.id) } } }
            }
        }
    }
}
