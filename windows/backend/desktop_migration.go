package backend

import (
	"errors"
	"os"
	"path/filepath"
)

// These are disposable copies and credentials created by the retired phone
// feature. Request archives and original media remain part of the ledger.
func retireMobileFiles(directory string) error {
	systemConfigMu.Lock()
	defer systemConfigMu.Unlock()
	var problems []error
	for _, name := range []string{"settings.json", "analytics_settings.json"} {
		path := filepath.Join(directory, name)
		value, err := systemReadJSON(path, 4<<20)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			problems = append(problems, err)
			continue
		}
		if _, exists := value["mobile_sync_enabled"]; exists {
			delete(value, "mobile_sync_enabled")
			if err = WriteJSON(path, value); err != nil {
				problems = append(problems, err)
			}
		}
	}
	for _, name := range []string{"mobile-host-win.dat", "mobile-projection-win.json"} {
		if err := os.Remove(filepath.Join(directory, name)); err != nil && !errors.Is(err, os.ErrNotExist) {
			problems = append(problems, err)
		}
	}
	return errors.Join(problems...)
}

func (s *Store) retireMobileSchema() error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, table := range []string{"usage_priced_calls", "usage_request_groups"} {
		for _, action := range []string{"insert", "update", "delete"} {
			if _, err = tx.Exec("DROP TRIGGER IF EXISTS mobile_revision_" + table + "_" + action); err != nil {
				return err
			}
		}
	}
	if _, err = tx.Exec(`DROP TABLE IF EXISTS mobile_image_outbox;
		DROP TABLE IF EXISTS mobile_detail_outbox;
		DELETE FROM usage_revisions WHERE kind IN ('mobile:codex','mobile:claude_code');`); err != nil {
		return err
	}
	return tx.Commit()
}
