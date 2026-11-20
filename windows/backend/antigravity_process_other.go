//go:build !windows

package backend

import (
	"context"
	"errors"
)

func antigravityAppEndpoints(context.Context) ([]antigravityEndpoint, error) {
	return nil, errors.New("Antigravity App 本地服务需要 Windows")
}
