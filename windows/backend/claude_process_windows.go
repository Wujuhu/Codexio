package backend

import (
	"golang.org/x/sys/windows"
	"path/filepath"
	"strconv"
	"strings"
)

// PID alone is not evidence: Claude's desktop metadata supplies Windows
// FILETIME process birth ticks, which must match the live engine exactly.
func claudeVerifiedProcess(metadata Row) bool {
	pid := ValueInt(metadata["pid"])
	if pid <= 0 || pid > 1<<32-1 || dataString(metadata, "pidDomain") != "win32:desktop" {
		return false
	}
	start, e := strconv.ParseUint(dataString(metadata, "procStart"), 10, 64)
	if e != nil || start == 0 {
		return false
	}
	h, e := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.SYNCHRONIZE, false, uint32(pid))
	if e != nil {
		return false
	}
	defer windows.CloseHandle(h)
	var birth, exit, kernel, user windows.Filetime
	if windows.GetProcessTimes(h, &birth, &exit, &kernel, &user) != nil {
		return false
	}
	ticks := uint64(birth.HighDateTime)<<32 | uint64(birth.LowDateTime)
	if ticks != start {
		return false
	}
	state, e := windows.WaitForSingleObject(h, 0)
	if e != nil || state != uint32(windows.WAIT_TIMEOUT) {
		return false
	}
	buffer := make([]uint16, 32768)
	size := uint32(len(buffer))
	if windows.QueryFullProcessImageName(h, 0, &buffer[0], &size) != nil {
		return false
	}
	name := strings.ToLower(filepath.Base(windows.UTF16ToString(buffer[:size])))
	return name == "claude.exe" || name == "claude-code.exe"
}
