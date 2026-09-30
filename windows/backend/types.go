package backend

import "context"

// Row preserves the legacy JSON ledger fields and their null/missing values.
type Row map[string]any

type Query struct {
	Mode        string `json:"mode"`
	Period      string `json:"period"`
	Start       string `json:"start"`
	End         string `json:"end"`
	Model       string `json:"model"`
	Tier        string `json:"tier"`
	Status      string `json:"status"`
	Search      string `json:"search"`
	Source      string `json:"source"`
	Page        int    `json:"page"`
	PageSize    int    `json:"page_size"`
	Granularity string `json:"granularity"`
}

type PageResult struct {
	Rows   []Row          `json:"rows"`
	Total  int            `json:"total"`
	Page   int            `json:"page"`
	Pages  int            `json:"pages"`
	Counts map[string]int `json:"counts"`
}

type DataOptions struct {
	Directory   string
	Roots       []string
	Mock        bool
	Config      Row
	ScanUpdated func(string)
}

type SystemOptions struct {
	Directory  string
	Executable string
	Version    string
	Mock       bool
	Config     func() Row
	Changed    func()
	Sample     func(Row)
	Exit       func()
}

type DesktopCallbacks struct {
	Action  func(string) error
	OpenURL func(string) error
	SavePNG func([]byte, string) (string, error)
	Changed func(string, Row)
}

type backgroundService interface {
	Start(context.Context)
	Close() error
}
