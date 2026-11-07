import Foundation

// The snapshot is in memory, so its change journal can share the writer's
// lifetime. As in v0.2.10 usage_queries.py, record both sides of an update and
// drain only after the corresponding projections have been written.
struct UsageChangeJournal {
    let database: Database
    private static let limit = 256

    init(database: Database) throws {
        self.database = database
        try database.script("""
        CREATE TEMP TABLE IF NOT EXISTS usage_projection_state(
            id INTEGER PRIMARY KEY CHECK(id=1), mutations INTEGER NOT NULL, needs_full INTEGER NOT NULL);
        INSERT OR IGNORE INTO usage_projection_state VALUES(1,0,0);
        CREATE TEMP TABLE IF NOT EXISTS usage_projection_dirty(
            kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,value)) WITHOUT ROWID;
        """)
        let sources = [
            ("usage_records", "record", "{row}.id"),
            ("usage_record_sources", "record", "{row}.record_id"),
            ("usage_turns", "session", "coalesce(json_extract({row}.data,'$.session_id'),'')"),
            ("usage_session_titles", "session", "{row}.session_id"),
            ("usage_session_context", "session", "{row}.session_id"),
            ("usage_agent_links", "full", "''")
        ]
        for (table, kind, expression) in sources {
            for (event, rows) in [("INSERT", ["NEW"]), ("UPDATE", ["OLD","NEW"]), ("DELETE", ["OLD"])] {
                let body: String
                if kind == "full" {
                    body = "UPDATE usage_projection_state SET mutations=mutations+1,needs_full=1;"
                } else {
                    let inserts = rows.map { row in
                        let value = expression.replacingOccurrences(of:"{row}",with:row)
                        return """
                        INSERT INTO usage_projection_dirty
                        SELECT '\(kind)',\(value) FROM usage_projection_state WHERE needs_full=0
                        ON CONFLICT(kind,value) DO NOTHING;
                        """
                    }.joined(separator:"\n")
                    body = """
                    UPDATE usage_projection_state SET mutations=mutations+1;
                    \(inserts)
                    UPDATE usage_projection_state SET needs_full=1 WHERE needs_full=0
                      AND EXISTS(SELECT 1 FROM usage_projection_dirty LIMIT 1 OFFSET \(Self.limit));
                    """
                }
                try database.script("CREATE TEMP TRIGGER IF NOT EXISTS projection_\(table)_\(event) AFTER \(event) ON main.\(table) BEGIN \(body) END")
            }
        }
    }

    // The ledger revision triggers cover the same six tables. A missing event
    // (including a write on another connection) forces a fresh full projection.
    func changes(expected: Int) throws -> [Object]? {
        guard expected > 0,
              let state = try database.query("SELECT mutations,needs_full FROM usage_projection_state WHERE id=1").first,
              state.integer("mutations") == expected, state.integer("needs_full") == 0 else { return nil }
        let rows = try database.query("SELECT kind,value FROM usage_projection_dirty LIMIT ?",[Self.limit+1])
        return rows.isEmpty || rows.count > Self.limit ? nil : rows
    }

    func clear() throws {
        try database.run("DELETE FROM usage_projection_dirty")
        try database.run("UPDATE usage_projection_state SET mutations=0,needs_full=0 WHERE id=1")
    }
}
