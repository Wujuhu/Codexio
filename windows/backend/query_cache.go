package backend

import (
	"fmt"
	"time"
)

const (
	queryCacheLimit   = 8 << 20
	queryCacheEntries = 64
)

type queryCacheKey struct {
	generation int64
	query      string
}

type queryCacheEntry struct {
	key string
	raw string
}

func (s *Store) cachedQuery(kind string, q Query) (queryCacheKey, Row, bool) {
	q.Page = 0
	q.PageSize = 0
	q.Mode = ""
	q.View = ""
	now := time.Now()
	zone, offset := now.Zone()
	key := queryCacheKey{s.Generation(), fmt.Sprint(kind, "|", now.Format("2006-01-02"), "|", zone, "|", offset, "|", dataJSON(q))}
	s.mu.Lock()
	element := s.queryCache[key.query]
	if s.queryCacheGeneration != key.generation || element == nil {
		s.mu.Unlock()
		return key, nil, false
	}
	s.queryCacheOrder.MoveToFront(element)
	raw := element.Value.(queryCacheEntry).raw
	s.mu.Unlock()
	return key, dataRow(raw), true
}
func (s *Store) cacheQuery(key queryCacheKey, value Row) {
	if value == nil {
		return
	}
	raw := dataJSON(value)
	if len(raw) > 2<<20 {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if key.generation != s.Generation() {
		return
	}
	if s.queryCacheGeneration != key.generation {
		clear(s.queryCache)
		s.queryCacheOrder.Init()
		s.queryCacheBytes = 0
		s.queryCacheGeneration = key.generation
	}
	if old := s.queryCache[key.query]; old != nil {
		s.queryCacheBytes -= len(old.Value.(queryCacheEntry).raw)
		s.queryCacheOrder.Remove(old)
	}
	s.queryCache[key.query] = s.queryCacheOrder.PushFront(queryCacheEntry{key.query, raw})
	s.queryCacheBytes += len(raw)
	for s.queryCacheBytes > queryCacheLimit || len(s.queryCache) > queryCacheEntries {
		last := s.queryCacheOrder.Back()
		entry := last.Value.(queryCacheEntry)
		s.queryCacheBytes -= len(entry.raw)
		delete(s.queryCache, entry.key)
		s.queryCacheOrder.Remove(last)
	}
}
