import Foundation
import SwiftUI

// Reuse NativeUpdater's stable version comparison and manifest-first lookup.
// Release tags identify a publication; ios.version identifies the installed app.
private enum IOSUpdateVersion {
    static func parts(_ value: String) -> [Int]? {
        let pieces = value.split(separator:".",omittingEmptySubsequences:false)
        guard pieces.count == 3, pieces.allSatisfy({ part in
            !part.isEmpty && part.utf8.allSatisfy { $0 >= 48 && $0 <= 57 }
                && (part.count == 1 || part.first != "0")
        }) else { return nil }
        let numbers = pieces.compactMap {Int($0)}
        return numbers.count == 3 && numbers.allSatisfy {$0 <= 65535} ? numbers : nil
    }
    static func newer(_ target: String, than current: String) -> Bool {
        guard let target = parts(target), let current = parts(current) else { return false }
        return current.lexicographicallyPrecedes(target)
    }
}

struct IOSUpdateRelease: Codable, Equatable, Identifiable, Sendable {
    enum NotesState: String, Codable, Sendable { case available, missing, unavailable }
    var version: String
    var tag: String
    var assetSHA256: String
    var assetSize: Int
    var notes: String
    var notesState: NotesState
    var id: String { version+":"+tag }
    var releaseURL: URL { URL(string:"https://github.com/Wujuhu/Codexio/releases/tag/"+tag)! }
    var valid: Bool {
        IOSUpdateVersion.parts(version) != nil && tag.first == "v"
            && IOSUpdateVersion.parts(String(tag.dropFirst())) != nil
            && assetSHA256.count == 64 && assetSHA256.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
            && assetSize > 0 && notes.utf8.count <= 60_000
    }
    var displayedNotes: String {
        switch notesState {
        case .available: return notes
        case .missing: return "此版本未提供 iOS 更新说明。"
        case .unavailable: return "更新说明暂时无法获取。"
        }
    }
}

private enum IOSPlatformNotes {
    static func extract(_ body: String) -> String? {
        let lines = body.replacingOccurrences(of:"\r\n",with:"\n").replacingOccurrences(of:"\r",with:"\n").components(separatedBy:"\n")
        var selected = false, output: [String] = []
        var fence: Character?, fenceLength = 0
        for line in lines {
            let text = line.trimmingCharacters(in:.whitespaces)
            let indented = line.hasPrefix("    ") || line.hasPrefix("\t")
            if !indented, let first = text.first, first == "`" || first == "~" {
                let count = text.prefix(while:{$0 == first}).count
                if count >= 3 {
                    if fence == nil { fence = first; fenceLength = count }
                    else if fence == first, count >= fenceLength, text.dropFirst(count).trimmingCharacters(in:.whitespaces).isEmpty { fence = nil }
                    if selected { output.append(line) }
                    continue
                }
            }
            if fence == nil, !indented {
                let level = text.prefix(while:{$0 == "#"}).count
                let suffix = text.dropFirst(level)
                if (1...2).contains(level), suffix.isEmpty || suffix.first?.isWhitespace == true {
                    if selected { break }
                    let heading = suffix.trimmingCharacters(in:.whitespaces).trimmingCharacters(in:CharacterSet(charactersIn:"#")).trimmingCharacters(in:.whitespaces)
                    if level == 2, heading.lowercased() == "ios" { selected = true }
                    continue
                }
            }
            if selected { output.append(line) }
        }
        let result = output.joined(separator:"\n").trimmingCharacters(in:.whitespacesAndNewlines)
        return selected && !result.isEmpty ? result : nil
    }
}

private enum IOSUpdateError: Error { case invalidManifest, invalidRelease, noPackage, http(Int), oversized }

private final class IOSUpdateClient: @unchecked Sendable {
    private struct Manifest: Decodable {
        struct Package: Decodable { let version: String; let url: URL; let size: Int; let sha256: String }
        let ios: Package?
    }
    private struct Publication: Decodable {
        struct Asset: Decodable { let name: String; let state: String; let browser_download_url: URL; let size: Int; let digest: String? }
        let tag_name: String
        let html_url: URL
        let draft: Bool
        let prerelease: Bool
        let body: String?
        let assets: [Asset]
    }
    private let session: URLSession
    private let current: String
    init(current: String) {
        self.current = current
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10; config.timeoutIntervalForResource = 20
        config.waitsForConnectivity = false
        session = URLSession(configuration:config)
    }
    private func read(_ url: URL) async throws -> Data {
        var request = URLRequest(url:url,cachePolicy:.reloadIgnoringLocalCacheData)
        request.setValue("application/vnd.github+json",forHTTPHeaderField:"Accept")
        request.setValue("Codexio-iOS/"+current,forHTTPHeaderField:"User-Agent")
        let (bytes,response) = try await session.bytes(for:request)
        guard let http = response as? HTTPURLResponse, response.url?.scheme == "https" else { throw IOSUpdateError.invalidRelease }
        guard http.statusCode == 200 else { throw IOSUpdateError.http(http.statusCode) }
        let limit = 1_048_576
        guard response.expectedContentLength <= limit else { throw IOSUpdateError.oversized }
        var data = Data()
        data.reserveCapacity(Int(max(0,response.expectedContentLength)))
        for try await byte in bytes {
            guard data.count < limit else { throw IOSUpdateError.oversized }
            data.append(byte)
        }
        try Task.checkCancellation()
        return data
    }
    func latest(cached: IOSUpdateRelease?) async throws -> IOSUpdateRelease? {
        guard IOSUpdateVersion.parts(current) != nil else { throw IOSUpdateError.invalidManifest }
        let url = URL(string:"https://github.com/Wujuhu/Codexio/releases/latest/download/latest.json")!
        let manifest = try JSONDecoder().decode(Manifest.self,from:await read(url))
        guard let package = manifest.ios, IOSUpdateVersion.parts(package.version) != nil else { throw IOSUpdateError.invalidManifest }
        guard IOSUpdateVersion.newer(package.version,than:current) else { return nil }
        let components = package.url.path.split(separator:"/").map(String.init)
        guard components.count == 6, Array(components.prefix(4)) == ["Wujuhu","Codexio","releases","download"], components.last == "Codexio.ipa" else { throw IOSUpdateError.invalidManifest }
        let tag = components[4]
        let expectedAsset = "https://github.com/Wujuhu/Codexio/releases/download/"+tag+"/Codexio.ipa"
        var candidate = IOSUpdateRelease(version:package.version,tag:tag,assetSHA256:package.sha256.lowercased(),assetSize:package.size,notes:"",notesState:.unavailable)
        guard candidate.valid, package.url.absoluteString == expectedAsset else { throw IOSUpdateError.invalidManifest }
        if let cached, cached.valid, cached.version == candidate.version, cached.tag == tag,
           cached.assetSHA256 == candidate.assetSHA256, cached.assetSize == candidate.assetSize, cached.notesState != .unavailable { return cached }
        // Metadata must finish successfully before an automatic reminder can
        // consume its one-time presentation. Failures retain the prior cache.
        let metadata = try await read(URL(string:"https://api.github.com/repos/Wujuhu/Codexio/releases/tags/"+tag)!)
        let publication = try JSONDecoder().decode(Publication.self,from:metadata)
        guard !publication.draft, !publication.prerelease, publication.tag_name == tag,
              publication.html_url == candidate.releaseURL else { throw IOSUpdateError.invalidRelease }
        guard let asset = publication.assets.first(where:{$0.name == "Codexio.ipa"}), asset.state == "uploaded",
              asset.browser_download_url == package.url, asset.size == package.size,
              asset.digest == nil || asset.digest?.lowercased() == "sha256:"+candidate.assetSHA256 else { throw IOSUpdateError.noPackage }
        if let notes = IOSPlatformNotes.extract(publication.body ?? "") {
            guard notes.utf8.count <= 60_000 else { throw IOSUpdateError.oversized }
            candidate.notes = notes; candidate.notesState = .available
        } else { candidate.notesState = .missing }
        return candidate
    }
}

@MainActor final class IOSUpdateChecker: ObservableObject {
    private struct Cache: Codable, Sendable {
        var release: IOSUpdateRelease?
        var checkedAt: Date?
        var checkedForVersion: String?
        var failedAt: Date?
        var highestPresentedVersion: String?
    }
    let currentVersion: String
    @Published private(set) var available: IOSUpdateRelease?
    @Published private(set) var checking = false
    @Published private(set) var status: String?
    @Published private(set) var presentationRequest: IOSUpdateRelease?
    private let files = DispatchQueue(label:"com.wujuhu.codexio.ios.updates",qos:.utility)
    private let cacheURL: URL
    private let client: IOSUpdateClient
    private var cache = Cache()
    private var loaded = false, foreground = false, manualRequested = false
    private var task: Task<Void,Never>?
    private var requestID = UUID()
    init() {
        currentVersion = Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "—"
        client = IOSUpdateClient(current:currentVersion)
        cacheURL = FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("Codexio/updates/ios-release.json")
        let url = cacheURL
        files.async { [weak self] in
            let size = (try? url.resourceValues(forKeys:[.fileSizeKey]).fileSize) ?? 0
            let saved = size > 0 && size <= 524_288 ? (try? Data(contentsOf:url)).flatMap {try? JSONDecoder().decode(Cache.self,from:$0)} : nil
            Task { @MainActor in self?.restore(saved) }
        }
    }
    private func restore(_ saved: Cache?) {
        if let saved {
            cache = saved
            if let value = saved.release, value.valid, IOSUpdateVersion.newer(value.version,than:currentVersion) { available = value }
            else { cache.release = nil }
            if let version = cache.highestPresentedVersion, IOSUpdateVersion.parts(version) == nil { cache.highestPresentedVersion = nil }
        }
        loaded = true
        if foreground { check(manual:manualRequested) }
    }
    func activate() { foreground = true; check(manual:false) }
    func deactivate() {
        foreground = false; requestID = UUID(); task?.cancel(); task = nil
        checking = false; manualRequested = false
    }
    private func recent(_ date: Date?, within interval: TimeInterval) -> Bool {
        guard let date else { return false }
        let elapsed = Date().timeIntervalSince(date)
        return elapsed >= 0 && elapsed < interval
    }
    func check(manual: Bool) {
        if manual { manualRequested = true; status = nil }
        guard loaded, foreground, task == nil else { return }
        if !manualRequested {
            if cache.checkedForVersion == currentVersion, available?.notesState != .unavailable,
               recent(cache.checkedAt,within:6*3600) { requestAutomaticPresentation(); return }
            if recent(cache.failedAt,within:3600) { return }
        }
        checking = true; status = nil
        let id = UUID(); requestID = id
        let client = client, previous = available
        task = Task.detached(priority:.utility) { [weak self] in
            let result: Result<IOSUpdateRelease?,Error>
            do { result = .success(try await client.latest(cached:previous)) }
            catch { if Task.isCancelled { return }; result = .failure(error) }
            guard !Task.isCancelled else { return }
            await self?.finish(result,id:id)
        }
    }
    private func finish(_ result: Result<IOSUpdateRelease?,Error>,id: UUID) {
        guard requestID == id, foreground else { return }
        checking = false; task = nil
        let manual = manualRequested; manualRequested = false
        switch result {
        case .success(let release):
            available = release; cache.release = release; cache.checkedAt = Date()
            cache.checkedForVersion = currentVersion; cache.failedAt = nil
            if manual {
                if let release { presentationRequest = release }
                else { presentationRequest = nil; status = "当前已是最新版本" }
            } else if release == nil { presentationRequest = nil }
            else { requestAutomaticPresentation() }
        case .failure:
            cache.failedAt = Date()
            if manual { status = "检查失败，请稍后重试。" }
        }
        persist()
    }
    private func requestAutomaticPresentation() {
        guard let available, available.notesState != .unavailable else { return }
        if let previous = cache.highestPresentedVersion, !IOSUpdateVersion.newer(available.version,than:previous) { return }
        presentationRequest = available
    }
    func showAvailable() { if let available { presentationRequest = available } }
    func takePresentation() -> IOSUpdateRelease? {
        let value = presentationRequest; presentationRequest = nil; return value
    }
    func didPresent(_ release: IOSUpdateRelease) {
        // Only a sheet that actually appeared counts as a reminder. Manual
        // viewing also establishes that the user has already seen this version.
        if let previous = cache.highestPresentedVersion, !IOSUpdateVersion.newer(release.version,than:previous) { return }
        cache.highestPresentedVersion = release.version; persist()
    }
    private func persist() {
        let saved = cache, url = cacheURL
        files.async {
            do {
                try FileManager.default.createDirectory(at:url.deletingLastPathComponent(),withIntermediateDirectories:true)
                try JSONEncoder().encode(saved).write(to:url,options:.atomic)
            } catch { /* Update metadata must not interfere with device sync. */ }
        }
    }
}

struct IOSUpdateNotice: View {
    @Environment(\.dismiss) private var dismiss
    let release: IOSUpdateRelease
    let currentVersion: String
    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            Text("发现 iOS 新版本").font(.title2.bold())
            VStack(spacing:8) {
                LabeledContent("当前版本",value:currentVersion)
                LabeledContent("最新版本",value:release.version)
            }.font(.subheadline).monospacedDigit()
            Divider()
            ScrollView {
                MobileMarkdownView(text:release.displayedNotes,images:[])
                    .frame(maxWidth:.infinity,alignment:.leading)
            }
            Link("GitHub 最新 Release ↗",destination:release.releaseURL).font(.subheadline)
            Button { dismiss() } label: { Text("OK").frame(maxWidth:.infinity) }
                .buttonStyle(.borderedProminent).controlSize(.large)
        }.padding(24).presentationDetents([.medium,.large]).presentationDragIndicator(.visible)
    }
}
