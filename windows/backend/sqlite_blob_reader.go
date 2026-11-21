package backend

import (
	"context"
	"errors"
	"io"
	"unsafe"

	"modernc.org/libc"
	"modernc.org/libc/sys/types"
	sqlite3 "modernc.org/sqlite/lib"
)

// SQLite's incremental BLOB interface avoids materializing the native App's
// prompt-bearing generation records. The pinned driver uses this same C API.
type sourceSQLite struct {
	tls    *libc.TLS
	db     uintptr
	buffer uintptr
	ctx    context.Context
}

func openSourceSQLite(ctx context.Context, path string) (*sourceSQLite, error) {
	source := &sourceSQLite{tls: libc.NewTLS(), ctx: ctx}
	name, err := libc.CString(path)
	if err != nil {
		source.close()
		return nil, err
	}
	const pointerBytes = int(unsafe.Sizeof(uintptr(0)))
	output := source.tls.Alloc(pointerBytes)
	*(*uintptr)(unsafe.Pointer(output)) = 0
	rc := sqlite3.Xsqlite3_open_v2(source.tls, name, output, sqlite3.SQLITE_OPEN_READONLY|sqlite3.SQLITE_OPEN_NOMUTEX, 0)
	source.db = *(*uintptr)(unsafe.Pointer(output))
	source.tls.Free(pointerBytes)
	libc.Xfree(source.tls, name)
	if rc != sqlite3.SQLITE_OK {
		source.close()
		return nil, errors.New("原生计时数据库暂不可读")
	}
	source.buffer = libc.Xmalloc(source.tls, types.Size_t(4096))
	if source.buffer == 0 {
		source.close()
		return nil, errors.New("原生计时读取缓冲不可用")
	}
	sqlite3.Xsqlite3_busy_timeout(source.tls, source.db, 100)
	if err = source.exec("BEGIN"); err != nil {
		source.close()
		return nil, err
	}
	return source, nil
}

func (s *sourceSQLite) close() {
	if s.tls == nil {
		return
	}
	if s.buffer != 0 {
		libc.Xfree(s.tls, s.buffer)
		s.buffer = 0
	}
	if s.db != 0 {
		sqlite3.Xsqlite3_close_v2(s.tls, s.db)
		s.db = 0
	}
	s.tls.Close()
	s.tls = nil
}

func (s *sourceSQLite) prepare(sql string) (uintptr, error) {
	if err := s.ctx.Err(); err != nil {
		return 0, err
	}
	query, err := libc.CString(sql)
	if err != nil {
		return 0, err
	}
	defer libc.Xfree(s.tls, query)
	const pointerBytes = int(unsafe.Sizeof(uintptr(0)))
	output := s.tls.Alloc(pointerBytes)
	defer s.tls.Free(pointerBytes)
	*(*uintptr)(unsafe.Pointer(output)) = 0
	rc := sqlite3.Xsqlite3_prepare_v2(s.tls, s.db, query, -1, output, 0)
	statement := *(*uintptr)(unsafe.Pointer(output))
	if rc != sqlite3.SQLITE_OK {
		if statement != 0 {
			sqlite3.Xsqlite3_finalize(s.tls, statement)
		}
		return 0, errors.New("原生计时索引暂不可读")
	}
	return statement, nil
}

func (s *sourceSQLite) exec(sql string) error {
	statement, err := s.prepare(sql)
	if err != nil {
		return err
	}
	defer sqlite3.Xsqlite3_finalize(s.tls, statement)
	if sqlite3.Xsqlite3_step(s.tls, statement) != sqlite3.SQLITE_DONE {
		return errors.New("原生计时读取事务暂不可用")
	}
	return nil
}

func (s *sourceSQLite) blob(table, column string, rowID int64) (*sourceBlob, error) {
	if err := s.ctx.Err(); err != nil {
		return nil, err
	}
	names := make([]uintptr, 0, 3)
	defer func() {
		for _, name := range names {
			libc.Xfree(s.tls, name)
		}
	}()
	for _, value := range []string{"main", table, column} {
		name, err := libc.CString(value)
		if err != nil {
			return nil, err
		}
		names = append(names, name)
	}
	const pointerBytes = int(unsafe.Sizeof(uintptr(0)))
	output := s.tls.Alloc(pointerBytes)
	defer s.tls.Free(pointerBytes)
	*(*uintptr)(unsafe.Pointer(output)) = 0
	rc := sqlite3.Xsqlite3_blob_open(s.tls, s.db, names[0], names[1], names[2], rowID, 0, output)
	handle := *(*uintptr)(unsafe.Pointer(output))
	if rc != sqlite3.SQLITE_OK {
		if handle != 0 {
			sqlite3.Xsqlite3_blob_close(s.tls, handle)
		}
		return nil, errors.New("原生计时片段暂不可读")
	}
	return &sourceBlob{source: s, handle: handle, size: int64(sqlite3.Xsqlite3_blob_bytes(s.tls, handle))}, nil
}

type sourceBlob struct {
	source *sourceSQLite
	handle uintptr
	size   int64
}

func (b *sourceBlob) close() {
	if b.handle != 0 {
		sqlite3.Xsqlite3_blob_close(b.source.tls, b.handle)
		b.handle = 0
	}
}

func (b *sourceBlob) ReadAt(data []byte, offset int64) (int, error) {
	if offset < 0 || offset > b.size {
		return 0, io.EOF
	}
	read := 0
	for read < len(data) && offset < b.size {
		if err := b.source.ctx.Err(); err != nil {
			return read, err
		}
		count := min(len(data)-read, 4096, int(b.size-offset))
		rc := sqlite3.Xsqlite3_blob_read(b.source.tls, b.handle, b.source.buffer, int32(count), int32(offset))
		if rc != sqlite3.SQLITE_OK {
			return read, errors.New("原生计时片段已变化")
		}
		copy(data[read:read+count], unsafe.Slice((*byte)(unsafe.Pointer(b.source.buffer)), count))
		read += count
		offset += int64(count)
	}
	if read < len(data) {
		return read, io.EOF
	}
	return read, nil
}
