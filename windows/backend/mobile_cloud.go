package backend

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

type mobileHTTPError struct {
	status   int
	reason   string
	revision int64
}

var mobileCloudClient = &http.Client{Timeout: 15 * time.Second,
	Transport:     &http.Transport{Proxy: systemAccountProxy, ResponseHeaderTimeout: 15 * time.Second, TLSHandshakeTimeout: 10 * time.Second, IdleConnTimeout: 30 * time.Second, MaxIdleConnsPerHost: 2},
	CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}

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
			if strings.Contains(path, "/images/") {
				limit = mobileImageEnvelopeLimit + 4096
			}
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
	response, e := mobileCloudClient.Do(request)
	if e != nil {
		return nil, &mobileNetworkError{cause: e}
	}
	defer response.Body.Close()
	raw, e := io.ReadAll(io.LimitReader(response.Body, mobileLimit+1))
	if e != nil {
		return nil, &mobileNetworkError{cause: e}
	}
	if len(raw) > mobileLimit {
		return nil, errors.New("云端响应无效或超过容量限制")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		failure, _ := DecodeRow(raw)
		return nil, &mobileHTTPError{status: response.StatusCode, reason: ValueString(failure["error"]), revision: ValueInt(failure["revision"])}
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
	if _, e := m.store.db.Exec("UPDATE mobile_image_outbox SET uploaded_digest='',referenced=0,retry_at=0"); e != nil {
		return e
	}
	if _, e := m.store.db.Exec("UPDATE mobile_detail_outbox SET sent_digest='',retry_at=0"); e != nil {
		return e
	}
	m.mu.Lock()
	m.host["cloud_enabled"] = true
	m.sent, m.sentDetails, m.cloudReaders = map[string]int64{}, map[string]string{}, map[string]bool{}
	m.cloudRecent = nil
	m.cloudRecentNeedsUpload = true
	e := m.saveLocked()
	if e == nil {
		m.status = "云端已启用"
		m.cloudError, m.cloudErrorTemporary = "", false
		m.uploadRetry, m.uploadFailures = time.Time{}, 0
		m.nextCapability = time.Time{}
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
	m.cloudImages, m.cloudDetails, m.cloudRequestKinds = false, false, false
	m.cloudDetailStamps = map[string]Row{}
	m.cloudError, m.cloudErrorTemporary = "", false
	m.uploadRetry, m.uploadFailures = time.Time{}, 0
	m.status = "云端密钥已移除"
	return m.saveLocked()
}
func (m *MobileHost) upload() {
	m.mu.Lock()
	if m.mock || !m.enabled || m.closed || m.host == nil || !ValueBool(m.host["cloud_enabled"]) || ValueString(m.host["writer"]) == "" || time.Now().Before(m.uploadRetry) {
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
			needed = needed || d.Generation == generation && d.Expires > float64(time.Now().Unix()) && m.sentDetails[id] != ValueString(d.Envelope["digest"]) && m.detailDeferred[id] <= time.Now().Unix()
		}
	}
	needed = needed || m.cloudImages && m.imagesPending || m.cloudDetails && m.parentsPending || m.cloudRecentNeedsUpload
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
	remoteSuccess := false
	request := func(path, method string, v any, detail bool) (Row, error) {
		m.mu.Lock()
		active := m.enabled && !m.closed && ValueBool(m.host["cloud_enabled"]) && ValueString(m.host["writer"]) == writer
		active = active && m.workerCtx == ctx && ctx.Err() == nil
		m.mu.Unlock()
		if !active {
			return nil, context.Canceled
		}
		result, e := m.cloud(ctx, path, method, writer, v, detail)
		if e == nil {
			remoteSuccess = true
		}
		return result, e
	}
	for _, r := range ValueStrings(host["revoked"]) {
		if _, e := request(base+"/readers/"+url.PathEscape(r), "DELETE", nil, false); e != nil {
			m.cloudFailure(e)
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
			m.cloudFailure(e)
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
			if _, e := request(base+"/readers", "PUT", Row{"id": readerID, "secret": r["cloudSecret"]}, false); e != nil {
				m.cloudFailure(e)
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
	supports, kinds, images := m.cloudDetails, m.cloudRequestKinds, m.cloudImages
	previousImageSupport := images
	m.mu.Unlock()
	var probeError error
	if probe {
		capability, e := request(base+"/capabilities", "GET", nil, false)
		if e == nil {
			supports, kinds, images = false, false, false
			for _, c := range ValueStrings(capability["capabilities"]) {
				supports = supports || c == "request-details-v1"
				kinds = kinds || c == "request-kinds-v1"
				images = images || c == "request-images-v1"
			}
		} else {
			var failure *mobileHTTPError
			if errors.As(e, &failure) && (failure.status == 404 || failure.reason == "NOT_FOUND") {
				supports, kinds, images = false, false, false
			} else {
				probeError = e
			}
		}
		m.mu.Lock()
		m.cloudDetails, m.cloudRequestKinds, m.cloudImages = supports, kinds, supports && images
		images = m.cloudImages
		if images != previousImageSupport {
			m.sentDetails = map[string]string{}
		}
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
					m.cloudFailure(e)
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
					m.cloudFailure(e)
					return
				}
				digest := HashString(string(payload))
				m.mu.Lock()
				previous := CloneRow(m.cloudRecent)
				mustUpload := m.cloudRecentNeedsUpload
				m.mu.Unlock()
				if ValueString(previous["digest"]) == digest && !mustUpload {
					m.mu.Lock()
					m.sent[name] = revision
					m.mu.Unlock()
					continue
				}
				envelope = Row{"dataset": "recent", "revision": max(revision, ValueInt(previous["revision"]), time.Now().UnixMilli()) + 1, "digest": digest, "payload": string(payload)}
			}
			ack, e := request(base+"/data/"+name, "PUT", envelope, false)
			if e != nil {
				m.cloudFailure(e)
				return
			}
			m.mu.Lock()
			m.sent[name] = revision
			envelope["revision"] = max(ValueInt(envelope["revision"]), ValueInt(ack["revision"]))
			if name == "recent" {
				m.cloudRecent = CloneRow(envelope)
				m.cloudRecentNeedsUpload = false
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
				m.cloudFailure(saveError)
				return
			}
		}
	}
	m.mu.Lock()
	changes := map[string]Row{}
	generation := m.store.Generation()
	if m.cloudDetails {
		for id, d := range m.details {
			if d.Generation == generation && d.Expires > float64(time.Now().Unix()) && m.sentDetails[id] != ValueString(d.Envelope["digest"]) && m.detailDeferred[id] <= time.Now().Unix() {
				changes[id] = CloneRow(d.Envelope)
			}
		}
	}
	m.mu.Unlock()
	if supports {
		parents, e := m.pendingImageDetails(ctx, images, 4)
		if e != nil {
			m.cloudFailure(e)
			return
		}
		for _, parent := range parents {
			id := ValueString(parent["id"])
			envelope := ValueRow(parent["envelope"])
			if ValueBool(parent["refresh"]) {
				envelope, e = m.getDetailWithForce(id, true, true)
				if e != nil {
					m.deferImageDetail(ctx, id, 60)
					continue
				}
			}
			// Reapply original deadlines before retrying a durable snapshot.
			value, e := DecodeRow([]byte(ValueString(envelope["payload"])))
			if e != nil || mobileDetailExpires(value) <= time.Now().Unix() {
				continue
			}
			_, changed, _ := mobileRetainValue("detail", value, time.Now().Unix())
			if changed {
				raw, _ := mobileJSON(value)
				envelope = Row{"dataset": envelope["dataset"], "revision": max(ValueInt(envelope["revision"]), time.Now().UnixMilli()) + 1, "digest": HashString(string(raw)), "payload": string(raw)}
				_, e = m.store.db.ExecContext(ctx, "UPDATE mobile_detail_outbox SET payload=?,bytes=?,revision=?,digest=? WHERE id=?", raw, len(raw), envelope["revision"], envelope["digest"], id)
				if e != nil {
					m.cloudFailure(e)
					return
				}
			}
			if previous := changes[id]; previous == nil || ValueInt(previous["revision"]) <= ValueInt(envelope["revision"]) {
				changes[id] = envelope
			}
		}
	}
	order := []string{}
	for id := range changes {
		order = append(order, id)
	}
	sort.Slice(order, func(i, j int) bool {
		return ValueInt(changes[order[i]]["revision"]) > ValueInt(changes[order[j]]["revision"])
	})
	for i, detailID := range order {
		if i >= 4 {
			m.mu.Lock()
			m.uploadAgain = true
			m.mu.Unlock()
			break
		}
		envelope := changes[detailID]
		value, e := DecodeRow([]byte(ValueString(envelope["payload"])))
		if e != nil {
			m.cloudFailure(e)
			return
		}
		if !images {
			delete(value, "images")
		}
		payload, e := mobileJSON(value)
		if e != nil {
			m.cloudFailure(e)
			return
		}
		m.mu.Lock()
		previous := CloneRow(m.cloudDetailStamps[detailID])
		m.mu.Unlock()
		revision := max(ValueInt(envelope["revision"]), ValueInt(previous["revision"])+1, time.Now().UnixMilli()+1)
		submitted := Row{"dataset": envelope["dataset"], "revision": revision, "digest": HashString(string(payload)), "payload": string(payload)}
		ack, e := request(base+"/details/"+detailID, "PUT", submitted, true)
		if e != nil {
			var response *mobileHTTPError
			if errors.As(e, &response) && response.reason == "DETAIL_CAPACITY" {
				m.deferImageDetail(ctx, detailID, 15*60)
				continue
			}
			if errors.As(e, &response) && response.status == 409 && response.revision > 0 {
				m.mu.Lock()
				m.cloudDetailStamps[detailID] = Row{"revision": response.revision, "expires": mobileDetailExpires(value)}
				if current := m.details[detailID]; current != nil {
					current.Envelope["revision"] = max(ValueInt(current.Envelope["revision"]), response.revision+1)
				}
				_ = m.saveProjectionLocked()
				m.mu.Unlock()
			}
			m.cloudFailure(e)
			return
		}
		if e := m.recordImageReferences(ctx, detailID, ValueRows(value["images"])); e != nil {
			m.cloudFailure(e)
			return
		}
		ackRevision := max(revision, ValueInt(ack["revision"]))
		ackDigest := firstString(ack["digest"], submitted["digest"])
		if e := m.acknowledgeImageDetail(ctx, detailID, ValueString(envelope["digest"]), images, ackRevision); e != nil {
			m.cloudFailure(e)
			return
		}
		m.mu.Lock()
		m.sentDetails[detailID] = ValueString(envelope["digest"])
		m.cloudDetailStamps[detailID] = Row{"revision": ackRevision, "expires": mobileDetailExpires(value)}
		if current := m.details[detailID]; current != nil {
			localFloor := ackRevision
			if ValueString(current.Envelope["digest"]) != ackDigest {
				localFloor++
			}
			current.Envelope["revision"] = max(ValueInt(current.Envelope["revision"]), localFloor)
		}
		for id, stamp := range m.cloudDetailStamps {
			if ValueInt(stamp["expires"]) <= time.Now().Unix() || len(m.cloudDetailStamps) > 64 && m.details[id] == nil {
				delete(m.cloudDetailStamps, id)
			}
		}
		e = m.saveProjectionLocked()
		m.mu.Unlock()
		if e != nil {
			m.cloudFailure(e)
			return
		}
	}
	if supports {
		parents, e := m.pendingImageDetails(ctx, images, 1)
		if e != nil {
			m.cloudFailure(e)
			return
		}
		m.mu.Lock()
		m.parentsPending = len(parents) > 0
		m.uploadAgain = m.uploadAgain || m.parentsPending
		m.mu.Unlock()
	}
	if images {
		batch, e := m.pendingImages(ctx, 3)
		if e != nil {
			m.cloudFailure(e)
			return
		}
		for _, image := range batch[:min(2, len(batch))] {
			envelope, e := m.imageEnvelope(ctx, image)
			if errors.Is(e, sql.ErrNoRows) || image.expires <= time.Now().Unix() {
				continue
			}
			if e == nil {
				_, e = request(base+"/images/"+image.id, "PUT", envelope, true)
			}
			if e != nil {
				var response *mobileHTTPError
				if errors.As(e, &response) && response.reason == "IMAGE_EXPIRED" {
					_, _ = m.store.db.ExecContext(ctx, "DELETE FROM mobile_image_outbox WHERE id=?", image.id)
					continue
				}
				_, _ = m.store.db.ExecContext(ctx, "UPDATE mobile_image_outbox SET retry_at=? WHERE id=?", time.Now().Add(time.Minute).Unix(), image.id)
				m.cloudFailure(e)
				return
			}
			if _, e := m.store.db.ExecContext(ctx, "UPDATE mobile_image_outbox SET uploaded_digest=?,retry_at=0 WHERE id=? AND digest=?", image.digest, image.id, image.digest); e != nil {
				m.cloudFailure(e)
				return
			}
		}
		m.mu.Lock()
		m.imagesPending = len(batch) > 2
		m.uploadAgain = m.uploadAgain || m.imagesPending
		m.mu.Unlock()
	}
	if probeError != nil {
		m.cloudFailure(probeError)
		return
	}
	m.mu.Lock()
	changed, stampChanged := false, false
	if remoteSuccess {
		changed = m.cloudSucceededLocked()
		stampChanged = m.recordSyncLocked("cloud", "")
	}
	m.mu.Unlock()
	if changed || stampChanged {
		m.notify()
	}
}
