import Foundation
import SQLite3

final class Database {
    private var handle: OpaquePointer?
    private let lock = NSRecursiveLock()
    private let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
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
            CREATE TABLE IF NOT EXISTS usage_record_sources(record_id TEXT NOT NULL,source_id TEXT NOT NULL,PRIMARY KEY(record_id,source_id));
            CREATE TABLE IF NOT EXISTS usage_turns(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_agent_links(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_sources(id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_meta(key TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_cursors(key TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_session_titles(session_id TEXT PRIMARY KEY,title TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_observations(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_revisions(kind TEXT PRIMARY KEY,revision INTEGER NOT NULL);
            INSERT OR IGNORE INTO usage_revisions VALUES('ledger',0),('observations',0);
            """)
            for table in ["usage_records","usage_record_sources","usage_turns","usage_agent_links","usage_session_titles","usage_observations"] {
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
            value["local_origin"] = row.integer("local_origin") == 1 || value.string("source_id").hasPrefix("local")
            return value
        }
    }
    func recordPayloads() throws -> [Object] {
        try query("SELECT r.id,r.data,t.title,EXISTS(SELECT 1 FROM usage_record_sources s WHERE s.record_id=r.id AND (s.source_id='local' OR s.source_id LIKE 'local:%')) AS local_origin FROM usage_records r LEFT JOIN usage_session_titles t ON r.session_id=t.session_id ORDER BY r.timestamp DESC")
    }
    func metadata(_ table: String) throws -> [Object] {
        guard ["usage_turns","usage_agent_links"].contains(table) else { return [] }
        return try query("SELECT data FROM \(table)").map { jsonObject(Data($0.string("data").utf8)) }
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
    }
    func writeTurn(_ row: Object) throws {
        guard !row.string("id").isEmpty else { return }
        var value = row
        if let existing = try query("SELECT data FROM usage_turns WHERE id=?",[row.string("id")]).first {
            let old = jsonObject(Data(existing.string("data").utf8))
            for (key,field) in old where value[key] == nil { value[key] = field }
            for key in ["prompt_preview","output_preview","ended_at"] where value.string(key).isEmpty { value[key] = old[key] }
        }
        try run("INSERT INTO usage_turns VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data",[value.string("id"),jsonString(value)])
    }
    func updateTitles(_ titles: [String:String]) throws {
        try transaction {
            for (id,title) in titles where !title.isEmpty {
                try run("INSERT INTO usage_session_titles VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title WHERE title<>excluded.title",[id,String(title.prefix(400))])
            }
        }
    }
}
