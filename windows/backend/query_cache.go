package backend

import (
	"fmt"
	"time"
)

func (s *Store) cachedQuery(kind string, q Query) (string, Row, bool) {
	q.Page = 0
	q.PageSize = 0
	q.Mode = ""
	now := time.Now()
	zone, offset := now.Zone()
	key := fmt.Sprint(kind, "|", s.Generation(), "|", now.Format("2006-01-02"), "|", zone, "|", offset, "|", dataJSON(q))
	s.mu.RLock()
	raw, ok := s.queryCache[key]
	s.mu.RUnlock()
	if !ok {
		return key, nil, false
	}
	return key, dataRow(raw), true
}
func (s *Store) cacheQuery(key string, value Row) {
	if value == nil {
		return
	}
	raw := dataJSON(value)
	if len(raw) > 2<<20 {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	generation := s.Generation()
	if s.queryCacheGeneration != generation || len(s.queryCache) >= 64 {
		s.queryCache = map[string]string{}
		s.queryCacheGeneration = generation
	}
	s.queryCache[key] = raw
}
