package backend

import (
	"bufio"
	"os"
	"strings"
)

// This compatibility path is needed only for old forks without an ordinal
// boundary. It is keyed by parent file identity/size/mtime and bounded to 32 forks.
func (s *Store) parentSignatures(parent, cutoff string) (map[string]bool, bool) {
	path := ""
	for _, files := range s.files {
		for _, file := range files {
			if rolloutID(file) == parent {
				path = file
				break
			}
		}
		if path != "" {
			break
		}
	}
	if path == "" {
		return nil, false
	}
	key := path + ":" + fileStamp(path) + ":" + cutoff
	if cached, ok := s.parentSignatureCache[key]; ok {
		return cached, true
	}
	f, e := os.Open(path)
	if e != nil {
		return nil, false
	}
	defer f.Close()
	r := bufio.NewScanner(f)
	r.Buffer(make([]byte, 65536), 8<<20)
	result := map[string]bool{}
	for r.Scan() {
		line := r.Bytes()
		if !strings.Contains(string(line), "token_count") {
			continue
		}
		entry, e := DecodeRow(line)
		if e != nil {
			continue
		}
		if timestamp := stamp(entry["timestamp"]); cutoff != "" && timestamp > cutoff {
			continue
		}
		p := ValueRow(entry["payload"])
		if dataString(entry, "type") != "event_msg" || dataString(p, "type") != "token_count" {
			continue
		}
		info := ValueRow(p["info"])
		total, _ := usageCounts(info["total_token_usage"])
		last, _ := usageCounts(info["last_token_usage"])
		result[pythonHash([]any{total, last})] = true
		if len(result) > 100000 {
			return nil, false
		}
	}
	if r.Err() != nil {
		return nil, false
	}
	total := len(result)
	for _, cached := range s.parentSignatureCache {
		total += len(cached)
	}
	for len(s.parentSignatureCache) >= 32 || total > 100000 {
		for old := range s.parentSignatureCache {
			total -= len(s.parentSignatureCache[old])
			delete(s.parentSignatureCache, old)
			break
		}
	}
	s.parentSignatureCache[key] = result
	return result, true
}
