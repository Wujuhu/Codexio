-- Local preparation only. Apply explicitly after 0002 and before this Worker.
-- Safe to repeat: no ALTER TABLE, no rewriting payloads/digests in SQL.
-- Existing rows enter a bounded cleanup queue; the Worker hashes canonical JSON.
CREATE TABLE IF NOT EXISTS payload_retention(
  host TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('dataset','detail')),name TEXT NOT NULL,
  source_revision INTEGER NOT NULL,source_digest TEXT NOT NULL,due REAL,
  PRIMARY KEY(host,kind,name),FOREIGN KEY(host) REFERENCES hosts(id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS payload_retention_due ON payload_retention(due) WHERE due IS NOT NULL;
CREATE TABLE IF NOT EXISTS request_images(
  host TEXT NOT NULL,id TEXT NOT NULL,request_id TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,
  payload TEXT NOT NULL,bytes INTEGER NOT NULL CHECK(bytes>0 AND bytes<=1500000),
  mime TEXT NOT NULL,width INTEGER NOT NULL CHECK(width>0 AND width<=2048),height INTEGER NOT NULL CHECK(height>0 AND height<=2048),
  created INTEGER NOT NULL,expires INTEGER NOT NULL CHECK(expires>created AND expires<=created+259200),orphan_until INTEGER,
  PRIMARY KEY(host,id),FOREIGN KEY(host) REFERENCES hosts(id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS request_images_expires ON request_images(expires);
CREATE INDEX IF NOT EXISTS request_images_orphan_until ON request_images(orphan_until) WHERE orphan_until IS NOT NULL;
-- No image bytes, names, or message text. <=128 identities per host prevent a
-- deleted/expired image from obtaining a fresh three-day window on retry.
CREATE TABLE IF NOT EXISTS image_lifetimes(
  host TEXT NOT NULL,id TEXT NOT NULL,request_id TEXT NOT NULL,created INTEGER NOT NULL,expires INTEGER NOT NULL,forget INTEGER NOT NULL,
  PRIMARY KEY(host,id),FOREIGN KEY(host) REFERENCES hosts(id),
  CHECK(expires>created AND expires<=created+259200),CHECK(forget=created+604800)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS image_lifetimes_forget ON image_lifetimes(forget);
CREATE TRIGGER IF NOT EXISTS datasets_delete_retention AFTER DELETE ON datasets BEGIN
  DELETE FROM payload_retention WHERE host=OLD.host AND kind='dataset' AND name=OLD.dataset;
END;
CREATE TRIGGER IF NOT EXISTS request_details_delete_media AFTER DELETE ON request_details BEGIN
  DELETE FROM payload_retention WHERE host=OLD.host AND kind='detail' AND name=OLD.id;
  DELETE FROM request_images WHERE host=OLD.host AND request_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS request_details_remove_media AFTER UPDATE OF payload ON request_details BEGIN
  DELETE FROM request_images WHERE host=OLD.host AND request_id=OLD.id
    AND EXISTS(SELECT 1 FROM json_each(OLD.payload,'$.images') ref WHERE json_extract(ref.value,'$.id')=request_images.id)
    AND NOT EXISTS(SELECT 1 FROM json_each(NEW.payload,'$.images') ref
      WHERE json_extract(ref.value,'$.id')=request_images.id AND json_extract(ref.value,'$.availability')='available'
      AND json_extract(ref.value,'$.expires')=request_images.expires AND json_extract(ref.value,'$.mime')=request_images.mime
      AND json_extract(ref.value,'$.width')=request_images.width AND json_extract(ref.value,'$.height')=request_images.height);
END;
INSERT OR IGNORE INTO payload_retention(host,kind,name,source_revision,source_digest,due)
  SELECT host,'dataset',dataset,revision,digest,0 FROM datasets;
INSERT OR IGNORE INTO payload_retention(host,kind,name,source_revision,source_digest,due)
  SELECT host,'detail',id,revision,digest,0 FROM request_details;
