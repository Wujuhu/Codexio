package backend

// Durable request bodies share the ledger transaction. The plaintext table is
// only a hot cache; its eviction never removes the compressed history.
import (
	"bytes"
	"compress/zlib"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

const messageArchiveVersion = 1

type messageQuery interface{ QueryRow(string, ...any) *sql.Row }
type messageQueryFunc func(string, ...any) *sql.Row

func (f messageQueryFunc) QueryRow(q string, args ...any) *sql.Row { return f(q, args...) }

func readMessage(q messageQuery, id string) (raw, digest string, revision int64, err error) {
	err = q.QueryRow("SELECT data,digest,revision FROM usage_request_messages WHERE id=?", id).Scan(&raw, &digest, &revision)
	if err == nil {
		return
	}
	if err != sql.ErrNoRows {
		return
	}
	var payload []byte
	var checksum, codec string
	var size int64
	err = q.QueryRow("SELECT payload,checksum,codec,size,digest,revision FROM usage_request_archives WHERE id=?", id).Scan(&payload, &checksum, &codec, &size, &digest, &revision)
	if err == sql.ErrNoRows {
		err = nil
		return
	}
	if err != nil {
		return
	}
	if codec != "zlib-json-v1" || size < 0 {
		err = errors.New("unsupported request archive")
		return
	}
	var reader io.ReadCloser
	reader, err = zlib.NewReader(bytes.NewReader(payload))
	if err != nil {
		return
	}
	defer reader.Close()
	var decoded []byte
	decoded, err = io.ReadAll(io.LimitReader(reader, size+1))
	if err != nil {
		return
	}
	if int64(len(decoded)) != size || HashString(string(decoded)) != checksum {
		err = errors.New("request archive checksum mismatch")
		return
	}
	raw = string(decoded)
	return
}
func archiveMessage(tx *sql.Tx, id, raw, digest string, revision int64) error {
	checksum := HashString(raw)
	var previous string
	if tx.QueryRow("SELECT checksum FROM usage_request_archives WHERE id=?", id).Scan(&previous) == nil && previous == checksum {
		return nil
	}
	var compressed bytes.Buffer
	writer, err := zlib.NewWriterLevel(&compressed, zlib.BestSpeed)
	if err != nil {
		return err
	}
	if _, err = writer.Write([]byte(raw)); err != nil {
		writer.Close()
		return err
	}
	if err = writer.Close(); err != nil {
		return err
	}
	_, err = tx.Exec(`INSERT INTO usage_request_archives(id,codec,payload,checksum,digest,revision,size) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET codec=excluded.codec,payload=excluded.payload,checksum=excluded.checksum,digest=excluded.digest,revision=excluded.revision,size=excluded.size`, id, "zlib-json-v1", compressed.Bytes(), checksum, digest, revision, len(raw))
	return err
}
func trimMessageCache(tx *sql.Tx) error {
	_, err := tx.Exec(`DELETE FROM usage_request_messages WHERE id IN (SELECT id FROM (SELECT id,row_number() OVER(ORDER BY accessed DESC,id) n,sum(size) OVER(ORDER BY accessed DESC,id) bytes FROM usage_request_messages) WHERE n>512 OR bytes>67108864) AND EXISTS(SELECT 1 FROM usage_request_archives a WHERE a.id=usage_request_messages.id AND a.digest=usage_request_messages.digest AND a.revision=usage_request_messages.revision)`)
	return err
}
func (s *Store) migrateMessageArchives() error {
	// Migrate only still-cached legacy messages. Eviction is gated on a durable
	// archive; older evicted bodies are recovered once from their source cursor.
	rows, err := s.db.Query(`SELECT id,data,digest,revision FROM usage_request_messages m WHERE NOT EXISTS(SELECT 1 FROM usage_request_archives a WHERE a.id=m.id) LIMIT 64`)
	if err != nil {
		return err
	}
	type item struct {
		id, raw, digest string
		revision        int64
	}
	items := []item{}
	for rows.Next() {
		var x item
		if err = rows.Scan(&x.id, &x.raw, &x.digest, &x.revision); err != nil {
			rows.Close()
			return err
		}
		items = append(items, x)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if len(items) == 0 {
		return nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, x := range items {
		if err = saveMessage(tx, x.id, dataRow(x.raw)); err != nil {
			return fmt.Errorf("archive request: %w", err)
		}
	}
	if err = trimMessageCache(tx); err != nil {
		return err
	}
	return tx.Commit()
}

func archiveMessageMedia(tx *sql.Tx, value Row) error {
	if len(ValueRows(value["attachments"]))+len(ValueRows(value["final_attachments"]))+len(ValueRows(value["generated_attachments"])) == 0 {
		return nil
	}
	var seq int
	var name, path string
	if err := tx.QueryRow("PRAGMA database_list").Scan(&seq, &name, &path); err != nil {
		return err
	}
	if path == "" {
		return nil
	}
	root := filepath.Dir(path)
	destination := filepath.Join(root, "request-history-media")
	for _, field := range []string{"attachments", "final_attachments", "generated_attachments"} {
		for _, item := range ValueRows(value[field]) {
			source := dataString(item, "path")
			name := filepath.Base(source)
			if !requestOwnedMediaName.MatchString(name) || (!strings.EqualFold(filepath.Dir(source), filepath.Join(root, "request-media")) && !strings.EqualFold(filepath.Dir(source), filepath.Join(root, "request-media-mock"))) {
				continue
			}
			target := filepath.Join(destination, name)
			if _, err := os.Stat(target); os.IsNotExist(err) {
				info, err := os.Lstat(source)
				if os.IsNotExist(err) {
					continue
				}
				if err != nil {
					return err
				}
				if !info.Mode().IsRegular() {
					return errors.New("invalid message image source")
				}
				data, err := os.ReadFile(source)
				if err != nil {
					return err
				}
				if requestBytesDigest(data) != strings.TrimSuffix(name, filepath.Ext(name)) {
					return errors.New("message image checksum mismatch")
				}
				if err = os.MkdirAll(destination, 0700); err != nil {
					return err
				}
				if err = writePrivateFile(target, data); err != nil {
					return err
				}
			}
			item["path"] = target
		}
	}
	return nil
}

func (s *Store) recordedRollout(root, path string) (int64, Row, bool) {
	root = filepath.Clean(root)
	source := "local"
	for index, candidate := range s.roots() {
		if filepath.Clean(candidate) == root && index > 0 {
			source = "local:" + HashString(strings.ToLower(root))[:16]
		}
	}
	var raw string
	if s.db.QueryRow("SELECT data FROM usage_cursors WHERE key=?", rolloutCursorKey(root, path, source)).Scan(&raw) != nil {
		return 0, nil, false
	}
	cursor := dataRow(raw)
	info, err := os.Stat(path)
	if err != nil || dataString(cursor, "path") != path || dataString(cursor, "identity") != rolloutSourceIdentity(path) || ValueInt(cursor["source_size"]) != info.Size() || ValueInt(cursor["mtime"]) != info.ModTime().UnixNano() {
		return 0, nil, false
	}
	return ValueInt(cursor["size"]), ValueRow(ValueRow(cursor["go_state"])["history_base"]), true
}

func (s *Store) archivedJourneyPage(key, signature string) (Row, error) {
	raw, _, _, err := readMessage(s.db, "journey:"+HashString(key))
	if err != nil {
		return nil, err
	}
	value := dataRow(raw)
	if dataString(value, "source_signature") == signature {
		return ValueRow(value["page"]), nil
	}
	return nil, nil
}
func (s *Store) archiveJourneyPage(key, signature string, page Row) error {
	payload := dataJSON(Row{"source_signature": signature, "page": page})
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = archiveMessage(tx, "journey:"+HashString(key), payload, HashString(payload), 1); err != nil {
		return err
	}
	return tx.Commit()
}
