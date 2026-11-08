package backend

import (
	"database/sql"
	"fmt"
	"strings"
)

const (
	projectionScopeVersion = 1
	projectionMaxChanges   = 256
	projectionMaxSessions  = 8
	projectionMaxCalls     = 4096
	projectionMaxTurns     = 1024
)

const codexTurnFilter = "coalesce(json_extract(data,'$.client_kind'),'codex')='codex'"
const projectionTurnSession = "json_extract(data,'$.session_id')"

// The same durable journal already drives per-call pricing. For the bounded
// path, also prove that its entries cover this ledger generation.
type projectionScope struct {
	sessions []string
	turns    []Row
}

func (s *projectionScope) filter(column string) (string, []any) {
	if s == nil {
		return "", nil
	}
	if len(s.sessions) == 0 {
		return " AND 0", nil
	}
	args := make([]any, len(s.sessions))
	for i, id := range s.sessions {
		args[i] = id
	}
	return " AND " + column + " IN (" + strings.Repeat("?,", len(args)-1) + "?)", args
}

func projectionJournalSize(tx *sql.Tx) (count int, known bool, err error) {
	var unsupported int
	err = tx.QueryRow(`SELECT count(*),coalesce(max(kind NOT IN ('record','turn','title','agent','claude_request')),0)
		FROM (SELECT kind FROM usage_query_changes LIMIT ?)`, projectionMaxChanges+1).Scan(&count, &unsupported)
	return count, count <= projectionMaxChanges && unsupported == 0, err
}

func readProjectionScope(tx *sql.Tx) (scope *projectionScope, err error) {
	var ambiguous bool
	err = tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM usage_query_changes WHERE kind='agent')
		OR EXISTS(SELECT 1 FROM usage_query_changes c JOIN usage_records r ON r.id=c.item_id WHERE c.kind='record'
		 AND (r.session_id IS NOT coalesce(json_extract(r.data,'$.session_id'),'') OR r.id IS NOT json_extract(r.data,'$.id')))
		OR EXISTS(SELECT 1 FROM usage_query_changes c JOIN usage_priced_calls p ON p.id=c.item_id
		 LEFT JOIN usage_records r ON r.id=c.item_id WHERE c.kind='record'
		 AND (coalesce(json_extract(p.data,'$.client_kind'),'codex') IS NOT coalesce(json_extract(r.data,'$.client_kind'),'codex')
		   OR json_extract(p.data,'$.claude_request_id') IS NOT json_extract(r.data,'$.claude_request_id')))
		OR EXISTS(SELECT 1 FROM usage_query_changes c LEFT JOIN usage_turns t ON t.id=c.item_id
		 WHERE c.kind='turn' AND t.id IS NULL)`).Scan(&ambiguous)
	if err != nil || ambiguous {
		return nil, err
	}
	// Read both ownerships before modifying the priced ledger. A call can move
	// threads without changing its durable response ID.
	rows, err := tx.Query(`WITH changes AS (SELECT DISTINCT kind,item_id FROM usage_query_changes),
		sessions(id) AS (
		  SELECT r.session_id FROM changes c JOIN usage_records r ON r.id=c.item_id WHERE c.kind='record'
		  UNION SELECT p.session_id FROM changes c JOIN usage_priced_calls p ON p.id=c.item_id WHERE c.kind='record'
		  UNION SELECT item_id FROM changes WHERE kind='title'
		  UNION SELECT json_extract(t.data,'$.session_id') FROM changes c JOIN usage_turns t ON t.id=c.item_id WHERE c.kind='turn'
		  UNION SELECT r.session_id FROM changes c JOIN claude_requests r ON r.id=c.item_id WHERE c.kind='claude_request'
		  UNION SELECT json_extract(g.data,'$.session_id') FROM changes c JOIN usage_request_groups g ON g.id=c.item_id WHERE c.kind='claude_request'
		) SELECT id,EXISTS(SELECT 1 FROM usage_query_unsafe_sessions u WHERE u.session_id=sessions.id) FROM sessions LIMIT ?`, projectionMaxSessions+1)
	if err != nil {
		return nil, err
	}
	var sessions []string
	safe := true
	for rows.Next() {
		var id sql.NullString
		var unsafe bool
		if err = rows.Scan(&id, &unsafe); err != nil {
			rows.Close()
			return nil, err
		}
		safe = safe && id.Valid && id.String != "" && !strings.Contains(id.String, ":") && !unsafe
		sessions = append(sessions, id.String)
	}
	err = rows.Err()
	rows.Close()
	if err != nil || !safe || len(sessions) == 0 || len(sessions) > projectionMaxSessions {
		return nil, err
	}
	scope = &projectionScope{sessions: sessions}
	guard, args := scope.filter("session_id")
	for _, table := range []string{"usage_records", "usage_priced_calls"} {
		var rows int
		err = tx.QueryRow("SELECT count(*) FROM (SELECT 1 FROM "+table+" WHERE 1"+guard+" LIMIT ?)", append(args, projectionMaxCalls+1)...).Scan(&rows)
		if err != nil || rows > projectionMaxCalls {
			return nil, err
		}
	}
	guard, args = scope.filter(projectionTurnSession)
	scope.turns, err = queryJSON(tx, "SELECT data FROM usage_turns WHERE "+codexTurnFilter+guard+" ORDER BY json_extract(data,'$.observed_at'),id LIMIT ?", append(args, projectionMaxTurns+1)...)
	if err != nil || len(scope.turns) > projectionMaxTurns || len(unsafeProjectionSessions(scope.turns, nil)) != 0 {
		return nil, err
	}
	return scope, nil
}

// Canonical references inside one thread can be resolved from its own rows.
// Keep explicit, inferred and currently dangling cross-thread edges in the
// full path, including their possible target when it arrives later.
func unsafeProjectionSessions(turns, links []Row) map[string]bool {
	blocked := map[string]bool{}
	for _, link := range links {
		blocked[dataString(link, "parent_session_id")] = true
		blocked[dataString(link, "child_session_id")] = true
	}
	for _, turn := range turns {
		session, parent := dataString(turn, "session_id"), dataString(turn, "parent_session_id")
		if ValueBool(turn["is_subagent"]) || parent != "" && parent != session {
			blocked[session], blocked[parent] = true, true
		}
		for _, key := range []string{"alias_of", "continuation_of", "root_turn_id", "parent_turn_id", "inherited_parent_turn_id"} {
			value := dataString(turn, key)
			if strings.HasPrefix(value, "turn:") && !strings.HasPrefix(value, "turn:"+session+":") {
				target, _, _ := strings.Cut(strings.TrimPrefix(value, "turn:"), ":")
				blocked[session], blocked[target] = true, true
			}
			if key == "alias_of" && value != "" && !strings.HasPrefix(value, "turn:") {
				blocked[session] = true
			}
		}
	}
	delete(blocked, "")
	return blocked
}

func rebuildUnsafeProjectionSessions(tx *sql.Tx, turns, links []Row) error {
	if _, err := tx.Exec("DELETE FROM usage_query_unsafe_sessions"); err != nil {
		return err
	}
	for session := range unsafeProjectionSessions(turns, links) {
		if _, err := tx.Exec("INSERT INTO usage_query_unsafe_sessions VALUES(?)", session); err != nil {
			return err
		}
	}
	return nil
}

// Unusual primary-key or metadata-owner updates cannot be recovered from the
// legacy journal's NEW key alone. Keep the legacy hints for pricing/recovery,
// adding an explicit fallback marker when that happens.
func (s *Store) installScopeGuards() error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// A migrated Python ledger can still have separate query and revision
	// triggers for these five tables. Go's combined triggers already cover
	// them. Retire the duplicates so an update logs one counted event. Keep
	// the observations revision trigger: it has no replacement here.
	for _, table := range []string{"usage_records", "usage_record_sources", "usage_session_titles", "usage_turns", "usage_agent_links"} {
		for _, action := range []string{"insert", "update", "delete"} {
			for _, prefix := range []string{"query_", "revision_"} {
				if _, err = tx.Exec("DROP TRIGGER IF EXISTS " + prefix + table + "_" + action); err != nil {
					return err
				}
			}
		}
	}
	for _, guard := range []struct{ table, condition string }{
		{"usage_records", "OLD.id IS NOT NEW.id"},
		{"usage_record_sources", "OLD.record_id IS NOT NEW.record_id"},
		{"usage_session_titles", "OLD.session_id IS NOT NEW.session_id"},
		{"usage_turns", "OLD.id IS NOT NEW.id OR json_extract(OLD.data,'$.session_id') IS NOT json_extract(NEW.data,'$.session_id')"},
		{"claude_requests", "OLD.id IS NOT NEW.id OR OLD.session_id IS NOT NEW.session_id"},
	} {
		statement := fmt.Sprintf("CREATE TRIGGER IF NOT EXISTS go_scope_%s_move AFTER UPDATE ON %s WHEN %s BEGIN INSERT INTO usage_query_changes(kind,item_id) VALUES('scope_full',''); END", guard.table, guard.table, guard.condition)
		if _, err = tx.Exec(statement); err != nil {
			return err
		}
	}
	_, err = tx.Exec(`CREATE TRIGGER IF NOT EXISTS go_scope_claude_request_delete AFTER DELETE ON claude_requests BEGIN
		UPDATE usage_revisions SET revision=revision+1 WHERE kind='ledger';
		INSERT INTO usage_query_changes(kind,item_id) VALUES('scope_full',OLD.id);
	END`)
	if err != nil {
		return err
	}
	return tx.Commit()
}
