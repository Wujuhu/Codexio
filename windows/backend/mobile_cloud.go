package backend

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type mobileHTTPError struct {
	status int
	reason string
}

func (e *mobileHTTPError) Error() string {
	if e.reason != "" {
		return "云端同步 HTTP " + strconv.Itoa(e.status) + "（" + e.reason + "）"
	}
	return "云端同步 HTTP " + strconv.Itoa(e.status)
}

func (m *MobileHost) cloud(ctx context.Context, path, method, token string, value any, detail bool) (Row, error) {
	if m.mock || ctx == nil || ctx.Err() != nil {
		return nil, errors.New("云端同步当前不可用")
	}
	var body []byte
	var e error
	if value != nil {
		body, e = mobileJSON(value)
		if e != nil {
			return nil, e
		}
		limit := mobileLimit
		if detail {
			limit = mobileDetailLimit + 4096
		}
		if len(body) > limit {
			return nil, errors.New("云端同步内容超过容量限制")
		}
	}
	request, e := http.NewRequestWithContext(ctx, method, mobileOrigin+path, bytes.NewReader(body))
	if e != nil {
		return nil, errors.New("无效云端请求")
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("User-Agent", "Codexio/0.3.4")
	client := &http.Client{Timeout: 15 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, e := client.Do(request)
	if e != nil {
		return nil, errors.New("云端连接失败，保留上次数据")
	}
	defer response.Body.Close()
	raw, e := io.ReadAll(io.LimitReader(response.Body, mobileLimit+1))
	if e != nil || len(raw) > mobileLimit {
		return nil, errors.New("云端响应无效或超过容量限制")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		failure, _ := DecodeRow(raw)
		return nil, &mobileHTTPError{status: response.StatusCode, reason: ValueString(failure["error"])}
	}
	if len(raw) == 0 {
		return Row{}, nil
	}
	r, e := DecodeRow(raw)
	if e != nil {
		return nil, errors.New("云端响应格式无效")
	}
	return r, nil
}
func (m *MobileHost) enroll(invite string) error {
	invite = strings.TrimSpace(invite)
	if invite == "" || len(invite) > 4096 || strings.ContainsAny(invite, "\r\n") {
		return errors.New("请输入有效邀请码")
	}
	m.cloudMu.Lock()
	defer m.cloudMu.Unlock()
	m.mu.Lock()
	if !m.enabled || m.host == nil {
		m.mu.Unlock()
		return errors.New("请先开启局域网同步")
	}
	writer := ValueString(m.host["writer"])
	if writer == "" {
		var e error
		writer, e = mobileSecret()
		if e != nil {
			m.mu.Unlock()
			return e
		}
		m.host["writer"] = writer
		if e = m.saveLocked(); e != nil {
			m.mu.Unlock()
			return e
		}
	}
	id, name, ctx := ValueString(m.host["id"]), m.name, m.ctx
	m.mu.Unlock()
	if _, e := m.cloud(ctx, "/v1/enroll", "POST", invite, Row{"host": id, "writer": writer, "name": name}, false); e != nil {
		return e
	}
	m.mu.Lock()
	m.host["cloud_enabled"] = true
	e := m.saveLocked()
	if e == nil {
		m.status = "云端已启用"
	}
	m.mu.Unlock()
	if e == nil {
		m.upload()
	}
	return e
}
func (m *MobileHost) removeCloud() error {
	m.cloudMu.Lock()
	defer m.cloudMu.Unlock()
	m.mu.Lock()
	if !ValueBool(m.host["cloud_enabled"]) {
		m.mu.Unlock()
		return nil
	}
	id, writer, ctx := ValueString(m.host["id"]), ValueString(m.host["writer"]), m.ctx
	m.mu.Unlock()
	if _, e := m.cloud(ctx, "/v1/hosts/"+url.PathEscape(id)+"/key", "DELETE", writer, nil, false); e != nil {
		return e
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.host["cloud_enabled"] = false
	m.host["writer"] = ""
	m.sent = map[string]int64{}
	m.sentDetails = map[string]string{}
	m.cloudReaders = map[string]bool{}
	m.nextCapability = time.Time{}
	m.status = "云端密钥已移除"
	return m.saveLocked()
}
func (m *MobileHost) upload() {
	m.mu.Lock()
	if m.mock || !m.enabled || m.closed || m.host == nil || !ValueBool(m.host["cloud_enabled"]) || ValueString(m.host["writer"]) == "" {
		m.mu.Unlock()
		return
	}
	if m.uploading {
		m.uploadAgain = true
		m.mu.Unlock()
		return
	}
	needed := len(ValueStrings(m.host["revoked"])) > 0 || time.Now().After(m.nextCapability)
	for _, r := range ValueRows(m.host["readers"]) {
		needed = needed || !m.cloudReaders[ValueString(r["id"])]
	}
	for name, d := range m.datasets {
		needed = needed || m.sent[name] != ValueInt(d["revision"])
	}
	generation := m.store.Generation()
	if m.cloudDetails {
		for id, d := range m.details {
			needed = needed || d.Generation == generation && d.Expires > float64(time.Now().Unix()) && m.sentDetails[id] != ValueString(d.Envelope["digest"])
		}
	}
	if !needed {
		m.mu.Unlock()
		return
	}
	m.uploading = true
	m.mu.Unlock()
	go m.uploadWork()
}
func (m *MobileHost) uploadWork() {
	m.cloudMu.Lock()
	defer m.cloudMu.Unlock()
	defer func() {
		m.mu.Lock()
		again := m.uploadAgain
		m.uploadAgain = false
		m.uploading = false
		m.mu.Unlock()
		if again {
			m.upload()
		}
	}()
	m.mu.Lock()
	if !m.enabled || m.closed || !ValueBool(m.host["cloud_enabled"]) {
		m.mu.Unlock()
		return
	}
	ctx := m.workerCtx
	host := CloneRow(m.host)
	m.mu.Unlock()
	id, writer := ValueString(host["id"]), ValueString(host["writer"])
	base := "/v1/hosts/" + url.PathEscape(id)
	request := func(path, method string, v any, detail bool) error {
		m.mu.Lock()
		active := m.enabled && !m.closed && ValueBool(m.host["cloud_enabled"]) && ValueString(m.host["writer"]) == writer
		active = active && m.workerCtx == ctx && ctx.Err() == nil
		m.mu.Unlock()
		if !active {
			return context.Canceled
		}
		_, e := m.cloud(ctx, path, method, writer, v, detail)
		return e
	}
	for _, r := range ValueStrings(host["revoked"]) {
		if e := request(base+"/readers/"+url.PathEscape(r), "DELETE", nil, false); e != nil {
			m.failure(e)
			return
		}
		m.mu.Lock()
		revoked := []string{}
		for _, v := range ValueStrings(m.host["revoked"]) {
			if v != r {
				revoked = append(revoked, v)
			}
		}
		m.host["revoked"] = revoked
		e := m.saveLocked()
		m.mu.Unlock()
		if e != nil {
			m.failure(e)
			return
		}
	}
	for _, r := range ValueRows(host["readers"]) {
		readerID := ValueString(r["id"])
		m.mu.Lock()
		sent := m.cloudReaders[readerID]
		paired := false
		for _, current := range ValueRows(m.host["readers"]) {
			if ValueString(current["id"]) == readerID {
				paired = true
			}
		}
		m.mu.Unlock()
		if !sent && paired {
			if e := request(base+"/readers", "PUT", Row{"id": readerID, "secret": r["cloudSecret"]}, false); e != nil {
				m.failure(e)
				return
			}
			m.mu.Lock()
			for _, current := range ValueRows(m.host["readers"]) {
				if ValueString(current["id"]) == readerID {
					m.cloudReaders[readerID] = true
				}
			}
			m.mu.Unlock()
		}
	}
	m.mu.Lock()
	probe := time.Now().After(m.nextCapability)
	supports, kinds := m.cloudDetails, m.cloudRequestKinds
	m.mu.Unlock()
	var probeError error
	if probe {
		capability, e := m.cloud(ctx, base+"/capabilities", "GET", writer, nil, false)
		if e == nil {
			supports, kinds = false, false
			for _, c := range ValueStrings(capability["capabilities"]) {
				supports = supports || c == "request-details-v1"
				kinds = kinds || c == "request-kinds-v1"
			}
		} else {
			var failure *mobileHTTPError
			if errors.As(e, &failure) && (failure.status == 404 || failure.reason == "NOT_FOUND") {
				supports, kinds = false, false
			} else {
				probeError = e
			}
		}
		m.mu.Lock()
		m.cloudDetails, m.cloudRequestKinds = supports, kinds
		m.nextCapability = time.Now().Add(time.Hour)
		if probeError != nil {
			m.nextCapability = time.Now().Add(time.Minute)
		}
		m.mu.Unlock()
	}
	for _, name := range []string{"live", "recent", "trends"} {
		m.mu.Lock()
		envelope := CloneRow(m.datasets[name])
		revision := ValueInt(envelope["revision"])
		sent := m.sent[name]
		m.mu.Unlock()
		if revision > 0 && (sent != revision || name == "recent" && probe) {
			localDigest := ValueString(envelope["digest"])
			if name == "recent" {
				// Mac keeps a separate bounded projection for older Workers. Digest
				// and revision belong to the exact submitted payload, including kinds.
				var recent []Row
				if e := json.Unmarshal([]byte(ValueString(envelope["payload"])), &recent); e != nil {
					m.failure(e)
					return
				}
				if !kinds {
					filtered := []Row{}
					for _, r := range recent {
						if ValueString(r["kind"]) != "approval_review" && !strings.EqualFold(ValueString(r["model"]), "codex-auto-review") {
							filtered = append(filtered, r)
						}
					}
					recent = filtered
				}
				payload, e := mobileJSON(recent)
				if e != nil {
					m.failure(e)
					return
				}
				digest := HashString(string(payload))
				m.mu.Lock()
				previous := CloneRow(m.cloudRecent)
				m.mu.Unlock()
				if ValueString(previous["digest"]) == digest {
					m.mu.Lock()
					m.sent[name] = revision
					m.mu.Unlock()
					continue
				}
				envelope = Row{"dataset": "recent", "revision": max(revision, ValueInt(previous["revision"]), time.Now().UnixMilli()) + 1, "digest": digest, "payload": string(payload)}
			}
			if e := request(base+"/data/"+name, "PUT", envelope, false); e != nil {
				m.failure(e)
				return
			}
			m.mu.Lock()
			m.sent[name] = revision
			if name == "recent" {
				m.cloudRecent = CloneRow(envelope)
				if current := m.datasets[name]; current != nil && ValueInt(current["revision"]) <= ValueInt(envelope["revision"]) {
					current["revision"] = ValueInt(envelope["revision"]) + 1
					if ValueString(current["digest"]) == localDigest {
						m.sent[name] = ValueInt(current["revision"])
					}
				}
			}
			saveError := m.saveProjectionLocked()
			m.mu.Unlock()
			if saveError != nil {
				m.failure(saveError)
				return
			}
		}
	}
	m.mu.Lock()
	changes := map[string]Row{}
	generation := m.store.Generation()
	if m.cloudDetails {
		for id, d := range m.details {
			if d.Generation == generation && d.Expires > float64(time.Now().Unix()) && m.sentDetails[id] != ValueString(d.Envelope["digest"]) {
				changes[id] = CloneRow(d.Envelope)
			}
		}
	}
	m.mu.Unlock()
	sent := 0
	for detailID, envelope := range changes {
		if sent >= 4 {
			m.mu.Lock()
			m.uploadAgain = true
			m.mu.Unlock()
			break
		}
		if e := request(base+"/details/"+detailID, "PUT", envelope, true); e != nil {
			m.failure(e)
			return
		}
		m.mu.Lock()
		m.sentDetails[detailID] = ValueString(envelope["digest"])
		m.mu.Unlock()
		sent++
	}
	if probeError != nil {
		m.failure(probeError)
		return
	}
	m.mu.Lock()
	changed := m.status != "云端已同步" || m.lastError != ""
	m.status = "云端已同步"
	m.lastError = ""
	stampChanged := m.recordSyncLocked("cloud", "")
	m.mu.Unlock()
	if changed || stampChanged {
		m.notify()
	}
}
