//go:build !windows

package backend

import (
	"errors"
	"path/filepath"
)

func upstreamDesktopRoute() (string, string, error) {
	return filepath.Join(systemCodexRoot(), "config.toml"), "", nil
}
func upstreamRestartDesktop() error                  { return errors.New("当前平台未提供桌面客户端重启") }
func upstreamDesktopClientRunning() (bool, error)    { return false, nil }
func upstreamLegacyRouteBusy(directory string) bool  { return false }
func upstreamConfigLock(path string) (func(), error) { return func() {}, nil }
