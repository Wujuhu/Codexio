import Foundation
import AppKit

final class NativeUpdater {
    private let state: AppState
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.updates",qos:.utility)
    private var checking = false
    private var jobDirectory: URL?
    private var release: Object = [:]
    private var timer: Timer?
    var installOnQuit = false
    init(state: AppState) {
        self.state = state
        if !state.paths.mock { timer = Timer.scheduledTimer(withTimeInterval:3600,repeats:true) { [weak self] _ in self?.check(manual:false) } }
    }
    func check(manual: Bool) {
        guard !state.paths.mock, !checking, jobDirectory == nil else { return }
        checking = true
        if manual { state.updateStatus = L("正在检查更新", "Checking for updates") }
        let autoDownload = state.preferences.analytics.flag("macos_auto_update",true)
        queue.async { [weak self] in
            guard let self else { return }
            do {
                let manifest = jsonObject(try HTTP.read(URL(string:"https://github.com/Wujuhu/Codexio/releases/latest/download/latest.json")!,maximum:1_048_576))
                let mac = manifest.object("macos"), version = mac.string("version")
                guard let targetVersion = Self.version(version), let current = Self.version(BuildInfo.version) else { throw AppFailure(L("更新清单无效", "Invalid update manifest")) }
                if !current.lexicographicallyPrecedes(targetVersion) { DispatchQueue.main.async { if manual { self.state.updateStatus = L("已是最新版本", "Up to date") }; self.checking = false }; return }
                #if arch(arm64)
                let architecture = "arm64"
                #else
                let architecture = "x86_64"
                #endif
                guard mac.string("architecture") == architecture, let size = mac.integer("size"), size > 1, size <= 536_870_912, mac.string("sha256").range(of:#"^[a-fA-F0-9]{64}$"#,options:.regularExpression) != nil, mac.string("url") == "https://github.com/Wujuhu/Codexio/releases/download/v\(version)/Codexio.app.zip" else { throw AppFailure(L("新版安装包信息与此 Mac 不匹配", "The update package does not match this Mac")) }
                if !manual && !autoDownload { DispatchQueue.main.async { self.state.updateStatus = L("有新版本", "Update available")+" "+version; self.checking = false }; return }
                DispatchQueue.main.async { self.state.updateStatus = L("正在下载", "Downloading")+" "+version }
                let directory = self.state.paths.data.appendingPathComponent("updates/"+UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased())
                try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
                let data = try HTTP.request(URLRequest(url:URL(string:mac.string("url"))!),maximum:size,timeout:120)
                guard data.count == size, digest(data) == mac.string("sha256").lowercased() else { throw AppFailure(L("更新文件大小或 SHA-256 不匹配", "Update size or SHA-256 does not match")) }
                try data.write(to:directory.appendingPathComponent("package.bin"),options:.atomic)
                let prepared = directory.appendingPathComponent("Codexio.pending")
                try Self.extract(directory.appendingPathComponent("package.bin"),to:prepared,version:version)
                self.release = mac; self.jobDirectory = directory
                DispatchQueue.main.async { self.state.updateAvailable = true; self.state.updateStatus = L("更新已就绪，正常退出后安装", "Update ready; installs after quitting normally"); self.checking = false }
            } catch {
                DispatchQueue.main.async { if manual { self.state.updateStatus = error.localizedDescription }; self.checking = false }
            }
        }
    }
    func prepareInstallerIfNeeded() throws {
        guard !state.paths.mock, let directory = jobDirectory, installOnQuit || state.preferences.analytics.flag("macos_auto_update",true) else { return }
        let target = Bundle.main.bundleURL.standardizedFileURL
        _ = try Installation.verify(target,version:BuildInfo.version)
        _ = try Installation.verify(directory.appendingPathComponent("Codexio.pending"),version:release.string("version"))
        let binary = directory.appendingPathComponent("CodexioUpdater")
        if !FileManager.default.fileExists(atPath:binary.path) { try FileManager.default.copyItem(at:Bundle.main.executableURL!,to:binary) }
        try FileManager.default.setAttributes([.posixPermissions:0o700],ofItemAtPath:binary.path)
        let job: Object = ["target":target.path,"version":release.string("version"),"sha256":release.string("sha256"),"old_sha256":digest(try Data(contentsOf:Bundle.main.executableURL!,options:.mappedIfSafe)),"parent_pid":Int(getpid()),"data_directory":state.paths.data.path]
        let jobURL = directory.appendingPathComponent("job.json")
        for name in ["installer-ready.json","commit.json"] { try? FileManager.default.removeItem(at:directory.appendingPathComponent(name)) }
        try atomicJSON(job,to:jobURL)
        let process = Process(); process.executableURL = binary; process.arguments = ["--native-update-job",jobURL.path]; process.standardInput = FileHandle.nullDevice; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        try process.run()
        let ready = directory.appendingPathComponent("installer-ready.json"), deadline = Date().addingTimeInterval(10)
        while process.isRunning && Date() < deadline {
            if readObject(ready).flag("ready") { try atomicJSON(["commit":true],to:directory.appendingPathComponent("commit.json")); return }
            Thread.sleep(forTimeInterval:0.05)
        }
        if process.isRunning { process.terminate() }
        throw AppFailure(L("更新程序未就绪，当前应用继续运行", "The installer is not ready; the current app will keep running"))
    }
    static func version(_ raw: String) -> [Int]? {
        guard raw.range(of:#"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"#,options:.regularExpression) != nil else { return nil }
        let parts = raw.split(separator:".").compactMap {Int($0)}
        return parts.count == 3 && parts.allSatisfy {$0 <= 65535} ? parts : nil
    }
    static func extract(_ archive: URL,to target: URL,version: String) throws {
        try ZipValidation.validate(archive)
        let folder = target.deletingLastPathComponent().appendingPathComponent("extract-"+UUID().uuidString)
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        defer { try? FileManager.default.removeItem(at:folder) }
        guard try execute("/usr/bin/ditto",["-x","-k",archive.path,folder.path],timeout:120).code == 0 else { throw AppFailure(L("更新包解压失败", "Could not extract the update")) }
        let app = folder.appendingPathComponent("Codexio.app")
        _ = try Installation.verify(app,version:version)
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        guard try execute("/usr/bin/lipo",["-verify_arch",architecture,app.appendingPathComponent("Contents/MacOS/Codexio").path]).code == 0 else { throw AppFailure(L("更新包芯片架构不匹配", "Update architecture does not match")) }
        guard !FileManager.default.fileExists(atPath:target.path) else { throw AppFailure(L("更新暂存已存在", "An update staging bundle already exists")) }
        try FileManager.default.moveItem(at:app,to:target)
    }
    static func installJob(_ url: URL) throws {
        let directory = url.deletingLastPathComponent(), job = readObject(url)
        let paths = try AppPaths(mock:false)
        guard directory.deletingLastPathComponent().standardizedFileURL == paths.data.appendingPathComponent("updates").standardizedFileURL, directory.resolvingSymlinksInPath() == directory.standardizedFileURL, directory.lastPathComponent.range(of:#"^[0-9a-f]{32}$"#,options:.regularExpression) != nil else { throw AppFailure("Invalid update directory") }
        let target = URL(fileURLWithPath:job.string("target")).standardizedFileURL
        guard target.pathExtension == "app", let pid = job.integer("parent_pid"), pid > 0, pid != getpid(), version(job.string("version")) != nil else { throw AppFailure("Invalid update job") }
        let expectedProcess = Installation.processPaths().first {$0.0 == Int32(pid)}?.1
        guard expectedProcess == target.appendingPathComponent("Contents/MacOS/Codexio").path else { throw AppFailure("Update parent does not own this app") }
        guard let lease = FileLease(target.deletingLastPathComponent().appendingPathComponent(".Codexio-update.lock")) else { throw AppFailure("Another update is in progress") }
        try withExtendedLifetime(lease) {
            _ = try Installation.verify(target)
            let archive = directory.appendingPathComponent("package.bin"), prepared = directory.appendingPathComponent("Codexio.pending")
            guard digest(try Data(contentsOf:archive,options:.mappedIfSafe)) == job.string("sha256").lowercased(), digest(try Data(contentsOf:target.appendingPathComponent("Contents/MacOS/Codexio"),options:.mappedIfSafe)) == job.string("old_sha256") else { throw AppFailure("Update verification failed") }
            _ = try Installation.verify(prepared,version:job.string("version"))
            try atomicJSON(["ready":true],to:directory.appendingPathComponent("installer-ready.json"))
            let deadline = Date().addingTimeInterval(90)
            while kill(Int32(pid),0) == 0 && Date() < deadline { Thread.sleep(forTimeInterval:0.1) }
            guard kill(Int32(pid),0) != 0, readObject(directory.appendingPathComponent("commit.json")).flag("commit") else { throw AppFailure("The app has not confirmed a normal exit") }
            guard digest(try Data(contentsOf:target.appendingPathComponent("Contents/MacOS/Codexio"),options:.mappedIfSafe)) == job.string("old_sha256") else { throw AppFailure("The installed app changed during update") }
            let pending = target.deletingLastPathComponent().appendingPathComponent(".Codexio-"+directory.lastPathComponent+".pending")
            let backup = target.deletingLastPathComponent().appendingPathComponent(".Codexio-"+directory.lastPathComponent+".previous")
            guard !FileManager.default.fileExists(atPath:pending.path), !FileManager.default.fileExists(atPath:backup.path) else { throw AppFailure("Update backup already exists") }
            guard try execute("/usr/bin/ditto",["--norsrc","--noextattr",prepared.path,pending.path],timeout:120).code == 0 else { throw AppFailure("Cannot stage the replacement") }
            _ = try Installation.verify(pending,version:job.string("version"))
            Installation.stopOldAgent()
            _ = try? execute("/usr/bin/pluginkit",["-r",target.appendingPathComponent(Installation.widgetRelative).path])
            try FileManager.default.moveItem(at:target,to:backup)
            do {
                try FileManager.default.moveItem(at:pending,to:target)
                try Installation.launchChecked(target,paths:paths,timeout:40)
                if !Installation.processPaths().contains(where:{$0.1.hasPrefix(backup.path+"/")}) { try? FileManager.default.removeItem(at:backup) }
                try atomicJSON(["state":"done","version":job.string("version")],to:directory.appendingPathComponent("state.json"))
                try? FileManager.default.removeItem(at:prepared); try? FileManager.default.removeItem(at:archive)
            } catch {
                if !Installation.processPaths().contains(where:{$0.1 == target.appendingPathComponent("Contents/MacOS/Codexio").path}) {
                    try? FileManager.default.removeItem(at:target); try FileManager.default.moveItem(at:backup,to:target)
                    _ = try? execute("/usr/bin/pluginkit",["-a",target.appendingPathComponent(Installation.widgetRelative).path]); _ = try? execute("/usr/bin/open",["-a",target.path])
                    try atomicJSON(["state":"rolled_back","message":error.localizedDescription],to:directory.appendingPathComponent("state.json"))
                } else { try atomicJSON(["state":"unconfirmed","backup":backup.path],to:directory.appendingPathComponent("state.json")) }
                throw error
            }
        }
    }
}
