import Foundation

@_silgen_name("codexio_archive_compress") private func archiveCompress(_ input: UnsafeRawPointer?,_ size: Int,_ output: UnsafeMutablePointer<UnsafeMutableRawPointer?>,_ count: UnsafeMutablePointer<Int>) -> Int32
@_silgen_name("codexio_archive_decompress") private func archiveDecompress(_ input: UnsafeRawPointer?,_ size: Int,_ output: UnsafeMutablePointer<UnsafeMutableRawPointer?>,_ expected: Int) -> Int32
@_silgen_name("codexio_archive_free") private func archiveFree(_ pointer: UnsafeMutableRawPointer?)

enum MessageArchive {
    static let version = 1
    static func encode(_ input: Data) throws -> Data {
        var output: UnsafeMutableRawPointer?, count=0
        let result=input.withUnsafeBytes { archiveCompress($0.baseAddress,input.count,&output,&count) }
        guard result==0,let output else { throw NSError(domain:NSPOSIXErrorDomain,code:Int(result)) }
        defer {archiveFree(output)}
        return Data(bytes:output,count:count)
    }
    static func decode(_ input: Data,size: Int) throws -> Data {
        guard size>=0 else {throw AppFailure("Invalid request archive size")}
        var output: UnsafeMutableRawPointer?
        let result=input.withUnsafeBytes { archiveDecompress($0.baseAddress,input.count,&output,size) }
        guard result==0,let output else { throw NSError(domain:NSPOSIXErrorDomain,code:Int(result)) }
        defer {archiveFree(output)}
        return Data(bytes:output,count:size)
    }
}

extension Database {
    func archiveMessageMedia(_ input: Object) throws -> Object {
        var value=input
        let destination=url.deletingLastPathComponent().appendingPathComponent("request-history-media",isDirectory:true)
        for field in ["attachments","final_attachments","generated_attachments"] where value[field] != nil {
            value[field]=try value.objects(field).map { item -> Object in
                var item=item;let source=URL(fileURLWithPath:item.string("path")).standardizedFileURL
                guard source.deletingLastPathComponent()==requestMedia.directory.standardizedFileURL,
                      source.lastPathComponent.range(of:#"^[a-f0-9]{64}\.[a-z]+$"#,options:.regularExpression) != nil else {return item}
                let target=destination.appendingPathComponent(source.lastPathComponent)
                if !FileStamp(target).exists,FileStamp(source).exists {
                    guard source.resolvingSymlinksInPath()==source else {throw AppFailure("Invalid message media source")}
                    let data=try Data(contentsOf:source)
                    guard digest(data)==source.deletingPathExtension().lastPathComponent else {throw AppFailure("Message image checksum mismatch")}
                    try FileManager.default.createDirectory(at:destination,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
                    try data.write(to:target,options:.atomic)
                }
                if FileStamp(target).exists {item["path"]=target.path}
                return item
            }
        }
        return value
    }
    func requestMessageRow(_ id: String) throws -> Object? {
        if let row=try query("SELECT data,digest,revision FROM usage_request_messages WHERE id=?",[id]).first {return row}
        guard var row=try query("SELECT codec,payload,checksum,digest,revision,size FROM usage_request_archives WHERE id=?",[id]).first else {return nil}
        guard row.string("codec")=="zlib-json-v1",let payload=row["payload"] as? Data,let size=row.integer("size") else {throw AppFailure("Unsupported request archive")}
        let decoded=try MessageArchive.decode(payload,size:size)
        guard digest(decoded)==row.string("checksum"),let text=String(data:decoded,encoding:.utf8) else {throw AppFailure("Request archive checksum mismatch")}
        row.removeValue(forKey:"payload");row["data"]=text
        return row
    }
    func archiveRequestMessage(_ id: String,encoded: String,digest hash: String,revision: Int) throws {
        let bytes=Data(encoded.utf8),checksum=digest(bytes)
        if try query("SELECT checksum FROM usage_request_archives WHERE id=?",[id]).first?.string("checksum")==checksum {return}
        let compressed=try MessageArchive.encode(bytes)
        try run("INSERT INTO usage_request_archives(id,codec,payload,checksum,digest,revision,size) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET codec=excluded.codec,payload=excluded.payload,checksum=excluded.checksum,digest=excluded.digest,revision=excluded.revision,size=excluded.size",[id,"zlib-json-v1",compressed,checksum,hash,revision,bytes.count])
    }
    func migrateMessageArchives() throws {
        let rows=try query("SELECT id,data,digest,revision FROM usage_request_messages m WHERE NOT EXISTS(SELECT 1 FROM usage_request_archives a WHERE a.id=m.id) LIMIT 64")
        guard !rows.isEmpty else {return}
        try transaction {
            for row in rows {try writeRequestMessage(row.string("id"),patch:jsonObject(Data(row.string("data").utf8)))}
        }
    }
}
