//go:build !windows

package backend

import "context"

func antigravityAppEndpoints(context.Context) ([]antigravityEndpoint, error) {
	return nil, errAntigravityAppClosed
}
