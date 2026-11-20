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
                        if let quota = readSummary(port: port, token: source.token, generation: generation), generation == self.generation {
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
        let command = try execute("/bin/ps", ["-ww", "-p", String(pid), "-o", "command="], timeout: 2)
        let arguments = String(decoding: command.output, as: UTF8.self).split(whereSeparator: \.isWhitespace)
        let token = arguments.enumerated().compactMap { index, value -> String? in
            if value == "--csrf_token", arguments.indices.contains(index + 1) { return String(arguments[index + 1]) }
            if value.hasPrefix("--csrf_token=") { return String(value.dropFirst("--csrf_token=".count)) }
            return nil
        }.first
        guard let token, !token.isEmpty, token.utf8.count <= 512, token.utf8.allSatisfy({ $0 > 32 && $0 < 127 }) else { return ("", []) }
        let listeners = try execute("/usr/sbin/lsof", ["-nP", "-a", "-p", String(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"], timeout: 2)
        let ports = String(decoding: listeners.output, as: UTF8.self).split(separator: "\n").compactMap { line -> UInt16? in
            guard line.hasPrefix("n127.0.0.1:"), let value = UInt16(line.dropFirst("n127.0.0.1:".count)), value > 0 else { return nil }
            return value
        }
        return (token, Array(Set(ports)).sorted())
    }

    private func readSummary(port: UInt16, token: String, generation: UUID) -> AntigravityQuota? {
        guard let url = URL(string: "https://127.0.0.1:\(port)/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary") else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("1", forHTTPHeaderField: "Connect-Protocol-Version")
        request.setValue(token, forHTTPHeaderField: "X-Codeium-Csrf-Token")
        request.httpBody = Data("{\"forceRefresh\":false}".utf8)
        let done = DispatchSemaphore(value: 0)
        let result = QuotaResult()
        let task = client.dataTask(with: request) { data, response, error in
            defer { done.signal() }
            guard error == nil, (response as? HTTPURLResponse)?.statusCode == 200,
                  let data, data.count <= 512 * 1024 else { return }
            let value = Self.parse(jsonObject(data))
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
        var value: AntigravityQuota?
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
