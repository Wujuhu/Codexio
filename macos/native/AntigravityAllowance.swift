import AppKit
import Darwin
import Foundation
import Security

struct AntigravityQuotaWindow: Equatable, Identifiable {
    var id: String
    var group: String
    var title: String
    var cadence: String
    var remaining: Double?
    var reset_at: Double?
    var reset_text: String
    var expired: Bool { reset_at.map { $0 <= Date().timeIntervalSince1970 } == true }
}

struct AntigravityQuota: Equatable {
    enum Availability: Equatable { case waiting, appClosed, unavailable, available }
    var windows: [AntigravityQuotaWindow] = []
    var availability: Availability = .waiting
    var observed: Date?
    var accountKey = ""
    var planType = ""

    enum Cadence: String { case fiveHour = "5h", weekly }
    func representative(_ cadence: Cadence) -> AntigravityQuotaWindow? {
        windows.filter { $0.cadence == cadence.rawValue && $0.remaining != nil && !$0.expired }
            .min { ($0.remaining ?? 100) < ($1.remaining ?? 100) }
    }

    var message: String? {
        switch availability {
        case .waiting: return nil
        case .appClosed: return L("打开 Antigravity App 后可读取额度", "Open Antigravity App to read allowances")
        case .unavailable: return L("Antigravity 额度暂不可用", "Antigravity allowances are temporarily unavailable")
        case .available: return nil
        }
    }
}

/// Connects only to the signed, same-user App's existing local language service.
/// No login, credential persistence, CLI, IDE, redirect or ambient proxy.
final class AntigravityAllowance: NSObject, URLSessionTaskDelegate {
    private struct ProcessRow {
        let pid: pid_t
        let parent: pid_t
    }
    private struct ListenerDescriptor: Decodable {
        let pid: pid_t
        let httpPort: UInt16
        let httpsPort: UInt16
        let csrfToken: String

        var validToken: Bool {
            (16...512).contains(csrfToken.utf8.count) && csrfToken.allSatisfy {
                $0.isASCII && ($0.isLetter || $0.isNumber || "._~+/=-".contains($0))
            }
        }
    }
    private let queue = DispatchQueue(label: "com.wujuhu.codexio.antigravity-quota", qos: .utility)
    private var verifiedBundles: [URL: FileStamp] = [:] // queue owned
    private lazy var client: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = ["HTTPEnable":0, "HTTPSEnable":0, "SOCKSEnable":0, "ProxyAutoConfigEnable":0, "ProxyAutoDiscoveryEnable":0]
        config.urlCache = nil
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.timeoutIntervalForRequest = 2
        config.timeoutIntervalForResource = 3
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    private let requestLock = NSLock()
    private var epoch = UUID()
    private var activeRequest: URLSessionDataTask?

    func stop() {
        requestLock.lock(); defer { requestLock.unlock() }
        epoch = UUID()
        activeRequest?.cancel()
        activeRequest = nil
    }

    private var generation: UUID {
        requestLock.lock(); defer { requestLock.unlock() }
        return epoch
    }

    func refresh(completion: @escaping (AntigravityQuota) -> Void) {
        let apps = NSWorkspace.shared.runningApplications.compactMap { app -> (pid_t, URL)? in
            guard !app.isTerminated, let id = app.bundleIdentifier, id.hasPrefix("com.google.antigravity"),
                  let bundle = app.bundleURL?.resolvingSymlinksInPath(),
                  ["antigravity.app", "antigravity 2.0.app"].contains(bundle.lastPathComponent.lowercased()) else { return nil }
            return (app.processIdentifier, bundle)
        }
        let generation = self.generation
        queue.async { [self] in
            guard generation == self.generation else { return }
            guard !apps.isEmpty else { completion(AntigravityQuota(availability: .appClosed)); return }
            guard let rows = try? processes() else { completion(AntigravityQuota(availability: .unavailable)); return }
            let lookup = Dictionary(uniqueKeysWithValues: rows.map { ($0.pid, $0.parent) })
            let deadline = ProcessInfo.processInfo.systemUptime + 6
            for (app, bundle) in apps where lookup[app] != nil && isGoogleApp(bundle) {
                for row in rows where belongsToApp(row.pid, app: app, bundle: bundle, processParents: lookup) {
                    guard generation == self.generation, ProcessInfo.processInfo.systemUptime < deadline else { break }
                    guard let source = try? readListener(row.pid) else { continue }
                    for port in source.ports.prefix(12) where processPath(row.pid)?.hasPrefix(bundle.path + "/Contents/") == true {
                        guard generation == self.generation, ProcessInfo.processInfo.systemUptime < deadline else { break }
                        let before = readAccount(port: port, token: source.token, generation: generation)
                        if let summary = read("RetrieveUserQuotaSummary", body: Data("{\"forceRefresh\":false}".utf8), port: port, token: source.token, generation: generation),
                           var quota = Self.parse(summary), generation == self.generation {
                            if let before, before == readAccount(port: port, token: source.token, generation: generation) {
                                quota.accountKey = before.key
                                quota.planType = before.plan
                            }
                            guard generation == self.generation else { return }
                            completion(quota)
                            return
                        }
                    }
                }
            }
            if generation == self.generation { completion(AntigravityQuota(availability: .unavailable)) }
        }
    }

    private func isGoogleApp(_ bundle: URL) -> Bool {
        let executable = Bundle(url: bundle)?.executableURL ?? bundle
        let stamp = FileStamp(executable)
        if verifiedBundles[bundle] == stamp { return true }
        var code: SecStaticCode?, requirement: SecRequirement?
        guard stamp.exists, SecStaticCodeCreateWithPath(bundle as CFURL, [], &code) == errSecSuccess, let code,
              SecRequirementCreateWithString("anchor apple generic and certificate leaf[subject.OU] = \"EQHXZ8M8AV\"" as CFString, [], &requirement) == errSecSuccess,
              SecStaticCodeCheckValidity(code, [], requirement) == errSecSuccess else { return false }
        if verifiedBundles.count >= 4 { verifiedBundles.removeAll() }
        verifiedBundles[bundle] = stamp
        return true
    }

    private func processes() throws -> [ProcessRow] {
        let result = try execute("/bin/ps", ["-ax", "-o", "uid=,pid=,ppid="], timeout: 2)
        return String(decoding: result.output, as: UTF8.self).split(separator: "\n").compactMap { line in
            let fields = line.split(whereSeparator: \.isWhitespace)
            guard fields.count == 3, uid_t(fields[0]) == getuid(), let pid = pid_t(fields[1]), let parent = pid_t(fields[2]) else { return nil }
            return ProcessRow(pid: pid, parent: parent)
        }
    }

    private func processPath(_ pid: pid_t) -> String? {
        var buffer = [CChar](repeating: 0, count: 4096)
        let count = buffer.withUnsafeMutableBufferPointer { proc_pidpath(pid, $0.baseAddress, UInt32($0.count)) }
        return count > 0 ? String(cString: buffer) : nil
    }

    private func belongsToApp(_ pid: pid_t, app: pid_t, bundle: URL, processParents: [pid_t: pid_t]) -> Bool {
        guard pid != app, let executable = processPath(pid), executable.hasPrefix(bundle.path + "/Contents/") else { return false }
        let name = URL(fileURLWithPath: executable).lastPathComponent
        guard name.hasPrefix("language_server") || name.hasPrefix("language-server") else { return false }
        var current = pid
        for _ in 0..<12 {
            guard let parent = processParents[current], parent != current else { break }
            if parent == app { return true }
            current = parent
        }
        return false
    }

    private func readListener(_ pid: pid_t) throws -> (token: String, ports: [UInt16]) {
        let listeners = try execute("/usr/sbin/lsof", ["-nP", "-a", "-p", String(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"], timeout: 2)
        let ports = Set(String(decoding: listeners.output, as: UTF8.self).split(separator: "\n").compactMap { line -> UInt16? in
            guard line.hasPrefix("n127.0.0.1:"), let value = UInt16(line.dropFirst("n127.0.0.1:".count)), value > 0 else { return nil }
            return value
        })
        guard !ports.isEmpty else { return ("", []) }
        if let descriptor = listenerDescriptor(pid, ports: ports) {
            return (descriptor.csrfToken, [descriptor.httpsPort])
        }

        // Older desktop App builds still advertise their handshake in arguments.
        let command = try execute("/bin/ps", ["-ww", "-p", String(pid), "-o", "command="], timeout: 2)
        let arguments = String(decoding: command.output, as: UTF8.self).split(whereSeparator: \.isWhitespace)
        let token = arguments.enumerated().compactMap { index, value -> String? in
            for flag in ["--csrf_token", "--csrf-token"] {
                if value == flag, arguments.indices.contains(index + 1) { return String(arguments[index + 1]) }
                if value.hasPrefix(flag + "=") { return String(value.dropFirst(flag.count + 1)) }
            }
            return nil
        }.first
        guard let token, !token.isEmpty, token.utf8.count <= 512, token.utf8.allSatisfy({ $0 > 32 && $0 < 127 }) else { return ("", []) }
        return (token, ports.sorted())
    }

    private func listenerDescriptor(_ pid: pid_t, ports: Set<UInt16>) -> ListenerDescriptor? {
        var process = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &process, size) == size, process.pbi_uid == getuid() else { return nil }
        let start = Double(process.pbi_start_tvsec) + Double(process.pbi_start_tvusec) / 1_000_000
        let daemon = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".gemini/antigravity/daemon", isDirectory: true)
        for port in ports.sorted() {
            let path = daemon.appendingPathComponent("hub_\(port).json")
            guard let descriptor = readDescriptor(path, since: start), descriptor.pid == pid,
                  descriptor.httpPort == port, descriptor.httpsPort != port, ports.contains(descriptor.httpsPort),
                  descriptor.validToken else { continue }
            return descriptor
        }
        return nil
    }

    private func readDescriptor(_ path: URL, since start: TimeInterval) -> ListenerDescriptor? {
        let maxSize = 16 * 1024
        let fd = open(path.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { return nil }
        let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? file.close() }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == getuid(),
              info.st_mode & 0o022 == 0, (1...maxSize).contains(Int(info.st_size)) else { return nil }
        let modified = Double(info.st_mtimespec.tv_sec) + Double(info.st_mtimespec.tv_nsec) / 1_000_000_000
        guard modified >= start, let data = try? file.read(upToCount: maxSize + 1), data.count <= maxSize else { return nil }
        return try? JSONDecoder().decode(ListenerDescriptor.self, from: data)
    }

    private struct Account: Equatable {
        let key: String
        let plan: String
    }

    private func readAccount(port: UInt16, token: String, generation: UUID) -> Account? {
        let body = Data(#"{"metadata":{"ideName":"antigravity","extensionName":"antigravity","locale":"en"}}"#.utf8)
        guard let value = read("GetUserStatus", body: body, port: port, token: token, generation: generation) else { return nil }
        let status = value.object("userStatus")
        let email = status.string("email").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let parts = email.split(separator: "@", omittingEmptySubsequences: false)
        guard email.utf8.count <= 320, parts.count == 2, parts.allSatisfy({ !$0.isEmpty }), !email.contains(where: \.isWhitespace) else { return nil }
        let tier = status.object("userTier").string("name").trimmingCharacters(in: .whitespacesAndNewlines)
        let fallback = status.object("planStatus").object("planInfo").string("planName").trimmingCharacters(in: .whitespacesAndNewlines)
        let plan = tier.isEmpty ? fallback : tier
        guard !plan.isEmpty, plan.utf8.count <= 160 else { return nil }
        return Account(key: digest(Data(("antigravity-account:" + email).utf8)), plan: plan)
    }

    private func read(_ method: String, body: Data, port: UInt16, token: String, generation: UUID) -> Object? {
        guard let url = URL(string: "https://127.0.0.1:\(port)/exa.language_server_pb.LanguageServerService/\(method)") else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("1", forHTTPHeaderField: "Connect-Protocol-Version")
        request.setValue(token, forHTTPHeaderField: "X-Codeium-Csrf-Token")
        request.httpBody = body
        let done = DispatchSemaphore(value: 0)
        let result = QuotaResult()
        let task = client.dataTask(with: request) { data, response, error in
            defer { done.signal() }
            guard error == nil, (response as? HTTPURLResponse)?.statusCode == 200,
                  let data, data.count <= 512 * 1024 else { return }
            let value = jsonObject(data)
            result.lock.lock(); result.value = value; result.lock.unlock()
        }
        requestLock.lock()
        guard generation == epoch else { requestLock.unlock(); task.cancel(); return nil }
        activeRequest = task
        task.resume()
        requestLock.unlock()
        defer {
            requestLock.lock()
            if activeRequest === task { activeRequest = nil }
            requestLock.unlock()
        }
        guard done.wait(timeout: .now() + 3) == .success else { task.cancel(); return nil }
        result.lock.lock(); defer { result.lock.unlock() }
        return result.value
    }

    private final class QuotaResult: @unchecked Sendable {
        let lock = NSLock()
        var value: Object?
    }

    private static func parse(_ raw: Object) -> AntigravityQuota? {
        let response = raw.object("response")
        let groups = [response.objects("groups"), raw.object("summary").objects("groups"), raw.objects("groups")]
            .first { !$0.isEmpty } ?? []
        var windows: [AntigravityQuotaWindow] = []
        var seen = Set<String>()
        for group in groups.prefix(32) {
            let name = group.string("displayName", group.string("name"))
            for bucket in group.objects("buckets").prefix(32) {
                let id = bucket.string("bucketId", bucket.string("id"))
                let cadence: AntigravityQuota.Cadence
                switch id { case "gemini-5h": cadence = .fiveHour; case "gemini-weekly": cadence = .weekly; default: continue }
                let title = bucket.string("displayName", bucket.string("name"))
                let key = id
                guard !id.isEmpty, seen.insert(key).inserted, !bucket.flag("disabled") else { continue }
                let remaining = bucket.object("remaining")
                let fraction = remaining.number("remainingFraction") ?? bucket.number("remainingFraction")
                    ?? (remaining.string("case") == "remainingFraction" ? remaining.number("value") : nil)
                let percent = fraction.flatMap { (0...1).contains($0) ? $0 * 100 : nil }
                let reset = parsedDate(bucket["resetTime"])?.timeIntervalSince1970
                let description = bucket.string("description")
                guard percent != nil || reset != nil || !description.isEmpty else { continue }
                windows.append(AntigravityQuotaWindow(id: key, group: name, title: title, cadence: cadence.rawValue, remaining: percent,
                                                       reset_at: reset, reset_text: String(description.prefix(180))))
            }
        }
        guard !windows.isEmpty else { return nil }
        return AntigravityQuota(windows: windows, availability: .available, observed: Date())
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if challenge.protectionSpace.host == "127.0.0.1", challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}
