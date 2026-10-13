//go:build !windows

package backend

// The Windows collector is compiled on Mac for diagnostics, but Windows birth
// evidence cannot verify a process on another operating system.
func claudeVerifiedProcess(Row) bool { return false }
