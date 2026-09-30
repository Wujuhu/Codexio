package backend

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

type systemRPCResult struct {
	value Row
	err   error
}
type systemRPC struct {
	cmd     *exec.Cmd
	input   io.WriteCloser
	mu      sync.Mutex
	writeMu sync.Mutex
	pending map[int64]chan systemRPCResult
	next    int64
	done    chan struct{}
	release func()
	notify  func(string, Row)
	once    sync.Once
}

func systemNewRPC(ctx context.Context, path, version, root string, notify func(string, Row)) (*systemRPC, error) {
	c, e := systemCLICommand(nil, path, "app-server", "--listen", "stdio://")
	if e != nil {
		return nil, e
	}
	c.Dir, _ = os.UserHomeDir()
	c.Env = systemChildEnvironment(root)
	systemConfigureCommand(c)
	systemPrepareOwnedCommand(c)
	in, e := c.StdinPipe()
	if e != nil {
		return nil, e
	}
	out, e := c.StdoutPipe()
	if e != nil {
		in.Close()
		return nil, e
	}
	c.Stderr = &systemDiscardWriter{}
	if e = c.Start(); e != nil {
		in.Close()
		return nil, errors.New("无法启动 Codex 组件")
	}
	release, e := systemOwnChild(c)
	if e != nil {
		_ = c.Process.Kill()
		_ = c.Wait()
		in.Close()
		return nil, errors.New("无法隔离 Codex 子进程")
	}
	r := &systemRPC{cmd: c, input: in, pending: map[int64]chan systemRPCResult{}, done: make(chan struct{}), release: release, notify: notify}
	go r.read(out)
	go func() { _ = c.Wait(); r.fail() }()
	init, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	_, e = r.request(init, "initialize", Row{"clientInfo": Row{"name": "codexio", "title": "Codexio", "version": version}, "capabilities": Row{"optOutNotificationMethods": []string{"thread/started", "thread/archived", "item/agentMessage/delta", "item/reasoning/delta", "item/commandExecution/outputDelta", "turn/started"}}})
	if e == nil {
		e = r.write(Row{"method": "initialized", "params": Row{}})
	}
	if e != nil {
		r.close()
		return nil, e
	}
	return r, nil
}
func (r *systemRPC) request(ctx context.Context, method string, params Row) (Row, error) {
	r.mu.Lock()
	select {
	case <-r.done:
		r.mu.Unlock()
		return nil, errors.New("Codex app-server 已退出")
	default:
	}
	if len(r.pending) >= 32 {
		r.mu.Unlock()
		return nil, errors.New("Codex 请求队列已满")
	}
	r.next++
	id := r.next
	reply := make(chan systemRPCResult, 1)
	r.pending[id] = reply
	r.mu.Unlock()
	defer func() { r.mu.Lock(); delete(r.pending, id); r.mu.Unlock() }()
	if e := r.write(Row{"method": method, "id": id, "params": params}); e != nil {
		return nil, e
	}
	select {
	case v := <-reply:
		return v.value, v.err
	case <-ctx.Done():
		return nil, errors.New("Codex 请求已取消或超时")
	case <-r.done:
		return nil, errors.New("Codex app-server 已退出")
	}
}
func (r *systemRPC) write(v Row) error {
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	if len(b) > 1<<20 {
		return errors.New("Codex 请求过大")
	}
	r.writeMu.Lock()
	defer r.writeMu.Unlock()
	_, e = r.input.Write(append(b, '\n'))
	if e != nil {
		return errors.New("无法写入 Codex app-server")
	}
	return nil
}
func (r *systemRPC) read(out io.Reader) {
	s := bufio.NewScanner(out)
	s.Buffer(make([]byte, 65536), 4<<20)
	for s.Scan() {
		v, e := DecodeRow(s.Bytes())
		if e != nil {
			continue
		}
		method := ValueString(v["method"])
		id, hasID := v["id"]
		if method != "" {
			if hasID {
				_ = r.write(Row{"id": id, "error": Row{"code": -32601, "message": "Method not supported"}})
			} else if r.notify != nil {
				r.notify(method, ValueRow(v["params"]))
			}
			continue
		}
		if !hasID {
			continue
		}
		r.mu.Lock()
		ch := r.pending[ValueInt(id)]
		r.mu.Unlock()
		if ch == nil {
			continue
		}
		result := systemRPCResult{value: ValueRow(v["result"])}
		if errorRow, ok := v["error"]; ok {
			code := ValueInt(ValueRow(errorRow)["code"])
			result.err = fmt.Errorf("Codex 请求失败（代码 %d）", code)
		}
		select {
		case ch <- result:
		default:
		}
	}
	r.fail()
}
func (r *systemRPC) fail() { r.once.Do(func() { close(r.done) }) }
func (r *systemRPC) close() {
	r.fail()
	_ = r.input.Close()
	r.writeMu.Lock()
	r.writeMu.Unlock()
	if r.release != nil {
		r.release()
		r.release = nil
	}
}
func (r *systemRPC) running() bool {
	select {
	case <-r.done:
		return false
	default:
		return true
	}
}

type systemDiscardWriter struct{}

func (*systemDiscardWriter) Write(p []byte) (int, error) { return len(p), nil }

type systemBoundedBuffer struct {
	bytes.Buffer
	limit int
}

func (b *systemBoundedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if b.Len() < b.limit {
		_, _ = b.Buffer.Write(p[:min(n, b.limit-b.Len())])
	}
	return n, nil
}
func systemChildEnvironment(root string) []string {
	env := os.Environ()
	if root != "" {
		env = systemSetEnv(env, "CODEX_HOME", root)
	}
	env = systemSetEnv(env, "RUST_LOG", "error")
	for _, key := range []string{"HTTPS_PROXY", "HTTP_PROXY"} {
		raw := os.Getenv(key)
		u, e := url.Parse(raw)
		if e != nil || raw == "" {
			continue
		}
		host := u.Hostname()
		if host != "localhost" && host != "127.0.0.1" && host != "::1" {
			continue
		}
		port := u.Port()
		if port == "" {
			port = "80"
			if u.Scheme == "https" {
				port = "443"
			}
		}
		conn, e := net.DialTimeout("tcp", net.JoinHostPort(host, port), 400*time.Millisecond)
		if e == nil {
			conn.Close()
			break
		}
		for _, k := range []string{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"} {
			env = systemRemoveEnv(env, k)
		}
		env = systemSetEnv(env, "NO_PROXY", "*")
		break
	}
	return env
}
func systemRemoveEnv(env []string, key string) []string {
	r := []string{}
	for _, v := range env {
		if !strings.EqualFold(strings.SplitN(v, "=", 2)[0], key) {
			r = append(r, v)
		}
	}
	return r
}
func systemSetEnv(env []string, key, value string) []string {
	return append(systemRemoveEnv(env, key), key+"="+value)
}

type systemProbe struct {
	at    time.Time
	valid bool
	size  int64
	mtime int64
}

var systemProbeMu sync.Mutex
var systemProbes = map[string]systemProbe{}

func systemProbeCLI(ctx context.Context, path string) bool {
	s, e := os.Stat(path)
	if e != nil || s.IsDir() || s.Size() == 0 {
		return false
	}
	for _, p := range []string{filepath.Join(filepath.Dir(path), "resources", "app.asar"), filepath.Join(filepath.Dir(path), "chrome_100_percent.pak")} {
		if _, e := os.Stat(p); e == nil {
			return false
		}
	}
	key := strings.ToLower(path)
	systemProbeMu.Lock()
	cached := systemProbes[key]
	systemProbeMu.Unlock()
	ttl := 5 * time.Second
	if cached.valid {
		ttl = time.Minute
	}
	if cached.size == s.Size() && cached.mtime == s.ModTime().UnixNano() && time.Since(cached.at) < ttl {
		return cached.valid
	}
	run := func(args ...string) ([]byte, bool) {
		pc, cancel := context.WithTimeout(ctx, 4*time.Second)
		defer cancel()
		c, e := systemCLICommand(pc, path, args...)
		if e != nil {
			return nil, false
		}
		c.Dir, _ = os.UserHomeDir()
		c.Env = systemChildEnvironment("")
		systemConfigureCommand(c)
		systemPrepareOwnedCommand(c)
		out := &systemBoundedBuffer{limit: 65536}
		c.Stdout = out
		c.Stderr = out
		if c.Start() != nil {
			return nil, false
		}
		release, e := systemOwnChild(c)
		if e != nil {
			c.Process.Kill()
			_ = c.Wait()
			return nil, false
		}
		defer release()
		stop := context.AfterFunc(pc, release)
		defer stop()
		e = c.Wait()
		return out.Bytes(), e == nil
	}
	b, valid := run("--version")
	valid = valid && regexp.MustCompile(`(?im)^\s*codex-cli\s+\S+`).Match(b)
	if valid {
		b, valid = run("app-server", "--help")
		valid = valid && bytes.Contains(b, []byte("app-server")) && bytes.Contains(b, []byte("--listen"))
	}
	systemProbeMu.Lock()
	if len(systemProbes) >= 128 {
		clear(systemProbes)
	}
	systemProbes[key] = systemProbe{time.Now(), valid, s.Size(), s.ModTime().UnixNano()}
	systemProbeMu.Unlock()
	return valid
}
func systemScanCLI(root string) []string {
	root = systemExpandPath(root)
	if root == "" || filepath.Dir(root) == root {
		return nil
	}
	type dir struct {
		path  string
		depth int
	}
	queue := []dir{{root, 0}}
	found := []string{}
	for visited := 0; len(queue) > 0 && visited < 256; visited++ {
		d := queue[0]
		queue = queue[1:]
		entries, e := os.ReadDir(d.path)
		if e != nil {
			continue
		}
		for _, entry := range entries {
			if entry.Type()&os.ModeSymlink != 0 {
				continue
			}
			path := filepath.Join(d.path, entry.Name())
			if entry.IsDir() {
				if d.depth < 6 && !systemContains([]string{"cache", "code cache", "gpucache", "logs", "crashpad", ".git", "sessions"}, strings.ToLower(entry.Name())) {
					queue = append(queue, dir{path, d.depth + 1})
				}
				continue
			}
			if strings.EqualFold(entry.Name(), "codex.exe") || entry.Name() == "codex" {
				found = append(found, path)
			}
		}
	}
	sort.SliceStable(found, func(i, j int) bool {
		a, ea := os.Stat(found[i])
		b, eb := os.Stat(found[j])
		return ea == nil && (eb != nil || a.ModTime().After(b.ModTime()))
	})
	return found
}
func systemDiscoverCLI(ctx context.Context, explicit string, excluded map[string]bool) (string, error) {
	seen := map[string]bool{}
	wrappers := []string{}
	try := func(p string) string {
		p = systemExpandPath(p)
		a, e := filepath.Abs(p)
		if e != nil {
			return ""
		}
		p = a
		if seen[strings.ToLower(p)] || excluded[strings.ToLower(p)] {
			return ""
		}
		seen[strings.ToLower(p)] = true
		if strings.EqualFold(filepath.Ext(p), ".cmd") || strings.EqualFold(filepath.Ext(p), ".bat") {
			wrappers = append(wrappers, p)
			return ""
		}
		if systemProbeCLI(ctx, p) {
			return p
		}
		return ""
	}
	hint := func(p string) string {
		if p == "" {
			return ""
		}
		p = systemExpandPath(p)
		if s, e := os.Stat(p); e == nil && s.IsDir() {
			for _, v := range systemScanCLI(p) {
				if x := try(v); x != "" {
					return x
				}
			}
			return ""
		}
		if strings.EqualFold(filepath.Ext(p), ".cmd") || strings.EqualFold(filepath.Ext(p), ".bat") {
			if x := try(strings.TrimSuffix(p, filepath.Ext(p)) + ".exe"); x != "" {
				return x
			}
			for _, r := range []string{filepath.Dir(p), filepath.Join(os.Getenv("APPDATA"), "npm")} {
				for _, v := range systemScanCLI(filepath.Join(r, "node_modules", "@openai", "codex")) {
					if x := try(v); x != "" {
						return x
					}
				}
			}
		}
		if x := try(p); x != "" {
			return x
		}
		parent := filepath.Dir(p)
		for i := 0; i < 4; i++ {
			if strings.EqualFold(filepath.Base(parent), "resources") || strings.EqualFold(filepath.Base(parent), "bin") {
				for _, v := range systemScanCLI(parent) {
					if x := try(v); x != "" {
						return x
					}
				}
				break
			}
			parent = filepath.Dir(parent)
		}
		return ""
	}
	for _, p := range []string{explicit, os.Getenv("CODEX_CLI_PATH")} {
		if v := hint(p); v != "" {
			return v, nil
		}
	}
	for _, n := range []string{"codex.exe", "codex.cmd", "codex"} {
		if p, e := exec.LookPath(n); e == nil {
			if v := hint(p); v != "" {
				return v, nil
			}
		}
	}
	local := os.Getenv("LOCALAPPDATA")
	for _, r := range []string{filepath.Join(local, "OpenAI", "Codex"), filepath.Join(local, "Programs", "OpenAI", "Codex"), filepath.Join(local, "Programs", "Codex"), filepath.Join(local, "OpenAI", "ChatGPT"), filepath.Join(local, "Programs", "ChatGPT")} {
		if v := hint(r); v != "" {
			return v, nil
		}
	}
	for _, base := range []string{os.Getenv("ProgramFiles"), os.Getenv("ProgramFiles(x86)")} {
		if base == "" {
			continue
		}
		for _, n := range []string{`OpenAI/Codex`, `Codex`, `OpenAI/ChatGPT`, `ChatGPT`} {
			if v := hint(filepath.Join(base, n)); v != "" {
				return v, nil
			}
		}
	}
	roots, running := systemInstallRoots(ctx)
	for _, p := range append(running, roots...) {
		if v := hint(p); v != "" {
			return v, nil
		}
	}
	home, _ := os.UserHomeDir()
	for _, r := range []string{filepath.Join(home, ".local", "bin"), filepath.Join(os.Getenv("APPDATA"), "npm")} {
		for _, n := range []string{"codex.exe", "codex.cmd", "codex"} {
			if v := hint(filepath.Join(r, n)); v != "" {
				return v, nil
			}
		}
	}
	for _, p := range wrappers {
		if !excluded[strings.ToLower(p)] && systemProbeCLI(ctx, p) {
			return p, nil
		}
	}
	return "", errors.New("未找到可用的 Codex 组件，请安装或更新 Codex App 并登录")
}
