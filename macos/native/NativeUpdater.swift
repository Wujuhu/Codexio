import Foundation
import AppKit

struct NativeUpdateRelease {
    let version: String
    let size: Int
    let sha256: String
    let url: URL
    let notes: String
    var manifest: Object { ["version":version,"size":size,"sha256":sha256,"url":url.absoluteString,"architecture":Self.architecture,"notes":notes] }
    static var architecture: String {
        #if arch(arm64)
        return "arm64"
        #else
        return "x86_64"
        #endif
    }
    init(_ value: Object, notes: String = "") throws {
        let version = value.string("version")
        guard NativeUpdater.version(version) != nil, value.string("architecture") == Self.architecture,
              let size = value.integer("size"), size > 1, size <= 536_870_912,
              value.string("sha256").range(of:#"^[a-fA-F0-9]{64}$"#,options:.regularExpression) != nil,
              value.string("url") == NativeUpdater.downloadURL(version).absoluteString else {
            throw AppFailure(L("新版安装包信息与此 Mac 不匹配", "The update package does not match this Mac"))
        }
        self.version = version; self.size = size; sha256 = value.string("sha256").lowercased()
        url = NativeUpdater.downloadURL(version); self.notes = String(notes.trimmingCharacters(in:.whitespacesAndNewlines).prefix(60_000))
    }
}

private final class UpdateCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var cancelled: Bool { lock.lock(); defer { lock.unlock() }; return value }
    func cancel() { lock.lock(); value = true; lock.unlock() }
    func check() throws { if cancelled { throw URLError(.cancelled) } }
}

// URLSession owns the temporary file; only a complete, bounded response enters our job.
private final class NativeUpdateDownload: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let release: NativeUpdateRelease
    private let destination: URL
    private let progress: (Int64) -> Void
    private let completion: (Result<URL,Error>) -> Void
    private let taskLock = NSLock()
    private var session: URLSession?
    private var task: URLSessionDownloadTask?
    private var failure: Error?
    private var downloaded = false
    private var lastProgress = Date.distantPast
    init(release: NativeUpdateRelease, destination: URL, progress: @escaping (Int64) -> Void, completion: @escaping (Result<URL,Error>) -> Void) {
        self.release = release; self.destination = destination; self.progress = progress; self.completion = completion
    }
    func start() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60; configuration.timeoutIntervalForResource = 600
        let callbacks = OperationQueue(); callbacks.maxConcurrentOperationCount = 1; callbacks.qualityOfService = .utility
        let session = URLSession(configuration:configuration,delegate:self,delegateQueue:callbacks)
        self.session = session
        var request = URLRequest(url:release.url); request.setValue("Codexio/"+BuildInfo.version,forHTTPHeaderField:"User-Agent")
        let task = session.downloadTask(with:request)
        taskLock.lock(); self.task = task; taskLock.unlock(); task.resume()
    }
    func cancel() { taskLock.lock(); let current = task; taskLock.unlock(); current?.cancel() }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard totalBytesWritten <= Int64(release.size), totalBytesExpectedToWrite <= Int64(release.size) else {
            failure = AppFailure(L("更新文件超过清单中的大小", "The update exceeds the size in its manifest")); downloadTask.cancel(); return
        }
        if Date().timeIntervalSince(lastProgress) >= 0.1 || totalBytesWritten == Int64(release.size) {
            lastProgress = Date(); progress(totalBytesWritten)
        }
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        do {
            guard let response = downloadTask.response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
                throw AppFailure(L("下载更新失败", "Could not download the update")+" (\((downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0))")
            }
            guard (try location.resourceValues(forKeys:[.fileSizeKey])).fileSize == release.size else {
                throw AppFailure(L("更新文件大小不匹配", "The update size does not match"))
            }
            try FileManager.default.moveItem(at:location,to:destination)
            try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:destination.path)
            downloaded = true
        } catch { failure = error }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = failure ?? error { completion(.failure(error)) }
        else if downloaded { progress(Int64(release.size)); completion(.success(destination)) }
        else { completion(.failure(AppFailure(L("下载没有生成更新文件", "The download did not produce an update file")))) }
        session.finishTasksAndInvalidate(); self.session = nil
        taskLock.lock(); self.task = nil; taskLock.unlock()
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(request.url?.scheme == "https" ? request : nil)
    }
}

final class NativeUpdater {
    private let state: AppState
    private let queue = DispatchQueue(label:"com.wujuhu.codexio.updates",qos:.utility)
    private let reminderQueue = DispatchQueue(label:"com.wujuhu.codexio.update-reminders",qos:.utility)
    private var checking = false
    private var timer: Timer?
    private var release: NativeUpdateRelease?
    private var jobDirectory: URL?
    private var download: NativeUpdateDownload?
    private var operation: UpdateCancellation?
    private var installerAttempt: UpdateCancellation?
    private var model: NativeUpdatePresentation?
    private var sheet: NSWindow?
    private var deferrals: Object = [:]
    private var explicitPresentation = false
    private var terminating = false
    private var recoveredNotice: URL?
    private(set) var installOnQuit = false
    private(set) var isResolvingStartup = true
    var isPresenting: Bool { sheet != nil }
    var onPresentationNeeded: (() -> Void)?
    var onPresentationFinished: (() -> Void)?
    var onInstallRequested: (() -> Void)?

    init(state: AppState) {
        self.state = state
        guard !state.paths.mock else { isResolvingStartup = false; return }
        timer = Timer.scheduledTimer(withTimeInterval:3600,repeats:true) { [weak self] _ in self?.check(manual:false) }
        timer?.tolerance = 60
        recoverPreviousJobs(initial:true)
    }
    deinit { timer?.invalidate(); download?.cancel() }

    // The application coordinates this sheet with reports. This method never activates the app.
    @discardableResult func presentIfNeeded(on window: NSWindow) -> Bool {
        guard !state.paths.mock, !terminating else { return false }
        if sheet != nil { return true }
        guard !isResolvingStartup, window.isVisible, !window.isMiniaturized, NSApp.isActive,
              window.attachedSheet == nil, let model,
              explicitPresentation || model.phase.isBusy || !isDeferred(model.version) else { return false }
        explicitPresentation = false
        let sheet = NativeUpdateSheet.make(model:model,update:{ [weak self] in self?.startUpdate() },later:{ [weak self] in self?.deferUpdate() },cancel:{ [weak self] in self?.cancelDownload(deferReminder:true) })
        self.sheet = sheet
        window.beginSheet(sheet) { [weak self] _ in
            self?.sheet = nil
            self?.onPresentationFinished?()
        }
        return true
    }

    func requestUpdate() {
        guard !state.paths.mock, !terminating else { return }
        explicitPresentation = true
        if sheet != nil || model?.phase.isBusy == true || jobDirectory != nil { onPresentationNeeded?(); return }
        if let release { acknowledgeReplacedFailure(); model = NativeUpdatePresentation(version:release.version,notes:release.notes,size:release.size); onPresentationNeeded?() }
        else { check(manual:true) }
    }

    func check(manual: Bool) {
        guard !state.paths.mock, !terminating else { return }
        if manual { explicitPresentation = true }
        guard !checking, operation == nil, jobDirectory == nil, sheet == nil else { if manual { onPresentationNeeded?() }; return }
        guard manual || state.preferences.analytics.flag("macos_auto_update",true) else { finishStartup(); onPresentationNeeded?(); return }
        checking = true
        if manual { state.updateStatus = L("正在检查更新", "Checking for updates") }
        queue.async { [weak self] in
            guard let self else { return }
            var candidateVersion: String?
            do {
                let manifest = jsonObject(try HTTP.read(URL(string:"https://github.com/Wujuhu/Codexio/releases/latest/download/latest.json")!,maximum:1_048_576))
                let mac = manifest.object("macos"), rawVersion = mac.string("version")
                guard let target = Self.version(rawVersion), let current = Self.version(BuildInfo.version) else { throw AppFailure(L("更新清单无效", "Invalid update manifest")) }
                let available: NativeUpdateRelease?
                if current.lexicographicallyPrecedes(target) {
                    candidateVersion = rawVersion
                    _ = try NativeUpdateRelease(mac)
                    available = try NativeUpdateRelease(mac,notes:Self.releaseNotes(rawVersion))
                } else { available = nil }
                DispatchQueue.main.async {
                    self.checking = false
                    if !self.terminating && self.operation == nil && self.jobDirectory == nil && self.sheet == nil {
                        self.release = available; self.state.updateAvailable = available != nil
                        if let available {
                            if self.model?.phase.isFailure != true || manual {
                                if manual { self.acknowledgeReplacedFailure() }
                                self.model = NativeUpdatePresentation(version:available.version,notes:available.notes,size:available.size)
                            }
                            self.state.updateStatus = L("有新版本", "Update available")+" "+available.version
                        } else {
                            if self.model?.phase.isFailure != true { self.model = nil }
                            self.explicitPresentation = false
                            if manual { self.state.updateStatus = L("已是最新版本", "Up to date") }
                        }
                    }
                    self.finishStartup(); self.onPresentationNeeded?()
                }
            } catch {
                let failedVersion = candidateVersion
                DispatchQueue.main.async {
                    self.checking = false
                    if let failedVersion, !self.terminating, self.operation == nil, self.jobDirectory == nil, self.sheet == nil {
                        self.showFailure(error.localizedDescription,version:failedVersion,directory:nil)
                    } else if manual { self.state.updateStatus = error.localizedDescription }
                    self.finishStartup(); self.onPresentationNeeded?()
                }
            }
        }
    }

    private func finishStartup() { isResolvingStartup = false }
    private static func releaseNotes(_ version: String) -> String {
        var request = URLRequest(url:URL(string:"https://api.github.com/repos/Wujuhu/Codexio/releases/tags/v\(version)")!)
        request.setValue("application/vnd.github+json",forHTTPHeaderField:"Accept")
        request.setValue("Codexio/"+BuildInfo.version,forHTTPHeaderField:"User-Agent")
        guard let data = try? HTTP.request(request,maximum:1_048_576,timeout:10) else { return "" }
        let value = jsonObject(data)
        return value.string("tag_name") == "v"+version && !value.flag("draft") ? value.string("body") : ""
    }
    private func isDeferred(_ version: String) -> Bool {
        let entry = deferrals.object(version), now = Date().timeIntervalSince1970
        return (entry.number("deferred_at") ?? 0) <= now && (entry.number("until") ?? 0) > now
    }
    private func deferUpdate() {
        guard let model else { closeSheet(); return }
        let now = Date().timeIntervalSince1970
        deferrals[model.version] = ["deferred_at":now,"until":now+86_400]
        let recent = deferrals.keys.filter {(deferrals.object($0).number("until") ?? 0) > now}.sorted {(deferrals.object($0).number("until") ?? 0) > (deferrals.object($1).number("until") ?? 0)}.prefix(32)
        deferrals = Dictionary(uniqueKeysWithValues:recent.map {($0,deferrals[$0]!)})
        let saved = deferrals, notice = model.phase.isFailure ? recoveredNotice : nil, data = state.paths.data
        reminderQueue.async { try? atomicJSON(saved,to:data.appendingPathComponent("updates/reminders.json")) }
        if let notice { queue.async { Self.acknowledgeNotice(notice,paths:data) } }
        recoveredNotice = nil; explicitPresentation = false
        if model.phase.isFailure { self.model = release.map {NativeUpdatePresentation(version:$0.version,notes:$0.notes,size:$0.size)} }
        closeSheet()
    }
    private func closeSheet() {
        if let sheet, let parent = sheet.sheetParent { parent.endSheet(sheet) }
        else { sheet = nil; onPresentationFinished?() }
    }
    private func acknowledgeReplacedFailure() {
        guard model?.phase.isFailure == true, let notice = recoveredNotice else { return }
        recoveredNotice = nil
        let data = state.paths.data
        queue.async { Self.acknowledgeNotice(notice,paths:data) }
    }

    private func startUpdate() {
        guard !state.paths.mock, !terminating, operation == nil, let release else { return }
        if jobDirectory != nil { requestInstallation(); return }
        let directory = state.paths.data.appendingPathComponent("updates/"+UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased())
        let operation = UpdateCancellation()
        self.operation = operation; jobDirectory = directory; recoveredNotice = directory
        model?.phase = .downloading; model?.received = 0
        state.updateStatus = L("正在下载", "Downloading")+" "+release.version
        let download = NativeUpdateDownload(release:release,destination:directory.appendingPathComponent("package.bin"),progress:{ [weak self] count in
            DispatchQueue.main.async {
                guard let self, self.operation === operation, !operation.cancelled else { return }
                self.model?.received = count
            }
        },completion:{ [weak self] result in
            self?.queue.async { [weak self] in self?.finishDownload(result,release:release,directory:directory,operation:operation) }
        })
        self.download = download
        queue.async { [weak self] in
            do {
                try operation.check()
                try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
                try atomicJSON(release.manifest,to:directory.appendingPathComponent("release.json"))
                try Self.writeState(directory,"downloading",version:release.version)
                try operation.check(); download.start()
                if operation.cancelled { download.cancel() }
            } catch { self?.finishDownload(.failure(error),release:release,directory:directory,operation:operation) }
        }
    }
    private func finishDownload(_ result: Result<URL,Error>, release: NativeUpdateRelease, directory: URL, operation: UpdateCancellation) {
        do {
            try operation.check()
            let archive = try result.get()
            DispatchQueue.main.async {
                if self.operation === operation && !operation.cancelled { self.model?.phase = .validating; self.state.updateStatus = L("正在校验更新", "Verifying the update") }
            }
            try Self.writeState(directory,"validating",version:release.version)
            guard digest(try Data(contentsOf:archive,options:.mappedIfSafe)) == release.sha256 else { throw AppFailure(L("更新文件 SHA-256 不匹配", "The update SHA-256 does not match")) }
            try operation.check()
            try Self.extract(archive,to:directory.appendingPathComponent("Codexio.pending"),version:release.version)
            try operation.check(); try Self.writeState(directory,"prepared",version:release.version)
            DispatchQueue.main.async {
                guard self.operation === operation else { return }
                self.operation = nil; self.download = nil
                if operation.cancelled || self.terminating {
                    self.jobDirectory = nil; self.model?.phase = .offer
                    self.queue.async { try? Self.writeState(directory,"cancelled",version:release.version); Self.cleanupPayload(directory) }
                } else { self.requestInstallation() }
            }
        } catch {
            let cancelled = operation.cancelled || (error as? URLError)?.code == .cancelled
            try? Self.writeState(directory,cancelled ? "cancelled" : "failed",version:release.version,message:cancelled ? "" : error.localizedDescription)
            Self.cleanupPayload(directory)
            DispatchQueue.main.async {
                guard self.operation === operation else { return }
                self.operation = nil; self.download = nil; self.jobDirectory = nil
                if cancelled { self.model?.phase = .offer; self.state.updateStatus = L("更新下载已取消", "Update download cancelled") }
                if !cancelled && !self.terminating { self.showFailure(error.localizedDescription,version:release.version,directory:directory) }
            }
        }
    }
    private func cancelDownload(deferReminder: Bool) {
        operation?.cancel(); download?.cancel()
        model?.phase = .offer
        state.updateStatus = L("更新下载已取消", "Update download cancelled")
        if deferReminder { deferUpdate() }
    }
    private func requestInstallation() {
        guard !terminating, jobDirectory != nil else { return }
        installOnQuit = true; model?.phase = .installing
        state.updateStatus = L("正在准备安装更新", "Preparing to install the update")
        onInstallRequested?()
    }

    // Called before the existing upstream/normal-exit sequence, including ordinary Command-Q.
    func beginTermination() {
        terminating = true
        if !installOnQuit {
            operation?.cancel(); download?.cancel()
            if let directory = jobDirectory, operation == nil, let release {
                jobDirectory = nil; model?.phase = .offer
                queue.async { try? Self.writeState(directory,"cancelled",version:release.version); Self.cleanupPayload(directory) }
            }
        }
    }
    func cancelTermination() {
        let hadInstaller = installerAttempt != nil
        terminating = false; installOnQuit = false; installerAttempt?.cancel()
        if operation?.cancelled == true { model?.phase = .offer }
        if let directory = jobDirectory, operation == nil {
            queue.async { try? atomicJSON(["cancel":true],to:directory.appendingPathComponent("cancel.json")); try? FileManager.default.removeItem(at:directory.appendingPathComponent("commit.json")) }
            if hadInstaller {
                jobDirectory = nil; model?.phase = .offer
                let version = release?.version ?? ""
                queue.async { try? Self.writeState(directory,"cancelled",version:version); Self.cleanupPayload(directory) }
            } else {
                model?.phase = .ready
                state.updateStatus = L("更新已准备，可稍后继续安装", "The update is ready to install later")
            }
        }
        onPresentationNeeded?()
    }

    func prepareInstallerIfNeeded(completion: @escaping (Error?) -> Void) {
        guard !state.paths.mock else { completion(nil); return }
        guard installOnQuit, let directory = jobDirectory, let release else {
            reminderQueue.async { DispatchQueue.main.async { completion(nil) } }; return
        }
        let attempt = UpdateCancellation(), data = state.paths.data
        installerAttempt = attempt
        queue.async {
            do {
                try Self.prepareInstaller(directory:directory,release:release,data:data,attempt:attempt)
                DispatchQueue.main.async {
                    self.installerAttempt = nil
                    completion(attempt.cancelled || !self.terminating || !self.installOnQuit ? URLError(.cancelled) : nil)
                }
            } catch {
                let cancelled = attempt.cancelled
                if !cancelled {
                    let previous = readObject(directory.appendingPathComponent("state.json"))
                    if !["rolled_back","unconfirmed","failed"].contains(previous.string("state")) { try? Self.writeState(directory,"failed",version:release.version,message:error.localizedDescription) }
                    Self.cleanupPayload(directory)
                }
                DispatchQueue.main.async {
                    self.installerAttempt = nil; self.installOnQuit = false; self.terminating = false
                    if !cancelled { self.jobDirectory = nil; self.showFailure(error.localizedDescription,version:release.version,directory:directory) }
                    completion(error)
                }
            }
        }
    }
    private static func prepareInstaller(directory: URL, release: NativeUpdateRelease, data: URL, attempt: UpdateCancellation) throws {
        try attempt.check()
        let target = Bundle.main.bundleURL.standardizedFileURL
        _ = try Installation.verify(target,version:BuildInfo.version)
        _ = try Installation.verify(directory.appendingPathComponent("Codexio.pending"),version:release.version)
        guard let executable = Bundle.main.executableURL else { throw AppFailure(L("找不到当前应用程序", "The current app executable is missing")) }
        let binary = directory.appendingPathComponent("CodexioUpdater")
        if !FileManager.default.fileExists(atPath:binary.path) { try FileManager.default.copyItem(at:executable,to:binary) }
        try FileManager.default.setAttributes([.posixPermissions:0o700],ofItemAtPath:binary.path)
        let job: Object = ["target":target.path,"version":release.version,"sha256":release.sha256,"size":release.size,"old_sha256":digest(try Data(contentsOf:executable,options:.mappedIfSafe)),"parent_pid":Int(getpid()),"data_directory":data.path]
        for name in ["installer-ready.json","commit.json","cancel.json"] { try? FileManager.default.removeItem(at:directory.appendingPathComponent(name)) }
        try atomicJSON(job,to:directory.appendingPathComponent("job.json"))
        try writeState(directory,"preparing",version:release.version)
        try attempt.check()
        let process = Process(); process.executableURL = binary; process.arguments = ["--native-update-job",directory.appendingPathComponent("job.json").path]
        process.standardInput = FileHandle.nullDevice; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        try process.run()
        do {
            let deadline = Date().addingTimeInterval(15)
            while process.isRunning && Date() < deadline {
                try attempt.check()
                if readObject(directory.appendingPathComponent("installer-ready.json")).flag("ready") {
                    try atomicJSON(["commit":true],to:directory.appendingPathComponent("commit.json")); return
                }
                Thread.sleep(forTimeInterval:0.05)
            }
            throw AppFailure(readObject(directory.appendingPathComponent("state.json")).string("message",L("更新程序未就绪，当前应用继续运行", "The installer is not ready; the current app will keep running")))
        } catch {
            try? atomicJSON(["cancel":true],to:directory.appendingPathComponent("cancel.json"))
            if process.isRunning { process.terminate(); process.waitUntilExit() }
            throw error
        }
    }
    private func showFailure(_ message: String, version: String, directory: URL?) {
        recoveredNotice = directory
        let next = model?.version == version ? model! : NativeUpdatePresentation(version:version,notes:"",size:0)
        next.phase = .failed(message); model = next
        state.updateStatus = message
        onPresentationNeeded?()
    }

    private struct Recovery {
        var deferrals: Object = [:]
        var notice: (URL,String,String)?
        var completedVersion: String?
        var active = false
    }
    private func recoverPreviousJobs(initial: Bool, retry: Int = 0) {
        let data = state.paths.data, activeDirectory = jobDirectory
        queue.async { [weak self] in
            guard let self else { return }
            let recovered = Self.recover(data:data,excluding:activeDirectory)
            DispatchQueue.main.async { [self] in
                if initial { self.deferrals = recovered.deferrals }
                if let (directory,version,message) = recovered.notice, self.operation == nil, self.jobDirectory == nil, self.recoveredNotice == nil, self.sheet == nil {
                    self.showFailure(message,version:version,directory:directory)
                } else if let version = recovered.completedVersion, self.model == nil {
                    self.state.updateStatus = L("已更新至", "Updated to")+" "+version
                }
                if initial {
                    if ProcessInfo.processInfo.environment["CODEXIO_SKIP_UPDATE_ONCE"] == nil { self.check(manual:false) }
                    else { self.finishStartup(); self.onPresentationNeeded?() }
                }
                if recovered.active && retry < 3 {
                    DispatchQueue.main.asyncAfter(deadline:.now()+Double(2 << retry)) { [weak self] in self?.recoverPreviousJobs(initial:false,retry:retry+1) }
                }
            }
        }
    }
    private static func recover(data: URL, excluding activeDirectory: URL?) -> Recovery {
        var result = Recovery()
        let root = data.appendingPathComponent("updates")
        let saved = readObject(root.appendingPathComponent("reminders.json")), now = Date().timeIntervalSince1970
        for key in saved.keys.sorted().suffix(32) where version(key) != nil {
            let entry = saved.object(key)
            if (entry.number("until") ?? 0) > now, (entry.number("deferred_at") ?? 0) <= now { result.deferrals[key] = entry }
        }
        guard let contents = try? FileManager.default.contentsOfDirectory(at:root,includingPropertiesForKeys:[.contentModificationDateKey,.isDirectoryKey,.isSymbolicLinkKey]) else { return result }
        let processes = Installation.processPaths()
        let jobs = contents.filter { ownedDirectory($0,data:data) }.sorted {
            ((try? $0.resourceValues(forKeys:[.contentModificationDateKey]).contentModificationDate) ?? .distantPast) > ((try? $1.resourceValues(forKeys:[.contentModificationDateKey]).contentModificationDate) ?? .distantPast)
        }
        for directory in jobs {
            if directory == activeDirectory { continue }
            if processes.contains(where:{$0.1.hasPrefix(directory.path+"/")}) { result.active = true; continue }
            let job = readObject(directory.appendingPathComponent("job.json")), release = readObject(directory.appendingPathComponent("release.json"))
            var saved = readObject(directory.appendingPathComponent("state.json"))
            let rawVersion = release.string("version",job.string("version",saved.string("version")))
            guard version(rawVersion) != nil else { continue }
            let phase = saved.string("state")
            if !["done","cancelled","failed","rolled_back","unconfirmed"].contains(phase) {
                let message = L("上次更新未完成，可重新下载该版本。当前应用仍可继续使用。", "The previous update was interrupted. You can download that version again and keep using this app.")
                try? writeState(directory,"failed",version:rawVersion,message:message)
                saved = readObject(directory.appendingPathComponent("state.json"))
            }
            cleanupPayload(directory)
            cleanupReplacementArtifacts(directory,job:job,saved:saved,processes:processes)
            if phase == "done" { result.completedVersion = rawVersion }
            if phase == "done" || phase == "cancelled" || saved.flag("notified") {
                removeFinishedJob(directory,data:data,processes:processes)
            } else if result.notice == nil {
                var message = saved.string("message",L("上次更新未能完成", "The previous update did not finish"))
                if phase == "rolled_back" { message += "\n"+L("已恢复旧版。", "The previous version was restored.") }
                if let backup = replacementURL(directory,job:job,suffix:"previous"), FileManager.default.fileExists(atPath:backup.path) {
                    message += "\n"+L("旧版备份仍保留，可用于恢复。", "The previous app backup is retained for recovery.")
                }
                result.notice = (directory,rawVersion,message)
            }
        }
        return result
    }
    private static func acknowledgeNotice(_ directory: URL, paths: URL) {
        guard ownedDirectory(directory,data:paths) else { return }
        var saved = readObject(directory.appendingPathComponent("state.json")); saved["notified"] = true
        try? atomicJSON(saved,to:directory.appendingPathComponent("state.json"))
        removeFinishedJob(directory, data:paths,processes:Installation.processPaths())
    }
    private static func removeFinishedJob(_ directory: URL, data: URL, processes: [(Int32,String)]) {
        guard ownedDirectory(directory,data:data), !processes.contains(where:{$0.1.hasPrefix(directory.path+"/")}) else { return }
        guard ["done","cancelled","failed","rolled_back","unconfirmed"].contains(readObject(directory.appendingPathComponent("state.json")).string("state")) else { return }
        let job = readObject(directory.appendingPathComponent("job.json"))
        if let backup = replacementURL(directory,job:job,suffix:"previous"), FileManager.default.fileExists(atPath:backup.path) { return }
        try? FileManager.default.removeItem(at:directory)
    }
    private static func ownedDirectory(_ directory: URL, data: URL) -> Bool {
        directory.deletingLastPathComponent().standardizedFileURL == data.appendingPathComponent("updates").standardizedFileURL &&
        directory.resolvingSymlinksInPath() == directory.standardizedFileURL &&
        directory.lastPathComponent.range(of:#"^[0-9a-f]{32}$"#,options:.regularExpression) != nil &&
        (try? directory.resourceValues(forKeys:[.isDirectoryKey]).isDirectory) == true
    }
    private static func replacementURL(_ directory: URL, job: Object, suffix: String) -> URL? {
        let path = job.string("target"), target = URL(fileURLWithPath:path).standardizedFileURL
        guard path.hasPrefix("/"), target == Installation.canonical else { return nil }
        return target.deletingLastPathComponent().appendingPathComponent(".Codexio-"+directory.lastPathComponent+"."+suffix)
    }
    private static func cleanupPayload(_ directory: URL) {
        // Rollback copies are intentionally absent: only a successful launch may retire one.
        for name in ["package.bin","Codexio.pending"] { try? FileManager.default.removeItem(at:directory.appendingPathComponent(name)) }
        if let contents = try? FileManager.default.contentsOfDirectory(at:directory,includingPropertiesForKeys:nil) {
            for item in contents where item.lastPathComponent.hasPrefix("extract-") { try? FileManager.default.removeItem(at:item) }
        }
    }
    private static func cleanupReplacementArtifacts(_ directory: URL, job: Object, saved: Object, processes: [(Int32,String)]) {
        guard let pending = replacementURL(directory,job:job,suffix:"pending"),
              let backup = replacementURL(directory,job:job,suffix:"previous"),
              let lease = FileLease(Installation.canonical.deletingLastPathComponent().appendingPathComponent(".Codexio-update.lock")) else { return }
        withExtendedLifetime(lease) {
            if !processes.contains(where:{$0.1.hasPrefix(pending.path+"/")}) { try? FileManager.default.removeItem(at:pending) }
            guard saved.string("state") == "done", !saved.string("confirmed_sha256").isEmpty,
                  !processes.contains(where:{$0.1.hasPrefix(backup.path+"/")}),
                  (try? Installation.verify(Installation.canonical,version:job.string("version"))) != nil,
                  let executable = try? Data(contentsOf:Installation.canonical.appendingPathComponent("Contents/MacOS/Codexio"),options:.mappedIfSafe),
                  digest(executable) == saved.string("confirmed_sha256") else { return }
            try? FileManager.default.removeItem(at:backup)
        }
    }
    private static func writeState(_ directory: URL, _ state: String, version: String, message: String = "", backup: URL? = nil, confirmedHash: String? = nil) throws {
        var value: Object = ["state":state,"version":version,"message":String(message.prefix(4096)),"updated_at":Date().timeIntervalSince1970]
        if let backup { value["backup"] = backup.path }
        if let confirmedHash { value["confirmed_sha256"] = confirmedHash }
        try atomicJSON(value,to:directory.appendingPathComponent("state.json"))
    }
    static func downloadURL(_ version: String) -> URL { URL(string:"https://github.com/Wujuhu/Codexio/releases/download/v\(version)/Codexio.app.zip")! }
    static func releaseURL(_ version: String) -> URL { URL(string:"https://github.com/Wujuhu/Codexio/releases/tag/v\(version)")! }
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
        guard try execute("/usr/bin/lipo",["-verify_arch",NativeUpdateRelease.architecture,app.appendingPathComponent("Contents/MacOS/Codexio").path]).code == 0 else { throw AppFailure(L("更新包芯片架构不匹配", "Update architecture does not match")) }
        guard !FileManager.default.fileExists(atPath:target.path) else { throw AppFailure(L("更新暂存已存在", "An update staging bundle already exists")) }
        try FileManager.default.moveItem(at:app,to:target)
    }
    static func installJob(_ url: URL) throws {
        let directory = url.deletingLastPathComponent(), paths = try AppPaths(mock:false)
        guard url.lastPathComponent == "job.json", ownedDirectory(directory,data:paths.data) else { throw AppFailure("Invalid update directory") }
        let job = readObject(url), rawVersion = job.string("version")
        do { try performInstall(directory:directory,job:job,paths:paths) }
        catch {
            let previous = readObject(directory.appendingPathComponent("state.json"))
            if !["rolled_back","unconfirmed"].contains(previous.string("state")) {
                let cancelled = readObject(directory.appendingPathComponent("cancel.json")).flag("cancel")
                try? writeState(directory,cancelled ? "cancelled" : "failed",version:rawVersion,message:cancelled ? "" : error.localizedDescription)
            }
            cleanupPayload(directory)
            throw error
        }
    }
    private static func performInstall(directory: URL, job: Object, paths: AppPaths) throws {
        let target = URL(fileURLWithPath:job.string("target")).standardizedFileURL, rawVersion = job.string("version")
        guard target == Installation.canonical, let pid = job.integer("parent_pid"), pid > 0, pid <= Int(Int32.max), pid != getpid(), version(rawVersion) != nil,
              job.string("sha256").range(of:#"^[a-fA-F0-9]{64}$"#,options:.regularExpression) != nil,
              job.string("old_sha256").range(of:#"^[a-fA-F0-9]{64}$"#,options:.regularExpression) != nil else { throw AppFailure("Invalid update job") }
        let expectedProcess = Installation.processPaths().first {$0.0 == Int32(pid)}?.1
        guard expectedProcess == target.appendingPathComponent("Contents/MacOS/Codexio").path else { throw AppFailure("Update parent does not own this app") }
        guard let lease = FileLease(target.deletingLastPathComponent().appendingPathComponent(".Codexio-update.lock")) else { throw AppFailure("Another update is in progress") }
        try withExtendedLifetime(lease) {
            _ = try Installation.verify(target)
            let archive = directory.appendingPathComponent("package.bin"), prepared = directory.appendingPathComponent("Codexio.pending")
            guard digest(try Data(contentsOf:archive,options:.mappedIfSafe)) == job.string("sha256").lowercased(), digest(try Data(contentsOf:target.appendingPathComponent("Contents/MacOS/Codexio"),options:.mappedIfSafe)) == job.string("old_sha256") else { throw AppFailure("Update verification failed") }
            if let size = job.integer("size"), (try archive.resourceValues(forKeys:[.fileSizeKey])).fileSize != size { throw AppFailure("Update size verification failed") }
            _ = try Installation.verify(prepared,version:rawVersion)
            try writeState(directory,"waiting_exit",version:rawVersion)
            try atomicJSON(["ready":true],to:directory.appendingPathComponent("installer-ready.json"))
            let deadline = Date().addingTimeInterval(90)
            while kill(Int32(pid),0) == 0 && Date() < deadline {
                if readObject(directory.appendingPathComponent("cancel.json")).flag("cancel") { throw AppFailure("Update installation was cancelled") }
                Thread.sleep(forTimeInterval:0.1)
            }
            guard kill(Int32(pid),0) != 0, readObject(directory.appendingPathComponent("commit.json")).flag("commit"), !readObject(directory.appendingPathComponent("cancel.json")).flag("cancel") else { throw AppFailure("The app has not confirmed a normal exit") }
            guard digest(try Data(contentsOf:target.appendingPathComponent("Contents/MacOS/Codexio"),options:.mappedIfSafe)) == job.string("old_sha256") else { throw AppFailure("The installed app changed during update") }
            let pending = target.deletingLastPathComponent().appendingPathComponent(".Codexio-"+directory.lastPathComponent+".pending")
            let backup = target.deletingLastPathComponent().appendingPathComponent(".Codexio-"+directory.lastPathComponent+".previous")
            guard !FileManager.default.fileExists(atPath:pending.path), !FileManager.default.fileExists(atPath:backup.path) else { throw AppFailure("Update backup already exists") }
            defer { try? FileManager.default.removeItem(at:pending) }
            try writeState(directory,"installing",version:rawVersion)
            guard try execute("/usr/bin/ditto",["--norsrc","--noextattr",prepared.path,pending.path],timeout:120).code == 0 else { throw AppFailure("Cannot stage the replacement") }
            _ = try Installation.verify(pending,version:rawVersion)
            try Installation.prepareReplacement(paths:paths)
            _ = try? execute("/usr/bin/pluginkit",["-r",target.appendingPathComponent(Installation.widgetRelative).path])
            try FileManager.default.moveItem(at:target,to:backup)
            do {
                try FileManager.default.moveItem(at:pending,to:target)
                try Installation.launchChecked(target,paths:paths,timeout:40)
                try writeState(directory,"done",version:rawVersion,confirmedHash:digest(try Data(contentsOf:target.appendingPathComponent("Contents/MacOS/Codexio"),options:.mappedIfSafe)))
                if !Installation.processPaths().contains(where:{$0.1.hasPrefix(backup.path+"/")}) { try? FileManager.default.removeItem(at:backup) }
                cleanupPayload(directory)
            } catch {
                let message = error.localizedDescription
                if !Installation.processPaths().contains(where:{$0.1 == target.appendingPathComponent("Contents/MacOS/Codexio").path}) {
                    do {
                        if FileManager.default.fileExists(atPath:target.path) { try FileManager.default.removeItem(at:target) }
                        try FileManager.default.moveItem(at:backup,to:target)
                        try writeState(directory,"rolled_back",version:rawVersion,message:message)
                        _ = try? execute("/usr/bin/pluginkit",["-a",target.appendingPathComponent(Installation.widgetRelative).path])
                        _ = try? execute("/usr/bin/open",["-a",target.path])
                    } catch { try? writeState(directory,"unconfirmed",version:rawVersion,message:message+"\n"+error.localizedDescription,backup:backup) }
                } else { try? writeState(directory,"unconfirmed",version:rawVersion,message:message,backup:backup) }
                throw error
            }
        }
    }
}
