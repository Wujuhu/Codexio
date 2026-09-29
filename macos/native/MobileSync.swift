import Foundation
import AppKit
import SwiftUI
import Network
import Security
import CoreImage.CIFilterBuiltins
import SystemConfiguration
import ImageIO
import UniformTypeIdentifiers

private struct MobileHostIdentity: Codable {
    var id: String
    var writer: String
    var cloudEnabled = false
    var certificate: Data
    var password: String
    var pin: String
    var readers: [MobileReader] = []
    var revoked: [String] = []
    var pendingKeyRemoval: Bool?
    var notes: [String:String]?
    var lastSync: SyncStamp?
    var localSyncs: [String:SyncStamp]?
    var cloudSync: SyncStamp?
}
struct SyncStamp: Codable { var time: Date; var route: String }
private struct MobileDetailStamp: Codable {
    var revision: Int64
    var digest: String
    var sent: String?
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
    @Published private(set) var deviceName = "我的 Mac"
    @Published private(set) var removingKey = false
    @Published private(set) var notes: [String:String] = [:]
    @Published private(set) var lastSync: SyncStamp?
    @Published private(set) var localSyncs: [String:SyncStamp] = [:]
    @Published private(set) var cloudSync: SyncStamp?
    @Published private(set) var syncError: String?
    @Published var notice: String?
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.mobile",qos:.utility)
    private let paths: AppPaths
    private let detailProvider: ((String) throws -> Object?)?
    private var details: [String:MobileEnvelope] = [:]
    private var detailSizes: [String:Int] = [:]
    private var detailDates: [String:Double] = [:]
    private var detailSources: [String:String] = [:]
    private var detailStamps: [String:MobileDetailStamp] = [:]
    private var detailDeferred: [String:Date] = [:]
    private var cloudDetails: Bool?
    private var nextCapabilityCheck = Date.distantPast
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
    private var pairingUIRevision = UUID() // Main queue, used only by UI callbacks.
    private var ticketUIRevision: UUID? // Service queue.
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
    private var metadataSaved = Date.distantPast
    init(paths: AppPaths, detailProvider: ((String) throws -> Object?)? = nil) {
        self.paths = paths
        self.detailProvider = detailProvider
        if !paths.mock {
            let saved = UserDefaults.standard.string(forKey:"codexio.mobile.name")?.trimmingCharacters(in:.whitespacesAndNewlines) ?? ""
            let system = SCDynamicStoreCopyComputerName(nil,nil) as String? ?? "Mac"
            deviceName = saved.isEmpty || saved == "我的 Mac" ? String(system.prefix(40)) : saved
        }
    }
    private var stateURL: URL { paths.data.appendingPathComponent("mobile-projection.json") }
    private var detailStateURL: URL { paths.data.appendingPathComponent("mobile-detail-revisions.json") }
    private var capabilities: [String] { detailProvider == nil ? [] : [MobileProtocol.detailCapability] }
    private func saveDetailStamps() throws {
        try MobileProtocol.encode(detailStamps).write(to:detailStateURL,options:.atomic)
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:detailStateURL.path)
    }
    private func report(_ text: String) {
        let normal = ["尚未开启","局域网服务已开启","云端已启用","同步已停止","局域网优先"].contains {text.hasPrefix($0)}
        DispatchQueue.main.async { if self.status != text { self.status = text }; self.syncError = normal ? nil : text }
    }
    private func synchronized(_ route: String, reader: String? = nil) {
        let stamp = SyncStamp(time:Date(),route:route)
        self.host?.lastSync = stamp
        if route == "cloud" { self.host?.cloudSync = stamp }
        if let reader { if host?.localSyncs == nil {host?.localSyncs = [:]}; host?.localSyncs?[reader] = stamp }
        let local = host?.localSyncs ?? [:], cloud = host?.cloudSync
        DispatchQueue.main.async { self.lastSync = stamp; self.localSyncs = local; self.cloudSync = cloud }
        if Date().timeIntervalSince(metadataSaved) >= 300 { try? saveHost(); metadataSaved = Date() }
    }
    func setNote(_ id: String,_ value: String) {
        let note = String(value.trimmingCharacters(in:.whitespacesAndNewlines).prefix(80))
        queue.async {
            guard self.host?.readers.contains(where:{$0.id == id}) == true, (self.host?.notes?[id] ?? "") != note else { return }
            if self.host?.notes == nil {self.host?.notes = [:]}; self.host?.notes?[id] = note
            do {try self.saveHost(); let notes = self.host?.notes ?? [:]; DispatchQueue.main.async {self.notes = notes}} catch {self.report(error.localizedDescription)}
        }
    }
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
        queue.async {
            guard !self.active else { return }
            do {
                let identity = try self.identity()
                if let data = try? Data(contentsOf:self.stateURL) { self.datasets = try JSONDecoder().decode([String:MobileEnvelope].self,from:data) }
                if let data = try? Data(contentsOf:self.detailStateURL) { self.detailStamps = (try? JSONDecoder().decode([String:MobileDetailStamp].self,from:data)) ?? [:] }
                self.cloudDetails = nil; self.nextCapabilityCheck = .distantPast
                self.exportRevision = nil; self.nextHistory = .distantPast
                if let value = self.datasets["trends"], let trends = try? value.decode(MobileTrends.self) { try self.put("trends",trends.singleModelsOnly()); try self.persist() }
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
                let cloud = self.host?.cloudEnabled ?? false, readers = self.host?.readers ?? [], notes = self.host?.notes ?? [:], last = self.host?.lastSync, local = self.host?.localSyncs ?? [:], removing = self.host?.pendingKeyRemoval == true, cloudStamp = self.host?.cloudSync
                DispatchQueue.main.async { self.enabled = true; self.cloudEnabled = cloud; self.readers = readers; self.notes = notes; self.lastSync = last; self.localSyncs = local; self.removingKey = removing; self.cloudSync = cloudStamp }
            } catch { self.report(error.localizedDescription); self.active = false }
        }
    }
    func stop(disable: Bool = false) {
        guard !paths.mock else { return }
        invalidatePairing()
        if disable { UserDefaults.standard.set(false,forKey:"codexio.mobile.enabled") }
        queue.async {
            self.active = false; self.generation = UUID(); self.timer?.cancel(); self.timer = nil
            self.listener?.cancel(); self.listener = nil
            self.connections.values.forEach {$0.cancel()}; self.connections.removeAll(); self.authenticated.removeAll()
            self.transport.stop(); self.sending = false; self.ticket = nil; self.candidate = nil
            self.details.removeAll(); self.detailSources.removeAll(); self.detailSizes.removeAll(); self.detailDates.removeAll()
            DispatchQueue.main.async { self.enabled = false; self.qrImage = nil; self.pending = nil }
            self.report("同步已停止；云端保留最后已上传的数据")
        }
    }
    func rename(_ value: String) { let name = String(value.trimmingCharacters(in:.whitespacesAndNewlines).prefix(30)); guard !name.isEmpty else { return }; deviceName = name; if !paths.mock { UserDefaults.standard.set(name,forKey:"codexio.mobile.name") } }
    func pair() {
        let name = deviceName
        let revision = UUID(); pairingUIRevision = revision; qrImage = nil; pending = nil
        queue.async {
            guard self.active, let host = self.host else { return }
            do {
                self.ticket = MobilePairCode(host:host.id,name:name,pin:host.pin,ticket:try MobileProtocol.secret(),expires:Date().timeIntervalSince1970+300,cloud:host.cloudEnabled ? MobileProtocol.cloudOrigin : nil)
                self.ticketUIRevision = revision
                self.candidate = nil
                let filter = CIFilter.qrCodeGenerator(); filter.message = try MobileProtocol.encode(self.ticket!); filter.correctionLevel = "M"
                guard let output = filter.outputImage?.transformed(by:.init(scaleX:6,y:6)), let cg = CIContext().createCGImage(output,from:output.extent) else { return }
                let image = NSImage(cgImage:cg,size:NSSize(width:220,height:220))
                DispatchQueue.main.async { guard self.pairingUIRevision == revision else {return}; self.qrImage = image; self.pending = nil }
                let issued = self.ticket?.ticket
                self.queue.asyncAfter(deadline:.now()+300) {
                    guard self.ticket?.ticket == issued else { return }
                    self.ticket = nil; self.candidate = nil; self.ticketUIRevision = nil
                    DispatchQueue.main.async { guard self.pairingUIRevision == revision else {return}; self.qrImage = nil; self.pending = nil }
                }
            } catch { self.report(error.localizedDescription) }
        }
    }
    func invalidatePairing() {
        pairingUIRevision = UUID(); qrImage = nil; pending = nil
        queue.async { self.ticket = nil; self.candidate = nil; self.ticketUIRevision = nil }
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
            self.host?.notes?.removeValue(forKey:id); self.host?.localSyncs?.removeValue(forKey:id)
            for (key,reader) in self.authenticated where reader == id {
                if let connection = self.connections[key] { MobileProtocol.send(MobileMessage(action:"revoked",error:"REVOKED"),over:connection) {_ in connection.cancel()} }
                self.authenticated.removeValue(forKey:key)
            }
            do { try self.saveHost(); self.upload() } catch { self.report(error.localizedDescription) }
            let readers = self.host?.readers ?? []
            DispatchQueue.main.async { self.readers = readers; self.notice = "已在本机撤销此 iPhone，请在 iPhone 的 Codexio 设置中删除这台 Mac。云端撤销将在连接可用时同步。" }
        }
    }
    func enroll(_ invite: String) {
        let name = deviceName
        queue.async {
            guard self.active, self.host?.pendingKeyRemoval != true, !self.sending else { return }
            if self.host?.writer.isEmpty == true { do {self.host?.writer = try MobileProtocol.secret(); try self.saveHost()} catch {self.report(error.localizedDescription);return} }
            guard let host = self.host else { return }
            self.sending = true; let generation = self.generation, transport = self.transport
            let body = try? JSONSerialization.data(withJSONObject:["host":host.id,"writer":host.writer,"name":name])
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
    func removeCloudKey() {
        queue.async {
            guard self.active, self.host?.cloudEnabled == true else { return }
            let previous = self.host
            self.host?.cloudEnabled = false; self.host?.pendingKeyRemoval = true
            do { try self.saveHost() } catch { self.host = previous; self.report(error.localizedDescription); return }
            DispatchQueue.main.async { self.cloudEnabled = false; self.removingKey = true }
            self.retryAt = .distantPast; self.upload()
        }
    }
    private func finishKeyRemoval() {
        guard active, !sending, let host, host.pendingKeyRemoval == true, Date() >= retryAt else { return }
        sending = true; let stamp = generation, transport = transport
        Task {
            do {
                _ = try await transport.request("/v1/hosts/\(host.id)/key",method:"DELETE",token:host.writer)
                self.queue.async {
                    guard self.generation == stamp else { return }
                    let pending = self.host
                    self.host?.writer = ""; self.host?.pendingKeyRemoval = false
                    do { try self.saveHost() } catch {self.host=pending;self.report(error.localizedDescription);self.sending=false;return}
                    self.sending = false; self.cloudReaders.removeAll(); self.sent.removeAll(); self.failures = 0
                    self.cloudDetails = nil; self.nextCapabilityCheck = .distantPast
                    for id in self.detailStamps.keys { self.detailStamps[id]?.sent = nil }; try? self.saveDetailStamps()
                    DispatchQueue.main.async {self.removingKey = false; self.syncError = nil; self.notice = "云端密钥已移除。再次启用云同步时，需要添加新的邀请码。局域网配对仍可使用。"}
                }
            } catch {
                self.queue.async {guard self.generation == stamp else {return}; self.sending=false; self.retryAt=Date().addingTimeInterval(60); self.report("密钥移除待完成："+error.localizedDescription)}
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
            if message.action == "ack", let reader = self.authenticated[key] {
                self.synchronized("lan",reader:reader); self.receive(connection); return
            }
            if let reader = message.reader, self.host?.readers.contains(where:{$0.id == reader.id && MobileProtocol.equal($0.localSecret,reader.localSecret)}) == true {
                let name = reader.name.trimmingCharacters(in:.whitespacesAndNewlines)
                if !name.isEmpty, name.count <= 40, let index = self.host?.readers.firstIndex(where:{$0.id == reader.id}), self.host?.readers[index].name != name {
                    self.host?.readers[index].name = name
                    do { try self.saveHost(); let readers = self.host?.readers ?? []; DispatchQueue.main.async { self.readers = readers } }
                    catch { self.report(error.localizedDescription) }
                }
                self.authenticated[key] = reader.id
                if message.action == "detail", let id = message.detailID, id.count == 64 {
                    do {
                        MobileProtocol.send(try self.detailMessage(id,full:message.full == true,part:message.detailPart ?? 0),over:connection)
                    } catch {
                        MobileProtocol.send(MobileMessage(action:"detail",error:"DETAIL_UNAVAILABLE",capabilities:self.capabilities,detailID:id,full:message.full == true),over:connection)
                    }
                    self.receive(connection); return
                }
                let known = message.known ?? [:]
                MobileProtocol.send(MobileMessage(action:"sync",known:self.datasets.mapValues(\.revision),datasets:self.datasets.values.filter {$0.revision > known[$0.dataset,default:0]},seen:Date().timeIntervalSince1970,cloud:self.cloudReaders.contains(reader.id) ? MobileProtocol.cloudOrigin : nil,supportsAck:true,capabilities:self.capabilities,detailVersions:self.details.mapValues(\.revision)),over:connection)
            } else if message.action == "pair", let reader = message.reader,
                      reader.id.count <= 64, reader.name.count <= 40, reader.localSecret.count == 43, reader.cloudSecret.count == 43,
                      let ticket = self.ticket, ticket.expires > Date().timeIntervalSince1970,
                      MobileProtocol.equal(message.ticket ?? "",ticket.ticket), self.candidate == nil || self.candidate?.id == reader.id {
                self.candidate = reader
                let revision = self.ticketUIRevision
                DispatchQueue.main.async { guard self.pairingUIRevision == revision else {return}; self.pending = reader }
                MobileProtocol.send(MobileMessage(action:"pending"),over:connection)
            } else { MobileProtocol.send(MobileMessage(action:message.action == "sync" ? "revoked" : "error",error:message.action == "sync" ? "REVOKED" : "配对已过期或被拒绝"),over:connection) }
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
    private func thumbnail(_ path: String) -> String? {
        // Decode only local regular images supplied by the ledger, with bounded
        // compressed input, decoded dimensions and final output. No URL fetches.
        guard path.hasPrefix("/"), let attributes = try? FileManager.default.attributesOfItem(atPath:path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              let size = attributes[.size] as? NSNumber, size.intValue <= 12 * 1_024 * 1_024,
              let source = CGImageSourceCreateWithURL(URL(fileURLWithPath:path) as CFURL,[kCGImageSourceShouldCache:false] as CFDictionary),
              let properties = CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],
              let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight] as? NSNumber,
              width.intValue > 0, height.intValue > 0, width.intValue <= 16_384, height.intValue <= 16_384,
              width.intValue * height.intValue <= 40_000_000,
              let image = CGImageSourceCreateThumbnailAtIndex(source,0,[kCGImageSourceCreateThumbnailFromImageAlways:true,kCGImageSourceThumbnailMaxPixelSize:240,kCGImageSourceCreateThumbnailWithTransform:true] as CFDictionary) else { return nil }
        let output = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(output,UTType.jpeg.identifier as CFString,1,nil) else { return nil }
        CGImageDestinationAddImage(destination,image,[kCGImageDestinationLossyCompressionQuality:0.55] as CFDictionary)
        guard CGImageDestinationFinalize(destination), output.length <= 12_288 else { return nil }
        return (output as Data).base64EncodedString()
    }
    private func loadDetail(_ requestID: String) throws -> MobileEnvelope? {
        guard let raw = try detailProvider?(requestID) else { return nil }
        let canonical = raw.string("id"), id = MobileProtocol.hash(Data(canonical.utf8))
        guard !canonical.isEmpty else { return nil }
        let started = raw.number("started_at") ?? 0, completed = raw.number("completed_at")
        guard started > 0, max(started,completed ?? started) >= Date().timeIntervalSince1970-MobileProtocol.detailRetention else { return nil }
        let sourceUser = raw.string("user"), sourceFinal = raw.string("final")
        var thumbnails = 0
        let attachments = (raw["attachments"] as? [Object] ?? []).prefix(6).map { attachment -> MobileAttachment in
            let name = MobileProtocol.prefix(attachment.string("name",URL(fileURLWithPath:attachment.string("path")).lastPathComponent),bytes:240)
            let mime = attachment.string("mime")
            let image = thumbnails < 2 && mime.hasPrefix("image/") ? thumbnail(attachment.string("path")) : nil
            if image != nil { thumbnails += 1 }
            return MobileAttachment(id:MobileProtocol.hash(Data((attachment.string("id")+name).utf8)),name:name.isEmpty ? "附件" : name,mime:mime.isEmpty ? nil : mime,thumbnail:image)
        }
        var value = MobileRequestDetail(id:id,started:started,completed:completed,status:raw.string("status"),user:sourceUser,final:sourceFinal,userComplete:raw.flag("user_complete"),finalComplete:raw.flag("final_complete"),availability:raw.string("availability","unavailable"),attachments:attachments,full:true)
        var data = try MobileProtocol.encode(value)
        if data.count > MobileProtocol.detailLimit {
            value.attachments = value.attachments.map { var item = $0; item.thumbnail = nil; return item }
            data = try MobileProtocol.encode(value)
        }
        if data.count > MobileProtocol.detailLimit {
            // Keep the originals in the ledger. An outlier is explicitly
            // unavailable for full phone transfer, never silently truncated.
            value = value.preview(); value.availability = "capacity"
            value.userComplete = false; value.finalComplete = false
            data = try MobileProtocol.encode(value)
        }
        if value.availability == "available", !value.userComplete || (!value.final.isEmpty && !value.finalComplete) { value.availability = "partial"; data = try MobileProtocol.encode(value) }
        let digest = MobileProtocol.hash(data), old = detailStamps[id]
        let revision = old?.digest == digest ? old!.revision : max(old?.revision ?? 0,Int64(Date().timeIntervalSince1970*1_000))+1
        let envelope = MobileEnvelope(dataset:"detail-"+id,revision:revision,digest:digest,payload:String(decoding:data,as:UTF8.self))
        details[id] = envelope
        detailSizes[id] = data.count; detailDates[id] = started
        while details.count > MobileProtocol.detailRows || detailSizes.values.reduce(0,+) > 8 * 1_024 * 1_024 {
            guard let evicted = detailDates.filter({$0.key != id}).min(by:{$0.value < $1.value})?.key else { break }
            details.removeValue(forKey:evicted); detailSizes.removeValue(forKey:evicted); detailDates.removeValue(forKey:evicted)
        }
        detailStamps[id] = MobileDetailStamp(revision:revision,digest:digest,sent:old?.sent)
        if old?.digest != digest { detailDeferred.removeValue(forKey:id) }
        if detailStamps.count > 256, let oldest = detailStamps.min(by:{$0.value.revision < $1.value.revision})?.key { detailStamps.removeValue(forKey:oldest) }
        return envelope
    }
    private func detailMessage(_ id: String, full: Bool, part: Int) throws -> MobileMessage {
        let wasCached = details[id] != nil
        guard let envelope = try details[id] ?? loadDetail(id), let value = try? envelope.decode(MobileRequestDetail.self), value.expires > Date().timeIntervalSince1970 else { return MobileMessage(action:"detail",error:"DETAIL_UNAVAILABLE",detailID:id,full:full) }
        if !wasCached { try saveDetailStamps() }
        if full {
            guard value.availability != "capacity" else { return MobileMessage(action:"detail",error:"DETAIL_CAPACITY",detailID:id,full:true) }
            let bytes = Data(envelope.payload.utf8)
            let count = (bytes.count+MobileProtocol.detailChunkBytes-1)/MobileProtocol.detailChunkBytes
            guard part >= 0, part < count else { return MobileMessage(action:"detail",error:"INVALID",detailID:id,full:true) }
            let start = part*MobileProtocol.detailChunkBytes, end = min(bytes.count,start+MobileProtocol.detailChunkBytes)
            return MobileMessage(action:"detail",detailID:id,full:true,detailPart:part,detailManifest:MobileDetailManifest(id:id,revision:envelope.revision,digest:envelope.digest,bytes:bytes.count,parts:count),detailChunk:bytes.subdata(in:start..<end).base64EncodedString())
        }
        let data = try MobileProtocol.encode(value.preview())
        return MobileMessage(action:"detail",detailID:id,full:false,detail:MobileEnvelope(dataset:envelope.dataset,revision:envelope.revision,digest:MobileProtocol.hash(data),payload:String(decoding:data,as:UTF8.self)))
    }
    private func updateDetails(_ rows: [UsageRow]) throws {
        guard detailProvider != nil else { return }
        let keep = Set(rows.prefix(MobileProtocol.detailRows).map {MobileProtocol.hash(Data($0.id.utf8))})
        var changed = false
        for row in rows.prefix(MobileProtocol.detailRows).reversed() {
            let id = MobileProtocol.hash(Data(row.id.utf8))
            // The ledger's content digest excludes scan/fetch timestamps. Legacy
            // rows use only business fields, and recovery is bounded per request.
            let source = row.raw.string("message_digest") + "/" + row.raw.string("status") + "/" + String(row.raw.number("message_revision") ?? 0) + "/" + row.raw.string("prompt_preview") + "/" + (row.tokens.map(String.init) ?? "")
            guard detailSources[id] != source else { continue }
            let old = details[id]
            _ = try loadDetail(row.id); detailSources[id] = source
            if old != details[id] { changed = true }
        }
        if details.keys.contains(where:{!keep.contains($0)}) || detailStamps.keys.contains(where:{!keep.contains($0)}) { changed = true }
        details = details.filter {keep.contains($0.key)}; detailSources = detailSources.filter {keep.contains($0.key)}; detailStamps = detailStamps.filter {keep.contains($0.key)}
        detailSizes = detailSizes.filter {details[$0.key] != nil}; detailDates = detailDates.filter {details[$0.key] != nil}
        if changed { try saveDetailStamps() }
    }
    func update(_ snapshot: UsageSnapshot,quota: MenuQuota,observed: Date? = nil) {
        guard !paths.mock, enabled else { return }
        let previews = true, name = deviceName
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
                let main = snapshot.requests.filter {$0.local && $0.raw.string("record_kind") == "user_request" && !$0.raw.flag("is_subagent") && ($0.date ?? .distantFuture) <= Date()}
                let running = main.filter {$0.raw.flag("duration_running") || $0.raw.string("status") == "running"}
                let completed = main.filter {$0.raw.string("status") == "completed" && !$0.raw.flag("duration_running")}.max { ($0.raw.number("completed_at") ?? $0.date?.timeIntervalSince1970 ?? 0) < ($1.raw.number("completed_at") ?? $1.date?.timeIntervalSince1970 ?? 0) }
                let current = running.first ?? completed
                let live = MobileLive(name:name,timeZone:TimeZone.current.identifier,observed:sample,task:current.map(request),runningCount:running.count,today:metric(snapshot.summaries["today"] ?? UsageSummary()),five:window(quota.five),week:window(quota.week))
                let old = self.datasets
                let oldDetails = self.details.mapValues(\.revision)
                try self.put("live",live)
                let dayKey = String(Calendar.current.startOfDay(for:Date()).timeIntervalSince1970)+TimeZone.current.identifier
                let taskKey = (live.task?.id ?? "")+"/"+String(live.runningCount)
                let urgent = self.exportPreview != previews || self.exportDay != dayKey || self.lastTaskKey != taskKey
                if urgent || (self.exportRevision != snapshot.revision && Date() >= self.nextHistory) {
                    let floor = Date().addingTimeInterval(-7*86400)
                    let recent = main.filter {($0.date ?? .distantPast) >= floor}
                    try self.put("recent",recent.prefix(200).map(request))
                    try self.updateDetails(recent)
                    let calendar = Calendar.current, today = calendar.startOfDay(for:Date())
                    let oldest = calendar.date(byAdding:.day,value:-89,to:today)!
                    let days = snapshot.allDays.filter {$0.date >= oldest && $0.date <= today}.map {MobileDay(id:String($0.date.timeIntervalSince1970),start:$0.date.timeIntervalSince1970,metric:MobileMetric(tokens:$0.tokens,cost:$0.cost,requests:$0.requests))}
                    let periods = [7,30,90].map { count -> MobilePeriod in
                        let start = calendar.date(byAdding:.day,value:1-count,to:today)!
                        let calls = snapshot.calls.filter {$0.local && ($0.date ?? .distantPast) >= start && ($0.date ?? .distantFuture) <= Date()}
                        let requests = main.filter {($0.date ?? .distantPast) >= start}
                        let grouped = Dictionary(grouping:calls,by:{$0.raw.string("model","unknown")})
                        let requestModels = Dictionary(grouping:requests,by:{$0.raw.string("model","unknown")})
                        let keys = Set(grouped.keys).union(requestModels.keys).filter {MobileTrends.isSingleModel($0) && MobileTrends.isSingleModel(modelName($0))}.sorted()
                        let models = keys.map {key in MobileModel(id:key,name:modelName(key),metric:metric(UsageSummary(rows:grouped[key] ?? [],requests:requestModels[key]?.count ?? 0)))}
                        return MobilePeriod(days:count,total:metric(UsageSummary(rows:calls,requests:requests.count)),models:models)
                    }
                    try self.put("trends",MobileTrends(daily:days,periods:periods))
                    self.exportRevision = snapshot.revision; self.exportPreview = previews; self.exportDay = dayKey
                    self.nextHistory = Date().addingTimeInterval(60)
                }
                if old != self.datasets || oldDetails != self.details.mapValues(\.revision) {
                    if old != self.datasets { try self.persist() }
                    for (key,connection) in self.connections where self.authenticated[key] != nil {
                        MobileProtocol.send(MobileMessage(action:"sync",known:self.datasets.mapValues(\.revision),datasets:self.datasets.values.filter {old[$0.dataset] != $0},seen:Date().timeIntervalSince1970,supportsAck:true,capabilities:self.capabilities,detailVersions:self.details.mapValues(\.revision)),over:connection)
                    }
                }
                if urgent || self.lastPrivacy != previews { self.lastPrivacy = previews; self.lastTaskKey = taskKey; self.urgentPending = true; self.upload() }
            } catch { self.report(error.localizedDescription) }
        }
    }
    private func upload() {
        if host?.pendingKeyRemoval == true { finishKeyRemoval(); return }
        guard active, let host, host.cloudEnabled, !sending, Date() >= retryAt else { return }
        let changed = datasets.values.filter {sent[$0.dataset] != $0.revision}
        let readers = host.readers.filter {!cloudReaders.contains($0.id)}
        let detailChanges = details.values.filter { detailStamps[String($0.dataset.dropFirst(7))]?.sent != $0.digest && Date() >= (detailDeferred[String($0.dataset.dropFirst(7))] ?? .distantPast) }.sorted {$0.revision > $1.revision}
        let probe = detailProvider != nil && Date() >= nextCapabilityCheck
        let support = cloudDetails
        guard !changed.isEmpty || !readers.isEmpty || !host.revoked.isEmpty || probe || (support == true && !detailChanges.isEmpty) else { return }
        sending = true; urgentPending = false; let generation = generation, transport = transport
        Task {
            do {
                for id in host.revoked { _ = try await transport.request("/v1/hosts/\(host.id)/readers/\(id)",method:"DELETE",token:host.writer) }
                for reader in readers {
                    let body = try JSONSerialization.data(withJSONObject:["id":reader.id,"secret":reader.cloudSecret])
                    _ = try await transport.request("/v1/hosts/\(host.id)/readers",method:"PUT",token:host.writer,body:body)
                }
                for value in changed { _ = try await transport.request("/v1/hosts/\(host.id)/data/\(value.dataset)",method:"PUT",token:host.writer,body:MobileProtocol.encode(value)) }
                // Detail negotiation and uploads cannot turn a successful legacy
                // summary sync into failure. Old Workers return no capability.
                var supported = support, uploaded: [MobileEnvelope] = [], capacityIDs: [String] = []
                var detailFailure: String?
                if probe {
                    do {
                        let response = try await transport.request("/v1/hosts/\(host.id)/capabilities",token:host.writer)
                        let message = try JSONDecoder().decode(MobileMessage.self,from:response)
                        supported = message.capabilities?.contains(MobileProtocol.detailCapability) == true
                    } catch { supported = false }
                }
                if supported == true {
                    for value in detailChanges.prefix(4) {
                        do {
                            let id = String(value.dataset.dropFirst(7))
                            _ = try await transport.request("/v1/hosts/\(host.id)/details/\(id)",method:"PUT",token:host.writer,body:MobileProtocol.encode(value))
                            uploaded.append(value)
                        } catch {
                            if case MobileError.http(_,_,let code) = error, code == "DETAIL_CAPACITY" { capacityIDs.append(String(value.dataset.dropFirst(7))); continue }
                            detailFailure = error.localizedDescription; break
                        }
                    }
                }
                let resolvedSupport = supported, sentDetails = uploaded, failure = detailFailure, deferred = capacityIDs
                self.queue.async {
                    guard self.generation == generation else { return }
                    self.cloudDetails = resolvedSupport
                    if probe { self.nextCapabilityCheck = Date().addingTimeInterval(3_600) }
                    for value in changed { self.sent[value.dataset] = value.revision }
                    for value in sentDetails { self.detailStamps[String(value.dataset.dropFirst(7))]?.sent = value.digest }
                    for id in deferred { self.detailDeferred[id] = Date().addingTimeInterval(3_600) }
                    if !sentDetails.isEmpty { try? self.saveDetailStamps() }
                    readers.forEach {self.cloudReaders.insert($0.id)}
                    let granted = Set(readers.map(\.id))
                    for (key,connection) in self.connections where granted.contains(self.authenticated[key] ?? "") {
                        MobileProtocol.send(MobileMessage(action:"sync",known:self.datasets.mapValues(\.revision),datasets:[],seen:Date().timeIntervalSince1970,cloud:MobileProtocol.cloudOrigin,supportsAck:true,capabilities:self.capabilities,detailVersions:self.details.mapValues(\.revision)),over:connection)
                    }
                    self.host?.revoked.removeAll {host.revoked.contains($0)}
                    self.synchronized("cloud")
                    try? self.saveHost(); self.sending = false; self.failures = 0
                    let pending = self.datasets.values.contains {self.sent[$0.dataset] != $0.revision}
                    if let failure { self.report("摘要已同步；详情待重试："+failure); self.retryAt = Date().addingTimeInterval(60) }
                    else if !deferred.isEmpty { self.report("局域网优先 · 部分详情达到云端容量，仍可在局域网读取") }
                    else { self.report(pending ? "局域网优先 · 云端有更新待同步" : "局域网优先 · 云端已同步") }
                    if self.urgentPending || self.host?.pendingKeyRemoval == true { self.upload() }
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
    @State private var confirmRemoval = false
    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            HStack {
                Toggle("同步",isOn:Binding(get:{sync.enabled},set:{$0 ? sync.start() : sync.stop(disable:true)}))
                Spacer(); SyncStampView(stamp:sync.lastSync)
            }
            if sync.enabled {
                TextField("设备名称",text:$name).onAppear { name = sync.deviceName }.onSubmit { sync.rename(name); refresh() }
                Text("首次配对请让 iPhone 与 Mac 处于可互通的局域网。").font(.caption).foregroundStyle(.secondary)
                if sync.removingKey { HStack {ProgressView().controlSize(.small); Text("正在移除云端密钥")} }
                else if !sync.cloudEnabled {
                    SecureField("云端密钥／邀请码",text:$invite)
                    Button("启用云同步") { sync.enroll(invite); invite = "" }.disabled(invite.isEmpty)
                } else {
                    HStack {Label("云同步",systemImage:"cloud"); Spacer(); Button("移除密钥",role:.destructive) {confirmRemoval = true}}
                }
                Button("生成二维码") { sync.pair(); refresh() }.disabled(sync.readers.count >= 3)
                if let image = sync.qrImage { Image(nsImage:image).interpolation(.none).resizable().scaledToFit().frame(width:220,height:220) }
                if let pending = sync.pending {
                    Text("允许 \(pending.name) 读取这台 Mac 的用量与请求内容？")
                    HStack { Button("拒绝") { sync.approve(false) }; Button("确认配对") { sync.approve(true) } }
                }
                ForEach(sync.readers) { reader in SyncReaderRow(sync:sync,reader:reader) }
                if let error = sync.syncError {Text(error).foregroundStyle(.red)}
            }
        }
        .onDisappear {sync.invalidatePairing()}
        .confirmationDialog("移除云端密钥？",isPresented:$confirmRemoval,titleVisibility:.visible) {Button("移除密钥",role:.destructive) {sync.removeCloudKey()}} message: {Text("云同步将停止，再次使用需添加新的邀请码。局域网配对保留。")}
        .alert("同步",isPresented:Binding(get:{sync.notice != nil},set:{if !$0 {sync.notice=nil}})) {Button("知道了") {sync.notice=nil}} message: {Text(sync.notice ?? "")}
    }
}
private struct SyncStampView: View {
    let stamp: SyncStamp?
    var body: some View {
        HStack(spacing:6) {
            if let stamp {Image(systemName:stamp.route == "lan" ? "wifi" : "cloud").help(stamp.route == "lan" ? "iPhone 已确认接收" : "Mac 已上传到云端"); Text(lastUpdateText(stamp.time).replacingOccurrences(of:"上次更新",with:"上次同步"))}
            else {Text("尚未同步")}
        }.font(.caption).foregroundStyle(.secondary)
    }
}
private struct SyncReaderRow: View {
    @ObservedObject var sync: MobileSync
    let reader: MobileReader
    @State private var note = ""
    private var stamp: SyncStamp? {
        let local = sync.localSyncs[reader.id], cloud = sync.cloudSync
        return [local,cloud].compactMap {$0}.max {$0.time < $1.time}
    }
    var body: some View {
        VStack(alignment:.leading,spacing:8) {
            HStack {Text(reader.name).fontWeight(.medium); Spacer(); Button("撤销",role:.destructive) {sync.revoke(reader.id)}}
            HStack {TextField("备注",text:$note).onSubmit {sync.setNote(reader.id,note)}; Spacer(); SyncStampView(stamp:stamp)}
        }.padding(12).background(Color.primary.opacity(0.035),in:RoundedRectangle(cornerRadius:10))
            .onAppear {note=sync.notes[reader.id] ?? ""}.onDisappear {sync.setNote(reader.id,note)}
    }
}
