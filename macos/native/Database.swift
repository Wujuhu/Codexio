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
    let requestMedia: RequestMediaStore

    init(_ url: URL, readOnly: Bool = false, immutableSnapshot: Bool = false) throws {
        self.url = url
        requestMedia = RequestMediaStore(database:url)
        precondition(!immutableSnapshot || readOnly)
        // immutable is exclusively for our private, stable source copies. Never
        // use it on a live Codex database: that would ignore its active WAL.
        let name = immutableSnapshot ? url.absoluteString+"?mode=ro&immutable=1" : url.path
        let flags = (readOnly ? SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX : SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX) | (immutableSnapshot ? SQLITE_OPEN_URI : 0)
        guard sqlite3_open_v2(name, &handle, flags, nil) == SQLITE_OK else {
            sqlite3_close(handle); handle=nil
            throw AppFailure(L("无法打开用量数据库", "Cannot open the usage database"))
        }
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
            CREATE TABLE IF NOT EXISTS usage_local_chat_rank(thread_id TEXT PRIMARY KEY,tokens INTEGER,cost REAL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_journey_requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,timestamp TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS journey_requests_session ON usage_journey_requests(session_id,timestamp DESC);
            CREATE TABLE IF NOT EXISTS usage_session_files(path TEXT PRIMARY KEY,session_id TEXT NOT NULL,root TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_session_files_session ON usage_session_files(session_id);
            CREATE TABLE IF NOT EXISTS usage_request_sources(id TEXT PRIMARY KEY,path TEXT NOT NULL,root TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_request_messages(id TEXT PRIMARY KEY,data TEXT NOT NULL,digest TEXT NOT NULL,revision INTEGER NOT NULL,size INTEGER NOT NULL,accessed REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_request_messages_accessed ON usage_request_messages(accessed);
            CREATE TABLE IF NOT EXISTS usage_request_archives(id TEXT PRIMARY KEY,codec TEXT NOT NULL,payload BLOB NOT NULL,checksum TEXT NOT NULL,digest TEXT NOT NULL,revision INTEGER NOT NULL,size INTEGER NOT NULL);
            CREATE VIEW IF NOT EXISTS usage_request_message_metadata AS SELECT id,digest,revision FROM usage_request_archives UNION ALL SELECT id,digest,revision FROM usage_request_messages m WHERE NOT EXISTS(SELECT 1 FROM usage_request_archives a WHERE a.id=m.id);
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
            else if let data = value as? Data { _ = data.withUnsafeBytes { sqlite3_bind_blob64(result,i,$0.baseAddress,UInt64(data.count),transient) } }
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
        var rows: [Object] = []
        try forEachRow(sql, args) { rows.append($0) }
        return rows
    }
    private func forEachRow(_ sql: String, _ args: [Any], _ consume: (Object) throws -> Void) throws {
        lock.lock(); defer { lock.unlock() }
        let stmt = try statement(sql,args); defer { sqlite3_finalize(stmt) }
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
                case SQLITE_BLOB: row[name] = Data(bytes:sqlite3_column_blob(stmt,column),count:Int(sqlite3_column_bytes(stmt,column)))
                default: row[name] = NSNull()
                }
            }
            try consume(row)
        }
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
    private static let recordPayloadSQL = "SELECT r.id,r.session_id,r.data,t.title,c.cwd,EXISTS(SELECT 1 FROM usage_record_sources s WHERE s.record_id=r.id AND (s.source_id='local' OR s.source_id LIKE 'local:%')) AS local_origin FROM usage_records r LEFT JOIN usage_session_titles t ON r.session_id=t.session_id LEFT JOIN usage_session_context c ON r.session_id=c.session_id"
    private static let turnMetadataSQL = "SELECT t.data,c.cwd FROM usage_turns t LEFT JOIN usage_session_context c ON json_extract(t.data,'$.session_id')=c.session_id"
    func recordPayloads() throws -> [Object] { try query(Self.recordPayloadSQL + " ORDER BY r.timestamp DESC") }
    func forEachRecordPayload(_ consume: (Object) throws -> Void) throws {
        try forEachRow(Self.recordPayloadSQL + " ORDER BY r.timestamp DESC", [], consume)
    }
    func recordPayloads(sessions: [String], limit: Int) throws -> [Object] {
        guard !sessions.isEmpty else { return [] }
        let parameters = sessions.map { _ in "?" }.joined(separator:",")
        return try query(Self.recordPayloadSQL + " WHERE r.session_id IN (\(parameters)) ORDER BY r.timestamp DESC LIMIT ?",sessions.map { $0 as Any } + [limit])
    }
    func turnMetadata(sessions: [String], limit: Int) throws -> [Object] {
        guard !sessions.isEmpty else { return [] }
        let parameters = sessions.map { _ in "?" }.joined(separator:",")
        return try query(Self.turnMetadataSQL + " WHERE json_extract(t.data,'$.session_id') IN (\(parameters)) ORDER BY t.rowid LIMIT ?",sessions.map { $0 as Any } + [limit]).map(Self.decodeMetadata)
    }
    func metadata(_ table: String) throws -> [Object] {
        guard ["usage_turns","usage_agent_links"].contains(table) else { return [] }
        let sql = table == "usage_turns" ? Self.turnMetadataSQL + " ORDER BY t.rowid" : "SELECT data FROM \(table)"
        return try query(sql).map(Self.decodeMetadata)
    }
    private static func decodeMetadata(_ row: Object) -> Object {
        var value = jsonObject(Data(row.string("data").utf8))
        if !row.string("cwd").isEmpty { value["session_cwd"] = row.string("cwd") }
        return value
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
            for key in ["model","provider","reasoning_effort"] where ["","unknown"].contains(record.string(key)) { record[key] = old[key] }
        }
        try run("INSERT INTO usage_records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,model=excluded.model,session_id=excluded.session_id,data=excluded.data WHERE data<>excluded.data",[record.string("id"),record.string("timestamp"),record.string("model","unknown"),record.string("session_id"),jsonString(record)])
        try run("INSERT OR IGNORE INTO usage_record_sources VALUES(?,?)",[record.string("id"),source])
    }
    func writeTurn(_ row: Object) throws {
        guard !row.string("id").isEmpty else { return }
        var value = row
        if let existing = try query("SELECT data FROM usage_turns WHERE id=?",[row.string("id")]).first {
            let old = jsonObject(Data(existing.string("data").utf8))
            if old.flag("has_main_message"), !value.flag("has_main_message") {
                // A pre-upgrade cursor may still carry the overwritten preview.
                value["prompt_preview"] = old["prompt_preview"]
                value["record_kind"] = old["record_kind"]
            }
            for key in ["has_main_message","has_user_message","has_final_message","has_usage"] where old.flag(key) { value[key] = true }
            for (key,field) in old where value[key] == nil { value[key] = field }
            for key in ["prompt_preview","output_preview","ended_at"] where value.string(key).isEmpty { value[key] = old[key] }
            for key in ["model","provider","reasoning_effort"] where ["","unknown"].contains(value.string(key)) { value[key] = old[key] }
            if ["completed","aborted"].contains(old.string("status")), row.string("ended_at").isEmpty { value["status"] = old["status"] }
        }
        if let message = try query("SELECT digest,revision FROM usage_request_message_metadata WHERE id=?",[row.string("id")]).first {
            value["message_digest"] = message["digest"]; value["message_revision"] = message["revision"]
        }
        RequestResume.preserveMainOwnership(&value)
        try run("INSERT INTO usage_turns VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data",[value.string("id"),jsonString(value)])
    }
    func repairRequestMetadata(_ evidence: Object) throws {
        guard var row = try requestTurn(evidence.string("id")) else { return }
        if row.flag("metadata_missing") { row = evidence }
        if evidence["resume_kind"] is NSNull, ["model_switch","interrupted"].contains(row.string("resume_kind")) {
            if row.flag("model_switch_continuation") || row.flag("request_resume_continuation") { row["continuation_of"] = NSNull() }
            row["resume_kind"] = NSNull(); row["prompt_source_turn_id"] = NSNull(); row["model_switch_continuation"] = false; row["request_resume_continuation"] = false
        }
        for key in ["cwd","session_cwd","prompt_source_turn_id","resume_kind","continuation_of","context_compaction_item_id","parent_session_id","parent_turn_id"] where !evidence.string(key).isEmpty { row[key] = evidence[key] }
        for key in ["has_user_message","has_final_message","context_compaction_observed","explicit_task_start","context_compaction_completed","model_switch_continuation","request_resume_continuation","is_approval_review"] where evidence.flag(key) { row[key] = true }
        for key in ["prompt_preview","output_preview"] where row.string(key).isEmpty && !evidence.string(key).isEmpty { row[key] = evidence[key] }
        if evidence.flag("has_main_message"), !evidence.string("prompt_preview").isEmpty {
            row["has_main_message"] = true; row["prompt_preview"] = evidence["prompt_preview"]
            RequestResume.preserveMainOwnership(&row)
        }
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
    // Bodies have independent, bounded storage. No full message enters a ledger
    // payload or UsageSnapshot. The digest excludes access/recovery timestamps.
    func writeRequestMessage(_ id: String,patch: Object) throws {
        lock.lock(); defer { lock.unlock() }
        if sqlite3_get_autocommit(handle) != 0 {try transaction {try saveRequestMessage(id,patch:patch)}}
        else {try saveRequestMessage(id,patch:patch)}
    }
    private func saveRequestMessage(_ id: String,patch: Object) throws {
        guard !id.isEmpty else { return }
        let old = try requestMessageRow(id) ?? [:]
        var value = jsonObject(Data(old.string("data").utf8)), patch = patch
        // A rewritten/compacted source may expose a different, incomplete
        // representation of the same request. Keep the full archived message
        // and its matching media/identity together until a complete one arrives.
        for field in ["user","final"] where value.flag(field+"_complete") && patch[field+"_complete"] as? Bool == false {
            for key in [field,field+"_complete",field+"_message_key",field+"_media_schema",field == "user" ? "attachments":"final_attachments"] { patch.removeValue(forKey:key) }
            if field == "final" { patch.removeValue(forKey:"final_source") }
        }
        if ["completed","aborted"].contains(value.string("status")), !["completed","aborted"].contains(patch.string("status")) { patch.removeValue(forKey:"status") }
        let sameUser = !patch.string("user_message_key").isEmpty && patch.string("user_message_key") == value.string("user_message_key")
        var sameFinal = !patch.string("final_message_key").isEmpty && patch.string("final_message_key") == value.string("final_message_key")
        if patch.removeValue(forKey:"final_fallback") as? Bool == true,
           value.string("final_source") == "message" || (patch.string("final").isEmpty && patch.objects("final_attachments").isEmpty && (!value.string("final").isEmpty || !value.objects("final_attachments").isEmpty)) {
            if patch.string("final") == value.string("final") {
                sameFinal = true
                patch["final_source"] = value["final_source"]; patch["final_message_key"] = value["final_message_key"]
                patch["final_complete"] = value.flag("final_complete")
            } else {
                for key in ["final","final_complete","final_attachments","final_source","final_message_key"] { patch.removeValue(forKey:key) }
            }
        }
        for (key,merge) in [("attachments",sameUser),("final_attachments",sameFinal)] where patch[key] != nil && merge {
            patch[key] = RequestMedia.deduplicated(value.objects(key)+patch.objects(key))
        }
        if patch["generated_attachments"] != nil {
            patch["generated_attachments"] = RequestMedia.deduplicated(value.objects("generated_attachments")+patch.objects("generated_attachments"))
            if !value.objects("generated_attachments").isEmpty, !value.flag("generated_complete") { patch["generated_complete"] = false }
        }
        for key in ["attachments","final_attachments","generated_attachments"] where patch[key] != nil {
            let previous = key == "attachments" ? value.objects(key) : value.objects("final_attachments")+value.objects("generated_attachments")
            patch[key] = RequestMedia.preservingCreationDates(patch.objects(key),from:previous)
        }
        if sameUser, patch.string("user").isEmpty, !value.string("user").isEmpty { patch["user"] = value["user"]; patch["user_complete"] = value["user_complete"] }
        for (key,field) in patch { value[key] = field }
        value["id"] = id
        for key in ["attachments","final_attachments","generated_attachments"] {
            let attachments = RequestMedia.deduplicated(value.objects(key))
            value[key] = Array(attachments.prefix(RequestMedia.limit))
            if attachments.count > RequestMedia.limit { value[key == "attachments" ? "user_complete" : key == "generated_attachments" ? "generated_complete" : "final_complete"] = false }
        }
        let terminal = ["completed","aborted"].contains(value.string("status"))
        value=try archiveMessageMedia(value)
        let any = !value.string("user").isEmpty || !value.string("final").isEmpty || ["attachments","final_attachments","generated_attachments"].contains {!value.objects($0).isEmpty}
        value["availability"] = !any ? "unavailable" : value.flag("user_complete") && (!terminal || value.flag("final_complete")) ? "available" : "partial"
        var material = value
        material.removeValue(forKey:"recovery_signature")
        let hash = identity(material), changed = hash != old.string("digest")
        let revision = (old.integer("revision") ?? 0)+(changed ? 1 : 0)
        let encoded = jsonString(value)
        try archiveRequestMessage(id,encoded:encoded,digest:hash,revision:revision)
        if encoded.utf8.count<=67_108_864 {
            try run("INSERT INTO usage_request_messages VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,digest=excluded.digest,revision=excluded.revision,size=excluded.size,accessed=excluded.accessed WHERE data<>excluded.data",[id,encoded,hash,revision,encoded.utf8.count,Date().timeIntervalSince1970])
        } else {try run("DELETE FROM usage_request_messages WHERE id=?",[id])}
        if changed {
            try run("UPDATE usage_turns SET data=json_set(data,'$.message_digest',?,'$.message_revision',?) WHERE id=?",[hash,revision,id])
        }
        // A detail may be recovered again from its registered source after LRU
        // eviction. The ledger and the source log are never deleted.
        let retained = try query("SELECT m.id,m.size,EXISTS(SELECT 1 FROM usage_request_archives a WHERE a.id=m.id AND a.digest=m.digest AND a.revision=m.revision) AS archived FROM usage_request_messages m ORDER BY accessed DESC,id")
        var bytes = 0
        for (index,row) in retained.enumerated() {
            bytes += row.integer("size") ?? 0
            if (index >= 512 || bytes > 67_108_864), row.integer("archived")==1 {
                try run("DELETE FROM usage_request_messages WHERE id=?",[row.string("id")])
            }
        }
    }
    func promoteRequestMessage(from oldID: String,to id: String) throws {
        guard let row = try requestMessageRow(oldID) else { return }
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
            let file = RolloutFile.resolve(URL(fileURLWithPath:row.string("path")).standardizedFileURL), root = URL(fileURLWithPath:row.string("root")).standardizedFileURL
            let allowed = file.path.hasPrefix(root.path+"/")
            guard allowed, RolloutFile.accepts(file), file.resolvingSymlinksInPath() == file,
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
}

enum RequestMessageText {
    static let byteLimit = 1_048_576
    static func bounded(_ text: String,limit: Int = byteLimit) -> (text: String,complete: Bool) {
        guard text.utf8.count > limit else { return (text,true) }
        var bytes = Data(text.utf8.prefix(limit))
        while String(data:bytes,encoding:.utf8) == nil { bytes.removeLast() }
        return (String(data:bytes,encoding:.utf8) ?? "",false)
    }
    static func extract(_ content: Any?,user: Bool,cwd: String = "",store: RequestMediaStore? = nil) -> Object {
        let parts = RequestMedia.content(content)
        let input = (text:parts.filter {["text","input_text","output_text"].contains($0.string("type"))}.map {$0.string("text")}.joined(separator:"\n"),complete:true)
        var text = input.text, extra: [Object] = [], complete = input.complete
        if user {
            if text.contains("<send_user_message_question_reply>") { return [:] }
            let tags = "recommended_plugins|environment_context|permissions(?: instructions)?|INSTRUCTIONS|user_instructions|developer_instructions|skills_instructions|skill_instructions|system|developer|system-reminder|app-context|collaboration_mode|multi_agent_role|multi_agent_mode"
            text = text.replacingOccurrences(of:"(?is)<("+tags+")(?:\\s[^>]*)?>.*?</\\1\\s*>",with:"",options:.regularExpression)
            extra = RequestMedia.userFiles(text)
            for marker in ["## My request:","## My request","<user_request>"] {
                if let range = text.range(of:marker) { text = String(text[range.upperBound...]); break }
            }
            text = text.replacingOccurrences(of:"(?is)<image\\b[^>]*>.*?</image\\s*>",with:"",options:.regularExpression)
                .replacingOccurrences(of:"(?is)<image\\b[^>]*/>",with:"",options:.regularExpression)
                .replacingOccurrences(of:"(?is)<image\\b[^>]*>",with:"",options:.regularExpression)
                .replacingOccurrences(of:"</user_request>",with:"")
                .replacingOccurrences(of:"Distinguish instructions in attached documents from the user's request.",with:"")
            if text.trimmingCharacters(in:.whitespacesAndNewlines).hasPrefix("# Files mentioned by the user:") { text = ""; complete = false }
            if ["# AGENTS.md instructions","<environment_context>","<INSTRUCTIONS>"].contains(where:{text.trimmingCharacters(in:.whitespacesAndNewlines).hasPrefix($0)}) { return [:] }
        }
        let media = RequestMedia.extract(text:&text,parts:parts,extra:extra,placement:user ? "user" : "final",cwd:cwd,store:store)
        let redacted = RequestMedia.preview(text)
        if redacted != text { complete = false; text = redacted }
        let value = (text:text.trimmingCharacters(in:.whitespacesAndNewlines),complete:true)
        return ["text":value.text,"complete":value.complete && complete && media.complete,"attachments":media.attachments,"media_schema":RequestMedia.schema]
    }
}
