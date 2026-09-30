//go:build !windows

package backend

import "os"

func ledgerFileIdentity(f *os.File) string { return f.Name() }
