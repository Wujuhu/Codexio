CREATE TABLE IF NOT EXISTS hosts(id TEXT PRIMARY KEY,writer_hash TEXT NOT NULL,name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS readers(host TEXT NOT NULL,id TEXT NOT NULL,token_hash TEXT NOT NULL,PRIMARY KEY(host,id),FOREIGN KEY(host) REFERENCES hosts(id));
CREATE TABLE IF NOT EXISTS datasets(host TEXT NOT NULL,dataset TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,updated INTEGER NOT NULL,PRIMARY KEY(host,dataset),FOREIGN KEY(host) REFERENCES hosts(id));
CREATE TABLE IF NOT EXISTS invites(hash TEXT PRIMARY KEY,expires INTEGER NOT NULL,host TEXT);
CREATE TABLE IF NOT EXISTS budget(day TEXT PRIMARY KEY,n INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS invites_host ON invites(host);
CREATE INDEX IF NOT EXISTS invites_expires ON invites(expires);
CREATE INDEX IF NOT EXISTS datasets_updated ON datasets(updated);
-- One primary B-tree plus one expiry index; host ranges contain <=64 records.
CREATE TABLE IF NOT EXISTS request_details(host TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,bytes INTEGER NOT NULL CHECK(bytes>0 AND bytes<=1048576),started REAL NOT NULL,expires INTEGER NOT NULL,PRIMARY KEY(host,id),FOREIGN KEY(host) REFERENCES hosts(id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS request_details_expires ON request_details(expires);
