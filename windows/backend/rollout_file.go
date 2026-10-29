package backend

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/klauspost/compress/zstd"
)

// A decoded file is leased only while an actual reader uses it. Completed
// request bodies live in usage_request_archives, outside this disposable cache.
var rolloutCacheMu queryWorkLock
var rolloutFiles = map[string]*rolloutLease{}
var rolloutCacheCleaned bool

type rolloutLease struct {
	path    string
	readers int
}
type rolloutFile struct {
	*os.File
	release func()
	once    sync.Once
}

func (f *rolloutFile) Close() (err error) {
	f.once.Do(func() {
		err = f.File.Close()
		if f.release != nil {
			f.release()
		}
	})
	return
}
func leaseRollout(key string, entry *rolloutLease) (*rolloutFile, error) {
	f, err := os.Open(entry.path)
	if err != nil {
		return nil, err
	}
	entry.readers++
	return &rolloutFile{File: f, release: func() {
		rolloutCacheMu.Lock()
		defer rolloutCacheMu.Unlock()
		entry.readers--
		if entry.readers == 0 {
			os.Remove(entry.path)
			delete(rolloutFiles, key)
		}
	}}, nil
}
func compressedRollout(path string) bool { return strings.HasSuffix(path, ".jsonl.zst") }
func isRolloutFile(path string) bool {
	return strings.HasSuffix(path, ".jsonl") || compressedRollout(path)
}
func resolveRolloutPath(path string) string {
	if _, err := os.Stat(path); err == nil || !isRolloutFile(path) {
		return path
	}
	alternative := path + ".zst"
	if compressedRollout(path) {
		alternative = strings.TrimSuffix(path, ".zst")
	}
	if _, err := os.Stat(alternative); err == nil {
		return alternative
	}
	return path
}
func rolloutSourceIdentity(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	return ledgerFileIdentity(f)
}
func rolloutCacheDirectory() (string, error) {
	if path := os.Getenv("CODEXIO_ROLLOUT_CACHE_DIR"); path != "" {
		return path, nil
	}
	root, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "Codexio", "Rollouts-v1"), nil
}

type rolloutContextReader struct {
	context.Context
	io.Reader
}

func (r rolloutContextReader) Read(p []byte) (int, error) {
	if err := r.Err(); err != nil {
		return 0, err
	}
	return r.Reader.Read(p)
}
func openRollout(path string, contexts ...context.Context) (*rolloutFile, error) {
	path = resolveRolloutPath(path)
	if !compressedRollout(path) {
		f, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		return &rolloutFile{File: f}, nil
	}
	ctx := context.Background()
	if len(contexts) > 0 && contexts[0] != nil {
		ctx = contexts[0]
	}
	if err := rolloutCacheMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer rolloutCacheMu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	source, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer source.Close()
	stamp, err := source.Stat()
	if err != nil {
		return nil, err
	}
	identity := ledgerFileIdentity(source)
	folder, err := rolloutCacheDirectory()
	if err != nil {
		return nil, err
	}
	if err = os.MkdirAll(folder, 0700); err != nil {
		return nil, err
	}
	if !rolloutCacheCleaned {
		items, _ := os.ReadDir(folder)
		for _, item := range items {
			if !item.IsDir() && (strings.HasSuffix(item.Name(), ".jsonl") || strings.HasSuffix(item.Name(), ".tmp")) {
				_ = os.Remove(filepath.Join(folder, item.Name()))
			}
		}
		rolloutCacheCleaned = true
	}
	key := HashString(fmt.Sprintf("%s:%s:%d:%d", path, identity, stamp.Size(), stamp.ModTime().UnixNano()))
	if entry := rolloutFiles[key]; entry != nil {
		return leaseRollout(key, entry)
	}
	temporary, err := os.CreateTemp(folder, "rollout-*.tmp")
	if err != nil {
		return nil, err
	}
	retained := false
	defer func() {
		temporary.Close()
		if !retained {
			os.Remove(temporary.Name())
		}
	}()
	decoder, err := zstd.NewReader(rolloutContextReader{ctx, source}, zstd.WithDecoderConcurrency(1), zstd.WithDecoderMaxMemory(128<<20))
	if err != nil {
		return nil, err
	}
	defer decoder.Close()
	_, err = io.Copy(temporary, rolloutContextReader{ctx, decoder})
	if err != nil {
		return nil, err
	}
	after, err := os.Stat(path)
	if err != nil || !os.SameFile(stamp, after) || stamp.Size() != after.Size() || !stamp.ModTime().Equal(after.ModTime()) {
		return nil, errors.New("rollout changed during decompression")
	}
	if err = temporary.Close(); err != nil {
		return nil, err
	}
	entry := &rolloutLease{path: temporary.Name()}
	reader, err := leaseRollout(key, entry)
	if err != nil {
		return nil, err
	}
	rolloutFiles[key] = entry
	retained = true
	return reader, nil
}
