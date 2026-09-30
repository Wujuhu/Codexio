//go:build windows

package backend

import (
	"errors"
	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
	"time"
	"unsafe"
)

// Identical current-user DPAPI blob, no optional entropy, UI forbidden, to
// Python mobile_windows._dpapi. Never overwrite an undecodable old identity.
func mobileDPAPI(value []byte, decode bool) ([]byte, error) {
	if len(value) == 0 || len(value) > 2<<20 {
		return nil, errors.New("invalid protected identity size")
	}
	input := windows.DataBlob{Size: uint32(len(value)), Data: &value[0]}
	var output windows.DataBlob
	var e error
	if decode {
		e = windows.CryptUnprotectData(&input, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &output)
	} else {
		e = windows.CryptProtectData(&input, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &output)
	}
	if e != nil {
		return nil, e
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(output.Data)))
	if output.Size > 2<<20 {
		return nil, errors.New("invalid unprotected identity size")
	}
	return append([]byte(nil), unsafe.Slice(output.Data, int(output.Size))...), nil
}
func mobileTimeZone() string {
	k, e := registry.OpenKey(registry.LOCAL_MACHINE, `SYSTEM\CurrentControlSet\Control\TimeZoneInformation`, registry.QUERY_VALUE)
	if e == nil {
		defer k.Close()
		name, _, e := k.GetStringValue("TimeZoneKeyName")
		if e == nil {
			if value := mobileWindowsZones[name]; value != "" {
				return value
			}
		}
	}
	name, _ := time.Now().Zone()
	return name
}
