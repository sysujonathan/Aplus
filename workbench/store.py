"""The only owner of the new product's database schema; never opens old databases."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import math
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_runtime_root(root=None):
    """Return one stable per-user data home without requiring setup."""
    configured = root or os.environ.get("A_WORKBENCH_HOME")
    if configured:
        return Path(configured).expanduser().resolve()

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    preferred = (base / "Aplus" / "runtime").resolve()

    # Preserve data created by releases that stored runtime beside the code.
    # A fresh checkout has no database here and therefore uses the stable
    # per-user location above.
    legacy = (ROOT / "runtime").resolve()
    if not (preferred / "workbench.sqlite3").exists() and (legacy / "workbench.sqlite3").exists():
        return legacy
    return preferred


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, root=None):
        self._files_lock = threading.RLock()
        self.root = resolve_runtime_root(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workbench.sqlite3"
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO meta VALUES('schema_version','1');
            CREATE TABLE IF NOT EXISTS datasets(
                id TEXT PRIMARY KEY, code TEXT NOT NULL, timeframe TEXT NOT NULL,
                source TEXT NOT NULL, adjustment TEXT NOT NULL, start TEXT, end TEXT,
                rows INTEGER NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                created TEXT NOT NULL, finished TEXT, progress INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0, message TEXT DEFAULT '', spec TEXT NOT NULL,
                result TEXT DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
                job_id TEXT, action TEXT NOT NULL, path TEXT, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations(
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, code TEXT NOT NULL,
                strategy TEXT NOT NULL, version TEXT NOT NULL, timeframe TEXT NOT NULL,
                asof TEXT NOT NULL, setup_date TEXT NOT NULL, dataset_id TEXT NOT NULL,
                payload TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans(
                id TEXT PRIMARY KEY, observation_id TEXT NOT NULL,
                code TEXT NOT NULL, strategy TEXT NOT NULL, timeframe TEXT NOT NULL,
                setup_date TEXT NOT NULL, state TEXT NOT NULL, entry REAL, stop REAL,
                target REAL, quantity INTEGER, notes TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plan_history(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
                time TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS registry(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, module TEXT NOT NULL,
                class_name TEXT NOT NULL, state TEXT NOT NULL, sha256 TEXT NOT NULL,
                params TEXT NOT NULL, timeframes TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS validations(
                id TEXT PRIMARY KEY, strategy TEXT NOT NULL, version TEXT NOT NULL,
                job_id TEXT NOT NULL, passed INTEGER NOT NULL, report TEXT NOT NULL,
                created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS observations_code ON observations(code,strategy,timeframe,asof);
            CREATE INDEX IF NOT EXISTS events_job ON events(job_id,seq);
            """)
            version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            if version not in {'1', '2', '3', '4'}:
                raise ValueError('数据库版本与程序不匹配，请先完成升级迁移；不会自动清理数据')
            db.execute("CREATE TABLE IF NOT EXISTS sync_coverage(code TEXT PRIMARY KEY, "
                       "dataset_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS scan_cache(key TEXT PRIMARY KEY, signal TEXT NOT NULL, created TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS watchlist(code TEXT PRIMARY KEY, observation_id TEXT NOT NULL, "
                       "notes TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL)")
            db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount

    def event(self, job, action, path="", **detail):
        self.execute("INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)",
                     (now(), job, action, str(path), dumps(detail)))

    def write_artifact(self, relative, content: bytes, job=None):
        # Windows non-strict resolve can return an extended path while another
        # thread creates the parent. Serialize path creation and atomic writes;
        # downloads remain parallel and the containment check stays mandatory.
        with self._files_lock:
            return self._write_artifact(relative, content, job)

    def _write_artifact(self, relative, content: bytes, job=None):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"文件必须保存在新版 A 的运行目录中：{path}（根目录 {self.root}）")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() == content:
            self.event(job, "复用已有文件", path, bytes=len(content))
            return path
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        with tmp.open("xb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        self.event(job, "写入文件", path, bytes=len(content), sha256=digest(content))
        return path

    def read_artifact(self, relative, job=None):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("不允许越过运行目录读取文件")
        data = path.read_bytes()
        self.event(job, "读取文件", path, bytes=len(data))
        return data

    def save_plan(self, observation_id, state, entry, stop, target, quantity, notes):
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>=0 for v in (entry,stop,target,quantity)):
            raise ValueError('价格和股数必须是有限的非负数')
        if state not in {"观察", "计划交易", "已手工入场", "已手工退出", "忽略"}:
            raise ValueError("未知的计划状态")
        obs = self.rows("SELECT * FROM observations WHERE id=?", (observation_id,))
        if not obs:
            raise ValueError("原始信号不存在")
        o = obs[0]
        if state in {"计划交易", "已手工入场"} and not (0 < stop < entry < target and quantity > 0):
            raise ValueError("做多计划需满足：0 < 止损 < 入场 < 目标，且股数大于 0")
        identity = "|".join(str(o[k]) for k in ("code", "strategy", "version", "timeframe", "setup_date"))
        pid = digest(identity.encode())[:24]
        values = (pid, observation_id, o['code'], o['strategy'], o['timeframe'], o['setup_date'],
                  state, entry, stop, target, quantity, notes, now())
        with self.connect() as db:
            db.execute("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                       "observation_id=excluded.observation_id,state=excluded.state,entry=excluded.entry,"
                       "stop=excluded.stop,target=excluded.target,quantity=excluded.quantity,"
                       "notes=excluded.notes,updated=excluded.updated", values)
            db.execute("INSERT INTO plan_history(plan_id,time,payload) VALUES(?,?,?)", (pid, now(), dumps(values)))
        self.event(None, "保存人工计划", self.path, plan_id=pid, state=state)
        return pid

    def watch(self, observation_id):
        rows = self.rows('SELECT code FROM observations WHERE id=?', (observation_id,))
        if not rows:
            raise ValueError('原始信号不存在')
        code = rows[0]['code']
        self.execute('INSERT INTO watchlist VALUES(?,?,?,1,?,?) ON CONFLICT(code) DO UPDATE SET '
                     'observation_id=excluded.observation_id,active=1,updated=excluded.updated',
                     (code, observation_id, '', now(), now()))
        self.event(None, '加入关注', code=code, observation=observation_id)

    def update_watch(self, code, notes, active=True):
        changed = self.execute('UPDATE watchlist SET notes=?,active=?,updated=? WHERE code=?',
                               (notes, int(active), now(), code))
        if not changed:
            raise ValueError('关注记录不存在')
        self.event(None, '更新关注' if active else '结束关注', code=code, notes=notes)
