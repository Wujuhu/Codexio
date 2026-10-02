package backend

// Port of macos/native/UpstreamCoordinator.read/layer/write. Codex resolves the
// effective configuration and writable user layer; installation paths are not
// evidence of where a running desktop app keeps its configuration.
import (
	"context"
	"errors"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

type upstreamConfigClient struct {
	rpc    *systemRPC
	ctx    context.Context
	cancel context.CancelFunc
}

func (p *UpstreamProxy) openConfigClient() (*upstreamConfigClient, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	path, err := systemDiscoverCLI(ctx, ValueString(p.config()["codex_path"]), nil)
	if err != nil {
		cancel()
		return nil, err
	}
	rpc, err := systemNewRPC(ctx, path, p.version, systemCodexRoot(), nil)
	if err != nil {
		cancel()
		return nil, err
	}
	return &upstreamConfigClient{rpc: rpc, ctx: ctx, cancel: cancel}, nil
}

func (c *upstreamConfigClient) close() {
	c.cancel()
	c.rpc.close()
}

func (c *upstreamConfigClient) read() (Row, error) {
	return c.rpc.request(c.ctx, "config/read", Row{"includeLayers": true})
}

func upstreamUserLayer(snapshot Row, file string) (Row, error) {
	var found Row
	for _, layer := range ValueRows(snapshot["layers"]) {
		name := ValueRow(layer["name"])
		if ValueString(name["type"]) == "user" && layer["disabledReason"] == nil && (file == "" || filepath.Clean(ValueString(name["file"])) == filepath.Clean(file)) {
			found = layer
		}
	}
	if found == nil || ValueString(ValueRow(found["name"])["file"]) == "" || ValueString(found["version"]) == "" {
		return nil, errors.New("找不到可安全修改的用户配置层")
	}
	return found, nil
}

var upstreamConfigKey = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func (c *upstreamConfigClient) write(locator []string, value any, file, version string) error {
	if len(locator) == 0 || file == "" || version == "" {
		return errors.New("路由配置层无效")
	}
	for _, key := range locator {
		if !upstreamConfigKey.MatchString(key) {
			return errors.New("当前配置键不能安全接管")
		}
	}
	response, err := c.rpc.request(c.ctx, "config/value/write", Row{"keyPath": strings.Join(locator, "."), "value": value, "mergeStrategy": "replace", "filePath": file, "expectedVersion": version})
	if err != nil {
		return err
	}
	if ValueString(response["status"]) != "ok" {
		return errors.New("配置被更高优先级覆盖，路由未生效")
	}
	return nil
}

func (c *upstreamConfigClient) resolve() (Row, error) {
	snapshot, err := c.read()
	if err != nil {
		return nil, err
	}
	account, err := c.rpc.request(c.ctx, "account/read", Row{"refreshToken": false})
	if err != nil {
		return nil, err
	}
	return upstreamConfigPlan(snapshot, ValueRow(account["account"]))
}

func upstreamConfigPlan(snapshot, account Row) (Row, error) {
	config := ValueRow(snapshot["config"])
	provider := firstString(config["model_provider"], "openai")
	if systemContains([]string{"amazon-bedrock", "ollama", "lmstudio"}, provider) {
		return nil, errors.New("该内置服务不支持 Responses 转发")
	}
	custom := ValueRow(ValueRow(config["model_providers"])[provider])
	if firstString(custom["wire_api"], "responses") != "responses" {
		return nil, errors.New("上游检测仅支持 Responses API")
	}
	base := []string{"openai_base_url"}
	origin := ValueString(config["openai_base_url"])
	if provider != "openai" {
		base = []string{"model_providers", provider, "base_url"}
		origin = ValueString(custom["base_url"])
	} else if origin == "" {
		origin = "https://chatgpt.com/backend-api/codex"
		if ValueString(config["forced_login_method"]) == "api" || ValueString(account["type"]) == "apiKey" {
			origin = "https://api.openai.com/v1"
		}
	}
	u, err := url.Parse(origin)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "http" && u.Scheme != "https") {
		return nil, errors.New("模型服务地址无效")
	}
	owner, err := upstreamUserLayer(snapshot, "")
	if err != nil {
		return nil, err
	}
	raw := ValueRow(owner["config"])
	profile := firstString(config["profile"], raw["profile"])
	locator := append([]string{}, base...)
	if profile != "" {
		candidate := append([]string{"profiles", profile}, base...)
		if value, present := upstreamNested(raw, candidate); present && value != nil {
			locator = candidate
		}
	}
	before, present := upstreamNested(raw, locator)
	if before == nil {
		present = false
	}
	file := ValueString(ValueRow(owner["name"])["file"])
	return Row{"patch_path": file, "version": owner["version"], "locator": locator, "base_path": base, "origin": strings.TrimRight(origin, "/"), "provider": provider, "original_present": present, "original_endpoint": before}, nil
}

func (c *upstreamConfigClient) targets(locator []string, endpoint string) (bool, error) {
	snapshot, err := c.read()
	if err != nil {
		return false, err
	}
	actual, _ := upstreamNested(ValueRow(snapshot["config"]), locator)
	return ValueString(actual) == endpoint, nil
}

func (c *upstreamConfigClient) restore(j Row) error {
	snapshot, err := c.read()
	if err != nil {
		return err
	}
	file := ValueString(j["config"])
	owner, err := upstreamUserLayer(snapshot, file)
	if err != nil {
		return err
	}
	locator := ValueStrings(j["locator"])
	actual, present := upstreamNested(ValueRow(owner["config"]), locator)
	if actual == nil {
		present = false
	}
	if ValueString(actual) == ValueString(j["applied_endpoint"]) {
		var before any
		if ValueBool(j["original_present"]) {
			before = j["original_endpoint"]
		}
		return c.write(locator, before, file, ValueString(owner["version"]))
	}
	if upstreamDigest([]any{present, actual}) != ValueString(j["original_digest"]) {
		return errors.New("路由已被其他程序修改，保留当前配置和转发")
	}
	return nil
}
