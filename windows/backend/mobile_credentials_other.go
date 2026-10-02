//go:build !windows

package backend

import (
	"errors"
	"time"
)

func mobileDPAPI(value []byte, decode bool) ([]byte, error) {
	return nil, errors.New("移动设备凭据需要 Windows DPAPI")
}
func mobileTimeZone() string { return time.Local.String() }
