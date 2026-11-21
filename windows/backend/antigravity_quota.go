package backend

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

type antigravityEndpoint struct {
	Port int    `json:"port"`
	CSRF string `json:"csrf"`
}

// This reader uses only the already-running desktop App's local language server.
// It never changes model-request routing or obtains account OAuth credentials.
type AntigravityQuota struct {
	mu      sync.RWMutex
	state   Row
	wake    chan struct{}
	config  func() Row
	changed func()
	sample  func(Row)
}

func NewAntigravityQuota(config func() Row, changed func()) *AntigravityQuota {
	return &AntigravityQuota{state: Row{"status": "reading", "groups": []Row{}}, wake: make(chan struct{}, 1), config: config, changed: changed}
}

func (q *AntigravityQuota) Refresh() {
	select {
	case q.wake <- struct{}{}:
	default:
	}
}

func (q *AntigravityQuota) Snapshot() Row {
	q.mu.RLock()
	result := CloneRow(q.state)
	q.mu.RUnlock()
	updated, valid := ParseStamp(result["updated_at"])
	stale := !valid || time.Since(updated) > time.Duration(max(120, ValueInt(q.config()["refresh_interval_seconds"])*2))*time.Second || time.Since(updated) < 0
	for _, key := range []string{"primary", "secondary"} {
		bucket := ValueRow(result[key])
		if len(bucket) > 0 {
			if reset, ok := ParseStamp(bucket["resets_at"]); ok && !time.Now().Before(reset) {
				bucket["remaining_percent"], bucket["used_percent"] = nil, nil
				stale = true
			}
		}
	}
	if stale && result["status"] == "ok" {
		result["status"] = "stale"
	}
	result["client_kind"], result["retained"] = antigravityClient, valid && result["status"] != "ok"
	return result
}

func (q *AntigravityQuota) Run(ctx context.Context) {
	timer := time.NewTimer(time.Millisecond)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-q.wake:
		case <-timer.C:
		}
		readCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
		value, err := readAntigravityQuota(readCtx)
		cancel()
		if ctx.Err() != nil {
			return
		}
		q.mu.Lock()
		old := CloneRow(q.state)
		if err != nil {
			q.state["status"], q.state["error"] = "unavailable", "打开 Antigravity App 后可读取额度"
			q.state["account_key"] = ""
		} else {
			groups := ValueRows(value["groups"])
			q.state = Row{"status": "ok", "groups": groups, "updated_at": UTCStamp(time.Now()), "primary": nil, "secondary": nil, "account_key": value["account_key"], "plan_type": value["plan_type"], "limit_id": antigravityClient}
			for _, group := range groups {
				for _, bucket := range ValueRows(group["buckets"]) {
					key := "primary"
					if bucket["id"] == "gemini-weekly" {
						key = "secondary"
					}
					q.state[key] = bucket
				}
			}
		}
		before, after := CloneRow(old), CloneRow(q.state)
		delete(before, "updated_at")
		delete(after, "updated_at")
		changed := dataJSON(before) != dataJSON(after)
		sampled := CloneRow(q.state)
		q.mu.Unlock()
		if q.sample != nil {
			q.sample(sampled)
		}
		if changed && q.changed != nil {
			q.changed()
		}
		interval := ValueInt(q.config()["refresh_interval_seconds"])
		if interval < 30 {
			interval = 60
		}
		if err != nil && len(ValueRows(old["groups"])) == 0 {
			interval = 30
		}
		timer.Reset(time.Duration(interval) * time.Second)
	}
}

func readAntigravityQuota(ctx context.Context) (Row, error) {
	endpoints, err := antigravityAppEndpoints(ctx)
	if err != nil || len(endpoints) == 0 {
		return nil, errors.New("Antigravity App 未运行")
	}
	// A self-signed certificate is accepted only on a verified App-owned
	// loopback port. The process session's CSRF token authenticates this RPC.
	transport := &http.Transport{Proxy: nil, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: true}, DialContext: (&net.Dialer{Timeout: 2 * time.Second}).DialContext, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	for _, endpoint := range endpoints {
		if endpoint.Port < 1 || endpoint.Port > 65535 || endpoint.CSRF == "" {
			continue
		}
		before, _ := antigravityAccountIdentity(ctx, client, endpoint)
		root, err := antigravityRPC(ctx, client, endpoint, "RetrieveUserQuotaSummary", Row{"forceRefresh": false})
		if err != nil {
			continue
		}
		if code, exists := root["code"]; exists && code != nil && ValueString(code) != "0" && ValueString(code) != "OK" && ValueString(code) != "SUCCESS" {
			continue
		}
		value := root
		if nested, ok := root["response"].(map[string]any); ok {
			value = nested
		} else if nested, ok := root["summary"].(map[string]any); ok {
			value = nested
		}
		groups := []Row{}
		for _, group := range ValueRows(value["groups"]) {
			buckets := []Row{}
			for _, raw := range ValueRows(group["buckets"]) {
				id := firstString(raw["bucketId"], raw["id"])
				if id != "gemini-5h" && id != "gemini-weekly" {
					continue
				}
				fraction := raw["remainingFraction"]
				if fraction == nil {
					remaining := ValueRow(raw["remaining"])
					fraction = remaining["remainingFraction"]
					if fraction == nil && remaining["case"] == "remainingFraction" {
						fraction = remaining["value"]
					}
				}
				var percent any
				_, numeric := fraction.(json.Number)
				if n, ok := ValueFloat(fraction); ok && numeric && !math.IsNaN(n) && !math.IsInf(n, 0) && n >= 0 && n <= 1 && !ValueBool(raw["disabled"]) {
					percent = n * 100
				}
				bucket := Row{"id": clip(id, 240), "name": clip(firstString(raw["displayName"], raw["name"], id), 240), "remaining_percent": percent, "used_percent": nil, "resets_at": nil, "description": clip(dataString(raw, "description"), 600), "window": clip(dataString(raw, "window"), 120), "disabled": ValueBool(raw["disabled"])}
				if remaining, valid := ValueFloat(percent); valid {
					bucket["used_percent"] = 100 - remaining
				}
				if date, err := time.Parse(time.RFC3339Nano, dataString(raw, "resetTime")); err == nil {
					bucket["resets_at"] = UTCStamp(date)
				}
				buckets = append(buckets, bucket)
			}
			if len(buckets) > 0 {
				groups = append(groups, Row{"name": clip(firstString(group["displayName"], group["name"]), 240), "description": clip(dataString(group, "description"), 600), "buckets": buckets})
			}
		}
		if len(groups) > 0 {
			result := Row{"groups": groups, "account_key": "", "plan_type": ""}
			after, _ := antigravityAccountIdentity(ctx, client, endpoint)
			if dataString(before, "account_key") != "" && before["account_key"] == after["account_key"] && before["plan_type"] == after["plan_type"] {
				result["account_key"], result["plan_type"] = after["account_key"], after["plan_type"]
			}
			return result, nil
		}
	}
	return nil, errors.New("Antigravity App 额度暂不可用")
}

func antigravityRPC(ctx context.Context, client *http.Client, endpoint antigravityEndpoint, method string, body Row) (Row, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	url := "https://127.0.0.1:" + strconv.Itoa(endpoint.Port) + "/exa.language_server_pb.LanguageServerService/" + method
	request, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Connect-Protocol-Version", "1")
	request.Header.Set("X-Codeium-Csrf-Token", endpoint.CSRF)
	response, err := client.Do(request)
	if err != nil {
		return nil, errors.New("Antigravity App 本地读取暂不可用")
	}
	defer response.Body.Close()
	data, err = io.ReadAll(io.LimitReader(response.Body, 1<<20+1))
	if err != nil || response.StatusCode != http.StatusOK || len(data) > 1<<20 {
		return nil, errors.New("Antigravity App 本地响应不可用")
	}
	root, err := DecodeRow(data)
	if err != nil {
		return nil, err
	}
	if code, exists := root["code"]; exists && code != nil && ValueString(code) != "0" && ValueString(code) != "OK" && ValueString(code) != "SUCCESS" {
		return nil, errors.New("Antigravity App 本地响应暂不可用")
	}
	return root, nil
}

func antigravityAccountIdentity(ctx context.Context, client *http.Client, endpoint antigravityEndpoint) (Row, error) {
	root, err := antigravityRPC(ctx, client, endpoint, "GetUserStatus", Row{"metadata": Row{"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}})
	if err != nil {
		return nil, err
	}
	status := ValueRow(root["userStatus"])
	email := strings.ToLower(strings.TrimSpace(dataString(status, "email")))
	if email == "" {
		return Row{}, nil
	}
	plan := firstString(ValueRow(status["userTier"])["name"], ValueRow(ValueRow(status["planStatus"])["planInfo"])["planName"])
	return Row{"account_key": "antigravity-account:" + HashString(email), "plan_type": plan}, nil
}
