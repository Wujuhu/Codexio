package backend

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/pelletier/go-toml/v2"
)

type systemResetCommand struct {
	id, account string
	reply       chan systemRPCResult
}
type QuotaService struct {
	opts           SystemOptions
	mu             sync.RWMutex
	state          Row
	raw            Row
	ctx            context.Context
	cancel         context.CancelFunc
	done           chan struct{}
	wake           chan struct{}
	reset          chan systemResetCommand
	client         *systemRPC
	clientMu       sync.Mutex
	startOnce      sync.Once
	closeOnce      sync.Once
	notifications  Row
	accountChanged bool
	failures       int
	identity       systemIdentity
	root, path     string
	reports        *systemAnalytics
}

func NewQuota(o SystemOptions) *QuotaService {
	return &QuotaService{opts: o, state: Row{"status": "reading", "applicable": false, "account": Row{}, "primary": nil, "secondary": nil, "credits": nil, "error": "", "updated_at": nil}, wake: make(chan struct{}, 1), reset: make(chan systemResetCommand, 1), done: make(chan struct{}), reports: systemNewAnalytics(o)}
}
func (q *QuotaService) Start(ctx context.Context) {
	q.startOnce.Do(func() { q.mu.Lock(); q.ctx, q.cancel = context.WithCancel(ctx); q.mu.Unlock(); go q.run() })
}
func (q *QuotaService) Snapshot() Row {
	q.mu.RLock()
	r := CloneRow(q.state)
	q.mu.RUnlock()
	r = systemQuotaProjection(r, q.interval())
	r["reports"] = q.reports.snapshot()
	return r
}
func systemQuotaProjection(r Row, interval int) Row {
	if t, ok := ParseStamp(r["updated_at"]); ok && ValueString(r["status"]) == "ok" {
		if age := time.Since(t); age > time.Duration(max(120, interval*2))*time.Second || age < 0 {
			r["status"] = "stale"
		}
	}
	for _, key := range []string{"primary", "secondary"} {
		w := ValueRow(r[key])
		if t, ok := ParseStamp(w["resets_at"]); ok && !time.Now().Before(t) {
			r[key] = nil
			if ValueString(r["status"]) == "ok" {
				r["status"] = "stale"
			}
		}
	}
	for _, c := range ValueRows(r["reset_credit_details"]) {
		if t, ok := ParseStamp(c["expires_at"]); ok && !time.Now().Before(t) && ValueString(c["status"]) == "available" {
			c["status"] = "expired"
			if ValueString(r["status"]) == "ok" {
				r["status"] = "stale"
			}
		}
	}
	if credits := ValueRow(r["credits"]); len(credits) > 0 {
		credits["details"] = r["reset_credit_details"]
		r["credits"] = credits
	}
	return r
}
func (q *QuotaService) Refresh() {
	select {
	case q.wake <- struct{}{}:
	default:
	}
}
func (q *QuotaService) Close() error {
	q.closeOnce.Do(func() {
		q.mu.RLock()
		cancel := q.cancel
		q.mu.RUnlock()
		if cancel != nil {
			cancel()
		}
		q.clientMu.Lock()
		if q.client != nil {
			q.client.close()
		}
		q.clientMu.Unlock()
		q.reports.close()
	})
	q.mu.RLock()
	started := q.ctx != nil
	q.mu.RUnlock()
	if started {
		select {
		case <-q.done:
		case <-time.After(8 * time.Second):
			return errors.New("Codex 子进程退出超时")
		}
	}
	return nil
}
func (q *QuotaService) config() Row {
	if q.opts.Config != nil {
		return q.opts.Config()
	}
	return systemDefaultConfig()
}
func (q *QuotaService) interval() int {
	n := int(ValueInt(q.config()["refresh_interval_seconds"]))
	if n != 30 && n != 60 && n != 300 {
		return 60
	}
	return n
}
func (q *QuotaService) publish(r Row) {
	r = systemQuotaProjection(r, q.interval())
	q.mu.Lock()
	old := CloneRow(q.state)
	q.state = r
	q.mu.Unlock()
	if q.opts.Sample != nil {
		q.opts.Sample(CloneRow(r))
	}
	delete(old, "updated_at")
	next := CloneRow(r)
	delete(next, "updated_at")
	a, _ := json.Marshal(old)
	b, _ := json.Marshal(next)
	if string(a) != string(b) && q.opts.Changed != nil {
		q.opts.Changed()
	}
}
func (q *QuotaService) run() {
	defer close(q.done)
	defer func() {
		q.clientMu.Lock()
		if q.client != nil {
			q.client.close()
			q.client = nil
		}
		q.clientMu.Unlock()
	}()
	for {
		if q.ctx.Err() != nil {
			return
		}
		err := q.cycle()
		if err != nil {
			q.failures++
			q.mu.RLock()
			r := CloneRow(q.state)
			q.mu.RUnlock()
			r["status"] = "error"
			r["error"] = err.Error()
			q.publish(r)
			q.clientMu.Lock()
			if q.client != nil {
				q.client.close()
				q.client = nil
			}
			q.clientMu.Unlock()
		} else {
			q.failures = 0
		}
		delay := time.Duration(max(1, q.interval())) * time.Second
		if q.failures > 0 {
			delay = time.Duration(min(300, 1<<min(q.failures, 8))) * time.Second
		}
		// Wake at a business deadline instead of leaving expired windows/credits
		// visible until the normal polling interval has elapsed.
		q.mu.RLock()
		current := CloneRow(q.state)
		q.mu.RUnlock()
		deadlines := []any{ValueRow(current["primary"])["resets_at"], ValueRow(current["secondary"])["resets_at"]}
		for _, c := range ValueRows(current["reset_credit_details"]) {
			deadlines = append(deadlines, c["expires_at"])
		}
		for _, value := range deadlines {
			if deadline, ok := ParseStamp(value); ok {
				remaining := time.Until(deadline)
				if remaining > 0 && remaining < delay {
					delay = remaining
				}
			}
		}
		t := time.NewTimer(delay)
		select {
		case <-q.ctx.Done():
			t.Stop()
			return
		case <-q.wake:
			t.Stop()
		case command := <-q.reset:
			t.Stop()
			r, e := q.consume(command)
			command.reply <- systemRPCResult{r, e}
		case <-t.C:
		}
	}
}
func (q *QuotaService) cycle() error {
	if q.opts.Mock {
		now := time.Now()
		q.publish(Row{"status": "ok", "applicable": true, "auth_mode": "mock", "account": Row{"type": "chatgpt", "email": "mock@example.invalid"}, "account_key": "mock", "plan_type": "plus", "primary": Row{"used_percent": 38, "remaining_percent": 62, "window_duration_mins": 300, "resets_at": now.Add(time.Hour).Unix()}, "secondary": Row{"used_percent": 24, "remaining_percent": 76, "window_duration_mins": 10080, "resets_at": now.Add(72 * time.Hour).Unix()}, "credits": nil, "reset_credits": nil, "reset_credit_details": nil, "error": "", "updated_at": UTCStamp(now)})
		return nil
	}
	config := q.config()
	root := systemCodexRoot()
	root, _ = filepath.Abs(root)
	path := ValueString(config["codex_path"])
	if q.root != root || q.path != path {
		q.clientMu.Lock()
		if q.client != nil {
			q.client.close()
			q.client = nil
		}
		q.clientMu.Unlock()
		q.root = root
		q.path = path
		q.identity = systemIdentity{}
		q.raw = nil
		q.reports.invalidate()
		q.publish(Row{"status": "reading", "applicable": false, "account": Row{}, "primary": nil, "secondary": nil, "credits": nil, "error": "", "updated_at": nil})
	}
	_, currentIdentity, _ := systemCredentials(root)
	if q.identity != currentIdentity {
		q.identity = currentIdentity
		q.raw = nil
		q.reports.invalidate()
		q.publish(Row{"status": "reading", "applicable": false, "account": Row{}, "primary": nil, "secondary": nil, "credits": nil, "error": "", "updated_at": nil})
	}
	q.clientMu.Lock()
	client := q.client
	q.clientMu.Unlock()
	if client == nil || !client.running() {
		excluded := map[string]bool{}
		var e error
		for i := 0; i < 4; i++ {
			exe, err := systemDiscoverCLI(q.ctx, path, excluded)
			if err != nil {
				return err
			}
			client, e = systemNewRPC(q.ctx, exe, q.opts.Version, root, q.notification)
			if e == nil {
				break
			}
			excluded[strings.ToLower(exe)] = true
		}
		if e != nil {
			return e
		}
		q.clientMu.Lock()
		q.client = client
		q.clientMu.Unlock()
	}
	ctx, cancel := context.WithTimeout(q.ctx, 20*time.Second)
	account, e := client.request(ctx, "account/read", Row{"refreshToken": false})
	cancel()
	if e != nil {
		return e
	}
	applicable, mode, e := systemAccountMode(root, account)
	if e != nil {
		return e
	}
	_, identity, _ := systemCredentials(root)
	q.mu.Lock()
	changed := q.accountChanged
	q.accountChanged = false
	notification := q.notifications
	q.notifications = nil
	q.mu.Unlock()
	if changed || q.identity != identity {
		q.identity = identity
		q.raw = nil
		q.reports.invalidate()
	}
	publicAccount := Row{}
	for _, k := range []string{"type", "email", "planType", "plan_type"} {
		if v, ok := ValueRow(account["account"])[k]; ok {
			publicAccount[k] = v
		}
	}
	if !applicable {
		q.raw = nil
		q.reports.invalidate()
		q.publish(Row{"status": "not_applicable", "applicable": false, "auth_mode": mode, "account": publicAccount, "primary": nil, "secondary": nil, "credits": nil, "error": "", "updated_at": nil})
		return nil
	}
	before := identity
	if q.raw == nil && identity.Account != "" {
		cache, e := systemReadJSON(filepath.Join(q.opts.Directory, "quota_cache.json"), 4<<20)
		if e == nil && ValueString(cache["account_key"]) == systemIdentityKey(identity) {
			if cached, e := systemParseQuota(cache); e == nil {
				cached["status"] = "stale"
				cached["error"] = ""
				cached["updated_at"] = cache["fetched_at"]
				cached["applicable"] = true
				cached["auth_mode"] = mode
				cached["account"] = publicAccount
				cached["account_key"] = cache["account_key"]
				q.publish(cached)
				q.raw = cache
			}
		}
	}
	payload := Row{}
	if len(notification) > 0 && !changed {
		payload = notification
		if len(ValueRow(payload["rateLimits"])) == 0 && len(ValueRow(payload["rate_limits"])) == 0 && len(ValueRow(payload["rateLimitsByLimitId"])) == 0 {
			payload = Row{"rateLimits": notification}
		}
		if q.raw != nil {
			payload = systemMergeQuota(q.raw, payload)
		}
	} else {
		ctx, cancel = context.WithTimeout(q.ctx, 20*time.Second)
		payload, e = client.request(ctx, "account/rateLimits/read", Row{"excludeResetCreditDetails": false})
		cancel()
		if e != nil {
			return e
		}
	}
	_, after, _ := systemCredentials(root)
	if before != after {
		q.identity = after
		q.raw = nil
		q.reports.invalidate()
		return errors.New("账户已变更，请重新刷新")
	}
	parsed, e := systemParseQuota(payload)
	if e != nil {
		return e
	}
	key := systemAccountKey(payload, before, after)
	q.mu.RLock()
	previousKey := ValueString(q.state["account_key"])
	q.mu.RUnlock()
	if q.raw != nil && previousKey != key {
		q.reports.invalidate()
	}
	q.raw = payload
	parsed["status"] = "ok"
	parsed["applicable"] = true
	parsed["auth_mode"] = mode
	parsed["account"] = publicAccount
	parsed["account_key"] = key
	parsed["error"] = ""
	parsed["updated_at"] = UTCStamp(time.Now())
	if key != "" {
		operations, _ := systemReadJSON(filepath.Join(q.opts.Directory, "reset_operations.json"), 1<<20)
		if operation := ValueRow(operations[key]); len(operation) > 0 {
			parsed["reset_pending"] = Row{"credit_id": operation["creditId"], "credit": operation["credit"], "pending": true}
		}
	}
	q.publish(parsed)
	cache := CloneRow(payload)
	cache["fetched_at"] = parsed["updated_at"]
	cache["account_key"] = key
	_ = WriteJSON(filepath.Join(q.opts.Directory, "quota_cache.json"), cache)
	return nil
}
func (q *QuotaService) notification(method string, params Row) {
	q.mu.Lock()
	switch method {
	case "account/updated":
		q.accountChanged = true
		q.notifications = nil
	case "account/rateLimits/updated":
		q.notifications = CloneRow(params)
	default:
		q.mu.Unlock()
		return
	}
	q.mu.Unlock()
	q.Refresh()
}
func systemAccountKey(payload Row, before, after systemIdentity) string {
	if before != after || after.Account == "" {
		return ""
	}
	remote := ValueString(systemFirst(payload, "accountId", "account_id"))
	if remote != "" && remote != after.Account {
		return ""
	}
	return systemIdentityKey(after)
}
func systemAccountMode(root string, result Row) (bool, string, error) {
	config := Row{}
	p := filepath.Join(root, "config.toml")
	if s, e := os.Stat(p); e == nil {
		if s.Size() > 2<<20 {
			return false, "unknown", errors.New("Codex 配置过大")
		}
		b, e := os.ReadFile(p)
		if e != nil {
			return false, "unknown", errors.New("无法读取 Codex 配置")
		}
		if e = toml.Unmarshal(b, &config); e != nil {
			return false, "unknown", errors.New("Codex 配置不是有效 TOML")
		}
	} else if !os.IsNotExist(e) {
		return false, "unknown", errors.New("无法读取 Codex 配置")
	}
	profile := ValueString(config["profile"])
	if profile != "" {
		if filepath.Base(profile) != profile || strings.ContainsAny(profile, `/\`) {
			return false, "unknown", errors.New("当前配置方案无效")
		}
		overlay := Row{}
		separate := filepath.Join(root, profile+".config.toml")
		if s, e := os.Stat(separate); e == nil {
			if s.Size() > 2<<20 {
				return false, "unknown", errors.New("Codex 配置过大")
			}
			b, e := os.ReadFile(separate)
			if e != nil || toml.Unmarshal(b, &overlay) != nil {
				return false, "unknown", errors.New("无法读取当前配置方案")
			}
		} else {
			overlay = ValueRow(ValueRow(config["profiles"])[profile])
			if len(overlay) == 0 {
				return false, "unknown", errors.New("当前配置方案不存在")
			}
		}
		config = systemDeepMerge(config, overlay)
	}
	provider := ValueString(config["model_provider"])
	if provider == "" {
		provider = "openai"
	}
	forced := strings.ToLower(ValueString(config["forced_login_method"]))
	requires := true
	if provider != "openai" {
		v := ValueRow(ValueRow(config["model_providers"])[provider])
		if len(v) == 0 {
			return false, "unknown", errors.New("当前模型供应商未定义")
		}
		requires = ValueBool(v["requires_openai_auth"])
	}
	if v, ok := systemFirst(result, "requiresOpenaiAuth", "requires_openai_auth").(bool); ok {
		requires = v
	}
	accountType := strings.ToLower(strings.NewReplacer("_", "", "-", "").Replace(ValueString(ValueRow(result["account"])["type"])))
	if provider != "openai" {
		return false, "provider:" + provider, nil
	}
	if forced == "api" || !requires {
		return false, "api", nil
	}
	if systemContains([]string{"apikey", "amazonbedrock", "bedrockapikey"}, accountType) {
		return false, accountType, nil
	}
	if accountType == "" {
		return true, "unknown", nil
	}
	return systemContains([]string{"chatgpt", "chatgptauthtokens", "agentidentity", "personalaccesstoken"}, accountType), accountType, nil
}
func systemDeepMerge(base, overlay Row) Row {
	r := CloneRow(base)
	for k, v := range overlay {
		if len(ValueRow(v)) > 0 && len(ValueRow(r[k])) > 0 {
			r[k] = systemDeepMerge(ValueRow(r[k]), ValueRow(v))
		} else {
			r[k] = v
		}
	}
	return r
}
func systemFirst(r Row, keys ...string) any {
	for _, k := range keys {
		if v, ok := r[k]; ok {
			return v
		}
	}
	return nil
}
func systemParseWindow(v any) Row {
	p := ValueRow(v)
	used, ok := ValueFloat(systemFirst(p, "usedPercent", "used_percent"))
	if !ok {
		return nil
	}
	used = math.RoundToEven(max(0, min(100, used)))
	r := Row{"used_percent": used, "remaining_percent": 100 - used, "window_duration_mins": nil, "resets_at": nil}
	if n, ok := ValueFloat(systemFirst(p, "windowDurationMins", "window_duration_mins", "window_minutes")); ok {
		r["window_duration_mins"] = int64(math.RoundToEven(n))
	}
	if n, ok := ValueFloat(systemFirst(p, "resetsAt", "resets_at")); ok {
		r["resets_at"] = int64(math.RoundToEven(n))
	}
	return r
}
func systemParseQuota(payload Row) (Row, error) {
	buckets := ValueRow(systemFirst(payload, "rateLimitsByLimitId", "rate_limits_by_limit_id"))
	selected := ValueRow(systemFirst(payload, "rateLimits", "rate_limits"))
	if len(buckets) > 0 {
		if v := ValueRow(buckets["codex"]); len(v) > 0 {
			selected = v
		} else if v := ValueRow(buckets[ValueString(systemFirst(selected, "limitId", "limit_id"))]); len(v) > 0 {
			selected = v
		} else {
			for _, v := range buckets {
				if m := ValueRow(v); len(m) > 0 {
					selected = m
					break
				}
			}
		}
	}
	if len(selected) == 0 {
		return nil, errors.New("额度结果缺少 rateLimits")
	}
	r := Row{"primary": nil, "secondary": nil, "plan_type": systemFirst(selected, "planType", "plan_type"), "limit_id": systemFirst(selected, "limitId", "limit_id"), "credits": nil, "reset_credits": nil, "reset_credit_details": nil}
	unmatched := []Row{}
	for _, k := range []string{"primary", "secondary"} {
		w := systemParseWindow(selected[k])
		if len(w) == 0 {
			continue
		}
		duration := ValueInt(w["window_duration_mins"])
		if duration >= 298 && duration <= 302 && r["primary"] == nil {
			r["primary"] = w
		} else if duration >= 10070 && duration <= 10090 && r["secondary"] == nil {
			r["secondary"] = w
		} else {
			unmatched = append(unmatched, w)
		}
	}
	if r["secondary"] == nil {
		for _, w := range unmatched {
			if w["window_duration_mins"] == nil || ValueInt(w["window_duration_mins"]) >= 1440 {
				r["secondary"] = w
				break
			}
		}
	}
	creditSource := payload
	if _, ok := payload["rateLimitResetCredits"]; !ok {
		if _, ok := payload["rate_limit_reset_credits"]; !ok {
			creditSource = selected
		}
	}
	summary := ValueRow(systemFirst(creditSource, "rateLimitResetCredits", "rate_limit_reset_credits"))
	if n, ok := ValueFloat(systemFirst(summary, "availableCount", "available_count")); ok && n >= 0 && math.Trunc(n) == n {
		r["reset_credits"] = n
	}
	details := []Row{}
	seen := map[string]bool{}
	for i, item := range ValueRows(summary["credits"]) {
		if i >= 1000 {
			break
		}
		id := ValueString(item["id"])
		if id == "" || seen[id] {
			continue
		}
		seen[id] = true
		expires, known := item["expiresAt"]
		if !known {
			expires, known = item["expires_at"]
		}
		if expires != nil {
			_, known = ValueFloat(expires)
		}
		status := ValueString(item["status"])
		if !systemContains([]string{"available", "redeeming", "redeemed"}, status) {
			status = "unknown"
		}
		details = append(details, Row{"id": id, "status": status, "reset_type": systemFirst(item, "resetType", "reset_type"), "expires_at": expires, "expiry_known": known, "granted_at": systemFirst(item, "grantedAt", "granted_at"), "title": item["title"], "description": item["description"]})
	}
	if _, ok := summary["credits"]; ok {
		r["reset_credit_details"] = details
	}
	r["credits"] = Row{"available_count": r["reset_credits"], "details": r["reset_credit_details"]}
	if len(buckets) > 0 {
		public := Row{}
		for k, v := range buckets {
			public[k] = Row{"primary": systemParseWindow(ValueRow(v)["primary"]), "secondary": systemParseWindow(ValueRow(v)["secondary"]), "plan_type": systemFirst(ValueRow(v), "planType", "plan_type")}
		}
		r["rate_limits_by_limit_id"] = public
	}
	return r, nil
}
func systemMergeQuota(old, incoming Row) Row {
	base := ValueRow(systemFirst(old, "rateLimits", "rate_limits"))
	update := ValueRow(systemFirst(incoming, "rateLimits", "rate_limits"))
	oldID := ValueString(systemFirst(base, "limitId", "limit_id"))
	newID := ValueString(systemFirst(update, "limitId", "limit_id"))
	merge := func(a, b Row) Row {
		if aID, bID := ValueString(systemFirst(a, "limitId", "limit_id")), ValueString(systemFirst(b, "limitId", "limit_id")); aID != "" && bID != "" && aID != bID {
			return CloneRow(b)
		}
		result := CloneRow(a)
		for k, v := range b {
			if v != nil {
				result[k] = v
			}
		}
		return result
	}
	buckets := CloneRow(ValueRow(systemFirst(old, "rateLimitsByLimitId", "rate_limits_by_limit_id")))
	updates := ValueRow(systemFirst(incoming, "rateLimitsByLimitId", "rate_limits_by_limit_id"))
	r := Row{}
	if len(buckets) == 0 && len(updates) == 0 && (oldID == "" || newID == "" || oldID == newID) {
		r["rateLimits"] = merge(base, update)
	} else {
		if len(buckets) == 0 {
			key := oldID
			if key == "" {
				key = "codex"
			}
			buckets[key] = base
		}
		if len(updates) == 0 {
			key := newID
			if key == "" {
				key = oldID
			}
			if key == "" {
				key = "codex"
			}
			updates = Row{key: update}
		}
		for id, v := range updates {
			buckets[id] = merge(ValueRow(buckets[id]), ValueRow(v))
		}
		selected := ValueRow(buckets["codex"])
		if len(selected) == 0 {
			selected = ValueRow(buckets[oldID])
		}
		if len(selected) == 0 {
			keys := make([]string, 0, len(buckets))
			for k := range buckets {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			if len(keys) > 0 {
				selected = ValueRow(buckets[keys[0]])
			}
		}
		r["rateLimits"] = selected
		r["rateLimitsByLimitId"] = buckets
	}
	for _, key := range []string{"accountId", "account_id"} {
		if v, ok := incoming[key]; ok {
			r[key] = v
		} else if v, ok := old[key]; ok {
			r[key] = v
		}
	}
	// Credit presence is independent of bucket changes; explicit null remains unknown.
	for _, source := range []Row{incoming, update, old, base} {
		if v, ok := source["rateLimitResetCredits"]; ok {
			r["rateLimitResetCredits"] = v
			break
		}
		if v, ok := source["rate_limit_reset_credits"]; ok {
			r["rateLimitResetCredits"] = v
			break
		}
	}
	return r
}
func (q *QuotaService) ResetCredit(id string) (Row, error) {
	if q.opts.Mock {
		return Row{"pending": false, "credit_id": id}, errors.New("模拟模式不使用真实重置")
	}
	if id == "" || len(id) > 512 {
		return nil, errors.New("请选择具体重置券")
	}
	q.mu.RLock()
	ctx := q.ctx
	account := ValueString(q.state["account_key"])
	q.mu.RUnlock()
	if ctx == nil || account == "" {
		return nil, errors.New("无法确认当前账户")
	}
	command := systemResetCommand{id, account, make(chan systemRPCResult, 1)}
	select {
	case q.reset <- command:
	default:
		return nil, errors.New("已有重置请求正在处理")
	}
	select {
	case r := <-command.reply:
		return r.value, r.err
	case <-ctx.Done():
		return nil, errors.New("重置结果待确认，请下次重试本次操作")
	}
}
func (q *QuotaService) consume(command systemResetCommand) (Row, error) {
	result := Row{"credit_id": command.id, "pending": false}
	q.clientMu.Lock()
	client := q.client
	q.clientMu.Unlock()
	if client == nil {
		return result, errors.New("额度服务未就绪")
	}
	ctx, cancel := context.WithTimeout(q.ctx, 20*time.Second)
	defer cancel()
	account, e := client.request(ctx, "account/read", Row{"refreshToken": false})
	if e != nil {
		return result, e
	}
	applicable, _, e := systemAccountMode(q.root, account)
	if e != nil || !applicable {
		return result, errors.New("此账户不提供 ChatGPT 额度")
	}
	_, before, e := systemCredentials(q.root)
	if e != nil {
		return result, e
	}
	payload, e := client.request(ctx, "account/rateLimits/read", Row{"excludeResetCreditDetails": false})
	if e != nil {
		return result, e
	}
	_, after, e := systemCredentials(q.root)
	key := systemAccountKey(payload, before, after)
	if e != nil || key == "" || key != command.account {
		return result, errors.New("账户已变更，请重新选择重置")
	}
	operations, e := systemReadJSON(filepath.Join(q.opts.Directory, "reset_operations.json"), 1<<20)
	if e != nil && !os.IsNotExist(e) {
		return result, errors.New("无法读取上一次重置操作")
	}
	pending := ValueRow(operations[key])
	if len(pending) > 0 && ValueString(pending["creditId"]) != command.id {
		return result, errors.New("请先确认上一次重置的结果")
	}
	snapshot, e := systemParseQuota(payload)
	if e != nil {
		return result, e
	}
	var credit Row
	for _, c := range ValueRows(snapshot["reset_credit_details"]) {
		if ValueString(c["id"]) == command.id {
			credit = c
			break
		}
	}
	if len(pending) > 0 {
		credit = ValueRow(pending["credit"])
	} else {
		if len(credit) == 0 || ValueString(credit["status"]) != "available" || ValueString(credit["reset_type"]) != "codexRateLimits" {
			return result, errors.New("这次重置已不可用")
		}
		if t, ok := ParseStamp(credit["expires_at"]); ok && !time.Now().Before(t) {
			return result, errors.New("这次重置已过期")
		}
		id, e := systemResetID()
		if e != nil {
			return result, e
		}
		pending = Row{"creditId": command.id, "idempotencyKey": id, "credit": credit, "startedAt": UTCStamp(time.Now())}
		operations[key] = pending
		if e = WriteJSON(filepath.Join(q.opts.Directory, "reset_operations.json"), operations); e != nil {
			return result, errors.New("无法保存重置操作，未提交重置")
		}
	}
	result["pending"] = true
	result["credit"] = credit
	_, current, e := systemCredentials(q.root)
	if e != nil || current != before {
		return result, errors.New("账户已变更，请重新选择重置")
	}
	ctx2, cancel2 := context.WithTimeout(q.ctx, 20*time.Second)
	defer cancel2()
	response, e := client.request(ctx2, "account/rateLimitResetCredit/consume", Row{"creditId": command.id, "idempotencyKey": pending["idempotencyKey"]})
	if e != nil {
		return result, errors.New("重置结果尚未确认，可重试本次操作")
	}
	outcome := ValueString(response["outcome"])
	if !systemContains([]string{"reset", "alreadyRedeemed", "nothingToReset", "noCredit"}, outcome) {
		return result, errors.New("重置结果尚未确认，可重试本次操作")
	}
	delete(operations, key)
	if e = WriteJSON(filepath.Join(q.opts.Directory, "reset_operations.json"), operations); e != nil {
		return result, errors.New("结果已返回但记录保存失败，请重试本次确认")
	}
	result["pending"] = false
	result["outcome"] = outcome
	q.Refresh()
	return result, nil
}
func systemRandomID() (string, error) {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	return hex.EncodeToString(b), nil
}
func systemResetID() (string, error) {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	s := hex.EncodeToString(b)
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:], nil
}
func (q *QuotaService) ReadReports(threads []Row, force bool) (Row, error) {
	q.mu.RLock()
	ctx := q.ctx
	state := CloneRow(q.state)
	q.mu.RUnlock()
	if ctx == nil {
		ctx = context.Background()
	}
	if q.opts.Mock {
		return q.reports.read(ctx, threads, force)
	}
	if !ValueBool(state["applicable"]) {
		return Row{"plan": Row{}, "chats": Row{}, "error": "此账户不提供 ChatGPT 额度"}, nil
	}
	r, e := q.reports.read(ctx, threads, force)
	q.mu.RLock()
	key := ValueString(q.state["account_key"])
	q.mu.RUnlock()
	if e == nil && ValueString(r["account_key"]) != "" && ValueString(r["account_key"]) != key {
		q.reports.invalidate()
		return nil, errors.New("账户已变更，请重新刷新")
	}
	return r, e
}

// Source changes invalidate the submitted chat set without switching login roots.
func (q *QuotaService) InvalidateReports() { q.reports.invalidate() }
