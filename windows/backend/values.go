package backend

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func ValueString(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprint(v)
}
func ValueFloat(v any) (float64, bool) {
	var n float64
	var e error
	switch x := v.(type) {
	case float64:
		n = x
	case float32:
		n = float64(x)
	case int:
		n = float64(x)
	case int64:
		n = float64(x)
	case int32:
		n = float64(x)
	case json.Number:
		n, e = x.Float64()
	case string:
		n, e = strconv.ParseFloat(x, 64)
	default:
		return 0, false
	}
	return n, e == nil && !math.IsNaN(n) && !math.IsInf(n, 0)
}
func ValueInt(v any) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case int:
		return int64(x)
	case json.Number:
		if n, e := x.Int64(); e == nil {
			return n
		}
	case string:
		if n, e := strconv.ParseInt(x, 10, 64); e == nil {
			return n
		}
	}
	if n, ok := ValueFloat(v); ok && n == math.Trunc(n) && n >= math.MinInt64 && n < math.MaxInt64 {
		return int64(n)
	}
	return 0
}
func ValueBool(v any) bool { b, _ := v.(bool); return b }
func ValueRow(v any) Row {
	switch x := v.(type) {
	case Row:
		return x
	case map[string]any:
		return Row(x)
	}
	return Row{}
}
func ValueRows(v any) []Row {
	switch x := v.(type) {
	case []Row:
		return x
	case []any:
		r := make([]Row, 0, len(x))
		for _, v := range x {
			if m := ValueRow(v); len(m) > 0 {
				r = append(r, m)
			}
		}
		return r
	}
	return []Row{}
}
func ValueStrings(v any) []string {
	switch x := v.(type) {
	case []string:
		return x
	case []any:
		r := make([]string, 0, len(x))
		for _, v := range x {
			if s := ValueString(v); s != "" {
				r = append(r, s)
			}
		}
		return r
	}
	return []string{}
}
func CloneRow(v Row) Row { b, _ := json.Marshal(v); r, _ := DecodeRow(b); return r }
func DecodeRow(b []byte) (Row, error) {
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.UseNumber()
	var r Row
	e := d.Decode(&r)
	if r == nil {
		r = Row{}
	}
	return r, e
}
func ReadJSON(path string) (Row, error) {
	b, e := os.ReadFile(path)
	if e != nil {
		return Row{}, e
	}
	return DecodeRow(b)
}
func WriteJSON(path string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	if e = os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return e
	}
	temp := path + ".tmp"
	f, e := os.OpenFile(temp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	_, e = f.Write(append(b, '\n'))
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		_ = os.Remove(temp)
		return e
	}
	return os.Rename(temp, path)
}
func HashString(v string) string  { h := sha256.Sum256([]byte(v)); return hex.EncodeToString(h[:]) }
func UTCStamp(t time.Time) string { return t.UTC().Format(time.RFC3339Nano) }
func ParseStamp(v any) (time.Time, bool) {
	if n, ok := ValueFloat(v); ok {
		sec, frac := math.Modf(n)
		return time.Unix(int64(sec), int64(frac*1e9)), true
	}
	s := ValueString(v)
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006-01-02T15:04:05.999999", "2006-01-02"} {
		if t, e := time.ParseInLocation(layout, s, time.Local); e == nil {
			return t, true
		}
	}
	return time.Time{}, false
}
