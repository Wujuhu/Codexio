import Foundation
import AppKit
import Darwin

final class FileLease {
    private var descriptor: Int32 = -1
    init?(_ url: URL) {
        descriptor = open(url.path,O_CREAT | O_RDWR | O_CLOEXEC,0o600)
        if descriptor < 0 || flock(descriptor,LOCK_EX | LOCK_NB) != 0 { if descriptor >= 0 { Darwin.close(descriptor) }; descriptor = -1; return nil }
    }
    deinit { if descriptor >= 0 { flock(descriptor,LOCK_UN); Darwin.close(descriptor) } }
}

enum Installation {
    static let canonical = URL(fileURLWithPath:"/Applications/Codexio.app")
    static let widgetRelative = "Contents/PlugIns/CodexioWidget.appex"
    static let agentLabel = "com.wujuhu.codexio.widget-refresh"
    static var agentURL: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents/"+agentLabel+".plist") }
    static func plist(_ url: URL) throws -> Object {
        let data = try Data(contentsOf:url)
        guard let value = try PropertyListSerialization.propertyList(from:data,format:nil) as? Object else { throw AppFailure(L("应用信息无效", "Invalid application information")) }
        return value
    }
    static func verify(_ bundle: URL, version: String? = nil) throws -> Object {
        guard try bundle.resourceValues(forKeys:[.isSymbolicLinkKey]).isSymbolicLink != true else { throw AppFailure(L("应用路径不能是符号链接", "The app path cannot be a symbolic link")) }
        let info = try plist(bundle.appendingPathComponent("Contents/Info.plist")), widget = try plist(bundle.appendingPathComponent(widgetRelative+"/Contents/Info.plist"))
        guard info.string("CFBundleIdentifier") == BuildInfo.bundleID, info.string("CFBundleExecutable") == "Codexio", widget.string("CFBundleIdentifier") == BuildInfo.widgetID, let build = Int(widget.string("CFBundleVersion")), build > 0, info.string("CFBundleShortVersionString").split(separator:".").count == 3 else { throw AppFailure(L("应用或小组件标识无效", "Invalid app or widget identity")) }
        if let version, info.string("CFBundleShortVersionString") != version { throw AppFailure(L("应用版本不匹配", "The app version does not match")) }
        guard try execute("/usr/bin/codesign",["--verify","--deep","--strict",bundle.path]).code == 0 else { throw AppFailure(L("应用签名校验失败", "App signature verification failed")) }
        var result = info; result["CodexioWidgetBuild"] = build; return result
    }
    static func binariesMatch(_ first: URL,_ second: URL) -> Bool {
        ["Contents/MacOS/Codexio",widgetRelative+"/Contents/MacOS/CodexioWidget"].allSatisfy { path in
            guard let a = try? Data(contentsOf:first.appendingPathComponent(path),options:.mappedIfSafe), let b = try? Data(contentsOf:second.appendingPathComponent(path),options:.mappedIfSafe) else { return false }; return digest(a) == digest(b)
        }
    }
    static func processPaths() -> [(Int32,String)] {
        guard let result = try? execute("/bin/ps",["-ww","-u",String(getuid()),"-o","pid=,comm="]), let text = String(data:result.output,encoding:.utf8) else { return [] }
        return text.split(separator:"\n").compactMap { line in
            let pieces = line.trimmingCharacters(in:.whitespaces).split(separator:" ",maxSplits:1,omittingEmptySubsequences:true)
            guard pieces.count == 2, let pid = Int32(pieces[0]) else { return nil }; return (pid,String(pieces[1]).trimmingCharacters(in:.whitespaces))
        }
    }
    static func stopOldAgent() throws {
        let service = "gui/\(getuid())/"+agentLabel
        if (try? execute("/bin/launchctl",["print",service]).code) == 0 {
            _ = try execute("/bin/launchctl",["bootout",service],timeout:10)
            guard (try? execute("/bin/launchctl",["print",service]).code) != 0 else { throw AppFailure(L("旧版小组件后台服务未能退出", "The legacy widget service could not stop")) }
        }
        if FileManager.default.fileExists(atPath:agentURL.path) { try FileManager.default.removeItem(at:agentURL) }
    }
    private static func arguments(_ pid: Int32) -> String {
        (try? execute("/bin/ps",["-p",String(pid),"-o","args="]).output).flatMap {String(data:$0,encoding:.utf8)} ?? ""
    }
    private static func ownedHost(_ path: String) -> Bool {
        guard path.hasSuffix("/Contents/MacOS/Codexio") else { return false }
        let bundle = URL(fileURLWithPath:path).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        return (try? plist(bundle.appendingPathComponent("Contents/Info.plist")))?.string("CFBundleIdentifier") == BuildInfo.bundleID
    }
    private static func ownedWidget(_ path: String) -> Bool {
        guard path.hasSuffix("/CodexioWidget.appex/Contents/MacOS/CodexioWidget") else { return false }
        let bundle = URL(fileURLWithPath:path).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        return (try? plist(bundle.appendingPathComponent("Contents/Info.plist")))?.string("CFBundleIdentifier") == BuildInfo.widgetID
    }
    static func retire(_ processes: [(Int32,String)]) throws {
        let targets = processes.filter {$0.0 != getpid()}
        func alive() -> [(Int32,String)] {
            let current = Dictionary(processPaths(),uniquingKeysWith:{$1})
            return targets.filter {current[$0.0] == $0.1}
        }
        for (pid,_) in alive() { kill(pid,SIGTERM) }
        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline && !alive().isEmpty { Thread.sleep(forTimeInterval:0.1) }
        for (pid,_) in alive() { kill(pid,SIGKILL) }
        let killed = Date().addingTimeInterval(2)
        while Date() < killed && !alive().isEmpty { Thread.sleep(forTimeInterval:0.1) }
        guard alive().isEmpty else { throw AppFailure(L("旧版 Codexio 进程未能退出", "An old Codexio process could not stop")) }
    }
    static func retireLegacyWidgetServices(paths: AppPaths) throws {
        guard !paths.mock else { return }
        try stopOldAgent()
        let services = processPaths().filter { pid,path in
            guard pid != getpid(), ownedHost(path) else { return false }
            let args = arguments(pid)
            return !args.contains("--mock") && ["--widget-service","--widget-refresh"].contains(where:args.contains)
        }
        try retire(services)
        let lock = paths.data.appendingPathComponent("native-widget.lock")
        if FileManager.default.fileExists(atPath:lock.path) { try FileManager.default.removeItem(at:lock) }
    }
    static func prepareReplacement(paths: AppPaths) throws {
        guard !paths.mock else { return }
        try retireLegacyWidgetServices(paths:paths)
        let hosts = processPaths().filter { pid,path in pid != getpid() && ownedHost(path) && !arguments(pid).contains("--mock") }
        for (pid,_) in hosts { _ = NSRunningApplication(processIdentifier:pid)?.terminate() }
        let deadline = Date().addingTimeInterval(30)
        while Date() < deadline {
            let current = Dictionary(processPaths(),uniquingKeysWith:{$1})
            if !hosts.contains(where:{current[$0.0] == $0.1}) { break }
            Thread.sleep(forTimeInterval:0.1)
        }
        let remaining = Dictionary(processPaths(),uniquingKeysWith:{$1})
        guard !hosts.contains(where:{remaining[$0.0] == $0.1}) else { throw AppFailure(L("旧版正在退出，请完成其退出提示后重试", "Finish quitting the previous app before retrying installation")) }
        try retire(processPaths().filter {ownedWidget($0.1)})
    }
    static func launchChecked(_ bundle: URL, paths: AppPaths, timeout: TimeInterval = 30) throws {
        let token = UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        let ready = paths.data.appendingPathComponent("startup-"+token+".json")
        let process = Process(); process.executableURL = bundle.appendingPathComponent("Contents/MacOS/Codexio")
        var environment = ProcessInfo.processInfo.environment; environment["CODEXIO_STARTUP_TOKEN"] = token; environment["CODEXIO_SKIP_UPDATE_ONCE"] = "1"
        process.environment = environment; process.standardInput = FileHandle.nullDevice; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        try process.run()
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            if readObject(ready).integer("pid") == Int(process.processIdentifier) { try? FileManager.default.removeItem(at:ready); return }
            Thread.sleep(forTimeInterval:0.1)
        }
        if process.isRunning {
            process.terminate()
            let stopping = Date().addingTimeInterval(3)
            while process.isRunning && Date() < stopping { Thread.sleep(forTimeInterval:0.05) }
            if process.isRunning { kill(process.processIdentifier,SIGKILL); process.waitUntilExit() }
        }
        throw AppFailure(L("新版未能确认启动，保留旧版以便恢复", "The new app did not confirm startup; the previous app is retained"))
    }
    static func takeoverIfNeeded(paths: AppPaths) throws -> Bool {
        guard !paths.mock else { return false }
        let source = Bundle.main.bundleURL.standardizedFileURL
        guard source.pathExtension == "app" else { throw AppFailure(L("请从完整的 Codexio.app 启动", "Launch the complete Codexio.app bundle")) }
        let incomingInfo = try verify(source)
        if source == canonical { return false }
        if let installed = try? verify(canonical) {
            let current = installed.string("CFBundleShortVersionString").split(separator:".").compactMap {Int($0)}
            let incoming = BuildInfo.version.split(separator:".").compactMap {Int($0)}
            if incoming.lexicographicallyPrecedes(current) || (incoming == current && (incomingInfo.integer("CodexioWidgetBuild") ?? 0) < (installed.integer("CodexioWidgetBuild") ?? 0)) || binariesMatch(source,canonical) {
                _ = try execute("/usr/bin/open",["-a",canonical.path]); return true
            }
        } else if FileManager.default.fileExists(atPath:canonical.path) {
            let info = try plist(canonical.appendingPathComponent("Contents/Info.plist"))
            guard info.string("CFBundleIdentifier") == BuildInfo.bundleID else { throw AppFailure(L("应用程序目录中的同名 App 不属于 Codexio", "The existing app at this path is not Codexio")) }
        }
        guard let installationLease = FileLease(canonical.deletingLastPathComponent().appendingPathComponent(".Codexio-update.lock")) else { throw AppFailure(L("Codexio 正在安装更新", "A Codexio installation is already in progress")) }
        defer { withExtendedLifetime(installationLease) {} }
        let nonce = String(UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased().prefix(16))
        let pending = canonical.deletingLastPathComponent().appendingPathComponent(".Codexio-takeover-"+nonce+".pending")
        let backup = canonical.deletingLastPathComponent().appendingPathComponent(".Codexio-takeover-"+nonce+".previous")
        var moved = false, placed = false
        do {
            guard try execute("/usr/bin/ditto",["--norsrc","--noextattr",source.path,pending.path],timeout:120).code == 0 else { throw AppFailure(L("无法复制应用", "Cannot copy the app")) }
            _ = try verify(pending,version:BuildInfo.version)
            guard binariesMatch(source,pending) else { throw AppFailure(L("应用副本不完整", "The app copy is incomplete")) }
            try prepareReplacement(paths:paths)
            _ = try? execute("/usr/bin/pluginkit",["-r",canonical.appendingPathComponent(widgetRelative).path])
            if FileManager.default.fileExists(atPath:canonical.path) { try FileManager.default.moveItem(at:canonical,to:backup); moved = true }
            try FileManager.default.moveItem(at:pending,to:canonical); placed = true
            try launchChecked(canonical,paths:paths)
            if moved && !processPaths().contains(where:{$0.1.hasPrefix(backup.path+"/")}) { try? FileManager.default.removeItem(at:backup) }
            return true
        } catch {
            if placed { try? FileManager.default.removeItem(at:canonical) }
            if moved { try? FileManager.default.moveItem(at:backup,to:canonical); _ = try? execute("/usr/bin/pluginkit",["-a",canonical.appendingPathComponent(widgetRelative).path]); _ = try? execute("/usr/bin/open",["-a",canonical.path]) }
            try? FileManager.default.removeItem(at:pending)
            throw error
        }
    }
    static func registeredWidgets() -> Set<String> {
        guard let result = try? execute("/usr/bin/pluginkit",["-m","-v","-A","-D","-i",BuildInfo.widgetID]), let output = String(data:result.output,encoding:.utf8) else { return [] }
        let expression = try? NSRegularExpression(pattern:#"/[^\r\n]+\.appex"#)
        return Set((expression?.matches(in:output,range:NSRange(output.startIndex...,in:output)) ?? []).compactMap {Range($0.range,in:output).map {String(output[$0]).trimmingCharacters(in:.whitespaces)}})
    }
    static func registerWidget(paths: AppPaths) throws {
        guard !paths.mock, Bundle.main.bundleURL.standardizedFileURL == canonical else { return }
        let extensionPath = canonical.appendingPathComponent(widgetRelative).path
        let widget = try plist(canonical.appendingPathComponent(widgetRelative+"/Contents/Info.plist"))
        let stamp = installedIdentity()
        let replaced = readObject(paths.data.appendingPathComponent("widget_install_state.json")).string("identity") != stamp
        let previousPaths = registeredWidgets()
        guard try execute("/usr/bin/pluginkit",["-a",extensionPath]).code == 0, registeredWidgets().contains(extensionPath) else { throw AppFailure(L("小组件注册未完成，已保留原有注册", "Widget registration did not complete; existing registrations are retained")) }
        guard try execute("/usr/bin/pluginkit",["-e","use","-i",BuildInfo.widgetID]).code == 0 else { throw AppFailure(L("小组件注册未完成，已保留原有注册", "Widget registration did not complete; existing registrations are retained")) }
        for stale in registeredWidgets() where stale != extensionPath { _ = try? execute("/usr/bin/pluginkit",["-r",stale]) }
        let registered = try execute("/usr/bin/pluginkit",["-m","-v","-A","-D","-i",BuildInfo.widgetID])
        let listing = String(data:registered.output,encoding:.utf8) ?? ""
        let expected = BuildInfo.widgetID+"("+widget.string("CFBundleShortVersionString")+")"
        guard registeredWidgets() == Set([extensionPath]), listing.split(separator:"\n").contains(where:{$0.contains(expected) && $0.hasSuffix(extensionPath)}), widget.string("CFBundleVersion") == BuildInfo.widgetVersion else { throw AppFailure(L("小组件注册未完成，已保留原有注册", "Widget registration did not complete; existing registrations are retained")) }
        if replaced || previousPaths != Set([extensionPath]) {
            try retire(processPaths().filter {ownedWidget($0.1)})
            cleanupWidgetCaches(paths:paths)
        }
        try atomicJSON(["identity":stamp,"app_version":BuildInfo.version,"widget_version":widget.string("CFBundleVersion"),"extension":extensionPath],to:paths.data.appendingPathComponent("widget_install_state.json"))
    }
    private static func installedIdentity() -> String {
        identity(["Contents/MacOS/Codexio",widgetRelative+"/Contents/MacOS/CodexioWidget"].map { path -> Object in
            let stamp = FileStamp(canonical.appendingPathComponent(path))
            return ["path":path,"size":stamp.size,"device":stamp.device,"inode":stamp.inode,"modified":stamp.modified?.timeIntervalSince1970 ?? 0]
        })
    }
    private static func cleanupWidgetCaches(paths: AppPaths) {
        guard !paths.mock else { return }
        let library = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library")
        for name in ["Caches/"+BuildInfo.widgetID,"Containers/"+BuildInfo.widgetID+"/Data/Library/Caches"] {
            let file = library.appendingPathComponent(name)
            if (try? file.resourceValues(forKeys:[.isSymbolicLinkKey]))?.isSymbolicLink == false { try? FileManager.default.removeItem(at:file) }
        }
        for folder in ["AIQuotaWidget","AIQuota"] {
            let legacy = library.appendingPathComponent("Application Support/"+folder)
            if legacy.standardizedFileURL == paths.data.standardizedFileURL { continue }
            for name in ["widget_snapshot.json","widget_install_state.json","native-widget.lock"] { try? FileManager.default.removeItem(at:legacy.appendingPathComponent(name)) }
        }
    }
    static func cleanupConfirmedBackups(paths: AppPaths) {
        guard !paths.mock, Bundle.main.bundleURL.standardizedFileURL == canonical,
              ProcessInfo.processInfo.environment["CODEXIO_STARTUP_TOKEN"] == nil,
              let current = try? verify(canonical), let files = try? FileManager.default.contentsOfDirectory(at:canonical.deletingLastPathComponent(),includingPropertiesForKeys:[.isSymbolicLinkKey]) else { return }
        let active = processPaths()
        for file in files where file.lastPathComponent.range(of:#"^\.Codexio-(?:takeover-[a-f0-9]{16}|[a-f0-9]{32})\.previous$"#,options:.regularExpression) != nil {
            guard !active.contains(where:{$0.1.hasPrefix(file.path+"/")}), let old = try? verify(file),
                  !(current.string("CFBundleShortVersionString").split(separator:".").compactMap {Int($0)}).lexicographicallyPrecedes(old.string("CFBundleShortVersionString").split(separator:".").compactMap {Int($0)}) else { continue }
            _ = try? execute("/usr/bin/pluginkit",["-r",file.appendingPathComponent(widgetRelative).path])
            try? FileManager.default.removeItem(at:file)
        }
    }
    static func acknowledge(paths: AppPaths) {
        guard !paths.mock else { return }
        if let token = ProcessInfo.processInfo.environment["CODEXIO_STARTUP_TOKEN"], token.range(of:#"^[a-f0-9]{32}$"#,options:.regularExpression) != nil {
            try? atomicJSON(["pid":Int(getpid()),"version":BuildInfo.version],to:paths.data.appendingPathComponent("startup-"+token+".json"))
        }
        if let legacy = ProcessInfo.processInfo.environment["CODEXIO_UPDATE_JOB"] {
            let directory = URL(fileURLWithPath:legacy).standardizedFileURL
            let job = readObject(directory.appendingPathComponent("job.json"))
            if directory.deletingLastPathComponent() == paths.data.appendingPathComponent("updates"), directory.lastPathComponent.range(of:#"^[a-f0-9]{32}$"#,options:.regularExpression) != nil, job.string("version") == BuildInfo.version, job.string("target") == Bundle.main.bundleURL.path {
                try? atomicJSON(["pid":Int(getpid()),"version":BuildInfo.version],to:directory.appendingPathComponent("ack.json"))
            }
        }
    }
}
