package backend

// The existing SQLite connection owns a bounded disk outbox. Encoded image
// bodies are loaded one at a time for cloud, or by 64 KiB slice for LAN. Pending
// uploads and their retry/deadline metadata survive UI cache eviction/restarts.
import (
	"context"
	"database/sql"
	"encoding/base64"
	"errors"
	"sort"
	"time"
)

const mobileImageLimit = 1048576
const mobileImageEnvelopeLimit = 1500000
const mobileImageRetention = int64(3 * 86400)
const mobileImageCacheBytes = 32 << 20
const mobileImageCacheRows = 128

var errMobileImageCapacity = errors.New("移动图片缓存容量不足，保留待上传内容并稍后重试")

type mobileImageRecord struct {
	id, request, digest, uploaded string
	bytes, expires, retry         int64
}

func (s *Store) mobileImageSchema() error {
	_, err := s.db.Exec(`CREATE TABLE IF NOT EXISTS mobile_image_outbox(
 id TEXT PRIMARY KEY,request_id TEXT NOT NULL,digest TEXT NOT NULL,
 payload BLOB,bytes INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,expires INTEGER NOT NULL,
 uploaded_digest TEXT NOT NULL DEFAULT '',referenced INTEGER NOT NULL DEFAULT 0,
 retry_at INTEGER NOT NULL DEFAULT 0,accessed INTEGER NOT NULL,metadata TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS mobile_image_outbox_expiry ON mobile_image_outbox(expires);
 CREATE INDEX IF NOT EXISTS mobile_image_outbox_request ON mobile_image_outbox(request_id);
 CREATE INDEX IF NOT EXISTS mobile_image_outbox_retry ON mobile_image_outbox(retry_at,expires);
 CREATE TABLE IF NOT EXISTS mobile_detail_outbox(id TEXT PRIMARY KEY,canonical TEXT NOT NULL,source TEXT NOT NULL,digest TEXT NOT NULL,
 payload BLOB,bytes INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL,expires INTEGER NOT NULL,image_expires INTEGER NOT NULL,
 sent_digest TEXT NOT NULL DEFAULT '',sent_images INTEGER NOT NULL DEFAULT 0,retry_at INTEGER NOT NULL DEFAULT 0,
 image_retry_at INTEGER NOT NULL DEFAULT 0,updated INTEGER NOT NULL);
 CREATE INDEX IF NOT EXISTS mobile_detail_outbox_retry ON mobile_detail_outbox(retry_at,image_expires);
 CREATE INDEX IF NOT EXISTS mobile_detail_outbox_expiry ON mobile_detail_outbox(image_expires);
 CREATE INDEX IF NOT EXISTS mobile_detail_outbox_canonical ON mobile_detail_outbox(canonical);`)
	return err
}

func (m *MobileHost) queueImage(ctx context.Context, reference, payload Row) error {
	id, request := ValueString(payload["id"]), ValueString(payload["request"])
	created, expires := ValueInt(payload["created"]), ValueInt(payload["expires"])
	now := time.Now().Unix()
	if !mobileIDPattern.MatchString(id) || !mobileIDPattern.MatchString(request) || id != ValueString(reference["id"]) ||
		created <= 0 || created > now+300 || expires <= now || expires > created+mobileImageRetention {
		return errors.New("无效或已过期的移动图片")
	}
	encoded := ValueString(payload["data"])
	if len(encoded) > (mobileImageLimit+2)/3*4 {
		return errors.New("移动图片超过容量限制")
	}
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || len(decoded) == 0 || len(decoded) > mobileImageLimit {
		return errors.New("移动图片编码无效")
	}
	decoded = nil
	body, err := mobileJSON(payload)
	if err != nil || len(body) > mobileImageEnvelopeLimit {
		return errors.New("移动图片 envelope 超过容量限制")
	}
	digest := HashString(string(body))
	tx, err := m.store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var priorDigest string
	var priorBytes int
	err = tx.QueryRow("SELECT digest,bytes FROM mobile_image_outbox WHERE id=?", id).Scan(&priorDigest, &priorBytes)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if priorDigest != "" && priorDigest != digest {
		return errors.New("移动图片身份发生冲突，原始内容已保留")
	}
	if priorBytes > 0 {
		_, err = tx.Exec("UPDATE mobile_image_outbox SET accessed=? WHERE id=?", now, id)
		if err != nil {
			return err
		}
		return tx.Commit()
	}
	var metadataCount int
	if err = tx.QueryRow("SELECT COUNT(*) FROM mobile_image_outbox").Scan(&metadataCount); err != nil {
		return err
	}
	if metadataCount >= 2048 && priorDigest == "" {
		removed, err := tx.Exec("DELETE FROM mobile_image_outbox WHERE id IN (SELECT id FROM mobile_image_outbox WHERE expires<=? ORDER BY expires LIMIT 32)", now)
		if err != nil {
			return err
		}
		count, _ := removed.RowsAffected()
		if int64(metadataCount)-count >= 2048 {
			return errMobileImageCapacity
		}
	}
	// Capacity pressure may evict only acknowledged bytes. Pending bodies and
	// original creation/deadline identities are never removed to make room.
	for {
		var count, size int
		if err = tx.QueryRow("SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM mobile_image_outbox WHERE payload IS NOT NULL").Scan(&count, &size); err != nil {
			return err
		}
		if count < mobileImageCacheRows && size+len(body) <= mobileImageCacheBytes {
			break
		}
		var victim string
		err = tx.QueryRow("SELECT id FROM mobile_image_outbox WHERE payload IS NOT NULL AND uploaded_digest=digest AND id<>? ORDER BY accessed LIMIT 1", id).Scan(&victim)
		if errors.Is(err, sql.ErrNoRows) {
			meta, _ := mobileJSON(reference)
			_, err = tx.Exec(`INSERT INTO mobile_image_outbox(id,request_id,digest,created,expires,retry_at,accessed,metadata)
 VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET retry_at=excluded.retry_at`, id, request, digest, created, expires, now+60, now, string(meta))
			if err != nil {
				return err
			}
			if err = tx.Commit(); err != nil {
				return err
			}
			return errMobileImageCapacity
		}
		if err != nil {
			return err
		}
		if _, err = tx.Exec("UPDATE mobile_image_outbox SET payload=NULL,bytes=0 WHERE id=?", victim); err != nil {
			return err
		}
	}
	meta, _ := mobileJSON(reference)
	_, err = tx.Exec(`INSERT INTO mobile_image_outbox(id,request_id,digest,payload,bytes,created,expires,accessed,metadata)
 VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,bytes=excluded.bytes,retry_at=0,accessed=excluded.accessed
 WHERE mobile_image_outbox.digest=excluded.digest AND mobile_image_outbox.created=excluded.created AND mobile_image_outbox.expires=excluded.expires`,
		id, request, digest, body, len(body), created, expires, now, string(meta))
	if err != nil {
		return err
	}
	return tx.Commit()
}

func (m *MobileHost) queuePreparedImages(ctx context.Context, prepared Row) ([]Row, bool, error) {
	refs := []Row{}
	pending := false
	payloads := ValueRow(prepared["image_payloads"])
	for _, source := range ValueRows(prepared["images"]) {
		ref := CloneRow(source)
		if ValueString(ref["availability"]) == "available" {
			if err := m.queueImage(ctx, ref, ValueRow(payloads[ValueString(ref["id"])])); err != nil {
				if !errors.Is(err, errMobileImageCapacity) {
					return nil, false, err
				}
				ref["availability"] = "unavailable"
				pending = true
			}
		} else if ValueInt(ref["expires"]) > time.Now().Unix() {
			pending = true
		}
		refs = append(refs, ref)
	}
	return refs, pending, nil
}

func (m *MobileHost) imageRecord(ctx context.Context, id string) (mobileImageRecord, error) {
	r := mobileImageRecord{id: id}
	err := m.store.db.QueryRowContext(ctx, `SELECT request_id,digest,bytes,expires,uploaded_digest,retry_at FROM mobile_image_outbox WHERE id=? AND expires>?`, id, time.Now().Unix()).Scan(&r.request, &r.digest, &r.bytes, &r.expires, &r.uploaded, &r.retry)
	return r, err
}

func (m *MobileHost) imageMessage(ctx context.Context, message Row) Row {
	id := ValueString(message["imageID"])
	result := Row{"action": "image", "imageID": id}
	part := ValueInt(message["imagePart"])
	if !mobileIDPattern.MatchString(id) || part < 0 {
		result["error"] = "INVALID"
		return result
	}
	if v, exists := message["imagePart"]; exists {
		if n, ok := ValueFloat(v); !ok || n != float64(part) {
			result["error"] = "INVALID"
			return result
		}
	}
	r, err := m.imageRecord(ctx, id)
	if err == nil && r.bytes == 0 {
		// Acknowledged images may leave the byte cache; use their durable owner
		// to prepare again without accepting a caller-supplied filesystem path.
		_, _ = m.getDetailWithForce(r.request, true, true)
		r, err = m.imageRecord(ctx, id)
	}
	if err != nil || r.bytes == 0 {
		result["error"] = "IMAGE_UNAVAILABLE"
		return result
	}
	count := (r.bytes + mobileDetailChunk - 1) / mobileDetailChunk
	if part >= count {
		result["error"] = "INVALID"
		return result
	}
	var chunk []byte
	err = m.store.db.QueryRowContext(ctx, "SELECT substr(payload,?,?) FROM mobile_image_outbox WHERE id=? AND digest=? AND expires>?", part*mobileDetailChunk+1, mobileDetailChunk, id, r.digest, time.Now().Unix()).Scan(&chunk)
	if err != nil || int64(len(chunk)) != min(int64(mobileDetailChunk), r.bytes-part*mobileDetailChunk) {
		result["error"] = "IMAGE_UNAVAILABLE"
		return result
	}
	result["imagePart"] = part
	result["imageManifest"] = Row{"id": id, "revision": 1, "digest": r.digest, "bytes": r.bytes, "parts": count}
	result["imageChunk"] = base64.StdEncoding.EncodeToString(chunk)
	return result
}

func (m *MobileHost) pendingImages(ctx context.Context, limit int) ([]mobileImageRecord, error) {
	rows, err := m.store.db.QueryContext(ctx, `SELECT id,request_id,digest,bytes,expires,uploaded_digest,retry_at FROM mobile_image_outbox
 WHERE referenced=1 AND payload IS NOT NULL AND uploaded_digest<>digest AND expires>? AND retry_at<=? ORDER BY expires,id LIMIT ?`, time.Now().Unix(), time.Now().Unix(), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []mobileImageRecord{}
	for rows.Next() {
		r := mobileImageRecord{}
		if err := rows.Scan(&r.id, &r.request, &r.digest, &r.bytes, &r.expires, &r.uploaded, &r.retry); err != nil {
			return nil, err
		}
		result = append(result, r)
	}
	return result, rows.Err()
}

func (m *MobileHost) imageEnvelope(ctx context.Context, image mobileImageRecord) (Row, error) {
	var payload []byte
	err := m.store.db.QueryRowContext(ctx, "SELECT payload FROM mobile_image_outbox WHERE id=? AND digest=? AND expires>? AND referenced=1", image.id, image.digest, time.Now().Unix()).Scan(&payload)
	if err != nil {
		return nil, err
	}
	if len(payload) == 0 || len(payload) > mobileImageEnvelopeLimit || HashString(string(payload)) != image.digest {
		return nil, errors.New("移动图片缓存校验失败")
	}
	return Row{"dataset": "image-" + image.id, "revision": 1, "digest": image.digest, "payload": string(payload)}, nil
}

func (m *MobileHost) recordImageReferences(ctx context.Context, request string, refs []Row) error {
	wanted := map[string]bool{}
	for _, ref := range refs {
		if ValueString(ref["availability"]) == "available" && ValueInt(ref["expires"]) > time.Now().Unix() {
			wanted[ValueString(ref["id"])] = true
		}
	}
	tx, err := m.store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	rows, err := tx.Query("SELECT id,referenced FROM mobile_image_outbox WHERE request_id=?", request)
	if err != nil {
		return err
	}
	prior := map[string]bool{}
	for rows.Next() {
		var id string
		var linked int
		if err := rows.Scan(&id, &linked); err != nil {
			rows.Close()
			return err
		}
		prior[id] = linked != 0
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for id, linked := range prior {
		if linked == wanted[id] {
			continue
		}
		// A server may have deleted bytes when a reference disappeared. Its
		// later reappearance must re-upload, even if the content ID is unchanged.
		if _, err = tx.Exec("UPDATE mobile_image_outbox SET referenced=?,uploaded_digest='',retry_at=0 WHERE id=?", boolInt(wanted[id]), id); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (m *MobileHost) refreshImages(ctx context.Context) {
	// This runs on the existing 30-second synchronization worker, no new timer.
	_, _ = m.store.db.ExecContext(ctx, "DELETE FROM mobile_image_outbox WHERE id IN (SELECT id FROM mobile_image_outbox WHERE expires<=? ORDER BY expires LIMIT 32)", time.Now().Unix())
	_, _ = m.store.db.ExecContext(ctx, "DELETE FROM mobile_detail_outbox WHERE id IN (SELECT id FROM mobile_detail_outbox WHERE image_expires<=? OR expires<=? ORDER BY image_expires LIMIT 32)", time.Now().Unix(), time.Now().Unix())
	pending, _ := m.pendingImages(ctx, 1)
	m.mu.Lock()
	supportsImages := m.cloudImages
	m.mu.Unlock()
	parents, _ := m.pendingImageDetails(ctx, supportsImages, 2)
	// Capacity deferrals also live in SQLite; losing a display-cache entry or
	// restarting the host must not lose their request/retry information.
	deferred := []string{}
	rows, err := m.store.db.QueryContext(ctx, `SELECT DISTINCT request_id FROM mobile_image_outbox
 WHERE payload IS NULL AND uploaded_digest='' AND expires>? AND retry_at<=? ORDER BY retry_at LIMIT 2`, time.Now().Unix(), time.Now().Unix())
	if err == nil {
		for rows.Next() {
			var id string
			if rows.Scan(&id) == nil {
				deferred = append(deferred, id)
			}
		}
		rows.Close()
	}
	rows, err = m.store.db.QueryContext(ctx, `SELECT id FROM mobile_detail_outbox WHERE image_retry_at>0 AND image_retry_at<=? AND image_expires>? AND expires>? ORDER BY image_retry_at LIMIT 2`, time.Now().Unix(), time.Now().Unix(), time.Now().Unix())
	if err == nil {
		for rows.Next() {
			var id string
			if rows.Scan(&id) == nil {
				deferred = append(deferred, id)
			}
		}
		rows.Close()
	}
	m.mu.Lock()
	m.imagesPending = m.imagesPending || len(pending) > 0
	m.parentsPending = len(parents) > 0
	wanted := map[string]bool{}
	for _, id := range deferred {
		wanted[id] = true
	}
	for id, detail := range m.details {
		if detail.ImageRetry > 0 && detail.ImageRetry <= time.Now().Unix() && detail.Expires > float64(time.Now().Unix()) {
			wanted[id] = true
		}
	}
	ids := []string{}
	for id := range wanted {
		ids = append(ids, id)
	}
	m.mu.Unlock()
	sort.Strings(ids)
	for _, id := range ids[:min(2, len(ids))] {
		if ctx.Err() != nil {
			return
		}
		_, _ = m.store.db.ExecContext(ctx, "UPDATE mobile_image_outbox SET retry_at=? WHERE request_id=? AND payload IS NULL", time.Now().Add(time.Minute).Unix(), id)
		_, _ = m.store.db.ExecContext(ctx, "UPDATE mobile_detail_outbox SET image_retry_at=? WHERE id=? AND image_retry_at>0", time.Now().Add(time.Minute).Unix(), id)
		m.mu.Lock()
		if detail := m.details[id]; detail != nil {
			detail.ImageRetry = time.Now().Add(time.Minute).Unix()
		}
		m.mu.Unlock()
		_, _ = m.getDetailWithForce(id, true, true)
	}
}
