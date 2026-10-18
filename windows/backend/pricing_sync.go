package backend

import "context"

// Approved official prices are bundled. Refresh never downloads or rewrites rates.
func (s *Store) syncPrices(ctx context.Context, force bool) error { return nil }
func (s *Store) RefreshPrices(ctx context.Context) error          { return nil }
