package main

import (
	"codexio/windows/backend"
	"embed"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

//go:embed all:frontend/dist
var desktopAssets embed.FS

//go:embed VERSION
var desktopVersion string

type desktopArguments struct {
	mock  bool
	smoke string
}

func desktopParse(args []string) (desktopArguments, error) {
	r := desktopArguments{}
	set := flag.NewFlagSet("Codexio", flag.ContinueOnError)
	set.SetOutput(io.Discard)
	set.BoolVar(&r.mock, "mock", false, "Use isolated mock data")
	set.StringVar(&r.smoke, "smoke-test", "", "Original three-item mock smoke output")
	alias := set.String("smoke-output", "", "Alias for smoke-test")
	if e := set.Parse(args); e != nil {
		return r, e
	}
	if set.NArg() != 0 {
		return r, errors.New("unsupported arguments")
	}
	if *alias != "" {
		if r.smoke != "" && r.smoke != *alias {
			return r, errors.New("conflicting smoke output paths")
		}
		r.smoke = *alias
	}
	if r.smoke != "" && !r.mock {
		return r, errors.New("--smoke-test requires --mock")
	}
	return r, nil
}
func main() {
	if handled, e := backend.RunUpdateHelper(os.Args[1:]); handled {
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
		return
	}
	args, e := desktopParse(os.Args[1:])
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(2)
	}
	if e = desktopRun(args); e != nil {
		fmt.Fprintln(os.Stderr, e)
		if args.smoke != "" {
			_ = desktopSmokeFailure(args.smoke, e.Error())
		} else if !args.mock {
			desktopError(e.Error())
		}
		os.Exit(1)
	}
}
func desktopRun(args desktopArguments) error {
	version := strings.TrimSpace(desktopVersion)
	directory := ""
	var e error
	if args.mock {
		base := ""
		if args.smoke != "" {
			base, e = filepath.Abs(args.smoke)
			if e != nil {
				return e
			}
			if e = os.MkdirAll(base, 0700); e != nil {
				return e
			}
			if e = os.Remove(filepath.Join(base, "result.json")); e != nil && !os.IsNotExist(e) {
				return e
			}
		}
		directory, e = os.MkdirTemp(base, "Codexio-mock-")
	} else {
		directory, e = backend.ResolveDataDirectory("")
	}
	if e != nil {
		return e
	}
	// Version-scoped native guard runs before opening stores or starting services.
	// Mock runs have their own temporary directory and cannot wake a real app.
	key := "com.wujuhu.codexio.windows.wails." + backend.HashString(strings.ToLower(filepath.Clean(directory)) + "\x00" + version)[:24]
	release, secondary, e := desktopSingleInstance(key)
	if e != nil {
		return e
	}
	if secondary {
		return nil
	}
	defer release()
	assets, e := fs.Sub(desktopAssets, "frontend/dist")
	if e != nil {
		return e
	}
	h := &desktopHost{directory: directory, mock: args.mock, assets: assets, icons: map[string]desktopIcon{}, savedMain: backend.Row{}, savedFloating: backend.Row{}}
	if args.smoke != "" {
		h.smoke = &SmokeService{host: h, output: args.smoke}
	}
	h.app = application.New(application.Options{Name: "Codexio", Description: "Codex usage and quota", Icon: h.brandIcon("main"), LogLevel: slog.LevelWarn,
		Assets:         application.AssetOptions{Handler: application.BundledAssetFileServer(assets), DisableLogging: true},
		Windows:        application.WindowsOptions{WndClass: "CodexioWailsWindow", DisableQuitOnLastWindowClosed: true, WebviewUserDataPath: filepath.Join(directory, "WebView2")},
		SingleInstance: &application.SingleInstanceOptions{UniqueID: key, ExitCode: 0, AdditionalData: map[string]string{"action": "show-main"}, OnSecondInstanceLaunch: func(application.SecondInstanceData) { go h.openMain() }},
		ShouldQuit:     h.shouldQuit, OnShutdown: h.shutdown, PostShutdown: h.releaseIcons,
		ErrorHandler: func(err error) {
			if h.smoke != nil {
				h.smoke.fail("界面启动失败：" + err.Error())
			} else {
				h.notice("界面操作失败，请重试")
			}
		},
	})
	executable, e := os.Executable()
	if e != nil {
		return e
	}
	h.service, e = backend.NewService(directory, executable, version, args.mock, backend.DesktopCallbacks{Action: h.action, OpenURL: h.app.Browser.OpenURL, SavePNG: h.savePNG, Changed: h.changed})
	if e != nil {
		return e
	}
	h.app.RegisterService(application.NewService(h.service))
	if h.smoke != nil {
		h.app.RegisterService(application.NewService(h.smoke))
	}
	h.setupTray()
	h.app.Event.OnApplicationEvent(events.Common.ApplicationStarted, func(*application.ApplicationEvent) {
		h.service.Start()
		h.applySettings()
		if h.smoke != nil {
			h.smoke.startDeadline()
		}
	})
	h.app.Event.OnApplicationEvent(events.Common.ThemeChanged, func(*application.ApplicationEvent) { h.applyAppearance() })
	h.openMain()
	err := h.app.Run()
	if h.smoke != nil {
		h.smoke.mu.Lock()
		ok := h.smoke.done && h.smoke.ok
		h.smoke.mu.Unlock()
		if !ok && err == nil {
			return errors.New("模拟冒烟未通过")
		}
	}
	return err
}
