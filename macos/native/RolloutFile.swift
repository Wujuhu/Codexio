import Foundation

@_silgen_name("codexio_zstd_decode")
private func decodeRolloutZstd(_ source: Int32,_ destination: Int32,_ limit: UInt64) -> Int32

/// A temporary decoded file lives only as long as its readers. Request history
/// is durably compressed in the ledger, independent of this disposable cache.
final class RolloutReader {
    private let handle: FileHandle
    private var release: (() -> Void)?
    private var closed=false
    init(_ handle: FileHandle,release: (() -> Void)?=nil) {self.handle=handle;self.release=release}
    deinit {try? close()}
    func read(upToCount count: Int) throws -> Data? {try handle.read(upToCount:count)}
    func seek(toOffset offset: UInt64) throws {try handle.seek(toOffset:offset)}
    func seekToEnd() throws -> UInt64 {try handle.seekToEnd()}
    func offset() throws -> UInt64 {try handle.offset()}
    func close() throws {
        guard !closed else {return};closed=true
        defer {release?();release=nil}
        try handle.close()
    }
}
enum RolloutFile {
    private static let lock=NSLock()
    private final class Entry {let file:URL;var readers=0;init(_ file:URL){self.file=file}}
    private static var files:[String:Entry]=[:]
    private static var cleaned=false
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
    static func size(_ source: URL) throws -> UInt64 {
        let reader = try open(source); defer { try? reader.close() }
        return try reader.seekToEnd()
    }
    private static var directory: URL {
        if let path = ProcessInfo.processInfo.environment["CODEXIO_ROLLOUT_CACHE_DIR"], !path.isEmpty { return URL(fileURLWithPath:path,isDirectory:true) }
        let base = ProcessInfo.processInfo.arguments.contains("--mock") ? FileManager.default.temporaryDirectory : FileManager.default.urls(for:.cachesDirectory,in:.userDomainMask)[0]
        return base.appendingPathComponent("Codexio/Rollouts-v1",isDirectory:true)
    }
    private static func reader(_ key: String,_ entry: Entry) throws -> RolloutReader {
        let handle=try FileHandle(forReadingFrom:entry.file);entry.readers+=1
        return RolloutReader(handle) {
            lock.lock();defer {lock.unlock()}
            entry.readers-=1
            if entry.readers==0 {try? FileManager.default.removeItem(at:entry.file);files.removeValue(forKey:key)}
        }
    }
    static func open(_ requested: URL) throws -> RolloutReader {
        let source=resolve(requested)
        guard compressed(source) else {return RolloutReader(try FileHandle(forReadingFrom:source))}
        lock.lock();defer {lock.unlock()}
        let stamp=FileStamp(source),key=signature(source),folder=directory
        guard stamp.exists else {throw CocoaError(.fileReadNoSuchFile)}
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        if !cleaned {
            for file in ((try? FileManager.default.contentsOfDirectory(at:folder,includingPropertiesForKeys:nil)) ?? []) where ["jsonl","tmp"].contains(file.pathExtension) {try? FileManager.default.removeItem(at:file)}
            cleaned=true
        }
        if let entry=files[key] {return try reader(key,entry)}
        let temporary=folder.appendingPathComponent(UUID().uuidString+".tmp")
        guard FileManager.default.createFile(atPath:temporary.path,contents:nil,attributes:[.posixPermissions:0o600]) else {throw CocoaError(.fileWriteUnknown)}
        var retained=false
        defer {if !retained {try? FileManager.default.removeItem(at:temporary)}}
        let input=try FileHandle(forReadingFrom:source),output=try FileHandle(forWritingTo:temporary)
        defer {try? input.close();try? output.close()}
        let error=decodeRolloutZstd(input.fileDescriptor,output.fileDescriptor,UInt64.max)
        guard error==0 else {throw NSError(domain:NSPOSIXErrorDomain,code:Int(error))}
        guard FileStamp(source)==stamp else {throw CocoaError(.fileReadUnknown)}
        try output.close()
        let entry=Entry(temporary),result=try reader(key,entry)
        files[key]=entry;retained=true
        return result
    }
}
