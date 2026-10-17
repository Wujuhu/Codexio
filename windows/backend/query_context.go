package backend

import (
	"context"
	"database/sql"
	"errors"
	"sync"
	"time"
)

// Like requestMediaCache.work, the gate is bounded and can abandon a wait.
// Existing collector callers retain Lock/Unlock and one owner at a time.
type queryWorkLock struct {
	once sync.Once
	gate chan struct{}
}

func (m *queryWorkLock) LockContext(ctx context.Context) error {
	m.once.Do(func() { m.gate = make(chan struct{}, 1) })
	select {
	case m.gate <- struct{}{}:
		if err := ctx.Err(); err != nil {
			m.Unlock()
			return err
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (m *queryWorkLock) Lock()   { _ = m.LockContext(context.Background()) }
func (m *queryWorkLock) Unlock() { <-m.gate }

func queryContext(contexts []context.Context) context.Context {
	if len(contexts) > 0 && contexts[0] != nil {
		return contexts[0]
	}
	return context.Background()
}

// Reuse the same SQLite pool, caches and Store identity. The context follows
// nested reads and recovery transactions, including waiting for a connection.
type queryReader struct {
	*sql.DB
	ctx context.Context
}

func (s *Store) readDB(contexts []context.Context) queryReader {
	return queryReader{s.db, queryContext(contexts)}
}
func (r queryReader) Query(statement string, args ...any) (*sql.Rows, error) {
	return r.DB.QueryContext(r.ctx, statement, args...)
}
func (r queryReader) QueryRow(statement string, args ...any) *sql.Row {
	return r.DB.QueryRowContext(r.ctx, statement, args...)
}
func (r queryReader) Exec(statement string, args ...any) (sql.Result, error) {
	return r.DB.ExecContext(r.ctx, statement, args...)
}
func (r queryReader) Begin() (*sql.Tx, error) { return r.DB.BeginTx(r.ctx, nil) }

func (s *Service) readContext(parent context.Context) (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithTimeout(parent, 30*time.Second)
	stop := func() bool { return false }
	if s.ctx != nil {
		stop = context.AfterFunc(s.ctx, cancel)
	}
	return ctx, func() { stop(); cancel() }
}
func queryError(ctx context.Context, err error) error {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return errors.New("数据读取超时，请重试")
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return err
}
