-- Initial DDL from de647de:workbench/store.py, before later trade tables.
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES('schema_version','1');
CREATE TABLE datasets(id TEXT PRIMARY KEY, code TEXT NOT NULL, timeframe TEXT NOT NULL,
source TEXT NOT NULL, adjustment TEXT NOT NULL, start TEXT, end TEXT, rows INTEGER NOT NULL,
path TEXT NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
created TEXT NOT NULL, finished TEXT, progress INTEGER DEFAULT 0,total INTEGER DEFAULT 0,
message TEXT DEFAULT '',spec TEXT NOT NULL,result TEXT DEFAULT '{}');
CREATE TABLE observations(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,code TEXT NOT NULL,
strategy TEXT NOT NULL,version TEXT NOT NULL,timeframe TEXT NOT NULL,asof TEXT NOT NULL,
setup_date TEXT NOT NULL,dataset_id TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE plans(id TEXT PRIMARY KEY,observation_id TEXT NOT NULL,code TEXT NOT NULL,
strategy TEXT NOT NULL,timeframe TEXT NOT NULL,setup_date TEXT NOT NULL,state TEXT NOT NULL,
entry REAL,stop REAL,target REAL,quantity INTEGER,notes TEXT NOT NULL,updated TEXT NOT NULL);
