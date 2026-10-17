package backend

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// Reuse the Codex journey presentation and bounded source reader. Ownership is
// resolved by indexed UUID ancestry, never by time or matching prompt text.
func (s *Service) GetClientJourneyCatalog(client, project, before string) (Row, error) {
	if normalizedClient(client) != "claude_code" {
		return s.GetJourneyCatalog(project, before)
	}
	cursor := dataRow(before)
	stamp := ValueString(cursor["stamp"])
	last := ValueString(cursor["id"])
	if stamp == "" {
		stamp = "9999"
		last = "~"
	}
	owner := `coalesce(nullif(json_extract(q.data,'$.projectId'),''),'chats')`
	projects, e := journeyRows(s.store.db, `SELECT `+owner+` AS id,coalesce(nullif(json_extract(q.data,'$.projectName'),''),'聊天') AS name FROM claude_requests q GROUP BY 1`)
	if e != nil {
		return nil, e
	}
	hasChats := false
	for _, p := range projects {
		if ValueString(p["id"]) == "chats" {
			hasChats = true
		}
	}
	if !hasChats {
		projects = append([]Row{{"id": "chats", "name": "聊天"}}, projects...)
	}
	filter := ""
	args := []any{stamp, stamp, last}
	if project != "" {
		filter = " AND project_id=?"
		args = append(args, project)
	}
	records, e := journeyRows(s.store.db, `SELECT * FROM (SELECT q.session_id AS id,coalesce(nullif(t.title,''),json_extract(q.data,'$.prompt_preview'),'—') AS title,`+owner+` AS project_id,max(json_extract(q.data,'$.timestamp')) AS stamp FROM claude_requests q LEFT JOIN usage_session_titles t ON t.session_id=q.session_id GROUP BY q.session_id) WHERE (stamp<? OR (stamp=? AND id<?))`+filter+` ORDER BY stamp DESC,id DESC LIMIT 51`, args...)
	if e != nil {
		return nil, e
	}
	next := ""
	if len(records) > 50 {
		records = records[:50]
		r := records[len(records)-1]
		next = dataJSON(Row{"stamp": r["stamp"], "id": r["id"]})
	}
	return Row{"projects": projects, "threads": records, "next": next}, nil
}
func (s *Service) claudeJourneyThread(thread string, page int) (Row, error) {
	where, args := queryFilter(Query{ClientKind: "claude_code", Period: "all", Source: "local"}, true)
	where += ` AND json_extract(data,'$.session_id')=? AND record_kind='user_request' AND is_subagent=0`
	args = append(args, thread)
	var count int
	if e := s.store.db.QueryRow("SELECT count(*) FROM usage_request_groups WHERE "+where, args...).Scan(&count); e != nil {
		return nil, e
	}
	pages := max(1, (count+24)/25)
	page = min(max(1, page), pages)
	records, e := journeyRows(s.store.db, "SELECT json_remove(data,'$.composition') AS data FROM usage_request_groups WHERE "+where+" ORDER BY timestamp DESC,id DESC LIMIT 25 OFFSET ?", append(args, (page-1)*25)...)
	if e != nil {
		return nil, e
	}
	requests := []Row{}
	for _, r := range records {
		requests = append(requests, dataRow(ValueString(r["data"])))
	}
	sums, e := journeyRows(s.store.db, `SELECT CASE WHEN count(json_extract(metrics,'$.tokens'))=count(*) THEN sum(json_extract(metrics,'$.tokens')) END AS tokens,CASE WHEN count(json_extract(metrics,'$.usd'))=count(*) THEN sum(json_extract(metrics,'$.usd')) END AS cost_usd,count(*) AS calls FROM usage_priced_calls WHERE session_id=?`, thread)
	if e != nil {
		return nil, e
	}
	sum := Row{"requests": count}
	if len(sums) > 0 {
		for k, v := range sums[0] {
			sum[k] = v
		}
	}
	return Row{"requests": requests, "summary": sum, "page": page, "pages": pages}, nil
}
func (s *Service) claudeJourneyFlow(request, child, cursor string) (Row, error) {
	db := s.store.db
	j := s.journeyReader()
	j.mu.Lock()
	defer j.mu.Unlock()
	spans, e := journeyRows(db, `SELECT l.path,min(l.offset) AS begin,max(l.offset) AS finish,l.identity,l.uuid,l.line_hash,l.offset FROM claude_locators l WHERE l.available=1 AND l.item_id IN(SELECT id FROM usage_records WHERE json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.claude_request_id')=? UNION SELECT ?) GROUP BY l.path,l.identity ORDER BY min(l.offset),l.path LIMIT 128`, request, request)
	if e != nil {
		return nil, e
	}
	signature := fmt.Sprint(s.store.Generation())
	for _, r := range spans {
		signature += journeyStamp(ValueString(r["path"]))
	}
	key := "claude|" + request + "|" + child + "|" + cursor
	if old, ok := j.cache[key]; ok && old.signature == signature {
		return old.value, nil
	}
	pos := dataRow(cursor)
	index := max(0, int(ValueInt(pos["range"])))
	offset := ValueInt(pos["offset"])
	lastModel := ValueString(pos["model"])
	events := []Row{}
	read := int64(0)
	ns := strings.Split(request, "request:")[0]
	for index < len(spans) && len(events) < 120 && read < 8<<20 {
		span := spans[index]
		path := ValueString(span["path"])
		if _, err := s.store.claudeReadLocator(span); err != nil {
			index++
			offset = 0
			continue
		}
		f, err := os.Open(path)
		if err != nil {
			index++
			offset = 0
			continue
		}
		stat, err := f.Stat()
		if err != nil {
			f.Close()
			index++
			offset = 0
			continue
		}
		begin := ValueInt(span["begin"])
		end := stat.Size()
		var next int64
		_ = db.QueryRow(`SELECT min(l.offset) FROM claude_locators l JOIN claude_requests q ON q.id=l.item_id WHERE l.path=? AND l.available=1 AND l.offset>? AND q.id<>? AND l.uuid=json_extract(q.data,'$.user_uuid')`, path, ValueInt(span["finish"]), request).Scan(&next)
		if next > begin {
			end = min(end, next)
		}
		offset = max(begin, offset)
		if offset >= end {
			f.Close()
			index++
			offset = 0
			continue
		}
		_, err = f.Seek(offset, io.SeekStart)
		if err != nil {
			f.Close()
			return nil, err
		}
		reader := bufio.NewReader(io.LimitReader(f, end-offset))
		for offset < end && len(events) < 120 && read < 8<<20 {
			line, n, err := journeyReadLine(reader)
			offset += n
			read += n
			if n == 0 {
				offset = end
				break
			}
			if err != nil && err != io.EOF {
				f.Close()
				return nil, err
			}
			row := dataRow(string(line))
			uuid := ValueString(row["uuid"])
			var owner string
			_ = db.QueryRow("SELECT request_id FROM claude_nodes WHERE id=?", ns+"uuid:"+uuid).Scan(&owner)
			agent := ValueString(row["agentId"])
			side := agent != "" || ValueBool(row["isSidechain"]) || strings.Contains(filepath.ToSlash(path), "/subagents/")
			if owner == request && ((child == "" && !side) || (child != "" && (agent == child || strings.TrimSuffix(filepath.Base(path), ".jsonl") == "agent-"+child))) {
				message := ValueRow(row["message"])
				var raw string
				_ = db.QueryRow(`SELECT data FROM usage_priced_calls WHERE json_extract(data,'$.claude_request_id')=? AND json_extract(data,'$.response_id')=? AND coalesce(json_extract(data,'$.claude_agent_id'),'')=? LIMIT 1`, request, ValueString(message["id"]), agent).Scan(&raw)
				events = append(events, claudeJourneyEvents(row, dataRow(raw), &lastModel)...)
			}
		}
		f.Close()
		if offset >= end {
			index++
			offset = 0
			lastModel = ""
		}
	}
	next := ""
	if index < len(spans) {
		next = dataJSON(Row{"range": index, "offset": offset, "model": lastModel})
	}
	result := Row{"events": events, "next": next}
	j.put(key, signature, result)
	return result, nil
}
func claudeJourneyEvents(row, meter Row, last *string) []Row {
	message := ValueRow(row["message"])
	uuid := ValueString(row["uuid"])
	events := []Row{}
	event := func(id, kind string, extra Row) Row {
		r := Row{"id": id, "kind": kind, "time": row["timestamp"], "status": "", "files": []Row{}}
		for k, v := range extra {
			r[k] = v
		}
		return r
	}
	if ValueString(row["type"]) == "assistant" {
		response := ValueString(message["id"])
		if response != "" && response != *last {
			*last = response
			events = append(events, event(response, "model", Row{"model": firstString(meter["returned_model"], meter["upstream_model"], message["model"]), "effort": meter["reasoning_effort"], "speed": meter["service_tier"], "cost_usd": meter["cost_usd"], "tokens": meter["total_tokens"], "usage": meter, "duration_ms": meter["duration_ms"]}))
		}
		for i, p := range ValueRows(message["content"]) {
			id := fmt.Sprintf("%s:%d", uuid, i)
			switch ValueString(p["type"]) {
			case "thinking":
				events = append(events, event(id, "reasoning", Row{"preview": clip(ValueString(p["thinking"]), 900)}))
			case "text":
				phase := "commentary"
				if stop := ValueString(message["stop_reason"]); stop == "end_turn" || stop == "stop_sequence" {
					phase = "final_answer"
				}
				events = append(events, event(id, "agentMessage", Row{"preview": clip(ValueString(p["text"]), 900), "phase": phase}))
			case "tool_use":
				tool := ValueString(p["name"])
				input := ValueRow(p["input"])
				kind := "tool"
				switch {
				case tool == "Bash":
					kind = "commandExecution"
				case tool == "Agent" || tool == "Task":
					kind = "collabAgentToolCall"
				case strings.HasPrefix(tool, "mcp__"):
					kind = "mcpToolCall"
				case tool == "WebFetch" || tool == "WebSearch":
					kind = "webSearch"
				}
				extra := Row{"tool": tool, "command": input["command"], "preview": clip(firstString(input["description"], input["prompt"], input["query"], input["url"]), 900)}
				if path := firstString(input["file_path"], input["path"]); path != "" {
					action := "update"
					if tool == "Read" {
						action = "read"
					}
					if tool == "Write" {
						action = "add"
					}
					extra["files"] = []Row{{"path": path, "action": action}}
				}
				events = append(events, event(firstString(p["id"], id), kind, extra))
			}
		}
	} else if ValueString(row["type"]) == "user" {
		for i, p := range ValueRows(message["content"]) {
			if ValueString(p["type"]) != "tool_result" {
				continue
			}
			result := ValueRow(row["toolUseResult"])
			agent := ValueString(result["agentId"])
			kind := "tool"
			if agent != "" {
				kind = "subAgentActivity"
			}
			status := "completed"
			if ValueBool(p["is_error"]) {
				status = "failed"
			}
			events = append(events, event(fmt.Sprintf("%s:%d", uuid, i), kind, Row{"parent_id": p["tool_use_id"], "tool": "工具结果", "preview": clip(journeyText(p["content"]), 900), "status": status, "agent_id": agent, "activity": "completed", "duration_ms": result["durationMs"]}))
		}
		if ValueBool(row["turnCompanion"]) || ValueString(row["continuationOfPromptId"]) != "" {
			events = append(events, event(uuid, "userMessage", Row{"preview": clip(journeyText(message["content"]), 900)}))
		}
	} else if ValueString(row["type"]) == "system" {
		switch ValueString(row["subtype"]) {
		case "turn_duration":
			events = append(events, event(uuid, "completed", Row{"status": "completed", "duration_ms": row["durationMs"]}))
		case "compact_boundary":
			events = append(events, event(uuid, "contextCompaction", nil))
		}
	}
	return events
}

func (s *Service) OpenJourneySource(client, thread string) error {
	if normalizedClient(client) != "claude_code" {
		return s.OpenChat(thread)
	}
	var path string
	err := s.store.db.QueryRow(`SELECT l.path FROM claude_locators l JOIN claude_requests q ON q.id=l.item_id WHERE q.session_id=? AND l.available=1 ORDER BY l.offset LIMIT 1`, thread).Scan(&path)
	if err != nil {
		return err
	}
	if s.callbacks.OpenURL == nil {
		return fmt.Errorf("本地记录暂不可用")
	}
	return s.callbacks.OpenURL(path)
}
