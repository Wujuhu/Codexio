package backend

import (
	"errors"
	"net/http"
	"net/url"
	"path"
	"strings"
	"unsafe"

	"golang.org/x/net/http/httpproxy"
	"golang.org/x/sys/windows"
)

// Match the Mac URLSession and Python urllib system-proxy fallback when the
// application was started from Explorer without proxy environment variables.
// Explicit environment proxies and NO_PROXY retain their normal precedence.
func systemAccountProxy(request *http.Request) (*url.URL, error) {
	config := httpproxy.FromEnvironment()
	if config.HTTPProxy != "" || config.HTTPSProxy != "" {
		return config.ProxyFunc()(request.URL)
	}
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
		return config.ProxyFunc()(request.URL)
	}
	host := strings.ToLower(request.URL.Hostname())
	for _, pattern := range strings.Split(windows.UTF16PtrToString(settings.Bypass), ";") {
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
	proxy := strings.TrimSpace(windows.UTF16PtrToString(settings.Proxy))
	if strings.Contains(proxy, "=") {
		selected := ""
		for _, entry := range strings.Split(proxy, ";") {
			protocol, value, found := strings.Cut(entry, "=")
			if found && strings.EqualFold(strings.TrimSpace(protocol), request.URL.Scheme) {
				selected = strings.TrimSpace(value)
				break
			}
		}
		proxy = selected
	}
	if proxy == "" {
		return config.ProxyFunc()(request.URL)
	}
	if !strings.Contains(proxy, "://") {
		proxy = "http://" + proxy
	}
	parsed, err := url.Parse(proxy)
	if err != nil || parsed.Host == "" || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https" && parsed.Scheme != "socks5" && parsed.Scheme != "socks5h") {
		return nil, errors.New("无法读取系统代理配置")
	}
	config.HTTPProxy, config.HTTPSProxy = proxy, proxy
	return config.ProxyFunc()(request.URL)
}
