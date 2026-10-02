package main

import (
	"codexio/windows/backend"
	"context"
	"errors"
	"github.com/wailsapp/wails/v3/pkg/application"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// SmokeService is bound only in --mock --smoke-test mode. It implements exactly
// the original startup/basic-data/close-reopen checks; it is not a test suite.
type SmokeService struct {
	host     *desktopHost
	output   string
	mu       sync.Mutex
	step     int
	checks   []string
	done     bool
	ok       bool
	oldID    uint
	deadline *time.Timer
}

func (s *SmokeService) startDeadline() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.done {
		return
	}
	s.deadline = time.AfterFunc(30*time.Second, func() { s.fail("程序未在 30 秒内完成三项模拟冒烟") })
}
func (s *SmokeService) Ready(ctx context.Context, values backend.Row) error {
	s.mu.Lock()
	if s.done {
		s.mu.Unlock()
		return nil
	}
	step := s.step
	s.mu.Unlock()
	h := s.host
	h.mu.Lock()
	w := h.main
	h.mu.Unlock()
	caller, ok := ctx.Value(application.WindowKey).(application.Window)
	if !ok || w == nil || caller.ID() != w.ID() {
		return errors.New("模拟就绪消息不属于当前主窗口")
	}
	if !desktopWindowVisible(w) {
		deadline := time.NewTimer(10 * time.Second)
		defer deadline.Stop()
		tick := time.NewTicker(50 * time.Millisecond)
		defer tick.Stop()
		for !desktopWindowVisible(w) {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-deadline.C:
				return s.finishError("主窗口未显示")
			case <-tick.C:
			}
		}
	}
	tokens := strings.TrimSpace(backend.ValueString(values["rendered_tokens"]))
	requests := strings.TrimSpace(backend.ValueString(values["rendered_requests"]))
	if backend.ValueString(values["page"]) != "overview" || tokens == "" || tokens == "—" || requests == "" || requests == "—" {
		return s.finishError("基本用量未显示")
	}
	overview, e := h.service.GetOverview(backend.Query{Period: "today"})
	if e != nil || backend.ValueRow(overview["summary"])["tokens"] == nil {
		return s.finishError("基本用量未读取")
	}
	_ = h.action("ui-ready")
	if step == 0 {
		s.mu.Lock()
		s.checks = []string{"程序启动", "基本数据显示"}
		s.step = 1
		s.oldID = w.ID()
		s.mu.Unlock()
		go func() {
			// Match the original smoke's 150 ms cadence and let the bridge reply
			// complete before destroying its caller window.
			time.Sleep(150 * time.Millisecond)
			w.Close()
			until := time.NewTimer(10 * time.Second)
			defer until.Stop()
			ticker := time.NewTicker(50 * time.Millisecond)
			defer ticker.Stop()
			for {
				if _, exists := h.app.Window.GetByID(w.ID()); !exists {
					h.mu.Lock()
					closed := h.main == nil
					h.mu.Unlock()
					if !closed {
						s.fail("主窗口未正常关闭")
						return
					}
					s.mu.Lock()
					s.step = 2
					s.mu.Unlock()
					h.openMain()
					return
				}
				select {
				case <-until.C:
					s.fail("主窗口未正常关闭")
					return
				case <-ticker.C:
				}
			}
		}()
		return nil
	}
	if step == 2 {
		s.mu.Lock()
		different := w.ID() != s.oldID
		if different {
			s.checks = append(s.checks, "主窗口关闭与重开")
		}
		s.mu.Unlock()
		if !different {
			return s.finishError("主窗口未能重新创建")
		}
		s.finish(true, "")
	}
	return nil
}
func (s *SmokeService) finishError(message string) error { s.fail(message); return errors.New(message) }
func (s *SmokeService) fail(message string)              { s.finish(false, message) }
func (s *SmokeService) finish(ok bool, message string) {
	s.mu.Lock()
	if s.done {
		s.mu.Unlock()
		return
	}
	s.done = true
	s.ok = ok
	if s.deadline != nil {
		s.deadline.Stop()
	}
	checks := append([]string{}, s.checks...)
	s.mu.Unlock()
	path, e := filepath.Abs(s.output)
	if e == nil {
		_ = os.MkdirAll(path, 0700)
		if err := backend.WriteJSON(filepath.Join(path, "result.json"), backend.Row{"ok": ok, "checks": checks, "error": message, "platform": "windows", "runtime": "Wails 3.0.0-beta.26", "version": strings.TrimSpace(desktopVersion), "mock": true, "data_path": s.host.directory}); err != nil {
			s.mu.Lock()
			s.ok = false
			s.mu.Unlock()
		}
	}
	go s.host.requestQuit()
}
func desktopSmokeFailure(output, message string) error {
	path, e := filepath.Abs(output)
	if e != nil {
		return e
	}
	if _, e := os.Stat(filepath.Join(path, "result.json")); e == nil {
		return nil
	}
	return backend.WriteJSON(filepath.Join(path, "result.json"), backend.Row{"ok": false, "checks": []string{}, "error": message, "platform": "windows", "mock": true})
}
