package backend

// Only the two existing official read-only WHAM contracts are permitted.
import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf16"
)

type systemIdentity struct{ Account, Subject string }

func systemIdentityKey(i systemIdentity) string {
	b, _ := json.Marshal([]string{"codexio-week-v1", i.Account, i.Subject})
	return HashString(string(b))
}
func utf16SystemDecode(b []uint16) []rune { return utf16.Decode(b) }
func systemCredentials(root string) (string, systemIdentity, error) {
	root = systemExpandPath(root)
	if root == "" {
		root = systemCodexRoot()
	}
	resolved, e := filepath.Abs(root)
	if e != nil {
		return "", systemIdentity{}, errors.New("无法确认当前账户")
	}
	if p, e := filepath.EvalSymlinks(resolved); e == nil {
		resolved = p
	}
	value, e := systemReadJSON(filepath.Join(resolved, "auth.json"), 1<<20)
	if e != nil {
		secret := systemKeyring("cli|" + HashString(resolved)[:16])
		value, _ = DecodeRow(secret)
		if len(value) == 0 && len(secret)%2 == 0 {
			words := make([]uint16, len(secret)/2)
			for i := range words {
				words[i] = uint16(secret[2*i]) | uint16(secret[2*i+1])<<8
			}
			value, _ = DecodeRow([]byte(string(utf16.Decode(words))))
		}
	}
	tokens := ValueRow(value["tokens"])
	token, ok := tokens["access_token"].(string)
	if !ok || token == "" {
		return "", systemIdentity{}, errors.New("需要本机 Codex 的 ChatGPT 登录状态")
	}
	parts := strings.Split(token, ".")
	if len(parts) < 2 || len(parts[1]) > 1<<20 {
		return "", systemIdentity{}, errors.New("无法确认当前账户")
	}
	decoded, e := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if e != nil {
		return "", systemIdentity{}, errors.New("无法确认当前账户")
	}
	claims, e := DecodeRow(decoded)
	if e != nil {
		return "", systemIdentity{}, errors.New("无法确认当前账户")
	}
	auth := ValueRow(claims["https://api.openai.com/auth"])
	account, _ := tokens["account_id"].(string)
	if account == "" {
		account, _ = auth["chatgpt_account_id"].(string)
	}
	subject, ok := claims["sub"].(string)
	if !ok {
		subject, ok = auth["chatgpt_user_id"].(string)
	}
	if !ok || account == "" || len(account) > 512 || len(subject) > 512 {
		return "", systemIdentity{}, errors.New("无法确认当前账户")
	}
	return token, systemIdentity{account, subject}, nil
}

type systemAnalytics struct {
	opts              SystemOptions
	mu                sync.Mutex
	cached            Row
	generation        int64
	lastRead, retryAt time.Time
	flight            chan struct{}
	cancel            context.CancelFunc
	closed            bool
}

func systemNewAnalytics(o SystemOptions) *systemAnalytics {
	return &systemAnalytics{opts: o, cached: Row{"plan": Row{}, "chats": Row{}, "account_key": "", "status": "unavailable", "error": ""}}
}
func (a *systemAnalytics) snapshot() Row { a.mu.Lock(); defer a.mu.Unlock(); return CloneRow(a.cached) }
func (a *systemAnalytics) invalidate() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.generation++
	if a.cancel != nil {
		a.cancel()
	}
	a.cached = Row{"plan": Row{}, "chats": Row{}, "account_key": "", "status": "unavailable", "error": ""}
	a.lastRead = time.Time{}
}

// Candidate-set changes invalidate the request dependency, while Mac keeps
// valid same-account reports visible until their replacement arrives.
func (a *systemAnalytics) invalidateCandidates() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.generation++
	a.lastRead = time.Time{}
	if a.cancel != nil {
		a.cancel()
	}
}
func (a *systemAnalytics) close() {
	a.mu.Lock()
	a.closed = true
	if a.cancel != nil {
		a.cancel()
	}
	a.mu.Unlock()
}
func (a *systemAnalytics) root() string {
	return systemCodexRoot()
}
func (a *systemAnalytics) read(parent context.Context, threads []Row, force bool) (Row, error) {
	if a.opts.Mock {
		return Row{"plan": Row{}, "chats": Row{}, "account_key": "mock", "status": "unavailable", "error": "模拟模式不读取账户统计"}, nil
	}
	if len(threads) > 10000 {
		return nil, errors.New("账户统计最多读取 10000 个聊天，请缩小范围")
	}
	a.mu.Lock()
	if a.closed {
		a.mu.Unlock()
		return nil, errors.New("账户统计服务已关闭")
	}
	if a.flight != nil {
		flight := a.flight
		a.mu.Unlock()
		select {
		case <-flight:
			return a.snapshot(), nil
		case <-parent.Done():
			return nil, parent.Err()
		}
	}
	now := time.Now()
	if now.Before(a.retryAt) || (!force && now.Sub(a.lastRead) < time.Minute) {
		r := CloneRow(a.cached)
		a.mu.Unlock()
		return r, nil
	}
	generation := a.generation
	a.lastRead = now
	flight := make(chan struct{})
	a.flight = flight
	ctx, cancel := context.WithCancel(parent)
	a.cancel = cancel
	a.mu.Unlock()
	defer func() {
		cancel()
		a.mu.Lock()
		a.cancel = nil
		a.flight = nil
		close(flight)
		a.mu.Unlock()
	}()
	root := a.root()
	client := &systemAnalyticsClient{root: root, version: a.opts.Version}
	a.mu.Lock()
	client.retryAt = a.retryAt
	a.mu.Unlock()
	plan, planErr := client.request(ctx, "usage/plan_limit_history?days=7", nil)
	chats, chatErr := client.threadUsage(ctx, threads)
	_, finalIdentity, identityErr := systemCredentials(root)
	if client.identity.Account == "" || identityErr != nil || client.identity != finalIdentity {
		a.invalidate()
		return nil, errors.New("账户已变更，请重新刷新")
	}
	errs := []string{}
	if planErr != nil {
		errs = append(errs, planErr.Error())
	}
	if chatErr != nil && !systemContains(errs, chatErr.Error()) {
		errs = append(errs, chatErr.Error())
	}
	a.mu.Lock()
	a.retryAt = client.retryAt
	if a.closed || generation != a.generation || ctx.Err() != nil {
		a.mu.Unlock()
		return nil, errors.New("账户统计请求已失效")
	}
	key := systemIdentityKey(client.identity)
	previous := accountReportContent(a.cached)
	if ValueString(a.cached["account_key"]) != key {
		a.cached = Row{"plan": Row{}, "chats": Row{}}
	}
	if planErr == nil {
		a.cached["plan"] = plan
	}
	if chatErr == nil {
		a.cached["chats"] = chats
	}
	a.cached["account_key"] = key
	a.cached["error"] = strings.Join(errs, " · ")
	a.cached["updated_at"] = UTCStamp(time.Now())
	a.cached["status"] = "ok"
	if len(errs) > 0 {
		a.cached["status"] = "error"
	}
	r := CloneRow(a.cached)
	a.mu.Unlock()
	if previous != accountReportContent(r) && a.opts.Changed != nil {
		a.opts.Changed()
	}
	return r, nil
}
func accountReportContent(row Row) string {
	content := CloneRow(row)
	delete(content, "updated_at")
	return rowJSON(content)
}

type systemAnalyticsClient struct {
	root, version string
	identity      systemIdentity
	retryAt       time.Time
}

func (c *systemAnalyticsClient) request(ctx context.Context, route string, payload Row) (Row, error) {
	if route != "usage/plan_limit_history?days=7" && route != "usage/thread_usage/query_v2" {
		return nil, errors.New("Unsupported analytics route")
	}
	if time.Now().Before(c.retryAt) {
		return nil, errors.New("请求频繁，请稍后刷新")
	}
	token, identity, e := systemCredentials(c.root)
	if e != nil {
		return nil, e
	}
	if c.identity.Account != "" && c.identity != identity {
		return nil, errors.New("账户已变更，请重新刷新")
	}
	c.identity = identity
	var body io.Reader
	method := "GET"
	if payload != nil {
		b, e := json.Marshal(payload)
		if e != nil || len(b) > 2<<20 {
			return nil, errors.New("聊天统计请求过大")
		}
		body = bytes.NewReader(b)
		method = "POST"
	}
	reqCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	req, e := http.NewRequestWithContext(reqCtx, method, "https://chatgpt.com/backend-api/wham/"+route, body)
	if e != nil {
		return nil, errors.New("无法创建统计请求")
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("ChatGPT-Account-Id", identity.Account)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "Codexio/"+c.version)
	httpClient := &http.Client{Timeout: 15 * time.Second, CheckRedirect: func(next *http.Request, via []*http.Request) error {
		if len(via) >= 5 || next.URL.Scheme != "https" || next.URL.Host != "chatgpt.com" || next.URL.User != nil {
			return errors.New("服务重定向被拒绝")
		}
		return nil
	}}
	response, e := httpClient.Do(req)
	if e != nil {
		return nil, errors.New("服务暂不可用，请检查网络后重试")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		messages := map[int]string{401: "请重新登录 Codex", 403: "此账户没有这项数据的访问权限", 404: "当前账户尚未提供这项明细", 429: "请求频繁，请稍后刷新"}
		if response.StatusCode == 429 {
			delay := 60.0
			if n, e := strconv.ParseFloat(response.Header.Get("Retry-After"), 64); e == nil && n >= 0 && n <= 3600 {
				delay = max(60, n)
			}
			c.retryAt = time.Now().Add(time.Duration(delay) * time.Second)
		}
		if message := messages[response.StatusCode]; message != "" {
			return nil, errors.New(message)
		}
		return nil, errors.New("服务暂不可用")
	}
	data, e := io.ReadAll(io.LimitReader(response.Body, 16000001))
	if e != nil || len(data) > 16000000 {
		return nil, errors.New("统计数据无效或过大")
	}
	_, after, e := systemCredentials(c.root)
	if e != nil || after != identity {
		return nil, errors.New("账户或统计数据已变化，请重新刷新")
	}
	result, e := DecodeRow(data)
	if e != nil || len(result) == 0 {
		return nil, errors.New("服务返回了无效统计数据")
	}
	return result, nil
}
func (c *systemAnalyticsClient) threadUsage(ctx context.Context, threads []Row) (Row, error) {
	result := []Row{}
	batch := []Row{}
	ids := map[string]bool{}
	allIDs := map[string]bool{}
	var asOf any
	responseBytes := 0
	flush := func() error {
		if e := ctx.Err(); e != nil {
			return errors.New("已取消读取")
		}
		response, e := c.request(ctx, "usage/thread_usage/query_v2", Row{"threads": batch})
		if e != nil {
			return e
		}
		encoded, _ := json.Marshal(response)
		responseBytes += len(encoded)
		if responseBytes > 16000000 {
			return errors.New("聊天统计数据过大")
		}
		expected := map[string]bool{}
		for _, row := range batch {
			expected[ValueString(row["thread_id"])] = true
		}
		received := map[string]bool{}
		rawThreads := response["threads"]
		switch rawThreads.(type) {
		case []any, []Row:
		default:
			return errors.New("服务返回了无效统计数据")
		}
		rows, e := systemAnalyticsRows(rawThreads, 100)
		if e != nil {
			return e
		}
		for _, row := range rows {
			id := ValueString(row["thread_id"])
			if !expected[id] || received[id] {
				return errors.New("服务返回了无效统计数据")
			}
			received[id] = true
			groups, e := systemAnalyticsRows(row["groups"], 1000)
			if e != nil {
				return e
			}
			parts := append([]Row{row}, groups...)
			for _, part := range parts {
				for _, key := range []string{"weekly_limit_percent", "five_hour_limit_percent", "balance_usage_credits"} {
					if v := part[key]; v != nil {
						switch v.(type) {
						case json.Number, float64, float32, int, int64, string:
						default:
							return errors.New("服务返回了无效统计数据")
						}
						if n, ok := ValueFloat(v); !ok || n < 0 {
							return errors.New("服务返回了无效统计数据")
						}
					}
				}
			}
			result = append(result, row)
		}
		for _, row := range batch {
			id := ValueString(row["thread_id"])
			if !received[id] {
				result = append(result, Row{"thread_id": id, "data_status": "unavailable", "groups": []Row{}})
			}
		}
		asOf = response["data_as_of"]
		batch = nil
		ids = map[string]bool{}
		return nil
	}
	for _, raw := range threads {
		id, ok := raw["thread_id"].(string)
		desc := []string{}
		valid := ok && strings.TrimSpace(id) != "" && len(id) <= 512
		if v := raw["descendant_thread_ids"]; v != nil {
			switch x := v.(type) {
			case []string:
				desc = x
			case []any:
				for _, d := range x {
					if s, ok := d.(string); ok {
						desc = append(desc, s)
					} else {
						valid = false
					}
				}
			default:
				valid = false
			}
		}
		values := append([]string{id}, desc...)
		local := map[string]bool{}
		if len(values) > 1000 {
			valid = false
		}
		for _, v := range values {
			if strings.TrimSpace(v) == "" || len(v) > 512 || local[v] || allIDs[v] {
				valid = false
			}
			local[v] = true
		}
		if !valid {
			result = append(result, Row{"thread_id": id, "data_status": "unavailable", "groups": []Row{}})
			continue
		}
		if len(batch) > 0 && (len(batch) >= 100 || len(ids)+len(values) > 1000) {
			if e := flush(); e != nil {
				return nil, e
			}
		}
		batch = append(batch, Row{"thread_id": id, "created_at": raw["created_at"], "descendant_thread_ids": desc})
		for _, v := range values {
			ids[v] = true
			allIDs[v] = true
		}
	}
	if len(batch) > 0 {
		if e := flush(); e != nil {
			return nil, e
		}
	}
	return Row{"threads": result, "data_as_of": asOf}, nil
}
func systemAnalyticsRows(v any, limit int) ([]Row, error) {
	if v == nil {
		return []Row{}, nil
	}
	r := []Row{}
	switch values := v.(type) {
	case []Row:
		r = values
	case []any:
		if len(values) > limit {
			return nil, errors.New("服务返回了过大的统计数据")
		}
		for _, raw := range values {
			row := ValueRow(raw)
			if len(row) == 0 {
				return nil, errors.New("服务返回了无效统计数据")
			}
			r = append(r, row)
		}
	default:
		return nil, errors.New("服务返回了无效统计数据")
	}
	if len(r) > limit {
		return nil, errors.New("服务返回了过大的统计数据")
	}
	return r, nil
}

// Kept private: URLs, status errors and credentials are never interpolated into
// public errors, and authorization cannot follow a cross-origin redirect.
