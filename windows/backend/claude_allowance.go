package backend

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/subtle"
	_ "embed"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf16"
)

//go:embed assets/claude_bridge.json
var claudeBridgeAssets []byte

type ClaudeAllowance struct {
	startedAt                          float64
	mu                                 sync.Mutex
	directory, root, executable, nonce string
	cached                             Row
	server                             *http.Server
	timer                              *time.Timer
	closed                             bool
	changed                            func()
	sample                             func(Row)
}

func NewClaudeAllowance(directory, executable string, changed func(), sample func(Row)) *ClaudeAllowance {
	return &ClaudeAllowance{directory: filepath.Join(directory, "claude-bridge"), root: claudeRoot(), executable: executable, cached: Row{}, changed: changed, sample: sample, startedAt: float64(time.Now().UnixNano()) / 1e9}
}
func (q *ClaudeAllowance) Start() {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed || q.server != nil {
		return
	}
	listener, e := net.Listen("tcp4", "127.0.0.1:0")
	if e != nil {
		return
	}
	key := make([]byte, 32)
	if _, e = rand.Read(key); e != nil {
		listener.Close()
		return
	}
	q.nonce = hex.EncodeToString(key)
	q.cached, _ = ReadJSON(filepath.Join(q.directory, "snapshot.json"))
	if q.cached == nil {
		q.cached = Row{}
	}
	address := "http://" + listener.Addr().String() + "/quota"
	server := &http.Server{Handler: http.HandlerFunc(q.serve), ReadHeaderTimeout: 2 * time.Second, ReadTimeout: 3 * time.Second, WriteTimeout: 3 * time.Second, MaxHeaderBytes: 4096}
	q.server = server
	if e = WriteJSON(filepath.Join(q.directory, "runtime.json"), Row{"url": address, "nonce": q.nonce}); e != nil {
		listener.Close()
		q.server = nil
		return
	}
	_ = q.install()
	q.scheduleLocked()
	go server.Serve(listener)
}
func (q *ClaudeAllowance) Close() {
	q.mu.Lock()
	q.closed = true
	server := q.server
	q.server = nil
	if q.timer != nil {
		q.timer.Stop()
	}
	q.mu.Unlock()
	if server != nil {
		_ = server.Close()
	}
	runtime := filepath.Join(q.directory, "runtime.json")
	value, _ := ReadJSON(runtime)
	if dataString(value, "nonce") == q.nonce {
		_ = os.Remove(runtime)
	}
}
func (q *ClaudeAllowance) identity(surface, session string) Row {
	if surface != "cli" {
		return Row{"identityKey": HashString(q.root + "|desktop"), "verified": false}
	}
	file := filepath.Join(q.root, ".claude.json")
	if filepath.Base(q.root) == ".claude" {
		file = filepath.Join(filepath.Dir(q.root), ".claude.json")
	}
	data, _ := systemReadJSON(file, 1<<20)
	account := ValueRow(data["oauthAccount"])
	id := dataString(account, "accountUuid")
	key := "local"
	if id != "" {
		key = id + "|" + dataString(account, "organizationUuid")
	}
	return Row{"identityKey": HashString(q.root + "|cli|" + key), "verified": id != "", "email": account["emailAddress"], "planType": firstString(account["planDisplayName"], account["subscriptionType"])}
}
func (q *ClaudeAllowance) serve(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" || r.URL.Path != "/quota" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+q.nonce)) != 1 {
		http.Error(w, "", 403)
		return
	}
	body, e := io.ReadAll(io.LimitReader(r.Body, 32769))
	if e != nil || len(body) > 32768 {
		http.Error(w, "", 400)
		return
	}
	payload, e := DecodeRow(body)
	at, ok := ValueFloat(payload["captured_at"])
	source := dataString(payload, "source")
	session := dataString(payload, "session_id")
	if e != nil || ValueInt(payload["schema"]) != 1 || !strings.EqualFold(filepath.Clean(dataString(payload, "config_root")), filepath.Clean(q.root)) || !ok || time.Since(time.UnixMilli(int64(at*1000))) > time.Minute || time.Until(time.UnixMilli(int64(at*1000))) > time.Minute || (source != "mods" && source != "statusline") || len(session) > 256 {
		http.Error(w, "", 400)
		return
	}
	surface := "cli"
	if dataString(payload, "surface") == "desktop" {
		surface = "desktop"
	}
	windows := []Row{}
	for _, window := range ValueRows(payload["windows"]) {
		kind := dataString(window, "kind")
		used, valid := ValueFloat(window["percentUsed"])
		if (kind != "five_hour" && kind != "seven_day") || !valid || used < 0 || used > 100 {
			continue
		}
		reset, has := ParseStamp(window["resetsAt"])
		if has && float64(reset.Unix()) <= at {
			continue
		}
		row := Row{"kind": kind, "used": used, "reset": nil}
		if has {
			row["reset"] = reset.Unix()
		}
		windows = append(windows, row)
		if len(windows) == 2 {
			break
		}
	}
	contexts := []Row{}
	for _, point := range ValueRows(payload["contexts"]) {
		window := ValueInt(point["window"])
		stamp, valid := ValueFloat(point["observed_at"])
		session := dataString(point, "session_id")
		uuid, prompt := dataString(point, "message_uuid"), dataString(point, "prompt_id")
		if window >= 0 && window <= 2147483647 && len(claudePointFields(point)) > 0 && len(dataString(point, "model")) <= 256 && len(dataString(point, "reasoning_effort")) <= 32 && valid && stamp >= q.startedAt && stamp <= at+5 && session != "" && len(session) <= 256 && ((uuid != "" && len(uuid) <= 256) || (prompt != "" && len(prompt) <= 256)) {
			contexts = append(contexts, point)
		}
		if len(contexts) >= 32 {
			break
		}
	}
	boundary := true
	if value, exists := payload["scheduled"]; exists {
		boundary = ValueBool(value)
	}
	value := Row{"boundary_sample": boundary, "contexts": contexts, "source": source, "surface": surface, "captured_at": at, "session_id": session, "windows": windows, "account": q.identity(surface, session)}
	q.mu.Lock()
	if q.closed {
		q.mu.Unlock()
		w.WriteHeader(503)
		return
	}
	oldAt, _ := ValueFloat(q.cached["captured_at"])
	if (oldAt > at && oldAt <= float64(time.Now().UnixMilli())/1000) || (source == "statusline" && dataString(q.cached, "source") == "mods" && dataString(ValueRow(q.cached["account"]), "identityKey") == dataString(ValueRow(value["account"]), "identityKey") && at-oldAt < 120) {
		q.mu.Unlock()
		w.WriteHeader(200)
		return
	}
	old := q.snapshotLocked()
	q.cached = value
	next := q.snapshotLocked()
	q.scheduleLocked()
	_ = WriteJSON(filepath.Join(q.directory, "snapshot.json"), value)
	q.mu.Unlock()
	if q.sample != nil {
		q.sample(next)
	}
	delete(old, "boundary_sample")
	delete(old, "contexts")
	delete(old, "updated_at")
	comparison := CloneRow(next)
	delete(comparison, "boundary_sample")
	delete(comparison, "contexts")
	delete(comparison, "updated_at")
	if dataJSON(old) != dataJSON(comparison) && q.changed != nil {
		q.changed()
	}
	w.WriteHeader(200)
}
func (q *ClaudeAllowance) scheduleLocked() {
	if q.timer != nil {
		q.timer.Stop()
	}
	at, _ := ValueFloat(q.cached["captured_at"])
	deadline := time.UnixMilli(int64(at * 1000)).Add(15 * time.Minute)
	now := time.Now()
	if !deadline.After(now) {
		return
	}
	for _, w := range ValueRows(q.cached["windows"]) {
		if reset, ok := ParseStamp(w["reset"]); ok && reset.After(now) && reset.Before(deadline) {
			deadline = reset
		}
	}
	q.timer = time.AfterFunc(time.Until(deadline)+20*time.Millisecond, func() {
		q.mu.Lock()
		if q.closed {
			q.mu.Unlock()
			return
		}
		q.scheduleLocked()
		q.mu.Unlock()
		if q.changed != nil {
			q.changed()
		}
	})
}
func (q *ClaudeAllowance) snapshotLocked() Row {
	at, _ := ValueFloat(q.cached["captured_at"])
	when := time.UnixMilli(int64(at * 1000))
	age := time.Since(when)
	fresh := at > 0 && age >= 0 && age < 15*time.Minute
	account := ValueRow(q.cached["account"])
	result := Row{"boundary_sample": q.cached["boundary_sample"], "contexts": q.cached["contexts"], "client_kind": "claude_code", "status": "unavailable", "applicable": true, "primary": nil, "secondary": nil, "account": Row{"email": account["email"]}, "account_key": account["identityKey"], "account_verified": account["verified"], "plan_type": account["planType"], "limit_id": "claude", "updated_at": nil, "error": ""}
	if at > 0 {
		result["updated_at"] = UTCStamp(when)
	}
	for _, window := range ValueRows(q.cached["windows"]) {
		key := "primary"
		minutes := 300
		if dataString(window, "kind") == "seven_day" {
			key = "secondary"
			minutes = 10080
		}
		reset, known := ParseStamp(window["reset"])
		if !fresh || known && !reset.After(time.Now()) {
			continue
		}
		used, ok := ValueFloat(window["used"])
		if !ok {
			continue
		}
		result[key] = Row{"used_percent": used, "remaining_percent": 100 - used, "resets_at": window["reset"], "window_duration_mins": minutes}
		result["status"] = "ok"
	}
	return result
}
func (q *ClaudeAllowance) Snapshot() Row { q.mu.Lock(); defer q.mu.Unlock(); return q.snapshotLocked() }
func (q *ClaudeAllowance) install() error {
	if _, e := os.Stat(q.root); e != nil {
		return nil
	}
	assets, _ := DecodeRow(claudeBridgeAssets)
	plugin := filepath.Join(q.directory, "plugin")
	runtime := filepath.Join(q.directory, "runtime.json")
	config := dataJSON(Row{"runtime": runtime, "root": q.root})
	for relative, value := range assets {
		if strings.Contains(relative, "..") || filepath.IsAbs(relative) {
			continue
		}
		path := filepath.Join(plugin, relative)
		text := strings.ReplaceAll(ValueString(value), "__CODEXIO_CONFIG__", config)
		old, _ := os.ReadFile(path)
		if string(old) != text {
			if e := writePrivateFile(path, []byte(text)); e != nil {
				return e
			}
		}
	}
	path := filepath.Join(q.root, "settings.json")
	original, e := os.ReadFile(path)
	if e != nil && !os.IsNotExist(e) {
		return e
	}
	settings := Row{}
	if len(original) > 0 {
		settings, e = DecodeRow(original)
		if e != nil {
			return e
		}
	}
	prior, _ := ReadJSON(filepath.Join(q.directory, "statusline.json"))
	env := CloneRow(ValueRow(settings["env"]))
	dirs := filepath.SplitList(dataString(env, "CLAUDE_CODE_PLUGIN_DIRS"))
	if !systemContains(dirs, plugin) {
		dirs = append(dirs, plugin)
	}
	env["CLAUDE_CODE_PLUGIN_DIRS"] = strings.Join(dirs, string(os.PathListSeparator))
	settings["env"] = env
	cli, _ := exec.LookPath("claude")
	if cli == "" {
		home, _ := os.UserHomeDir()
		candidate := filepath.Join(home, ".local", "bin", "claude.exe")
		if _, e = os.Stat(candidate); e == nil {
			cli = candidate
		}
	}
	modern := false
	if resolved, e := filepath.EvalSymlinks(cli); e == nil {
		for p := resolved; p != filepath.Dir(p); p = filepath.Dir(p) {
			name := strings.TrimSuffix(filepath.Base(p), ".exe")
			if version, e := systemVersion(name); e == nil {
				minimum, _ := systemVersion("2.1.287")
				modern = systemCompareVersion(version, minimum) >= 0
				break
			}
		}
	}
	if cli != "" && !modern {
		status := CloneRow(ValueRow(settings["statusLine"]))
		if dataString(status, "command") == dataString(prior, "managed") {
			status = CloneRow(ValueRow(prior["original"]))
		}
		shell, args := claudeStatusShell(dataString(status, "command"))
		fallback := ""
		if dataString(status, "command") != "" {
			quoted := []string{}
			for _, arg := range args {
				quoted = append(quoted, psQuote(arg))
			}
			fallback = "$body | & " + psQuote(shell) + " " + strings.Join(quoted, " ") + "; exit $LASTEXITCODE"
		}
		script := "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); [Console]::InputEncoding = [Text.UTF8Encoding]::new($false); $body = [Console]::In.ReadToEnd(); if (Test-Path -LiteralPath " + psQuote(runtime) + ") { $body | & " + psQuote(q.executable) + " '--claude-statusline' " + psQuote(q.directory) + "; exit $LASTEXITCODE }; " + fallback
		words := utf16.Encode([]rune(script))
		encoded := make([]byte, len(words)*2)
		for i, word := range words {
			binary.LittleEndian.PutUint16(encoded[i*2:], word)
		}
		command := "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + base64.StdEncoding.EncodeToString(encoded)
		if e = WriteJSON(filepath.Join(q.directory, "statusline.json"), Row{"managed": command, "original": status, "root": q.root}); e != nil {
			return e
		}
		status["type"] = "command"
		status["command"] = command
		settings["statusLine"] = status
	} else if dataString(prior, "managed") != "" && dataString(ValueRow(settings["statusLine"]), "command") == dataString(prior, "managed") {
		status := ValueRow(prior["original"])
		if len(status) == 0 {
			delete(settings, "statusLine")
		} else {
			settings["statusLine"] = status
		}
	}
	old, _ := DecodeRow(original)
	if dataJSON(settings) != dataJSON(old) {
		if len(original) > 0 {
			_ = writePrivateFile(filepath.Join(q.directory, "settings-backup-"+strconvTime()+".json"), original)
		}
		return WriteJSON(path, settings)
	}
	return nil
}
func psQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "''") + "'" }
func strconvTime() string         { return time.Now().UTC().Format("20060102T150405.000000000") }
func claudeStatusShell(command string) (string, []string) {
	for _, path := range []string{filepath.Join(os.Getenv("ProgramFiles"), "Git", "bin", "bash.exe"), filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "Git", "bin", "bash.exe")} {
		if _, e := os.Stat(path); e == nil {
			return path, []string{"--noprofile", "--norc", "-c", command}
		}
	}
	return "powershell.exe", []string{"-NoProfile", "-NonInteractive", "-Command", command}
}
func RunClaudeStatusline(args []string) (bool, int) {
	if len(args) == 0 || args[0] != "--claude-statusline" {
		return false, 0
	}
	if len(args) != 2 || filepath.Base(args[1]) != "claude-bridge" {
		return true, 0
	}
	input, _ := io.ReadAll(io.LimitReader(os.Stdin, 1<<20))
	raw, _ := DecodeRow(input)
	directory := args[1]
	prior, _ := ReadJSON(filepath.Join(directory, "statusline.json"))
	runtime, _ := ReadJSON(filepath.Join(directory, "runtime.json"))
	u, e := url.Parse(dataString(runtime, "url"))
	if e == nil && u.Scheme == "http" && u.Hostname() == "127.0.0.1" && u.Path == "/quota" && dataString(runtime, "nonce") != "" {
		windows := []Row{}
		limits := ValueRow(raw["rate_limits"])
		for _, key := range []string{"five_hour", "seven_day"} {
			window := ValueRow(limits[key])
			if used, ok := ValueFloat(window["used_percentage"]); ok {
				row := Row{"kind": key, "percentUsed": used}
				if reset, ok := ParseStamp(window["resets_at"]); ok {
					row["resetsAt"] = UTCStamp(reset)
				}
				windows = append(windows, row)
			}
		}
		contexts := []Row{}
		if dataString(raw, "prompt_id") != "" {
			point := Row{"session_id": raw["session_id"], "prompt_id": raw["prompt_id"], "observed_at": float64(time.Now().UnixNano()) / 1e9}
			if window, ok := knownCount(ValueRow(raw["context_window"])["context_window_size"]); ok && window > 0 {
				point["window"] = window
			}
			if model := dataString(ValueRow(raw["model"]), "id"); model != "" {
				point["model"] = model
			}
			if effort := dataString(ValueRow(raw["effort"]), "level"); effort != "" {
				point["reasoning_effort"] = effort
			}
			contexts = append(contexts, point)
		}
		body := Row{"contexts": contexts, "schema": 1, "source": "statusline", "surface": "cli", "session_id": raw["session_id"], "config_root": prior["root"], "captured_at": float64(time.Now().UnixMilli()) / 1000, "windows": windows}
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		request, _ := http.NewRequestWithContext(ctx, "POST", u.String(), strings.NewReader(dataJSON(body)))
		request.Header.Set("Authorization", "Bearer "+dataString(runtime, "nonce"))
		request.Header.Set("Content-Type", "application/json")
		if response, e := (&http.Client{Timeout: time.Second, Transport: &http.Transport{Proxy: nil}}).Do(request); e == nil {
			response.Body.Close()
		}
	}
	original := dataString(ValueRow(prior["original"]), "command")
	if original == "" {
		return true, 0
	}
	shell, arguments := claudeStatusShell(original)
	command := exec.Command(shell, arguments...)
	systemConfigureCommand(command)
	command.Stdin = bytes.NewReader(input)
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if e := command.Run(); e != nil {
		if exit, ok := e.(*exec.ExitError); ok {
			return true, exit.ExitCode()
		}
	}
	return true, 0
}
