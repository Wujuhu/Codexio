import Foundation
import CryptoKit
import Security
import Network

enum MobileProtocol {
    static let version = 1
    static let service = "_codexio._tcp"
    static let cloudOrigin = "https://codexio-sync.503948883.workers.dev"
    static let limit = 262_144
    static let detailCapability = "request-details-v1"
    static let detailLimit = 1_048_576
    static let detailChunkBytes = 65_536
    static let detailRows = 64
    static let detailRetention: Double = 7 * 86_400
    static func prefix(_ value: String, bytes: Int) -> String {
        guard value.utf8.count > bytes else { return value }
        var result = String(decoding:value.utf8.prefix(max(0,bytes)),as:UTF8.self)
        // A UTF-8 byte boundary can cut a scalar. Remove replacement suffixes.
        while result.last == "\u{FFFD}" { result.removeLast() }
        return result
    }
    static func encode<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(value)
    }
    static func hash(_ data: Data) -> String { SHA256.hash(data:data).map {String(format:"%02x",$0)}.joined() }
    static func secret() throws -> String {
        var bytes = [UInt8](repeating:0,count:32)
        guard SecRandomCopyBytes(kSecRandomDefault,bytes.count,&bytes) == errSecSuccess else { throw MobileError.message("无法创建安全凭据") }
        return Data(bytes).base64EncodedString().replacingOccurrences(of:"+",with:"-").replacingOccurrences(of:"/",with:"_").replacingOccurrences(of:"=",with:"")
    }
    static func equal(_ a: String,_ b: String) -> Bool {
        let x = Array(a.utf8), y = Array(b.utf8)
        guard x.count == y.count else { return false }
        return zip(x,y).reduce(UInt8(0)) { $0 | ($1.0 ^ $1.1) } == 0
    }
    static func parameters(identity: SecIdentity? = nil, pin: String? = nil, queue: DispatchQueue) -> NWParameters {
        let tls = NWProtocolTLS.Options()
        sec_protocol_options_set_min_tls_protocol_version(tls.securityProtocolOptions,.TLSv12)
        if let identity, let value = sec_identity_create(identity) {
            sec_protocol_options_set_local_identity(tls.securityProtocolOptions,value)
            sec_protocol_options_set_peer_authentication_required(tls.securityProtocolOptions,false)
        }
        if let pin {
            sec_protocol_options_set_verify_block(tls.securityProtocolOptions,{ _, trust, done in
                let value = sec_trust_copy_ref(trust).takeRetainedValue()
                guard let chain = SecTrustCopyCertificateChain(value) as? [SecCertificate], let cert = chain.first,
                      equal(hash(SecCertificateCopyData(cert) as Data),pin) else { done(false); return }
                SecTrustSetAnchorCertificates(value,[cert] as CFArray)
                SecTrustSetAnchorCertificatesOnly(value,true)
                SecTrustSetPolicies(value,SecPolicyCreateBasicX509())
                done(SecTrustEvaluateWithError(value,nil))
            },queue)
        }
        let parameters = NWParameters(tls:tls,tcp:NWProtocolTCP.Options())
        let ws = NWProtocolWebSocket.Options(); ws.autoReplyPing = true; ws.maximumMessageSize = limit
        // Bonjour endpoints have no HTTP URL. TLS authenticates the host, then
        // Network.framework supplies message framing without an HTTP Upgrade.
        ws.skipHandshake = true
        parameters.defaultProtocolStack.applicationProtocols.insert(ws,at:0)
        parameters.includePeerToPeer = false
        return parameters
    }
    static func send(_ message: MobileMessage, over connection: NWConnection, completion: @escaping (Error?) -> Void = {_ in}) {
        do {
            let bytes = try encode(message)
            guard bytes.count <= limit else { throw MobileError.message("同步数据超过限制") }
            let metadata = NWProtocolWebSocket.Metadata(opcode:.text)
            connection.send(content:bytes,contentContext:.init(identifier:"codexio",metadata:[metadata]),isComplete:true,completion:.contentProcessed {completion($0)})
        } catch { completion(error) }
    }
}

enum MobileError: LocalizedError {
    case message(String)
    case http(Int, Double?, String?)
    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        case .http(let code,_,_): return code == 401 || code == 403 ? "云端授权不可用，请在 Mac 检查配对" : "云端请求失败（\(code)），保留上次数据"
        }
    }
}

enum MobileKeychain {
    static func read(_ key: String) throws -> Data? {
        let query: [String:Any] = [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:"com.wujuhu.codexio.mobile",kSecAttrAccount as String:key,kSecReturnData as String:true,kSecMatchLimit as String:kSecMatchLimitOne]
        var result: CFTypeRef?; let status = SecItemCopyMatching(query as CFDictionary,&result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw MobileError.message("钥匙串不可用（\(status)）") }
        return result as? Data
    }
    static func save(_ data: Data, key: String) throws {
        let query: [String:Any] = [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:"com.wujuhu.codexio.mobile",kSecAttrAccount as String:key]
        let fields: [String:Any] = [kSecValueData as String:data,kSecAttrAccessible as String:kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var status = SecItemUpdate(query as CFDictionary,fields as CFDictionary)
        if status == errSecItemNotFound { status = SecItemAdd(query.merging(fields) {$1} as CFDictionary,nil) }
        guard status == errSecSuccess else { throw MobileError.message("无法保存设备凭据（\(status)）") }
    }
}

struct MobileMetric: Codable, Equatable {
    var tokens: Int? = nil
    var cost: Double? = nil
    var requests: Int = 0
    var costComplete: Bool = true
    var hitRate: Double? = nil
}
struct MobileRequest: Codable, Identifiable, Equatable {
    var id: String
    var started: Double
    var status: String
    var preview: String?
    var model: String
    var effort: String?
    var speed: String?
    var tokens: Int?
    var cost: Double?
    var duration: Double?
}
struct MobileQuota: Codable, Equatable {
    var remaining: Double?
    var reset: Double?
    var observed: Double?
    var retained: Bool
}
struct MobileLive: Codable, Equatable {
    var name: String
    var timeZone: String
    var observed: Double?
    var task: MobileRequest?
    var runningCount: Int
    var today: MobileMetric
    var five: MobileQuota
    var week: MobileQuota
}
struct MobileDay: Codable, Identifiable, Equatable {
    var id: String
    var start: Double
    var metric: MobileMetric
}
struct MobileModel: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var metric: MobileMetric
}
struct MobilePeriod: Codable, Equatable {
    var days: Int
    var total: MobileMetric
    var models: [MobileModel]
}
struct MobileTrends: Codable, Equatable {
    var daily: [MobileDay]
    var periods: [MobilePeriod]
    static func isSingleModel(_ value: String) -> Bool {
        let name = value.trimmingCharacters(in:.whitespacesAndNewlines)
        return !["", "unknown", "mixed", "multiple", "—", "未知", "多模型", "多个模型"].contains(name.lowercased())
            && !["+", "→", ",", "，", "\n"].contains(where:name.contains)
    }
    func singleModelsOnly() -> Self {
        var copy = self
        copy.periods = periods.map { period in
            var result = period
            result.models.removeAll {!Self.isSingleModel($0.id) || !Self.isSingleModel($0.name)}
            return result
        }
        return copy
    }
}
struct MobileEnvelope: Codable, Equatable {
    var dataset: String
    var revision: Int64
    var digest: String
    var payload: String
    func decode<T: Decodable>(_ type: T.Type) throws -> T { try JSONDecoder().decode(type,from:Data(payload.utf8)) }
    func valid(limit: Int = MobileProtocol.limit) -> Bool { revision > 0 && payload.utf8.count <= limit && MobileProtocol.hash(Data(payload.utf8)) == digest }
}
struct MobilePairCode: Codable {
    var version: Int = 1
    var host: String
    var name: String
    var pin: String
    var ticket: String
    var expires: Double
    var cloud: String?
}
struct MobileReader: Codable, Identifiable {
    var id: String
    var name: String
    var localSecret: String
    var cloudSecret: String
}
struct MobileMessage: Codable {
    var action: String
    var ticket: String? = nil
    var reader: MobileReader? = nil
    var known: [String:Int64]? = nil
    var datasets: [MobileEnvelope]? = nil
    var error: String? = nil
    var seen: Double? = nil
    var cloud: String? = nil
    var supportsAck: Bool? = nil
    var capabilities: [String]? = nil
    var detailVersions: [String:Int64]? = nil
    var detailID: String? = nil
    var full: Bool? = nil
    var detail: MobileEnvelope? = nil
    var detailPart: Int? = nil
    var detailManifest: MobileDetailManifest? = nil
    var detailChunk: String? = nil
}

struct MobileDetailManifest: Codable, Equatable {
    var id: String
    var revision: Int64
    var digest: String
    var bytes: Int
    var parts: Int
    func valid(for value: String) -> Bool {
        id == value && revision > 0 && digest.count == 64 && bytes > 0 && bytes <= MobileProtocol.detailLimit
            && parts == (bytes + MobileProtocol.detailChunkBytes - 1) / MobileProtocol.detailChunkBytes
    }
}

// These records are separate from live/recent/trends so older Workers retain
// their original allow-list. No paths, tool messages, or execution controls.
struct MobileAttachment: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var mime: String?
    var thumbnail: String? // Bounded JPEG data; never a local or remote URL.
}
struct MobileRequestDetail: Codable, Equatable {
    var id: String
    var started: Double
    var completed: Double?
    var status: String
    var user: String
    var final: String
    var userComplete: Bool
    var finalComplete: Bool
    var availability: String
    var attachments: [MobileAttachment]
    var full: Bool
    var expires: Double { max(started,completed ?? started) + MobileProtocol.detailRetention }
    func preview() -> Self {
        var value = self
        value.user = MobileProtocol.prefix(user,bytes:1_200)
        value.final = MobileProtocol.prefix(final,bytes:5_000)
        value.attachments = attachments.map { var item = $0; item.thumbnail = nil; return item }
        value.full = false
        return value
    }
    func valid(for requestID: String) -> Bool {
        id == requestID && id.count == 64 && started.isFinite && started > 0
            && (completed?.isFinite ?? true) && user.utf8.count <= MobileProtocol.detailLimit
            && final.utf8.count <= MobileProtocol.detailLimit && attachments.count <= 6
            && attachments.allSatisfy { $0.name.utf8.count <= 240 && ($0.thumbnail?.utf8.count ?? 0) <= 16_384 }
    }
}

final class MobileHTTP: NSObject, URLSessionTaskDelegate {
    private lazy var session = URLSession(configuration:.ephemeral,delegate:self,delegateQueue:nil)
    func urlSession(_ session: URLSession,task: URLSessionTask,willPerformHTTPRedirection response: HTTPURLResponse,newRequest request: URLRequest,completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func request(_ path: String, method: String = "GET", token: String, body: Data? = nil) async throws -> Data {
        guard let url = URL(string:MobileProtocol.cloudOrigin+path), url.scheme == "https" else { throw MobileError.message("无效云端地址") }
        var request = URLRequest(url:url); request.httpMethod = method; request.timeoutInterval = 15
        request.setValue("Bearer "+token,forHTTPHeaderField:"Authorization")
        request.setValue("application/json",forHTTPHeaderField:"Content-Type"); request.httpBody = body
        let (data,response) = try await session.data(for:request)
        guard let response = response as? HTTPURLResponse, data.count <= MobileProtocol.limit else { throw MobileError.message("无效同步响应") }
        guard (200..<300).contains(response.statusCode) else {
            let code = (try? JSONSerialization.jsonObject(with:data) as? [String:Any])?["error"] as? String
            throw MobileError.http(response.statusCode,response.value(forHTTPHeaderField:"Retry-After").flatMap(Double.init),code)
        }
        return data
    }
    func stop() { session.invalidateAndCancel() }
}
