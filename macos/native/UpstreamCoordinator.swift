import Foundation
import AppKit

final class UpstreamCoordinator {
    private let state: AppState
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.upstream",qos:.utility)
    private let client = CodexClient()
    private var directory: URL { state.paths.data.appendingPathComponent("upstream") }
    private var journalURL: URL { directory.appendingPathComponent("route.json") }
    private var relay: UpstreamRelay?
    private var quitting = false
    init(state: AppState) { self.state = state }
    private func nested(_ value: Object,_ path: [String]) -> Any? {
        var current: Any = value
        for key in path { guard let object = current as? Object, let next = object[key] else { return nil }; current = next }
        return current
    }
    private func keyPath(_ path: [String]) throws -> String {
        guard path.allSatisfy({$0.range(of:#"^[A-Za-z0-9_-]+$"#,options:.regularExpression) != nil}) else { throw AppFailure(L("当前配置键不能安全接管", "The configuration key cannot be safely managed")) }
        return path.joined(separator:".")
    }
    private func read() throws -> Object {
        try client.start(hint:state.preferences.general.string("codex_path"))
        return try client.request("config/read",["includeLayers":true])
    }
    private func layer(_ snapshot: Object,file: String? = nil) throws -> Object {
        let layers = snapshot.objects("layers").filter {$0.object("name").string("type") == "user" && ($0["disabledReason"] == nil || $0["disabledReason"] is NSNull)}
        guard let found = file.flatMap({file in layers.first {$0.object("name").string("file") == file}}) ?? (file == nil ? layers.last : nil) else { throw AppFailure(L("找不到可安全修改的用户配置层", "No writable user configuration layer was found")) }
        return found
    }
    private func write(_ path: [String],value: Any,file: String,version: String) throws {
        let response = try client.request("config/value/write",["keyPath":try keyPath(path),"value":value,"mergeStrategy":"replace","filePath":file,"expectedVersion":version])
        guard response.string("status") == "ok" else { throw AppFailure(L("配置被更高优先级覆盖，路由未生效", "A higher-priority configuration overrides this route")) }
    }
    func recoverAndResume() {
        guard !state.paths.mock else { return }
        let enabled = state.preferences.analytics.flag("upstream_detection_enabled")
        queue.async { [weak self] in
            guard let self, !self.quitting else { return }
            do { try self.restore(); if enabled { try self.start(); DispatchQueue.main.async { self.state.onRestartCodex?({}) } } }
            catch { self.report(error.localizedDescription) }
        }
    }
    func setEnabled(_ enabled: Bool) {
        guard !state.paths.mock else { return }
        queue.async { [weak self] in
            guard let self, !self.quitting else { return }
            do {
                if enabled { try self.restore(); try self.start() } else { try self.restore() }
                DispatchQueue.main.async {
                    self.state.setPreference("upstream_detection_enabled",enabled)
                    self.state.upstreamStatus = L("配置已更新；重新打开 Codex 后生效", "Configuration updated; reopen Codex to apply it")
                    self.state.refreshQuota(); self.state.onRestartCodex?({})
                }
            } catch { self.report(error.localizedDescription) }
        }
    }
    private func report(_ message: String) { DispatchQueue.main.async { self.state.upstreamStatus = message } }
    private func start() throws {
        let snapshot = try read(), config = snapshot.object("config"), provider = config.string("model_provider","openai")
        guard !["amazon-bedrock","ollama","lmstudio"].contains(provider) else { throw AppFailure(L("该内置服务不支持 Responses 转发", "This built-in provider does not support a Responses relay")) }
        let custom = config.object("model_providers").object(provider)
        guard custom.string("wire_api","responses") == "responses" else { throw AppFailure(L("上游检测仅支持 Responses API", "Upstream detection supports the Responses API only")) }
        let account = try client.request("account/read",["refreshToken":false]).object("account")
        let basePath = provider == "openai" ? ["openai_base_url"] : ["model_providers",provider,"base_url"]
        var origin = provider == "openai" ? config.string("openai_base_url") : custom.string("base_url")
        if origin.isEmpty && provider == "openai" { origin = config.string("forced_login_method") == "api" || account.string("type") == "apiKey" ? "https://api.openai.com/v1" : "https://chatgpt.com/backend-api/codex" }
        guard let components = URLComponents(string:origin), ["https","http"].contains(components.scheme ?? ""), components.host != nil, components.user == nil, components.password == nil, components.query == nil, components.fragment == nil else { throw AppFailure(L("模型服务地址无效", "Invalid provider URL")) }
        while origin.hasSuffix("/") { origin.removeLast() }
        let owner = try layer(snapshot), file = owner.object("name").string("file"), raw = owner.object("config")
        let profile = config.string("profile",raw.string("profile"))
        var locator = basePath
        if !profile.isEmpty && nested(raw,["profiles",profile]+basePath) != nil { locator = ["profiles",profile]+basePath }
        let before = nested(raw,locator)
        let nonce = UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        let runtime = directory.appendingPathComponent("native-"+nonce)
        try FileManager.default.createDirectory(at:runtime,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        try atomicJSON(["origin":origin,"route_token":nonce,"database":state.paths.data.appendingPathComponent("upstream.sqlite").path],to:runtime.appendingPathComponent("job.json"))
        let relay = try UpstreamRelay(directory:runtime); try relay.start(); self.relay = relay
        let deadline = Date().addingTimeInterval(12), readyURL = runtime.appendingPathComponent("ready.json")
        while !FileManager.default.fileExists(atPath:readyURL.path) && !FileManager.default.fileExists(atPath:runtime.appendingPathComponent("failed.json").path) && Date() < deadline { Thread.sleep(forTimeInterval:0.05) }
        let ready = readObject(readyURL)
        guard let port = ready.integer("port"), (1...65535).contains(port) else { relay.stop(); self.relay = nil; throw AppFailure(L("本机转发未能启动", "The local relay could not start")) }
        let routed = "http://127.0.0.1:\(port)/\(nonce)/v1"
        let journal: Object = ["route_version":3,"native":true,"embedded":true,"root_config":state.accountRoot.appendingPathComponent("config.toml").path,"config":file,"provider_id":provider,"locator":locator,"origin":origin,"original_present":before != nil,"original_endpoint":before ?? NSNull(),"applied_endpoint":routed,"original_digest":identity([before != nil,before ?? NSNull()]),"applied_digest":identity([true,routed]),"runtime":runtime.path,"pid":Int(getpid())]
        do {
            try atomicJSON(journal,to:journalURL)
            try write(locator,value:routed,file:file,version:owner.string("version"))
            let effective = try read().object("config")
            guard nested(effective,basePath) as? String == routed else { throw AppFailure(L("路由未生效，正在恢复", "The route was not applied; restoring it")) }
            report(L("上游检测已开启", "Upstream detection is enabled"))
        } catch { try? restore(); throw error }
    }
    private func restore() throws {
        let journal = readObject(journalURL)
        guard !journal.isEmpty else { relay?.stop(); relay = nil; return }
        guard journal.integer("route_version") == 3, let locator = journal["locator"] as? [String], !locator.isEmpty else { throw AppFailure(L("旧路由需要先在原版 Codexio 中关闭，恢复记录已保留", "Disable the legacy route in the previous Codexio app first; recovery data is retained")) }
        let applied = journal.string("applied_endpoint"), before = journal["original_endpoint"] ?? NSNull(), present = journal.flag("original_present")
        guard journal.string("original_digest") == identity([present,before]), journal.string("applied_digest") == identity([true,applied]) else { throw AppFailure(L("路由恢复记录校验失败", "Route recovery information could not be verified")) }
        let snapshot = try read(), owner = try layer(snapshot,file:journal.string("config"))
        let actual = nested(owner.object("config"),locator)
        if actual as? String == applied {
            try write(locator,value:present ? before : NSNull(),file:journal.string("config"),version:owner.string("version"))
        } else if identity(actual ?? NSNull()) != identity(before) {
            throw AppFailure(L("路由已被其他程序修改，保留当前配置和转发", "Another app changed the route; the current configuration and relay are retained"))
        }
        try FileManager.default.removeItem(at:journalURL)
        if !journal.string("runtime").isEmpty {
            let runtime = URL(fileURLWithPath:journal.string("runtime"))
            if runtime.deletingLastPathComponent() == directory && runtime.lastPathComponent.range(of:#"^native-[a-f0-9]{32}$"#,options:.regularExpression) != nil {
                try atomicJSON(["restored_at":Date().timeIntervalSince1970],to:runtime.appendingPathComponent("restored.json"))
                let legacy = runtime.appendingPathComponent("CodexioRelay").path
                let processes = Installation.processPaths().filter {$0.1 == legacy && $0.0 != getpid()}
                try Installation.retire(processes)
            }
        }
        relay?.stop(); relay = nil
    }
    func prepareQuit(completion: @escaping () -> Void) {
        guard !state.paths.mock else { completion(); return }
        queue.async { [weak self] in
            guard let self else { DispatchQueue.main.async(execute:completion); return }
            self.quitting = true
            do {
                try self.restore(); self.client.closeAndWait()
                DispatchQueue.main.async(execute:completion)
            }
            catch {
                self.report(error.localizedDescription); self.client.close()
                DispatchQueue.main.async {
                    let alert = NSAlert(); alert.messageText = L("上游路由未能恢复", "The upstream route could not be restored")
                    alert.informativeText = error.localizedDescription
                    alert.addButton(withTitle:L("返回应用", "Return to app")); alert.addButton(withTitle:L("保留恢复记录并退出", "Keep recovery data and quit"))
                    if alert.runModal() == .alertSecondButtonReturn {
                        self.queue.async { self.relay?.stop(); self.relay = nil; self.client.closeAndWait(); DispatchQueue.main.async(execute:completion) }
                    } else { self.state.onCancelQuit?() }
                }
            }
        }
    }
    func cancelQuit() {
        queue.async { [weak self] in
            guard let self else { return }; self.quitting = false
            guard self.relay == nil, self.state.preferences.analytics.flag("upstream_detection_enabled") else { return }
            do { try self.restore(); try self.start() } catch { self.report(error.localizedDescription) }
        }
    }
}
