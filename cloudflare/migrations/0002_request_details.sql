-- Apply explicitly before deploying request-details-v1. Safe to repeat.
CREATE INDEX IF NOT EXISTS invites_host ON invites(host);
CREATE INDEX IF NOT EXISTS invites_expires ON invites(expires);
CREATE INDEX IF NOT EXISTS datasets_updated ON datasets(updated);
CREATE TABLE IF NOT EXISTS request_details(host TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,bytes INTEGER NOT NULL CHECK(bytes>0 AND bytes<=1048576),started REAL NOT NULL,expires INTEGER NOT NULL,PRIMARY KEY(host,id),FOREIGN KEY(host) REFERENCES hosts(id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS request_details_expires ON request_details(expires);
