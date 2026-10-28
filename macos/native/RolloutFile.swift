import Foundation

@_silgen_name("codexio_zstd_decode")
private func decodeRolloutZstd(_ source: Int32,_ destination: Int32,_ limit: UInt64) -> Int32

/// Keep original source identities and decompressed byte offsets. Only the I/O
/// boundary sees the bounded, disposable cache; the ledger never owns a copy of
/// a compressed source or interprets compression as deletion.
enum RolloutFile {
    private static let lock = NSLock()
    private static let limit: UInt64 = 1_073_741_824
    static func compressed(_ file: URL) -> Bool { file.lastPathComponent.hasSuffix(".jsonl.zst") }
    static func accepts(_ file: URL) -> Bool { file.pathExtension == "jsonl" || compressed(file) }
    static func resolve(_ file: URL) -> URL {
        if FileStamp(file).exists { return file }
        let alternative = compressed(file) ? file.deletingPathExtension() : URL(fileURLWithPath:file.path+".zst")
        return accepts(file) && FileStamp(alternative).exists ? alternative : file
    }
    static func signature(_ file: URL) -> String {
        let stamp = FileStamp(file)
        return identity(["path":file.path,"size":stamp.size,"modified":stamp.modified?.timeIntervalSince1970 ?? 0,"device":stamp.device,"inode":stamp.inode] as Object)
    }
    static func open(_ source: URL) throws -> FileHandle { try FileHandle(forReadingFrom:readableURL(source)) }
    static func size(_ source: URL) throws -> UInt64 {
        let reader = try open(source); defer { try? reader.close() }
        return try reader.seekToEnd()
    }
    private static var directory: URL {
        if let path = ProcessInfo.processInfo.environment["CODEXIO_ROLLOUT_CACHE_DIR"], !path.isEmpty { return URL(fileURLWithPath:path,isDirectory:true) }
        let base = ProcessInfo.processInfo.arguments.contains("--mock") ? FileManager.default.temporaryDirectory : FileManager.default.urls(for:.cachesDirectory,in:.userDomainMask)[0]
        return base.appendingPathComponent("Codexio/Rollouts-v1",isDirectory:true)
    }
    static func readableURL(_ requested: URL) throws -> URL {
        let source = resolve(requested)
        guard compressed(source) else { return source }
        lock.lock(); defer { lock.unlock() }
        let stamp = FileStamp(source), key = signature(source), folder = directory
        guard stamp.exists else { throw CocoaError(.fileReadNoSuchFile) }
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        for file in ((try? FileManager.default.contentsOfDirectory(at:folder,includingPropertiesForKeys:nil)) ?? []) where file.pathExtension == "tmp" {
            if let modified=FileStamp(file).modified, Date().timeIntervalSince(modified)>3600 { try? FileManager.default.removeItem(at:file) }
        }
        let destination = folder.appendingPathComponent(key+".jsonl")
        if FileStamp(destination).exists {
            if let modified = FileStamp(destination).modified, Date().timeIntervalSince(modified) > 60 { try? FileManager.default.setAttributes([.modificationDate:Date()],ofItemAtPath:destination.path) }
            return destination
        }
        let temporary = folder.appendingPathComponent(UUID().uuidString+".tmp")
        guard FileManager.default.createFile(atPath:temporary.path,contents:nil,attributes:[.posixPermissions:0o600]) else { throw CocoaError(.fileWriteUnknown) }
        defer { try? FileManager.default.removeItem(at:temporary) }
        let input = try FileHandle(forReadingFrom:source), output = try FileHandle(forWritingTo:temporary)
        defer { try? input.close(); try? output.close() }
        let error = decodeRolloutZstd(input.fileDescriptor,output.fileDescriptor,limit)
        guard error == 0 else { throw NSError(domain:NSPOSIXErrorDomain,code:Int(error)) }
        guard FileStamp(source) == stamp else { throw CocoaError(.fileReadUnknown) }
        try output.close()
        do { try FileManager.default.moveItem(at:temporary,to:destination) }
        catch { if !FileStamp(destination).exists { throw error } }
        trim(folder,keeping:destination)
        return destination
    }
    private static func trim(_ folder: URL,keeping destination: URL) {
        let files = ((try? FileManager.default.contentsOfDirectory(at:folder,includingPropertiesForKeys:nil)) ?? []).filter {$0.pathExtension == "jsonl"}
        let entries = files.map {($0,FileStamp($0))}.sorted {($0.1.modified ?? .distantPast) < ($1.1.modified ?? .distantPast)}
        var bytes = entries.reduce(UInt64(0)) {$0+$1.1.size}, count = entries.count
        for (file,stamp) in entries where file != destination && (bytes > limit || count > 64) {
            if (try? FileManager.default.removeItem(at:file)) != nil { bytes -= stamp.size; count -= 1 }
        }
    }
}
