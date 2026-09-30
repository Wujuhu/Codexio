//go:build windows

package backend

import (
	"fmt"
	"golang.org/x/sys/windows"
	"os"
)

func ledgerFileIdentity(f *os.File) string {
	var info windows.ByHandleFileInformation
	if windows.GetFileInformationByHandle(windows.Handle(f.Fd()), &info) != nil {
		return ""
	}
	return fmt.Sprintf("%d:%d:%d", info.VolumeSerialNumber, info.FileIndexHigh, info.FileIndexLow)
}
