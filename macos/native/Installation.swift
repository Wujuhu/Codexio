import Foundation
import AppKit
import Darwin

final class FileLease {
    private var descriptor: Int32 = -1
    init?(_ url: URL) {
        descriptor = open(url.path,O_CREAT | O_RDWR,0o600)
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
        return info
    }
    static func binariesMatch(_ first: URL,_ second: URL) -> Bool {
        ["Contents/MacOS/Codexio",widgetRelative+"/Contents/MacOS/CodexioWidget"].allSatisfy { path in
            guard let a = try? Data(contentsOf:first.appendingPathComponent(path),options:.mappedIfSafe), let b = try? Data(contentsOf:second.appendingPathComponent(path),options:.mappedIfSafe) else { return false }; return digest(a) == digest(b)
        }
    }
    static func processPaths() -> [(Int32,String)] {
        guard let result = try? execute("/bin/ps",["-ww","-axo","pid=,comm="]), let text = String(data:result.output,encoding:.utf8) else { return [] }
        return text.split(separator:"\n").compactMap { line in
            let pieces = line.trimmingCharacters(in:.whitespaces).split(separator:" ",maxSplits:1,omittingEmptySubsequences:true)
            guard pieces.count == 2, let pid = Int32(pieces[0]) else { return nil }; return (pid,String(pieces[1]).trimmingCharacters(in:.whitespaces))
        }
    }
    static func stopOldAgent() {
        _ = try? execute("/bin/launchctl",["bootout","gui/\(getuid())",agentURL.path],timeout:10)
        try? FileManager.default.removeItem(at:agentURL)
    }
    static func stopOldProcesses() {
        let candidates = processPaths().filter { pid,path in pid != getpid() && (path.hasPrefix(canonical.path+"/") && path.hasSuffix("/Codexio") || path.hasSuffix("/CodexioWidget.appex/Contents/MacOS/CodexioWidget")) }
        var retiring: [Int32] = []
        for (pid,_) in candidates {
            let args = (try? execute("/bin/ps",["-p",String(pid),"-o","args="]).output).flatMap {String(data:$0,encoding:.utf8)} ?? ""
            if ["--upstream-proxy","--upstream-helper","--apply-mac-update","--native-update-job"].contains(where:args.contains) { continue }
            kill(pid,SIGTERM); retiring.append(pid)
        }
        let deadline = Date().addingTimeInterval(4)
        while Date() < deadline && retiring.contains(where:{kill($0,0) == 0}) { Thread.sleep(forTimeInterval:0.05) }
        for pid in retiring where kill(pid,0) == 0 { kill(pid,SIGKILL) }
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
        _ = try verify(source)
        if source == canonical { return false }
        if let installed = try? verify(canonical) {
            let current = installed.string("CFBundleShortVersionString").split(separator:".").compactMap {Int($0)}
            let incoming = BuildInfo.version.split(separator:".").compactMap {Int($0)}
            if incoming.lexicographicallyPrecedes(current) || binariesMatch(source,canonical) {
                _ = try execute("/usr/bin/open",["-a",canonical.path]); return true
            }
        } else if FileManager.default.fileExists(atPath:canonical.path) {
            let info = try plist(canonical.appendingPathComponent("Contents/Info.plist"))
            guard info.string("CFBundleIdentifier") == BuildInfo.bundleID else { throw AppFailure(L("应用程序目录中的同名 App 不属于 Codexio", "The existing app at this path is not Codexio")) }
        }
        let nonce = String(UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased().prefix(16))
        let pending = canonical.deletingLastPathComponent().appendingPathComponent(".Codexio-takeover-"+nonce+".pending")
        let backup = canonical.deletingLastPathComponent().appendingPathComponent(".Codexio-takeover-"+nonce+".previous")
        var moved = false, placed = false
        do {
            guard try execute("/usr/bin/ditto",["--norsrc","--noextattr",source.path,pending.path],timeout:120).code == 0 else { throw AppFailure(L("无法复制应用", "Cannot copy the app")) }
            _ = try verify(pending,version:BuildInfo.version)
            guard binariesMatch(source,pending) else { throw AppFailure(L("应用副本不完整", "The app copy is incomplete")) }
            stopOldAgent(); stopOldProcesses()
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
        guard try execute("/usr/bin/pluginkit",["-a",extensionPath]).code == 0, registeredWidgets().contains(extensionPath) else { throw AppFailure(L("小组件注册未完成，已保留原有注册", "Widget registration did not complete; existing registrations are retained")) }
        guard try execute("/usr/bin/pluginkit",["-e","use","-i",BuildInfo.widgetID]).code == 0 else { throw AppFailure(L("小组件注册未完成，已保留原有注册", "Widget registration did not complete; existing registrations are retained")) }
        for stale in registeredWidgets() where stale != extensionPath { _ = try? execute("/usr/bin/pluginkit",["-r",stale]) }
        let widget = try plist(canonical.appendingPathComponent(widgetRelative+"/Contents/Info.plist"))
        guard registeredWidgets() == Set([extensionPath]), widget.string("CFBundleVersion") == BuildInfo.widgetVersion else { throw AppFailure(L("小组件注册未完成，已保留原有注册", "Widget registration did not complete; existing registrations are retained")) }
        try atomicJSON(["app_version":BuildInfo.version,"widget_version":widget.string("CFBundleVersion"),"extension":extensionPath],to:paths.data.appendingPathComponent("widget_install_state.json"))
    }
    static func ensureAgent(paths: AppPaths) throws {
        guard !paths.mock, Bundle.main.bundleURL.standardizedFileURL == canonical else { return }
        let executable = canonical.appendingPathComponent("Contents/MacOS/Codexio").path
        let agent: Object = ["Label":agentLabel,"ProgramArguments":[executable,"--widget-service"],"RunAtLoad":true,"KeepAlive":["SuccessfulExit":false],"ThrottleInterval":30,"ProcessType":"Background","LowPriorityIO":true,"Nice":10]
        let previous = try? plist(agentURL)
        if (previous?["ProgramArguments"] as? [String]) != [executable,"--widget-service"] {
            stopOldAgent(); try FileManager.default.createDirectory(at:agentURL.deletingLastPathComponent(),withIntermediateDirectories:true)
            try PropertyListSerialization.data(fromPropertyList:agent,format:.xml,options:0).write(to:agentURL,options:.atomic)
        }
        let domain = "gui/\(getuid())"
        if (try? execute("/bin/launchctl",["print",domain+"/"+agentLabel]).code) != 0 { _ = try execute("/bin/launchctl",["bootstrap",domain,agentURL.path]) }
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
