package backend

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const systemLatestManifest = "https://github.com/Wujuhu/Codexio/releases/latest/download/latest.json"
const systemMaxUpdateBytes int64 = 512 << 20

var systemVersionPattern = regexp.MustCompile(`^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`)
var systemDigestPattern = regexp.MustCompile(`^[0-9a-fA-F]{64}$`)
var systemJobPattern = regexp.MustCompile(`^[0-9a-f]{32}$`)

type systemRelease struct {
	version, url, digest, notes string
	size                        int64
}
type Updater struct {
	opts           SystemOptions
	mu             sync.Mutex
	state          Row
	release        *systemRelease
	checking       chan struct{}
	installing     bool
	cancel         context.CancelFunc
	job            string
	closed         bool
	startOnce      sync.Once
	lifetimeCancel context.CancelFunc
}

func NewUpdater(o SystemOptions) *Updater {
	return &Updater{opts: o, state: Row{"status": "idle", "version": o.Version, "notes": "", "progress": 0, "error": "", "url": ""}}
}
func (u *Updater) Snapshot() Row { u.mu.Lock(); defer u.mu.Unlock(); return CloneRow(u.state) }

// Automatic checks only announce availability. Download and installation always
// require Install, which the service exposes only behind the user's action.
func (u *Updater) Start(parent context.Context) {
	u.startOnce.Do(func() {
		if u.opts.Mock {
			return
		}
		ctx, cancel := context.WithCancel(parent)
		u.mu.Lock()
		if u.closed {
			u.mu.Unlock()
			cancel()
			return
		}
		u.lifetimeCancel = cancel
		u.mu.Unlock()
		skip := os.Getenv("CODEXIO_SKIP_UPDATE_ONCE") == "1"
		_ = os.Unsetenv("CODEXIO_SKIP_UPDATE_ONCE")
		go func() {
			initial := time.NewTimer(5 * time.Second)
			defer initial.Stop()
			checks := time.NewTicker(time.Hour)
			defer checks.Stop()
			cleanup := time.NewTicker(5 * time.Minute)
			defer cleanup.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-initial.C:
					if !skip && u.autoEnabled() {
						check, c := context.WithTimeout(ctx, 30*time.Second)
						_, _ = u.Check(check)
						c()
					}
				case <-checks.C:
					if u.autoEnabled() {
						check, c := context.WithTimeout(ctx, 30*time.Second)
						_, _ = u.Check(check)
						c()
					}
				case <-cleanup.C:
					systemCleanupUpdates(u.opts.Directory)
				}
			}
		}()
	})
}
func (u *Updater) autoEnabled() bool {
	if u.opts.Config == nil {
		return true
	}
	return ValueBool(u.opts.Config()["auto_update"])
}
func (u *Updater) Defer() error {
	u.mu.Lock()
	release := u.release
	u.mu.Unlock()
	if release == nil {
		return nil
	}
	path := filepath.Join(u.opts.Directory, "updates", "reminders.json")
	values, e := systemReadJSON(path, 1<<20)
	if e != nil && !os.IsNotExist(e) {
		return e
	}
	now := time.Now().Unix()
	for k, v := range values {
		if ValueInt(v) <= now {
			delete(values, k)
		}
	}
	values[release.version] = now + 86400
	if e = WriteJSON(path, values); e != nil {
		return e
	}
	u.mu.Lock()
	u.state["deferred_until"] = now + 86400
	u.mu.Unlock()
	return nil
}
func (u *Updater) publish(status string, progress int, message string) {
	u.mu.Lock()
	u.state["status"] = status
	u.state["progress"] = progress
	u.state["error"] = message
	u.mu.Unlock()
	if u.opts.Changed != nil {
		u.opts.Changed()
	}
}
func (u *Updater) Check(ctx context.Context) (Row, error) {
	if u.opts.Mock {
		u.publish("mock", 0, "")
		return u.Snapshot(), nil
	}
	u.mu.Lock()
	if u.closed {
		u.mu.Unlock()
		return nil, errors.New("更新服务已关闭")
	}
	if u.checking != nil {
		done := u.checking
		u.mu.Unlock()
		select {
		case <-done:
			return u.Snapshot(), nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	if u.installing {
		r := CloneRow(u.state)
		u.mu.Unlock()
		return r, nil
	}
	done := make(chan struct{})
	u.checking = done
	u.mu.Unlock()
	defer func() { u.mu.Lock(); u.checking = nil; close(done); u.mu.Unlock() }()
	u.publish("checking", 0, "")
	response, e := systemUpdateRequest(ctx, systemLatestManifest, u.opts.Version)
	if e != nil {
		u.publish("error", 0, e.Error())
		return u.Snapshot(), e
	}
	defer response.Body.Close()
	if response.StatusCode == 404 {
		u.mu.Lock()
		u.release = nil
		u.mu.Unlock()
		u.publish("current", 0, "")
		return u.Snapshot(), nil
	}
	if response.StatusCode != 200 {
		e = fmt.Errorf("GitHub 请求失败（HTTP %d）", response.StatusCode)
		u.publish("error", 0, e.Error())
		return u.Snapshot(), e
	}
	b, e := io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
	if e != nil || len(b) > 1<<20 {
		e = errors.New("更新清单无效或过大")
	} else {
		b = bytes.TrimPrefix(b, []byte{0xef, 0xbb, 0xbf})
		var manifest Row
		manifest, e = DecodeRow(b)
		if e == nil {
			var release *systemRelease
			release, e = systemManifestRelease(manifest, u.opts.Version)
			if e == nil {
				u.mu.Lock()
				u.release = release
				if release != nil {
					u.state["version"] = release.version
					u.state["notes"] = release.notes
					u.state["url"] = release.url
					reminders, _ := systemReadJSON(filepath.Join(u.opts.Directory, "updates", "reminders.json"), 1<<20)
					u.state["deferred_until"] = reminders[release.version]
				} else {
					u.state["version"] = u.opts.Version
					u.state["notes"] = ""
					u.state["url"] = ""
				}
				u.mu.Unlock()
				if release == nil {
					u.publish("current", 0, "")
				} else {
					u.publish("available", 0, "")
				}
				return u.Snapshot(), nil
			}
		}
	}
	if e == nil {
		e = errors.New("更新清单无效")
	}
	u.publish("error", 0, "GitHub 返回了无效的版本信息")
	return u.Snapshot(), errors.New("GitHub 返回了无效的版本信息")
}
func systemVersion(v string) ([3]int, error) {
	var numbers [3]int
	if len(v) > 18 {
		return numbers, errors.New("版本号无效")
	}
	matches := systemVersionPattern.FindStringSubmatch(v)
	if len(matches) != 4 {
		return numbers, errors.New("版本号必须为三段数字")
	}
	for i := range numbers {
		n, e := strconv.Atoi(matches[i+1])
		if e != nil || n > 65535 {
			return numbers, errors.New("版本号超出 Windows 支持范围")
		}
		numbers[i] = n
	}
	return numbers, nil
}
func systemCompareVersion(a, b [3]int) int {
	for i := range a {
		if a[i] < b[i] {
			return -1
		}
		if a[i] > b[i] {
			return 1
		}
	}
	return 0
}
func systemManifestRelease(m Row, current string) (*systemRelease, error) {
	n, e := systemVersion(ValueString(m["version"]))
	if e != nil {
		return nil, e
	}
	old, e := systemVersion(current)
	if e != nil {
		return nil, e
	}
	if systemCompareVersion(n, old) <= 0 {
		return nil, nil
	}
	version := fmt.Sprintf("%d.%d.%d", n[0], n[1], n[2])
	expected := "https://github.com/Wujuhu/Codexio/releases/download/v" + version + "/Codexio.exe"
	if ValueString(m["url"]) != expected {
		return nil, errors.New("更新文件不属于指定的 GitHub 发布版本")
	}
	digest := ValueString(m["sha256"])
	if !systemDigestPattern.MatchString(digest) {
		return nil, errors.New("更新附件缺少 SHA-256 校验值")
	}
	size, ok := ValueFloat(m["size"])
	if !ok || size != float64(int64(size)) || size < 2 || size > float64(systemMaxUpdateBytes) {
		return nil, errors.New("更新文件大小无效")
	}
	notes := ValueString(m["notes"])
	if len(notes) > 4000 {
		notes = notes[:4000]
	}
	return &systemRelease{version, expected, strings.ToLower(digest), notes, int64(size)}, nil
}
func systemUpdateRequest(ctx context.Context, raw, version string) (*http.Response, error) {
	p, e := url.Parse(raw)
	if e != nil || p.Scheme != "https" || p.Host != "github.com" || p.User != nil || p.RawQuery != "" || p.Fragment != "" || (!strings.HasPrefix(p.Path, "/Wujuhu/Codexio/releases/download/v") && raw != systemLatestManifest) {
		return nil, errors.New("更新地址无效")
	}
	request, e := http.NewRequestWithContext(ctx, "GET", raw, nil)
	if e != nil {
		return nil, errors.New("更新地址无效")
	}
	request.Header.Set("User-Agent", "Codexio/"+version)
	request.Header.Set("Cache-Control", "no-cache")
	request.Header.Set("Accept", "application/octet-stream")
	client := &http.Client{Timeout: 20 * time.Minute, Transport: &http.Transport{Proxy: systemAccountProxy, ResponseHeaderTimeout: 20 * time.Second, TLSHandshakeTimeout: 15 * time.Second, IdleConnTimeout: 30 * time.Second}, CheckRedirect: func(next *http.Request, via []*http.Request) error {
		v := next.URL
		if len(via) >= 5 || v.Scheme != "https" || v.User != nil {
			return errors.New("更新重定向无效")
		}
		if v.Host == "github.com" {
			if !strings.HasPrefix(v.Path, "/Wujuhu/Codexio/releases/") {
				return errors.New("更新仓库重定向被拒绝")
			}
		} else if v.Host != "release-assets.githubusercontent.com" && v.Host != "objects.githubusercontent.com" {
			return errors.New("更新下载站点被拒绝")
		}
		return nil
	}}
	response, e := client.Do(request)
	if e != nil {
		return nil, errors.New("无法连接 GitHub，请检查网络后重试")
	}
	return response, nil
}
func (u *Updater) Install(ctx context.Context) error {
	if u.opts.Mock {
		return errors.New("模拟模式不安装真实更新")
	}
	u.mu.Lock()
	if u.closed || u.installing || u.checking != nil || u.release == nil {
		u.mu.Unlock()
		return errors.New("请先检查更新，或等待当前操作完成")
	}
	release := *u.release
	u.installing = true
	installCtx, cancel := context.WithCancel(ctx)
	u.cancel = cancel
	u.mu.Unlock()
	defer func() { cancel(); u.mu.Lock(); u.installing = false; u.cancel = nil; u.mu.Unlock() }()
	fail := func(e error) error { u.publish("error", 0, e.Error()); return e }
	target := u.opts.Executable
	if target == "" {
		target, _ = os.Executable()
	}
	target, e := filepath.Abs(target)
	if e != nil || !strings.EqualFold(filepath.Base(target), "Codexio.exe") {
		return fail(errors.New("请以 Codexio.exe 文件名运行应用后更新"))
	}
	if e = systemRegularPath(target); e != nil {
		return fail(e)
	}
	self, e := os.Executable()
	if e != nil || !strings.EqualFold(filepath.Clean(self), target) {
		return fail(errors.New("只能更新当前应用的原路径"))
	}
	if u.opts.Exit == nil {
		return fail(errors.New("应用退出入口未就绪，无法安装"))
	}
	root := filepath.Join(u.opts.Directory, "updates")
	if e = os.MkdirAll(root, 0700); e != nil {
		return fail(errors.New("无法建立更新暂存目录"))
	}
	id, e := systemRandomID()
	if e != nil {
		return fail(e)
	}
	directory := filepath.Join(root, id)
	if e = os.Mkdir(directory, 0700); e != nil {
		return fail(errors.New("无法建立更新暂存目录"))
	}
	u.mu.Lock()
	u.job = directory
	u.mu.Unlock()
	u.publish("downloading", 0, "")
	if e = u.download(installCtx, release, directory); e != nil {
		return fail(e)
	}
	u.publish("verifying", 100, "")
	oldHash, e := systemFileHash(target)
	if e != nil {
		return fail(errors.New("无法校验当前程序"))
	}
	helper := filepath.Join(directory, "CodexioUpdater.exe")
	if e = systemCopyFile(target, helper); e != nil {
		return fail(errors.New("无法准备更新程序"))
	}
	job := Row{"target": target, "old_sha256": oldHash, "sha256": release.digest, "size": release.size, "version": release.version, "parent_pid": os.Getpid(), "arguments": []string{}}
	for _, a := range os.Args[1:] {
		if a == "--apply-update" || a == "--mock" || a == "--smoke" {
			return fail(errors.New("当前运行模式不能安装真实更新"))
		}
	}
	job["arguments"] = os.Args[1:]
	if e = WriteJSON(filepath.Join(directory, "job.json"), job); e != nil {
		return fail(errors.New("无法保存更新任务"))
	}
	env := systemSetEnv(os.Environ(), "CODEXIO_DATA_DIR", u.opts.Directory)
	cmd, e := systemDetachedStart(helper, []string{"--apply-update", filepath.Join(directory, "job.json")}, directory, env)
	if e != nil {
		return fail(errors.New("无法启动更新程序，当前版本继续运行"))
	}
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()
	deadline := time.NewTimer(25 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	u.publish("preparing", 100, "")
	for {
		state, _ := systemReadJSON(filepath.Join(directory, "state.json"), 32768)
		if ValueString(state["state"]) == "ready" {
			if e = os.WriteFile(filepath.Join(directory, "commit"), []byte("confirmed\n"), 0600); e != nil {
				_ = os.WriteFile(filepath.Join(directory, "cancel"), nil, 0600)
				return fail(errors.New("无法确认安装，当前版本继续运行"))
			}
			u.publish("waiting_exit", 100, "")
			u.opts.Exit()
			return nil
		}
		if ValueString(state["state"]) == "failed" {
			return fail(errors.New(ValueString(state["message"])))
		}
		select {
		case <-installCtx.Done():
			_ = os.WriteFile(filepath.Join(directory, "cancel"), nil, 0600)
			return fail(errors.New("已取消更新"))
		case <-deadline.C:
			_ = os.WriteFile(filepath.Join(directory, "cancel"), nil, 0600)
			return fail(errors.New("更新准备超时，当前版本继续运行"))
		case <-exited:
			return fail(errors.New("更新程序已退出，当前版本继续运行"))
		case <-ticker.C:
		}
	}
}
func (u *Updater) download(ctx context.Context, release systemRelease, directory string) error {
	partial := filepath.Join(directory, "package.part")
	defer os.Remove(partial)
	response, e := systemUpdateRequest(ctx, release.url, u.opts.Version)
	if e != nil {
		return e
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return errors.New("更新下载失败")
	}
	if response.ContentLength >= 0 && response.ContentLength != release.size {
		return errors.New("更新文件大小与发布信息不一致")
	}
	output, e := os.OpenFile(partial, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return errors.New("无法创建下载文件")
	}
	defer output.Close()
	hash := sha256.New()
	buffer := make([]byte, 256<<10)
	received := int64(0)
	previous := -1
	for {
		if ctx.Err() != nil {
			return errors.New("已取消更新")
		}
		n, readErr := response.Body.Read(buffer)
		if n > 0 {
			received += int64(n)
			if received > release.size {
				return errors.New("更新文件大小与发布信息不一致")
			}
			hash.Write(buffer[:n])
			if _, e = output.Write(buffer[:n]); e != nil {
				return errors.New("下载文件写入失败")
			}
			percent := int(received * 100 / release.size)
			if percent != previous {
				u.publish("downloading", percent, "")
				previous = percent
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return errors.New("下载未完成，请检查网络后重试")
		}
	}
	if received != release.size || hex.EncodeToString(hash.Sum(nil)) != release.digest {
		return errors.New("更新文件校验失败，已保留当前版本")
	}
	if e = output.Sync(); e != nil {
		return errors.New("下载文件写入失败")
	}
	if e = output.Close(); e != nil {
		return e
	}
	if e = systemVerifyEXE(partial, release.size, release.digest); e != nil {
		return e
	}
	if ctx.Err() != nil {
		return errors.New("已取消更新")
	}
	return os.Rename(partial, filepath.Join(directory, "package.bin"))
}
func (u *Updater) Close() error {
	u.mu.Lock()
	u.closed = true
	if u.lifetimeCancel != nil {
		u.lifetimeCancel()
	}
	if u.cancel != nil {
		u.cancel()
	}
	job := u.job
	state := ValueString(u.state["status"])
	u.mu.Unlock()
	if job != "" && state != "waiting_exit" {
		_ = os.WriteFile(filepath.Join(job, "cancel"), nil, 0600)
	}
	return nil
}

// AcknowledgeUpdate is called by the application only after its actual UI and
// services are ready. Constructing a service is not evidence of successful start.
func (u *Updater) AcknowledgeUpdate() error {
	if u.opts.Mock {
		return nil
	}
	raw := os.Getenv("CODEXIO_UPDATE_JOB")
	if raw == "" {
		systemCleanupUpdates(u.opts.Directory)
		return nil
	}
	_ = os.Unsetenv("CODEXIO_UPDATE_JOB")
	directory, e := systemValidateJobDirectory(raw, u.opts.Directory)
	if e != nil {
		return e
	}
	job, e := systemReadJSON(filepath.Join(directory, "job.json"), 32768)
	if e != nil {
		return e
	}
	self, e := os.Executable()
	if e != nil {
		return e
	}
	state, _ := systemReadJSON(filepath.Join(directory, "state.json"), 32768)
	if ValueString(state["state"]) == "rolled_back" {
		hash, err := systemFileHash(self)
		if err == nil && hash == ValueString(job["old_sha256"]) {
			u.publish("rolled_back", 0, ValueString(state["message"]))
			return nil
		}
	}
	if !strings.EqualFold(filepath.Clean(self), filepath.Clean(ValueString(job["target"]))) || ValueString(job["version"]) != u.opts.Version {
		return errors.New("启动确认版本或路径不一致")
	}
	if e = systemVerifyEXE(self, ValueInt(job["size"]), ValueString(job["sha256"])); e != nil {
		return e
	}
	return WriteJSON(filepath.Join(directory, "ack.json"), Row{"version": u.opts.Version, "pid": os.Getpid(), "sha256": job["sha256"]})
}
func systemValidateJobDirectory(raw, data string) (string, error) {
	absolute, e := filepath.Abs(raw)
	if e != nil || !systemJobPattern.MatchString(filepath.Base(absolute)) {
		return "", errors.New("更新暂存目录无效")
	}
	root, e := filepath.Abs(filepath.Join(data, "updates"))
	if e != nil {
		return "", e
	}
	if !strings.EqualFold(filepath.Dir(absolute), root) {
		return "", errors.New("更新暂存目录不属于本应用")
	}
	resolved, e := filepath.EvalSymlinks(absolute)
	if e != nil || !strings.EqualFold(resolved, absolute) {
		return "", errors.New("更新暂存目录不能是链接")
	}
	s, e := os.Stat(absolute)
	if e != nil || !s.IsDir() {
		return "", errors.New("更新暂存目录无效")
	}
	return absolute, nil
}
func systemRegularPath(path string) error {
	s, e := os.Lstat(path)
	if e != nil || !s.Mode().IsRegular() || s.Mode()&os.ModeSymlink != 0 {
		return errors.New("更新文件路径无效")
	}
	resolved, e := filepath.EvalSymlinks(path)
	if e != nil || !strings.EqualFold(filepath.Clean(resolved), filepath.Clean(path)) {
		return errors.New("更新路径不能经过链接")
	}
	return nil
}
func systemFileHash(path string) (string, error) {
	if e := systemRegularPath(path); e != nil {
		return "", e
	}
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	hash := sha256.New()
	_, e = io.Copy(hash, f)
	if e != nil {
		return "", e
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}
func systemVerifyEXE(path string, size int64, digest string) error {
	if size < 2 || size > systemMaxUpdateBytes || !systemDigestPattern.MatchString(digest) {
		return errors.New("更新校验信息无效")
	}
	s, e := os.Stat(path)
	if e != nil || s.Size() != size {
		return errors.New("更新文件大小不符")
	}
	hash, e := systemFileHash(path)
	if e != nil || !strings.EqualFold(hash, digest) {
		return errors.New("更新文件 SHA-256 校验失败")
	}
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	header := make([]byte, 64)
	if _, e = io.ReadFull(f, header); e != nil || !bytes.Equal(header[:2], []byte("MZ")) {
		return errors.New("更新文件不是有效的 Windows 程序")
	}
	offset := int64(binary.LittleEndian.Uint32(header[60:]))
	if offset < 64 || offset > size-4 {
		return errors.New("更新文件 PE 头无效")
	}
	pe := make([]byte, 4)
	if _, e = f.ReadAt(pe, offset); e != nil || !bytes.Equal(pe, []byte{'P', 'E', 0, 0}) {
		return errors.New("更新文件 PE 头无效")
	}
	return nil
}
func systemCopyFile(source, target string) error {
	if e := systemRegularPath(source); e != nil {
		return e
	}
	in, e := os.Open(source)
	if e != nil {
		return e
	}
	defer in.Close()
	out, e := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	_, e = io.Copy(out, in)
	if e == nil {
		e = out.Sync()
	}
	closeErr := out.Close()
	if e == nil {
		e = closeErr
	}
	return e
}
func systemState(directory, state, message string) error {
	return WriteJSON(filepath.Join(directory, "state.json"), Row{"state": state, "message": message, "time": time.Now().Unix()})
}
func systemReplaceWithRetry(ctx context.Context, source, target string) error {
	timer := time.NewTimer(30 * time.Second)
	defer timer.Stop()
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		if e := os.Rename(source, target); e == nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
			return errors.New("程序文件仍被占用，已保留旧版和下载文件")
		case <-ticker.C:
		}
	}
}
func RunUpdateHelper(args []string) (bool, error) {
	if len(args) == 0 || args[0] != "--apply-update" {
		return false, nil
	}
	if len(args) != 2 || os.Getenv("CODEXIO_MOCK") == "1" || systemContains(args, "--mock") {
		return true, errors.New("更新运行模式无效")
	}
	data := os.Getenv("CODEXIO_DATA_DIR")
	if data == "" {
		var e error
		data, e = ResolveDataDirectory("")
		if e != nil {
			return true, e
		}
	}
	path, e := filepath.Abs(args[1])
	if e != nil || filepath.Base(path) != "job.json" {
		return true, errors.New("更新任务位置无效")
	}
	if e = systemRegularPath(path); e != nil {
		return true, e
	}
	directory, e := systemValidateJobDirectory(filepath.Dir(path), data)
	if e != nil {
		return true, e
	}
	self, e := os.Executable()
	if e != nil || !strings.EqualFold(filepath.Dir(self), directory) || !strings.EqualFold(filepath.Base(self), "CodexioUpdater.exe") {
		return true, errors.New("更新程序位置无效")
	}
	job, e := systemReadJSON(path, 32768)
	if e == nil {
		e = systemApplyUpdate(context.Background(), directory, job)
	}
	if e != nil {
		_ = systemState(directory, "failed", e.Error())
	}
	return true, e
}
func systemApplyUpdate(ctx context.Context, directory string, job Row) error {
	target := ValueString(job["target"])
	if !filepath.IsAbs(target) || !strings.EqualFold(filepath.Base(target), "Codexio.exe") {
		return errors.New("更新目标无效")
	}
	if e := systemRegularPath(target); e != nil {
		return e
	}
	if _, e := systemVersion(ValueString(job["version"])); e != nil {
		return e
	}
	if !systemDigestPattern.MatchString(ValueString(job["old_sha256"])) {
		return errors.New("旧版校验信息无效")
	}
	args := ValueStrings(job["arguments"])
	if len(args) > 64 {
		return errors.New("重启参数过多")
	}
	for _, a := range args {
		if len(a) > 4096 || strings.ContainsRune(a, 0) || a == "--apply-update" || a == "--mock" || a == "--smoke" {
			return errors.New("重启参数无效")
		}
	}
	pid := int(ValueInt(job["parent_pid"]))
	if pid <= 0 || pid == os.Getpid() {
		return errors.New("更新进程信息无效")
	}
	wait, releaseParent, e := systemWaitParent(pid, target, directory)
	if e != nil {
		return e
	}
	defer releaseParent()
	unlock, e := systemInstallLock(target)
	if e != nil {
		return e
	}
	defer unlock()
	packagePath := filepath.Join(directory, "package.bin")
	if e = systemVerifyEXE(packagePath, ValueInt(job["size"]), ValueString(job["sha256"])); e != nil {
		return e
	}
	hash, e := systemFileHash(target)
	if e != nil || hash != ValueString(job["old_sha256"]) {
		return errors.New("程序文件已变化，请重新检查更新")
	}
	pending := filepath.Join(filepath.Dir(target), ".Codexio-"+filepath.Base(directory)+".pending")
	defer os.Remove(pending)
	backup := filepath.Join(directory, "previous.bin")
	if e = systemCopyFile(packagePath, pending); e != nil {
		return errors.New("无法准备原位替换文件")
	}
	if e = systemVerifyEXE(pending, ValueInt(job["size"]), ValueString(job["sha256"])); e != nil {
		return e
	}
	if e = systemCopyFile(target, backup); e != nil {
		return errors.New("无法备份旧版")
	}
	if hash, e = systemFileHash(backup); e != nil || hash != ValueString(job["old_sha256"]) {
		return errors.New("旧版备份校验失败")
	}
	if e = systemState(directory, "ready", ""); e != nil {
		return e
	}
	if e = wait(ctx); e != nil {
		return e
	}
	hash, e = systemFileHash(target)
	if e != nil || hash != ValueString(job["old_sha256"]) {
		return errors.New("程序文件已变化，停止替换")
	}
	replaced := false
	var processDone chan error
	var processPID int
	restart := func() (chan error, int, error) {
		env := systemSetEnv(os.Environ(), "CODEXIO_UPDATE_JOB", directory)
		env = systemSetEnv(env, "CODEXIO_SKIP_UPDATE_ONCE", "1")
		c, e := systemDetachedStart(target, args, filepath.Dir(target), env)
		if e != nil {
			return nil, 0, e
		}
		done := make(chan error, 1)
		go func() { done <- c.Wait() }()
		return done, c.Process.Pid, nil
	}
	rollback := func(cause error) error {
		if processDone != nil {
			select {
			case <-processDone:
			default:
				_ = systemState(directory, "unconfirmed", "新版仍在运行，旧版备份已保留")
				return cause
			}
		}
		if replaced {
			hash, e := systemFileHash(target)
			if e != nil || hash != ValueString(job["sha256"]) {
				return errors.New("目标已变化，保留备份并停止回滚")
			}
			if e = systemCopyFile(backup, pending); e != nil {
				return e
			}
			if e = systemReplaceWithRetry(ctx, pending, target); e != nil {
				return e
			}
		}
		_ = systemState(directory, "rolled_back", cause.Error())
		_, _, e := restart()
		return e
	}
	if e = systemReplaceWithRetry(ctx, pending, target); e != nil {
		return rollback(e)
	}
	replaced = true
	processDone, processPID, e = restart()
	if e != nil {
		return rollback(errors.New("新版启动失败，正在恢复旧版"))
	}
	deadline := time.NewTimer(40 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		ack, _ := systemReadJSON(filepath.Join(directory, "ack.json"), 32768)
		if ValueString(ack["version"]) == ValueString(job["version"]) && ValueInt(ack["pid"]) == int64(processPID) && ValueString(ack["sha256"]) == ValueString(job["sha256"]) {
			return systemState(directory, "done", "已更新至 "+ValueString(job["version"]))
		}
		select {
		case <-processDone:
			processDone = nil
			return rollback(errors.New("新版启动失败，正在恢复旧版"))
		case <-deadline.C:
			return systemState(directory, "unconfirmed", "已替换并启动新版；启动确认超时，旧版备份已保留")
		case <-ticker.C:
		case <-ctx.Done():
			return rollback(ctx.Err())
		}
	}
}
func systemCleanupUpdates(data string) {
	root := filepath.Join(data, "updates")
	entries, e := os.ReadDir(root)
	if e != nil {
		return
	}
	type item struct {
		path     string
		modified time.Time
	}
	jobs := []item{}
	for _, entry := range entries {
		if !entry.IsDir() || entry.Type()&os.ModeSymlink != 0 || !systemJobPattern.MatchString(entry.Name()) {
			continue
		}
		path := filepath.Join(root, entry.Name())
		if _, e := systemValidateJobDirectory(path, data); e != nil {
			continue
		}
		s, e := entry.Info()
		if e == nil {
			jobs = append(jobs, item{path, s.ModTime()})
		}
	}
	sort.Slice(jobs, func(i, j int) bool { return jobs[i].modified.After(jobs[j].modified) })
	backups := 0
	for _, job := range jobs {
		if time.Since(job.modified) < 120*time.Second {
			continue
		}
		state, e := systemReadJSON(filepath.Join(job.path, "state.json"), 32768)
		if e != nil || !systemContains([]string{"done", "rolled_back", "failed", "cancelled", "unconfirmed"}, ValueString(state["state"])) {
			continue
		}
		_, backupErr := os.Stat(filepath.Join(job.path, "previous.bin"))
		keep := backupErr == nil && (backups < 2 || ValueString(state["state"]) == "unconfirmed")
		if keep {
			backups++
		}
		for _, name := range []string{"package.bin", "package.part", "CodexioUpdater.exe", "previous.bin"} {
			if name == "previous.bin" && keep {
				continue
			}
			path := filepath.Join(job.path, name)
			if systemRegularPath(path) == nil {
				_ = os.Remove(path)
			}
		}
	}
}
