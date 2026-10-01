package backend

// Uses the same executable/resource and process-identity checks as
// upstream_client.py; shell Codex commands are never desktop restart targets.
import (
	"errors"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

type upstreamProcess struct {
	pid, parent uint32
	path        string
	birth       int64
	args        []string
}

func upstreamProcesses() ([]upstreamProcess, error) {
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil, err
	}
	defer windows.CloseHandle(snapshot)
	entry := windows.ProcessEntry32{Size: uint32(unsafe.Sizeof(windows.ProcessEntry32{}))}
	rows := []upstreamProcess{}
	for err = windows.Process32First(snapshot, &entry); err == nil; err = windows.Process32Next(snapshot, &entry) {
		if len(rows) > 65536 {
			return nil, errors.New("进程清单过大")
		}
		rows = append(rows, upstreamProcess{pid: entry.ProcessID, parent: entry.ParentProcessID, path: windows.UTF16ToString(entry.ExeFile[:])})
	}
	if err != windows.ERROR_NO_MORE_FILES {
		return nil, err
	}
	return rows, nil
}
func upstreamOpenProcess(pid uint32, memory bool) (windows.Handle, error) {
	access := uint32(windows.PROCESS_QUERY_LIMITED_INFORMATION | windows.SYNCHRONIZE)
	if memory {
		access |= windows.PROCESS_VM_READ | windows.PROCESS_QUERY_INFORMATION
	}
	return windows.OpenProcess(access, false, pid)
}
func upstreamReadProcess(h windows.Handle) (string, int64, []string, error) {
	buffer := make([]uint16, 32768)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(h, 0, &buffer[0], &size); err != nil {
		return "", 0, nil, err
	}
	var birth, exit, kernel, user windows.Filetime
	if err := windows.GetProcessTimes(h, &birth, &exit, &kernel, &user); err != nil {
		return "", 0, nil, err
	}
	command := make([]byte, 65536)
	var returned uint32
	if err := windows.NtQueryInformationProcess(h, windows.ProcessCommandLineInformation, unsafe.Pointer(&command[0]), uint32(len(command)), &returned); err != nil {
		return "", 0, nil, err
	}
	u := (*windows.NTUnicodeString)(unsafe.Pointer(&command[0]))
	base := uintptr(unsafe.Pointer(&command[0]))
	pointer := uintptr(unsafe.Pointer(u.Buffer))
	if u.Length%2 != 0 || pointer < base || pointer+uintptr(u.Length) > base+uintptr(len(command)) {
		return "", 0, nil, errors.New("无法确认客户端参数")
	}
	args, err := windows.DecomposeCommandLine(windows.UTF16ToString(unsafe.Slice(u.Buffer, int(u.Length)/2)))
	if err != nil {
		return "", 0, nil, err
	}
	return windows.UTF16ToString(buffer[:size]), birth.Nanoseconds(), args, nil
}
func upstreamDesktopClients() ([]upstreamProcess, error) {
	processes, err := upstreamProcesses()
	if err != nil {
		return nil, err
	}
	candidates := map[uint32]upstreamProcess{}
	for _, process := range processes {
		name := strings.ToLower(filepath.Base(process.path))
		if name != "codex.exe" && name != "chatgpt.exe" {
			continue
		}
		h, err := upstreamOpenProcess(process.pid, false)
		if err != nil {
			continue
		}
		path, birth, args, err := upstreamReadProcess(h)
		windows.CloseHandle(h)
		if err != nil {
			continue
		}
		helper := false
		for _, arg := range args {
			if strings.HasPrefix(arg, "--type=") {
				helper = true
			}
		}
		if helper {
			continue
		}
		if stat, err := os.Stat(filepath.Join(filepath.Dir(path), "resources", "app.asar")); err != nil || !stat.Mode().IsRegular() {
			continue
		}
		process.path = path
		process.birth = birth
		process.args = args
		candidates[process.pid] = process
	}
	result := []upstreamProcess{}
	for _, process := range candidates {
		if parent, ok := candidates[process.parent]; ok && strings.EqualFold(parent.path, process.path) {
			continue
		}
		result = append(result, process)
	}
	return result, nil
}
func upstreamDesktopClientRunning() (bool, error) {
	clients, err := upstreamDesktopClients()
	return len(clients) > 0, err
}
func upstreamReadRemote(h windows.Handle, address uintptr, buffer unsafe.Pointer, size uintptr) error {
	var read uintptr
	err := windows.ReadProcessMemory(h, address, (*byte)(buffer), size, &read)
	if err != nil {
		return err
	}
	if read != size {
		return errors.New("客户端配置读取不完整")
	}
	return nil
}
func upstreamEnvironment(h windows.Handle) (map[string]string, string, error) {
	var basic windows.PROCESS_BASIC_INFORMATION
	var returned uint32
	if err := windows.NtQueryInformationProcess(h, windows.ProcessBasicInformation, unsafe.Pointer(&basic), uint32(unsafe.Sizeof(basic)), &returned); err != nil {
		return nil, "", err
	}
	var peb windows.PEB
	if err := upstreamReadRemote(h, uintptr(unsafe.Pointer(basic.PebBaseAddress)), unsafe.Pointer(&peb), unsafe.Sizeof(peb)); err != nil {
		return nil, "", err
	}
	var params windows.RTL_USER_PROCESS_PARAMETERS
	if err := upstreamReadRemote(h, uintptr(unsafe.Pointer(peb.ProcessParameters)), unsafe.Pointer(&params), unsafe.Sizeof(params)); err != nil {
		return nil, "", err
	}
	if params.EnvironmentSize == 0 || params.EnvironmentSize > 128<<10 || params.EnvironmentSize%2 != 0 {
		return nil, "", errors.New("无法确认客户端环境")
	}
	words := make([]uint16, params.EnvironmentSize/2)
	if err := upstreamReadRemote(h, uintptr(params.Environment), unsafe.Pointer(&words[0]), params.EnvironmentSize); err != nil {
		return nil, "", err
	}
	values := map[string]string{}
	for _, entry := range strings.Split(string(utf16SystemDecode(words)), "\x00") {
		key, value, ok := strings.Cut(entry, "=")
		if ok && key != "" {
			values[strings.ToUpper(key)] = value
		}
	}
	cwd := ""
	if n := params.CurrentDirectory.DosPath.Length; n > 0 && n%2 == 0 {
		b := make([]uint16, n/2)
		if err := upstreamReadRemote(h, uintptr(unsafe.Pointer(params.CurrentDirectory.DosPath.Buffer)), unsafe.Pointer(&b[0]), uintptr(n)); err != nil {
			return nil, "", err
		}
		cwd = windows.UTF16ToString(b)
	}
	return values, cwd, nil
}
func upstreamDesktopRoute() (string, string, error) {
	clients, err := upstreamDesktopClients()
	if err != nil {
		return "", "", err
	}
	if len(clients) == 0 {
		return filepath.Join(systemCodexRoot(), "config.toml"), "", nil
	}
	processes, err := upstreamProcesses()
	if err != nil {
		return "", "", err
	}
	parents := map[uint32]uint32{}
	for _, p := range processes {
		parents[p.pid] = p.parent
	}
	root, profile := "", ""
	for _, client := range clients {
		found := false
		for _, process := range processes {
			pid := process.parent
			descendant := false
			for steps := 0; pid != 0 && steps < 64; steps++ {
				if pid == client.pid {
					descendant = true
					break
				}
				pid = parents[pid]
			}
			if !descendant || !strings.EqualFold(filepath.Base(process.path), "codex.exe") {
				continue
			}
			h, err := upstreamOpenProcess(process.pid, true)
			if err != nil {
				continue
			}
			path, _, args, err := upstreamReadProcess(h)
			if err != nil {
				windows.CloseHandle(h)
				continue
			}
			isBackend := false
			for _, a := range args {
				if a == "app-server" {
					isBackend = true
				}
			}
			relative, rerr := filepath.Rel(filepath.Dir(client.path), path)
			if !isBackend || rerr != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
				windows.CloseHandle(h)
				continue
			}
			values, cwd, err := upstreamEnvironment(h)
			windows.CloseHandle(h)
			if err != nil {
				return "", "", errors.New("无法确认桌面客户端的配置路径，请等待客户端启动完成后再开启上游检测")
			}
			for _, key := range []string{"OPENAI_BASE_URL", "CHATGPT_BASE_URL"} {
				if _, ok := values[key]; ok {
					return "", "", errors.New("客户端环境变量覆盖了模型路由，未进行接管")
				}
			}
			selected := ""
			for i := 0; i < len(args); i++ {
				arg := args[i]
				option, value := "", ""
				if (arg == "-p" || arg == "--profile" || arg == "-c" || arg == "--config") && i+1 < len(args) {
					option = arg
					i++
					value = args[i]
				} else if strings.HasPrefix(arg, "--profile=") || strings.HasPrefix(arg, "--config=") {
					option, value, _ = strings.Cut(arg, "=")
				}
				if option == "-p" || option == "--profile" {
					selected = value
				}
				if option == "-c" || option == "--config" {
					key := strings.TrimSpace(strings.SplitN(value, "=", 2)[0])
					key = strings.SplitN(key, ".", 2)[0]
					for _, routeKey := range []string{"model_provider", "model_providers", "openai_base_url", "chatgpt_base_url", "forced_login_method", "experimental_realtime_ws_base_url"} {
						if key == routeKey {
							return "", "", errors.New("客户端启动参数覆盖了模型路由，未进行接管")
						}
					}
					if key == "profile" {
						var overlay Row
						if err = tomlDecodeProfile(value, &overlay); err != nil {
							return "", "", err
						}
						selected = ValueString(overlay["profile"])
					}
				}
			}
			home := values["CODEX_HOME"]
			if home == "" {
				home = systemCodexRoot()
			}
			if !filepath.IsAbs(home) {
				if cwd == "" {
					return "", "", errors.New("无法确认客户端工作目录")
				}
				home = filepath.Join(cwd, home)
			}
			candidate, err := filepath.Abs(filepath.Join(home, "config.toml"))
			if err != nil {
				return "", "", err
			}
			if foundRoot := root != ""; foundRoot && (!strings.EqualFold(root, candidate) || profile != selected) {
				return "", "", errors.New("多个桌面客户端使用不同的配置，请保留一个配置后再开启上游检测")
			}
			root = candidate
			profile = selected
			found = true
		}
		if !found {
			return "", "", errors.New("无法确认当前桌面客户端的配置路径，请等待客户端启动完成后再开启上游检测")
		}
	}
	return root, profile, nil
}

var upstreamRestartMu sync.Mutex

func upstreamLegacyRouteBusy(directory string) bool {
	session, err := ReadJSON(filepath.Join(directory, "upstream", "session.json"))
	if err != nil {
		return false
	}
	process := ValueRow(session["process"])
	pid := ValueInt(process["pid"])
	created, ok := ValueFloat(process["created"])
	if !ok || pid <= 0 || pid > 1<<32-1 {
		return false
	}
	h, err := upstreamOpenProcess(uint32(pid), false)
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	var birth, exit, kernel, user windows.Filetime
	if windows.GetProcessTimes(h, &birth, &exit, &kernel, &user) != nil {
		return false
	}
	state, err := windows.WaitForSingleObject(h, 0)
	return err == nil && state == uint32(windows.WAIT_TIMEOUT) && math.Abs(float64(birth.Nanoseconds())/1e9-created) < 0.01
}
func upstreamRestartDesktop() error {
	upstreamRestartMu.Lock()
	defer upstreamRestartMu.Unlock()
	clients, err := upstreamDesktopClients()
	if err != nil {
		return err
	}
	if len(clients) == 0 {
		return errors.New("未找到正在运行的 Codex 桌面客户端")
	}
	user := windows.NewLazySystemDLL("user32.dll")
	enum := user.NewProc("EnumWindows")
	windowPID := user.NewProc("GetWindowThreadProcessId")
	post := user.NewProc("PostMessageW")
	launched := map[string]string{}
	for _, client := range clients {
		h, err := upstreamOpenProcess(client.pid, false)
		if err != nil {
			continue
		}
		path, birth, _, err := upstreamReadProcess(h)
		if err != nil || birth != client.birth || !strings.EqualFold(path, client.path) {
			windows.CloseHandle(h)
			return errors.New("客户端进程已变化，请重试")
		}
		aumid := ""
		proc := windows.NewLazySystemDLL("kernel32.dll").NewProc("GetApplicationUserModelId")
		var length uint32
		code, _, _ := proc.Call(uintptr(h), uintptr(unsafe.Pointer(&length)), 0)
		if code == 122 && length > 0 && length < 32768 {
			b := make([]uint16, length)
			code, _, _ = proc.Call(uintptr(h), uintptr(unsafe.Pointer(&length)), uintptr(unsafe.Pointer(&b[0])))
			if code == 0 {
				aumid = windows.UTF16ToString(b)
			}
		}
		callback := syscall.NewCallback(func(hwnd, unused uintptr) uintptr {
			var pid uint32
			windowPID.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
			if pid == client.pid {
				post.Call(hwnd, 0x0010, 0, 0)
			}
			return 1
		})
		enum.Call(callback, 0)
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			state, err := windows.WaitForSingleObject(h, 100)
			if err != nil {
				windows.CloseHandle(h)
				return err
			}
			if state == windows.WAIT_OBJECT_0 {
				break
			}
		}
		state, err := windows.WaitForSingleObject(h, 0)
		windows.CloseHandle(h)
		if err != nil || state != windows.WAIT_OBJECT_0 {
			return errors.New("Codex 客户端尚未正常退出，已保留可用转发，请稍后重试")
		}
		launched[client.path] = aumid
	}
	for path, aumid := range launched {
		var command *exec.Cmd
		if aumid != "" {
			command = exec.Command(filepath.Join(os.Getenv("WINDIR"), "explorer.exe"), `shell:AppsFolder\`+aumid)
		} else {
			command = exec.Command(path)
			command.Dir = filepath.Dir(path)
		}
		systemConfigureCommand(command)
		if err = command.Start(); err != nil {
			return errors.New("未能重新打开 Codex 客户端，可手动打开客户端")
		}
		go command.Wait()
	}
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		running, err := upstreamDesktopClients()
		if err == nil {
			for _, client := range running {
				if _, ok := launched[client.path]; ok {
					return nil
				}
			}
		}
		time.Sleep(250 * time.Millisecond)
	}
	return errors.New("Codex 客户端启动未确认，请手动打开客户端")
}

func upstreamConfigLock(path string) (func(), error) {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	overlap := &windows.Overlapped{}
	deadline := time.Now().Add(5 * time.Second)
	for {
		err = windows.LockFileEx(windows.Handle(file.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, overlap)
		if err == nil {
			return func() { windows.UnlockFileEx(windows.Handle(file.Fd()), 0, 1, 0, overlap); file.Close() }, nil
		}
		if time.Now().After(deadline) {
			file.Close()
			return nil, errors.New("另一个上游检测操作正在修改配置，请稍后重试")
		}
		time.Sleep(50 * time.Millisecond)
	}
}
