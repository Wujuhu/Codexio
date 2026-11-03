package backend

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Like the existing Claude price alias history, names apply only after their
// observation. Persist the name on each request/call; never relabel old records
// with today's picker. Collection owns this cache under Store.work.
type requestModelNames struct {
	path      string
	sources   Row
	stamps    map[string]string
	confirmed map[string]float64
}

func requestModelID(value string) string {
	value = strings.TrimSpace(value)
	if strings.HasSuffix(strings.ToLower(value), "[1m]") {
		value = value[:len(value)-4]
	}
	return value
}
func explicitRequestModelName(target, source Row) {
	name := strings.TrimSpace(firstString(source["model_display_name"], source["modelDisplayName"]))
	if name == "" || len(name) > 512 {
		return
	}
	target["model_display_name"], target["model_display_id"], target["model_display_source"] = name, target["model"], "request"
}
func copyRequestModelName(target, source Row, ownedTurn ...bool) {
	if dataString(source, "model_display_name") == "" || (len(ownedTurn) == 0 || !ownedTurn[0]) && requestModelID(firstString(source["model_display_id"], source["model"])) != requestModelID(dataString(target, "model")) {
		return
	}
	for _, key := range []string{"model_display_name", "model_display_id", "model_display_source"} {
		if value, exists := source[key]; exists {
			target[key] = value
		}
	}
}
func (s *Store) observeRequestModelNames(root, client string) error {
	if s.modelNames == nil {
		s.modelNames = map[string]*requestModelNames{}
	}
	cache := s.modelNames[client]
	if cache == nil {
		path := filepath.Join(s.options.Directory, "request-model-names-"+client+".json")
		sources, _ := ReadJSON(path)
		if sources == nil {
			sources = Row{}
		}
		cache = &requestModelNames{path: path, sources: sources, stamps: map[string]string{}, confirmed: map[string]float64{}}
		s.modelNames[client] = cache
	}
	name := "models_cache.json"
	if client == "claude_code" {
		name = "settings.json"
	}
	path, key := filepath.Join(root, name), HashString(filepath.Clean(root))
	info, err := os.Stat(path)
	if err != nil && !os.IsNotExist(err) {
		return nil
	}
	stamp := "absent"
	if info != nil {
		stamp = fmt.Sprintf("%d:%d", info.ModTime().UnixNano(), info.Size())
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if cache.stamps[key] == stamp {
		cache.confirmed[key] = now
		return nil
	}
	labels, ambiguous := Row{}, map[string]bool{}
	if info != nil {
		raw, err := systemReadJSON(path, 4<<20)
		if err != nil {
			return nil
		}
		options := ValueRows(raw["models"])
		idKey, nameKey := "slug", "display_name"
		if client == "claude_code" {
			options = ValueRows(ValueRow(raw["modelPicker"])["options"])
			idKey, nameKey = "model", "label"
		}
		for i, option := range options {
			if i >= 2000 {
				break
			}
			id, label := requestModelID(dataString(option, idKey)), strings.TrimSpace(dataString(option, nameKey))
			if id == "" || len(id) > 512 || label == "" || len(label) > 512 || ambiguous[id] {
				continue
			}
			if old := dataString(labels, id); old != "" && old != label {
				delete(labels, id)
				ambiguous[id] = true
			} else {
				labels[id] = label
			}
		}
	}
	signature := HashString(dataJSON(labels))
	saved := ValueRow(cache.sources[key])
	if dataString(saved, "signature") != signature {
		history := ValueRows(saved["history"])
		if len(history) > 0 {
			until, ok := cache.confirmed[key]
			if !ok {
				until, _ = ValueFloat(saved["observed_at"])
			}
			history[len(history)-1]["until"] = until
		}
		history = append(history, Row{"since": now, "labels": labels})
		if len(history) > 128 {
			history = history[len(history)-128:]
		}
		if cache.sources[key] == nil && len(cache.sources) >= 64 {
			return nil
		}
		cache.sources[key] = Row{"signature": signature, "observed_at": now, "history": history}
		if err := WriteJSON(cache.path, cache.sources); err != nil {
			return err
		}
	}
	cache.stamps[key], cache.confirmed[key] = stamp, now
	return nil
}
func (s *Store) captureRequestModelName(row Row, root, client string, at any) {
	cache := s.modelNames[client]
	if cache == nil || dataString(row, "model_display_name") != "" {
		return
	}
	if at == nil {
		at = row["started_at"]
		if at == nil {
			at = row["timestamp"]
		}
	}
	date, ok := ParseStamp(at)
	if !ok {
		return
	}
	stamp, key, id := float64(date.UnixNano())/1e9, HashString(filepath.Clean(root)), requestModelID(dataString(row, "model"))
	if id == "" || stamp > cache.confirmed[key] {
		return
	}
	history := ValueRows(ValueRow(cache.sources[key])["history"])
	for i := len(history) - 1; i >= 0; i-- {
		since, _ := ValueFloat(history[i]["since"])
		until, closed := ValueFloat(history[i]["until"])
		if stamp < since || closed && stamp > until {
			continue
		}
		if label := dataString(ValueRow(history[i]["labels"]), id); label != "" {
			row["model_display_name"], row["model_display_id"] = label, row["model"]
			row["model_display_source"] = "codex_catalog"
			if client == "claude_code" {
				row["model_display_source"] = "model_picker"
			}
		}
		return
	}
}
