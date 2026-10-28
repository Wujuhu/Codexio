package backend

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/klauspost/compress/zstd"
)

// Reuse the seekable collector/recovery/journey readers. Decompression is only
// an I/O adapter: source keys and all offsets still identify the original JSONL.
const rolloutCacheLimit int64 = 1 << 30

var rolloutCacheMu queryWorkLock

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
func openRollout(path string, contexts ...context.Context) (*os.File, error) {
	path = resolveRolloutPath(path)
	if !compressedRollout(path) {
		return os.Open(path)
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
	key := HashString(fmt.Sprintf("%s:%s:%d:%d", path, identity, stamp.Size(), stamp.ModTime().UnixNano()))
	destination := filepath.Join(folder, key+".jsonl")
	if file, e := os.Open(destination); e == nil {
		if info, e := file.Stat(); e == nil && time.Since(info.ModTime()) > time.Minute {
			_ = os.Chtimes(destination, time.Now(), time.Now())
		}
		return file, nil
	}
	temporary, err := os.CreateTemp(folder, "rollout-*.tmp")
	if err != nil {
		return nil, err
	}
	defer func() { temporary.Close(); os.Remove(temporary.Name()) }()
	decoder, err := zstd.NewReader(rolloutContextReader{ctx, source}, zstd.WithDecoderConcurrency(1), zstd.WithDecoderMaxMemory(128<<20))
	if err != nil {
		return nil, err
	}
	defer decoder.Close()
	written, err := io.Copy(temporary, io.LimitReader(rolloutContextReader{ctx, decoder}, rolloutCacheLimit+1))
	if err != nil {
		return nil, err
	}
	if written > rolloutCacheLimit {
		return nil, errors.New("decompressed rollout exceeds cache limit")
	}
	after, err := os.Stat(path)
	if err != nil || !os.SameFile(stamp, after) || stamp.Size() != after.Size() || !stamp.ModTime().Equal(after.ModTime()) {
		return nil, errors.New("rollout changed during decompression")
	}
	if err = temporary.Close(); err != nil {
		return nil, err
	}
	if err = os.Rename(temporary.Name(), destination); err != nil {
		if _, e := os.Stat(destination); e != nil {
			return nil, err
		}
	}
	trimRolloutCache(folder, destination)
	return os.Open(destination)
}
func trimRolloutCache(folder, keeping string) {
	type entry struct {
		path string
		info os.FileInfo
	}
	items, _ := os.ReadDir(folder)
	files := []entry{}
	var bytes int64
	for _, item := range items {
		info, err := item.Info()
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		path := filepath.Join(folder, item.Name())
		if strings.HasSuffix(item.Name(), ".tmp") && time.Since(info.ModTime()) > time.Hour {
			_ = os.Remove(path)
		}
		if !strings.HasSuffix(item.Name(), ".jsonl") {
			continue
		}
		bytes += info.Size()
		files = append(files, entry{path, info})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].info.ModTime().Before(files[j].info.ModTime()) })
	count := len(files)
	for _, file := range files {
		if file.path != keeping && (bytes > rolloutCacheLimit || count > 64) {
			if os.Remove(file.path) == nil {
				bytes -= file.info.Size()
				count--
			}
		}
	}
}
