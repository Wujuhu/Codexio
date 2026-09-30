package backend

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

func systemConfigureCommand(c *exec.Cmd) {
	if c.SysProcAttr == nil {
		c.SysProcAttr = &syscall.SysProcAttr{}
	}
	c.SysProcAttr.HideWindow = true
	c.SysProcAttr.CreationFlags = 0x08000000 | 0x00000200
}
func systemPrepareOwnedCommand(c *exec.Cmd) {
	if c.SysProcAttr == nil {
		c.SysProcAttr = &syscall.SysProcAttr{}
	}
	c.SysProcAttr.CreationFlags |= windows.CREATE_SUSPENDED
}
func systemCLICommand(ctx context.Context, path string, args ...string) (*exec.Cmd, error) {
	makeCommand := func(name string, args ...string) *exec.Cmd {
		if ctx != nil {
			return exec.CommandContext(ctx, name, args...)
		}
		return exec.Command(name, args...)
	}
	if !strings.EqualFold(filepath.Ext(path), ".cmd") && !strings.EqualFold(filepath.Ext(path), ".bat") {
		return makeCommand(path, args...), nil
	}
	// Cmd/Node shims are a last fallback and receive fixed backend arguments only.
	if strings.ContainsAny(path, "\"&|<>^%!\r\n") {
		return nil, errors.New("Codex 启动脚本路径包含不支持的字符")
	}
	for _, a := range args {
		if !systemContains([]string{"app-server", "--listen", "stdio://", "--version", "--help"}, a) {
			return nil, errors.New("Codex 启动参数无效")
		}
	}
	shell := filepath.Join(os.Getenv("SystemRoot"), "System32", "cmd.exe")
	c := makeCommand(shell)
	c.SysProcAttr = &syscall.SysProcAttr{CmdLine: syscall.EscapeArg(shell) + ` /d /s /c ""` + path + `" ` + strings.Join(args, " ") + `"`}
	return c, nil
}
func systemOwnChild(c *exec.Cmd) (func(), error) {
	job, e := windows.CreateJobObject(nil, nil)
	if e != nil {
		return nil, e
	}
	var info windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
	info.BasicLimitInformation.LimitFlags = 0x2000
	_, e = windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation, uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info)))
	if e != nil {
		windows.CloseHandle(job)
		return nil, e
	}
	p, e := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE|windows.SYNCHRONIZE, false, uint32(c.Process.Pid))
	if e != nil {
		windows.CloseHandle(job)
		return nil, e
	}
	defer windows.CloseHandle(p)
	if e = windows.AssignProcessToJobObject(job, p); e != nil {
		windows.CloseHandle(job)
		return nil, e
	}
	if c.SysProcAttr != nil && c.SysProcAttr.CreationFlags&windows.CREATE_SUSPENDED != 0 {
		if e = systemResumeOwnedChild(p, uint32(c.Process.Pid)); e != nil {
			windows.CloseHandle(job)
			return nil, e
		}
	}
	var once sync.Once
	return func() { once.Do(func() { _ = windows.CloseHandle(job) }) }, nil
}
func systemResumeOwnedChild(process windows.Handle, pid uint32) error {
	state, e := windows.WaitForSingleObject(process, 0)
	if e != nil || state == windows.WAIT_OBJECT_0 {
		return errors.New("Codex 子进程已退出")
	}
	snapshot, e := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPTHREAD, 0)
	if e != nil {
		return e
	}
	defer windows.CloseHandle(snapshot)
	entry := windows.ThreadEntry32{Size: uint32(unsafe.Sizeof(windows.ThreadEntry32{}))}
	for e = windows.Thread32First(snapshot, &entry); e == nil; e = windows.Thread32Next(snapshot, &entry) {
		if entry.OwnerProcessID != pid {
			continue
		}
		thread, err := windows.OpenThread(windows.THREAD_SUSPEND_RESUME, false, entry.ThreadID)
		if err != nil {
			return err
		}
		state, err = windows.WaitForSingleObject(process, 0)
		if err == nil && state != windows.WAIT_OBJECT_0 {
			_, err = windows.ResumeThread(thread)
		}
		windows.CloseHandle(thread)
		if err != nil {
			return err
		}
		if state == windows.WAIT_OBJECT_0 {
			return errors.New("Codex 子进程已退出")
		}
		return nil
	}
	return errors.New("无法恢复已隔离的 Codex 子进程")
}
func systemDetachedStart(path string, args []string, dir string, env []string) (*exec.Cmd, error) {
	c := exec.Command(path, args...)
	c.Dir = dir
	c.Env = env
	systemConfigureCommand(c)
	e := c.Start()
	return c, e
}
func systemInstallLock(target string) (func(), error) {
	// Windows mutex ownership belongs to an OS thread, not a Go goroutine.
	runtime.LockOSThread()
	name, _ := windows.UTF16PtrFromString(`Local\Codexio.Update.` + HashString(strings.ToLower(target))[:24])
	h, e := windows.CreateMutex(nil, false, name)
	if e != nil {
		runtime.UnlockOSThread()
		return nil, e
	}
	n, e := windows.WaitForSingleObject(h, 0)
	if e != nil || (n != 0 && n != 0x80) {
		windows.CloseHandle(h)
		runtime.UnlockOSThread()
		return nil, errors.New("另一个 Codexio 实例正在更新")
	}
	return func() { windows.ReleaseMutex(h); windows.CloseHandle(h); runtime.UnlockOSThread() }, nil
}
func systemWaitParent(pid int, target, directory string) (func(context.Context) error, func(), error) {
	h, e := windows.OpenProcess(windows.SYNCHRONIZE|windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if e != nil {
		return nil, nil, errors.New("无法确认正在更新的应用进程")
	}
	b := make([]uint16, 32768)
	n := uint32(len(b))
	if e = windows.QueryFullProcessImageName(h, 0, &b[0], &n); e != nil || !strings.EqualFold(filepath.Clean(windows.UTF16ToString(b[:n])), filepath.Clean(target)) {
		windows.CloseHandle(h)
		return nil, nil, errors.New("更新目标与应用进程不一致")
	}
	wait := func(ctx context.Context) error {
		deadline := time.NewTimer(90 * time.Second)
		defer deadline.Stop()
		tick := time.NewTicker(100 * time.Millisecond)
		defer tick.Stop()
		for {
			if _, e := os.Stat(filepath.Join(directory, "cancel")); e == nil {
				return errors.New("已取消更新")
			}
			v, e := windows.WaitForSingleObject(h, 0)
			if e != nil {
				return e
			}
			if v == 0 {
				if _, e := os.Stat(filepath.Join(directory, "commit")); e != nil {
					return errors.New("应用未确认安装，已保留当前版本")
				}
				return nil
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-deadline.C:
				return errors.New("应用尚未正常退出，已保留当前版本")
			case <-tick.C:
			}
		}
	}
	return wait, func() { windows.CloseHandle(h) }, nil
}

type systemCredential struct {
	Flags, Type             uint32
	TargetName, Comment     *uint16
	LastWritten             windows.Filetime
	BlobSize                uint32
	Blob                    *byte
	Persist, AttributeCount uint32
	Attributes              uintptr
	TargetAlias, UserName   *uint16
}

func systemKeyring(name string) []byte {
	dll := windows.NewLazySystemDLL("advapi32.dll")
	read := dll.NewProc("CredReadW")
	free := dll.NewProc("CredFree")
	for _, target := range []string{"Codex Auth:" + name, name + ":Codex Auth", "Codex Auth/" + name} {
		s, _ := windows.UTF16PtrFromString(target)
		var p *systemCredential
		r, _, _ := read.Call(uintptr(unsafe.Pointer(s)), 1, 0, uintptr(unsafe.Pointer(&p)))
		if r != 0 && p != nil {
			var result []byte
			if p.BlobSize <= 1<<20 && p.Blob != nil {
				result = append([]byte{}, unsafe.Slice(p.Blob, p.BlobSize)...)
			}
			free.Call(uintptr(unsafe.Pointer(p)))
			return result
		}
	}
	return nil
}
func systemLanguage() string {
	p := windows.NewLazySystemDLL("kernel32.dll").NewProc("GetUserPreferredUILanguages")
	var count, size uint32
	r, _, _ := p.Call(8, uintptr(unsafe.Pointer(&count)), 0, uintptr(unsafe.Pointer(&size)))
	if r != 0 && size > 0 && size < 65536 {
		b := make([]uint16, size)
		r, _, _ = p.Call(8, uintptr(unsafe.Pointer(&count)), uintptr(unsafe.Pointer(&b[0])), uintptr(unsafe.Pointer(&size)))
		if r != 0 {
			for _, s := range strings.Split(string(utf16SystemDecode(b)), "\x00") {
				s = strings.ToLower(s)
				if strings.HasPrefix(s, "zh") {
					return "zh"
				}
				if strings.HasPrefix(s, "en") {
					return "en"
				}
			}
		}
	}
	return "en"
}
func systemInstallRoots(ctx context.Context) ([]string, []string) {
	roots := []string{}
	for _, h := range []registry.Key{registry.CURRENT_USER, registry.LOCAL_MACHINE} {
		for _, view := range []uint32{registry.WOW64_64KEY, registry.WOW64_32KEY} {
			k, e := registry.OpenKey(h, `Software\Microsoft\Windows\CurrentVersion\Uninstall`, registry.READ|view)
			if e != nil {
				continue
			}
			names, _ := k.ReadSubKeyNames(1024)
			for _, n := range names {
				v, e := registry.OpenKey(k, n, registry.READ|view)
				if e != nil {
					continue
				}
				label, _, _ := v.GetStringValue("DisplayName")
				if strings.Contains(strings.ToLower(label), "codex") || strings.Contains(strings.ToLower(label), "chatgpt") {
					for _, key := range []string{"InstallLocation", "DisplayIcon"} {
						s, _, e := v.GetStringValue(key)
						if e == nil && s != "" {
							if key == "DisplayIcon" {
								s = strings.Split(s, ",")[0]
								s = filepath.Dir(systemExpandPath(s))
							}
							roots = append(roots, s)
						}
					}
				}
				v.Close()
			}
			k.Close()
		}
	}
	// Read-only MSIX/process inventory, including applications on custom drives.
	pc, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	shell := filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	c := exec.CommandContext(pc, shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", `$ErrorActionPreference='SilentlyContinue'; [Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); $roots=@(Get-AppxPackage | Where-Object {$_.Name -match 'Codex|ChatGPT'} | Sort-Object Version -Descending | Select-Object -ExpandProperty InstallLocation); $running=@(Get-CimInstance Win32_Process -Filter "Name='codex.exe'" | Select-Object -ExpandProperty ExecutablePath -Unique); @{roots=$roots;running=$running} | ConvertTo-Json -Compress`)
	systemConfigureCommand(c)
	var out systemBoundedBuffer
	out.limit = 1 << 20
	c.Stdout = &out
	c.Stderr = &systemDiscardWriter{}
	if c.Run() == nil {
		var r Row
		if json.Unmarshal(out.Bytes(), &r) == nil {
			return append(roots, ValueStrings(r["roots"])...), ValueStrings(r["running"])
		}
	}
	return roots, nil
}
