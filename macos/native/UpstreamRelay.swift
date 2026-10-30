import Foundation
import Network
import AppKit

// The transport queue never opens SQLite, decodes response JSON or waits for a
// database lock. This shared writer bounds both queued work and transient bytes.
private final class ResponseObservationWriter: @unchecked Sendable {
    private struct Work { let bytes: Int; let body: () -> Void }
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.relay.observations",qos:.utility)
    private let lock = NSLock()
    private var pending: [Work] = []
    private var pendingBytes = 0
    private var retainedItems = 0
    private var running = false
    private var stopped = false
    private var database: Database?
    init(_ url: URL) throws {
        try queue.sync {
            let database = try Database(url)
            try database.script("CREATE TABLE IF NOT EXISTS observations(response_id TEXT PRIMARY KEY,model TEXT NOT NULL,event TEXT NOT NULL,rank INTEGER NOT NULL,observed_at REAL NOT NULL); CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY,value INTEGER NOT NULL); INSERT OR IGNORE INTO revision VALUES(1,0);")
            self.database = database
        }
    }
    func submit(bytes: Int = 0, _ body: @escaping () -> Void) -> Bool {
        lock.lock()
        guard !stopped, retainedItems < 64, bytes <= 8_388_608-pendingBytes else { lock.unlock(); return false }
        pending.append(Work(bytes:bytes,body:body)); pendingBytes += bytes; retainedItems += 1
        let schedule = !running; running = true; lock.unlock()
        if schedule { queue.async { [self] in drain() } }
        return true
    }
    private func drain() {
        while true {
            lock.lock()
            guard !stopped, !pending.isEmpty else { running = false; lock.unlock(); return }
            let work = pending.removeFirst(); lock.unlock()
            work.body()
            lock.lock(); pendingBytes -= work.bytes; retainedItems -= 1; lock.unlock()
        }
    }
    func record(id: String, model: String, event: String, rank: Int) {
        lock.lock(); let active = !stopped; lock.unlock()
        guard active, let database else { return }
        try? database.transaction {
            let previous = try database.query("SELECT model,rank FROM observations WHERE response_id=?",[id]).first
            if let previous {
                let oldRank = previous.integer("rank") ?? 0
                guard rank >= oldRank, previous.string("model") != model || rank != oldRank else { return }
            }
            try database.run("INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(response_id) DO UPDATE SET model=excluded.model,event=excluded.event,rank=excluded.rank,observed_at=excluded.observed_at",[id,model,event,rank,Date().timeIntervalSince1970])
            try database.run("UPDATE revision SET value=value+1 WHERE id=1")
        }
    }
    func stop() {
        lock.lock(); stopped = true; retainedItems -= pending.count; pendingBytes -= pending.reduce(0) { $0+$1.bytes }; pending.removeAll(); lock.unlock()
        queue.async { [self] in database = nil }
    }
}

private final class ResponseModelObserver {
    static let limit = 2_097_152
    private let writer: ResponseObservationWriter
    // Submission is exclusively on the relay queue; parsing is exclusively on
    // the writer queue. Once bytes are dropped, stop observing that connection.
    private var accepting = true
    private var isSSE: Bool?
    private var enabled = true
    private var buffer = Data()
    private var line = Data()
    private var event = Data()
    private var droppingLine = false
    private var droppingEvent = false
    private var websocket: WebSocketModelObserver?
    init(writer: ResponseObservationWriter) { self.writer = writer }
    func configure(type: String, encoded: Bool, websocket: Bool = false) {
        submit { [self] in
            self.enabled = !encoded
            if websocket { self.websocket = WebSocketModelObserver { [weak self] in self?.observe($0) } }
            else if type.lowercased().contains("text/event-stream") { self.isSSE = true }
            else if type.lowercased().contains("application/json") { self.isSSE = false }
        }
    }
    func feed(_ data: Data) {
        guard !data.isEmpty else { return }
        submit(bytes:data.count) {
            guard self.enabled else { return }
            if let websocket = self.websocket { websocket.feed(data) } else { self.parse(data) }
        }
    }
    func finish() {
        submit {
            guard self.enabled, self.websocket == nil else { return }
            if self.isSSE == false { if !self.droppingEvent { self.observe(self.buffer) } }
            else if self.isSSE == true {
                if !self.line.isEmpty || self.droppingLine { self.finishLine() }
                self.finishEvent()
            }
            self.buffer.removeAll(); self.line.removeAll(); self.event.removeAll()
        }
    }
    private func submit(bytes: Int = 0, _ body: @escaping () -> Void) {
        guard accepting else { return }
        if !writer.submit(bytes:bytes,body) { accepting = false }
    }
    private func parse(_ data: Data) {
        var input = data
        if isSSE == nil {
            // Whitespace before a JSON response or an SSE event carries no
            // observation. Never accumulate an unbounded unknown prefix.
            if let first = input.firstIndex(where:{ ![9,10,13,32].contains($0) }) {
                input = Data(input[first...]); isSSE = input.first != 123 && input.first != 91
            } else { return }
        }
        if isSSE == false {
            guard !droppingEvent else { return }
            if input.count <= Self.limit-buffer.count { buffer.append(input) }
            else { droppingEvent = true; buffer.removeAll() }
            return
        }
        var start = input.startIndex
        for index in input.indices where input[index] == 10 {
            appendLine(Data(input[start..<index])); finishLine(); start = input.index(after:index)
        }
        if start < input.endIndex { appendLine(Data(input[start...])) }
    }
    private func appendLine(_ data: Data) {
        guard !droppingLine else { return }
        if data.count <= Self.limit-line.count { line.append(data) }
        else { line.removeAll(); droppingLine = true; droppingEvent = true; event.removeAll() }
    }
    private func finishLine() {
        defer { line.removeAll(keepingCapacity:true); droppingLine = false }
        guard !droppingLine else { return }
        if line.last == 13 { line.removeLast() }
        if line.isEmpty { finishEvent(); return }
        guard !droppingEvent, line.starts(with:Data("data:".utf8)) else { return }
        var value = Data(line.dropFirst(5)); if value.first == 32 { value.removeFirst() }
        let separator = event.isEmpty ? 0 : 1
        if value.count+separator <= Self.limit-event.count {
            if separator == 1 { event.append(10) }; event.append(value)
        } else { droppingEvent = true; event.removeAll() }
    }
    private func finishEvent() {
        if !droppingEvent, !event.isEmpty { observe(event) }
        event.removeAll(keepingCapacity:true); droppingEvent = false
    }
    private func observe(_ data: Data) {
        guard !data.isEmpty, data.count <= Self.limit else { return }
        let value = jsonObject(data)
        var kind = value.string("type")
        let nested = value.object("response")
        let response = nested.isEmpty ? value : nested
        if kind.isEmpty || kind == "response" {
            // Ordinary Responses JSON has no SSE type, but has the same id/model.
            guard !nested.isEmpty || value.string("object") == "response" || kind == "response", !response.string("id").isEmpty, !response.string("model").isEmpty else { return }
            switch response.string("status") {
            case "in_progress","queued": kind = "response.created"
            case "failed": kind = "response.failed"
            case "incomplete": kind = "response.incomplete"
            default: kind = "response.completed"
            }
        }
        guard ["response.created","response.completed","response.incomplete","response.failed"].contains(kind) else { return }
        let id = response.string("id"), model = response.string("model")
        guard !id.isEmpty, id.utf8.count <= 256, !model.isEmpty, model.utf8.count <= 256 else { return }
        writer.record(id:id,model:model,event:kind,rank:kind == "response.created" ? 1 : 2)
    }
}

// A passive frame parser: all bytes are forwarded by the transport unchanged.
// Control frames can interrupt fragmented text; malformed/compressed/oversized
// messages affect observation only, and their payload is skipped without storage.
private final class WebSocketModelObserver {
    private let observe: (Data) -> Void
    private var header = Data()
    private var remaining: UInt64 = 0
    private var opcode: UInt8 = 0
    private var final = false
    private var active = false
    private var valid = false
    private var collecting = false
    private var disabled = false
    private var message = Data()
    init(observe: @escaping (Data) -> Void) { self.observe = observe }
    func feed(_ data: Data) {
        guard !disabled else { return }
        var cursor = 0
        while cursor < data.count {
            if remaining == 0 {
                while header.count < 2 && cursor < data.count { header.append(data[cursor]); cursor += 1 }
                guard header.count >= 2 else { return }
                let short = Int(header[1]&127), masked = header[1]&128 != 0
                let size = 2+(short == 126 ? 2 : short == 127 ? 8 : 0)+(masked ? 4 : 0)
                while header.count < size && cursor < data.count { header.append(data[cursor]); cursor += 1 }
                guard header.count == size else { return }
                var length = UInt64(short)
                if short >= 126 {
                    length = 0
                    let end = short == 126 ? 4 : 10
                    for index in 2..<end { length = (length<<8)|UInt64(header[index]) }
                    if short == 127 && header[2]&128 != 0 { disabled = true; message.removeAll(); return }
                }
                final = header[0]&128 != 0; opcode = header[0]&15
                let ordinary = header[0]&112 == 0 && !masked
                collecting = opcode == 0 || opcode == 1
                if opcode == 1 {
                    active = true; valid = ordinary; message.removeAll(keepingCapacity:true)
                } else if opcode == 0 {
                    valid = valid && active && ordinary
                } else if opcode == 2 || ![8,9,10].contains(opcode) {
                    active = false; valid = false; message.removeAll()
                } else if !ordinary || !final || length > 125 {
                    valid = false; message.removeAll()
                }
                if collecting, length > UInt64(ResponseModelObserver.limit-message.count) { valid = false; message.removeAll() }
                remaining = length; header.removeAll(keepingCapacity:true)
                if remaining == 0 { finishFrame(); continue }
            }
            let count = Int(min(remaining,UInt64(data.count-cursor)))
            if collecting && active && valid { message.append(data.subdata(in:cursor..<cursor+count)) }
            cursor += count; remaining -= UInt64(count)
            if remaining == 0 { finishFrame() }
        }
    }
    private func finishFrame() {
        if collecting && final {
            if active && valid { observe(message) }
            active = false; valid = false; message.removeAll(keepingCapacity:true)
        }
    }
}

final class UpstreamRelay {
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.relay")
    private let queueKey = DispatchSpecificKey<Bool>()
    private var listener: NWListener?
    private var connections: [UUID:RelayConnection] = [:]
    private var lease: FileLease?
    private let directory: URL
    private let origin: URL
    private let token: String
    private let localPath: String
    private let port: NWEndpoint.Port
    private let writer: ResponseObservationWriter
    init(directory: URL) throws {
        self.directory = directory
        let job = readObject(directory.appendingPathComponent("job.json"))
        guard let origin = URL(string:job.string("origin")), let components = URLComponents(url:origin,resolvingAgainstBaseURL:false), ["http","https"].contains(components.scheme ?? ""), components.host != nil, components.user == nil, components.password == nil, components.query == nil, components.fragment == nil, job.string("route_token").range(of:#"^[0-9a-f]{32}$"#,options:.regularExpression) != nil else { throw AppFailure("Invalid relay configuration") }
        self.origin = origin; token = job.string("route_token")
        localPath = job.string("local_path").isEmpty ? "/v1" : job.string("local_path")
        guard ["/v1","/backend-api/codex"].contains(localPath) else { throw AppFailure("Invalid relay path") }
        if job["port"] != nil {
            guard let raw = job.integer("port"), let number = job["port"] as? NSNumber, String(cString:number.objCType) != "c", number.doubleValue == Double(raw), raw > 0, raw <= 65535, let port = NWEndpoint.Port(rawValue:UInt16(raw)) else { throw AppFailure("Invalid relay port") }
            self.port = port
        } else { port = .any }
        let expected = directory.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("upstream.sqlite")
        guard URL(fileURLWithPath:job.string("database")).standardizedFileURL == expected.standardizedFileURL else { throw AppFailure("Invalid relay database path") }
        writer = try ResponseObservationWriter(expected)
        queue.setSpecific(key:queueKey,value:true)
    }
    func start() throws {
        guard let lease = FileLease(directory.appendingPathComponent("relay.lock")) else { throw AppFailure("The relay is already running") }; self.lease = lease
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host:"127.0.0.1",port:.any)
        let listener = try NWListener(using:parameters,on:port); self.listener = listener
        listener.stateUpdateHandler = { [weak self] status in
            guard let self else { return }
            if case .ready = status, let port = listener.port { try? atomicJSON(["pid":Int(getpid()),"port":Int(port.rawValue)],to:self.directory.appendingPathComponent("ready.json")) }
            if case .failed(let error) = status { try? atomicJSON(["error":error.localizedDescription],to:self.directory.appendingPathComponent("failed.json")) }
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            let id = UUID()
            let relay = RelayConnection(connection:connection,queue:self.queue,origin:self.origin,token:self.token,localPath:self.localPath,writer:self.writer) { [weak self] in self?.connections.removeValue(forKey:id) }
            self.connections[id] = relay; relay.start()
        }
        listener.start(queue:queue)
    }
    func stop() {
        let stop = {
            self.listener?.newConnectionHandler = nil; self.listener?.stateUpdateHandler = nil; self.listener?.cancel(); self.listener = nil
            for connection in Array(self.connections.values) { connection.cancel() }
            self.connections.removeAll(); self.lease = nil; self.writer.stop()
        }
        if DispatchQueue.getSpecific(key:queueKey) == true { stop() } else { queue.sync(execute:stop) }
    }
    deinit { writer.stop() }
}

private final class RelayConnection: NSObject, URLSessionDataDelegate, URLSessionStreamDelegate, @unchecked Sendable {
    private let connection: NWConnection
    private let queue: DispatchQueue
    private let origin: URL
    private let token: String
    private let localPath: String
    private let observer: ResponseModelObserver
    private let finished: () -> Void
    private var received = Data()
    private var initialWebSocketBytes = Data()
    private var headers: [String:String] = [:]
    private var requestLine: [String] = []
    private var headerEnd: Int?
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var stream: URLSessionStreamTask?
    private var ended = false
    private var responseStarted = false
    private var switching = false
    private var converted = false
    private var upgradeSent = false
    private var handshakeDelivered = false
    private var clientStreamReady = false
    private var clientReceivePending = false
    private var clientWritePending = false
    private var wantsWebSocket = false
    private var bodyless = false
    private var clientEOF = false
    private var serverEOF = false
    private var clientFinishedWriting = false
    private var serverFinishedSending = false
    private var timeout: DispatchWorkItem?
    init(connection: NWConnection,queue: DispatchQueue,origin: URL,token: String,localPath: String,writer: ResponseObservationWriter,finished: @escaping ()->Void) {
        self.connection = connection; self.queue = queue; self.origin = origin; self.token = token; self.localPath = localPath; self.observer = ResponseModelObserver(writer:writer); self.finished = finished
    }
    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            if case .failed = state { self?.finish() }
        }
        connection.start(queue:queue)
        let timeout = DispatchWorkItem { [weak self] in if self?.task == nil { self?.fail(408) } }; self.timeout = timeout
        queue.asyncAfter(deadline:.now()+60,execute:timeout); receiveRequest()
    }
    private func receiveRequest() {
        connection.receive(minimumIncompleteLength:1,maximumLength:65536) { [weak self] data,_,complete,error in
            guard let self, !self.ended else { return }
            if let data { self.received.append(data) }
            if self.received.count > 128_000_000+65536 { self.fail(413); return }
            self.clientEOF = complete
            do { if try self.parse() { if error != nil { self.finish() }; return } } catch { self.fail(400); return }
            if complete || error != nil { self.finish() } else { self.receiveRequest() }
        }
    }
    private static func tokens(_ value: String?) -> Set<String> {
        Set((value ?? "").split(separator:",").map { $0.trimmingCharacters(in:.whitespaces).lowercased() })
    }
    private func parse() throws -> Bool {
        if headerEnd == nil {
            guard let range = received.range(of:Data([13,10,13,10])) else { if received.count > 65536 { throw AppFailure("Header too large") }; return false }
            guard range.upperBound <= 65536, let text = String(data:received.subdata(in:0..<range.lowerBound),encoding:.utf8) else { throw AppFailure("Invalid headers") }
            headerEnd = range.upperBound
            let lines = text.components(separatedBy:"\r\n"); requestLine = (lines.first ?? "").components(separatedBy:" ")
            guard requestLine.count == 3, ["GET","POST","DELETE","OPTIONS","HEAD","PUT","PATCH"].contains(requestLine[0]), requestLine[2] == "HTTP/1.1" || requestLine[2] == "HTTP/1.0" else { throw AppFailure("Invalid request") }
            for line in lines.dropFirst() {
                guard let separator = line.firstIndex(of:":") else { throw AppFailure("Invalid header") }
                let key = String(line[..<separator]).lowercased()
                guard key.range(of:#"^[!#$%&'*+.^_`|~0-9a-z-]+$"#,options:.regularExpression) != nil else { throw AppFailure("Invalid header name") }
                let value = String(line[line.index(after:separator)...]).trimmingCharacters(in:.whitespaces)
                guard !value.unicodeScalars.contains(where:{ $0.value < 32 && $0.value != 9 || $0.value == 127 }) else { throw AppFailure("Invalid header value") }
                if let previous = headers[key] {
                    if key == "content-length" { guard value == previous else { throw AppFailure("Ambiguous length") } }
                    else { headers[key] = previous+(key == "cookie" ? "; " : ", ")+value }
                } else { headers[key] = value }
            }
            if headers["expect"]?.lowercased() == "100-continue" { connection.send(content:Data("HTTP/1.1 100 Continue\r\n\r\n".utf8),completion:.contentProcessed({_ in})) }
        }
        let start = headerEnd!, data = received.subdata(in:start..<received.count)
        let body: Data, consumed: Int
        if headers["transfer-encoding"] != nil {
            guard Self.tokens(headers["transfer-encoding"]) == ["chunked"], headers["content-length"] == nil else { throw AppFailure("Ambiguous framing") }
            guard let decoded = try decodeChunks(data) else { return false }; body = decoded.0; consumed = decoded.1
        } else {
            let length: Int
            if let raw = headers["content-length"] { guard raw.allSatisfy({ $0.isASCII && $0.isNumber }), let count = Int(raw), count <= 128_000_000 else { throw AppFailure("Invalid length") }; length = count } else { length = 0 }
            guard data.count >= length else { return false }; body = Data(data.prefix(length)); consumed = length
        }
        guard let target = URLComponents(string:requestLine[1]), target.scheme == nil, target.host == nil, target.fragment == nil, requestLine[1].hasPrefix("/"), !requestLine[1].hasPrefix("//") else { throw AppFailure("Invalid target") }
        let path = target.percentEncodedPath
        let prefixes = ["/"+token+localPath,"/"+token+"/v1"]
        guard let prefix = prefixes.first(where:{ path == $0 || path.hasPrefix($0+"/") }) else { fail(403); return true }
        let suffix = String(path.dropFirst(prefix.count))
        guard !(suffix.removingPercentEncoding ?? suffix).split(separator:"/").contains(".."), var destination = URLComponents(url:origin,resolvingAgainstBaseURL:false) else { throw AppFailure("Invalid route") }
        var base = destination.percentEncodedPath
        while base.hasSuffix("/") { base.removeLast() }
        destination.percentEncodedPath = base+suffix; destination.percentEncodedQuery = target.percentEncodedQuery
        guard let url = destination.url, url.host == origin.host, url.scheme == origin.scheme, url.port == origin.port else { throw AppFailure("Invalid route") }
        wantsWebSocket = Self.tokens(headers["connection"]).contains("upgrade") && Self.tokens(headers["upgrade"]).contains("websocket")
        if wantsWebSocket {
            guard requestLine[0] == "GET", body.isEmpty else { throw AppFailure("Invalid upgrade") }
            initialWebSocketBytes = Data(data.dropFirst(consumed))
        } else if data.count != consumed { throw AppFailure("HTTP pipelining is not supported") }
        var request = URLRequest(url:url,cachePolicy:.reloadIgnoringLocalCacheData)
        request.httpMethod = requestLine[0]; request.httpBody = body.isEmpty ? nil : body
        request.httpShouldHandleCookies = false; request.httpShouldUsePipelining = false
        var excluded = Self.tokens(headers["connection"]).union(["host","connection","keep-alive","content-length","transfer-encoding","expect","accept-encoding","upgrade","proxy-authorization","proxy-connection","te","trailer","x-codexio-route"])
        // Compression would make passive response observation ambiguous. The
        // websocket itself remains a byte tunnel, without re-framing messages.
        excluded.insert("sec-websocket-extensions")
        for (key,value) in headers where !excluded.contains(key) { request.setValue(value,forHTTPHeaderField:key) }
        request.setValue("identity",forHTTPHeaderField:"Accept-Encoding")
        if wantsWebSocket {
            request.setValue("Upgrade",forHTTPHeaderField:"Connection")
            request.setValue(headers["upgrade"],forHTTPHeaderField:"Upgrade")
        }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 600; config.timeoutIntervalForResource = 7200
        config.httpShouldUsePipelining = false; config.httpShouldSetCookies = false
        config.httpCookieAcceptPolicy = .never; config.httpCookieStorage = nil
        config.urlCredentialStorage = nil; config.urlCache = nil; config.requestCachePolicy = .reloadIgnoringLocalCacheData
        let delegates = OperationQueue(); delegates.maxConcurrentOperationCount = 1; delegates.underlyingQueue = queue
        let session = URLSession(configuration:config,delegate:self,delegateQueue:delegates); self.session = session
        task = session.dataTask(with:request)
        received.removeAll(); headers.removeAll(); timeout?.cancel(); task?.resume()
        if wantsWebSocket {
            if clientEOF { finish() } else { receiveWebSocketClient() }
        } else {
            if clientEOF { finish() } else { watchHTTPClient() }
        }
        return true
    }
    private func watchHTTPClient() {
        // Native HTTP deliberately treats a client FIN as cancellation rather
        // than supporting request half-close. One bounded receive notices a
        // cancelled request even before upstream headers or during an idle SSE.
        // Extra bytes are unsupported pipelining, never another forwarded body.
        connection.receive(minimumIncompleteLength:1,maximumLength:1) { [weak self] data,_,complete,error in
            guard let self, !self.ended else { return }
            if complete || error != nil || data?.isEmpty == false { self.finish() }
            else { self.watchHTTPClient() }
        }
    }
    private func decodeChunks(_ data: Data) throws -> (Data,Int)? {
        var cursor = 0, output = Data()
        while cursor < data.count {
            guard let line = data.range(of:Data([13,10]),in:cursor..<data.count) else { return nil }
            guard line.lowerBound-cursor <= 8192 else { throw AppFailure("Invalid chunk header") }
            let raw = String(decoding:data.subdata(in:cursor..<line.lowerBound),as:UTF8.self).split(separator:";",omittingEmptySubsequences:false).first ?? ""
            guard !raw.isEmpty, raw.allSatisfy({ $0.isASCII && $0.isHexDigit }), let count = Int(raw,radix:16), count <= 128_000_000-output.count else { throw AppFailure("Invalid chunk") }
            cursor = line.upperBound
            if count == 0 {
                let trailerStart = cursor
                while true {
                    guard let ending = data.range(of:Data([13,10]),in:cursor..<data.count) else { if data.count-trailerStart > 65536 { throw AppFailure("Trailer too large") }; return nil }
                    if ending.upperBound-trailerStart > 65536 { throw AppFailure("Trailer too large") }
                    if ending.lowerBound == cursor { return (output,ending.upperBound) }
                    cursor = ending.upperBound
                }
            }
            guard count <= data.count-cursor-2 else { return nil }
            guard data[cursor+count] == 13 && data[cursor+count+1] == 10 else { throw AppFailure("Invalid chunk ending") }
            output.append(data.subdata(in:cursor..<cursor+count)); cursor += count+2
        }
        return nil
    }
    func urlSession(_ session: URLSession,dataTask: URLSessionDataTask,didReceive response: URLResponse,completionHandler: @escaping (URLSession.ResponseDisposition)->Void) {
        guard !ended, !converted else { completionHandler(.cancel); return }
        guard let response = response as? HTTPURLResponse else { completionHandler(.cancel); fail(502); return }
        if response.statusCode == 101 {
            guard wantsWebSocket, Self.tokens(response.value(forHTTPHeaderField:"Upgrade")).contains("websocket"), Self.tokens(response.value(forHTTPHeaderField:"Connection")).contains("upgrade") else { completionHandler(.cancel); fail(502); return }
            switching = true; responseStarted = true
            observer.configure(type:"",encoded:false,websocket:true)
            let head = responseHead(response,upgrade:true,decoded:false)
            connection.send(content:head,completion:.contentProcessed { [weak self] error in
                guard let self, !self.ended else { return }
                if error != nil { self.finish(); return }
                self.handshakeDelivered = true; self.upgradeSent = true; self.startStreamIfReady()
            })
            completionHandler(.becomeStream); return
        }
        let encoding = Self.tokens(response.value(forHTTPHeaderField:"Content-Encoding")).subtracting(["identity"])
        let decodedEncodings: Set<String> = ["gzip","x-gzip","deflate","br"]
        let decoded = !encoding.isEmpty && encoding.isSubset(of:decodedEncodings)
        // Foundation decompresses its supported content codings before callbacks.
        // Never label those decoded bytes gzip; opaque unknown codings pass intact.
        if !encoding.isEmpty, !decoded, !encoding.isDisjoint(with:decodedEncodings) { completionHandler(.cancel); fail(502); return }
        bodyless = requestLine.first == "HEAD" || response.statusCode == 204 || response.statusCode == 304 || (100..<200).contains(response.statusCode)
        observer.configure(type:response.value(forHTTPHeaderField:"Content-Type") ?? "",encoded:!encoding.isEmpty)
        responseStarted = true; wantsWebSocket = false; initialWebSocketBytes.removeAll()
        connection.send(content:responseHead(response,upgrade:false,decoded:decoded),completion:.contentProcessed { [weak self] error in if error != nil { self?.finish() } })
        completionHandler(.allow)
    }
    private func responseHead(_ response: HTTPURLResponse, upgrade: Bool, decoded: Bool) -> Data {
        var excluded = Self.tokens(response.value(forHTTPHeaderField:"Connection")).union(["connection","content-length","transfer-encoding","keep-alive","proxy-connection","proxy-authenticate","proxy-authorization","te","trailer"])
        if upgrade { excluded.remove("upgrade") }
        else { excluded.insert("upgrade") }
        if decoded { excluded.insert("content-encoding") }
        var head = "HTTP/1.1 \(response.statusCode) \(HTTPURLResponse.localizedString(forStatusCode:response.statusCode))\r\n"
        head += upgrade ? "Connection: Upgrade\r\n" : "Connection: close\r\n"
        if !upgrade && !bodyless { head += "Transfer-Encoding: chunked\r\n" }
        if bodyless, let length = response.value(forHTTPHeaderField:"Content-Length"), requestLine.first == "HEAD" || response.statusCode == 304 { head += "Content-Length: "+length+"\r\n" }
        for (rawKey,value) in response.allHeaderFields {
            let key = String(describing:rawKey)
            if !excluded.contains(key.lowercased()) {
                if let values = value as? [String] { for value in values { head += key+": "+value+"\r\n" } }
                else { head += key+": "+String(describing:value)+"\r\n" }
            }
        }
        head += "\r\n"; return Data(head.utf8)
    }
    func urlSession(_ session: URLSession,dataTask: URLSessionDataTask,didBecome streamTask: URLSessionStreamTask) {
        guard !ended else { streamTask.cancel(); return }
        converted = true; stream = streamTask; task = nil
        streamTask.resume(); startStreamIfReady()
    }
    private func startStreamIfReady() {
        guard !ended, upgradeSent, stream != nil else { return }
        // The pre-upgrade receive remains the only receive during handoff. Any
        // callback appends behind earlier bytes while the write pump drains.
        upgradeSent = false; clientStreamReady = true
        readStream(); writeClientStream()
    }
    private func receiveWebSocketClient() {
        guard !ended, !clientEOF, !clientReceivePending else { return }
        clientReceivePending = true
        connection.receive(minimumIncompleteLength:1,maximumLength:65536) { [weak self] data,_,complete,error in
            guard let self, !self.ended else { return }
            self.clientReceivePending = false
            // A rejected upgrade uses this existing watcher for HTTP cancellation;
            // never start a competing receive when the non101 headers arrive.
            if !self.wantsWebSocket {
                if complete || error != nil || data?.isEmpty == false { self.finish() }
                else { self.watchHTTPClient() }
                return
            }
            if error != nil || (complete && !self.handshakeDelivered) { self.finish(); return }
            if let data, !data.isEmpty {
                guard data.count <= 2_097_152-self.initialWebSocketBytes.count else { self.finish(); return }
                self.initialWebSocketBytes.append(data)
            }
            self.clientEOF = complete
            if self.clientStreamReady { self.writeClientStream() }
            else if !complete { self.receiveWebSocketClient() }
        }
    }
    private func writeClientStream() {
        guard !ended, clientStreamReady, !clientWritePending, let stream else { return }
        if !initialWebSocketBytes.isEmpty {
            let chunk = Data(initialWebSocketBytes.prefix(65536))
            initialWebSocketBytes.removeFirst(chunk.count); clientWritePending = true
            stream.write(chunk,timeout:0) { [weak self] error in
                guard let self, !self.ended else { return }
                self.clientWritePending = false
                if error != nil { self.finish() } else { self.writeClientStream() }
            }
        } else if clientEOF {
            if !clientFinishedWriting {
                clientFinishedWriting = true; stream.closeWrite(); finishIfEOF()
            }
        } else { receiveWebSocketClient() }
    }
    private func readStream() {
        guard !ended, let stream, !serverEOF else { return }
        stream.readData(ofMinLength:1,maxLength:65536,timeout:0) { [weak self] data,eof,error in
            guard let self, !self.ended else { return }
            self.serverEOF = eof
            let afterSend = { [self] in
                guard !self.ended else { return }
                if error != nil { self.finish() }
                else if eof {
                    self.connection.send(content:nil,contentContext:.finalMessage,isComplete:true,completion:.contentProcessed { [weak self] sendError in
                        guard let self, !self.ended else { return }
                        if sendError != nil { self.finish() } else { self.serverFinishedSending = true; self.finishIfEOF() }
                    })
                } else { self.readStream() }
            }
            if let data, !data.isEmpty {
                self.observer.feed(data)
                self.connection.send(content:data,completion:.contentProcessed { [weak self] sendError in
                    guard let self, !self.ended else { return }
                    if sendError != nil { self.finish() } else { afterSend() }
                })
            } else { afterSend() }
        }
    }
    private func finishIfEOF() { if clientEOF && serverEOF && clientFinishedWriting && serverFinishedSending { finish() } }
    func urlSession(_ session: URLSession,dataTask: URLSessionDataTask,didReceive data: Data) {
        guard !ended, !converted, !switching, !bodyless else { return }
        observer.feed(data); dataTask.suspend()
        let chunk = Data(String(data.count,radix:16).utf8)+Data([13,10])+data+Data([13,10])
        connection.send(content:chunk,completion:.contentProcessed { [weak self] error in
            guard let self, !self.ended else { return }
            if error != nil { self.finish() } else { dataTask.resume() }
        })
    }
    func urlSession(_ session: URLSession,task: URLSessionTask,didCompleteWithError error: Error?) {
        guard !ended else { return }
        if converted {
            if task === stream, error != nil { finish() }
            return
        }
        if switching { if error != nil { finish() }; return }
        if !responseStarted { fail(502); return }
        if error != nil { finish(); return }
        observer.finish()
        connection.send(content:bodyless ? nil : Data("0\r\n\r\n".utf8),contentContext:.finalMessage,isComplete:true,completion:.contentProcessed { [weak self] _ in self?.finish() })
    }
    func urlSession(_ session: URLSession,task: URLSessionTask,willPerformHTTPRedirection response: HTTPURLResponse,newRequest request: URLRequest,completionHandler: @escaping (URLRequest?)->Void) { completionHandler(nil) }
    private func fail(_ status: Int) {
        guard !ended else { return }
        guard !responseStarted else { finish(); return }
        responseStarted = true
        connection.send(content:Data("HTTP/1.1 \(status) Relay Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".utf8),contentContext:.finalMessage,isComplete:true,completion:.contentProcessed { [weak self] _ in self?.finish() })
    }
    func cancel() { finish() }
    private func finish() {
        guard !ended else { return }; ended = true; timeout?.cancel()
        received.removeAll(); initialWebSocketBytes.removeAll(); headers.removeAll()
        connection.stateUpdateHandler = nil; connection.cancel()
        task?.cancel(); stream?.cancel(); session?.invalidateAndCancel()
        task = nil; stream = nil; session = nil; finished()
    }
}
