package backend

import (
	"errors"
	"net/http"
	"net/netip"
	"net/url"
	"path"
	"strings"
	"unsafe"

	"golang.org/x/net/http/httpproxy"
	"golang.org/x/sys/windows"
)

// Explorer-launched apps must use the same configured proxy as Windows.
// User system settings are authoritative; environment settings are a fallback
// only when Windows has no manual proxy configured.
type systemWindowsProxySettings struct {
	proxy, bypass string
	configured    bool
}

func systemReadWindowsProxy() systemWindowsProxySettings {
	var settings struct {
		AutoDetect                   int32
		AutoConfigURL, Proxy, Bypass *uint16
	}
	read := windows.NewLazySystemDLL("winhttp.dll").NewProc("WinHttpGetIEProxyConfigForCurrentUser")
	free := windows.NewLazySystemDLL("kernel32.dll").NewProc("GlobalFree")
	ok, _, _ := read.Call(uintptr(unsafe.Pointer(&settings)))
	defer func() {
		for _, value := range []*uint16{settings.AutoConfigURL, settings.Proxy, settings.Bypass} {
			if value != nil {
				free.Call(uintptr(unsafe.Pointer(value)))
			}
		}
	}()
	if ok == 0 || settings.Proxy == nil {
		return systemWindowsProxySettings{}
	}
	return systemWindowsProxySettings{windows.UTF16PtrToString(settings.Proxy), windows.UTF16PtrToString(settings.Bypass), true}
}

func systemAccountProxy(request *http.Request) (*url.URL, error) {
	settings := systemReadWindowsProxy()
	if !settings.configured {
		return httpproxy.FromEnvironment().ProxyFunc()(request.URL)
	}
	host := strings.ToLower(request.URL.Hostname())
	for _, pattern := range strings.Split(settings.bypass, ";") {
		pattern = strings.ToLower(strings.TrimSpace(pattern))
		if pattern == "<local>" && !strings.Contains(host, ".") {
			return nil, nil
		}
		if matched, _ := path.Match(pattern, host); pattern != "" && matched {
			return nil, nil
		}
		if matched, _ := path.Match(pattern, strings.ToLower(request.URL.Host)); pattern != "" && matched {
			return nil, nil
		}
	}
	proxy, err := settings.forScheme(request.URL.Scheme)
	if err != nil || proxy == "" {
		return nil, err
	}
	// The Windows bypass list above is authoritative, not inherited NO_PROXY.
	system := httpproxy.Config{HTTPProxy: proxy, HTTPSProxy: proxy}
	return system.ProxyFunc()(request.URL)
}

func (settings systemWindowsProxySettings) forScheme(scheme string) (string, error) {
	proxy := strings.TrimSpace(settings.proxy)
	if strings.Contains(proxy, "=") {
		selected := ""
		for _, entry := range strings.Split(proxy, ";") {
			protocol, value, found := strings.Cut(entry, "=")
			if found && strings.EqualFold(strings.TrimSpace(protocol), scheme) {
				selected = strings.TrimSpace(value)
				break
			}
		}
		proxy = selected
	}
	if proxy == "" {
		return "", nil
	}
	if !strings.Contains(proxy, "://") {
		proxy = "http://" + proxy
	}
	parsed, err := url.Parse(proxy)
	if err != nil || parsed.Host == "" || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https" && parsed.Scheme != "socks5" && parsed.Scheme != "socks5h") {
		return "", errors.New("无法读取系统代理配置")
	}
	return parsed.String(), nil
}

// Reuse our HTTP clients' Windows proxy for the account/config CLI only.
// No system settings or Codex configuration are changed.
func systemChildProxyEnvironment(env []string) ([]string, error) {
	settings := systemReadWindowsProxy()
	if !settings.configured {
		return env, nil
	}
	for _, scheme := range []string{"http", "https"} {
		proxy, err := settings.forScheme(scheme)
		if err != nil {
			return nil, err
		}
		key := strings.ToUpper(scheme) + "_PROXY"
		env = systemRemoveEnv(env, key)
		if proxy != "" {
			env = systemSetEnv(env, key, proxy)
		}
	}
	env = systemRemoveEnv(env, "ALL_PROXY")
	bypass := []string{"localhost", "127.0.0.1", "::1"}
	for _, pattern := range strings.Split(settings.bypass, ";") {
		pattern = strings.TrimSpace(pattern)
		if pattern == "" || strings.EqualFold(pattern, "<local>") {
			continue
		}
		if strings.HasPrefix(pattern, "*.") {
			pattern = strings.TrimPrefix(pattern, "*")
		} else if strings.HasSuffix(pattern, ".*") {
			parts := strings.Split(strings.TrimSuffix(pattern, ".*"), ".")
			if len(parts) <= 3 {
				address := strings.Join(parts, ".") + strings.Repeat(".0", 4-len(parts))
				if ip, err := netip.ParseAddr(address); err == nil && ip.Is4() {
					pattern = netip.PrefixFrom(ip, len(parts)*8).String()
				}
			}
		}
		bypass = append(bypass, pattern)
	}
	return systemSetEnv(env, "NO_PROXY", strings.Join(bypass, ",")), nil
}
