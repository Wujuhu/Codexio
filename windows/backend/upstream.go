package backend

// This is a port of ManagedRoute/ResponseObserver: only the selected base_url
// field is changed, authentication and the rest of the user's TOML are retained.
import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/pelletier/go-toml/v2"
)

type UpstreamProxy struct {
	mu              sync.Mutex
	directory       string
	config          func() Row
	mock            bool
	changed         func()
	server          *http.Server
	listener        net.Listener
	db              *sql.DB
	journal         Row
	status          string
	lastError       string
	restartRequired bool
	websockets      map[*upstreamDuplex]bool
	observations    chan Row
	cancel          context.CancelFunc
}

func NewUpstreamProxy(dir string, config func() Row, mock bool, changed func()) *UpstreamProxy {
	return &UpstreamProxy{directory: dir, config: config, mock: mock, changed: changed, status: "disabled", observations: make(chan Row, 64), websockets: map[*upstreamDuplex]bool{}}
}
func (p *UpstreamProxy) Public() Row {
	p.mu.Lock()
	defer p.mu.Unlock()
	return Row{"enabled": p.server != nil, "status": p.status, "error": p.lastError, "restart_required": p.restartRequired}
}
func (p *UpstreamProxy) Toggle(enabled bool) (Row, error) {
	if p.mock {
		return p.Public(), errors.New("模拟模式不会修改 Codex 配置")
	}
	if !enabled {
		e := p.Stop()
		return p.Public(), e
	}
	p.mu.Lock()
	if p.server != nil {
		p.mu.Unlock()
		return p.Public(), nil
	}
	defer p.mu.Unlock()
	unlock, e := upstreamConfigLock(filepath.Join(p.directory, "upstream", "route.lock"))
	if e != nil {
		return nil, e
	}
	defer unlock()
	if upstreamLegacyRouteBusy(p.directory) {
		return nil, errors.New("旧版 Codexio 的上游转发仍在运行，请先退出旧版")
	}
	if e := p.restoreJournal(); e != nil {
		return Row{"enabled": false, "error": e.Error()}, e
	}
	plan, e := upstreamResolve()
	if e != nil {
		return Row{"enabled": false, "error": e.Error()}, e
	}
	target, e := url.Parse(ValueString(plan["origin"]))
	if e != nil || target.Host == "" || target.User != nil || target.RawQuery != "" || target.Fragment != "" || (target.Scheme != "http" && target.Scheme != "https") {
		return nil, errors.New("当前模型服务 base_url 无效")
	}
	ln, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		return nil, e
	}
	random := make([]byte, 32)
	if _, e = rand.Read(random); e != nil {
		ln.Close()
		return nil, e
	}
	token := base64.RawURLEncoding.EncodeToString(random)
	endpoint := "http://" + ln.Addr().String() + "/" + token + "/v1"
	path := ValueString(plan["patch_path"])
	original, e := os.ReadFile(path)
	existed := e == nil
	if e != nil && !os.IsNotExist(e) {
		ln.Close()
		return nil, e
	}
	locator := ValueStrings(plan["locator"])
	table, key := locator[:len(locator)-1], locator[len(locator)-1]
	line := upstreamQuoteKey(key) + " = " + strconvQuote(endpoint)
	patched, old, present, e := upstreamPatch(string(original), table, key, &line)
	if e != nil {
		ln.Close()
		return nil, e
	}
	j := Row{"route_version": "go-1", "config": path, "locator": locator, "original_present": present, "original_line": old, "original_endpoint": plan["original_endpoint"], "applied_endpoint": endpoint, "existed": existed, "original_digest": upstreamDigest([]any{present, plan["original_endpoint"]}), "applied_digest": upstreamDigest([]any{true, endpoint})}
	if e = WriteJSON(p.journalPath(), j); e != nil {
		ln.Close()
		return nil, e
	}
	// Check again immediately before writing; preserve concurrent user edits.
	current, _ := os.ReadFile(path)
	if !bytes.Equal(current, original) {
		_ = os.Remove(p.journalPath())
		ln.Close()
		return nil, errors.New("Codex 配置同时发生变化，请重试")
	}
	if e = writePrivateFile(path, []byte(patched)); e != nil {
		_ = os.Remove(p.journalPath())
		ln.Close()
		return nil, e
	}
	db, e := sql.Open("sqlite", filepath.Join(p.directory, "upstream.sqlite"))
	if e != nil {
		ln.Close()
		_ = p.restoreJournal()
		return nil, e
	}
	db.SetMaxOpenConns(1)
	if _, e = db.Exec(`PRAGMA journal_mode=WAL;PRAGMA busy_timeout=5000;CREATE TABLE IF NOT EXISTS observations(response_id TEXT PRIMARY KEY,model TEXT NOT NULL,event TEXT NOT NULL,rank INTEGER NOT NULL,observed_at REAL NOT NULL);CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY,value INTEGER NOT NULL);INSERT OR IGNORE INTO revision VALUES(1,0)`); e != nil {
		db.Close()
		ln.Close()
		_ = p.restoreJournal()
		return nil, e
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	basePath := strings.TrimRight(target.Path, "/")
	proxy.Director = func(r *http.Request) {
		escaped := r.URL.EscapedPath()
		r.URL.Scheme = target.Scheme
		r.URL.Host = target.Host
		r.URL.Path = basePath + strings.TrimPrefix(r.URL.Path, "/"+token+"/v1")
		r.URL.RawPath = strings.TrimRight(target.EscapedPath(), "/") + strings.TrimPrefix(escaped, "/"+token+"/v1")
		r.Host = target.Host
		r.Header.Del("Sec-WebSocket-Extensions")
		r.Header.Del("X-Codexio-Route")
		r.Header.Set("Accept-Encoding", "identity")
	}
	proxy.Transport = &http.Transport{Proxy: http.ProxyFromEnvironment, ForceAttemptHTTP2: false, TLSHandshakeTimeout: 15 * time.Second, ResponseHeaderTimeout: 60 * time.Second, IdleConnTimeout: 90 * time.Second, MaxIdleConns: 16}
	proxy.ModifyResponse = func(r *http.Response) error {
		if r.StatusCode == 101 {
			if rw, ok := r.Body.(io.ReadWriteCloser); ok {
				connection := &upstreamDuplex{ReadWriteCloser: rw, observer: &upstreamFrameObserver{emit: p.observe}}
				connection.closed = func() { p.mu.Lock(); delete(p.websockets, connection); p.mu.Unlock() }
				p.mu.Lock()
				active := p.server != nil
				if active {
					p.websockets[connection] = true
				}
				p.mu.Unlock()
				if !active {
					_ = rw.Close()
					return errors.New("上游转发已经停止")
				}
				r.Body = connection
			}
			return nil
		}
		if r.StatusCode >= 200 && r.StatusCode < 300 && strings.Contains(r.Request.URL.Path, "/responses") && (r.Header.Get("Content-Encoding") == "" || r.Header.Get("Content-Encoding") == "identity") {
			sse := strings.Contains(r.Header.Get("Content-Type"), "text/event-stream")
			r.Body = &upstreamBody{ReadCloser: r.Body, observer: &upstreamLineObserver{emit: p.observe, sse: sse, determined: sse}}
		}
		return nil
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		http.Error(w, "Upstream service unavailable", http.StatusBadGateway)
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		parts := strings.SplitN(strings.TrimPrefix(r.URL.Path, "/"), "/", 3)
		if len(parts) < 2 || parts[1] != "v1" || subtle.ConstantTimeCompare([]byte(parts[0]), []byte(token)) != 1 {
			http.NotFound(w, r)
			return
		}
		proxy.ServeHTTP(w, r)
	})
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 90 * time.Second, MaxHeaderBytes: 1 << 20}
	ctx, cancel := context.WithCancel(context.Background())
	p.server = server
	p.listener = ln
	p.db = db
	p.journal = j
	p.cancel = cancel
	p.status = "running"
	p.restartRequired = true
	p.lastError = ""
	go p.writeObservations(ctx)
	go func() { _ = server.Serve(ln) }()
	if p.changed != nil {
		go p.changed()
	}
	return Row{"enabled": true, "status": "running", "restart_required": true}, nil
}
func (p *UpstreamProxy) Stop() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.mock {
		return nil
	}
	if p.server == nil && upstreamLegacyRouteBusy(p.directory) {
		return errors.New("旧版 Codexio 的上游转发仍在运行，未更改其配置")
	}
	unlock, e := upstreamConfigLock(filepath.Join(p.directory, "upstream", "route.lock"))
	if e != nil {
		return e
	}
	defer unlock()
	if e := p.restoreJournal(); e != nil {
		p.lastError = e.Error()
		return e
	}
	if p.server != nil {
		p.restartRequired = true
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = p.server.Shutdown(ctx)
		cancel()
		_ = p.server.Close()
		p.server = nil
	}
	for socket := range p.websockets {
		_ = socket.ReadWriteCloser.Close()
	}
	p.websockets = map[*upstreamDuplex]bool{}
	if p.cancel != nil {
		p.cancel()
		p.cancel = nil
	}
	if p.db != nil {
		_ = p.db.Close()
		p.db = nil
	}
	p.listener = nil
	p.status = "disabled"
	p.lastError = ""
	if p.changed != nil {
		go p.changed()
	}
	return nil
}
func (p *UpstreamProxy) journalPath() string {
	return filepath.Join(p.directory, "upstream", "route.json")
}
func (p *UpstreamProxy) restoreJournal() error {
	j, e := ReadJSON(p.journalPath())
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return errors.New("上游恢复记录无法读取，已保留配置")
	}
	if ValueString(j["route_version"]) != "go-1" && ValueInt(j["route_version"]) != 3 {
		return p.restoreLegacyJournal(j)
	}
	if ValueString(j["original_digest"]) != upstreamDigest([]any{ValueBool(j["original_present"]), j["original_endpoint"]}) || ValueString(j["applied_digest"]) != upstreamDigest([]any{true, j["applied_endpoint"]}) {
		return errors.New("上游恢复记录校验失败，已保留原配置")
	}
	path := ValueString(j["config"])
	locator := ValueStrings(j["locator"])
	if path == "" || len(locator) == 0 {
		return errors.New("上游恢复记录无效")
	}
	text, e := os.ReadFile(path)
	if e != nil && !os.IsNotExist(e) {
		return e
	}
	var doc map[string]any
	if e = toml.Unmarshal(text, &doc); e != nil {
		return errors.New("Codex 配置无效，无法安全恢复")
	}
	actual, present := upstreamNested(doc, locator)
	originalPresent := ValueBool(j["original_present"])
	original := j["original_endpoint"]
	if !present && !originalPresent || present == originalPresent && ValueString(actual) == ValueString(original) {
		return os.Remove(p.journalPath())
	}
	if ValueString(actual) != ValueString(j["applied_endpoint"]) {
		// A user changed the selected endpoint. Keep that change and stop only
		// our own route; it no longer points at this process.
		return os.Remove(p.journalPath())
	}
	var line *string
	if originalPresent {
		v := ValueString(j["original_line"])
		if v == "" {
			v = upstreamQuoteKey(locator[len(locator)-1]) + " = " + strconvQuote(ValueString(original))
		}
		line = &v
	}
	restored, _, _, e := upstreamPatch(string(text), locator[:len(locator)-1], locator[len(locator)-1], line)
	if e != nil {
		return e
	}
	check, _ := os.ReadFile(path)
	if !bytes.Equal(check, text) {
		return errors.New("Codex 配置同时发生变化，请重试恢复")
	}
	if e = writePrivateFile(path, []byte(restored)); e != nil {
		return e
	}
	return os.Remove(p.journalPath())
}
func upstreamDigest(value any) string {
	var b bytes.Buffer
	encoder := json.NewEncoder(&b)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(value)
	return HashString(strings.TrimSuffix(b.String(), "\n"))
}
func tomlDecodeProfile(value string, out *Row) error {
	var m map[string]any
	if e := toml.Unmarshal([]byte(value), &m); e != nil {
		return errors.New("客户端配置方案参数无效")
	}
	*out = Row(m)
	return nil
}
func (p *UpstreamProxy) RestartClient() error {
	if p.mock {
		return errors.New("模拟模式不会重启真实客户端")
	}
	if e := upstreamRestartDesktop(); e != nil {
		return e
	}
	p.mu.Lock()
	p.restartRequired = false
	p.mu.Unlock()
	if p.changed != nil {
		p.changed()
	}
	return nil
}
func upstreamNested(doc map[string]any, path []string) (any, bool) {
	var v any = doc
	for _, k := range path {
		m := ValueRow(v)
		next, ok := m[k]
		if !ok {
			return nil, false
		}
		v = next
	}
	return v, true
}
func upstreamReadTOML(path string) (Row, error) {
	b, e := os.ReadFile(path)
	if os.IsNotExist(e) {
		return Row{}, nil
	}
	if e != nil {
		return nil, e
	}
	var m map[string]any
	if e = toml.Unmarshal(b, &m); e != nil {
		return nil, e
	}
	return Row(m), nil
}
func upstreamMerge(a, b Row) Row {
	r := CloneRow(a)
	for k, v := range b {
		if _, ok := v.(map[string]any); ok {
			r[k] = upstreamMerge(ValueRow(r[k]), ValueRow(v))
		} else {
			r[k] = v
		}
	}
	return r
}
func upstreamResolve() (Row, error) {
	rootPath, selectedProfile, e := upstreamDesktopRoute()
	if e != nil {
		return nil, e
	}
	root, e := upstreamReadTOML(rootPath)
	if e != nil {
		return nil, errors.New("Codex 配置不是有效 TOML")
	}
	profile := ValueString(root["profile"])
	if selectedProfile != "" {
		profile = selectedProfile
	}
	overlay := Row{}
	overlayPath := rootPath
	prefix := []string{}
	if profile != "" {
		if strings.ContainsAny(profile, `/\`) || profile == "." || profile == ".." {
			return nil, errors.New("配置方案名称无效")
		}
		separate := filepath.Join(filepath.Dir(rootPath), profile+".config.toml")
		if _, e = os.Stat(separate); e == nil {
			overlayPath = separate
			overlay, e = upstreamReadTOML(separate)
			if e != nil {
				return nil, e
			}
		} else {
			overlay = ValueRow(ValueRow(root["profiles"])[profile])
			if len(overlay) == 0 {
				return nil, errors.New("当前配置方案不存在")
			}
			prefix = []string{"profiles", profile}
		}
	}
	effective := upstreamMerge(root, overlay)
	provider := ValueString(effective["model_provider"])
	if provider == "" {
		provider = "openai"
	}
	for _, unsupported := range []string{"amazon-bedrock", "ollama", "lmstudio"} {
		if provider == unsupported {
			return nil, errors.New("当前内置模型供应商没有可接管的 Responses base_url")
		}
	}
	path := rootPath
	locator := []string{"openai_base_url"}
	origin := ValueString(effective["openai_base_url"])
	var original any
	var originalPresent bool
	if provider == "openai" {
		if _, ok := overlay["openai_base_url"]; ok {
			path = overlayPath
			locator = append(prefix, "openai_base_url")
		} else if _, ok := root["openai_base_url"]; !ok && profile != "" {
			path = overlayPath
			locator = append(prefix, "openai_base_url")
		}
		if origin == "" {
			auth, _ := systemReadJSON(filepath.Join(filepath.Dir(rootPath), "auth.json"), 1<<20)
			if ValueString(effective["forced_login_method"]) == "api" || ValueString(auth["OPENAI_API_KEY"]) != "" {
				origin = "https://api.openai.com/v1"
			} else {
				origin = "https://chatgpt.com/backend-api/codex"
			}
		}
	} else {
		base := ValueRow(ValueRow(root["model_providers"])[provider])
		over := ValueRow(ValueRow(overlay["model_providers"])[provider])
		p := upstreamMerge(base, over)
		if len(p) == 0 {
			return nil, errors.New("当前模型供应商未定义")
		}
		if api := ValueString(p["wire_api"]); api != "" && api != "responses" {
			return nil, errors.New("上游检测仅支持 Responses API")
		}
		origin = ValueString(p["base_url"])
		locator = []string{"model_providers", provider, "base_url"}
		if _, ok := over["base_url"]; ok {
			path = overlayPath
			locator = append(append([]string{}, prefix...), locator...)
		}
	}
	u, e := url.Parse(origin)
	if e != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && u.Scheme != "http") {
		return nil, errors.New("模型服务 base_url 无效或包含不支持的账号/查询参数")
	}
	doc, e := upstreamReadTOML(path)
	if e != nil {
		return nil, e
	}
	original, originalPresent = upstreamNested(map[string]any(doc), locator)
	if originalPresent {
		if _, ok := original.(string); !ok {
			return nil, errors.New("当前模型服务 base_url 字段无效")
		}
	}
	return Row{"root_config": rootPath, "patch_path": path, "locator": locator, "origin": strings.TrimRight(origin, "/"), "provider": provider, "original_endpoint": original, "original_present": originalPresent}, nil
}
func strconvQuote(s string) string { b, _ := json.Marshal(s); return string(b) }
func upstreamQuoteKey(s string) string {
	if regexp.MustCompile(`^[A-Za-z0-9_-]+$`).MatchString(s) {
		return s
	}
	return strconvQuote(s)
}
func upstreamHeader(table []string) string {
	parts := make([]string, len(table))
	for i, s := range table {
		parts[i] = upstreamQuoteKey(s)
	}
	return "[" + strings.Join(parts, ".") + "]"
}
func upstreamHeaderParts(header string) ([]string, bool) {
	var m map[string]any
	if toml.Unmarshal([]byte(header+"\n__codexio_probe__=true\n"), &m) != nil {
		return nil, false
	}
	parts := []string{}
	current := Row(m)
	for {
		if _, ok := current["__codexio_probe__"]; ok {
			return parts, true
		}
		if len(current) != 1 {
			return nil, false
		}
		for k, v := range current {
			parts = append(parts, k)
			current = ValueRow(v)
		}
	}
}
func upstreamPatch(text string, table []string, key string, line *string) (string, string, bool, error) {
	newline := "\n"
	if strings.Contains(text, "\r\n") {
		newline = "\r\n"
	}
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	current := []string{}
	match := len(table) == 0
	tableFound := match
	insert := len(lines)
	old := ""
	found := -1
	for i, l := range lines {
		trim := strings.TrimSpace(l)
		if strings.HasPrefix(trim, "[") {
			header := trim
			if at := strings.Index(header, "]"); at >= 0 {
				header = header[:at+1]
			}
			parts, ok := upstreamHeaderParts(header)
			if !ok {
				return "", "", false, errors.New("无法安全定位 Codex 配置表")
			}
			if match && insert == len(lines) {
				insert = i
			}
			current = parts
			match = rowJSON(current) == rowJSON(table)
			if match {
				tableFound = true
				insert = i + 1
			}
			continue
		}
		if match {
			lhs := strings.TrimSpace(strings.SplitN(trim, "=", 2)[0])
			if lhs == key || lhs == upstreamQuoteKey(key) {
				found = i
				old = l
				break
			}
			if i >= insert {
				insert = i + 1
			}
		}
	}
	if found >= 0 {
		if line == nil {
			lines = append(lines[:found], lines[found+1:]...)
		} else {
			lines[found] = *line
		}
		patched := strings.Join(lines, newline)
		var verify map[string]any
		if e := toml.Unmarshal([]byte(patched), &verify); e != nil {
			return "", "", false, errors.New("该配置写法不能安全替换，已保留原配置")
		}
		return patched, old, true, nil
	}
	if line == nil {
		return text, "", false, nil
	}
	if !tableFound {
		if len(lines) > 0 && lines[len(lines)-1] != "" {
			lines = append(lines, "")
		}
		lines = append(lines, upstreamHeader(table), *line, "")
	} else {
		if insert > len(lines) {
			insert = len(lines)
		}
		lines = append(lines, "")
		copy(lines[insert+1:], lines[insert:])
		lines[insert] = *line
	}
	patched := strings.Join(lines, newline)
	var verify map[string]any
	if e := toml.Unmarshal([]byte(patched), &verify); e != nil {
		return "", "", false, e
	}
	return patched, "", false, nil
}

func (p *UpstreamProxy) observe(data []byte) {
	if len(data) > 2<<20 {
		return
	}
	r, e := DecodeRow(data)
	if e != nil {
		return
	}
	response := ValueRow(r["response"])
	id, model := ValueString(response["id"]), ValueString(response["model"])
	if id == "" {
		id = firstString(r["response_id"], r["id"])
	}
	if model == "" {
		model = ValueString(r["model"])
	}
	if id == "" || model == "" || len(id) > 256 || len(model) > 256 {
		return
	}
	event := ValueString(r["type"])
	rank := 2
	if event == "response.created" {
		rank = 1
	}
	select {
	case p.observations <- Row{"id": id, "model": model, "event": event, "rank": rank}:
	default:
	}
}
func (p *UpstreamProxy) writeObservations(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case r := <-p.observations:
			p.mu.Lock()
			db := p.db
			p.mu.Unlock()
			if db == nil {
				continue
			}
			var oldModel string
			var oldRank int
			e := db.QueryRow(`SELECT model,rank FROM observations WHERE response_id=?`, r["id"]).Scan(&oldModel, &oldRank)
			rank := int(ValueInt(r["rank"]))
			if e == nil && (oldRank > rank || oldRank == rank && oldModel == r["model"]) {
				continue
			}
			tx, e := db.BeginTx(ctx, nil)
			if e != nil {
				continue
			}
			_, e = tx.ExecContext(ctx, `INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(response_id) DO UPDATE SET model=excluded.model,event=excluded.event,rank=excluded.rank,observed_at=excluded.observed_at`, r["id"], r["model"], r["event"], rank, float64(time.Now().UnixNano())/1e9)
			if e == nil {
				_, e = tx.ExecContext(ctx, `UPDATE revision SET value=value+1 WHERE id=1`)
			}
			if e == nil {
				e = tx.Commit()
			} else {
				_ = tx.Rollback()
			}
			if e == nil && p.changed != nil {
				p.changed()
			}
		}
	}
}

type upstreamLineObserver struct {
	emit       func([]byte)
	sse        bool
	determined bool
	buffer     []byte
	dropping   bool
}

func (o *upstreamLineObserver) feed(b []byte, eof bool) {
	if !o.determined {
		prefix := append(append([]byte{}, o.buffer...), b...)
		trim := bytes.TrimSpace(prefix)
		if len(trim) == 0 && !eof {
			o.buffer = prefix
			return
		}
		if len(trim) > 0 && (trim[0] == '{' || trim[0] == '[') {
			o.determined = true
			o.sse = false
		} else if bytes.HasPrefix(trim, []byte("data:")) || bytes.HasPrefix(trim, []byte("event:")) || bytes.HasPrefix(trim, []byte(":")) {
			o.determined = true
			o.sse = true
		} else if len(prefix) > 256 || eof {
			o.determined = true
			o.sse = false
		} else {
			o.buffer = prefix
			return
		}
		o.buffer = nil
		b = prefix
	}
	if !o.sse {
		if len(o.buffer)+len(b) <= 2<<20 {
			o.buffer = append(o.buffer, b...)
		} else {
			o.dropping = true
			o.buffer = nil
		}
		if eof && !o.dropping {
			o.emit(o.buffer)
		}
		return
	}
	for _, c := range b {
		if c == '\n' {
			if !o.dropping && bytes.HasPrefix(o.buffer, []byte("data:")) {
				o.emit(bytes.TrimSpace(o.buffer[5:]))
			}
			o.buffer = nil
			o.dropping = false
			continue
		}
		if !o.dropping {
			if len(o.buffer) >= 2<<20 {
				o.dropping = true
				o.buffer = nil
			} else {
				o.buffer = append(o.buffer, c)
			}
		}
	}
	if eof && !o.dropping && bytes.HasPrefix(o.buffer, []byte("data:")) {
		o.emit(bytes.TrimSpace(o.buffer[5:]))
		o.buffer = nil
	}
}

type upstreamBody struct {
	io.ReadCloser
	observer *upstreamLineObserver
}

func (b *upstreamBody) Read(p []byte) (int, error) {
	n, e := b.ReadCloser.Read(p)
	b.observer.feed(p[:n], e == io.EOF)
	return n, e
}

type upstreamDuplex struct {
	io.ReadWriteCloser
	observer  *upstreamFrameObserver
	closed    func()
	closeOnce sync.Once
}

func (d *upstreamDuplex) Close() error {
	err := d.ReadWriteCloser.Close()
	d.closeOnce.Do(func() {
		if d.closed != nil {
			d.closed()
		}
	})
	return err
}

func (d *upstreamDuplex) Read(p []byte) (int, error) {
	n, e := d.ReadWriteCloser.Read(p)
	d.observer.feed(p[:n])
	return n, e
}

type upstreamFrameObserver struct {
	emit       func([]byte)
	buffer     []byte
	message    []byte
	skip       uint64
	compressed bool
}

func (o *upstreamFrameObserver) feed(b []byte) {
	if o.skip > 0 {
		n := min(uint64(len(b)), o.skip)
		b = b[n:]
		o.skip -= n
		if len(b) == 0 {
			return
		}
	}
	if len(o.buffer)+len(b) > 4<<20 {
		o.buffer = nil
		o.message = nil
		return
	}
	o.buffer = append(o.buffer, b...)
	for len(o.buffer) >= 2 {
		first, second := o.buffer[0], o.buffer[1]
		length := uint64(second & 127)
		header := 2
		if length == 126 {
			if len(o.buffer) < 4 {
				return
			}
			length = uint64(binary.BigEndian.Uint16(o.buffer[2:4]))
			header = 4
		} else if length == 127 {
			if len(o.buffer) < 10 {
				return
			}
			length = binary.BigEndian.Uint64(o.buffer[2:10])
			header = 10
		}
		if second&128 != 0 {
			header += 4
		}
		if uint64(len(o.buffer)) < uint64(header) {
			return
		}
		if length > 2<<20 {
			o.skip = length
			rest := o.buffer[header:]
			n := min(uint64(len(rest)), o.skip)
			o.skip -= n
			o.buffer = rest[n:]
			o.message = nil
			continue
		}
		if uint64(len(o.buffer)) < uint64(header)+length {
			return
		}
		payload := o.buffer[header : header+int(length)]
		opcode := first & 15
		if first&64 != 0 {
			o.compressed = true
		}
		if opcode == 1 || opcode == 2 {
			o.message = nil
			o.compressed = first&64 != 0
		}
		if opcode <= 2 && !o.compressed && len(o.message)+len(payload) <= 2<<20 {
			o.message = append(o.message, payload...)
		}
		if first&128 != 0 && opcode <= 2 {
			if !o.compressed {
				o.emit(o.message)
			}
			o.message = nil
			o.compressed = false
		}
		o.buffer = o.buffer[header+int(length):]
	}
}
