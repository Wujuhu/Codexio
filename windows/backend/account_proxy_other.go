//go:build !windows

package backend

import (
	"net/http"
	"net/url"
)

func systemAccountProxy(request *http.Request) (*url.URL, error) {
	return http.ProxyFromEnvironment(request)
}

func systemChildProxyEnvironment(env []string) ([]string, error) { return env, nil }
