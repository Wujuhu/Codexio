package backend

// MobileHost is a read-only projection owned by the desktop process. Its local
// transport is TLS plus raw RFC6455 frames (Network.framework skipHandshake).
import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/grandcat/zeroconf"
	qrcode "github.com/skip2/go-qrcode"
)

const mobileOrigin = "https://codexio-sync.503948883.workers.dev"
const mobileLimit = 262144
const mobileDetailLimit = 1048576
const mobileDetailChunk = 65536

var mobileUUID = regexp.MustCompile(`^[0-9a-fA-F-]{36}$`)
var mobileSecretPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{43}$`)
var mobileIDPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

type mobileDetail struct {
	Envelope   Row
	Expires    float64
	Access     time.Time
	Generation int64
	Thumbnails bool
	Source     string
}
type MobileHost struct {
	mu                        sync.Mutex
	actionMu                  sync.Mutex
	directory                 string
	store                     *Store
	quota                     *QuotaService
	config                    func() Row
	mock                      bool
	changed                   func()
	host                      Row
	lastSaved                 Row
	name, status, lastError   string
	enabled, closed           bool
	ticket, pending           Row
	qrData, qrURL             string
	listener                  net.Listener
	bonjour                   *zeroconf.Server
	connections               map[net.Conn]bool
	ctx                       context.Context
	cancel                    context.CancelFunc
	workerCancel              context.CancelFunc
	workerCtx                 context.Context
	wake                      chan struct{}
	clients                   chan struct{}
	datasets                  map[string]Row
	requests                  map[string]string
	details                   map[string]*mobileDetail
	projectionKey, historyKey string
	sent                      map[string]int64
	sentDetails               map[string]string
	cloudReaders              map[string]bool
	cloudDetails              bool
	cloudRequestKinds         bool
	cloudRecent               Row
	nextCapability            time.Time
	uploading, uploadAgain    bool
	cloudMu                   sync.Mutex
	detailMu                  sync.Mutex
	todayMetric               Row
	currentTask               Row
	runningCount              int
}

func NewMobileHost(directory string, store *Store, quota *QuotaService, config func() Row, mock bool, changed func()) *MobileHost {
	name, _ := os.Hostname()
	if name == "" {
		name = "Windows PC"
	}
	return &MobileHost{directory: directory, store: store, quota: quota, config: config, mock: mock, changed: changed, name: mobilePrefix(name, 40), status: "尚未开启", connections: map[net.Conn]bool{}, wake: make(chan struct{}, 1), clients: make(chan struct{}, 8), datasets: map[string]Row{}, requests: map[string]string{}, details: map[string]*mobileDetail{}, sent: map[string]int64{}, sentDetails: map[string]string{}, cloudReaders: map[string]bool{}}
}
func (m *MobileHost) notify() {
	if m.changed != nil {
		m.changed()
	}
}
func (m *MobileHost) failure(e error) {
	m.mu.Lock()
	changed := m.lastError != e.Error()
	m.lastError = e.Error()
	m.mu.Unlock()
	if changed {
		m.notify()
	}
}
func (m *MobileHost) Start(ctx context.Context) {
	m.actionMu.Lock()
	defer m.actionMu.Unlock()
	m.mu.Lock()
	if m.ctx != nil || m.closed {
		m.mu.Unlock()
		return
	}
	m.ctx, m.cancel = context.WithCancel(ctx)
	var loadError error
	if !m.mock {
		loadError = m.loadIdentityLocked(false)
	}
	m.mu.Unlock()
	if loadError != nil {
		m.failure(loadError)
		return
	}
	if !m.mock && ValueBool(m.config()["mobile_sync_enabled"]) {
		if e := m.enable(); e != nil {
			m.failure(e)
		}
	}
}
func (m *MobileHost) RequestProjection() {
	select {
	case m.wake <- struct{}{}:
	default:
	}
}
func (m *MobileHost) Public() Row {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.expireTicketLocked()
	readers := []Row{}
	notes := ValueRow(m.host["notes"])
	localSyncs := ValueRow(m.host["local_syncs"])
	cloudSync := ValueRow(m.host["cloud_sync"])
	for _, r := range ValueRows(m.host["readers"]) {
		id := ValueString(r["id"])
		last := ValueRow(localSyncs[id])
		if m.cloudReaders[id] && ValueString(cloudSync["time"]) > ValueString(last["time"]) {
			last = cloudSync
		}
		readers = append(readers, Row{"id": id, "name": r["name"], "note": ValueString(notes[id]), "last_sync": CloneRow(last)})
	}
	var pending any
	if m.pending != nil {
		pending = Row{"id": m.pending["id"], "name": m.pending["name"]}
	}
	return Row{"enabled": m.enabled, "status": m.status, "error": m.lastError, "name": m.name, "readers": readers, "pending": pending, "qr_data": m.qrData, "qr_url": m.qrURL, "cloud_enabled": ValueBool(m.host["cloud_enabled"]), "last_sync": CloneRow(ValueRow(m.host["last_sync"]))}
}

// Match Mac SyncStamp: LAN means an authenticated receiver acknowledged;
// cloud means a completed upload. Coalesce persistence to the displayed minute.
func (m *MobileHost) recordSyncLocked(route, reader string) bool {
	now := time.Now()
	previous := ValueRow(m.host["cloud_sync"])
	local := ValueRow(m.host["local_syncs"])
	if reader != "" {
		previous = ValueRow(local[reader])
	}
	if at, ok := ParseStamp(previous["time"]); ok && at.Truncate(time.Minute).Equal(now.Truncate(time.Minute)) {
		return false
	}
	stamp := Row{"time": UTCStamp(now), "route": route}
	m.host["last_sync"] = stamp
	if reader == "" {
		m.host["cloud_sync"] = stamp
	} else {
		local[reader] = stamp
		m.host["local_syncs"] = local
	}
	if e := m.saveLocked(); e != nil {
		m.lastError = e.Error()
	}
	return true
}
func (m *MobileHost) expireTicketLocked() {
	if m.ticket != nil && ValueInt(m.ticket["expires"]) <= time.Now().Unix() {
		m.ticket = nil
		m.pending = nil
		m.qrData = ""
		m.qrURL = ""
	}
}
func mobilePrefix(s string, n int) string {
	if len(s) <= n {
		return s
	}
	s = s[:n]
	for !utf8.ValidString(s) && len(s) > 0 {
		s = s[:len(s)-1]
	}
	return s
}
func mobileJSON(v any) ([]byte, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if e := encoder.Encode(v); e != nil {
		return nil, e
	}
	return bytes.TrimSuffix(out.Bytes(), []byte{'\n'}), nil
}
func (m *MobileHost) loadProjectionLocked() error {
	f, e := os.Open(filepath.Join(m.directory, "mobile-projection-win.json"))
	if errors.Is(e, os.ErrNotExist) {
		return nil
	}
	if e != nil {
		return errors.New("无法读取移动摘要缓存，原文件已保留")
	}
	defer f.Close()
	data, e := io.ReadAll(io.LimitReader(f, (2<<20)+1))
	if e != nil || len(data) > 2<<20 {
		return errors.New("移动摘要缓存超过容量限制，原文件已保留")
	}
	value, e := DecodeRow(data)
	if e != nil {
		return errors.New("移动摘要缓存格式错误，原文件已保留")
	}
	valid := func(envelope Row, dataset string) bool {
		payload := ValueString(envelope["payload"])
		_, isString := envelope["payload"].(string)
		return isString && ValueString(envelope["dataset"]) == dataset && ValueInt(envelope["revision"]) > 0 && len(payload) <= mobileLimit && json.Valid([]byte(payload)) && HashString(payload) == ValueString(envelope["digest"])
	}
	for _, dataset := range []string{"live", "recent", "trends"} {
		if envelope := ValueRow(ValueRow(value["datasets"])[dataset]); valid(envelope, dataset) {
			m.datasets[dataset] = envelope
		}
	}
	if envelope := ValueRow(value["cloudRecent"]); valid(envelope, "recent") {
		m.cloudRecent = envelope
	}
	return nil
}
func (m *MobileHost) saveProjectionLocked() error {
	if m.mock {
		return nil
	}
	data, e := mobileJSON(Row{"datasets": m.datasets, "cloudRecent": m.cloudRecent})
	if e != nil {
		return e
	}
	return writePrivateFile(filepath.Join(m.directory, "mobile-projection-win.json"), data)
}
func mobileSecret() (string, error) {
	b := make([]byte, 32)
	_, e := rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b), e
}
func mobileEqual(a, b string) bool {
	return len(a) == len(b) && subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}
func mobileIdentity(name string) (Row, error) {
	key, e := rsa.GenerateKey(rand.Reader, 2048)
	if e != nil {
		return nil, e
	}
	serial, e := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 159))
	if e != nil {
		return nil, e
	}
	now := time.Now()
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "Codexio Local"}, NotBefore: now.Add(-24 * time.Hour), NotAfter: now.AddDate(10, 0, 0), BasicConstraintsValid: true, IsCA: true, MaxPathLenZero: true, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, e := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if e != nil {
		return nil, e
	}
	private, e := x509.MarshalPKCS8PrivateKey(key)
	if e != nil {
		return nil, e
	}
	id := make([]byte, 16)
	if _, e = rand.Read(id); e != nil {
		return nil, e
	}
	id[6] = (id[6] & 15) | 64
	id[8] = (id[8] & 63) | 128
	u := strings.ToUpper(fmt.Sprintf("%x-%x-%x-%x-%x", id[:4], id[4:6], id[6:8], id[8:10], id[10:]))
	writer, e := mobileSecret()
	if e != nil {
		return nil, e
	}
	pin := sha256.Sum256(der)
	return Row{"id": u, "name": name, "writer": writer, "cloud_enabled": false, "certificate": string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})), "private_key": string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: private})), "pin": hex.EncodeToString(pin[:]), "readers": []Row{}, "revoked": []string{}, "notes": Row{}}, nil
}
func (m *MobileHost) loadIdentityLocked(create bool) error {
	if m.host != nil {
		return nil
	}
	p := filepath.Join(m.directory, "mobile-host-win.dat")
	f, e := os.Open(p)
	if errors.Is(e, os.ErrNotExist) {
		if !create {
			return nil
		}
		m.host, e = mobileIdentity(m.name)
		if e != nil {
			return e
		}
		return m.saveLocked()
	}
	if e != nil {
		return errors.New("无法读取移动设备凭据，原文件已保留")
	}
	defer f.Close()
	raw, e := io.ReadAll(io.LimitReader(f, (2<<20)+1))
	if e != nil || len(raw) > 2<<20 {
		return errors.New("移动设备凭据超过容量限制，原文件已保留")
	}
	raw, e = mobileDPAPI(raw, true)
	if e != nil {
		return errors.New("无法解密移动设备凭据，原文件已保留")
	}
	h, e := DecodeRow(raw)
	if e != nil {
		return errors.New("移动设备凭据格式错误，原文件已保留")
	}
	cert, e := tls.X509KeyPair([]byte(ValueString(h["certificate"])), []byte(ValueString(h["private_key"])))
	if e != nil || len(cert.Certificate) == 0 {
		return errors.New("移动设备证书无效，原文件已保留")
	}
	pin := sha256.Sum256(cert.Certificate[0])
	if !mobileUUID.MatchString(ValueString(h["id"])) || !mobileEqual(ValueString(h["pin"]), hex.EncodeToString(pin[:])) || len(ValueRows(h["readers"])) > 3 {
		return errors.New("移动设备身份无效，原文件已保留")
	}
	if writer := ValueString(h["writer"]); writer != "" && !mobileSecretPattern.MatchString(writer) {
		return errors.New("移动设备云端凭据无效，原文件已保留")
	}
	for _, r := range ValueRows(h["readers"]) {
		if !mobileUUID.MatchString(ValueString(r["id"])) || !mobileSecretPattern.MatchString(ValueString(r["localSecret"])) || !mobileSecretPattern.MatchString(ValueString(r["cloudSecret"])) || len([]rune(ValueString(r["name"]))) > 40 {
			return errors.New("已配对移动设备凭据无效，原文件已保留")
		}
	}
	m.host = h
	m.lastSaved = CloneRow(h)
	if n := ValueString(h["name"]); n != "" {
		m.name = mobilePrefix(n, 40)
	}
	return nil
}
func (m *MobileHost) saveLocked() error {
	if m.mock || m.host == nil {
		return nil
	}
	pending := CloneRow(m.host)
	raw, e := mobileJSON(pending)
	if e != nil {
		return e
	}
	raw, e = mobileDPAPI(raw, false)
	if e != nil {
		m.restoreSavedLocked()
		return errors.New("无法保存受保护的移动设备凭据")
	}
	if e = writePrivateFile(filepath.Join(m.directory, "mobile-host-win.dat"), raw); e != nil {
		m.restoreSavedLocked()
		return e
	}
	m.lastSaved = pending
	return nil
}
func (m *MobileHost) restoreSavedLocked() {
	if m.lastSaved == nil {
		m.host = nil
		return
	}
	m.host = CloneRow(m.lastSaved)
	if name := ValueString(m.host["name"]); name != "" {
		m.name = name
	}
}
func mobileAddresses() ([]string, []net.Interface) {
	addresses := []string{}
	ifs := []net.Interface{}
	all, _ := net.Interfaces()
	for _, in := range all {
		if in.Flags&net.FlagUp == 0 || in.Flags&net.FlagLoopback != 0 || in.Flags&net.FlagMulticast == 0 {
			continue
		}
		found := false
		aa, _ := in.Addrs()
		for _, a := range aa {
			ip, _, e := net.ParseCIDR(a.String())
			if e == nil && ip.To4() != nil && !ip.IsLoopback() && !ip.IsUnspecified() {
				addresses = append(addresses, ip.String())
				found = true
			}
		}
		if found {
			ifs = append(ifs, in)
		}
	}
	return addresses, ifs
}
func (m *MobileHost) enable() error {
	m.mu.Lock()
	if m.mock {
		m.mu.Unlock()
		return errors.New("模拟模式不会启动真实移动同步")
	}
	if m.closed {
		m.mu.Unlock()
		return errors.New("移动同步已关闭")
	}
	if m.enabled {
		m.mu.Unlock()
		return nil
	}
	if m.ctx == nil {
		m.ctx, m.cancel = context.WithCancel(context.Background())
	}
	if e := m.loadIdentityLocked(true); e != nil {
		m.mu.Unlock()
		return e
	}
	if e := m.loadProjectionLocked(); e != nil {
		m.mu.Unlock()
		return e
	}
	h := CloneRow(m.host)
	m.mu.Unlock()
	cert, e := tls.X509KeyPair([]byte(ValueString(h["certificate"])), []byte(ValueString(h["private_key"])))
	if e != nil {
		return errors.New("移动设备 TLS 证书不可用")
	}
	ips, ifs := mobileAddresses()
	if len(ips) == 0 {
		return errors.New("没有可用的局域网 IPv4 地址")
	}
	listener, e := tls.Listen("tcp4", "0.0.0.0:0", &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12})
	if e != nil {
		return e
	}
	bonjour, e := zeroconf.RegisterProxy(ValueString(h["id"]), "_codexio._tcp", "local.", listener.Addr().(*net.TCPAddr).Port, ValueString(h["id"])+".local.", ips, []string{"protocol=1"}, ifs)
	if e != nil {
		listener.Close()
		return fmt.Errorf("Bonjour 注册失败: %w", e)
	}
	m.mu.Lock()
	ctx, cancel := context.WithCancel(m.ctx)
	m.workerCancel = cancel
	m.workerCtx = ctx
	m.listener = listener
	m.bonjour = bonjour
	m.enabled = true
	m.status = "局域网服务已开启"
	m.lastError = ""
	m.projectionKey = ""
	m.historyKey = ""
	m.mu.Unlock()
	go m.serve(ctx, listener)
	go m.run(ctx)
	m.RequestProjection()
	m.notify()
	return nil
}
func (m *MobileHost) stop() {
	m.mu.Lock()
	if m.workerCancel != nil {
		m.workerCancel()
		m.workerCancel = nil
	}
	listener, bonjour := m.listener, m.bonjour
	m.listener = nil
	m.bonjour = nil
	m.enabled = false
	m.ticket = nil
	m.pending = nil
	m.qrData = ""
	m.qrURL = ""
	m.details = map[string]*mobileDetail{}
	m.status = "同步已停止"
	for c := range m.connections {
		c.Close()
	}
	m.mu.Unlock()
	if listener != nil {
		listener.Close()
	}
	if bonjour != nil {
		bonjour.Shutdown()
	}
	m.notify()
}
func (m *MobileHost) Close() error {
	m.actionMu.Lock()
	defer m.actionMu.Unlock()
	m.mu.Lock()
	if m.closed {
		m.mu.Unlock()
		return nil
	}
	m.closed = true
	if m.cancel != nil {
		m.cancel()
	}
	m.mu.Unlock()
	m.stop()
	return nil
}
func (m *MobileHost) run(ctx context.Context) {
	tick := time.NewTicker(30 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-m.wake:
			m.project(ctx)
		case <-tick.C:
			m.mu.Lock()
			old := m.qrData
			m.expireTicketLocked()
			expired := old != m.qrData
			m.mu.Unlock()
			if expired {
				m.notify()
			}
			m.project(ctx)
			m.upload()
		}
	}
}

func (m *MobileHost) Action(action string, v Row) (Row, error) {
	m.actionMu.Lock()
	defer m.actionMu.Unlock()
	if v == nil {
		v = Row{}
	}
	if m.mock {
		return m.Public(), errors.New("模拟模式不会修改移动设备凭据或启动网络服务")
	}
	var e error
	switch action {
	case "enable", "disable":
		enabled := action == "enable"
		if b, ok := v["enabled"].(bool); ok {
			enabled = b
		}
		if enabled {
			e = m.enable()
		} else {
			m.stop()
		}
		if e == nil {
			e = SaveConfig(m.directory, Row{"mobile_sync_enabled": enabled})
		}
	case "pair":
		m.mu.Lock()
		m.expireTicketLocked()
		if !m.enabled || m.host == nil {
			e = errors.New("请先开启局域网同步")
		} else if len(ValueRows(m.host["readers"])) >= 3 {
			e = errors.New("最多配对 3 台 iPhone")
		} else {
			var secret string
			secret, e = mobileSecret()
			if e == nil {
				m.ticket = Row{"version": 1, "host": m.host["id"], "name": m.name, "pin": m.host["pin"], "ticket": secret, "expires": float64(time.Now().Unix()) + 300}
				if ValueBool(m.host["cloud_enabled"]) {
					m.ticket["cloud"] = mobileOrigin
				}
				m.pending = nil
				raw, _ := mobileJSON(m.ticket)
				m.qrData = string(raw)
				png, err := qrcode.Encode(m.qrData, qrcode.Medium, 360)
				if err != nil {
					e = err
				} else {
					m.qrURL = "data:image/png;base64," + base64.StdEncoding.EncodeToString(png)
				}
			}
		}
		m.mu.Unlock()
	case "approve", "deny":
		m.mu.Lock()
		m.expireTicketLocked()
		if m.pending == nil || m.ticket == nil {
			e = errors.New("配对请求已过期")
		} else if id := ValueString(v["id"]); id != "" && id != ValueString(m.pending["id"]) {
			e = errors.New("配对请求已变化，请核对当前设备")
		} else {
			if action == "approve" {
				rs := ValueRows(m.host["readers"])
				if len(rs) >= 3 {
					e = errors.New("最多配对 3 台 iPhone")
				} else {
					for _, r := range rs {
						if r["id"] == m.pending["id"] {
							e = errors.New("设备已配对")
						}
					}
					if e == nil {
						m.host["readers"] = append(rs, CloneRow(m.pending))
						e = m.saveLocked()
					}
				}
			}
			if e == nil {
				m.ticket = nil
				m.pending = nil
				m.qrData = ""
				m.qrURL = ""
			}
		}
		m.mu.Unlock()
		if e == nil && action == "approve" {
			m.upload()
		}
	case "revoke":
		id := ValueString(v["id"])
		m.mu.Lock()
		rs := []Row{}
		found := false
		for _, r := range ValueRows(m.host["readers"]) {
			if ValueString(r["id"]) == id {
				found = true
			} else {
				rs = append(rs, r)
			}
		}
		if !found {
			e = errors.New("找不到已配对设备")
		} else {
			m.host["readers"] = rs
			m.host["revoked"] = append(ValueStrings(m.host["revoked"]), id)
			delete(m.cloudReaders, id)
			delete(ValueRow(m.host["notes"]), id)
			delete(ValueRow(m.host["local_syncs"]), id)
			e = m.saveLocked()
		}
		m.mu.Unlock()
		if e == nil {
			m.upload()
		}
	case "rename":
		name := mobilePrefix(strings.TrimSpace(ValueString(v["name"])), 40)
		if name == "" {
			e = errors.New("设备名称不能为空")
		} else {
			m.mu.Lock()
			m.name = name
			if m.host != nil {
				m.host["name"] = name
				e = m.saveLocked()
			}
			m.projectionKey = ""
			m.mu.Unlock()
			m.RequestProjection()
		}
	case "note":
		m.mu.Lock()
		id := ValueString(v["id"])
		found := false
		for _, r := range ValueRows(m.host["readers"]) {
			if ValueString(r["id"]) == id {
				found = true
			}
		}
		if !found {
			e = errors.New("找不到已配对设备")
		} else {
			notes := ValueRow(m.host["notes"])
			notes[id] = mobilePrefix(strings.TrimSpace(ValueString(v["note"])), 80)
			m.host["notes"] = notes
			e = m.saveLocked()
		}
		m.mu.Unlock()
	case "enroll":
		e = m.enroll(ValueString(v["invite"]))
	case "remove-cloud":
		e = m.removeCloud()
	case "upload":
		m.upload()
	default:
		e = errors.New("不支持的移动同步操作")
	}
	if e != nil {
		m.failure(e)
	} else {
		m.notify()
	}
	return m.Public(), e
}
