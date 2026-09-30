package backend

// Durable schema and immutable-response merge ported from usage_store.py.
import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	_ "modernc.org/sqlite"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

type Store struct {
	db                   *sql.DB
	path                 string
	options              DataOptions
	mu                   sync.RWMutex
	work                 sync.Mutex
	config               Row
	status               Row
	lastScanAt           string
	prices               []Row
	priceVersion         string
	priceStatus          Row
	generation           atomic.Int64
	refresh              chan bool
	closed               chan struct{}
	closeOnce            sync.Once
	directories          map[string]directorySnapshot
	titleSignatures      map[string]string
	lastDiscovery        time.Time
	files                map[string][]string
	parentSignatureCache map[string]map[string]bool
	upstreamSignature    string
	timeSignature        string
	lastWallTime         time.Time
	nextFuture           string
	queryCache           map[string]string
	queryCacheGeneration int64
}
type directorySnapshot struct {
	stamp   int64
	entries []string
}

func dataJSON(v any) string {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return string(bytes.TrimSuffix(b.Bytes(), []byte{'\n'}))
}
func pythonHash(v any) string           { return HashString(dataJSON(v)) }
func dataRow(v string) Row              { r, _ := DecodeRow([]byte(v)); return r }
func dataString(r Row, k string) string { return ValueString(r[k]) }
func firstString(v ...any) string {
	for _, x := range v {
		if s := ValueString(x); s != "" {
			return s
		}
	}
	return ""
}
func turnKey(session, turn string) string { return "turn:" + session + ":" + turn }
func boolInt(v bool) int {
	if v {
		return 1
	}
	return 0
}
func OpenStore(o DataOptions) (*Store, error) {
	if o.Directory == "" {
		return nil, errors.New("missing data directory")
	}
	if e := os.MkdirAll(o.Directory, 0700); e != nil {
		return nil, e
	}
	name := "usage.sqlite"
	if o.Mock {
		name = "usage_mock.sqlite"
	}
	db, e := sql.Open("sqlite", filepath.Join(o.Directory, name))
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	s := &Store{db: db, options: o, config: CloneRow(o.Config), status: Row{"status": "starting"}, refresh: make(chan bool, 1), closed: make(chan struct{}), directories: map[string]directorySnapshot{}, titleSignatures: map[string]string{}, files: map[string][]string{}}
	s.path = filepath.Join(o.Directory, name)
	s.queryCache = map[string]string{}
	s.parentSignatureCache = map[string]map[string]bool{}
	if e = s.schema(); e != nil {
		db.Close()
		return nil, e
	}
	if e = s.loadPrices(); e != nil {
		db.Close()
		return nil, e
	}
	if o.Mock {
		if e = s.seedMock(); e != nil {
			db.Close()
			return nil, e
		}
	}
	if e = s.project(); e != nil {
		db.Close()
		return nil, e
	}
	return s, nil
}
func (s *Store) schema() error {
	_, e := s.db.Exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=10000;
 CREATE TABLE IF NOT EXISTS usage_records(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,model TEXT NOT NULL,session_id TEXT NOT NULL,data TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS usage_records_time ON usage_records(timestamp DESC);
 CREATE INDEX IF NOT EXISTS usage_records_model_time ON usage_records(model,timestamp);
 CREATE TABLE IF NOT EXISTS usage_record_sources(record_id TEXT NOT NULL,source_id TEXT NOT NULL,PRIMARY KEY(record_id,source_id));
 CREATE TABLE IF NOT EXISTS usage_observations(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_sources(id TEXT PRIMARY KEY,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_cursors(key TEXT PRIMARY KEY,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_meta(key TEXT PRIMARY KEY,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_session_titles(session_id TEXT PRIMARY KEY,title TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_turns(id TEXT PRIMARY KEY,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_agent_links(id TEXT PRIMARY KEY,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_origins(source_id TEXT NOT NULL,file_key TEXT NOT NULL,kind TEXT NOT NULL,item_id TEXT NOT NULL,generation TEXT NOT NULL,PRIMARY KEY(source_id,file_key,kind,item_id));
 CREATE INDEX IF NOT EXISTS usage_origins_item ON usage_origins(source_id,kind,item_id);
 CREATE TABLE IF NOT EXISTS usage_revisions(kind TEXT PRIMARY KEY,revision INTEGER NOT NULL);
 INSERT OR IGNORE INTO usage_revisions VALUES('ledger',0),('observations',0);
 CREATE TABLE IF NOT EXISTS usage_request_messages(id TEXT PRIMARY KEY,data TEXT NOT NULL,digest TEXT NOT NULL,revision INTEGER NOT NULL,size INTEGER NOT NULL,accessed REAL NOT NULL);
 CREATE INDEX IF NOT EXISTS usage_request_messages_accessed ON usage_request_messages(accessed);
 CREATE TABLE IF NOT EXISTS usage_priced_calls(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,model TEXT NOT NULL,tier TEXT NOT NULL,source_id TEXT NOT NULL,session_id TEXT NOT NULL,turn_id TEXT NOT NULL,metrics TEXT NOT NULL,data TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS priced_calls_time ON usage_priced_calls(timestamp DESC,id DESC);
 CREATE INDEX IF NOT EXISTS priced_calls_model_time ON usage_priced_calls(model,timestamp);
 CREATE INDEX IF NOT EXISTS go_priced_calls_owner ON usage_priced_calls(session_id,turn_id,timestamp);
 CREATE TABLE IF NOT EXISTS usage_query_sources(record_id TEXT NOT NULL,source_id TEXT NOT NULL,PRIMARY KEY(record_id,source_id));
 CREATE TABLE IF NOT EXISTS usage_request_groups(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,record_kind TEXT NOT NULL,is_subagent INTEGER NOT NULL,subagent_count INTEGER NOT NULL,data TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS request_groups_time ON usage_request_groups(timestamp DESC,id DESC);
 CREATE TABLE IF NOT EXISTS usage_request_members(request_id TEXT NOT NULL,record_id TEXT NOT NULL,PRIMARY KEY(request_id,record_id));
 CREATE INDEX IF NOT EXISTS request_members_record ON usage_request_members(record_id,request_id);
 CREATE TABLE IF NOT EXISTS usage_query_turns(id TEXT PRIMARY KEY,data TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS go_query_turns_alias ON usage_query_turns(json_extract(data,'$.resolved_id'),json_extract(data,'$.session_id'),json_extract(data,'$.turn_id'));
 CREATE INDEX IF NOT EXISTS go_query_turns_root ON usage_query_turns(json_extract(data,'$.root_id'));
 CREATE INDEX IF NOT EXISTS go_turns_owner ON usage_turns(json_extract(data,'$.session_id'),json_extract(data,'$.turn_id'));
 CREATE TABLE IF NOT EXISTS usage_query_state(key TEXT PRIMARY KEY,data TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS usage_query_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,item_id TEXT NOT NULL);`)
	if e != nil {
		return e
	}
	for _, v := range []struct{ table, field, kind string }{{"usage_records", "id", "record"}, {"usage_record_sources", "record_id", "record"}, {"usage_session_titles", "session_id", "title"}, {"usage_turns", "id", "turn"}, {"usage_agent_links", "id", "agent"}} {
		for _, a := range []string{"INSERT", "UPDATE", "DELETE"} {
			ref := "NEW"
			if a == "DELETE" {
				ref = "OLD"
			}
			q := fmt.Sprintf("CREATE TRIGGER IF NOT EXISTS go_revision_%s_%s AFTER %s ON %s BEGIN UPDATE usage_revisions SET revision=revision+1 WHERE kind='ledger'; INSERT INTO usage_query_changes(kind,item_id) VALUES('%s',%s.%s); END", v.table, a, a, v.table, v.kind, ref, v.field)
			if _, e = s.db.Exec(q); e != nil {
				return e
			}
		}
	}
	return nil
}
func (s *Store) Database() *sql.DB { return s.db }
func (s *Store) Generation() int64 { return s.generation.Load() }
func (s *Store) Status() Row {
	s.mu.RLock()
	defer s.mu.RUnlock()
	r := CloneRow(s.status)
	r["generation"] = s.Generation()
	r["updated_at"] = s.lastScanAt
	r["last_scan_at"] = s.lastScanAt
	r["price_version"] = s.priceVersion
	r["pricing"] = CloneRow(s.priceStatus)
	return r
}
func (s *Store) Configure(r Row) {
	s.mu.Lock()
	relevant := false
	for k, v := range r {
		if (k == "codex_roots" || k == "usage_refresh_interval_seconds") && dataJSON(s.config[k]) != dataJSON(v) {
			relevant = true
		}
		s.config[k] = v
	}
	s.mu.Unlock()
	if relevant {
		s.Refresh()
	}
}
func (s *Store) Refresh() {
	select {
	case s.refresh <- false:
	default:
	}
}
func (s *Store) Rescan() {
	select {
	case <-s.refresh:
	default:
	}
	select {
	case s.refresh <- true:
	default:
	}
}
func (s *Store) Close() error {
	var e error
	s.closeOnce.Do(func() { close(s.closed); s.work.Lock(); defer s.work.Unlock(); e = s.db.Close() })
	return e
}
func (s *Store) Run(ctx context.Context, changed func()) {
	tick := time.NewTimer(time.Millisecond)
	defer tick.Stop()
	force := false
	for {
		select {
		case <-ctx.Done():
			return
		case <-s.closed:
			return
		case force = <-s.refresh:
		case <-tick.C:
		}
		s.work.Lock()
		before := s.Generation()
		var e error
		if !s.options.Mock {
			e = s.collect(ctx, force)
			if e == nil {
				s.syncPrices(ctx)
			}
		}
		if e == nil {
			e = s.project()
		}
		upstreamPath := filepath.Join(s.options.Directory, "upstream.sqlite")
		upstreamSignature := fileStamp(upstreamPath) + fileStamp(upstreamPath+"-wal")
		if upstreamSignature != s.upstreamSignature {
			s.upstreamSignature = upstreamSignature
			s.generation.Add(1)
		}
		now := time.Now()
		zone, offset := now.Zone()
		timeSignature := fmt.Sprint(now.Format("2006-01-02"), zone, offset)
		var future sql.NullString
		_ = s.db.QueryRow("SELECT min(timestamp) FROM usage_priced_calls WHERE timestamp>?", ledgerStamp(now)).Scan(&future)
		if timeSignature != s.timeSignature || !s.lastWallTime.IsZero() && now.Before(s.lastWallTime) || s.nextFuture != "" && future.String != s.nextFuture {
			s.generation.Add(1)
		}
		s.timeSignature = timeSignature
		s.lastWallTime = now
		s.nextFuture = future.String
		s.mu.Lock()
		previous := dataJSON(s.status)
		s.status = Row{"status": "ready"}
		if e != nil {
			s.status = Row{"status": "error", "error": e.Error()}
		} else {
			s.lastScanAt = ledgerStamp(time.Now())
		}
		statusChanged := previous != dataJSON(s.status)
		interval := ValueInt(s.config["usage_refresh_interval_seconds"])
		if interval == 0 {
			interval = ValueInt(s.config["refresh_interval"])
		}
		s.mu.Unlock()
		s.work.Unlock()
		if (before != s.Generation() || statusChanged) && changed != nil {
			changed()
		}
		force = false
		if interval < 5 {
			interval = 10
		}
		if interval > 3600 {
			interval = 3600
		}
		tick.Reset(time.Duration(interval) * time.Second)
	}
}
func (s *Store) saveRecord(tx *sql.Tx, r Row, source, file, generation string) error {
	if response := dataString(r, "response_id"); response != "" {
		r["id"] = "response:" + dataString(r, "session_id") + ":" + response
	}
	id := dataString(r, "id")
	if id == "" {
		return nil
	}
	for k := range r {
		if k == "cost_usd" || k == "usd" || k == "price" {
			delete(r, k)
		}
	}
	r["source_id"] = source
	if timestamp := stamp(r["timestamp"]); timestamp != "" {
		r["timestamp"] = timestamp
	}
	var raw string
	_ = tx.QueryRow("SELECT data FROM usage_records WHERE id=?", id).Scan(&raw)
	if raw != "" {
		old := dataRow(raw)
		var other int
		_ = tx.QueryRow("SELECT count(*) FROM usage_origins WHERE kind='record' AND item_id=? AND (source_id<>? OR file_key<>?)", id, source, file).Scan(&other)
		copied := !(ValueBool(r["context_owner_verified"]) && !ValueBool(old["context_owner_verified"])) && (other > 0 || dataString(old, "source_id") != source || ValueBool(old["context_owner_verified"]) && !ValueBool(r["context_owner_verified"]))
		if dataString(r, "response_id") != "" {
			if copied {
				for _, k := range []string{"timestamp", "model", "service_tier", "model_context_window", "provider", "turn_id", "request_turn_id", "session_id", "prompt_preview", "output_preview", "duration_ms", "call_started_at", "call_ended_at", "record_kind", "source"} {
					if old[k] != nil && old[k] != "" && old[k] != "unknown" {
						r[k] = old[k]
					}
				}
			}
			if len(dataString(old, "quality")) >= 8 && dataString(old, "quality")[:8] == "response" {
				for _, k := range append(append([]string{}, tokenFields...), "reported_total_tokens", "timestamp") {
					if v, ok := old[k]; ok {
						r[k] = v
					}
				}
				r["quality"] = old["quality"]
			}
		}
		for _, k := range []string{"session_title", "prompt_preview", "output_preview", "model", "service_tier", "provider", "model_context_window", "limit_id", "duration_ms"} {
			if r[k] == nil || r[k] == "" || r[k] == "unknown" {
				r[k] = old[k]
			}
		}
		if ValueBool(old["context_owner_verified"]) {
			r["context_owner_verified"] = true
		}
	}
	payload := dataJSON(r)
	if payload != raw {
		if _, e := tx.Exec(`INSERT INTO usage_records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,model=excluded.model,session_id=excluded.session_id,data=excluded.data`, id, dataString(r, "timestamp"), firstString(r["model"], "unknown"), dataString(r, "session_id"), payload); e != nil {
			return e
		}
	}
	if _, e := tx.Exec("INSERT OR IGNORE INTO usage_record_sources VALUES(?,?)", id, source); e != nil {
		return e
	}
	return saveOrigin(tx, source, file, "record", id, generation)
}
func saveOrigin(tx *sql.Tx, source, file, kind, id, generation string) error {
	_, e := tx.Exec(`INSERT INTO usage_origins VALUES(?,?,?,?,?) ON CONFLICT(source_id,file_key,kind,item_id) DO UPDATE SET generation=excluded.generation`, source, file, kind, id, generation)
	return e
}
func saveMetadata(tx *sql.Tx, table, kind string, r Row, source, file, generation string) error {
	r["source_id"] = source
	r["_generation"] = generation
	id := source + "|" + file + "|" + dataString(r, "id")
	payload := dataJSON(r)
	var old string
	_ = tx.QueryRow("SELECT data FROM "+table+" WHERE id=?", id).Scan(&old)
	if old != "" {
		a, b := dataRow(old), CloneRow(r)
		for _, k := range []string{"observed_at", "_generation"} {
			delete(a, k)
			delete(b, k)
		}
		if dataJSON(a) == dataJSON(b) {
			return saveOrigin(tx, source, file, kind, id, generation)
		}
	}
	_, e := tx.Exec("INSERT INTO "+table+" VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data", id, payload)
	if e != nil {
		return e
	}
	return saveOrigin(tx, source, file, kind, id, generation)
}
func saveMessage(tx *sql.Tx, id string, patch Row) error {
	if id == "" || len(id) > 512 {
		return nil
	}
	var raw string
	_ = tx.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", id).Scan(&raw)
	v := dataRow(raw)
	for k, x := range patch {
		v[k] = x
	}
	payload := dataJSON(v)
	if payload == raw {
		return nil
	}
	if len(payload) > 1048576 {
		v["user"] = clip(dataString(v, "user"), 1200)
		v["final"] = clip(dataString(v, "final"), 5000)
		v["user_complete"] = false
		v["final_complete"] = false
		v["availability"] = "capacity"
		payload = dataJSON(v)
	}
	_, e := tx.Exec(`INSERT INTO usage_request_messages VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,digest=excluded.digest,revision=excluded.revision,size=excluded.size,accessed=excluded.accessed`, id, payload, HashString(payload), time.Now().UnixMilli(), len(payload), float64(time.Now().Unix()))
	return e
}
func (s *Store) seedMock() error {
	var n int
	_ = s.db.QueryRow("SELECT count(*) FROM usage_records").Scan(&n)
	if n > 0 {
		return nil
	}
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	for i := 0; i < 3; i++ {
		stamp := UTCStamp(time.Now().Add(-time.Duration(i+1) * time.Hour))
		id := fmt.Sprintf("mock-%d", i)
		r := Row{"id": id, "response_id": id, "session_id": "mock-chat", "turn_id": id, "request_turn_id": id, "timestamp": stamp, "model": "gpt-6.1-sol", "provider": "openai", "service_tier": "default", "quality": "response", "input_tokens": 1200 + i*100, "cached_input_tokens": 400, "cache_write_input_tokens": 0, "output_tokens": 300, "reasoning_output_tokens": 100, "total_tokens": 1500 + i*100, "prompt_preview": "整理项目进度并核对数据", "output_preview": "已整理当前项目进度。", "context_owner_verified": true, "session_title": "Codexio 开发"}
		if e = s.saveRecord(tx, r, "mock", "mock", "1"); e != nil {
			return e
		}
		m := CloneRow(r)
		m["id"] = turnKey("mock-chat", id)
		m["started_at"] = stamp
		m["ended_at"] = stamp
		m["status"] = "completed"
		m["verified"] = true
		m["has_usage"] = true
		m["has_user_message"] = true
		if e = saveMetadata(tx, "usage_turns", "turn", m, "mock", "mock", "1"); e != nil {
			return e
		}
		if e = saveMessage(tx, dataString(m, "id"), Row{"user": "整理项目进度并核对数据", "final": "已整理当前项目进度。", "user_complete": true, "final_complete": true}); e != nil {
			return e
		}
	}
	return tx.Commit()
}
