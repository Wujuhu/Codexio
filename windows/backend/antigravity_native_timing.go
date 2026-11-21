package backend

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
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
	owner                       string
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

func makeNativeTimingTarget(id, session, original string, step, input, cached, output any) (nativeTimingTarget, bool) {
	target := nativeTimingTarget{id: id, session: session, original: original}
	uuid := strings.TrimPrefix(session, "antigravity:app:")
	if uuid == session || rolloutUUID.FindString(uuid) != uuid || uuid == "" {
		return target, false
	}
	var stepOK, inputOK, cacheOK, outputOK bool
	target.step, stepOK = knownCount(step)
	target.input, inputOK = knownCount(input)
	target.cached, cacheOK = knownCount(cached)
	target.output, outputOK = knownCount(output)
	if !stepOK || !inputOK || !cacheOK || !outputOK {
		return target, false
	}
	var err error
	target.created, err = time.Parse(time.RFC3339Nano, original)
	if err != nil || antigravityEventID(session, Row{"created_at": original, "step_index": target.step}) != id {
		return target, false
	}
	target.key = fmt.Sprintf("%s:%d:%d:%d", id, target.input, target.cached, target.output)
	return target, true
}

func (s *Store) enrichAntigravityTimings(records []Row, reader queryReader) error {
	calls := collectAntigravityTimingRows(records)
	targets := map[string][]nativeTimingTarget{}
	if err := s.addAntigravityTimingTargets(calls, targets, reader); err != nil {
		return err
	}
	groups, err := collectAntigravityRequestTimings(records, targets, reader)
	if err != nil {
		return err
	}
	for session, wanted := range targets {
		eligible := wanted[:0]
		for _, target := range wanted {
			if group := groups[target.owner]; group == nil || !group.incomplete {
				eligible = append(eligible, target)
			}
		}
		targets[session] = eligible
	}
	err = s.resolveAntigravityTimings(reader, targets, func(target nativeTimingTarget, value antigravityNativeTiming) {
		if group := groups[target.owner]; group != nil && value.hasDuration {
			group.addDuration(value.duration)
		}
		for _, row := range calls[target.id] {
			if current, known := ValueFloat(row["duration_ms"]); (!known || current < 0) && value.hasDuration {
				row["duration_ms"], row["duration_source"] = value.duration, "antigravity_native_generation"
			}
			if current, known := ValueFloat(row["ttft_ms"]); (!known || current < 0) && value.hasTTFT {
				row["ttft_ms"] = value.ttft
			}
		}
	})
	if err != nil {
		return err
	}
	for _, group := range groups {
		group.apply()
	}
	return nil
}

func collectAntigravityTimingRows(records []Row) map[string][]Row {
	calls := map[string][]Row{}
	for _, row := range records {
		id := nativeTimingID(row)
		if id == "" {
			continue
		}
		duration, hasDuration := ValueFloat(row["duration_ms"])
		ttft, hasTTFT := ValueFloat(row["ttft_ms"])
		if !hasDuration || duration < 0 || !hasTTFT || ttft < 0 {
			calls[id] = append(calls[id], row)
		}
	}
	return calls
}

func (s *Store) addAntigravityTimingTargets(calls map[string][]Row, targets map[string][]nativeTimingTarget, reader queryReader) error {
	if s.options.Mock || len(calls) == 0 {
		return nil
	}
	ids := make([]string, 0, len(calls))
	for id := range calls {
		ids = append(ids, id)
	}
	args := make([]any, len(ids))
	for i, id := range ids {
		args[i] = id
	}
	slots := strings.TrimSuffix(strings.Repeat("?,", len(ids)), ",")
	rows, err := reader.Query("SELECT id,session_id,json_extract(data,'$.step_index'),coalesce(json_extract(data,'$.created_at'),'') FROM antigravity_events WHERE id IN ("+slots+")", args...)
	if err != nil {
		return err
	}
	for rows.Next() {
		var id, session, original string
		var step any
		if err = rows.Scan(&id, &session, &step, &original); err != nil {
			rows.Close()
			return err
		}
		row := calls[id][0]
		target, ok := makeNativeTimingTarget(id, session, original, step, row["raw_input_tokens"], row["raw_cache_read_tokens"], row["raw_output_tokens"])
		if ok {
			targets[session] = append(targets[session], target)
		}
	}
	err = rows.Err()
	rows.Close()
	return err
}

func (s *Store) resolveAntigravityTimings(reader queryReader, targets map[string][]nativeTimingTarget, apply func(nativeTimingTarget, antigravityNativeTiming)) error {
	if s.options.Mock || len(targets) == 0 {
		return nil
	}
	cache := &s.antigravityTiming
	if err := cache.work.LockContext(reader.ctx); err != nil {
		return err
	}
	defer cache.work.Unlock()
	if cache.sources == nil {
		cache.sources = map[string]*nativeTimingSource{}
	}
	for session, wanted := range targets {
		if err := reader.ctx.Err(); err != nil {
			return err
		}
		if len(wanted) == 0 {
			continue
		}
		uuid := strings.TrimPrefix(session, "antigravity:app:")
		path := filepath.Join(filepath.Dir(antigravityRoot()), "conversations", uuid+".db")
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
		pending := map[string]bool{}
		for _, target := range wanted {
			if _, exists := entry.values[target.key]; !exists && !pending[target.key] {
				missing = append(missing, target)
				pending[target.key] = true
			}
		}
		var values map[string]antigravityNativeTiming
		if len(missing) > 0 {
			var readErr error
			values, readErr = readAntigravityNativeTimings(reader, path, missing)
			if reader.ctx.Err() != nil {
				return reader.ctx.Err()
			}
			if stamp != nativeTimingStamp(path)+"|"+nativeTimingStamp(path+"-wal") {
				continue
			}
			if readErr != nil {
				values = nil
			}
		}
		for _, target := range wanted {
			cached, found := entry.values[target.key]
			if !found && values == nil {
				continue
			}
			if !found {
				cached.value = values[target.key]
			}
			cached.used = cache.clock
			entry.values[target.key] = cached
			value := cached.value
			if value.matches == 1 && !value.conflict {
				apply(target, value)
			}
		}
		if len(entry.values) > nativeTimingEntries {
			keys := make([]string, 0, len(entry.values))
			for key := range entry.values {
				keys = append(keys, key)
			}
			sort.Slice(keys, func(i, j int) bool {
				left, right := entry.values[keys[i]].used, entry.values[keys[j]].used
				if left == right {
					return keys[i] < keys[j]
				}
				return left < right
			})
			for _, key := range keys[:len(keys)-nativeTimingEntries] {
				delete(entry.values, key)
			}
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
	wantedSteps := map[int64][]nativeTimingTarget{}
	for _, target := range targets {
		wantedSteps[target.step] = append(wantedSteps[target.step], target)
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
			for _, target := range wantedSteps[step.index] {
				if generation.input != uint64(target.input) || generation.cached != uint64(target.cached) || generation.output != uint64(target.output) {
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

func readNativeGeneration(source *sourceSQLite, rowID int64, wanted map[int64][]nativeTimingTarget) (nativeGeneration, error) {
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
		needed = needed || len(wanted[index]) > 0
	}
	if needed {
		err = generation.readTiming(blob)
	}
	return generation, err
}
