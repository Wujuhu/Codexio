package backend

// Append scanner follows usage_collector.py: incomplete tails are retried, replay
// context never takes ownership, and one modern meter plus its legacy notice is one call.
import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

func clip(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
func boundedMap(r Row, n int) {
	if len(r) <= n {
		return
	}
	keys := make([]string, 0, len(r))
	for k := range r {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys[:len(keys)-n] {
		delete(r, k)
	}
}
func stamp(v any) string {
	t, ok := ParseStamp(v)
	if !ok {
		return ""
	}
	return ledgerStamp(t)
}
func ledgerStamp(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05.000Z") }

var rolloutUUID = regexp.MustCompile(`(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`)
var mentionedFilePattern = regexp.MustCompile(`(?m)^## (.+?): ((?:[A-Za-z]:[\\/]|/)[^\r\n]+)$`)
var embeddedImagePattern = regexp.MustCompile(`(?is)<image\b[^>]*>.*?</image\s*>`)

func rolloutID(path string) string {
	ids := rolloutUUID.FindAllString(filepath.Base(path), -1)
	if len(ids) > 0 {
		return strings.ToLower(ids[len(ids)-1])
	}
	return pythonHash(filepath.Base(path))[:32]
}
func rolloutCursorKey(root, path, source string) string {
	return rolloutCursorPrefix(root, source) + rolloutID(path)
}
func rolloutCursorPrefix(root, source string) string {
	absolute, _ := filepath.Abs(root)
	return "usage:" + source + ":" + pythonHash(strings.ToLower(absolute))[:16] + ":"
}
func (s *Store) roots() []string {
	s.mu.RLock()
	r := ValueStrings(s.config["codex_roots"])
	if len(r) == 0 {
		r = append([]string{}, s.options.Roots...)
	}
	s.mu.RUnlock()
	if len(r) == 0 {
		if p := os.Getenv("CODEX_HOME"); p != "" {
			r = []string{p}
		} else if h, e := os.UserHomeDir(); e == nil {
			r = []string{filepath.Join(h, ".codex")}
		}
	}
	return r
}
func (s *Store) discover(path string, force bool) ([]string, error) {
	stat, e := os.Stat(path)
	if os.IsNotExist(e) {
		return []string{}, nil
	}
	if e != nil {
		return nil, e
	}
	if !stat.IsDir() {
		return nil, nil
	}
	cached, ok := s.directories[path]
	var entries []string
	if !force && ok && cached.stamp == stat.ModTime().UnixNano() {
		entries = cached.entries
	} else {
		items, e := os.ReadDir(path)
		if e != nil {
			return nil, e
		}
		entries = make([]string, 0, len(items))
		for _, entry := range items {
			if entry.IsDir() {
				entries = append(entries, entry.Name()+string(filepath.Separator))
			} else if strings.HasSuffix(entry.Name(), ".jsonl") {
				entries = append(entries, entry.Name())
			}
		}
		s.directories[path] = directorySnapshot{stat.ModTime().UnixNano(), entries}
	}
	files := []string{}
	for _, entry := range entries {
		p := filepath.Join(path, entry)
		if strings.HasSuffix(entry, string(filepath.Separator)) {
			children, e := s.discover(p, force)
			if e != nil {
				return nil, e
			}
			files = append(files, children...)
		} else {
			files = append(files, p)
		}
	}
	return files, nil
}
func fileStamp(p string) string {
	st, e := os.Stat(p)
	if e != nil {
		return ""
	}
	return fmt.Sprintf("%d:%d", st.Size(), st.ModTime().UnixNano())
}
func (s *Store) collect(ctx context.Context, force bool) error {
	s.metadataRepairRemaining = 4
	rediscover := force || time.Since(s.lastDiscovery) > 5*time.Minute
	if rediscover {
		s.lastDiscovery = time.Now()
	}
	active := map[string]bool{}
	for index, root := range s.roots() {
		if e := ctx.Err(); e != nil {
			return e
		}
		root = filepath.Clean(root)
		if info, e := os.Stat(root); e != nil || !info.IsDir() {
			return fmt.Errorf("Codex 数据目录不可用: %s", root)
		}
		source := "local"
		if index > 0 {
			source = "local:" + HashString(strings.ToLower(root))[:16]
		}
		active[source] = true
		all := []string{}
		for _, sub := range []string{"sessions", "archived_sessions"} {
			f, e := s.discover(filepath.Join(root, sub), rediscover)
			if e != nil {
				return e
			}
			all = append(all, f...)
		}
		sort.Strings(all)
		s.files[source] = all
		// Scan older sessions first so explicit inherited parent boundaries can resolve.
		if e := s.readTitles(root, rediscover); e != nil {
			return e
		}
		seen := map[string]bool{}
		for _, path := range all {
			if e := ctx.Err(); e != nil {
				return e
			}
			key := rolloutCursorKey(root, path, source)
			seen[key] = true
			if e := s.scanFile(ctx, path, key, source, force); e != nil {
				return fmt.Errorf("scan %s: %w", filepath.Base(path), e)
			}
		}
		rows, e := s.db.Query("SELECT key FROM usage_cursors WHERE instr(key,?)=1", rolloutCursorPrefix(root, source))
		if e != nil {
			return e
		}
		missing := []string{}
		for rows.Next() {
			var key string
			if e = rows.Scan(&key); e != nil {
				rows.Close()
				return e
			}
			if !seen[key] {
				missing = append(missing, key)
			}
		}
		rows.Close()
		for _, key := range missing {
			tx, e := s.db.Begin()
			if e != nil {
				return e
			}
			if e = pruneOrigin(tx, source, key, ""); e == nil {
				_, e = tx.Exec("DELETE FROM usage_cursors WHERE key=?", key)
			}
			if e == nil {
				e = tx.Commit()
			} else {
				tx.Rollback()
			}
			if e != nil {
				return e
			}
		}
		if _, e = s.db.Exec("INSERT INTO usage_sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data", source, dataJSON(Row{"id": source, "name": "本机", "path": root, "status": "ready"})); e != nil {
			return e
		}
	}
	// Full-message storage is bounded independently of the immutable usage ledger.
	_, e := s.db.Exec(`DELETE FROM usage_request_messages WHERE id IN (SELECT id FROM (SELECT id,row_number() OVER(ORDER BY accessed DESC,id) n,sum(size) OVER(ORDER BY accessed DESC,id) bytes FROM usage_request_messages) WHERE n>512 OR bytes>67108864)`)
	return e
}
func (s *Store) readTitles(root string, force bool) error {
	paths, _ := filepath.Glob(filepath.Join(root, "state*.sqlite"))
	sort.Strings(paths)
	paths = append([]string{filepath.Join(root, "session_index.jsonl")}, paths...)
	changed := force
	signatures := map[string]string{}
	for _, path := range paths {
		signature := fileStamp(path) + fileStamp(path+"-wal")
		signatures[path] = signature
		if s.titleSignatures[path] != signature {
			changed = true
		}
	}
	if !changed {
		return nil
	}
	titles := map[string]string{}
	ranks := map[string]int{}
	put := func(id, title string, rank int) {
		if id != "" && userText(title) != "" && rank >= ranks[id] {
			titles[id] = clip(title, 400)
			ranks[id] = rank
		}
	}
	for _, path := range paths {
		if signatures[path] == "" {
			continue
		}
		if strings.HasSuffix(path, ".jsonl") {
			f, e := os.Open(path)
			if e != nil {
				continue
			}
			scan := bufio.NewScanner(f)
			scan.Buffer(make([]byte, 65536), 2<<20)
			for scan.Scan() {
				r, e := DecodeRow(scan.Bytes())
				if e != nil {
					continue
				}
				name := firstString(r["thread_name"], r["name"])
				rank := 2
				if name == "" {
					name = dataString(r, "title")
					rank = 1
				}
				put(firstString(r["id"], r["thread_id"]), name, rank)
			}
			e = scan.Err()
			f.Close()
			if e != nil {
				return e
			}
		} else {
			db, e := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?mode=ro&_pragma=busy_timeout(2000)")
			if e != nil {
				continue
			}
			rows, e := db.Query("SELECT id,coalesce(name,''),coalesce(title,'') FROM threads")
			if e != nil {
				rows, e = db.Query("SELECT id,'',coalesce(title,'') FROM threads")
			}
			if e == nil {
				for rows.Next() {
					var id, name, title string
					if rows.Scan(&id, &name, &title) == nil {
						if userText(name) != "" {
							put(id, name, 3)
						} else {
							put(id, title, 1)
						}
					}
				}
				e = rows.Err()
				rows.Close()
			}
			db.Close()
			if e != nil {
				return e
			}
		}
	}
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	for id, title := range titles {
		if _, e = tx.Exec("INSERT INTO usage_session_titles VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title WHERE title<>excluded.title", id, title); e != nil {
			return e
		}
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	for path, signature := range signatures {
		s.titleSignatures[path] = signature
	}
	return nil
}
func checkpointHash(f *os.File, offset int64) string {
	start := offset - 4096
	if start < 0 {
		start = 0
	}
	b := make([]byte, offset-start)
	n, _ := f.ReadAt(b, start)
	return HashString(string(b[:n]))
}
func prefixHash(f *os.File, offset int64) string {
	if offset > 1024 {
		offset = 1024
	}
	b := make([]byte, offset)
	n, _ := f.ReadAt(b, 0)
	return HashString(string(b[:n]))
}
func (s *Store) scanFile(ctx context.Context, path, key, source string, force bool) error {
	stat, e := os.Stat(path)
	if e != nil {
		return e
	}
	var raw string
	_ = s.db.QueryRow("SELECT data FROM usage_cursors WHERE key=?", key).Scan(&raw)
	cursor := dataRow(raw)
	offset := ValueInt(cursor["offset"])
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	identity := ledgerFileIdentity(f)
	// Old classification is repaired from metadata only, at most four related
	// sources per collection. Unchanged sources are never replayed again.
	if !force && ValueInt(cursor["parser_version"]) == 1 && dataString(cursor, "identity") == identity && ValueInt(cursor["metadata_version"]) != 1 && s.metadataRepairRemaining > 0 {
		s.metadataRepairRemaining--
		recovered, err := s.recoverSourceMetadata(ctx, path, source, key, dataString(cursor, "generation"), nil, true, 32<<20)
		if err != nil {
			return err
		}
		oldState := ValueRow(cursor["go_state"])
		if recovered["request_source"] != nil {
			oldState["request_source"] = recovered["request_source"]
		}
		if stat.Size() <= 32<<20 {
			for _, k := range []string{"resume_tracking", "last_input", "previous_turn_id", "request_model"} {
				if recovered[k] != nil {
					oldState[k] = recovered[k]
				}
			}
		}
		for id, value := range ValueRow(recovered["turns"]) {
			if existing := ValueRow(ValueRow(oldState["turns"])[id]); len(existing) > 0 {
				for k, v := range ValueRow(value) {
					existing[k] = v
				}
				existing["record_kind"] = classifyRequest(existing)
			}
		}
		cursor["metadata_version"] = 1
		cursor["metadata_partial"] = stat.Size() > 32<<20
		if _, err = s.db.Exec("UPDATE usage_cursors SET data=? WHERE key=?", dataJSON(cursor), key); err != nil {
			return err
		}
	}
	parentAvailable := false
	if pending := dataString(ValueRow(cursor["go_state"]), "pending_parent"); pending != "" {
		_, parentAvailable = s.parentSignatures(pending, dataString(ValueRow(cursor["go_state"]), "fork_timestamp"))
	}
	if !force && ValueInt(cursor["parser_version"]) == 1 && dataString(cursor, "identity") == identity && ValueInt(cursor["size"]) == stat.Size() && ValueInt(cursor["mtime"]) == stat.ModTime().UnixNano() && !parentAvailable {
		return nil
	}
	valid := !force && ValueInt(cursor["parser_version"]) == 1 && dataString(cursor, "identity") == identity && offset <= stat.Size() && offset >= 0 && dataString(cursor, "checkpoint") == checkpointHash(f, offset) && dataString(cursor, "prefix") == prefixHash(f, offset) && !(offset == stat.Size() && ValueInt(cursor["mtime"]) != stat.ModTime().UnixNano())
	if parentAvailable {
		valid = false
	}
	state := ValueRow(cursor["go_state"])
	if len(state) == 0 {
		valid = false
	}
	generation := dataString(cursor, "generation")
	if !valid {
		offset = 0
		generation = HashString(fmt.Sprintf("%s:%d", key, time.Now().UnixNano()))[:20]
		state = Row{"session_id": rolloutID(path), "rollout_id": rolloutID(path), "model": "unknown", "provider": "unknown", "turns": Row{}, "calls": Row{}, "question_calls": Row{}, "last_by_source": Row{}, "last_totals": Row{}}
	}
	if _, e = f.Seek(offset, io.SeekStart); e != nil {
		return e
	}
	reader := bufio.NewReaderSize(f, 65536)
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	processed := 0
	malformed := false
	for {
		if e = ctx.Err(); e != nil {
			return e
		}
		line, readErr := readBoundedLine(reader, 8<<20)
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return readErr
		}
		start := offset
		offset += int64(len(line))
		entry, parseErr := DecodeRow(line)
		if parseErr == nil {
			entry["_byte_offset"] = start
			if e = s.processEntry(tx, entry, state, source, key, generation); e != nil {
				return e
			}
		} else {
			malformed = true
		}
		processed++
		if processed%1000 == 0 {
			if e = tx.Commit(); e != nil {
				return e
			}
			tx, e = s.db.Begin()
			if e != nil {
				return e
			}
			defer tx.Rollback()
		}
	}
	// Only after the complete replacement was parsed may stale contributions disappear.
	reconcile := !valid || ValueBool(cursor["reconcile_pending"])
	if reconcile && offset == stat.Size() && !malformed && dataString(state, "pending_parent") == "" {
		if e = pruneOrigin(tx, source, key, generation); e != nil {
			return e
		}
		reconcile = false
	}
	metadataVersion := ValueInt(cursor["metadata_version"])
	if !valid {
		metadataVersion = 1
	}
	cursor = Row{"path": path, "parser_version": 1, "metadata_version": metadataVersion, "metadata_partial": cursor["metadata_partial"], "identity": identity, "prefix": prefixHash(f, offset), "offset": offset, "size": stat.Size(), "mtime": stat.ModTime().UnixNano(), "checkpoint": checkpointHash(f, offset), "generation": generation, "go_state": state, "reconcile_pending": reconcile}
	if _, e = tx.Exec("INSERT INTO usage_cursors VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", key, dataJSON(cursor)); e != nil {
		return e
	}
	return tx.Commit()
}
func readBoundedLine(r *bufio.Reader, max int) ([]byte, error) {
	b := []byte{}
	for {
		p, e := r.ReadSlice('\n')
		if len(b)+len(p) > max {
			return nil, fmt.Errorf("rollout line exceeds %d bytes", max)
		}
		b = append(b, p...)
		if e == bufio.ErrBufferFull {
			continue
		}
		return b, e
	}
}
func pruneOrigin(tx *sql.Tx, source, file, generation string) error {
	rows, e := tx.Query("SELECT kind,item_id FROM usage_origins WHERE source_id=? AND file_key=? AND generation<>?", source, file, generation)
	if e != nil {
		return e
	}
	items := [][2]string{}
	for rows.Next() {
		var kind, id string
		if e = rows.Scan(&kind, &id); e != nil {
			rows.Close()
			return e
		}
		items = append(items, [2]string{kind, id})
	}
	rows.Close()
	if _, e = tx.Exec("DELETE FROM usage_origins WHERE source_id=? AND file_key=? AND generation<>?", source, file, generation); e != nil {
		return e
	}
	for _, item := range items {
		kind, id := item[0], item[1]
		var n int
		if e = tx.QueryRow("SELECT count(*) FROM usage_origins WHERE kind=? AND item_id=?", kind, id).Scan(&n); e != nil {
			return e
		}
		if kind == "record" {
			_, e = tx.Exec("DELETE FROM usage_record_sources WHERE record_id=? AND source_id=? AND NOT EXISTS(SELECT 1 FROM usage_origins WHERE kind='record' AND item_id=? AND source_id=?)", id, source, id, source)
			if e != nil {
				return e
			}
		}
		if n > 0 {
			continue
		}
		table := map[string]string{"record": "usage_records", "turn": "usage_turns", "agent_link": "usage_agent_links", "observation": "usage_observations"}[kind]
		if table != "" {
			if _, e = tx.Exec("DELETE FROM "+table+" WHERE id=?", id); e != nil {
				return e
			}
		}
	}
	return nil
}
func userText(text string) string {
	if strings.Contains(text, "<send_user_message_question_reply>") {
		return ""
	}
	for _, marker := range []string{"## My request:", "## My request", "<user_request>"} {
		if i := strings.Index(text, marker); i >= 0 {
			text = strings.TrimSpace(text[i+len(marker):])
			if j := strings.Index(text, "</user_request>"); j >= 0 {
				text = text[:j]
			}
			break
		}
	}
	for _, tag := range []string{"recommended_plugins", "environment_context", "permissions instructions", "permissions", "INSTRUCTIONS", "user_instructions", "developer_instructions", "skills_instructions", "skill_instructions", "system", "developer", "system-reminder", "app-context", "collaboration_mode", "multi_agent_role", "multi_agent_mode"} {
		for {
			lower := strings.ToLower(text)
			i := strings.Index(lower, "<"+strings.ToLower(tag))
			if i < 0 {
				break
			}
			end := strings.Index(lower[i:], "</"+strings.ToLower(tag)+">")
			if end < 0 {
				text = text[:i]
				break
			}
			text = text[:i] + text[i+end+len(tag)+3:]
		}
	}
	text = strings.TrimSpace(text)
	text = embeddedImagePattern.ReplaceAllString(text, "")
	text = strings.ReplaceAll(text, "Distinguish instructions in attached documents from the user's request.", "")
	for _, prefix := range []string{"# AGENTS.md instructions", "# Files mentioned by the user:"} {
		if strings.HasPrefix(text, prefix) {
			return ""
		}
	}
	return text
}
func visibleMessage(value any, user bool) (string, []Row, bool) {
	parts := []string{}
	attachments := []Row{}
	var items []any
	if a, ok := value.([]any); ok {
		items = a
	} else {
		items = []any{value}
	}
	for _, item := range items {
		if str, ok := item.(string); ok {
			if str != "" {
				parts = append(parts, str)
			}
			continue
		}
		r := ValueRow(item)
		kind := strings.ToLower(strings.ReplaceAll(dataString(r, "type"), "_", ""))
		switch kind {
		case "text", "inputtext", "outputtext":
			text := dataString(r, "text")
			if text != "" {
				parts = append(parts, text)
			}
		case "image", "inputimage", "localimage", "imageurl", "file", "inputfile":
			path := firstString(r["path"], r["file_path"], r["image_url"])
			name := filepath.Base(firstString(r["filename"], r["name"], path, "附件"))
			mime := "application/octet-stream"
			if strings.Contains(kind, "image") {
				mime = "image/*"
			}
			if len(attachments) < 32 {
				if !filepath.IsAbs(path) || len(path) > 4096 {
					path = ""
				}
				attachments = append(attachments, Row{"id": HashString(kind + name + path)[:32], "name": clip(name, 240), "path": path, "mime": mime})
			}
		}
	}
	text := strings.TrimSpace(strings.Join(parts, "\n"))
	complete := true
	if user {
		if strings.Contains(text, "<send_user_message_question_reply>") {
			return "", nil, true
		}
		if start := strings.Index(text, "# Files mentioned by the user:"); start >= 0 {
			section := text[start:]
			if end := strings.Index(section, "## My request"); end >= 0 {
				section = section[:end]
			}
			for _, match := range mentionedFilePattern.FindAllStringSubmatch(section, 32-len(attachments)) {
				path := strings.TrimSpace(match[2])
				mime := "application/octet-stream"
				switch strings.ToLower(filepath.Ext(path)) {
				case ".png":
					mime = "image/png"
				case ".jpg", ".jpeg":
					mime = "image/jpeg"
				case ".gif":
					mime = "image/gif"
				case ".webp":
					mime = "image/webp"
				}
				attachments = append(attachments, Row{"id": HashString(match[1] + path)[:32], "name": clip(match[1], 255), "path": path, "mime": mime})
			}
			if !strings.Contains(text, "## My request") && !strings.Contains(text, "<user_request>") {
				complete = false
			}
		}
		text = userText(text)
	}
	if len(text) > 1048576 {
		end := 1048576
		for end > 0 && text[end]&0xc0 == 0x80 {
			end--
		}
		text = text[:end]
		complete = false
	}
	return text, attachments, complete
}
func getTurn(state Row, id, timestamp string) Row {
	turns := ValueRow(state["turns"])
	state["turns"] = turns
	if id == "" {
		return nil
	}
	if r := ValueRow(turns[id]); len(r) > 0 {
		return r
	}
	r := CloneRow(ValueRow(state["request_source"]))
	r["id"] = turnKey(dataString(state, "session_id"), id)
	r["session_id"] = state["session_id"]
	r["turn_id"] = id
	r["started_at"] = timestamp
	r["started_inferred"] = true
	r["status"] = "unknown"
	r["verified"] = true
	r["first_turn"] = state["first_turn"] == nil
	if state["first_turn"] == nil {
		state["first_turn"] = id
	}
	turns[id] = r
	return r
}
func inheritedEntry(entry, state Row) bool {
	if boundary, ok := knownCount(state["history_start"]); ok {
		if ordinal, ok := knownCount(entry["ordinal"]); ok {
			return ordinal < boundary
		}
	}
	t := stamp(entry["timestamp"])
	return dataString(state, "parent_id") != "" && t != "" && dataString(state, "fork_timestamp") != "" && t < dataString(state, "fork_timestamp")
}
func (s *Store) processEntry(tx *sql.Tx, entry, state Row, source, file, generation string) error {
	p := ValueRow(entry["payload"])
	kind := dataString(entry, "type")
	sub := dataString(p, "type")
	timestamp := stamp(entry["timestamp"])
	if len(p) == 0 {
		return nil
	}
	if kind == "session_meta" && !ValueBool(state["meta_seen"]) {
		state["meta_seen"] = true
		state["session_id"] = firstString(p["id"], p["thread_id"], state["session_id"])
		state["provider"] = firstString(p["model_provider"], "unknown")
		state["history_start"] = p["subagent_history_start_ordinal"]
		state["history_base"] = p["history_base"]
		state["fork_timestamp"] = timestamp
		src := ValueRow(p["source"])
		agent := ValueRow(src["subagent"])
		spawn := ValueRow(agent["thread_spawn"])
		parent := firstString(spawn["parent_thread_id"], p["parent_thread_id"], p["forked_from_id"])
		state["parent_id"] = parent
		review := approvalSource(p["source"]) || dataString(p, "originator") == "codex-auto-review"
		guardian := ValueRow(agent["guardian"])
		requestSource := Row{"source": p["source"], "originator": p["originator"], "is_subagent": len(agent) > 0, "parent_session_id": parent, "parent_turn_id": firstString(spawn["parent_turn_id"], p["parent_turn_id"]), "agent_path": spawn["agent_path"]}
		if review {
			requestSource["record_kind"] = "automatic_approval_review"
			requestSource["is_approval_review"] = true
			requestSource["is_subagent"] = true
			requestSource["parent_session_id"] = firstString(spawn["parent_thread_id"], guardian["parent_thread_id"], p["parent_thread_id"], p["forked_from_id"])
			requestSource["parent_turn_id"] = firstString(spawn["parent_turn_id"], guardian["parent_turn_id"], p["parent_turn_id"])
		}
		state["cwd"], state["session_cwd"] = p["cwd"], p["cwd"]
		state["request_source"] = requestSource
		return nil
	}
	inherited := inheritedEntry(entry, state)
	if kind == "turn_context" || sub == "thread_settings_applied" {
		settings := p
		if sub == "thread_settings_applied" {
			settings = ValueRow(p["thread_settings"])
			if len(settings) == 0 {
				settings = ValueRow(p["settings"])
			}
		}
		for _, k := range []string{"model", "service_tier", "model_context_window", "auth_mode", "cwd"} {
			if settings[k] != nil {
				state[k] = settings[k]
			}
		}
		if effort := firstString(settings["effort"], settings["reasoning_effort"]); effort != "" {
			state["reasoning_effort"] = effort
		}
		if provider := firstString(settings["model_provider_id"], settings["model_provider"]); provider != "" {
			state["provider"] = provider
		}
	}
	// Preserve inherited turn IDs only as structural parent evidence, never user rows.
	if inherited {
		if id := firstString(p["turn_id"], func() any {
			if kind == "turn_context" {
				return p["id"]
			}
			return nil
		}()); id != "" {
			state["inherited_turn_id"] = id
		}
		if sub == "token_count" && !ValueBool(state["_recovery"]) {
			state["inherited_entry"] = true
			_, e := s.accounting(entry, state, tx)
			delete(state, "inherited_entry")
			return e
		}
		return nil
	}
	explicit := ""
	if kind == "turn_context" {
		explicit = firstString(p["turn_id"], p["id"])
	} else if sub == "task_started" || sub == "turn_started" {
		explicit = dataString(p, "turn_id")
	}
	if explicit != "" {
		if sub == "task_started" || sub == "turn_started" {
			settings := ValueRow(p["thread_settings"])
			if len(settings) == 0 {
				settings = ValueRow(p["settings"])
			}
			for _, k := range []string{"model", "reasoning_effort", "service_tier", "model_context_window", "cwd"} {
				if settings[k] != nil {
					state[k] = settings[k]
				} else if p[k] != nil {
					state[k] = p[k]
				}
			}
		}
		old := getTurn(state, dataString(state, "turn_id"), timestamp)
		if dataString(state, "turn_id") != explicit && old != nil && ValueBool(old["synthetic"]) && !ValueBool(old["has_usage"]) && dataString(old, "ended_at") == "" {
			replacement := CloneRow(old)
			replacement["id"] = turnKey(dataString(state, "session_id"), explicit)
			replacement["turn_id"] = explicit
			replacement["synthetic"] = false
			ValueRow(state["turns"])[explicit] = replacement
			old["alias_of"] = replacement["id"]
			if e := persistCollectedTurn(tx, old, state, source, file, generation); e != nil {
				return e
			}
			last := ValueRow(state["last_input"])
			if dataString(last, "turn_id") == dataString(old, "turn_id") {
				last["turn_id"] = explicit
			}
			if !ValueBool(state["_metadata_only"]) {
				_, _ = tx.Exec("INSERT OR IGNORE INTO usage_request_messages SELECT ?,data,digest,revision,size,accessed FROM usage_request_messages WHERE id=?", replacement["id"], old["id"])
			}
			if pending := ValueRow(state["_pending_message"]); dataString(pending, "id") == dataString(old, "id") && ValueBool(ValueRow(state["_recovery_targets"])[dataString(replacement, "id")]) {
				if e := saveMessage(tx, dataString(replacement, "id"), ValueRow(pending["patch"])); e != nil {
					return e
				}
				delete(state, "_pending_message")
			}
		}
		if dataString(state, "turn_id") != explicit {
			state["previous_turn_id"] = state["turn_id"]
			state["request_model"] = state["model"]
			delete(state, "root_turn_id")
			state["modern_candidate"] = nil
			state["pending_output"] = ""
		}
		state["turn_id"] = explicit
		if kind == "turn_context" || sub == "task_started" || sub == "turn_started" {
			state["request_model"] = state["model"]
		}
		r := getTurn(state, explicit, timestamp)
		if p["root_turn_id"] != nil {
			r["root_turn_id"] = p["root_turn_id"]
			state["root_turn_id"] = p["root_turn_id"]
		}
		if approvalRequest(r) {
			for _, k := range []string{"parent_turn_id", "parent_session_id"} {
				if p[k] != nil {
					r[k] = p[k]
				}
			}
			if p["parent_thread_id"] != nil {
				r["parent_session_id"] = p["parent_thread_id"]
			}
		}
		if owner := dataString(state, "pending_question_owner"); owner != "" && owner != explicit {
			r["continuation_of"] = turnKey(dataString(state, "session_id"), owner)
		}
		if kind == "event_msg" {
			r["status"] = "running"
			r["started_at"] = timestamp
			r["started_inferred"] = false
			r["explicit_task_start"] = true
		}
		if parentTurn := dataString(state, "inherited_turn_id"); parentTurn != "" {
			r["inherited_parent_turn_id"] = parentTurn
		}
	}
	message, representation := entryUserContent(entry)
	if message != nil {
		if ok, owner := questionReply(message, state); ok {
			r := getTurn(state, dataString(state, "turn_id"), timestamp)
			if r != nil && owner != "" && owner != dataString(r, "turn_id") {
				r["continuation_of"] = turnKey(dataString(state, "session_id"), owner)
			}
			if owner != "" && (r == nil || dataString(r, "ended_at") != "") {
				state["pending_question_owner"] = owner
			}
		} else {
			text, attachments, complete := visibleMessage(message, true)
			if text != "" || len(attachments) > 0 {
				delete(state, "pending_question_owner")
				r := getTurn(state, dataString(state, "turn_id"), timestamp)
				last := ValueRow(state["last_input"])
				fingerprint := messageFingerprint(message)
				messageID := firstString(p["id"], ValueRow(p["item"])["id"])
				conflicting := messageID != "" && dataString(last, "message_id") != "" && messageID != dataString(last, "message_id")
				sameIdentity := messageID != "" && messageID == dataString(last, "message_id") && dataString(last, "turn_id") == dataString(state, "turn_id")
				paired := sameIdentity || !conflicting && dataString(last, "turn_id") == dataString(state, "turn_id") && dataString(last, "hash") == fingerprint && dataString(last, "representation") != representation && !ValueBool(state["usage_since_input"]) && r != nil && dataString(r, "ended_at") == ""
				if r == nil || dataString(r, "ended_at") != "" && !paired || ValueBool(r["synthetic"]) && !paired {
					marker := p["id"]
					if marker == nil {
						marker = entry["ordinal"]
					}
					if marker == nil {
						marker = entry["_byte_offset"]
					}
					id := "legacy-user:" + pythonHash([]any{state["session_id"], marker, timestamp, fingerprint})[:24]
					if ValueBool(state["_recovery"]) {
						// Recover legacy Go IDs by their exact original source-line
						// identity, never by matching summaries or nearby timestamps.
						oldID := "legacy-user:" + pythonHash([]any{state["session_id"], marker, timestamp, pythonHash(strings.Join(strings.Fields(text), " "))})[:24]
						var present int
						_ = tx.QueryRow("SELECT count(*) FROM usage_turns WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.turn_id')=?", state["session_id"], oldID).Scan(&present)
						if present > 0 {
							id = oldID
						}
					}
					state["turn_id"] = id
					state["previous_turn_id"] = func() any {
						if r != nil {
							return r["turn_id"]
						}
						return ""
					}()
					delete(state, "root_turn_id")
					r = getTurn(state, id, timestamp)
					r["synthetic"] = true
				}
				r["has_user_message"] = true
				hashes := ValueStrings(r["input_hashes"])
				present := false
				for _, hash := range hashes {
					if hash == fingerprint {
						present = true
					}
				}
				if !present {
					hashes = append(hashes, fingerprint)
					if len(hashes) > 32 {
						hashes = hashes[len(hashes)-32:]
					}
				}
				r["input_hashes"] = hashes
				if !paired && (dataString(r, "status") == "unknown" || dataString(r, "status") == "") {
					r["status"] = "running"
				}
				if dataString(r, "prompt_preview") == "" {
					r["prompt_preview"] = clip(strings.Join(strings.Fields(text), " "), 600)
					if text == "" {
						r["prompt_preview"] = "附件消息"
					}
				}
				if e := persistCollectedMessage(tx, dataString(r, "id"), Row{"user": text, "user_complete": complete, "attachments": attachments}, state); e != nil {
					return e
				}
				state["last_input"] = Row{"hash": fingerprint, "representation": representation, "message_id": messageID, "turn_id": state["turn_id"]}
				state["usage_since_input"] = false
			}
		}
	}
	r := getTurn(state, dataString(state, "turn_id"), timestamp)
	messageMetadata := ValueRow(p["internal_chat_message_metadata_passthrough"])
	eventOwner, eventTurn := firstString(p["thread_id"], messageMetadata["thread_id"]), firstString(p["turn_id"], messageMetadata["turn_id"])
	ownsEvent := (eventOwner == "" || eventOwner == dataString(state, "session_id")) && (eventTurn == "" || eventTurn == dataString(state, "turn_id"))
	if r != nil {
		for _, k := range []string{"model", "reasoning_effort", "service_tier", "model_context_window", "provider", "cwd", "session_cwd"} {
			if state[k] != nil {
				r[k] = state[k]
			}
		}
		if m := dataString(state, "request_model"); m != "" && m != "unknown" {
			r["model"] = m
		}
		if dataString(r, "model") == "codex-auto-review" {
			r["record_kind"] = "automatic_approval_review"
			r["is_subagent"] = true
		}
	}
	if kind == "response_item" || sub == "agent_message" {
		if !ValueBool(state["_recovery"]) {
			if e := s.agentEntry(tx, p, state, source, file, generation, timestamp); e != nil {
				return e
			}
		}
		assistant := dataString(p, "role") == "assistant" || sub == "agent_message"
		channel := firstString(p["channel"], p["phase"])
		recipient := dataString(p, "recipient")
		if ownsEvent && assistant && (sub == "message" || sub == "agent_message") && (channel == "" || channel == "commentary" || channel == "final" || channel == "final_answer") && (recipient == "" || recipient == "all" || recipient == "user") {
			content := p["content"]
			if sub == "agent_message" {
				content = p["message"]
			}
			text, _, complete := visibleMessage(content, false)
			if r != nil && text != "" {
				r["latest_output_preview"] = clip(text, 600)
				state["pending_output"] = clip(text, 600)
				if channel == "final" || channel == "final_answer" {
					r["output_preview"] = clip(text, 600)
					r["has_final_message"] = true
					if e := persistCollectedMessage(tx, dataString(r, "id"), Row{"final": text, "final_complete": complete}, state); e != nil {
						return e
					}
				}
			}
		}
	}
	if r != nil && ownsEvent && (kind == "compacted" || sub == "item_completed" && dataString(ValueRow(p["item"]), "type") == "ContextCompaction") {
		r["context_compaction_observed"] = true
	}
	if r != nil && ownsEvent && (sub == "task_complete" || sub == "turn_complete" || sub == "turn_aborted") {
		r["ended_at"] = firstString(stamp(p["completed_at"]), timestamp)
		r["status"] = "completed"
		if sub == "turn_aborted" {
			r["status"] = "aborted"
		}
		if d, ok := ValueFloat(p["duration_ms"]); ok && d >= 0 {
			r["duration_ms"] = d
		}
		if final, exists := p["last_agent_message"]; exists {
			r["context_compaction_completed"] = final == nil && sub != "turn_aborted"
			text, _, complete := visibleMessage(final, false)
			if text != "" {
				r["output_preview"] = clip(text, 600)
				r["has_final_message"] = true
				if e := persistCollectedMessage(tx, dataString(r, "id"), Row{"final": text, "final_complete": complete}, state); e != nil {
					return e
				}
			}
		}
		if e := persistCollectedMessage(tx, dataString(r, "id"), Row{}, state); e != nil {
			return e
		}
	}
	if sub == "raw_response_completed" {
		state["active_response_id"] = p["response_id"]
	}
	if !ValueBool(state["_recovery"]) && (kind == "token_usage_record" || sub == "token_count") {
		record, e := s.accounting(entry, state, tx)
		if e != nil {
			return e
		}
		if record != nil {
			if e = s.saveRecord(tx, record, source, file, generation); e != nil {
				return e
			}
			state["usage_since_input"] = true
			if r != nil && ValueBool(record["context_owner_verified"]) {
				r["has_usage"] = true
			}
		}
	}
	if r != nil {
		if patch := resumeObserve(entry, state); patch != nil {
			applyResumePatch(patch, r, state)
		}
		r["record_kind"] = classifyRequest(r)
		r["observed_at"] = timestamp
		if e := persistCollectedTurn(tx, r, state, source, file, generation); e != nil {
			return e
		}
	}
	trimTurns(state)
	return nil
}
func trimTurns(state Row) {
	turns := ValueRow(state["turns"])
	if len(turns) <= 32 {
		return
	}
	keys := []string{}
	for key := range turns {
		if key != dataString(state, "turn_id") {
			keys = append(keys, key)
		}
	}
	sort.Slice(keys, func(i, j int) bool {
		return dataString(ValueRow(turns[keys[i]]), "started_at") < dataString(ValueRow(turns[keys[j]]), "started_at")
	})
	for _, key := range keys {
		if len(turns) <= 32 {
			break
		}
		delete(turns, key)
	}
}
func questionReply(message any, state Row) (bool, string) {
	text, _, _ := visibleMessage(message, false)
	opening, closing := "<send_user_message_question_reply>", "</send_user_message_question_reply>"
	if !strings.HasPrefix(text, opening) || !strings.HasSuffix(text, closing) {
		return false, ""
	}
	var answers []Row
	if json.Unmarshal([]byte(strings.TrimSuffix(strings.TrimPrefix(text, opening), closing)), &answers) != nil || len(answers) == 0 || len(answers) > 32 {
		return false, ""
	}
	owners := map[string]bool{}
	for _, a := range answers {
		var ids []any
		switch v := a["questionItemId"].(type) {
		case string:
			_ = json.Unmarshal([]byte(v), &ids)
		case []any:
			ids = v
		}
		if len(ids) < 2 {
			return false, ""
		}
		if owner := dataString(ValueRow(state["question_calls"]), ValueString(ids[1])); owner != "" {
			owners[owner] = true
		}
	}
	if len(owners) == 1 {
		for owner := range owners {
			return true, owner
		}
	}
	return true, ""
}
func (s *Store) agentEntry(tx *sql.Tx, p, state Row, source, file, generation, timestamp string) error {
	kind := dataString(p, "type")
	id := dataString(p, "call_id")
	calls := ValueRow(state["calls"])
	state["calls"] = calls
	name := dataString(p, "name")
	if i := strings.LastIndex(name, "."); i >= 0 {
		name = name[i+1:]
	}
	if kind == "function_call" || kind == "custom_tool_call" {
		if id == "" {
			return nil
		}
		if name == "request_user_input_async" || name == "send_user_message_async" {
			q := ValueRow(state["question_calls"])
			q[id] = state["turn_id"]
			state["question_calls"] = q
			boundedMap(q, 512)
			return nil
		}
		if name != "spawn_agent" && name != "followup_task" && name != "send_input" {
			return nil
		}
		args := ValueRow(p["arguments"])
		if raw, ok := p["arguments"].(string); ok {
			args = dataRow(raw)
		}
		if len(args) == 0 {
			args = ValueRow(p["input"])
		}
		target := firstString(args["target"], args["id"])
		linkKind := "followup"
		if name == "spawn_agent" {
			target = dataString(args, "task_name")
			linkKind = "spawn"
		}
		if target != "" && !strings.HasPrefix(target, "/") && !strings.Contains(target, "-") {
			target = strings.TrimSuffix(firstString(ValueRow(state["request_source"])["agent_path"], "/root"), "/") + "/" + target
		}
		calls[id] = Row{"id": "agent-link:" + pythonHash([]any{state["session_id"], id}), "parent_session_id": state["session_id"], "parent_turn_id": state["turn_id"], "kind": linkKind, "target": target, "message_hash": pythonHash(strings.Join(strings.Fields(dataString(args, "message")), " ")), "timestamp": timestamp, "call_id": id}
		boundedMap(calls, 64)
	} else if kind == "function_call_output" || kind == "custom_tool_call_output" {
		link := ValueRow(calls[id])
		if len(link) == 0 {
			return nil
		}
		delete(calls, id)
		output := ValueRow(p["output"])
		if raw, ok := p["output"].(string); ok {
			output = dataRow(raw)
		}
		if len(output) == 0 || ValueBool(output["isError"]) || output["error"] != nil {
			return nil
		}
		link["child_session_id"] = firstString(output["agent_id"], output["thread_id"])
		link["child_turn_id"] = output["turn_id"]
		return saveMetadata(tx, "usage_agent_links", "agent_link", link, source, file, generation)
	}
	return nil
}
func usageCounts(raw any) ([]int64, bool) {
	r := ValueRow(raw)
	if len(r) == 0 {
		return nil, false
	}
	a := make([]int64, 6)
	for i, k := range tokenFields {
		v, exists := r[k]
		if !exists {
			v = 0
			if k == "cached_input_tokens" && r["cache_read_input_tokens"] != nil {
				v = r["cache_read_input_tokens"]
			}
		}
		n, ok := knownCount(v)
		if !ok {
			return nil, false
		}
		a[i] = n
	}
	return a, true
}
func countSlice(v any) []int64 {
	switch a := v.(type) {
	case []int64:
		return a
	case []any:
		r := make([]int64, len(a))
		for i, v := range a {
			r[i] = ValueInt(v)
		}
		return r
	}
	return nil
}
func billable(raw any, a []int64) bool {
	r := ValueRow(raw)
	return len(a) == 6 && r["input_tokens"] != nil && r["output_tokens"] != nil && a[0]+a[3] > 0
}
func (s *Store) accounting(entry, state Row, tx *sql.Tx) (Row, error) {
	p := ValueRow(entry["payload"])
	timestamp := stamp(entry["timestamp"])
	modern := dataString(entry, "type") == "token_usage_record"
	var usage []int64
	quality, response, owner, turn := "", "", dataString(state, "session_id"), firstString(p["turn_id"], state["turn_id"])
	if modern {
		usage, _ = usageCounts(p["usage"])
		if !billable(p["usage"], usage) {
			return nil, nil
		}
		response = dataString(p, "response_id")
		owner = firstString(p["thread_id"], owner)
		quality = "response"
		state["modern_candidate"] = Row{"usage": usage, "turn_id": turn, "response_id": response, "matched": false}
	} else {
		info := ValueRow(p["info"])
		rate := ValueRow(p["rate_limits"])
		if len(rate) > 0 {
			state["limit_id"] = firstString(rate["limit_id"], "codex")
		}
		total, _ := usageCounts(info["total_token_usage"])
		last, _ := usageCounts(info["last_token_usage"])
		signature := HashString(dataJSON([]any{total, last}))
		bucket := firstString(ValueRow(p["rate_limits"])["limit_id"], "unknown")
		bySource := ValueRow(state["last_by_source"])
		duplicate := len(total) > 0 && (dataString(bySource, bucket) == signature || dataString(state, "previous_signature") == signature)
		bySource[bucket] = signature
		state["last_by_source"] = bySource
		state["previous_signature"] = signature
		high := countSlice(state["high_water"])
		old := countSlice(ValueRow(state["last_totals"])[bucket])
		totals := ValueRow(state["last_totals"])
		if len(total) > 0 {
			totals[bucket] = total
		}
		state["last_totals"] = totals
		synthetic := len(last) == 6 && last[0]+last[1]+last[2]+last[3]+last[4] == 0 && last[5] > 0
		if len(total) > 0 {
			next := append([]int64{}, total...)
			if len(high) == 6 {
				for i := range next {
					if high[i] > next[i] {
						next[i] = high[i]
					}
				}
			}
			reset := len(old) == 6 && dataJSON(total) == dataJSON(last)
			if reset || synthetic && total[0]+total[3] == 0 {
				next = total
			}
			state["high_water"] = next
		}
		if duplicate || synthetic {
			return nil, nil
		}
		if ValueBool(state["inherited_entry"]) {
			return nil, nil
		}
		if !ValueBool(state["replay_done"]) && dataString(state, "parent_id") != "" && state["history_start"] == nil && state["history_base"] == nil {
			signatures, available := s.parentSignatures(dataString(state, "parent_id"), dataString(state, "fork_timestamp"))
			if !available {
				state["pending_parent"] = state["parent_id"]
				return nil, nil
			}
			delete(state, "pending_parent")
			if signatures[signature] {
				return nil, nil
			}
			state["replay_done"] = true
		}
		candidate := ValueRow(state["modern_candidate"])
		if len(candidate) > 0 && !ValueBool(candidate["matched"]) && (dataString(candidate, "turn_id") == turn || turn == "") && (dataJSON(last) == dataJSON(countSlice(candidate["usage"])) || !billable(info["last_token_usage"], last)) {
			candidate["matched"] = true
			paired := ValueRow(candidate["record"])
			if limit := dataString(rate, "limit_id"); limit != "" && len(paired) > 0 && ValueBool(paired["context_owner_verified"]) && dataString(paired, "limit_id") != limit {
				paired = CloneRow(paired)
				paired["limit_id"] = limit
				candidate["record"] = paired
				return paired, nil
			}
			return nil, nil
		}
		if billable(info["last_token_usage"], last) {
			usage = last
			quality = "legacy_last"
		} else if len(total) == 6 {
			if len(high) == 0 {
				if state["history_base"] != nil || dataString(state, "parent_id") != "" {
					return nil, nil
				}
				usage = total
				quality = "cumulative_observation"
			} else {
				usage = make([]int64, 6)
				for i := range total {
					if total[i] < high[i] || len(old) == 6 && old[i] > high[i] {
						return nil, nil
					}
					usage[i] = total[i] - high[i]
				}
				quality = "cumulative_delta"
			}
		} else {
			return nil, nil
		}
		if usage[0]+usage[3] == 0 {
			return nil, nil
		}
		if len(candidate) == 0 {
			response = dataString(state, "active_response_id")
			delete(state, "active_response_id")
		}
		if m := firstString(info["model"], info["model_name"], p["model"]); m != "" {
			state["model"] = m
		}
	}
	if timestamp == "" {
		return nil, nil
	}
	id := "legacy:" + pythonHash([]any{state["rollout_id"], timestamp, state["previous_signature"]})
	if modern {
		id = "modern:" + HashString(dataJSON([]any{state["rollout_id"], timestamp, usage, turn}))
	}
	if response != "" {
		id = "response:" + owner + ":" + response
	}
	r := Row{"id": id, "response_id": response, "session_id": owner, "turn_id": turn, "request_turn_id": turn, "timestamp": timestamp, "quality": quality, "model": state["model"], "provider": state["provider"], "service_tier": state["service_tier"], "reasoning_effort": state["reasoning_effort"], "model_context_window": state["model_context_window"], "auth_mode": state["auth_mode"]}
	r["limit_id"] = state["limit_id"]
	if modern && dataString(p, "model") != "" {
		r["model"] = p["model"]
	}
	for i, k := range tokenFields {
		r[k] = usage[i]
	}
	r["reported_total_tokens"] = usage[5]
	r["total_tokens"] = usage[0] + usage[3]
	owned := owner == dataString(state, "session_id") && (dataString(state, "turn_id") == "" || turn == dataString(state, "turn_id"))
	r["context_owner_verified"] = owned
	if owned {
		meta := getTurn(state, turn, timestamp)
		r["prompt_preview"] = meta["prompt_preview"]
		r["output_preview"] = state["pending_output"]
		r["record_kind"] = meta["record_kind"]
		r["source"] = meta["source"]
		for _, field := range []string{"is_approval_review", "parent_session_id", "parent_turn_id", "cwd", "session_cwd"} {
			r[field] = meta[field]
		}
		if approvalRequest(r) || approvalRequest(meta) {
			r["record_kind"] = "automatic_approval_review"
			r["is_approval_review"] = true
			r["is_subagent"] = true
		}
	} else {
		r["model"] = "unknown"
		r["service_tier"] = nil
		r["reasoning_effort"] = nil
		r["model_context_window"] = nil
		r["limit_id"] = nil
	}
	for _, k := range []string{"duration_ms", "call_started_at", "call_ended_at"} {
		if p[k] != nil {
			r[k] = p[k]
		}
	}
	state["pending_output"] = ""
	if modern {
		candidate := ValueRow(state["modern_candidate"])
		candidate["record"] = CloneRow(r)
	}
	return r, nil
}
