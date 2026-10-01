package backend

import (
	"context"
	"errors"
	"net"
	"time"
)

type mobileNetworkError struct{ cause error }

func (e *mobileNetworkError) Error() string {
	return "云端连接暂时不可用，保留上次数据"
}
func (e *mobileNetworkError) Unwrap() error { return e.cause }

func mobileTemporaryError(err error) bool {
	var response *mobileHTTPError
	if errors.As(err, &response) {
		return response.status == 429 || response.status >= 500
	}
	var transport *mobileNetworkError
	if errors.As(err, &transport) {
		return true
	}
	var network net.Error
	return errors.As(err, &network)
}

func (m *MobileHost) cloudFailure(err error) {
	if errors.Is(err, context.Canceled) {
		return
	}
	m.mu.Lock()
	temporary := mobileTemporaryError(err)
	changed := m.cloudError != err.Error() || m.cloudErrorTemporary != temporary
	m.cloudError, m.cloudErrorTemporary = err.Error(), temporary
	m.uploadFailures = min(6, m.uploadFailures+1)
	wait := time.Duration(30*(1<<uint(m.uploadFailures-1))) * time.Second
	m.uploadRetry = time.Now().Add(min(15*time.Minute, wait))
	m.mu.Unlock()
	if changed {
		m.notify()
	}
}

// Only a confirmed cloud operation may clear cloud failures. LAN ACKs clear
// only the LAN temporary state; they never erase credentials/configuration errors.
func (m *MobileHost) cloudSucceededLocked() bool {
	changed := m.cloudError != "" || m.status != "云端已同步"
	m.cloudError, m.cloudErrorTemporary = "", false
	m.uploadRetry, m.uploadFailures = time.Time{}, 0
	m.status = "云端已同步"
	return changed
}
