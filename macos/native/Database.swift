import Foundation
import SQLite3

final class Database {
    private var handle: OpaquePointer?
    private let lock = NSRecursiveLock()
    private let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    private let sourceDateFormatter: DateFormatter = {
        let formatter = DateFormatter(); formatter.locale = Locale(identifier:"en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd'T'HH-mm-ss"; return formatter
    }()
    let url: URL

    init(_ url: URL, readOnly: Bool = false) throws {
        self.url = url
        let flags = readOnly ? SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX : SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        guard sqlite3_open_v2(url.path, &handle, flags, nil) == SQLITE_OK else { throw AppFailure(L("无法打开用量数据库", "Cannot open the usage database")) }
        sqlite3_busy_timeout(handle, 15000)
        if !readOnly {
            try script("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS usage_records(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,model TEXT NOT NULL,session_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_records_time ON usage_records(timestamp DESC);
            CREATE INDEX IF NOT EXISTS usage_records_turn ON usage_records(session_id,COALESCE(NULLIF(json_extract(data,'$.request_turn_id'),''),json_extract(data,'$.turn_id')));
            CREATE TABLE IF NOT EXISTS usage_record_sources(record_id TEXT NOT NULL,source_id TEXT NOT NULL,PRIMARY KEY(record_id,source_id));
            CREATE TABLE IF NOT EXISTS usage_turns(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_agent_links(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_sources(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_meta(key TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_cursors(key TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_session_titles(session_id TEXT PRIMARY KEY,title TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_session_context(session_id TEXT PRIMARY KEY,cwd TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_session_files(path TEXT PRIMARY KEY,session_id TEXT NOT NULL,root TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_session_files_session ON usage_session_files(session_id);
            CREATE TABLE IF NOT EXISTS usage_request_sources(id TEXT PRIMARY KEY,path TEXT NOT NULL,root TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_request_ids(id TEXT PRIMARY KEY,mobile_id TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS usage_request_messages(id TEXT PRIMARY KEY,data TEXT NOT NULL,digest TEXT NOT NULL,revision INTEGER NOT NULL,size INTEGER NOT NULL,accessed REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_request_messages_accessed ON usage_request_messages(accessed);
            CREATE INDEX IF NOT EXISTS usage_turns_session ON usage_turns(json_extract(data,'$.session_id'));
            CREATE INDEX IF NOT EXISTS usage_turns_continuation ON usage_turns(json_extract(data,'$.session_id'),json_extract(data,'$.continuation_of'));
            CREATE INDEX IF NOT EXISTS usage_turns_root ON usage_turns(json_extract(data,'$.session_id'),json_extract(data,'$.root_turn_id'));
            CREATE TABLE IF NOT EXISTS usage_observations(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_revisions(kind TEXT PRIMARY KEY,revision INTEGER NOT NULL);
            INSERT OR IGNORE INTO usage_revisions VALUES('ledger',0),('observations',0);
            """)
            for table in ["usage_records","usage_record_sources","usage_turns","usage_agent_links","usage_session_titles","usage_session_context","usage_observations"] {
                let kind = table == "usage_observations" ? "observations" : "ledger"
                for action in ["INSERT","UPDATE","DELETE"] {
                    try script("CREATE TRIGGER IF NOT EXISTS revision_\(table)_\(action.lowercased()) AFTER \(action) ON \(table) BEGIN UPDATE usage_revisions SET revision=revision+1 WHERE kind='\(kind)'; END;")
                }
            }
            if object("usage_meta",key:"request-message-ids-v1").isEmpty {
                try transaction {
                    for row in try query("SELECT id FROM usage_turns") { try registerRequestID(row.string("id")) }
                    for row in try query("SELECT DISTINCT session_id,COALESCE(NULLIF(json_extract(data,'$.request_turn_id'),''),json_extract(data,'$.turn_id')) AS turn_id FROM usage_records WHERE session_id<>''") {
                        let turn = row.string("turn_id")
                        if !turn.isEmpty { try registerRequestID(turn.hasPrefix("turn:") ? turn : "turn:"+row.string("session_id")+":"+turn) }
                    }
                    try put("usage_meta",key:"request-message-ids-v1",value:["ready":true])
                }
            }
        }
    }
    deinit { sqlite3_close(handle) }
    private func failure() -> AppFailure { AppFailure(String(cString: sqlite3_errmsg(handle))) }
    func script(_ sql: String) throws {
        lock.lock(); defer { lock.unlock() }
        guard sqlite3_exec(handle, sql, nil, nil, nil) == SQLITE_OK else { throw failure() }
    }
    private func statement(_ sql: String, _ args: [Any]) throws -> OpaquePointer {
        var result: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &result, nil) == SQLITE_OK, let result else { throw failure() }
        for (index, value) in args.enumerated() {
            let i = Int32(index + 1)
            if value is NSNull { sqlite3_bind_null(result,i) }
            else if let s = value as? String { sqlite3_bind_text(result,i,s,-1,transient) }
            else if let n = value as? Int { sqlite3_bind_int64(result,i,Int64(n)) }
            else if let n = value as? Double { sqlite3_bind_double(result,i,n) }
            else { sqlite3_bind_text(result,i,jsonString(value),-1,transient) }
        }
        return result
    }
    func run(_ sql: String, _ args: [Any] = []) throws {
        lock.lock(); defer { lock.unlock() }
        let stmt = try statement(sql,args); defer { sqlite3_finalize(stmt) }
        guard sqlite3_step(stmt) == SQLITE_DONE else { throw failure() }
    }
    func query(_ sql: String, _ args: [Any] = []) throws -> [Object] {
        lock.lock(); defer { lock.unlock() }
        let stmt = try statement(sql,args); defer { sqlite3_finalize(stmt) }
        var rows: [Object] = []
        while true {
            let status = sqlite3_step(stmt)
            if status == SQLITE_DONE { break }
            guard status == SQLITE_ROW else { throw failure() }
            var row: Object = [:]
            for column in 0..<sqlite3_column_count(stmt) {
                let name = String(cString: sqlite3_column_name(stmt,column))
                switch sqlite3_column_type(stmt,column) {
                case SQLITE_INTEGER: row[name] = Int(sqlite3_column_int64(stmt,column))
                case SQLITE_FLOAT: row[name] = sqlite3_column_double(stmt,column)
                case SQLITE_TEXT: row[name] = String(cString: sqlite3_column_text(stmt,column))
                default: row[name] = NSNull()
                }
            }
            rows.append(row)
        }
        return rows
    }
    func transaction<T>(_ body: () throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        try script("BEGIN IMMEDIATE")
        do { let value = try body(); try script("COMMIT"); return value }
        catch { try? script("ROLLBACK"); throw error }
    }
    func object(_ table: String, key: String) -> Object {
        guard ["usage_meta","usage_cursors"].contains(table), let row = try? query("SELECT data FROM \(table) WHERE key=?", [key]).first else { return [:] }
        return jsonObject(Data(row.string("data").utf8))
    }
    func put(_ table: String, key: String, value: Object) throws {
        guard ["usage_meta","usage_cursors"].contains(table) else { return }
        try run("INSERT INTO \(table)(key,data) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",[key,jsonString(value)])
    }
    func records() throws -> [Object] {
        try recordPayloads().map { row in
            var value = jsonObject(Data(row.string("data").utf8))
            if !row.string("title").isEmpty { value["session_title"] = row.string("title") }
            if !row.string("cwd").isEmpty { value["session_cwd"] = row.string("cwd") }
            value["local_origin"] = row.integer("local_origin") == 1 || value.string("source_id").hasPrefix("local")
            return value
        }
    }
    func recordPayloads() throws -> [Object] {
        try query("SELECT r.id,r.data,t.title,c.cwd,EXISTS(SELECT 1 FROM usage_record_sources s WHERE s.record_id=r.id AND (s.source_id='local' OR s.source_id LIKE 'local:%')) AS local_origin FROM usage_records r LEFT JOIN usage_session_titles t ON r.session_id=t.session_id LEFT JOIN usage_session_context c ON r.session_id=c.session_id ORDER BY r.timestamp DESC")
    }
    func metadata(_ table: String) throws -> [Object] {
        guard ["usage_turns","usage_agent_links"].contains(table) else { return [] }
        let sql = table == "usage_turns" ? "SELECT t.data,c.cwd FROM usage_turns t LEFT JOIN usage_session_context c ON json_extract(t.data,'$.session_id')=c.session_id" : "SELECT data FROM \(table)"
        return try query(sql).map { row in
            var value = jsonObject(Data(row.string("data").utf8))
            if !row.string("cwd").isEmpty { value["session_cwd"] = row.string("cwd") }
            return value
        }
    }
    func writeRecord(_ raw: Object, source: String = "local") throws {
        guard !raw.string("id").isEmpty else { return }
        var record = raw
        if let existing = try query("SELECT data FROM usage_records WHERE id=?",[raw.string("id")]).first {
            let old = jsonObject(Data(existing.string("data").utf8))
            if !record.flag("context_owner_verified",true) && old.flag("context_owner_verified",true) {
                for key in ["model","reasoning_effort","service_tier","model_context_window","limit_id","prompt_preview","output_preview","provider"] { record[key] = old[key] }
                record["context_owner_verified"] = true
            }
            for (key,value) in old where record[key] == nil || record[key] is NSNull { record[key] = value }
            for key in ["prompt_preview","output_preview","session_title"] where record.string(key).isEmpty { record[key] = old[key] }
        }
        try run("INSERT INTO usage_records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,model=excluded.model,session_id=excluded.session_id,data=excluded.data WHERE data<>excluded.data",[record.string("id"),record.string("timestamp"),record.string("model","unknown"),record.string("session_id"),jsonString(record)])
        try run("INSERT OR IGNORE INTO usage_record_sources VALUES(?,?)",[record.string("id"),source])
        let turn = record.string("request_turn_id").isEmpty ? record.string("turn_id") : record.string("request_turn_id")
        if !record.string("session_id").isEmpty, !turn.isEmpty { try registerRequestID(turn.hasPrefix("turn:") ? turn : "turn:"+record.string("session_id")+":"+turn) }
    }
    func writeTurn(_ row: Object) throws {
        guard !row.string("id").isEmpty else { return }
        var value = row
        if let existing = try query("SELECT data FROM usage_turns WHERE id=?",[row.string("id")]).first {
            let old = jsonObject(Data(existing.string("data").utf8))
            for (key,field) in old where value[key] == nil { value[key] = field }
            for key in ["prompt_preview","output_preview","ended_at"] where value.string(key).isEmpty { value[key] = old[key] }
        }
        if let message = try query("SELECT digest,revision FROM usage_request_messages WHERE id=?",[row.string("id")]).first {
            value["message_digest"] = message["digest"]; value["message_revision"] = message["revision"]
        }
        try run("INSERT INTO usage_turns VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data",[value.string("id"),jsonString(value)])
        try registerRequestID(value.string("id"))
    }
    func repairRequestMetadata(_ evidence: Object) throws {
        guard var row = try requestTurn(evidence.string("id")) else { return }
        if row.flag("metadata_missing") { row = evidence }
        for key in ["cwd","session_cwd","prompt_source_turn_id","resume_kind","continuation_of","context_compaction_item_id","parent_session_id","parent_turn_id"] where !evidence.string(key).isEmpty { row[key] = evidence[key] }
        for key in ["has_user_message","has_final_message","context_compaction_observed","explicit_task_start","context_compaction_completed","model_switch_continuation","is_approval_review"] where evidence.flag(key) { row[key] = true }
        for key in ["prompt_preview","output_preview"] where row.string(key).isEmpty && !evidence.string(key).isEmpty { row[key] = evidence[key] }
        if RequestClassification.isApproval(row) { row["record_kind"] = RequestClassification.approval }
        else if row.flag("has_user_message") || !row.string("continuation_of").isEmpty { row["record_kind"] = "user_request" }
        else if evidence.string("record_kind") == "context_compaction", row.string("prompt_preview").isEmpty, row.string("resume_kind").isEmpty, !row.flag("has_final_message") { row["record_kind"] = "context_compaction" }
        try writeTurn(row)
    }
    func updateTitles(_ titles: [String:String]) throws {
        try transaction {
            for (id,title) in titles where !title.isEmpty {
                try run("INSERT INTO usage_session_titles VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title WHERE title<>excluded.title",[id,String(title.prefix(400))])
            }
        }
    }
    func updateSessionContexts(_ values: [String:String]) throws {
        for (session,cwd) in values where !session.isEmpty && cwd.hasPrefix("/") && cwd.utf8.count <= 4096 {
            try run("INSERT INTO usage_session_context VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET cwd=excluded.cwd WHERE cwd<>excluded.cwd",[session,cwd])
        }
    }
    func registerSessionFile(_ file: URL,root: URL,session: String) throws {
        guard !session.isEmpty else { return }
        try run("INSERT INTO usage_session_files VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET session_id=excluded.session_id,root=excluded.root WHERE session_id<>excluded.session_id OR root<>excluded.root",[file.path,session,root.path])
    }
    func registerRequestSource(_ id: String,file: URL,root: URL) throws {
        // Reuse the indexer's existing cursor identities, without replaying usage.
        guard !id.isEmpty else { return }
        try run("INSERT INTO usage_request_sources VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path,root=excluded.root WHERE path<>excluded.path OR root<>excluded.root",[id,file.path,root.path])
    }
    private func registerRequestID(_ id: String) throws {
        guard !id.isEmpty else { return }
        try run("INSERT OR IGNORE INTO usage_request_ids VALUES(?,?)",[id,digest(Data(id.utf8))])
    }

    // Bodies have independent, bounded storage. No full message enters a ledger
    // payload or UsageSnapshot. The digest excludes access/recovery timestamps.
    func writeRequestMessage(_ id: String,patch: Object) throws {
        lock.lock(); defer { lock.unlock() }
        guard !id.isEmpty else { return }
        let old = try query("SELECT data,digest,revision FROM usage_request_messages WHERE id=?",[id]).first ?? [:]
        var value = jsonObject(Data(old.string("data").utf8))
        for (key,field) in patch { value[key] = field }
        value["id"] = id
        for key in ["user","final"] {
            let bounded = RequestMessageText.bounded(value.string(key))
            value[key] = bounded.text
            if !bounded.complete { value[key+"_complete"] = false }
        }
        value["attachments"] = Array(value.objects("attachments").prefix(32))
        let terminal = ["completed","aborted"].contains(value.string("status"))
        let any = !value.string("user").isEmpty || !value.string("final").isEmpty || !value.objects("attachments").isEmpty
        value["availability"] = !any ? "unavailable" : value.flag("user_complete") && (!terminal || value.flag("final_complete")) ? "available" : "partial"
        var material = value
        material.removeValue(forKey:"recovery_signature")
        let hash = identity(material), changed = hash != old.string("digest")
        let revision = (old.integer("revision") ?? 0)+(changed ? 1 : 0)
        let encoded = jsonString(value)
        try run("INSERT INTO usage_request_messages VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,digest=excluded.digest,revision=excluded.revision,size=excluded.size,accessed=excluded.accessed WHERE data<>excluded.data",[id,encoded,hash,revision,encoded.utf8.count,Date().timeIntervalSince1970])
        if changed {
            try run("UPDATE usage_turns SET data=json_set(data,'$.message_digest',?,'$.message_revision',?) WHERE id=?",[hash,revision,id])
        }
        // A detail may be recovered again from its registered source after LRU
        // eviction. The ledger and the source log are never deleted.
        let retained = try query("SELECT id,size FROM usage_request_messages ORDER BY accessed DESC,id")
        var bytes = 0
        for (index,row) in retained.enumerated() {
            bytes += row.integer("size") ?? 0
            if index >= 512 || bytes > 67_108_864 {
                try run("DELETE FROM usage_request_messages WHERE id=?",[row.string("id")])
            }
        }
    }
    func promoteRequestMessage(from oldID: String,to id: String) throws {
        guard let row = try query("SELECT data FROM usage_request_messages WHERE id=?",[oldID]).first else { return }
        try writeRequestMessage(id,patch:jsonObject(Data(row.string("data").utf8)))
        try run("DELETE FROM usage_request_messages WHERE id=?",[oldID])
    }
    private func requestTurn(_ id: String) throws -> Object? {
        if let stored = try query("SELECT data FROM usage_turns WHERE id=?",[id]).first {
            var row = jsonObject(Data(stored.string("data").utf8))
            if ["","unknown"].contains(row.string("model").lowercased()), !RequestClassification.isApproval(row) {
                let calls = try query("SELECT count(*) AS calls,min(CASE WHEN model='codex-auto-review' OR json_extract(data,'$.is_approval_review')=1 OR json_extract(data,'$.record_kind')='approval_review' THEN 1 ELSE 0 END) AS reviews FROM usage_records WHERE session_id=? AND COALESCE(NULLIF(json_extract(data,'$.request_turn_id'),''),json_extract(data,'$.turn_id')) IN (?,?)",[row.string("session_id"),row.string("turn_id"),id]).first ?? [:]
                if calls.integer("calls") ?? 0 > 0, calls.integer("reviews") == 1 { row["record_kind"] = RequestClassification.approval; row["is_approval_review"] = true }
            }
            return row
        }
        let parts = id.split(separator:":",maxSplits:2).map(String.init)
        guard parts.count == 3, parts[0] == "turn", let call = try query("SELECT timestamp FROM usage_records WHERE session_id=? AND COALESCE(NULLIF(json_extract(data,'$.request_turn_id'),''),json_extract(data,'$.turn_id')) IN (?,?) ORDER BY timestamp LIMIT 1",[parts[1],parts[2],id]).first else { return nil }
        return ["id":id,"session_id":parts[1],"turn_id":parts[2],"started_at":call.string("timestamp"),"started_inferred":true,"metadata_missing":true,"status":"unknown"]
    }
    private func detailMembers(_ root: Object) throws -> [Object] {
        let session = root.string("session_id")
        var ids: Set<String> = [root.string("id"),root.string("turn_id")]
        var result = [root]
        // Only explicit continuation/root edges can contribute a final answer.
        // Child-agent answers and unrelated messages never become the main reply.
        var cursor = 0
        while cursor < result.count && result.count < 64 {
            let current = result[cursor]; cursor += 1
            let key = current.string("id"), turn = current.string("turn_id")
            let related = try query("SELECT data FROM usage_turns WHERE json_extract(data,'$.session_id')=? AND (json_extract(data,'$.continuation_of') IN (?,?) OR json_extract(data,'$.root_turn_id') IN (?,?)) LIMIT 65",[session,key,turn,key,turn]).map {jsonObject(Data($0.string("data").utf8))}
            let additions = related.filter { row in
                !row.flag("is_subagent") && !RequestClassification.isApproval(row) && row.string("alias_of").isEmpty && !ids.contains(row.string("id")) &&
                [row.string("continuation_of"),row.string("root_turn_id")].contains(where:{!$0.isEmpty && ids.contains($0)})
            }
            for row in additions.prefix(max(0,64-result.count)) { result.append(row); ids.insert(row.string("id")); ids.insert(row.string("turn_id")) }
        }
        return result.sorted {$0.string("started_at") < $1.string("started_at")}
    }
    private func sourceFiles(_ request: Object) throws -> [(URL,URL)] {
        lock.lock(); defer { lock.unlock() }
        let known = try query("SELECT path,root FROM usage_request_sources WHERE id=?",[request.string("id")])
        var rows = known
        if known.isEmpty || !FileStamp(URL(fileURLWithPath:known[0].string("path"))).exists {
            rows += try query("SELECT path,root FROM usage_session_files WHERE session_id=? ORDER BY path DESC LIMIT 32",[request.string("session_id")])
            if let started = parsedDate(request["started_at"]) {
                sourceDateFormatter.timeZone = .current
                let boundary = "rollout-"+sourceDateFormatter.string(from:started)+"~"
                rows.sort {
                    let left = URL(fileURLWithPath:$0.string("path")).lastPathComponent
                    let right = URL(fileURLWithPath:$1.string("path")).lastPathComponent
                    if (left <= boundary) != (right <= boundary) { return left <= boundary }
                    return left > right
                }
            }
        }
        var result: [(URL,URL)] = [], seen = Set<String>()
        for row in rows {
            let file = URL(fileURLWithPath:row.string("path")).standardizedFileURL, root = URL(fileURLWithPath:row.string("root")).standardizedFileURL
            let allowed = ["sessions","archived_sessions"].contains { file.path.hasPrefix(root.appendingPathComponent($0).path+"/") }
            guard allowed, file.pathExtension == "jsonl", file.resolvingSymlinksInPath() == file,
                  root.resolvingSymlinksInPath() == root, FileStamp(file).exists, seen.insert(file.path).inserted else { continue }
            result.append((file,root))
            if result.count == 8 { break }
        }
        return result
    }
    func recoverRequestMetadata(_ requestID: String) throws {
        guard let row = try requestTurn(requestID), let (file,root) = try sourceFiles(row).first else { return }
        try UsageIndexer(self).recoverMessageDetails(ids:[requestID],file:file,root:root,metadataOnly:true)
    }
    func recoverRecentRequestMetadata(_ requestIDs: [String],maximumFiles: Int) throws -> (done: [String], unavailable: [String]) {
        var files: [URL:(URL,Set<String>)] = [:]
        var done: [String] = [], unavailable: [String] = []
        for id in requestIDs.prefix(12) {
            guard let row = try requestTurn(id), let (file,root) = try sourceFiles(row).first else { done.append(id); unavailable.append(id); continue }
            guard files[file] != nil || files.count < maximumFiles else { continue }
            var group = files[file] ?? (root,[]); group.1.insert(id); files[file] = group
        }
        for (file,(root,ids)) in files {
            do {
                try UsageIndexer(self).recoverMessageDetails(ids:ids,file:file,root:root,metadataOnly:true)
                for id in ids {
                    let row = try requestTurn(id) ?? [:]
                    if row.string("record_kind") != "context_compaction" && row.string("prompt_preview").isEmpty && !row.flag("has_user_message") { unavailable.append(id) }
                }
            } catch let error as NSError where error.domain == NSCocoaErrorDomain || error.domain == NSPOSIXErrorDomain {
                unavailable.append(contentsOf: ids)
            }
            done.append(contentsOf:ids)
        }
        return (done, unavailable)
    }
    func requestMessageDetail(_ requestID: String) throws -> Object? {
        lock.lock(); defer { lock.unlock() }
        guard !requestID.isEmpty, requestID.utf8.count <= 512,
              let lookup = try query("SELECT id FROM usage_request_ids WHERE id=? OR mobile_id=? LIMIT 1",[requestID,requestID]).first,
              var root = try requestTurn(lookup.string("id")) else { return nil }
        var seen = Set<String>()
        while !root.string("alias_of").isEmpty, seen.insert(root.string("id")).inserted, seen.count <= 64 {
            guard let parent = try requestTurn(root.string("alias_of")) else { break }; root = parent
        }
        guard root.string("record_kind") != "context_compaction" else { return nil }
        var members = try detailMembers(root)
        let promptSourceID = root.flag("has_user_message") ? "" : root.string("prompt_source_turn_id")
        let promptSourceKey = promptSourceID.hasPrefix("turn:") ? promptSourceID : "turn:"+root.string("session_id")+":"+promptSourceID
        let promptSource = promptSourceID.isEmpty ? nil : try requestTurn(promptSourceKey)
        let recoveryMembers = members+(promptSource.map {[$0]} ?? [])
        var sources: [String:[(URL,URL)]] = [:], signatures: [String:String] = [:]
        for member in recoveryMembers {
            let id = member.string("id"), files = try sourceFiles(member)
            sources[id] = files
            signatures[id] = identity(files.map { value -> [Any] in
                let stamp = FileStamp(value.0)
                return [value.0.path,stamp.size,stamp.modified?.timeIntervalSince1970 ?? 0,stamp.device,stamp.inode]
            })
        }
        let missing = try recoveryMembers.filter { member in
            let stored = try query("SELECT data FROM usage_request_messages WHERE id=?",[member.string("id")]).first ?? [:]
            let detail = jsonObject(Data(stored.string("data").utf8))
            let ownsInput = member.string("id") == root.string("id") || member.string("id") == promptSourceKey || member.flag("has_user_message")
            return ((ownsInput && !detail.flag("user_complete")) || (["completed","aborted"].contains(member.string("status")) && !detail.flag("final_complete"))) && detail.string("recovery_signature") != signatures[member.string("id")]
        }
        if !missing.isEmpty {
            var batches: [URL:(URL,Set<String>)] = [:], order: [URL] = []
            for member in missing {
                let id = member.string("id")
                for (file,rootURL) in sources[id] ?? [] {
                    if batches[file] == nil { batches[file] = (rootURL,[]); order.append(file) }
                    batches[file]?.1.insert(id)
                }
            }
            var pending = Set(missing.map {$0.string("id")})
            for file in order {
                guard let (rootURL,targets) = batches[file] else { continue }
                let ids = targets.intersection(pending)
                guard !ids.isEmpty else { continue }
                try UsageIndexer(self).recoverMessageDetails(ids:ids,file:file,root:rootURL)
                for member in missing where ids.contains(member.string("id")) {
                    let id = member.string("id")
                    let detail = jsonObject(Data((try query("SELECT data FROM usage_request_messages WHERE id=?",[id]).first ?? [:]).string("data").utf8))
                    let ownsInput = id == root.string("id") || id == promptSourceKey || member.flag("has_user_message")
                    if (!ownsInput || detail.flag("user_complete")) && (!["completed","aborted"].contains(member.string("status")) || detail.flag("final_complete")) { pending.remove(id) }
                }
            }
            for member in missing {
                let id = member.string("id")
                try writeRequestMessage(id,patch:["recovery_signature":signatures[id] ?? "missing"])
            }
            members = try detailMembers(try requestTurn(root.string("id")) ?? root)
        }
        if let promptSource, let stored = try query("SELECT data FROM usage_request_messages WHERE id=?",[promptSource.string("id")]).first {
            let input = jsonObject(Data(stored.string("data").utf8))
            if input.flag("user_complete") || !input.string("user").isEmpty {
                try writeRequestMessage(root.string("id"),patch:["user":input.string("user"),"user_complete":input.flag("user_complete"),"attachments":input.objects("attachments")])
            }
        }
        guard (try requestTurn(root.string("id")))?.string("record_kind") != "context_compaction" else { return nil }
        var details: [Object] = []
        for member in members {
            let id = member.string("id")
            var detail = jsonObject(Data((try query("SELECT data FROM usage_request_messages WHERE id=?",[id]).first ?? [:]).string("data").utf8))
            let metadata: Object = ["started_at":parsedDate(member["started_at"])?.timeIntervalSince1970 as Any? ?? NSNull(),"completed_at":parsedDate(member["ended_at"])?.timeIntervalSince1970 as Any? ?? NSNull(),"status":member.string("status","unknown"),"recovery_signature":signatures[id] ?? "missing"]
            for (key,value) in metadata { detail[key] = value }
            try writeRequestMessage(id,patch:detail)
            let stored = try query("SELECT data,digest,revision FROM usage_request_messages WHERE id=?",[id]).first ?? [:]
            detail = jsonObject(Data(stored.string("data").utf8)); detail["digest"] = stored["digest"]; detail["revision"] = stored["revision"]
            try run("UPDATE usage_request_messages SET accessed=? WHERE id=?",[Date().timeIntervalSince1970,id])
            details.append(detail)
        }
        guard let first = details.first, let last = details.last else { return nil }
        let users = details.map {$0.string("user")}.filter {!$0.isEmpty}
        let user = RequestMessageText.bounded(users.joined(separator:"\n\n")), final = last.string("final")
        let allAttachments = details.flatMap {$0.objects("attachments")}, attachments = Array(allAttachments.prefix(32))
        let userComplete = user.complete && members.count < 64 && allAttachments.count <= 32 && first.flag("user_complete") && details.dropFirst().allSatisfy {$0.string("user").isEmpty || $0.flag("user_complete")}
        let finalComplete = members.count < 64 && last.flag("final_complete"), terminal = ["completed","aborted"].contains(last.string("status"))
        let available = !user.text.isEmpty || !final.isEmpty || !attachments.isEmpty
        var result: Object = ["id":root.string("id"),"user":user.text,"final":final,"user_complete":userComplete,"final_complete":finalComplete,"started_at":first["started_at"] ?? NSNull(),"completed_at":last["completed_at"] ?? NSNull(),"status":last.string("status","unknown"),"attachments":attachments,"availability":!available ? "unavailable" : userComplete && (!terminal || finalComplete) ? "available" : "partial"]
        result["digest"] = identity(result)
        result["revision"] = details.reduce(0) {$0+($1.integer("revision") ?? 0)}
        return result
    }
}

enum RequestMessageText {
    static let byteLimit = 1_048_576
    static func bounded(_ text: String) -> (text: String,complete: Bool) {
        guard text.utf8.count > byteLimit else { return (text,true) }
        var bytes = Data(text.utf8.prefix(byteLimit))
        while String(data:bytes,encoding:.utf8) == nil { bytes.removeLast() }
        return (String(data:bytes,encoding:.utf8) ?? "",false)
    }
    static func extract(_ content: Any?,user: Bool) -> Object {
        let parts = content as? [Object] ?? []
        var text = content as? String ?? parts.filter {["text","input_text","output_text"].contains($0.string("type"))}.map {$0.string("text")}.joined(separator:"\n")
        var attachments: [Object] = [], complete = true
        if user {
            if text.contains("<send_user_message_question_reply>") { return [:] }
            let tags = "recommended_plugins|environment_context|permissions(?: instructions)?|INSTRUCTIONS|user_instructions|developer_instructions|skills_instructions|skill_instructions|system|developer|system-reminder|app-context|collaboration_mode|multi_agent_role|multi_agent_mode"
            text = text.replacingOccurrences(of:"(?is)<("+tags+")(?:\\s[^>]*)?>.*?</\\1\\s*>",with:"",options:.regularExpression)
            if let wrapper = text.range(of:"# Files mentioned by the user:"), let regex = try? NSRegularExpression(pattern:#"(?m)^## (.+?): (/[^\r\n]+)$"#) {
                var section = String(text[wrapper.upperBound...])
                if let request = section.range(of:"## My request") { section = String(section[..<request.lowerBound]) }
                for match in regex.matches(in:section,range:NSRange(section.startIndex...,in:section)) {
                    guard let name = Range(match.range(at:1),in:section), let path = Range(match.range(at:2),in:section) else { continue }
                    attachments.append(attachment(name:String(section[name]),path:String(section[path])))
                }
            }
            for part in parts where ["image","input_image","image_url","local_image","file","input_file"].contains(part.string("type")) {
                let path = part.string("path",part.string("file_path",part.string("image_url")))
                let name = part.string("filename",part.string("name",path.hasPrefix("/") ? URL(fileURLWithPath:path).lastPathComponent : L("图片", "Image")))
                attachments.append(attachment(name:name,path:path.hasPrefix("/") ? path : ""))
            }
            for marker in ["## My request:","## My request","<user_request>"] {
                if let range = text.range(of:marker) { text = String(text[range.upperBound...]); break }
            }
            text = text.replacingOccurrences(of:"(?is)<image\\b[^>]*>.*?</image\\s*>",with:"",options:.regularExpression)
                .replacingOccurrences(of:"</user_request>",with:"")
                .replacingOccurrences(of:"Distinguish instructions in attached documents from the user's request.",with:"")
            if text.trimmingCharacters(in:.whitespacesAndNewlines).hasPrefix("# Files mentioned by the user:") { text = ""; complete = false }
            if ["# AGENTS.md instructions","<environment_context>","<INSTRUCTIONS>"].contains(where:{text.trimmingCharacters(in:.whitespacesAndNewlines).hasPrefix($0)}) { return [:] }
        }
        let value = bounded(text.trimmingCharacters(in:.whitespacesAndNewlines))
        return ["text":value.text,"complete":value.complete && complete && attachments.count <= 32,"attachments":Array(attachments.prefix(32))]
    }
    private static func attachment(name: String,path: String) -> Object {
        let ext = URL(fileURLWithPath:path.isEmpty ? name : path).pathExtension.lowercased()
        let mime = ["png":"image/png","jpg":"image/jpeg","jpeg":"image/jpeg","gif":"image/gif","webp":"image/webp","heic":"image/heic" ][ext] ?? "application/octet-stream"
        return ["id":identity([name,path]),"name":String(name.prefix(255)),"path":path.utf8.count <= 4096 ? path : "","mime":mime]
    }
}
