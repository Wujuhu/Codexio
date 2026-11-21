package backend

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	sqlite3 "modernc.org/sqlite/lib"
)

const nativeTimingSessions = 8
const nativeTimingEntries = 512

type antigravityNativeTiming struct {
	ttft, duration       float64
	hasTTFT, hasDuration bool
	matches              int
	conflict             bool
}

type nativeTimingEntry struct {
	value antigravityNativeTiming
	used  uint64
}

type nativeTimingSource struct {
	stamp  string
	values map[string]nativeTimingEntry
	used   uint64
}

type antigravityTimingCache struct {
	work    queryWorkLock
	sources map[string]*nativeTimingSource
	clock   uint64
}

type nativeTimingTarget struct {
	id, key, session, original  string
	step, input, cached, output int64
	created                     time.Time
}

func nativeTimingID(row Row) string {
	if dataString(row, "client_kind") != antigravityClient || dataString(row, "record_kind") == "user_request" {
		return ""
	}
	if row["request_status"] != "completed" && row["status"] != "completed" {
		return ""
	}
	return strings.TrimPrefix(dataString(row, "id"), "unassigned:")
}

func nativeTimingStamp(path string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return ""
	}
	return fmt.Sprintf("%s:%d:%d", ledgerFileIdentity(file), info.Size(), info.ModTime().UnixNano())
}

func (s *Store) enrichAntigravityTimings(records []Row, reader queryReader) error {
	if s.options.Mock {
		return nil
	}
	calls := map[string]Row{}
	for _, row := range records {
		if id := nativeTimingID(row); id != "" {
			calls[id] = row
		}
	}
	ids := make([]string, 0, len(calls))
	for id := range calls {
		ids = append(ids, id)
	}
	if len(ids) == 0 {
		return nil
	}
	args := make([]any, len(ids))
	for i, id := range ids {
		args[i] = id
	}
	slots := strings.TrimSuffix(strings.Repeat("?,", len(ids)), ",")
	rows, err := reader.Query("SELECT id,session_id,json_extract(data,'$.step_index'),json_extract(data,'$.created_at') FROM antigravity_events WHERE id IN ("+slots+")", args...)
	if err != nil {
		return err
	}
	targets := map[string][]nativeTimingTarget{}
	byID := map[string]nativeTimingTarget{}
	for rows.Next() {
		var target nativeTimingTarget
		if err = rows.Scan(&target.id, &target.session, &target.step, &target.original); err != nil {
			rows.Close()
			return err
		}
		row := calls[target.id]
		var inputOK, cacheOK, outputOK bool
		target.input, inputOK = knownCount(row["raw_input_tokens"])
		target.cached, cacheOK = knownCount(row["raw_cache_read_tokens"])
		target.output, outputOK = knownCount(row["raw_output_tokens"])
		uuid := strings.TrimPrefix(target.session, "antigravity:app:")
		if !inputOK || !cacheOK || !outputOK || uuid == target.session || rolloutUUID.FindString(uuid) != uuid {
			continue
		}
		target.created, err = time.Parse(time.RFC3339Nano, target.original)
		if err != nil || target.step < 0 {
			continue
		}
		if antigravityEventID(target.session, Row{"created_at": target.original, "step_index": target.step}) != target.id {
			continue
		}
		target.key = fmt.Sprintf("%s:%d:%d:%d", target.id, target.input, target.cached, target.output)
		path := filepath.Join(filepath.Dir(antigravityRoot()), "conversations", uuid+".db")
		targets[path] = append(targets[path], target)
		byID[target.id] = target
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	cache := &s.antigravityTiming
	if err = cache.work.LockContext(reader.ctx); err != nil {
		return err
	}
	defer cache.work.Unlock()
	if cache.sources == nil {
		cache.sources = map[string]*nativeTimingSource{}
	}
	for path, wanted := range targets {
		if err = reader.ctx.Err(); err != nil {
			return err
		}
		stamp := nativeTimingStamp(path)
		if stamp == "" {
			continue
		}
		stamp += "|" + nativeTimingStamp(path+"-wal")
		entry := cache.sources[path]
		cache.clock++
		if entry == nil || entry.stamp != stamp {
			entry = &nativeTimingSource{stamp: stamp, values: map[string]nativeTimingEntry{}}
			cache.sources[path] = entry
		}
		entry.used = cache.clock
		missing := []nativeTimingTarget{}
		for _, target := range wanted {
			if _, exists := entry.values[target.key]; !exists {
				missing = append(missing, target)
			}
		}
		if len(missing) > 0 {
			values, readErr := readAntigravityNativeTimings(reader, path, missing)
			if reader.ctx.Err() != nil {
				return reader.ctx.Err()
			}
			if readErr == nil && stamp == nativeTimingStamp(path)+"|"+nativeTimingStamp(path+"-wal") {
				for _, target := range missing {
					entry.values[target.key] = nativeTimingEntry{value: values[target.key], used: cache.clock}
				}
			}
		}
		for _, row := range records {
			target, exists := byID[nativeTimingID(row)]
			if !exists {
				continue
			}
			cached, found := entry.values[target.key]
			if !found {
				continue
			}
			cached.used = cache.clock
			entry.values[target.key] = cached
			value := cached.value
			if value.matches != 1 || value.conflict {
				continue
			}
			if current, known := ValueFloat(row["duration_ms"]); (!known || current < 0) && value.hasDuration {
				row["duration_ms"], row["duration_source"] = value.duration, "antigravity_native_generation"
			}
			if current, known := ValueFloat(row["ttft_ms"]); (!known || current < 0) && value.hasTTFT {
				row["ttft_ms"] = value.ttft
			}
		}
		for len(entry.values) > nativeTimingEntries {
			oldest := ""
			used := ^uint64(0)
			for key, value := range entry.values {
				if value.used < used {
					oldest, used = key, value.used
				}
			}
			delete(entry.values, oldest)
		}
	}
	for len(cache.sources) > nativeTimingSessions {
		oldest := ""
		used := ^uint64(0)
		for path, value := range cache.sources {
			if value.used < used {
				oldest, used = path, value.used
			}
		}
		delete(cache.sources, oldest)
	}
	return nil
}

type nativeStep struct {
	index   int64
	created nativeDuration
}

func (s *sourceSQLite) plannerStep(index int64) (nativeStep, bool, error) {
	statement, err := s.prepare(fmt.Sprintf("SELECT rowid,step_type,status,metadata IS NOT NULL FROM steps WHERE idx=%d", index))
	if err != nil {
		return nativeStep{}, false, err
	}
	defer sqlite3.Xsqlite3_finalize(s.tls, statement)
	rc := sqlite3.Xsqlite3_step(s.tls, statement)
	if rc == sqlite3.SQLITE_DONE {
		return nativeStep{}, false, nil
	}
	if rc != sqlite3.SQLITE_ROW {
		return nativeStep{}, false, errors.New("原生步骤索引暂不可用")
	}
	if sqlite3.Xsqlite3_column_int64(s.tls, statement, 1) != 15 || sqlite3.Xsqlite3_column_int64(s.tls, statement, 2) != 3 {
		return nativeStep{}, false, nil
	}
	rowID := sqlite3.Xsqlite3_column_int64(s.tls, statement, 0)
	if sqlite3.Xsqlite3_column_int64(s.tls, statement, 3) == 0 {
		return nativeStep{index: index}, true, nil
	}
	blob, err := s.blob("steps", "metadata", rowID)
	if err != nil {
		return nativeStep{}, false, err
	}
	defer blob.close()
	value := nativeStep{index: index}
	seenCreated := false
	err = nativeProtoFields(blob, 0, blob.size, func(field nativeProtoField) error {
		if field.number != 1 || field.wire != 2 {
			return nil
		}
		created, err := nativeProtoTime(blob, field.offset, field.length, false)
		if seenCreated && value.created != created {
			return errors.New("原生步骤时间冲突")
		}
		seenCreated = true
		value.created = created
		return err
	})
	return value, true, err
}

func readAntigravityNativeTimings(reader queryReader, path string, targets []nativeTimingTarget) (map[string]antigravityNativeTiming, error) {
	source, err := openSourceSQLite(reader.ctx, path)
	if err != nil {
		return nil, err
	}
	defer source.close()
	statement, err := source.prepare("SELECT rowid FROM gen_metadata WHERE data IS NOT NULL ORDER BY idx")
	if err != nil {
		return nil, err
	}
	defer sqlite3.Xsqlite3_finalize(source.tls, statement)
	wantedSteps := map[int64]bool{}
	for _, target := range targets {
		wantedSteps[target.step] = true
	}
	values := map[string]antigravityNativeTiming{}
	for {
		if err = reader.ctx.Err(); err != nil {
			return nil, err
		}
		rc := sqlite3.Xsqlite3_step(source.tls, statement)
		if rc == sqlite3.SQLITE_DONE {
			break
		}
		if rc != sqlite3.SQLITE_ROW {
			return nil, errors.New("原生生成索引暂不可用")
		}
		rowID := sqlite3.Xsqlite3_column_int64(source.tls, statement, 0)
		generation, err := readNativeGeneration(source, rowID, wantedSteps)
		if err != nil {
			return nil, err
		}
		if !generation.chatKnown || !generation.usageKnown {
			continue
		}
		planners := []nativeStep{}
		for index := range generation.steps {
			step, found, err := source.plannerStep(index)
			if err != nil {
				return nil, err
			}
			if found {
				planners = append(planners, step)
			}
		}
		for _, step := range planners {
			if !step.created.known {
				continue
			}
			for _, target := range targets {
				if step.index != target.step || generation.input != uint64(target.input) || generation.cached != uint64(target.cached) || generation.output != uint64(target.output) {
					continue
				}
				if step.created.seconds != target.created.Unix() {
					continue
				}
				// App 2.33 serializes created_at at whole-second UTC precision.
				// A future fractional source timestamp must match exactly.
				if strings.Contains(target.original, ".") && step.created.nanos != int64(target.created.Nanosecond()) {
					continue
				}
				value := values[target.key]
				value.matches++
				value.conflict = value.conflict || len(planners) != 1
				if generation.ttft.known {
					value.ttft, value.hasTTFT = generation.ttft.milliseconds(), true
				}
				if generation.ttft.known && generation.stream.known {
					duration := generation.ttft.milliseconds() + generation.stream.milliseconds()
					if duration > 0 {
						value.duration, value.hasDuration = duration, true
					}
				}
				values[target.key] = value
			}
		}
	}
	return values, nil
}

func readNativeGeneration(source *sourceSQLite, rowID int64, wanted map[int64]bool) (nativeGeneration, error) {
	blob, err := source.blob("gen_metadata", "data", rowID)
	if err != nil {
		return nativeGeneration{}, err
	}
	defer blob.close()
	generation, err := nativeGenerationHeader(blob)
	if err != nil || !generation.chatKnown {
		return generation, err
	}
	needed := false
	for index := range generation.steps {
		needed = needed || wanted[index]
	}
	if needed {
		err = generation.readTiming(blob)
	}
	return generation, err
}
