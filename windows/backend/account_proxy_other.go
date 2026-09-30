//go:build !windows

package backend

import (
	"net/http"
	"net/url"
)

func systemAccountProxy(request *http.Request) (*url.URL, error) {
	return http.ProxyFromEnvironment(request)
}
