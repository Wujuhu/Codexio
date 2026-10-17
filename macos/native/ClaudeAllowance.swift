import Foundation
import Network

/// One loopback receiver owned by the main app. No OAuth, browser cookies,
/// model calls, LaunchAgent, or independently surviving collection process.
final class ClaudeAllowance {
    private let paths: AppPaths
    let root: URL
    let directory: URL
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.claude-allowance",qos:.utility)
    private var listener: NWListener?
    private var clients: [UUID:AllowanceConnection] = [:]
    private let nonce = UUID().uuidString+UUID().uuidString
    private var cached: Object = [:]
    private let startedAt = Date().timeIntervalSince1970
    private var metadataStamp: FileStamp?
    private var metadata: Object = [:]
    var onSample: ((Object)->Void)?
    init(paths: AppPaths,root: URL) {self.paths = paths; self.root = root; directory = paths.data.appendingPathComponent("claude-bridge")}
    func start() {
        guard !paths.mock else {return}
        queue.async { [weak self] in
            guard let self, self.listener == nil else {return}
            self.cached = readObject(self.directory.appendingPathComponent("snapshot.json"))
            do {
                let parameters = NWParameters.tcp
                parameters.requiredLocalEndpoint = .hostPort(host:"127.0.0.1",port:.any)
                let listener = try NWListener(using:parameters); self.listener = listener
                listener.stateUpdateHandler = { [weak self,weak listener] state in
                    guard let self, let listener else {return}
                    if case .ready = state, let port = listener.port {
                        do {
                            try atomicJSON(["url":"http://127.0.0.1:\(port.rawValue)/quota","nonce":self.nonce],to:self.directory.appendingPathComponent("runtime.json"))
                            try self.install()
                        } catch { /* Keep local data usable when managed settings refuse integration. */ }
                    }
                }
                listener.newConnectionHandler = { [weak self] connection in
                    guard let self, self.clients.count < 8 else {connection.cancel();return}
                    let id = UUID()
                    let client = AllowanceConnection(connection:connection,queue:self.queue,receive:{ [weak self] headers,data in
                        guard let self, headers["authorization"] == "Bearer "+self.nonce else {return false}
                        return self.accept(jsonObject(data))
                    },finished:{[weak self] in self?.clients.removeValue(forKey:id)})
                    self.clients[id] = client; client.start()
                }
                listener.start(queue:self.queue)
            } catch {self.listener = nil}
        }
    }
    func stop() {
        guard !paths.mock else {return}
        queue.sync {
            listener?.cancel(); listener = nil
            for client in Array(clients.values) {client.stop()}; clients.removeAll()
            let runtime = directory.appendingPathComponent("runtime.json")
            if readObject(runtime).string("nonce") == nonce {try? FileManager.default.removeItem(at:runtime)}
        }
    }
    func snapshot() -> Object {queue.sync {cached}}
    private func account(surface: String,session: String) -> Object {
        // Desktop may be signed into a different account from the CLI. Without
        // a matching identity from that host, retain a session-scoped identity.
        guard surface == "cli" else {return ["identityKey":identity(root.path+"|desktop"),"verified":false]}
        let file = root.lastPathComponent == ".claude" ? root.deletingLastPathComponent().appendingPathComponent(".claude.json") : root.appendingPathComponent(".claude.json")
        let stamp = FileStamp(file)
        if stamp != metadataStamp {metadataStamp = stamp; metadata = readObject(file).object("oauthAccount")}
        let id = metadata.string("accountUuid"), org = metadata.string("organizationUuid")
        return ["identityKey":identity(root.path+"|cli|"+(id.isEmpty ? "local" : id+"|"+org)),"verified":!id.isEmpty,
                "email":metadata.string("emailAddress"),"planType":metadata.string("planDisplayName",metadata.string("subscriptionType"))]
    }
    private func accept(_ payload: Object) -> Bool {
        guard payload.integer("schema") == 1, payload.string("config_root") == root.path,
              ["mods","statusline"].contains(payload.string("source")), payload.string("session_id").count <= 256,
              let captured = payload.number("captured_at"), abs(Date().timeIntervalSince1970-captured) <= 60 else {return false}
        let source = payload.string("source"), surface = payload.string("surface") == "desktop" ? "desktop" : "cli"
        let currentAccount = account(surface:surface,session:payload.string("session_id"))
        var windows: [Object] = []
        for window in payload.objects("windows").prefix(4) {
            guard ["five_hour","seven_day"].contains(window.string("kind")), let used = window.number("percentUsed"), used.isFinite, (0...100).contains(used) else {continue}
            let reset = parsedDate(window["resetsAt"])
            guard reset == nil || reset!.timeIntervalSince1970 > captured else {continue}
            windows.append(["kind":window.string("kind"),"used":used,"reset":reset?.timeIntervalSince1970 as Any? ?? NSNull()])
        }
        let contexts = payload.objects("contexts").prefix(32).filter {point in
            let window = point.integer("window") ?? 0
            guard window >= 0, window <= 2_147_483_647, point.string("model").count <= 256, point.string("reasoning_effort").count <= 32,
                  window > 0 || !point.string("model").isEmpty || !point.string("reasoning_effort").isEmpty,
                  let at = point.number("observed_at"), at >= startedAt, at <= captured+5 else {return false}
            return point.string("session_id").count <= 256 && !point.string("session_id").isEmpty &&
                ((!point.string("message_uuid").isEmpty && point.string("message_uuid").count <= 256) || (!point.string("prompt_id").isEmpty && point.string("prompt_id").count <= 256))
        }
        let previous = cached.number("captured_at") ?? 0
        let keepQuota = (previous > captured && previous <= Date().timeIntervalSince1970) ||
            (source == "statusline" && cached.string("source") == "mods" && cached.object("account").string("identityKey") == currentAccount.string("identityKey") && captured-previous < 120)
        if keepQuota {
            // Quota precedence must not discard another request's start data.
            if !contexts.isEmpty {var observation = cached; observation["contexts"] = Array(contexts); onSample?(observation)}
            return true
        }
        let value: Object = ["boundary_sample":payload["scheduled"] as? Bool ?? true,"contexts":Array(contexts),"source":source,"surface":surface,"captured_at":captured,"windows":windows,
                             "account":currentAccount,"session_id":payload.string("session_id")]
        cached = value; try? atomicJSON(value,to:directory.appendingPathComponent("snapshot.json")); onSample?(value)
        return true
    }
    private func install() throws {
        guard !paths.mock, FileManager.default.fileExists(atPath:root.path), let source = Bundle.main.url(forResource:"claude_bridge",withExtension:"json") else {return}
        let plugin = directory.appendingPathComponent("plugin"), runtime = directory.appendingPathComponent("runtime.json")
        let configuration = jsonString(["runtime":runtime.path,"root":root.path])
        for (relative,value) in readObject(source) {
            guard let text = value as? String, !relative.contains(".."), !relative.hasPrefix("/") else {continue}
            let path = plugin.appendingPathComponent(relative)
            let content = text.replacingOccurrences(of:"__CODEXIO_CONFIG__",with:configuration)
            if (try? String(contentsOf:path,encoding:.utf8)) != content {
                try FileManager.default.createDirectory(at:path.deletingLastPathComponent(),withIntermediateDirectories:true)
                try content.write(to:path,atomically:true,encoding:.utf8)
            }
        }
        let path = root.appendingPathComponent("settings.json")
        let existing = try? Data(contentsOf:path)
        guard existing == nil || (try? JSONSerialization.jsonObject(with:existing!)) is Object else {return}
        var settings = existing.map(jsonObject) ?? [:], env = settings.object("env")
        var directories = env.string("CLAUDE_CODE_PLUGIN_DIRS").split(separator:":").map(String.init)
        if !directories.contains(plugin.path) {directories.append(plugin.path)}
        env["CLAUDE_CODE_PLUGIN_DIRS"] = directories.joined(separator:":"); settings["env"] = env
        let priorURL = directory.appendingPathComponent("statusline.json"), prior = readObject(priorURL)
        let helper = Bundle.main.executableURL?.path ?? ""
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates = [home.appendingPathComponent(".local/bin/claude").path,"/opt/homebrew/bin/claude","/usr/local/bin/claude"]+(ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator:":").map {String($0)+"/claude"}
        let cli = URL(fileURLWithPath:candidates.first(where:{FileManager.default.isExecutableFile(atPath:$0)}) ?? home.appendingPathComponent(".local/bin/claude").path)
        let cliVersion = AppVersion(cli.resolvingSymlinksInPath().lastPathComponent)
        let legacy = FileManager.default.isExecutableFile(atPath:cli.path) && (cliVersion == nil || cliVersion! < AppVersion("2.1.287")!)
        if legacy {
            let original = settings.object("statusLine").string("command") == prior.string("managed") ? prior.object("original") : settings.object("statusLine")
            let fallback = original.string("command").isEmpty ? ":" : original.string("command")
            let command = "if [ -f "+Self.quote(runtime.path)+" ]; then "+Self.quote(helper)+" --claude-statusline "+Self.quote(directory.path)+"; else ( "+fallback+" ); fi"
            try atomicJSON(["managed":command,"original":original,"root":root.path],to:priorURL)
            var status = original; status["type"] = "command"; status["command"] = command; settings["statusLine"] = status
        } else if !prior.string("managed").isEmpty, settings.object("statusLine").string("command") == prior.string("managed") {
            let original = prior.object("original"); if original.isEmpty {settings.removeValue(forKey:"statusLine")} else {settings["statusLine"] = original}
        }
        if jsonString(settings) != jsonString(existing.map(jsonObject) ?? [:]) {
            if let existing {try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true); try existing.write(to:directory.appendingPathComponent("settings-backup-"+UUID().uuidString+".json"),options:.atomic)}
            try atomicJSON(settings,to:path)
        }
    }
    private static func quote(_ value: String) -> String {"'"+value.replacingOccurrences(of:"'",with:"'\\''")+"'"}
    static func statusline(_ arguments: [String]) -> Int32? {
        guard let index = arguments.firstIndex(of:"--claude-statusline") else {return nil}
        guard !arguments.contains("--mock"), arguments.indices.contains(index+1) else {return 0}
        let directory = URL(fileURLWithPath:arguments[index+1]).standardizedFileURL
        guard directory.lastPathComponent == "claude-bridge" else {return 0}
        let input = FileHandle.standardInput.readDataToEndOfFile(), raw = input.count <= 1_048_576 ? jsonObject(input) : [:]
        let prior = readObject(directory.appendingPathComponent("statusline.json")), runtime = readObject(directory.appendingPathComponent("runtime.json"))
        if let url = URL(string:runtime.string("url")), url.scheme == "http", url.host == "127.0.0.1", url.path == "/quota", !runtime.string("nonce").isEmpty {
            let limits = raw.object("rate_limits")
            let windows: [Object] = ["five_hour","seven_day"].compactMap {key in
                let window = limits.object(key); guard let used = window.number("used_percentage") else {return nil}
                return ["kind":key,"percentUsed":used,"resetsAt":window.number("resets_at").map {iso(Date(timeIntervalSince1970:$0))} as Any? ?? NSNull()]
            }
            var contexts: [Object] = []
            if !raw.string("prompt_id").isEmpty {
                var point: Object = ["session_id":raw.string("session_id"),"prompt_id":raw.string("prompt_id"),"observed_at":Date().timeIntervalSince1970]
                if let window = raw.object("context_window").integer("context_window_size"), window > 0 {point["window"] = window}
                let model = raw.object("model").string("id"), effort = raw.object("effort").string("level")
                if !model.isEmpty {point["model"] = model}
                if !effort.isEmpty {point["reasoning_effort"] = effort}
                contexts = [point]
            }
            let body: Object = ["contexts":contexts,"schema":1,"source":"statusline","surface":"cli","session_id":raw.string("session_id"),"config_root":prior.string("root"),"captured_at":Date().timeIntervalSince1970,"windows":windows]
            var request = URLRequest(url:url,timeoutInterval:1); request.httpMethod = "POST"; request.httpBody = try? jsonData(body)
            request.setValue("application/json",forHTTPHeaderField:"Content-Type"); request.setValue("Bearer "+runtime.string("nonce"),forHTTPHeaderField:"Authorization")
            let session = URLSession(configuration:.ephemeral), done = DispatchSemaphore(value:0)
            let task = session.dataTask(with:request) {_,_,_ in done.signal()}; task.resume(); _ = done.wait(timeout:.now()+1.5); session.invalidateAndCancel()
        }
        let original = prior.object("original").string("command")
        guard !original.isEmpty else {return 0}
        let process = Process(), pipe = Pipe(); process.executableURL = URL(fileURLWithPath:"/bin/sh"); process.arguments = ["-c",original]
        process.standardInput = pipe; process.standardOutput = FileHandle.standardOutput; process.standardError = FileHandle.standardError
        do {try process.run(); pipe.fileHandleForWriting.write(input); try? pipe.fileHandleForWriting.close(); process.waitUntilExit(); return process.terminationStatus} catch {return 0}
    }
}

private final class AllowanceConnection {
    let connection: NWConnection, queue: DispatchQueue
    let receivePayload: ([String:String],Data)->Bool
    let finished: ()->Void
    var bytes = Data(), closed = false
    init(connection: NWConnection,queue: DispatchQueue,receive: @escaping ([String:String],Data)->Bool,finished: @escaping ()->Void) {self.connection = connection;self.queue = queue;receivePayload = receive;self.finished = finished}
    func start() {connection.start(queue:queue); read(); queue.asyncAfter(deadline:.now()+5) {[weak self] in self?.stop()}}
    func stop() {guard !closed else {return};closed = true;connection.cancel();finished()}
    func read() {
        connection.receive(minimumIncompleteLength:1,maximumLength:32768) {[weak self] data,_,complete,error in
            guard let self, !self.closed else {return}
            if let data {self.bytes.append(data)}
            guard self.bytes.count <= 65536 else {self.stop();return}
            if let boundary = self.bytes.range(of:Data("\r\n\r\n".utf8)), let head = String(data:self.bytes[..<boundary.lowerBound],encoding:.utf8) {
                let lines = head.components(separatedBy:"\r\n")
                var headers: [String:String] = [:]
                for line in lines.dropFirst() {let parts = line.split(separator:":",maxSplits:1);if parts.count == 2 {headers[String(parts[0]).lowercased()] = parts[1].trimmingCharacters(in:.whitespaces)}}
                guard lines.first == "POST /quota HTTP/1.1", headers["transfer-encoding"] == nil, let length = headers["content-length"].flatMap(Int.init), length >= 0, length <= 32768 else {self.stop();return}
                if self.bytes.count-boundary.upperBound >= length {
                    let ok = self.receivePayload(headers,self.bytes.subdata(in:boundary.upperBound..<boundary.upperBound+length))
                    self.connection.send(content:Data("HTTP/1.1 \(ok ? "200 OK" : "403 Forbidden")\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".utf8),completion:.contentProcessed { [weak self] _ in self?.stop() });return
                }
            }
            if complete || error != nil {self.stop()} else {self.read()}
        }
    }
}
