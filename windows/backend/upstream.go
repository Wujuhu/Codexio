package backend

// This is a port of ManagedRoute/ResponseObserver: only the selected base_url
// field is changed, authentication and the rest of the user's TOML are retained.
import (
	"bufio"
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
	version         string
	config          func() Row
	mock            bool
	changed         func()
	server          *http.Server
	listener        net.Listener
	db              *sql.DB
	transport       *http.Transport
	journal         Row
	status          string
	lastError       string
	restartRequired bool
	desiredEnabled  bool
	routeChanged    bool
	clientRunning   bool
	alreadyTargeted bool
	websockets      map[*upstreamDuplex]bool
	observations    chan Row
	cancel          context.CancelFunc
}

func NewUpstreamProxy(dir, version string, config func() Row, mock bool, changed func()) *UpstreamProxy {
	return &UpstreamProxy{directory: dir, version: version, config: config, mock: mock, changed: changed, status: "disabled", desiredEnabled: ValueBool(config()["upstream_detection_enabled"]), observations: make(chan Row, 64), websockets: map[*upstreamDuplex]bool{}}
}
func (p *UpstreamProxy) Public() Row {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.publicLocked()
}
func (p *UpstreamProxy) publicLocked() Row {
	return Row{"enabled": p.server != nil, "desired_enabled": p.desiredEnabled, "status": p.status, "error": p.lastError, "route_changed": p.routeChanged, "client_running": p.clientRunning, "already_targeted": p.alreadyTargeted, "restart_required": p.restartRequired}
}
func (p *UpstreamProxy) Toggle(enabled bool) (Row, error) {
	if p.mock {
		return p.Public(), errors.New("模拟模式不会修改 Codex 配置")
	}
	clientRunning, e := upstreamDesktopClientRunning()
	if e != nil {
		return p.Public(), e
	}
	if !enabled {
		e := p.stop(clientRunning)
		return p.Public(), e
	}
	p.mu.Lock()
	if p.server != nil {
		client, err := p.openConfigClient()
		if err != nil {
			p.mu.Unlock()
			return p.Public(), err
		}
		matched, err := client.targets(ValueStrings(p.journal["base_path"]), ValueString(p.journal["applied_endpoint"]))
		client.close()
		if err != nil || !matched {
			p.alreadyTargeted = false
			p.mu.Unlock()
			if err == nil {
				err = errors.New("路由已被其他程序修改，保留当前配置和转发")
			}
			return p.Public(), err
		}
		p.desiredEnabled = true
		p.routeChanged = false
		p.clientRunning = clientRunning
		p.alreadyTargeted = true
		p.status = "running"
		p.lastError = ""
		if !clientRunning {
			p.restartRequired = false
		}
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
	previous, _ := ReadJSON(p.journalPath())
	if e := p.restoreJournal(); e != nil {
		return Row{"enabled": false, "error": e.Error()}, e
	}
	client, e := p.openConfigClient()
	if e != nil {
		return p.publicLocked(), e
	}
	defer client.close()
	plan, e := client.resolve()
	if e != nil {
		return Row{"enabled": false, "error": e.Error()}, e
	}
	target, e := url.Parse(ValueString(plan["origin"]))
	if e != nil || target.Host == "" || target.User != nil || target.RawQuery != "" || target.Fragment != "" || (target.Scheme != "http" && target.Scheme != "https") {
		return nil, errors.New("当前模型服务 base_url 无效")
	}
	address, token := upstreamResumeRoute(previous, plan)
	ln, e := net.Listen("tcp", address)
	if e != nil && address != "127.0.0.1:0" {
		ln, e = net.Listen("tcp", "127.0.0.1:0")
		token = ""
	}
	if e != nil {
		return nil, e
	}
	if token == "" {
		random := make([]byte, 32)
		if _, e = rand.Read(random); e != nil {
			ln.Close()
			return nil, e
		}
		token = base64.RawURLEncoding.EncodeToString(random)
	}
	localPath := ValueString(plan["local_path"])
	endpoint := "http://" + ln.Addr().String() + "/" + token + localPath
	path := ValueString(plan["patch_path"])
	locator := ValueStrings(plan["locator"])
	before, present := plan["original_endpoint"], ValueBool(plan["original_present"])
	j := Row{"route_version": "go-rpc-1", "config": path, "locator": locator, "base_path": plan["base_path"], "original_present": present, "original_endpoint": before, "applied_endpoint": endpoint, "origin": plan["origin"], "provider_id": plan["provider"], "local_path": localPath, "original_digest": upstreamDigest([]any{present, before}), "applied_digest": upstreamDigest([]any{true, endpoint})}
	db, e := sql.Open("sqlite", filepath.Join(p.directory, "upstream.sqlite"))
	if e != nil {
		ln.Close()
		return nil, e
	}
	db.SetMaxOpenConns(1)
	if _, e = db.Exec(`PRAGMA journal_mode=WAL;PRAGMA busy_timeout=5000;CREATE TABLE IF NOT EXISTS observations(response_id TEXT PRIMARY KEY,model TEXT NOT NULL,event TEXT NOT NULL,rank INTEGER NOT NULL,observed_at REAL NOT NULL);CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY,value INTEGER NOT NULL);INSERT OR IGNORE INTO revision VALUES(1,0)`); e != nil {
		db.Close()
		ln.Close()
		return nil, e
	}
	handler := p.relayHandler(target, token, localPath)
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 90 * time.Second, MaxHeaderBytes: 1 << 20}
	ctx, cancel := context.WithCancel(context.Background())
	p.server = server
	p.listener = ln
	p.db = db
	p.journal = j
	p.cancel = cancel
	go p.writeObservations(ctx)
	go func() { _ = server.Serve(ln) }()
	// The relay is accepting requests before Codex adopts its address, like Mac.
	if e = WriteJSON(p.journalPath(), j); e != nil {
		p.closeRelayLocked()
		return p.publicLocked(), e
	}
	e = client.write(locator, endpoint, path, ValueString(plan["version"]))
	if e == nil {
		var matched bool
		matched, e = client.targets(ValueStrings(plan["base_path"]), endpoint)
		if e == nil && !matched {
			e = errors.New("路由未生效，正在恢复")
		}
	}
	if e != nil {
		// A separate RPC deadline permits rollback even after the write timed out.
		restoreError := p.restoreJournal()
		if restoreError == nil {
			p.closeRelayLocked()
		} else {
			p.status = "error"
			p.lastError = restoreError.Error()
		}
		return p.publicLocked(), errors.Join(e, restoreError)
	}
	p.status = "running"
	p.desiredEnabled = true
	p.routeChanged = true
	p.clientRunning = clientRunning
	p.alreadyTargeted = true
	p.restartRequired = clientRunning
	p.lastError = ""
	if p.changed != nil {
		go p.changed()
	}
	return p.publicLocked(), nil
}

// upstreamResumeRoute reuses only a validated recovery record for the same
// provider/origin. An old client can keep /token/v1 until it reloads its config.
func upstreamResumeRoute(j, plan Row) (string, string) {
	fallback := "127.0.0.1:0"
	if ValueString(j["origin"]) != ValueString(plan["origin"]) || ValueString(j["provider_id"]) != ValueString(plan["provider"]) || ValueString(j["applied_digest"]) != upstreamDigest([]any{true, j["applied_endpoint"]}) {
		return fallback, ""
	}
	u, err := url.Parse(ValueString(j["applied_endpoint"]))
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return fallback, ""
	}
	parts := strings.SplitN(strings.TrimPrefix(u.EscapedPath(), "/"), "/", 2)
	if len(parts) != 2 || !regexp.MustCompile(`^[A-Za-z0-9_-]{32,64}$`).MatchString(parts[0]) || (parts[1] != "v1" && parts[1] != "backend-api/codex") {
		return fallback, ""
	}
	return u.Host, parts[0]
}
func upstreamRouteMatches(path, token, localPath string) bool {
	for _, suffix := range []string{localPath, "/v1"} {
		prefix := "/" + token + suffix
		if path == prefix || strings.HasPrefix(path, prefix+"/") {
			return true
		}
	}
	return false
}
func suffixPath(path, token, localPath string) string {
	// Handler validates the escaped prefix first. Strip it once; do not clean
	// or decode the suffix, so %2F, percent signs and queries survive intact.
	for _, suffix := range []string{localPath, "/v1"} {
		prefix := "/" + token + suffix
		if path == prefix || strings.HasPrefix(path, prefix+"/") {
			return strings.TrimPrefix(path, prefix)
		}
	}
	return path
}
func (p *UpstreamProxy) relayHandler(target *url.URL, token, localPath string) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(target)
	basePath := strings.TrimRight(target.Path, "/")
	proxy.Director = func(r *http.Request) {
		escaped := r.URL.EscapedPath()
		r.URL.Scheme = target.Scheme
		r.URL.Host = target.Host
		r.URL.Path = basePath + suffixPath(r.URL.Path, token, localPath)
		r.URL.RawPath = strings.TrimRight(target.EscapedPath(), "/") + suffixPath(escaped, token, localPath)
		r.Host = target.Host
		// Local observation must not append a proxy-generated loopback address.
		// nil is ReverseProxy's documented opt-out (same as the prior repair).
		r.Header["X-Forwarded-For"] = nil
		r.Header.Del("Sec-WebSocket-Extensions")
		r.Header.Del("X-Codexio-Route")
		r.Header.Set("Accept-Encoding", "identity")
	}
	p.transport = &http.Transport{Proxy: systemAccountProxy, ForceAttemptHTTP2: true, DisableCompression: true, TLSHandshakeTimeout: 15 * time.Second, ResponseHeaderTimeout: 600 * time.Second, IdleConnTimeout: 90 * time.Second, MaxIdleConns: 16}
	proxy.Transport = p.transport
	proxy.FlushInterval = -1
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
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		escaped := r.URL.EscapedPath()
		parts := strings.SplitN(strings.TrimPrefix(escaped, "/"), "/", 2)
		if len(parts) < 2 || subtle.ConstantTimeCompare([]byte(parts[0]), []byte(token)) != 1 || !upstreamRouteMatches(escaped, token, localPath) {
			http.NotFound(w, r)
			return
		}
		proxy.ServeHTTP(upstreamResponseWriter{w}, r)
	})

}

// Go's Upgrade copier reads the Hijack connection directly, bypassing bytes
// already buffered by net/http with the request headers. Read through that
// original reader so an early client frame is not lost.
type upstreamBufferedConn struct {
	net.Conn
	reader *bufio.Reader
}

func (c *upstreamBufferedConn) Read(data []byte) (int, error) { return c.reader.Read(data) }
func (c *upstreamBufferedConn) CloseWrite() error {
	if connection, ok := c.Conn.(interface{ CloseWrite() error }); ok {
		return connection.CloseWrite()
	}
	return c.Conn.Close()
}

type upstreamResponseWriter struct{ http.ResponseWriter }

func (w upstreamResponseWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w upstreamResponseWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	connection, buffered, err := http.NewResponseController(w.ResponseWriter).Hijack()
	if err != nil {
		return nil, nil, err
	}
	return &upstreamBufferedConn{Conn: connection, reader: buffered.Reader}, buffered, nil
}

func (p *UpstreamProxy) Stop() error {
	if p.mock {
		return nil
	}
	clientRunning, _ := upstreamDesktopClientRunning()
	return p.stop(clientRunning)
}
func (p *UpstreamProxy) stop(clientRunning bool) error {
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
	_, journalError := os.Stat(p.journalPath())
	hadRoute := journalError == nil || p.server != nil
	if e := p.restoreJournal(); e != nil {
		p.lastError = e.Error()
		return e
	}
	p.closeRelayLocked()
	p.status = "disabled"
	p.lastError = ""
	p.desiredEnabled = false
	p.routeChanged = hadRoute
	p.clientRunning = clientRunning
	p.alreadyTargeted = false
	p.restartRequired = hadRoute && clientRunning
	if p.changed != nil {
		go p.changed()
	}
	return nil
}
func (p *UpstreamProxy) closeRelayLocked() {
	if p.server != nil {
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
	if p.transport != nil {
		p.transport.CloseIdleConnections()
		p.transport = nil
	}
	p.listener = nil
	p.journal = nil
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
	if ValueString(j["route_version"]) != "go-rpc-1" && ValueString(j["route_version"]) != "go-1" && ValueInt(j["route_version"]) != 3 {
		return p.restoreLegacyJournal(j)
	}
	if ValueString(j["original_digest"]) != upstreamDigest([]any{ValueBool(j["original_present"]), j["original_endpoint"]}) || ValueString(j["applied_digest"]) != upstreamDigest([]any{true, j["applied_endpoint"]}) {
		return errors.New("上游恢复记录校验失败，已保留原配置")
	}
	if ValueString(j["route_version"]) == "go-rpc-1" {
		client, err := p.openConfigClient()
		if err != nil {
			return err
		}
		defer client.close()
		if err = client.restore(j); err != nil {
			return err
		}
		return os.Remove(p.journalPath())
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
		return errors.New("路由已被其他程序修改，保留当前配置和转发")
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
func (p *UpstreamProxy) RestartClient() error {
	if p.mock {
		return errors.New("模拟模式不会重启真实客户端")
	}
	if e := upstreamRestartDesktop(); e != nil {
		return e
	}
	p.mu.Lock()
	p.restartRequired = false
	p.routeChanged = false
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
	nested := len(response) > 0
	if !nested {
		response = r
	}
	// Outer metadata cannot fill holes in a nested response. Only actual
	// strings from one response object are evidence of an id and model.
	id, _ := response["id"].(string)
	model, _ := response["model"].(string)
	if id == "" || model == "" || len(id) > 256 || len(model) > 256 {
		return
	}
	event := ValueString(r["type"])
	if event == "" || event == "response" {
		if ValueString(r["object"]) != "response" && !nested && event != "response" {
			return
		}
		status, _ := response["status"].(string)
		switch status {
		case "in_progress", "queued":
			event = "response.created"
		case "failed":
			event = "response.failed"
		case "incomplete":
			event = "response.incomplete"
		default:
			event = "response.completed"
		}
	}
	if !systemContains([]string{"response.created", "response.completed", "response.incomplete", "response.failed"}, event) {
		return
	}
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
			e := db.QueryRowContext(ctx, `SELECT model,rank FROM observations WHERE response_id=?`, r["id"]).Scan(&oldModel, &oldRank)
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
	emit         func([]byte)
	sse          bool
	determined   bool
	buffer       []byte
	event        []byte
	dropping     bool
	lineDropping bool
	disabled     bool
}

func (o *upstreamLineObserver) line() {
	if o.lineDropping {
		o.dropping = true
		o.lineDropping = false
		o.buffer = nil
		return
	}
	line := bytes.TrimSuffix(o.buffer, []byte("\r"))
	o.buffer = nil
	if len(line) == 0 {
		if !o.dropping && len(o.event) > 0 {
			o.emit(o.event)
		}
		o.event = nil
		o.dropping = false
	} else if bytes.HasPrefix(line, []byte("data:")) && !o.dropping {
		data := bytes.TrimPrefix(line[5:], []byte(" "))
		if len(o.event)+len(data)+1 > 2<<20 {
			o.event = nil
			o.dropping = true
			return
		}
		if len(o.event) > 0 {
			o.event = append(o.event, '\n')
		}
		o.event = append(o.event, data...)
	}
}
func (o *upstreamLineObserver) feed(b []byte, eof bool) {
	if o.disabled {
		return
	}
	if !o.determined {
		if len(o.buffer)+len(b) > 2<<20 {
			o.buffer = nil
			o.disabled = true
			return
		}
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
		if len(o.buffer)+len(b) > 2<<20 {
			o.buffer = nil
			o.disabled = true
			return
		}
		o.buffer = append(o.buffer, b...)
		if eof {
			o.emit(o.buffer)
			o.buffer = nil
		}
		return
	}
	for _, c := range b {
		if c == '\n' {
			o.line()
		} else if !o.lineDropping {
			if len(o.buffer) >= 2<<20 {
				o.lineDropping = true
				o.buffer = nil
			} else {
				o.buffer = append(o.buffer, c)
			}
		}
	}
	if eof {
		if len(o.buffer) > 0 || o.lineDropping {
			o.line()
		}
		o.line()
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
	compressed bool
	disabled   bool
}

func (o *upstreamFrameObserver) feed(b []byte) {
	if o.disabled {
		return
	}
	if len(o.buffer)+len(b) > 4<<20 {
		o.disabled = true
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
			o.disabled = true
			o.buffer = nil
			o.message = nil
			return
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
		if opcode <= 2 && !o.compressed {
			if len(o.message)+len(payload) > 2<<20 {
				o.disabled = true
				o.buffer = nil
				o.message = nil
				return
			}
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
