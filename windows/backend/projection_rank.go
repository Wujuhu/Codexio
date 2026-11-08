package backend

import (
	"database/sql"
	"strings"
	"time"
)

// As in v0.2.10 and the Mac ledger, page the committed local totals; details
// and arbitrary filters continue to use the existing per-call SQL.
const rankRoot = "coalesce(roots.root_session_id,p.session_id)"
const rankClient = "coalesce(json_extract(p.data,'$.client_kind'),'codex')"
const rankColumns = `coalesce(roots.root_session_id,p.session_id),coalesce(t.title,max(CASE WHEN roots.root_session_id=p.session_id OR roots.root_session_id IS NULL THEN json_extract(p.data,'$.session_title') END),''),CASE WHEN coalesce(json_extract(p.data,'$.client_kind'),'codex')='claude_code' AND count(json_extract(p.metrics,'$.tokens'))<>count(*) THEN NULL ELSE sum(json_extract(p.metrics,'$.tokens')) END,CASE WHEN coalesce(json_extract(p.data,'$.client_kind'),'codex')='claude_code' AND count(json_extract(p.metrics,'$.usd'))<>count(*) THEN NULL ELSE sum(json_extract(p.metrics,'$.usd')) END,sum(coalesce(json_extract(p.metrics,'$.unpriced_calls'),0)),sum(CASE WHEN json_extract(p.data,'$.client_kind')='claude_code' AND (coalesce(json_extract(p.data,'$.usage_complete'),0)=0 OR json_extract(p.data,'$.usage_conflict')=1) THEN 1 ELSE 0 END)`
const rankFrom = ` FROM usage_priced_calls p LEFT JOIN usage_query_session_roots roots ON roots.session_id=p.session_id LEFT JOIN usage_session_titles t ON t.session_id=coalesce(roots.root_session_id,p.session_id)`
const rankLocalCalls = `EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=p.id AND (s.source_id='local' OR s.source_id LIKE 'local:%'))`
const rankLocalRequests = `EXISTS(SELECT 1 FROM json_each(json_extract(g.data,'$.source_ids')) WHERE value='local' OR value LIKE 'local:%')`

func plainUsageFilter(q Query) bool {
	return (q.Model == "" || q.Model == "all") && (q.Tier == "" || q.Tier == "all") &&
		(q.Status == "" || q.Status == "all") && strings.TrimSpace(q.Search) == ""
}

func rebuildLocalRank(tx *sql.Tx, scope *projectionScope) error {
	filter, args := scope.filter("session_id")
	if _, err := tx.Exec("DELETE FROM usage_query_local_rank WHERE 1"+filter, args...); err != nil {
		return err
	}
	rootFilter, rootArgs := scope.filter("r.root_session_id")
	callFilter, callArgs := scope.filter("p.session_id")
	// Count requests once per root and client. A correlated count can scan
	// the whole request ledger again for every root during a cold rebuild.
	_, err := tx.Exec(`WITH request_counts AS (
		SELECT r.root_session_id AS session_id,coalesce(json_extract(g.data,'$.client_kind'),'codex') AS client,count(*) AS requests
		FROM usage_request_groups g JOIN usage_query_session_roots r ON r.session_id=json_extract(g.data,'$.session_id')
		WHERE g.record_kind='user_request' AND g.is_subagent=0 AND `+rankLocalRequests+rootFilter+`
		GROUP BY 1,2
	)
	INSERT INTO usage_query_local_rank
	SELECT `+rankClient+`,`+rankColumns+`,coalesce(max(n.requests),0)`+rankFrom+`
	LEFT JOIN request_counts n ON n.session_id=`+rankRoot+` AND n.client=`+rankClient+`
	WHERE `+rankLocalCalls+callFilter+` GROUP BY `+rankClient+`,`+rankRoot, append(rootArgs, callArgs...)...)
	return err
}

func materializedLocalRank(reader queryReader, q Query, page, size int, metric string) ([]Row, int, int, bool, error) {
	if !plainUsageFilter(q) || (q.Period != "all" && q.Period != "all_time") || q.Start != "" || q.End != "" {
		return nil, 0, 0, false, nil
	}
	tx, err := reader.DB.BeginTx(reader.ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, 0, 0, false, err
	}
	defer tx.Rollback()
	var ready, future bool
	end := ledgerStamp(time.Now())
	if err = tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM usage_query_state WHERE key='go_projection' AND json_extract(data,'$.scope_version')=?),
        EXISTS(SELECT 1 FROM usage_priced_calls WHERE timestamp>=?) OR EXISTS(SELECT 1 FROM usage_request_groups WHERE timestamp>=?)`, projectionScopeVersion, end, end).Scan(&ready, &future); err != nil {
		return nil, 0, 0, false, err
	}
	if !ready || future {
		return nil, 0, 0, false, nil
	}
	client := normalizedClient(q.ClientKind)
	var total int
	if err = tx.QueryRow("SELECT count(*) FROM usage_query_local_rank WHERE client=?", client).Scan(&total); err != nil {
		return nil, 0, 0, true, err
	}
	page = min(max(1, page), max(1, (total+size-1)/size))
	order := "tokens"
	if metric == "cost_usd" {
		order = "usd"
	}
	rows, err := tx.Query("SELECT session_id,title,tokens,usd,unpriced,incomplete,requests FROM usage_query_local_rank WHERE client=? ORDER BY "+order+" DESC,session_id LIMIT ? OFFSET ?", client, size, (page-1)*size)
	if err != nil {
		return nil, 0, 0, true, err
	}
	chats, err := scanLocalRank(rows, client, true)
	return chats, total, page, true, err
}

func scanLocalRank(rows *sql.Rows, client string, storedRequests bool) ([]Row, error) {
	defer rows.Close()
	chats := []Row{}
	for rows.Next() {
		var session, title string
		var tokens sql.NullInt64
		var usd sql.NullFloat64
		var unpriced, incomplete int
		var requests int
		args := []any{&session, &title, &tokens, &usd, &unpriced, &incomplete}
		if storedRequests {
			args = append(args, &requests)
		}
		if err := rows.Scan(args...); err != nil {
			return nil, err
		}
		r := Row{"client_kind": client, "session_id": session, "thread_id": session, "session_title": title, "name": title, "title": title, "tokens": nil, "local_tokens": nil, "usd": nil, "cost_usd": nil, "cost_partial": unpriced > 0, "cost_complete": unpriced == 0}
		if unpriced > 0 {
			r["data_status"] = "partial"
		}
		if client == "claude_code" {
			if incomplete > 0 {
				r["data_status"] = "partial"
			}
		}
		if tokens.Valid {
			r["tokens"], r["local_tokens"] = tokens.Int64, tokens.Int64
		}
		if usd.Valid {
			r["usd"] = usd.Float64
			r["cost_usd"] = usd.Float64
		}
		if storedRequests {
			r["user_requests"] = requests
		}
		chats = append(chats, r)
	}
	return chats, rows.Err()
}
