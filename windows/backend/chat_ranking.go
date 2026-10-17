package backend

import (
	"context"
	"database/sql"
	"strconv"
	"strings"
)

// Reuse the v0.2.10-derived priced ledger, session roots and bounded query cache.
func (s *Service) GetChatRanking(ctx context.Context, metric string, page int) (Row, error) {
	ctx, cancel := s.readContext(ctx)
	defer cancel()
	if metric != "tokens" {
		metric = "cost_usd"
	}
	q := Query{Period: "all", Source: "local", Page: max(1, page), PageSize: 25}
	key, cached, ok := s.store.cachedQuery("chat-ranking:"+metric+":"+strconv.Itoa(q.Page), q)
	if ok {
		return cached, queryError(ctx, nil)
	}
	rows, total, index, err := s.store.localChatRowsSorted(q, q.Page, 25, metric, ctx)
	if err != nil {
		return nil, queryError(ctx, err)
	}
	result := Row{"threads": rows, "page": index, "pages": max(1, (total+24)/25), "total": total, "data_as_of": s.store.Status()["updated_at"]}
	if err := queryError(ctx, nil); err != nil {
		return nil, err
	}
	s.store.cacheQuery(key, result)
	return result, queryError(ctx, nil)
}

func (s *Service) GetChatRankingDetails(ctx context.Context, thread string) (Row, error) {
	return s.GetClientChatRankingDetails(ctx, "codex", thread)
}
func (s *Service) GetClientChatRankingDetails(ctx context.Context, clientKind, thread string) (Row, error) {
	ctx, cancel := s.readContext(ctx)
	defer cancel()
	q := Query{ClientKind: clientKind, Period: "all", Source: "local"}
	key, cached, ok := s.store.cachedQuery("chat-ranking-details:"+thread, q)
	if ok {
		return cached, queryError(ctx, nil)
	}
	where, args := queryFilter(q, false)
	where = strings.ReplaceAll(where, "usage_priced_calls.id", "p.id")
	args = append(args, thread)
	query := `SELECT p.model,coalesce(json_extract(p.data,'$.reasoning_effort'),''),coalesce(json_extract(p.data,'$.service_tier'),''),CASE WHEN coalesce(json_extract(p.data,'$.client_kind'),'codex')='claude_code' AND count(json_extract(p.metrics,'$.usd'))<>count(*) THEN NULL ELSE sum(json_extract(p.metrics,'$.usd')) END,CASE WHEN coalesce(json_extract(p.data,'$.client_kind'),'codex')='claude_code' AND count(json_extract(p.metrics,'$.tokens'))<>count(*) THEN NULL ELSE sum(json_extract(p.metrics,'$.tokens')) END FROM usage_priced_calls p LEFT JOIN usage_query_session_roots roots ON roots.session_id=p.session_id WHERE ` + where + ` AND coalesce(roots.root_session_id,p.session_id)=? GROUP BY 1,2,3 ORDER BY 1,2,3`
	records, err := s.store.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, queryError(ctx, err)
	}
	defer records.Close()
	groups := []Row{}
	for records.Next() {
		var model, effort, tier string
		var cost sql.NullFloat64
		var tokens sql.NullInt64
		if err = records.Scan(&model, &effort, &tier, &cost, &tokens); err != nil {
			return nil, queryError(ctx, err)
		}
		row := Row{"client_kind": normalizedClient(clientKind), "model": model, "reasoning_effort": effort, "speed": requestSpeed(tier), "cost_usd": nil, "tokens": nil}
		if cost.Valid {
			row["cost_usd"] = cost.Float64
		}
		if tokens.Valid {
			row["tokens"] = tokens.Int64
		}
		groups = append(groups, row)
	}
	if err = records.Err(); err != nil {
		return nil, queryError(ctx, err)
	}
	result := Row{"groups": groups}
	if err := queryError(ctx, nil); err != nil {
		return nil, err
	}
	s.store.cacheQuery(key, result)
	return result, nil
}
