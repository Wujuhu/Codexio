import Foundation
import Darwin

// Apple's read-only SQLite can reject a WAL-mode database after the writer has
// checkpointed and removed its sidecars. Keep the original source read-only.
// Like ObjectFileCache, reuse by file identity/mtime/size and bound the cache.
final class ReadOnlySource {
    private struct Signature: Equatable {
        let main: FileStamp
        let wal: FileStamp
        let journal: FileStamp
        init(_ url: URL) {
            main=FileStamp(url)
            wal=FileStamp(URL(fileURLWithPath:url.path+"-wal"))
            journal=FileStamp(URL(fileURLWithPath:url.path+"-journal"))
        }
        var standalone: Bool { main.exists && !wal.exists && !journal.exists }
    }
    private final class Snapshot {
        let signature: Signature
        let directory: URL
        let database: Database
        init(_ source: URL, signature: Signature) throws {
            self.signature=signature
            directory=FileManager.default.temporaryDirectory.appendingPathComponent("codexio-source-"+UUID().uuidString,isDirectory:true)
            try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
            let copy=directory.appendingPathComponent("snapshot.sqlite")
            do {
                // APFS copy-on-write avoids reading/copying the entire history.
                // Other filesystems use one disk copy per changed source, never
                // an in-memory copy; only two snapshots can be retained.
                if clonefile(source.path,copy.path,0) != 0 { try FileManager.default.copyItem(at:source,to:copy) }
                guard Signature(source)==signature else { throw Changed() }
                database=try Database(copy,readOnly:true,immutableSnapshot:true)
            } catch {
                try? FileManager.default.removeItem(at:directory)
                throw error
            }
        }
        deinit { try? FileManager.default.removeItem(at:directory) }
    }
    private struct Changed: Error {}
    private var snapshots: [URL:Snapshot]=[:]
    private var order: [URL]=[]
    func clear() { snapshots.removeAll(); order.removeAll() }

    // One bounded retry handles a writer creating/removing WAL during a read.
    // The cloned file is immutable; source changes invalidate it before publish.
    func read<T>(_ url: URL,_ body: (Database) throws -> T) throws -> T {
        for _ in 0..<2 {
            let before=Signature(url)
            do {
                let result:T
                if before.standalone {
                    let snapshot:Snapshot
                    if let cached=snapshots[url],cached.signature==before {snapshot=cached}
                    else {
                        remove(url)
                        snapshot=try Snapshot(url,signature:before)
                        while order.count>=2 {remove(order[0])}
                        snapshots[url]=snapshot
                    }
                    order.removeAll {$0==url};order.append(url)
                    result=try body(snapshot.database)
                } else {
                    remove(url)
                    result=try body(Database(url,readOnly:true))
                }
                if !before.standalone || Signature(url)==before {return result}
            } catch {
                if !(error is Changed) && Signature(url)==before {throw error}
            }
            remove(url)
        }
        throw AppFailure(L("本地记录正在更新，请稍后刷新", "Local records are changing. Refresh shortly."))
    }
    private func remove(_ url: URL) {
        snapshots.removeValue(forKey:url)
        order.removeAll {$0==url}
    }
}
