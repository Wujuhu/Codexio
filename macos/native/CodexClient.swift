import Foundation
import AppKit
import Security
import LocalAuthentication

final class ReplyBox {
    let semaphore = DispatchSemaphore(value:0)
    var result: Result<Any,Error>?
}

final class CodexClient {
    private var process: Process?
    private var input: FileHandle?
    private var buffer = Data()
    private var pending: [Int:ReplyBox] = [:]
    private var nextID = 0
    private var shuttingDown = false
    private var retired: [Process] = []
    private let lock = NSRecursiveLock()
    private let writer = NSLock()
    var onNotification: ((String,Object)->Void)?

    private static let probeLock = NSLock()
    private static var probes: [String:(Date,Bool)] = [:]
    private static func bundledCandidates(_ app: URL) -> [URL] {
        if app.lastPathComponent == "CodexCLI.app" { return [app.appendingPathComponent("Contents/MacOS/codex")] }
        let resources = app.appendingPathComponent("Contents/Resources"), package = resources.appendingPathComponent("codex-cli")
        var results: [URL] = []
        let metadata = readObject(package.appendingPathComponent("codex-package.json")), entry = metadata.string("entrypoint")
        if metadata.integer("layoutVersion") == 1, !entry.isEmpty, !entry.hasPrefix("/"), !entry.split(separator:"/").contains("..") {
            results.append(package.appendingPathComponent(entry))
        }
        results += [package.appendingPathComponent("bin/codex"),package.appendingPathComponent("CodexCLI.app/Contents/MacOS/codex"),resources.appendingPathComponent("codex")]
        return results
    }
    private static func usable(_ path: URL) -> Bool {
        guard FileManager.default.isExecutableFile(atPath:path.path), let attributes = try? path.resourceValues(forKeys:[.isRegularFileKey,.fileSizeKey,.contentModificationDateKey]), attributes.isRegularFile == true else { return false }
        if path.deletingLastPathComponent().lastPathComponent == "MacOS" {
            let info = try? Installation.plist(path.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Info.plist"))
            guard info?.string("CFBundleIdentifier") == "com.openai.codex.cli", info?.string("CFBundleExecutable") == path.lastPathComponent else { return false }
        }
        let key = path.path+":"+String(attributes.fileSize ?? 0)+":"+String(attributes.contentModificationDate?.timeIntervalSince1970 ?? 0)
        probeLock.lock(); let cached = probes[key]; probeLock.unlock()
        if let cached, Date().timeIntervalSince(cached.0) < (cached.1 ? 60 : 5) { return cached.1 }
        let version = try? execute(path.path,["--version"],timeout:4)
        var valid = version?.code == 0 && (version.flatMap {String(data:$0.output,encoding:.utf8)} ?? "").range(of:#"(?m)^\s*codex-cli\s+\S+"#,options:.regularExpression) != nil
        if valid {
            let help = try? execute(path.path,["app-server","--help"],timeout:4)
            let output = help.flatMap {String(data:$0.output+$0.error,encoding:.utf8)} ?? ""
            valid = help?.code == 0 && output.contains("app-server") && output.contains("--listen")
        }
        probeLock.lock(); if probes.count > 128 { probes.removeAll() }; probes[key] = (Date(),valid); probeLock.unlock()
        return valid
    }
    static func discover(_ hint: String?) -> URL? {
        var candidates: [URL] = []
        for value in [hint,ProcessInfo.processInfo.environment["CODEX_CLI_PATH"]].compactMap({$0}) where !value.isEmpty {
            let path = URL(fileURLWithPath:NSString(string:value).expandingTildeInPath)
            if path.pathExtension.lowercased() == "app" { candidates += bundledCandidates(path) }
            else {
                candidates.append(path)
                candidates += [path.appendingPathComponent("bin/codex"),path.appendingPathComponent("codex")]
                var parent = path.deletingLastPathComponent()
                for _ in 0..<9 {
                    if parent.pathExtension.lowercased() == "app", parent.lastPathComponent != "CodexCLI.app" { candidates += bundledCandidates(parent); break }
                    let next = parent.deletingLastPathComponent(); if next == parent { break }; parent = next
                }
            }
        }
        var apps: [URL] = []
        for bundle in ["com.openai.codex","com.openai.chat"] {
            apps += NSRunningApplication.runningApplications(withBundleIdentifier:bundle).compactMap(\.bundleURL)
            if let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier:bundle) { apps.append(app) }
        }
        for root in [URL(fileURLWithPath:"/Applications"),FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications")] {
            apps += ["ChatGPT.app","Codex.app"].map {root.appendingPathComponent($0)}
        }
        for app in apps { candidates += bundledCandidates(app) }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let dirs = (ProcessInfo.processInfo.environment["PATH"] ?? "").components(separatedBy:":") + ["/opt/homebrew/bin","/usr/local/bin",home+"/.local/bin",home+"/.volta/bin",home+"/.npm-global/bin"]
        candidates += dirs.filter {!$0.isEmpty}.map {URL(fileURLWithPath:$0).appendingPathComponent("codex")}
        let node = URL(fileURLWithPath:home+"/.nvm/versions/node")
        candidates += ((try? FileManager.default.contentsOfDirectory(at:node,includingPropertiesForKeys:nil)) ?? []).sorted {$0.lastPathComponent > $1.lastPathComponent}.map {$0.appendingPathComponent("bin/codex")}
        var seen = Set<String>()
        return candidates.map { $0.standardizedFileURL.resolvingSymlinksInPath() }.first { seen.insert($0.path).inserted && usable($0) }
    }
    var running: Bool { lock.lock(); defer { lock.unlock() }; return process?.isRunning == true }
    func start(hint: String?) throws {
        lock.lock(); let closed = shuttingDown; lock.unlock()
        guard !closed else { throw AppFailure(L("连接已关闭", "Connection closed")) }
        if running { return }
        guard let executable = Self.discover(hint) else { throw AppFailure(L("未找到 Codex，请安装 ChatGPT 或 Codex 并登录", "Codex was not found. Install ChatGPT or Codex and sign in.")) }
        let child = Process(), out = Pipe(), err = Pipe(), stdin = Pipe()
        child.executableURL = executable; child.arguments = ["app-server","--listen","stdio://"]
        child.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        var environment = ProcessInfo.processInfo.environment; environment["RUST_LOG"] = "error"
        for key in ["PYTHONHOME","PYTHONPATH","QT_PLUGIN_PATH","QT_QPA_PLATFORM"] { environment.removeValue(forKey:key) }
        child.environment = environment; child.standardInput = stdin; child.standardOutput = out; child.standardError = err
        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; self?.failPending(AppFailure(L("Codex 连接已关闭", "Codex connection closed"))); return }
            self?.receive(data)
        }
        err.fileHandleForReading.readabilityHandler = { handle in if handle.availableData.isEmpty { handle.readabilityHandler = nil } }
        do {
            lock.lock()
            guard !shuttingDown else { lock.unlock(); throw AppFailure(L("连接已关闭", "Connection closed")) }
            process = child; input = stdin.fileHandleForWriting; buffer = Data()
            do { try child.run(); lock.unlock() } catch { lock.unlock(); throw error }
            _ = try request("initialize",["clientInfo":["name":"codexio","title":"Codexio","version":BuildInfo.version],"capabilities":["optOutNotificationMethods":["item/agentMessage/delta","item/reasoning/delta","item/commandExecution/outputDelta"]]])
            try send(["method":"initialized","params":[:]])
        } catch { close(); throw error }
    }
    func request(_ method: String, _ params: Object? = nil, timeout: TimeInterval = 20) throws -> Object {
        lock.lock(); nextID += 1; let id = nextID; let box = ReplyBox(); pending[id] = box; lock.unlock()
        var payload: Object = ["method":method,"id":id]; if let params { payload["params"] = params }
        do { try send(payload) } catch { lock.lock(); pending.removeValue(forKey:id); lock.unlock(); throw error }
        if box.semaphore.wait(timeout:.now()+timeout) == .timedOut {
            lock.lock(); pending.removeValue(forKey:id); lock.unlock()
            throw AppFailure(L("Codex 请求超时", "Codex request timed out"))
        }
        return try box.result?.get() as? Object ?? [:]
    }
    private func send(_ payload: Object) throws {
        let data = try jsonData(payload) + Data([10])
        writer.lock(); defer { writer.unlock() }
        guard let input, process?.isRunning == true else { throw AppFailure(L("Codex 尚未连接", "Codex is not connected")) }
        try input.write(contentsOf:data)
    }
    private func receive(_ data: Data) {
        lock.lock(); buffer.append(data)
        var lines: [Data] = []
        while let newline = buffer.firstIndex(of:10) { lines.append(buffer.subdata(in:0..<newline)); buffer.removeSubrange(0...newline) }
        if buffer.count > 32_000_000 { buffer.removeAll() }
        lock.unlock()
        for line in lines {
            let message = jsonObject(line)
            if let id = message.integer("id"), message["method"] == nil {
                lock.lock(); let box = pending.removeValue(forKey:id); lock.unlock()
                if let box {
                    if let error = message["error"] as? Object { box.result = .failure(AppFailure(error.string("message",L("Codex 返回错误", "Codex returned an error")))) }
                    else { box.result = .success(message["result"] ?? [:]) }
                    box.semaphore.signal()
                }
            } else if message["id"] != nil {
                try? send(["id":message["id"]!,"error":["code":-32601,"message":"Method not supported"]])
            } else if let method = message["method"] as? String { onNotification?(method,message.object("params")) }
        }
    }
    private func failPending(_ error: Error) {
        lock.lock(); let boxes = Array(pending.values); pending.removeAll(); lock.unlock()
        for box in boxes { box.result = .failure(error); box.semaphore.signal() }
    }
    func close() {
        lock.lock(); let old = process; process = nil; let stdin = input; input = nil; lock.unlock()
        try? stdin?.close()
        if let old, old.isRunning {
            old.terminate(); lock.lock(); retired.removeAll {!$0.isRunning}; retired.append(old); lock.unlock()
        }
        failPending(AppFailure(L("连接已关闭", "Connection closed")))
    }
    func shutdown() {
        lock.lock(); shuttingDown = true; lock.unlock(); closeAndWait()
    }
    func closeAndWait() {
        close()
        lock.lock(); let children = retired; retired.removeAll(); lock.unlock()
        let deadline = Date().addingTimeInterval(3)
        while children.contains(where:{$0.isRunning}) && Date() < deadline { Thread.sleep(forTimeInterval:0.03) }
        for child in children where child.isRunning { kill(child.processIdentifier,SIGKILL); child.waitUntilExit() }
    }
    deinit { close() }
}

struct HTTPFailure: LocalizedError {
    let code: Int
    let retryAfter: TimeInterval?
    var errorDescription: String? {
        switch code {
        case 401: return L("请重新登录 Codex", "Sign in to Codex again")
        case 403: return L("此账户没有这项数据的访问权限", "This account cannot access these details")
        case 404: return L("当前账户尚未提供这项明细", "This account has not provided these details")
        case 429: return L("请求频繁，请稍后刷新", "Too many requests. Refresh later.")
        default: return L("服务暂不可用", "Service unavailable") + " (\(code))"
        }
    }
}

private final class HTTPCollector: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    var data = Data(); var error: Error?; var response: HTTPURLResponse?
    let maximum: Int; let completed = DispatchSemaphore(value:0)
    init(maximum: Int) { self.maximum = maximum }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition)->Void) {
        self.response = response as? HTTPURLResponse
        if response.expectedContentLength > Int64(maximum) { error = AppFailure(L("响应超过大小限制", "Response exceeds the size limit")); completionHandler(.cancel) }
        else { completionHandler(.allow) }
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if self.data.count + data.count > maximum { error = AppFailure(L("响应超过大小限制", "Response exceeds the size limit")); dataTask.cancel() }
        else { self.data.append(data) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) { self.error = self.error ?? error; completed.signal() }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?)->Void) {
        if task.originalRequest?.url?.scheme == "https" && request.url?.scheme != "https" { completionHandler(nil) }
        else if task.originalRequest?.value(forHTTPHeaderField:"Authorization") != nil && (request.url?.host != task.originalRequest?.url?.host || request.url?.port != task.originalRequest?.url?.port) { completionHandler(nil) }
        else { completionHandler(request) }
    }
}

enum HTTP {
    static func read(_ url: URL, maximum: Int = 16_000_000) throws -> Data { try request(URLRequest(url:url),maximum:maximum) }
    static func request(_ request: URLRequest, maximum: Int = 16_000_000, timeout: TimeInterval = 25) throws -> Data {
        let delegate = HTTPCollector(maximum:maximum), config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = timeout; config.timeoutIntervalForResource = timeout
        let session = URLSession(configuration:config,delegate:delegate,delegateQueue:nil)
        let task = session.dataTask(with:request); task.resume()
        defer { session.invalidateAndCancel() }
        guard delegate.completed.wait(timeout:.now()+timeout+2) == .success else { throw AppFailure(L("网络请求超时", "Network request timed out")) }
        if let error = delegate.error { throw error }
        guard let response = delegate.response, (200..<300).contains(response.statusCode) else { throw HTTPFailure(code:delegate.response?.statusCode ?? 0,retryAfter:delegate.response?.value(forHTTPHeaderField:"Retry-After").flatMap(Double.init)) }
        return delegate.data
    }
}

struct AccountIdentity: Equatable {
    let accountID: String
    let userID: String
    var key: String { identity(["codexio-week-v1",accountID,userID]) }
}

struct BackendCredentials {
    let token: String
    let account: AccountIdentity
    static func load(root: URL) throws -> BackendCredentials {
        var auth = readObject(root.appendingPathComponent("auth.json"))
        if auth.isEmpty {
            let name = "cli|" + String(digest(Data(root.standardizedFileURL.path.utf8)).prefix(16))
            let context = LAContext(); context.interactionNotAllowed = true
            let query: [CFString:Any] = [kSecClass:kSecClassGenericPassword,kSecAttrService:"Codex Auth",kSecAttrAccount:name,kSecReturnData:true,kSecMatchLimit:kSecMatchLimitOne,kSecUseAuthenticationContext:context]
            var item: CFTypeRef?
            if SecItemCopyMatching(query as CFDictionary,&item) == errSecSuccess, let data = item as? Data { auth = jsonObject(data) }
        }
        let tokens = auth.object("tokens"), token = tokens.string("access_token")
        guard !token.isEmpty else { throw AppFailure(L("需要本机 Codex 的 ChatGPT 登录状态", "A local Codex ChatGPT sign-in is required")) }
        var claims: Object = [:]
        if let section = token.split(separator:".").dropFirst().first {
            var encoded = String(section).replacingOccurrences(of:"-",with:"+").replacingOccurrences(of:"_",with:"/")
            encoded += String(repeating:"=",count:(4-encoded.count%4)%4)
            if let data = Data(base64Encoded:encoded) { claims = jsonObject(data) }
        }
        let provider = claims.object("https://api.openai.com/auth")
        let accountID = tokens.string("account_id",provider.string("chatgpt_account_id"))
        let userID = claims.string("sub",provider.string("chatgpt_user_id"))
        guard !accountID.isEmpty else { throw AppFailure(L("无法确认当前账户", "Cannot identify the current account")) }
        return BackendCredentials(token:token,account:AccountIdentity(accountID:accountID,userID:userID))
    }
}

final class AccountAnalytics {
    let root: URL
    private(set) var account: AccountIdentity?
    init(root: URL) { self.root = root }
    func load(path: String, body: Object? = nil) throws -> Object {
        let credentials = try BackendCredentials.load(root:root)
        if let account, account != credentials.account { throw AppFailure(L("账户已变更，请重新刷新", "The account changed. Refresh again.")) }
        account = credentials.account
        guard ["usage/plan_limit_history?days=7","usage/thread_usage/query_v2"].contains(path) else { throw AppFailure("Unsupported analytics route") }
        var request = URLRequest(url:URL(string:"https://chatgpt.com/backend-api/wham/"+path)!)
        request.setValue("Bearer "+credentials.token,forHTTPHeaderField:"Authorization")
        request.setValue(credentials.account.accountID,forHTTPHeaderField:"ChatGPT-Account-Id")
        request.setValue("application/json",forHTTPHeaderField:"Accept")
        request.setValue("Codexio/"+BuildInfo.version,forHTTPHeaderField:"User-Agent")
        if let body { request.httpMethod = "POST"; request.httpBody = try jsonData(body); request.setValue("application/json",forHTTPHeaderField:"Content-Type") }
        let data = try HTTP.request(request)
        guard try BackendCredentials.load(root:root).account == credentials.account else { throw AppFailure(L("账户已变更，请重新刷新", "The account changed. Refresh again.")) }
        guard let result = try JSONSerialization.jsonObject(with:data) as? Object else { throw AppFailure(L("服务返回了无效统计数据", "The service returned invalid statistics")) }
        return result
    }
    func plan() throws -> Object { try load(path:"usage/plan_limit_history?days=7") }
    func chats(_ threads: [Object]) throws -> Object {
        var all: [Object] = [], batch: [Object] = [], ids = Set<String>(), allIDs = Set<String>(), stamp: Any?
        func unavailable(_ id: String) -> Object { ["thread_id":id,"data_status":"unavailable","groups":[Object]()] }
        func flush() throws {
            let result = try load(path:"usage/thread_usage/query_v2",body:["threads":batch])
            let expected = Set(batch.map {$0.string("thread_id")}); var received = Set<String>()
            guard let rows = result["threads"] as? [Object] else { throw AppFailure(L("服务返回了无效统计数据", "The service returned invalid statistics")) }
            for row in rows {
                let id = row.string("thread_id")
                guard expected.contains(id), received.insert(id).inserted else { throw AppFailure(L("服务返回了无效统计数据", "The service returned invalid statistics")) }
                for part in [row]+row.objects("groups") {
                    for key in ["weekly_limit_percent","five_hour_limit_percent","balance_usage_credits"] where part[key] != nil && !(part[key] is NSNull) {
                        guard let value = part.number(key), value >= 0 else { throw AppFailure(L("服务返回了无效统计数据", "The service returned invalid statistics")) }
                    }
                }
                all.append(row)
            }
            all += expected.subtracting(received).sorted().map(unavailable)
            stamp = result["data_as_of"]; batch.removeAll(); ids.removeAll()
        }
        for raw in threads {
            let id = raw.string("thread_id"), descendants = raw["descendant_thread_ids"] as? [String] ?? []
            let values = [id]+descendants, unique = Set(values)
            guard values.allSatisfy({!$0.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty && $0.count <= 512}), unique.count == values.count, unique.isDisjoint(with:allIDs), values.count <= 1000 else { all.append(unavailable(id)); continue }
            if !batch.isEmpty && (batch.count == 100 || ids.count+unique.count > 1000) { try flush() }
            batch.append(["thread_id":id,"created_at":raw["created_at"] ?? NSNull(),"descendant_thread_ids":descendants]); ids.formUnion(unique); allIDs.formUnion(unique)
        }
        if !batch.isEmpty { try flush() }
        return ["threads":all,"data_as_of":stamp ?? NSNull()]
    }
}
