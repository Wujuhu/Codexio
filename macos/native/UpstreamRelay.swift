import Foundation
import Network
import AppKit

final class ResponseModelObserver {
    private var buffer = Data()
    private var lines: [Data] = []
    private var eventSize = 0
    private var dropping = false
    private var isSSE: Bool?
    private let database: Database
    init(database: Database) { self.database = database }
    func contentType(_ type: String) { if type.contains("text/event-stream") { isSSE = true } else if type.contains("application/json") { isSSE = false } }
    func feed(_ data: Data) {
        if isSSE == nil { let probe = String(decoding:data.prefix(40),as:UTF8.self).trimmingCharacters(in:.whitespacesAndNewlines); if !probe.isEmpty { isSSE = !probe.hasPrefix("{") && !probe.hasPrefix("[") } }
        if isSSE == false {
            if buffer.count+data.count <= 2_097_152 { buffer.append(data) } else { dropping = true; buffer.removeAll() }; return
        }
        buffer.append(data)
        while let newline = buffer.firstIndex(of:10) {
            var line = buffer.subdata(in:0..<newline); buffer.removeSubrange(0...newline)
            if line.last == 13 { line.removeLast() }
            if line.isEmpty {
                if !dropping { observe(lines.reduce(Data()) { $0 + ($0.isEmpty ? Data() : Data([10])) + $1 }) }
                dropping = false; lines.removeAll(); eventSize = 0
            } else if line.starts(with:Data("data:".utf8)) && !dropping {
                line.removeFirst(5); if line.first == 32 { line.removeFirst() }
                eventSize += line.count
                if eventSize > 2_097_152 { dropping = true; lines.removeAll() } else { lines.append(line) }
            }
        }
        if buffer.count > 2_097_152 { buffer.removeAll(); dropping = true; lines.removeAll() }
    }
    func finish() { if isSSE == false && !dropping { observe(buffer) } }
    private func observe(_ data: Data) {
        let value = jsonObject(data), event = value.string("type","response.completed")
        guard ["response.created","response.completed","response.incomplete","response.failed"].contains(event) else { return }
        let response = value.object("response").isEmpty ? value : value.object("response")
        let id = response.string("id"), model = response.string("model")
        guard !id.isEmpty, id.count <= 256, !model.isEmpty, model.count <= 256 else { return }
        let rank = event == "response.created" ? 1 : 2
        try? database.run("INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(response_id) DO UPDATE SET model=excluded.model,event=excluded.event,rank=excluded.rank,observed_at=excluded.observed_at WHERE excluded.rank>=observations.rank",[id,model,event,rank,Date().timeIntervalSince1970])
        try? database.run("UPDATE revision SET value=value+1 WHERE id=1")
    }
}

final class UpstreamRelay {
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.relay")
    private var listener: NWListener?
    private var connections: [UUID:RelayConnection] = [:]
    private var lastRequest = Date()
    private var timer: DispatchSourceTimer?
    private var lease: FileLease?
    private let directory: URL
    private let origin: URL
    private let token: String
    private let database: Database
    init(directory: URL) throws {
        self.directory = directory
        let job = readObject(directory.appendingPathComponent("job.json"))
        guard let origin = URL(string:job.string("origin")), ["http","https"].contains(origin.scheme ?? ""), origin.host != nil, job.string("route_token").range(of:#"^[0-9a-f]{32}$"#,options:.regularExpression) != nil else { throw AppFailure("Invalid relay configuration") }
        self.origin = origin; token = job.string("route_token")
        let expected = directory.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("upstream.sqlite")
        guard URL(fileURLWithPath:job.string("database")).standardizedFileURL == expected.standardizedFileURL else { throw AppFailure("Invalid relay database path") }
        database = try Database(expected)
        try database.script("CREATE TABLE IF NOT EXISTS observations(response_id TEXT PRIMARY KEY,model TEXT NOT NULL,event TEXT NOT NULL,rank INTEGER NOT NULL,observed_at REAL NOT NULL); CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY,value INTEGER NOT NULL); INSERT OR IGNORE INTO revision VALUES(1,0);")
    }
    static func run(directory: URL) throws {
        let relay = try UpstreamRelay(directory:directory)
        try relay.start(); withExtendedLifetime(relay) { dispatchMain() }
    }
    private func start() throws {
        guard let lease = FileLease(directory.appendingPathComponent("relay.lock")) else { return }; self.lease = lease
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host:"127.0.0.1",port:.any)
        let listener = try NWListener(using:parameters,on:.any); self.listener = listener
        listener.stateUpdateHandler = { [weak self] status in
            guard let self else { return }
            if case .ready = status, let port = listener.port { try? atomicJSON(["pid":Int(getpid()),"port":Int(port.rawValue)],to:self.directory.appendingPathComponent("ready.json")) }
            if case .failed = status { exit(1) }
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            self.lastRequest = Date()
            let id = UUID()
            let relay = RelayConnection(connection:connection,queue:self.queue,origin:self.origin,token:self.token,database:self.database) { [weak self] in self?.connections.removeValue(forKey:id) }
            self.connections[id] = relay; relay.start()
        }
        listener.start(queue:queue)
        let timer = DispatchSource.makeTimerSource(queue:queue); self.timer = timer
        timer.schedule(deadline:.now()+30,repeating:30)
        timer.setEventHandler { [weak self] in
            guard let self, FileManager.default.fileExists(atPath:self.directory.appendingPathComponent("restored.json").path), self.connections.isEmpty, Date().timeIntervalSince(self.lastRequest) > 120 else { return }
            let hosts = NSRunningApplication.runningApplications(withBundleIdentifier:"com.openai.chat") + NSRunningApplication.runningApplications(withBundleIdentifier:"com.openai.codex")
            let cliRunning = Installation.processPaths().contains { $0.1.hasSuffix("/codex") }
            if hosts.isEmpty && !cliRunning { exit(0) }
        }
        timer.resume()
    }
}

private final class RelayConnection: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let connection: NWConnection
    let queue: DispatchQueue
    let origin: URL
    let token: String
    let observer: ResponseModelObserver
    let finished: () -> Void
    private var received = Data()
    private var headers: [String:String] = [:]
    private var requestLine: [String] = []
    private var headerEnd: Int?
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var ended = false
    private var responseStarted = false
    private var timeout: DispatchWorkItem?
    init(connection: NWConnection,queue: DispatchQueue,origin: URL,token: String,database: Database,finished: @escaping ()->Void) {
        self.connection = connection; self.queue = queue; self.origin = origin; self.token = token; self.observer = ResponseModelObserver(database:database); self.finished = finished
    }
    func start() {
        connection.start(queue:queue)
        let timeout = DispatchWorkItem { [weak self] in if self?.task == nil { self?.fail(408) } }; self.timeout = timeout
        queue.asyncAfter(deadline:.now()+60,execute:timeout); receive()
    }
    private func receive() {
        connection.receive(minimumIncompleteLength:1,maximumLength:65536) { [weak self] data,_,complete,error in
            guard let self, !self.ended else { return }
            if let data { self.received.append(data) }
            if self.received.count > 128_000_000 { self.fail(413); return }
            do { if try self.parse() { return } } catch { self.fail(400); return }
            if complete || error != nil { self.finish() } else { self.receive() }
        }
    }
    private func parse() throws -> Bool {
        if headerEnd == nil {
            guard let range = received.range(of:Data([13,10,13,10])) else { if received.count > 65536 { throw AppFailure("Header too large") }; return false }
            headerEnd = range.upperBound
            guard let text = String(data:received.subdata(in:0..<range.lowerBound),encoding:.utf8) else { throw AppFailure("Invalid headers") }
            let lines = text.components(separatedBy:"\r\n"); requestLine = (lines.first ?? "").components(separatedBy:" ")
            guard requestLine.count == 3, ["GET","POST","DELETE","OPTIONS"].contains(requestLine[0]) else { throw AppFailure("Invalid request") }
            for line in lines.dropFirst() {
                guard let separator = line.firstIndex(of:":") else { throw AppFailure("Invalid header") }
                headers[String(line[..<separator]).lowercased()] = String(line[line.index(after:separator)...]).trimmingCharacters(in:.whitespaces)
            }
            if headers["expect"]?.lowercased() == "100-continue" { connection.send(content:Data("HTTP/1.1 100 Continue\r\n\r\n".utf8),completion:.contentProcessed({_ in})) }
        }
        let start = headerEnd!, data = received.subdata(in:start..<received.count)
        let body: Data
        if headers["transfer-encoding"]?.lowercased().contains("chunked") == true {
            guard headers["content-length"] == nil else { throw AppFailure("Ambiguous framing") }
            guard let decoded = try decodeChunks(data) else { return false }; body = decoded
        } else {
            let length: Int
            if let raw = headers["content-length"] { guard let count = Int(raw), count >= 0, count <= 128_000_000 else { throw AppFailure("Invalid length") }; length = count } else { length = 0 }
            guard data.count >= length else { return false }; body = data.prefix(length)
        }
        let prefix = "/"+token+"/v1", target = requestLine[1]
        guard target == prefix || target.hasPrefix(prefix+"/") || target.hasPrefix(prefix+"?") else { fail(403); return true }
        let suffix = String(target.dropFirst(prefix.count))
        guard !suffix.contains(".."), let url = URL(string:origin.absoluteString+suffix), url.host == origin.host else { throw AppFailure("Invalid route") }
        var request = URLRequest(url:url); request.httpMethod = requestLine[0]; request.httpBody = body.isEmpty ? nil : body
        let excluded = Set(["host","connection","keep-alive","content-length","transfer-encoding","expect","accept-encoding","upgrade","proxy-authorization","proxy-connection","x-codexio-route"])
        for (key,value) in headers where !excluded.contains(key) { request.setValue(value,forHTTPHeaderField:key) }
        request.setValue("identity",forHTTPHeaderField:"Accept-Encoding")
        let config = URLSessionConfiguration.ephemeral; config.timeoutIntervalForRequest = 600; config.timeoutIntervalForResource = 7200
        let delegates = OperationQueue(); delegates.maxConcurrentOperationCount = 1; delegates.underlyingQueue = queue
        let session = URLSession(configuration:config,delegate:self,delegateQueue:delegates); self.session = session
        task = session.dataTask(with:request); received.removeAll(); timeout?.cancel(); task?.resume(); return true
    }
    private func decodeChunks(_ data: Data) throws -> Data? {
        var cursor = 0, output = Data()
        while cursor < data.count {
            guard let line = data.range(of:Data([13,10]),in:cursor..<data.count) else { return nil }
            let raw = String(decoding:data.subdata(in:cursor..<line.lowerBound),as:UTF8.self).split(separator:";").first ?? ""
            guard let count = Int(raw,radix:16), count >= 0, count <= 128_000_000 else { throw AppFailure("Invalid chunk") }
            cursor = line.upperBound
            if count == 0 { return output }
            guard cursor+count+2 <= data.count else { return nil }
            guard data[cursor+count] == 13 && data[cursor+count+1] == 10 else { throw AppFailure("Invalid chunk ending") }
            output.append(data.subdata(in:cursor..<cursor+count)); cursor += count+2
        }
        return nil
    }
    func urlSession(_ session: URLSession,dataTask: URLSessionDataTask,didReceive response: URLResponse,completionHandler: @escaping (URLSession.ResponseDisposition)->Void) {
        guard let response = response as? HTTPURLResponse else { completionHandler(.cancel); fail(502); return }
        observer.contentType(response.value(forHTTPHeaderField:"Content-Type") ?? "")
        var head = "HTTP/1.1 \(response.statusCode) Response\r\nConnection: close\r\nTransfer-Encoding: chunked\r\n"
        for (rawKey,value) in response.allHeaderFields {
            let key = String(describing:rawKey)
            if !["connection","content-length","transfer-encoding","content-encoding","keep-alive"].contains(key.lowercased()) { head += key+": "+String(describing:value)+"\r\n" }
        }
        head += "\r\n"; responseStarted = true
        connection.send(content:Data(head.utf8),completion:.contentProcessed { [weak self] error in if error != nil { self?.finish() } })
        completionHandler(.allow)
    }
    func urlSession(_ session: URLSession,dataTask: URLSessionDataTask,didReceive data: Data) {
        observer.feed(data); dataTask.suspend()
        let chunk = Data(String(data.count,radix:16).utf8)+Data([13,10])+data+Data([13,10])
        connection.send(content:chunk,completion:.contentProcessed { [weak self] error in if error != nil { self?.finish() } else { dataTask.resume() } })
    }
    func urlSession(_ session: URLSession,task: URLSessionTask,didCompleteWithError error: Error?) {
        observer.finish()
        if !responseStarted { fail(502); return }
        if error != nil { finish(); return }
        connection.send(content:Data("0\r\n\r\n".utf8),completion:.contentProcessed { [weak self] _ in self?.finish() })
    }
    func urlSession(_ session: URLSession,task: URLSessionTask,willPerformHTTPRedirection response: HTTPURLResponse,newRequest request: URLRequest,completionHandler: @escaping (URLRequest?)->Void) { completionHandler(request.url?.host == origin.host && request.url?.scheme == origin.scheme && request.url?.port == origin.port ? request : nil) }
    private func fail(_ status: Int) {
        guard !responseStarted else { finish(); return }
        connection.send(content:Data("HTTP/1.1 \(status) Relay Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".utf8),completion:.contentProcessed { [weak self] _ in self?.finish() })
    }
    private func finish() { guard !ended else { return }; ended = true; timeout?.cancel(); connection.cancel(); task?.cancel(); session?.invalidateAndCancel(); finished() }
}
