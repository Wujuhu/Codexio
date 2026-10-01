package backend

import (
	"context"
	"database/sql"
	"errors"
	"time"
)

// Persist the parent as well as the image bytes: cloud needs the acknowledged
// detail reference first, and the display cache is free to drop either request.
func (m *MobileHost) queueImageDetail(ctx context.Context, id, canonical, source string, envelope Row, expires, imageRetry int64, images []Row) error {
	imageExpires := int64(0)
	for _, image := range images {
		imageExpires = max(imageExpires, ValueInt(image["expires"]))
	}
	if imageExpires <= time.Now().Unix() {
		var previous int64
		err := m.store.db.QueryRowContext(ctx, "SELECT image_expires FROM mobile_detail_outbox WHERE id=?", id).Scan(&previous)
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if previous <= time.Now().Unix() {
			_, err = m.store.db.ExecContext(ctx, "DELETE FROM mobile_detail_outbox WHERE id=?", id)
			return err
		}
		// Send a no-image replacement once instead of retrying its old snapshot.
		// Its original image deadline is preserved, never renewed here.
		imageExpires, imageRetry = previous, 0
	}
	payload := []byte(ValueString(envelope["payload"]))
	tx, err := m.store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var count, size, priorSize int
	var priorDigest string
	err = tx.QueryRow("SELECT bytes,digest FROM mobile_detail_outbox WHERE id=?", id).Scan(&priorSize, &priorDigest)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if err = tx.QueryRow("SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM mobile_detail_outbox").Scan(&count, &size); err != nil {
		return err
	}
	if count >= 2048 && priorDigest == "" {
		return errMobileImageCapacity
	}
	// A bounded body cache may apply backpressure; the lightweight canonical
	// owner/deadline/retry record still survives, and pending image bytes stay pinned.
	var stored any = payload
	bytes := len(payload)
	if size-priorSize+bytes > mobileImageCacheBytes {
		stored, bytes = nil, 0
	}
	_, err = tx.Exec(`INSERT INTO mobile_detail_outbox(id,canonical,source,digest,payload,bytes,revision,expires,image_expires,image_retry_at,updated)
 VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET canonical=excluded.canonical,source=excluded.source,digest=excluded.digest,
 payload=excluded.payload,bytes=excluded.bytes,revision=max(mobile_detail_outbox.revision,excluded.revision),
 expires=excluded.expires,image_expires=excluded.image_expires,image_retry_at=excluded.image_retry_at,
 retry_at=CASE WHEN mobile_detail_outbox.digest=excluded.digest THEN mobile_detail_outbox.retry_at ELSE 0 END,updated=excluded.updated`,
		id, canonical, source, ValueString(envelope["digest"]), stored, bytes, ValueInt(envelope["revision"]), expires, imageExpires, imageRetry, time.Now().Unix())
	if err != nil {
		return err
	}
	return tx.Commit()
}

func (m *MobileHost) canonicalMobileRequest(id string) string {
	m.mu.Lock()
	canonical := m.requests[id]
	m.mu.Unlock()
	if canonical == "" {
		_ = m.store.db.QueryRow("SELECT canonical FROM mobile_detail_outbox WHERE id=? AND expires>? AND image_expires>?", id, time.Now().Unix(), time.Now().Unix()).Scan(&canonical)
	}
	if canonical != "" && HashString(canonical) != id {
		return ""
	}
	return canonical
}

func (m *MobileHost) pendingImageDetails(ctx context.Context, images bool, limit int) ([]Row, error) {
	rows, err := m.store.db.QueryContext(ctx, `SELECT o.id,o.source,o.digest,o.payload,o.revision,g.data FROM mobile_detail_outbox o
 JOIN usage_request_groups g ON g.id=o.canonical WHERE o.expires>? AND o.image_expires>? AND o.retry_at<=?
 AND (o.sent_digest<>o.digest OR o.sent_images<>?) AND g.record_kind='user_request' AND g.is_subagent=0 AND `+mobileLocalGroup+`
 ORDER BY o.updated DESC,o.id LIMIT ?`, time.Now().Unix(), time.Now().Unix(), time.Now().Unix(), boolInt(images), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Row{}
	for rows.Next() {
		var id, source, digest, current string
		var payload []byte
		var revision int64
		if err := rows.Scan(&id, &source, &digest, &payload, &revision, &current); err != nil {
			return nil, err
		}
		item := Row{"id": id, "refresh": len(payload) == 0 || source != mobileDetailSource(dataRow(current))}
		if !ValueBool(item["refresh"]) {
			item["envelope"] = Row{"dataset": "detail-" + id, "revision": revision, "digest": digest, "payload": string(payload)}
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (m *MobileHost) deferImageDetail(ctx context.Context, id string, seconds int64) {
	_, _ = m.store.db.ExecContext(ctx, "UPDATE mobile_detail_outbox SET retry_at=? WHERE id=?", time.Now().Unix()+seconds, id)
	m.mu.Lock()
	m.detailDeferred[id] = time.Now().Unix() + seconds
	m.mu.Unlock()
}

func (m *MobileHost) acknowledgeImageDetail(ctx context.Context, id, digest string, images bool, revision int64) error {
	_, err := m.store.db.ExecContext(ctx, `UPDATE mobile_detail_outbox SET sent_digest=?,sent_images=?,retry_at=0,revision=max(revision,?) WHERE id=? AND digest=?`, digest, boolInt(images), revision, id, digest)
	return err
}
