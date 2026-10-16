package backend

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/base64"
	"errors"
	"io"
	"os"
	"sort"
	"strings"
)

func (s *Service) GetClientFilters(clientKind string) Row {
	r, e := s.store.ClientFilters(clientKind)
	if e != nil {
		return Row{"models": []string{}, "tiers": []string{}, "error": e.Error()}
	}
	r["client_kind"] = normalizedClient(clientKind)
	if normalizedClient(clientKind) == "claude_code" {
		r["tiers"] = []string{}
		r["statuses"] = []string{"completed", "failed", "aborted", "unknown"}
	}
	return r
}
func (s *Service) GetClientChatRanking(clientKind, metric string, page int) (Row, error) {
	client := normalizedClient(clientKind)
	if client == "codex" {
		return s.GetChatRanking(metric, page), nil
	}
	q := Query{ClientKind: client, Period: "all", Source: "local", Page: max(1, page), PageSize: 25}
	key, cached, ok := s.store.cachedQuery("client-chat-ranking:"+metric+":"+dataJSON(q.Page), q)
	if ok {
		return cached, nil
	}
	rows, total, index, e := s.store.localChatRowsSorted(q, q.Page, 25, metric)
	if e != nil {
		return nil, e
	}
	result := Row{"client_kind": client, "threads": rows, "page": index, "pages": max(1, (total+24)/25), "total": total, "data_as_of": s.store.ClientStatus(client)["updated_at"]}
	s.store.cacheQuery(key, result)
	return result, nil
}
func (s *Store) rebuildClaudeGroups(tx *sql.Tx, dirty map[string]bool, full bool) error {
	// Only changed request IDs are materialized. The immutable calls and request
	// metadata remain on disk; each group streams its member calls.
	var query string
	if full {
		query = "SELECT id FROM claude_requests"
	} else {
		query = `SELECT item_id AS id FROM usage_query_changes WHERE kind='claude_request' UNION SELECT json_extract(data,'$.claude_request_id') FROM usage_records WHERE id IN(SELECT item_id FROM usage_query_changes WHERE kind='record') AND json_extract(data,'$.client_kind')='claude_code' UNION SELECT id FROM claude_requests WHERE session_id IN(SELECT item_id FROM usage_query_changes WHERE kind='title')`
	}
	lastRequest := ""
	var e error
	for {
		rows, err := tx.Query("SELECT request_id FROM (SELECT id AS request_id FROM ("+query+")) WHERE request_id>? ORDER BY request_id LIMIT 250", lastRequest)
		if err != nil {
			return err
		}
		ids := []string{}
		for rows.Next() {
			var id sql.NullString
			if err = rows.Scan(&id); err != nil {
				rows.Close()
				return err
			}
			if id.Valid && id.String != "" {
				ids = append(ids, id.String)
			}
		}
		rows.Close()
		if len(ids) == 0 {
			break
		}
		for _, id := range ids {
			lastRequest = id
			group := claudeGetRequest(tx, id)
			if len(group) == 0 {
				continue
			}
			if _, e = tx.Exec("DELETE FROM usage_request_members WHERE request_id=?", id); e != nil {
				return e
			}
			group["context_input_tokens"] = nil
			sum := newMetricSum()
			models := map[string]bool{}
			returnedModels := map[string]bool{}
			tiers := map[string]bool{}
			estimated := false
			sources := map[string]bool{}
			subagents := map[string]bool{}
			calls := 0
			outputsComplete := true
			mainTiming := []Row{}
			timingPending := false
			callRows, err := tx.Query("SELECT data FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.claude_request_id')=? ORDER BY timestamp,id", id)
			if err != nil {
				return err
			}

			for callRows.Next() {
				var raw string
				if err = callRows.Scan(&raw); err != nil {
					callRows.Close()
					return err
				}
				r := dataRow(raw)
				sum.add(r)
				tiers[dataString(r, "service_tier")] = true
				estimated = estimated || dataString(r, "pricing_status") == "estimated"
				if !ValueBool(r["is_subagent"]) && dataString(r, "claude_agent_id") == "" {
					timingPending = timingPending || !ValueBool(r["call_timing_checked"]) || ValueBool(r["call_timing_incomplete"])
					mainTiming = append(mainTiming, r)
					sort.SliceStable(mainTiming, func(i, j int) bool { return claudeCallStart(mainTiming[i]).After(claudeCallStart(mainTiming[j])) })
					if len(mainTiming) > 2 {
						mainTiming = mainTiming[:2]
					}
					if _, ok := knownCount(r["context_input_tokens"]); ok {
						group["context_input_tokens"] = r["context_input_tokens"]
					}
					if dataString(group, "model") == "" {
						group["model"] = r["requested_model"]
					}
					if dataString(group, "reasoning_effort") == "" {
						group["reasoning_effort"] = r["reasoning_effort"]
					}
				}
				calls++
				if model := dataString(r, "model"); model != "" {
					models[model] = true
				}
				if returned := dataString(r, "returned_model"); returned != "" {
					returnedModels[returned] = true
					models[returned] = true
				}
				sources[dataString(r, "source_id")] = true
				if _, ok := knownCount(r["output_tokens"]); !ok {
					outputsComplete = false
				}
				if ValueBool(r["is_subagent"]) {
					subagents[dataString(r, "session_id")] = true
				}
				if !ValueBool(r["is_subagent"]) && dataString(r, "output_preview") != "" {
					group["output_preview"] = r["output_preview"]
				}
			}
			err = callRows.Err()
			callRows.Close()
			if err != nil {
				return err
			}
			if _, e = tx.Exec("INSERT OR IGNORE INTO usage_request_members SELECT ?,id FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.claude_request_id')=?", id, id); e != nil {
				return e
			}
			metrics := sum.row()
			for _, k := range []string{"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "cache_hit_rate"} {
				group[k] = metrics[k]
			}
			group["total_tokens"] = metrics["tokens"]
			group["cost_usd"] = metrics["usd"]
			if !ValueBool(metrics["cost_complete"]) {
				group["cost_usd"] = nil
			}
			group["pricing_status"] = "priced"
			if estimated {
				group["pricing_status"] = "estimated"
			}
			if group["cost_usd"] == nil {
				group["pricing_status"] = "unpriced"
			}
			group["service_tier"] = nil
			group["service_tiers"] = sortedKeys(tiers)
			if !tiers[""] && len(tiers) > 0 {
				if len(tiers) == 1 {
					group["service_tier"] = sortedKeys(tiers)[0]
				} else {
					group["service_tier"] = "mixed"
				}
			}
			group["call_count"] = calls
			group["models"] = sortedKeys(models)
			group["upstream_models"] = sortedKeys(returnedModels)
			group["upstream_mismatched"] = false
			group["source_ids"] = sortedKeys(sources)
			group["subagent_count"] = len(subagents)
			group["is_subagent"] = false
			group["tps"] = nil

			group["ttft"] = nil
			group["model_context_window"] = nil
			if calls == 0 {
				group["source_ids"] = []string{dataString(group, "source_id")}
			}
			if dataString(group, "reasoning_effort") != "" {
				group["reasoning_effort"] = requestEffortName(group["reasoning_effort"])
			}
			if timingPending && len(mainTiming) > 0 {
				mainTiming[0]["call_timing_incomplete"] = true
			}
			for key, value := range claudeRequestTiming(group, mainTiming) {
				group[key] = value
			}
			if duration, ok := ValueFloat(group["duration_ms"]); ok && duration > 0 && outputsComplete && dataString(group, "request_status") == "completed" {
				if output, ok := ValueFloat(group["output_tokens"]); ok {
					group["tps"] = output / (duration / 1000)
				}
			}
			var title string
			_ = tx.QueryRow("SELECT title FROM usage_session_titles WHERE session_id=?", group["session_id"]).Scan(&title)
			if title != "" {
				group["session_title"] = title
			}
			if e = saveGroup(tx, group); e != nil {
				return e
			}
		}
		if len(ids) < 250 {
			break
		}
	}
	// Materialize calls lacking explicit human ownership as unassigned calls.
	last := ""
	for {
		query := `SELECT data FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')='claude_code' AND id>?`
		if !full {
			query += ` AND id IN(SELECT item_id FROM usage_query_changes WHERE kind='record')`
		}
		query += " ORDER BY id LIMIT 250"
		records, err := queryJSON(tx, query, last)
		if err != nil {
			return err
		}
		if len(records) == 0 {
			break
		}
		for _, r := range records {
			last = dataString(r, "id")
			request := dataString(r, "claude_request_id")
			unassigned := "unassigned:" + last
			if request != "" {
				if _, e = tx.Exec("DELETE FROM usage_request_groups WHERE id=?", unassigned); e != nil {
					return e
				}
				if _, e = tx.Exec("DELETE FROM usage_request_members WHERE request_id=?", unassigned); e != nil {
					return e
				}
				continue
			}
			r["id"] = unassigned
			r["record_kind"] = "unassigned"
			r["call_count"] = 1
			r["request_status"] = "unknown"
			r["source_ids"] = []string{dataString(r, "source_id")}
			if e = saveGroup(tx, r); e != nil {
				return e
			}
			if _, e = tx.Exec("INSERT OR IGNORE INTO usage_request_members VALUES(?,?)", unassigned, last); e != nil {
				return e
			}
		}
		if len(records) < 250 {
			break
		}
	}
	// Codex roots are rebuilt independently. Only explicit child memberships
	// connect Claude sessions to a human host for ranking.
	if _, e = tx.Exec(`INSERT OR REPLACE INTO usage_query_session_roots SELECT DISTINCT session_id,session_id FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')='claude_code'`); e != nil {
		return e
	}
	_, e = tx.Exec(`UPDATE usage_query_session_roots AS roots SET root_session_id=(SELECT q.session_id FROM usage_priced_calls p JOIN claude_requests q ON q.id=json_extract(p.data,'$.claude_request_id') WHERE p.session_id=roots.session_id AND json_extract(p.data,'$.is_subagent')=1 LIMIT 1) WHERE EXISTS(SELECT 1 FROM usage_priced_calls p JOIN claude_requests q ON q.id=json_extract(p.data,'$.claude_request_id') WHERE p.session_id=roots.session_id AND json_extract(p.data,'$.is_subagent')=1) AND (SELECT count(DISTINCT q.session_id) FROM usage_priced_calls p JOIN claude_requests q ON q.id=json_extract(p.data,'$.claude_request_id') WHERE p.session_id=roots.session_id AND json_extract(p.data,'$.is_subagent')=1)=1`)
	return e
}
func (s *Store) claudeLongestDuration(q Query) (any, bool, error) {
	where, args := queryFilter(q, true)
	rows, e := s.db.Query("SELECT sum(json_extract(data,'$.duration_ms')),sum(CASE WHEN json_extract(data,'$.duration_ms') IS NULL THEN 1 ELSE 0 END) FROM usage_request_groups WHERE record_kind='user_request' AND "+where+" GROUP BY json_extract(data,'$.session_id')", args...)
	if e != nil {
		return nil, false, e
	}
	defer rows.Close()
	var longest any
	partial := false
	for rows.Next() {
		var duration sql.NullFloat64
		var missing int
		if e = rows.Scan(&duration, &missing); e != nil {
			return nil, false, e
		}
		partial = partial || missing > 0
		if duration.Valid && missing == 0 {
			seconds := duration.Float64 / 1000
			if old, ok := ValueFloat(longest); !ok || seconds > old {
				longest = seconds
			}
		}
	}
	return longest, partial, rows.Err()
}
func (s *Store) claudeSourceFields(rows []Row) {
	for _, r := range rows {
		if normalizedClient(dataString(r, "client_kind")) != "claude_code" {
			continue
		}
		var available int
		_ = s.db.QueryRow("SELECT EXISTS(SELECT 1 FROM claude_locators WHERE item_id=? AND available=1)", strings.TrimPrefix(dataString(r, "id"), "unassigned:")).Scan(&available)
		r["source_exists"] = available > 0
		r["source_revision"] = s.Generation()
	}
}
func (s *Store) claudeReadLocator(locator Row) (Row, error) {
	path := dataString(locator, "path")
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	if ledgerFileIdentity(f) != dataString(locator, "identity") {
		return nil, errors.New("original source replaced")
	}
	_, e = f.Seek(ValueInt(locator["offset"]), io.SeekStart)
	if e != nil {
		return nil, e
	}
	line, e := readBoundedLine(bufio.NewReaderSize(f, 65536), rolloutLineLimit)
	if e != nil {
		return nil, e
	}
	if HashString(string(line)) != dataString(locator, "line_hash") {
		return nil, errors.New("original source changed")
	}
	entry, e := DecodeRow(line)
	if e != nil || dataString(entry, "uuid") != dataString(locator, "uuid") {
		return nil, errors.New("original source missing")
	}
	return entry, nil
}
func (s *Store) claudeLocators(item string, limit int) ([]Row, error) {
	rows, e := s.db.Query("SELECT uuid,path,offset,identity,line_hash FROM claude_locators WHERE item_id=? AND available=1 ORDER BY path,offset LIMIT ?", item, limit)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	result := []Row{}
	for rows.Next() {
		var uuid, path, identity, hash string
		var offset int64
		if e = rows.Scan(&uuid, &path, &offset, &identity, &hash); e != nil {
			return nil, e
		}
		result = append(result, Row{"uuid": uuid, "path": path, "offset": offset, "identity": identity, "line_hash": hash})
	}
	return result, rows.Err()
}
func (s *Store) claudeDetail(request Row, call bool, page int) (Row, error) {
	id := dataString(request, "id")
	// Full bodies are recovered on demand from verified live locators. No cached
	// body survives source deletion; prompt_preview remains solely a summary.
	result := Row{"request": CloneRow(request), "user": nil, "final": nil, "attachments": []Row{}, "images": []Row{}, "members": []Row{}, "composition": []Row{}, "page": max(1, page), "pages": 1, "user_complete": false, "final_complete": false, "availability": "unavailable", "source_exists": false, "source_revision": s.Generation()}
	item := id
	if strings.HasPrefix(id, "unassigned:") {
		item = strings.TrimPrefix(id, "unassigned:")
		call = true
	}
	locators, e := s.claudeLocators(item, 513)
	if e != nil {
		return nil, e
	}
	userUUID := dataString(request, "user_uuid")
	if call && dataString(request, "claude_request_id") != "" {
		owner := claudeGetRequestDB(s.db, dataString(request, "claude_request_id"))
		userUUID = dataString(owner, "user_uuid")
		userLocators, err := s.claudeLocators(dataString(owner, "id"), 513)
		if err != nil {
			return nil, err
		}
		for _, l := range userLocators {
			if dataString(l, "uuid") == userUUID {
				locators = append([]Row{l}, locators...)
				break
			}
		}
	}
	seen := map[string]bool{}
	userText := []string{}
	finalText := []string{}
	attachments := []Row{}
	images := []Row{}
	complete := len(locators) <= 512
	bodyBytes := 0
	finalResponse := ""
	if len(locators) > 512 {
		locators = locators[:512]
	}
	for _, locator := range locators {
		uuid := dataString(locator, "uuid")
		if seen[uuid] {
			continue
		}
		entry, err := s.claudeReadLocator(locator)
		if err != nil {
			complete = false
			continue
		}
		seen[uuid] = true
		result["source_exists"] = true
		message := ValueRow(entry["message"])
		text, textComplete := claudeText(message["content"])
		complete = complete && textComplete
		bodyBytes += len(text)
		if bodyBytes > 1<<20 {
			complete = false
			break
		}
		if dataString(entry, "type") == "user" && uuid == userUUID {
			userText = append(userText, text)
			result["user_complete"] = textComplete
		}
		if dataString(entry, "type") == "assistant" {
			response := dataString(message, "id")
			if response != "" && response != finalResponse {
				finalText = nil
				finalResponse = response
			}
			if text != "" {
				finalText = append(finalText, text)
			}
		}
		for index, part := range ValueRows(message["content"]) {
			if dataString(part, "type") != "image" && dataString(part, "type") != "document" {
				continue
			}
			source := ValueRow(part["source"])
			media := firstString(source["media_type"], part["media_type"])
			name := firstString(part["title"], part["name"], "附件")
			attachment := Row{"id": HashString(uuid + ":" + dataJSON(index)), "name": name, "content_type": media, "mime": media, "available": dataString(source, "type") == "base64" && dataString(source, "data") != "", "kind": dataString(part, "type")}
			if len(attachments)+len(images) < requestMediaLimit {
				if strings.HasPrefix(media, "image/") {
					placement := "user"
					if dataString(entry, "type") == "assistant" {
						placement = "final"
					}
					image := CloneRow(attachment)
					image["placement"] = placement
					image["availability"] = "pending"
					images = append(images, image)
					markdown := "\n![" + name + "](codexio-image://" + dataString(image, "id") + ")"
					if placement == "final" {
						finalText = append(finalText, markdown)
					} else {
						userText = append(userText, markdown)
					}
				} else {
					attachments = append(attachments, attachment)
				}
			} else {
				complete = false
			}
		}
	}
	if len(userText) > 0 {
		result["user"] = strings.Join(userText, "\n")
	}
	if len(finalText) > 0 {
		result["final"] = strings.Join(finalText, "\n")
		result["final_complete"] = complete
	}
	result["attachments"] = attachments
	result["images"] = images
	delete(ValueRow(result["request"]), "output_preview")
	if ValueBool(result["source_exists"]) {
		result["availability"] = "partial"
		if complete && ValueBool(result["user_complete"]) && ValueBool(result["final_complete"]) {
			result["availability"] = "available"
		}
	}
	if call {
		member := CloneRow(request)
		delete(member, "output_preview")
		result["members"] = []Row{member}
		result["total_members"] = 1
		return result, nil
	}
	var count int
	if e = s.db.QueryRow("SELECT count(*) FROM usage_request_members WHERE request_id=?", id).Scan(&count); e != nil {
		return nil, e
	}
	pages := max(1, (count+49)/50)
	page = min(max(1, page), pages)
	rows, e := s.db.Query("SELECT p.data FROM usage_priced_calls p JOIN usage_request_members m ON m.record_id=p.id WHERE m.request_id=? ORDER BY p.timestamp,p.id LIMIT 50 OFFSET ?", id, (page-1)*50)
	if e != nil {
		return nil, e
	}
	members := []Row{}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			rows.Close()
			return nil, e
		}
		member := dataRow(raw)
		delete(member, "output_preview")
		members = append(members, member)
	}
	rows.Close()
	result["members"] = members
	result["total_members"] = count
	result["pages"] = pages
	result["page"] = page
	return result, nil
}
func (s *Store) claudeImage(ctx context.Context, requestID, imageID string) (Row, error) {
	item := strings.TrimPrefix(requestID, "unassigned:")
	locators, e := s.claudeLocators(item, 512)
	if e != nil {
		return nil, e
	}
	var raw string
	_ = s.db.QueryRow("SELECT data FROM usage_priced_calls WHERE id=?", item).Scan(&raw)
	if ownerID := dataString(dataRow(raw), "claude_request_id"); ownerID != "" {
		owner := claudeGetRequestDB(s.db, ownerID)
		userLocators, err := s.claudeLocators(ownerID, 512)
		if err != nil {
			return nil, err
		}
		for _, l := range userLocators {
			if dataString(l, "uuid") == dataString(owner, "user_uuid") {
				locators = append(locators, l)
				break
			}
		}
	}
	for _, locator := range locators {
		if e = ctx.Err(); e != nil {
			return nil, e
		}
		entry, err := s.claudeReadLocator(locator)
		if err != nil {
			continue
		}
		for index, part := range ValueRows(ValueRow(entry["message"])["content"]) {
			if HashString(dataString(entry, "uuid")+":"+dataJSON(index)) != imageID {
				continue
			}
			source := ValueRow(part["source"])
			media := dataString(source, "media_type")
			encoded := dataString(source, "data")
			if !strings.HasPrefix(media, "image/") || dataString(source, "type") != "base64" || len(encoded) > 16<<20 {
				return Row{"availability": "unavailable", "id": imageID}, nil
			}
			decoded, err := base64.StdEncoding.DecodeString(encoded)
			if err != nil || len(decoded) > 12<<20 {
				return Row{"availability": "unavailable", "id": imageID}, nil
			}
			return Row{"id": imageID, "availability": "available", "dataURL": "data:" + media + ";base64," + encoded, "mime": media, "sha256": HashString(string(decoded))}, nil
		}
	}
	return Row{"availability": "unavailable", "id": imageID}, nil
}

func claudeGetRequestDB(db *sql.DB, id string) Row {
	var raw string
	_ = db.QueryRow("SELECT data FROM claude_requests WHERE id=?", id).Scan(&raw)
	return dataRow(raw)
}
