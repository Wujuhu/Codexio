package backend

// Read-only Codex catalog and selected-turn history. Root membership and prices
// come from the existing materialized ledger; process details are paginated.
import (
	"bufio"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type journeyCacheEntry struct {
	signature string
	value     Row
}
type localJourney struct {
	mu      sync.Mutex
	store   *Store
	cache   map[string]journeyCacheEntry
	globals map[string]journeyCacheEntry
	headers map[string]journeyCacheEntry
}
type journeyRange struct {
	path, segment, turn, user string
	start, end                int64
	stamp                     int64
}

var journeyVisualization = regexp.MustCompile(`(?s)visualize(.*?)`)
var journeyUUID = regexp.MustCompile(`[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}`)

func (s *Service) journeyReader() *localJourney {
	s.journeyOnce.Do(func() {
		s.journey = &localJourney{store: s.store, cache: map[string]journeyCacheEntry{}, globals: map[string]journeyCacheEntry{}, headers: map[string]journeyCacheEntry{}}
	})
	return s.journey
}
func journeyStamp(path string) string {
	file, e := os.Open(path)
	if e != nil {
		return path + ":missing"
	}
	defer file.Close()
	info, e := file.Stat()
	if e != nil {
		return path + ":missing"
	}
	return fmt.Sprintf("%s:%d:%d:%s", path, info.Size(), info.ModTime().UnixNano(), ledgerFileIdentity(file))
}
func journeyDB(root, prefix string) string {
	files, _ := filepath.Glob(filepath.Join(root, prefix+"*.sqlite"))
	sort.Slice(files, func(i, j int) bool {
		a, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(filepath.Base(files[i]), prefix), ".sqlite"))
		b, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(filepath.Base(files[j]), prefix), ".sqlite"))
		return a > b
	})
	if len(files) > 0 {
		return files[0]
	}
	return ""
}
func journeyOpen(path string) (*sql.DB, error) {
	if path == "" {
		return nil, errors.New("本地过程记录暂不可用")
	}
	db, e := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?mode=ro&_pragma=busy_timeout(2000)")
	if e == nil {
		db.SetMaxOpenConns(1)
	}
	return db, e
}
func journeyRows(db *sql.DB, query string, args ...any) ([]Row, error) {
	rs, e := db.Query(query, args...)
	if e != nil {
		return nil, e
	}
	defer rs.Close()
	columns, e := rs.Columns()
	if e != nil {
		return nil, e
	}
	out := []Row{}
	for rs.Next() {
		values := make([]any, len(columns))
		pointers := make([]any, len(columns))
		for i := range values {
			pointers[i] = &values[i]
		}
		if e = rs.Scan(pointers...); e != nil {
			return nil, e
		}
		row := Row{}
		for i, k := range columns {
			if b, ok := values[i].([]byte); ok {
				row[k] = string(b)
			} else {
				row[k] = values[i]
			}
		}
		out = append(out, row)
	}
	return out, rs.Err()
}
func (j *localJourney) global(root string) Row {
	path := filepath.Join(root, ".codex-global-state.json")
	stamp := journeyStamp(path)
	if old, ok := j.globals[path]; ok && old.signature == stamp {
		return old.value
	}
	file, e := os.Open(path)
	if e != nil {
		return Row{}
	}
	defer file.Close()
	data, e := io.ReadAll(io.LimitReader(file, 8<<20))
	if e != nil {
		return Row{}
	}
	row, e := DecodeRow(data)
	if e != nil {
		return j.globals[path].value
	}
	if len(j.globals) > 8 {
		j.globals = map[string]journeyCacheEntry{}
	}
	j.globals[path] = journeyCacheEntry{stamp, row}
	return row
}
func (j *localJourney) put(key, signature string, value Row) {
	if len(j.cache) >= 3 {
		j.cache = map[string]journeyCacheEntry{}
	}
	if len(dataJSON(value)) <= 2<<20 {
		j.cache[key] = journeyCacheEntry{signature, value}
	}
}

func (s *Service) GetJourneyCatalog(project, before string) (Row, error) {
	j := s.journeyReader()
	j.mu.Lock()
	defer j.mu.Unlock()
	if s.mock {
		ranking := s.GetChatRanking("tokens", 1)
		threads := []Row{}
		for _, r := range ValueRows(ranking["threads"]) {
			threads = append(threads, Row{"id": r["thread_id"], "title": firstString(r["title"], r["thread_id"]), "project_id": "chats"})
		}
		return Row{"projects": []Row{{"id": "chats", "name": "聊天"}}, "threads": threads, "next": ""}, nil
	}
	roots := s.store.roots()
	signature := ""
	for _, root := range roots {
		path := journeyDB(root, "state_")
		signature += journeyStamp(path) + journeyStamp(path+"-wal") + journeyStamp(filepath.Join(root, ".codex-global-state.json"))
	}
	key := "catalog:" + project + ":" + before
	if old, ok := j.cache[key]; ok && old.signature == signature {
		return old.value, nil
	}
	cursor := dataRow(before)
	stamp := int64(^uint64(0) >> 1)
	if v, ok := ValueFloat(cursor["stamp"]); ok {
		stamp = int64(v)
	}
	last := firstString(cursor["id"], "~")
	projects := map[string]string{"chats": "聊天"}
	threads := []Row{}
	for _, root := range roots {
		path := journeyDB(root, "state_")
		if path == "" {
			continue
		}
		global := j.global(root)
		for id, v := range ValueRow(global["local-projects"]) {
			projects[id] = firstString(ValueRow(v)["name"], "—")
		}
		visibleProjects := Row{}
		for id := range ValueRow(global["local-projects"]) {
			visibleProjects[id] = id
		}
		for _, v := range ValueRow(global["app-server-project-id-by-legacy-project-id-by-host"]) {
			for legacy, server := range ValueRow(v) {
				if _, ok := visibleProjects[legacy]; ok {
					if id, ok := server.(string); ok {
						visibleProjects[id] = legacy
					}
				}
			}
		}
		mapped := Row{}
		for id, v := range ValueRow(global["thread-project-assignments"]) {
			if p, ok := visibleProjects[ValueString(ValueRow(v)["projectId"])]; ok {
				mapped[id] = p
			}
		}
		for _, id := range ValueStrings(global["projectless-thread-ids"]) {
			if _, ok := mapped[id]; !ok {
				mapped[id] = "chats"
			}
		}
		db, e := journeyOpen(path)
		if e != nil {
			continue
		}
		columns, _ := journeyRows(db, "PRAGMA table_info(threads)")
		has := map[string]bool{}
		for _, r := range columns {
			has[ValueString(r["name"])] = true
		}
		clock := "updated_at*1000"
		if has["recency_at_ms"] {
			clock = "coalesce(recency_at_ms,updated_at_ms,updated_at*1000)"
		} else if has["recency_at"] {
			clock = "coalesce(recency_at,updated_at)*1000"
		}
		name := "coalesce(title,'')"
		if has["name"] {
			name = "coalesce(nullif(name,''),title,'')"
		}
		stored := "NULL"
		if has["project_id"] {
			stored = `json_extract(?,'$."'||project_id||'"')`
		}
		owner := `coalesce(json_extract(?,'$."'||id||'"'),` + stored + `,'chats')`
		filter := ""
		args := []any{dataJSON(mapped)}
		if has["project_id"] {
			args = append(args, dataJSON(visibleProjects))
		}
		args = append(args, stamp, stamp, last)
		if project != "" {
			filter = " AND project_id=?"
			args = append(args, project)
		}
		query := `SELECT * FROM (SELECT id,` + name + ` AS title,` + clock + ` AS stamp,rollout_path,` + owner + ` AS project_id FROM threads WHERE archived=0 AND instr(source,'subagent')=0) WHERE (stamp<? OR (stamp=? AND id<?))` + filter + ` ORDER BY stamp DESC,id DESC LIMIT 51`
		records, e := journeyRows(db, query, args...)
		db.Close()
		if e != nil {
			return nil, e
		}
		for _, r := range records {
			r["root"] = root
			r["title"] = firstString(userText(ValueString(r["title"])), "—")
			threads = append(threads, r)
		}
	}
	sort.Slice(threads, func(i, k int) bool {
		a, b := ValueInt(threads[i]["stamp"]), ValueInt(threads[k]["stamp"])
		if a == b {
			return ValueString(threads[i]["id"]) > ValueString(threads[k]["id"])
		}
		return a > b
	})
	seen := map[string]bool{}
	unique := []Row{}
	for _, r := range threads {
		id := ValueString(r["id"])
		if !seen[id] {
			seen[id] = true
			unique = append(unique, r)
		}
	}
	more := len(unique) > 50
	if more {
		unique = unique[:50]
	}
	next := ""
	if more {
		r := unique[len(unique)-1]
		next = dataJSON(Row{"stamp": r["stamp"], "id": r["id"]})
	}
	pr := []Row{}
	for id, name := range projects {
		pr = append(pr, Row{"id": id, "name": name})
	}
	sort.Slice(pr, func(a, b int) bool { return ValueString(pr[a]["name"]) < ValueString(pr[b]["name"]) })
	result := Row{"projects": pr, "threads": unique, "next": next}
	j.put(key, signature, result)
	return result, nil
}

func (s *Service) GetJourneyThread(thread string, page int) (Row, error) {
	if strings.HasPrefix(thread, "claude:") {
		return s.claudeJourneyThread(thread, page)
	}
	page = max(1, page)
	where, args := queryFilter(Query{Period: "all", Source: "local"}, true)
	args = append(args, thread)
	where += ` AND json_extract(data,'$.session_id')=? AND record_kind='user_request' AND is_subagent=0`
	var count int
	if e := s.store.db.QueryRow("SELECT count(*) FROM usage_request_groups WHERE "+where, args...).Scan(&count); e != nil {
		return nil, e
	}
	pages := max(1, (count+24)/25)
	page = min(page, pages)
	records, e := journeyRows(s.store.db, `SELECT json_remove(data,'$.composition') AS data FROM usage_request_groups WHERE `+where+` ORDER BY timestamp DESC,id DESC LIMIT 25 OFFSET ?`, append(args, (page-1)*25)...)
	if e != nil {
		return nil, e
	}
	requests := []Row{}
	for _, r := range records {
		requests = append(requests, dataRow(ValueString(r["data"])))
	}
	cw, ca := queryFilter(Query{Period: "all", Source: "local"}, false)
	cw = strings.ReplaceAll(cw, "usage_priced_calls.id", "p.id")
	ca = append(ca, thread)
	totals, e := journeyRows(s.store.db, `SELECT sum(json_extract(p.metrics,'$.tokens')) AS tokens,sum(json_extract(p.metrics,'$.usd')) AS cost_usd,count(*) AS calls FROM usage_priced_calls p LEFT JOIN usage_query_session_roots roots ON roots.session_id=p.session_id WHERE `+cw+` AND coalesce(roots.root_session_id,p.session_id)=?`, ca...)
	if e != nil {
		return nil, e
	}
	summary := Row{"requests": count}
	if len(totals) > 0 {
		for k, v := range totals[0] {
			summary[k] = v
		}
	}
	return Row{"requests": requests, "summary": summary, "page": page, "pages": pages}, nil
}

func (j *localJourney) ranges(root, thread string, turns []string) ([]journeyRange, error) {
	state, e := journeyOpen(journeyDB(root, "state_"))
	if e != nil {
		return nil, e
	}
	defer state.Close()
	history, e := journeyOpen(journeyDB(root, "thread_history_"))
	if e != nil {
		return nil, e
	}
	defer history.Close()
	var path string
	if e = state.QueryRow("SELECT rollout_path FROM threads WHERE id=?", thread).Scan(&path); e != nil {
		return nil, e
	}
	cut := int64(^uint64(0) >> 1)
	ordinal := cut
	seen := map[string]bool{}
	result := []journeyRange{}
	for depth := 0; depth < 128; depth++ {
		absolute, _ := filepath.Abs(path)
		resolved, e := filepath.EvalSymlinks(absolute)
		rel, re := filepath.Rel(root, absolute)
		if e != nil || re != nil || !strings.EqualFold(resolved, absolute) || strings.HasPrefix(rel, "..") || (!strings.HasPrefix(filepath.ToSlash(rel), "sessions/") && !strings.HasPrefix(filepath.ToSlash(rel), "archived_sessions/")) {
			return nil, errors.New("历史片段路径不可用")
		}
		ids := journeyUUID.FindAllString(filepath.Base(path), -1)
		if len(ids) == 0 {
			return nil, errors.New("历史片段标识缺失")
		}
		id := ids[len(ids)-1]
		if seen[id] {
			return nil, errors.New("历史片段关联不完整")
		}
		seen[id] = true
		info, e := os.Stat(path)
		if e != nil {
			return nil, e
		}
		end := min(cut, info.Size())
		slots := strings.TrimSuffix(strings.Repeat("?,", len(turns)), ",")
		args := []any{id}
		for _, t := range turns {
			args = append(args, t)
		}
		args = append(args, ordinal)
		records, e := journeyRows(history, `SELECT turn_id,first_user_item_id,rollout_byte_offset,rollout_end_byte_offset,started_at FROM thread_turns WHERE thread_id=? AND turn_id IN (`+slots+`) AND rollout_ordinal<? ORDER BY rollout_ordinal`, args...)
		if e != nil {
			return nil, e
		}
		segmentRanges := []journeyRange{}
		for _, r := range records {
			begin := int64(ValueInt(r["rollout_byte_offset"]))
			finish := end
			if n, ok := ValueFloat(r["rollout_end_byte_offset"]); ok {
				finish = min(end, int64(n))
			}
			if finish > begin {
				segmentRanges = append(segmentRanges, journeyRange{path, id, ValueString(r["turn_id"]), ValueString(r["first_user_item_id"]), begin, finish, int64(ValueInt(r["started_at"]))})
			}
		}
		result = append(segmentRanges, result...)
		stamp := journeyStamp(path)
		base := Row{}
		if saved, ok := j.headers[path]; ok && saved.signature == stamp {
			base = saved.value
		} else {
			file, e := os.Open(path)
			if e != nil {
				return nil, e
			}
			line, _, e := journeyReadLine(bufio.NewReader(io.LimitReader(file, 2<<20)))
			file.Close()
			if e != nil {
				return nil, e
			}
			base = ValueRow(ValueRow(dataRow(string(line))["payload"])["history_base"])
			if len(j.headers) >= 128 {
				j.headers = map[string]journeyCacheEntry{}
			}
			j.headers[path] = journeyCacheEntry{stamp, base}
		}
		if len(base) == 0 {
			break
		}
		parent := ValueString(base["thread_id"])
		cut = int64(ValueInt(base["end_byte_offset"]))
		ordinal = int64(ValueInt(base["end_ordinal_exclusive"]))
		source := "local"
		for index, candidate := range j.store.roots() {
			if filepath.Clean(candidate) == filepath.Clean(root) && index > 0 {
				source = "local:" + HashString(strings.ToLower(filepath.Clean(root)))[:16]
			}
		}
		candidates, e := journeyRows(j.store.db, "SELECT json_extract(data,'$.path') AS path FROM usage_cursors WHERE key=?", rolloutCursorPrefix(root, source)+parent)
		if e != nil {
			return nil, e
		}
		path = ""
		for _, r := range candidates {
			candidate := ValueString(r["path"])
			ids := journeyUUID.FindAllString(filepath.Base(candidate), -1)
			if len(ids) > 0 && ids[len(ids)-1] == parent {
				path = candidate
				break
			}
		}
		if path == "" {
			return nil, errors.New("历史片段关联不完整")
		}
	}
	return result, nil
}

// Retain only the envelope and compact metadata for oversized image results.
// Tool result bodies are intentionally not copied into the UI or cache.
var journeyScalar = regexp.MustCompile(`[,{]\s*"([A-Za-z_]+)"\s*:\s*("(?:[^"\\]|\\.)*")`)

func journeyLargeRecord(prefix, tail []byte) []byte {
	fields := Row{}
	types := []string{}
	for _, part := range [][]byte{prefix, tail} {
		for _, m := range journeyScalar.FindAllSubmatch(part, -1) {
			var value string
			if json.Unmarshal(m[2], &value) != nil {
				continue
			}
			key := string(m[1])
			if key == "type" {
				types = append(types, value)
			}
			if _, exists := fields[key]; !exists {
				fields[key] = value
			}
		}
	}
	if len(types) >= 2 && types[0] == "response_item" && (types[1] == "function_call_output" || types[1] == "custom_tool_call_output") {
		return []byte(dataJSON(Row{"timestamp": fields["timestamp"], "type": "response_item", "payload": Row{"type": types[1], "call_id": fields["call_id"]}}))
	}

	if len(types) < 3 || types[0] != "event_msg" || types[1] != "item_completed" {
		return nil
	}
	item := Row{"type": types[2], "id": fields["id"], "status": fields["status"]}
	for _, key := range []string{"kind", "path", "savedPath", "revisedPrompt", "tool", "server", "phase"} {
		if v, ok := fields[key]; ok {
			item[key] = v
		}
	}
	if ValueString(item["id"]) == "" {
		return nil
	}
	return []byte(dataJSON(Row{"timestamp": fields["timestamp"], "type": "event_msg", "payload": Row{"type": "item_completed", "turn_id": fields["turn_id"], "item": item}}))
}
func journeyReadLine(reader *bufio.Reader) ([]byte, int64, error) {
	line := []byte{}
	prefix := []byte{}
	tail := []byte{}
	used := int64(0)
	large := false
	for {
		part, e := reader.ReadSlice('\n')
		used += int64(len(part))
		if len(prefix) < 8192 {
			n := min(8192-len(prefix), len(part))
			prefix = append(prefix, part[:n]...)
		}
		tail = append(tail, part...)
		if len(tail) > 8192 {
			tail = append([]byte{}, tail[len(tail)-8192:]...)
		}
		if !large && len(line)+len(part) <= 2<<20 {
			line = append(line, part...)
		} else {
			large = true
			line = nil
		}
		if e != bufio.ErrBufferFull {
			if e == io.EOF && used > 0 {
				e = nil
			}
			if large {
				line = journeyLargeRecord(prefix, tail)
			}
			return line, used, e
		}
	}
}

func (s *Service) GetJourneyFlow(request, child, cursor string) (Row, error) {
	if strings.HasPrefix(request, "claude:") {
		return s.claudeJourneyFlow(request, child, cursor)
	}
	j := s.journeyReader()
	j.mu.Lock()
	defer j.mu.Unlock()
	members, e := journeyRows(s.store.db, `SELECT data FROM usage_query_turns WHERE json_extract(data,'$.root_id')=?`, request)
	if e != nil {
		return nil, e
	}
	parts := strings.SplitN(request, ":", 3)
	if len(parts) != 3 {
		return nil, errors.New("请求标识不可用")
	}
	thread := parts[1]
	if child != "" {
		thread = child
	}
	turns := []string{}
	resumes := map[string]bool{}
	seenTurn := map[string]bool{}
	for _, raw := range members {
		r := dataRow(ValueString(raw["data"]))
		t := ValueString(r["turn_id"])
		if ValueString(r["continuation_of"]) != "" {
			resumes[t] = true
		}
		if ValueString(r["session_id"]) == thread && !seenTurn[t] && t != "" {
			seenTurn[t] = true
			turns = append(turns, t)
		}
	}
	if child == "" && !seenTurn[parts[2]] {
		turns = append(turns, parts[2])
		seenTurn[parts[2]] = true
	}
	if len(turns) == 0 {
		return Row{"events": []Row{}, "next": ""}, nil
	}
	if s.mock {
		calls, _ := s.store.Detail(request, 1)
		events := []Row{}
		for _, r := range ValueRows(calls["members"]) {
			events = append(events, Row{"id": r["id"], "kind": "model", "model": r["model"], "cost_usd": r["cost_usd"], "tokens": r["total_tokens"], "usage": r, "status": "completed"})
		}
		return Row{"events": events, "next": ""}, nil
	}
	root := ""
	for _, candidate := range s.store.roots() {
		db, err := journeyOpen(journeyDB(candidate, "state_"))
		if err != nil {
			continue
		}
		var present int
		err = db.QueryRow("SELECT 1 FROM threads WHERE id=?", thread).Scan(&present)
		db.Close()
		if err == nil {
			root = candidate
			break
		}
	}
	if root == "" {
		return nil, errors.New("本地过程记录暂不可用")
	}
	spans, e := j.ranges(root, thread, turns)
	if e != nil {
		return nil, e
	}
	signature := ""
	for _, r := range spans {
		signature += fmt.Sprintf("%s:%s:%d:%d", journeyStamp(r.path), r.turn, r.start, r.end)
	}
	key := "flow:" + request + ":" + child + ":" + cursor
	var cached Row
	if old, ok := j.cache[key]; ok && old.signature == signature {
		cached = old.value
	}
	meterRows, e := journeyRows(s.store.db, `SELECT json_object('response_id',json_extract(p.data,'$.response_id'),'model',p.model,'cost_usd',json_extract(p.metrics,'$.usd'),'reasoning_effort',json_extract(p.data,'$.reasoning_effort'),'service_tier',p.tier) AS data FROM usage_priced_calls p JOIN usage_request_members m ON m.record_id=p.id WHERE m.request_id=? AND p.session_id=?`, request, thread)
	if e != nil {
		return nil, e
	}
	meters := map[string]Row{}
	for _, r := range meterRows {
		value := dataRow(ValueString(r["data"]))
		meters[ValueString(value["response_id"])] = value
	}
	totals, err := journeyRows(s.store.db, `SELECT p.session_id,sum(json_extract(p.metrics,'$.usd')) AS cost_usd,sum(json_extract(p.metrics,'$.tokens')) AS tokens,count(*) AS calls FROM usage_priced_calls p JOIN usage_request_members m ON m.record_id=p.id WHERE m.request_id=? GROUP BY p.session_id`, request)
	if err != nil {
		return nil, err
	}
	agents := map[string]Row{}
	for _, r := range totals {
		agents[ValueString(r["session_id"])] = r
	}
	pricePage := func(page Row) Row {
		result := CloneRow(page)
		events := []Row{}
		for _, raw := range ValueRows(page["events"]) {
			e := CloneRow(raw)
			if ValueString(e["kind"]) == "turnStart" {
				if !resumes[ValueString(e["turn_id"])] {
					continue
				}
				e["kind"] = "resume"
			}
			if ValueString(e["kind"]) == "model" {
				m := meters[ValueString(e["id"])]
				e["model"] = m["model"]
				e["effort"] = m["reasoning_effort"]
				e["speed"] = m["service_tier"]
				e["cost_usd"] = m["cost_usd"]
			}
			if child := ValueString(e["agent_id"]); child != "" {
				for _, k := range []string{"cost_usd", "tokens", "calls"} {
					e[k] = agents[child][k]
				}
			}
			events = append(events, e)
		}
		result["events"] = events
		return result
	}
	if cached != nil {
		return pricePage(cached), nil
	}
	position := dataRow(cursor)
	index := int(max(0, ValueInt(position["range"])))
	offset := int64(max(0, ValueInt(position["offset"])))
	active := ValueString(position["turn"])
	parent := ValueString(position["parent"])
	events := []Row{}
	seen := map[string]bool{}
	readBytes := int64(0)
	for index < len(spans) && len(events) < 120 && readBytes < 8<<20 {
		span := spans[index]
		offset = min(span.end, max(span.start, offset))
		file, e := os.Open(span.path)
		if e != nil {
			return nil, e
		}
		if _, e = file.Seek(offset, io.SeekStart); e != nil {
			file.Close()
			return nil, e
		}
		reader := bufio.NewReader(io.LimitReader(file, span.end-offset))
		if active == "" {
			active = span.turn
		}
		for offset < span.end {
			line, n, err := journeyReadLine(reader)
			offset += n
			readBytes += n
			if err != nil && err != io.EOF {
				file.Close()
				return nil, err
			}
			row := dataRow(string(line))
			p := ValueRow(row["payload"])
			kind := ValueString(row["type"])
			typ := ValueString(p["type"])
			if kind == "turn_context" || kind == "event_msg" && typ == "task_started" {
				active = ValueString(p["turn_id"])
			}
			owner := firstString(p["turn_id"], active)
			if seenTurn[owner] {
				if event := journeyEvent(row, nil, &parent); event != nil {
					event["turn_id"] = owner
					if len(spans) > 0 && ValueString(event["kind"]) == "userMessage" {
						event["primary"] = child == "" && ValueString(event["id"]) == spans[0].user
						if child != "" {
							event["kind"] = "agentInput"
						}
					}
					id := ValueString(event["id"])
					if !seen[id] {
						seen[id] = true
						events = append(events, event)
					}
				}
			}
			if len(events) >= 120 || readBytes >= 8<<20 || err == io.EOF {
				break
			}
		}
		file.Close()
		if offset >= span.end {
			index++
			offset = 0
			active = ""
			parent = ""
		}
	}
	next := ""
	if index < len(spans) {
		next = dataJSON(Row{"range": index, "offset": offset, "turn": active, "parent": parent})
	}
	result := Row{"events": events, "next": next}
	j.put(key, signature, result)
	return pricePage(result), nil
}

func journeyText(value any) string {
	if value == nil {
		return ""
	}
	if s, ok := value.(string); ok {
		return s
	}
	if a, ok := value.([]any); ok {
		parts := []string{}
		for _, v := range a {
			if s := journeyText(v); s != "" {
				parts = append(parts, s)
			}
		}
		return strings.Join(parts, "\n")
	}
	row := ValueRow(value)
	if len(row) == 0 {
		return ""
	}
	return firstString(row["text"], journeyText(row["content"]))
}
func journeyEvent(row Row, meters map[string]Row, parent *string) Row {
	p := ValueRow(row["payload"])
	typ := ValueString(p["type"])
	kind := ValueString(row["type"])
	stamp := ValueString(row["timestamp"])
	e := Row{"time": stamp, "status": "", "files": []Row{}}
	if kind == "token_usage_record" {
		id := ValueString(p["response_id"])
		u := ValueRow(p["usage"])
		m := meters[id]
		e["id"] = id
		e["kind"] = "model"
		e["model"] = m["model"]
		e["effort"] = m["reasoning_effort"]
		e["speed"] = m["service_tier"]
		e["cost_usd"] = m["cost_usd"]
		e["tokens"] = u["total_tokens"]
		e["usage"] = u
		e["status"] = "completed"
		return e
	}
	if kind == "response_item" && (typ == "function_call" || typ == "custom_tool_call") {
		name := ValueString(p["name"])
		id := firstString(p["call_id"], p["id"])
		journeyPendingStart(parent, id)
		e["id"] = "call:" + id
		e["kind"] = "tool"
		e["tool"] = name
		e["command"] = clip(firstString(p["input"], p["arguments"]), 65536)
		if strings.Contains(name, "request_user_input") {
			e["kind"] = "userInput"
			args := dataRow(ValueString(p["arguments"]))
			questions := []string{}
			for _, q := range ValueRows(args["questions"]) {
				questions = append(questions, firstString(q["title"], q["question"]))
			}
			e["preview"] = clip(strings.Join(questions, "\n"), 900)
			delete(e, "command")
		}
		return e
	}
	if kind == "response_item" && (typ == "function_call_output" || typ == "custom_tool_call_output") {
		journeyPendingEnd(parent, ValueString(p["call_id"]))
		return nil
	}
	if kind == "event_msg" && typ == "task_started" {
		e["id"] = "start:" + ValueString(p["turn_id"])
		e["kind"] = "turnStart"
		return e
	}

	if kind == "event_msg" && (typ == "task_complete" || typ == "turn_aborted") {
		e["id"] = typ + ":" + ValueString(p["turn_id"]) + ":" + stamp
		e["kind"] = "completed"
		e["status"] = "completed"
		if typ == "turn_aborted" {
			e["kind"] = "pause"
			e["status"] = "interrupted"
		}
		return e
	}
	if kind == "compacted" {
		e["id"] = "compact:" + stamp
		e["kind"] = "contextCompaction"
		return e
	}
	if kind != "event_msg" || typ != "item_completed" {
		return nil
	}
	item := ValueRow(p["item"])
	raw := ValueString(item["type"])
	if raw == "" {
		return nil
	}
	eventKind := strings.ToLower(raw[:1]) + raw[1:]
	if raw == "Extension" {
		eventKind = firstString(item["kind"], "extension")
	}
	e["id"] = item["id"]
	e["kind"] = eventKind
	e["status"] = firstString(item["status"], "completed")
	if ms, ok := ValueFloat(p["started_at_ms"]); ok {
		e["time"] = UTCStamp(time.UnixMilli(int64(ms)))
	}
	duration := ValueRow(item["duration"])
	if len(duration) > 0 {
		secs, _ := ValueFloat(duration["secs"])
		nanos, _ := ValueFloat(duration["nanos"])
		e["duration_ms"] = secs*1000 + nanos/1e6
	} else {
		e["duration_ms"] = item["durationMs"]
	}
	switch eventKind {
	case "reasoning":
		e["preview"] = clip(journeyText(item["summary_text"]), 2000)
	case "agentMessage":
		message := journeyText(item["content"])
		files := []Row{}
		for _, match := range journeyVisualization.FindAllStringSubmatch(message, -1) {
			data := dataRow(match[1])
			if path := ValueString(data["path"]); path != "" {
				files = append(files, Row{"path": path, "action": "visualization"})
			}
		}
		e["preview"] = clip(journeyVisualization.ReplaceAllString(message, ""), 900)
		e["phase"] = item["phase"]
		e["files"] = files
	case "userMessage":
		content := journeyText(item["content"])
		e["preview"] = clip(userText(content), 900)
		if strings.Contains(content, "send_user_message_question_reply") {
			e["kind"] = "userInputReply"
			e["preview"] = journeyAnswer(content)
		}
	case "commandExecution":
		parts := ValueStrings(item["command"])
		command := ValueString(item["command"])
		if len(parts) == 3 && (parts[1] == "-lc" || parts[1] == "-c") {
			command = parts[2]
		} else if len(parts) > 0 {
			command = strings.Join(parts, " ")
		}
		e["command"] = clip(command, 65536)
		e["tool"] = "exec_command"
		e["cwd"] = item["cwd"]
		e["exit_code"] = item["exit_code"]
		files := []Row{}
		for _, a := range ValueRows(item["parsed_cmd"]) {
			if path := firstString(a["path"], a["name"]); path != "" {
				files = append(files, Row{"path": path, "action": a["type"]})
			}
		}
		e["files"] = files
	case "fileChange":
		changes := ValueRows(item["changes"])
		if len(changes) == 0 {
			for path, v := range ValueRow(item["changes"]) {
				v := CloneRow(ValueRow(v))
				v["path"] = path
				changes = append(changes, v)
			}
		}
		files := []Row{}
		for _, c := range changes {
			k := ValueRow(c["kind"])
			files = append(files, Row{"path": c["path"], "action": firstString(k["type"], c["type"], "update"), "destination": firstString(k["move_path"], c["move_path"])})
		}
		e["files"] = files
		e["tool"] = "apply_patch"
	case "webSearch":
		action := ValueRow(item["action"])
		e["tool"] = "web.run"
		e["action"] = action["type"]
		e["preview"] = clip(firstString(action["query"], journeyText(action["queries"]), action["url"], action["pattern"], item["query"]), 900)
	case "mcpToolCall":
		e["tool"] = item["tool"]
		e["server"] = item["server"]
		e["preview"] = clip(dataJSON(item["arguments"]), 400)
	case "subAgentActivity":
		e["agent_id"] = item["agent_thread_id"]
		e["preview"] = item["agent_path"]
		e["activity"] = item["kind"]
	case "collabAgentToolCall":
		e["tool"] = item["tool"]
		e["preview"] = clip(journeyText(item["receiver_thread_ids"]), 900)
		e["agent_ids"] = item["receiver_thread_ids"]
	case "imageView":
		e["tool"] = "view_image"
		e["preview"] = item["path"]
	case "imageGeneration":
		e["tool"] = "imagegen"
		e["preview"] = clip(ValueString(item["revisedPrompt"]), 900)
		e["files"] = []Row{{"path": item["savedPath"], "action": "image"}}
	case "sleep":
		e["duration_ms"] = item["duration_ms"]
	default:
		e["preview"] = clip(ValueString(item["text"]), 900)
		e["tool"] = item["tool"]
	}
	pending := journeyPending(*parent)
	if len(pending) == 1 && pending[0] != "__ambiguous__" && eventKind != "userMessage" && eventKind != "reasoning" && eventKind != "agentMessage" {
		e["parent_id"] = "call:" + pending[0]
	}
	return e
}

func journeyAnswer(content string) string {
	const start = "<send_user_message_question_reply>"
	const end = "</send_user_message_question_reply>"
	a, b := strings.Index(content, start), strings.Index(content, end)
	if a < 0 || b < a {
		return clip(userText(content), 900)
	}
	var answers []Row
	if json.Unmarshal([]byte(content[a+len(start):b]), &answers) != nil {
		return clip(userText(content), 900)
	}
	values := []string{}
	for _, answer := range answers {
		values = append(values, ValueString(answer["question"])+"\n"+journeyText(answer["answer"]))
	}
	return clip(strings.Join(values, "\n"), 900)
}

func journeyPending(value string) []string {
	if value == "" {
		return nil
	}
	var ids []string
	if strings.HasPrefix(value, "[") && json.Unmarshal([]byte(value), &ids) == nil {
		return ids
	}
	return []string{value}
}
func journeyPendingStart(state *string, id string) {
	ids := journeyPending(*state)
	if len(ids) > 0 {
		found := false
		for _, v := range ids {
			if v == "__ambiguous__" {
				found = true
			}
		}
		if !found {
			ids = append(ids, "__ambiguous__")
		}
	}
	if len(ids) > 64 {
		ids = []string{"__ambiguous__"}
	}
	ids = append(ids, id)
	*state = dataJSON(ids)
}
func journeyPendingEnd(state *string, id string) {
	ids := []string{}
	for _, v := range journeyPending(*state) {
		if v != id {
			ids = append(ids, v)
		}
	}
	if len(ids) == 0 || (len(ids) == 1 && ids[0] == "__ambiguous__") {
		*state = ""
	} else {
		*state = dataJSON(ids)
	}
}
