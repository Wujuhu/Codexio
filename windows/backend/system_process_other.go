//go:build !windows

package backend

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"strings"
	"syscall"
)

func systemConfigureCommand(c *exec.Cmd)    { c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} }
func systemPrepareOwnedCommand(c *exec.Cmd) {}
func systemCLICommand(ctx context.Context, path string, args ...string) (*exec.Cmd, error) {
	if ctx != nil {
		return exec.CommandContext(ctx, path, args...), nil
	}
	return exec.Command(path, args...), nil
}
func systemOwnChild(c *exec.Cmd) (func(), error) {
	return func() { _ = syscall.Kill(-c.Process.Pid, syscall.SIGKILL) }, nil
}
func systemDetachedStart(path string, args []string, dir string, env []string) (*exec.Cmd, error) {
	return nil, errors.New("Windows updater unavailable on this platform")
}
func systemInstallLock(string) (func(), error) { return nil, errors.New("Windows updater unavailable") }
func systemWaitParent(int, string, string) (func(context.Context) error, func(), error) {
	return nil, nil, errors.New("Windows updater unavailable")
}
func systemKeyring(string) []byte                             { return nil }
func systemInstallRoots(context.Context) ([]string, []string) { return nil, nil }
func systemLanguage() string {
	if strings.HasPrefix(strings.ToLower(os.Getenv("LANG")), "zh") {
		return "zh"
	}
	return "en"
}
