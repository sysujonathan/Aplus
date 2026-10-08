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
            if version not in {'1', '2', '3', '4', '5', '6', '7', '8'}:
                raise ValueError('数据库版本与程序不匹配，请先完成升级迁移；不会自动清理数据')
            db.execute("CREATE TABLE IF NOT EXISTS sync_coverage(code TEXT PRIMARY KEY, "
                       "dataset_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS scan_cache(key TEXT PRIMARY KEY, signal TEXT NOT NULL, created TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS watchlist(code TEXT PRIMARY KEY, observation_id TEXT NOT NULL, "
                       "notes TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL)")
            # v5 adds a manual execution ledger.  A market signal, including an
            # H2 TRIGGERED state, never writes here: only explicit trader input
            # may create an execution and therefore a real position.
            db.execute("CREATE TABLE IF NOT EXISTS accounts("
                       "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                       "initial_equity REAL NOT NULL, risk_limit_pct REAL NOT NULL DEFAULT 3, "
                       "per_trade_risk_pct REAL NOT NULL DEFAULT 1, "
                       "max_position_pct REAL NOT NULL DEFAULT 30, "
                       "cash_reserve_pct REAL NOT NULL DEFAULT 10, "
                       "active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL, "
                       "accounting_mode TEXT NOT NULL DEFAULT 'snapshot', current_total_assets REAL, "
                       "broker_account_no TEXT NOT NULL DEFAULT '')")
            db.execute("CREATE TABLE IF NOT EXISTS executions("
                       "id TEXT PRIMARY KEY, account_id TEXT NOT NULL, plan_id TEXT, "
                       "observation_id TEXT, code TEXT NOT NULL, side TEXT NOT NULL, "
                       "trade_time TEXT NOT NULL, price REAL NOT NULL, quantity INTEGER NOT NULL, "
                       "fee REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', "
                       "notes TEXT NOT NULL DEFAULT '', plan_snapshot TEXT NOT NULL DEFAULT '{}', "
                       "created TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS executions_account_time "
                       "ON executions(account_id,trade_time,created)")
            db.execute("CREATE INDEX IF NOT EXISTS executions_account_code "
                       "ON executions(account_id,code,trade_time)")
            account_columns = {row[1] for row in db.execute("PRAGMA table_info(accounts)")}
            if "accounting_mode" not in account_columns:
                db.execute("ALTER TABLE accounts ADD COLUMN accounting_mode TEXT NOT NULL DEFAULT 'snapshot'")
            if "current_total_assets" not in account_columns:
                db.execute("ALTER TABLE accounts ADD COLUMN current_total_assets REAL")
            if "broker_account_no" not in account_columns:
                db.execute("ALTER TABLE accounts ADD COLUMN broker_account_no TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE TABLE IF NOT EXISTS positions("
                       "id TEXT PRIMARY KEY, account_id TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, "
                       "plan_id TEXT, observation_id TEXT, entry REAL NOT NULL, stop REAL NOT NULL, "
                       "tp1 REAL NOT NULL, tp2 REAL, tp3 REAL, status TEXT NOT NULL DEFAULT 'OPEN', "
                       "created TEXT NOT NULL, updated TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS position_fills("
                       "id TEXT PRIMARY KEY, position_id TEXT NOT NULL, side TEXT NOT NULL, "
                       "trade_date TEXT NOT NULL, price REAL NOT NULL, quantity INTEGER NOT NULL, "
                       "fees REAL NOT NULL DEFAULT 0, created TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS closed_trades("
                       "id TEXT PRIMARY KEY, account_id TEXT NOT NULL, position_id TEXT, "
                       "code TEXT NOT NULL, name TEXT NOT NULL, close_date TEXT NOT NULL, "
                       "holding_days INTEGER NOT NULL, pnl REAL NOT NULL, return_pct REAL NOT NULL, "
                       "notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS cash_flows("
                       "id TEXT PRIMARY KEY, account_id TEXT NOT NULL, flow_date TEXT NOT NULL, "
                       "category TEXT NOT NULL, amount REAL NOT NULL, notes TEXT NOT NULL DEFAULT '', "
                       "created TEXT NOT NULL, updated TEXT NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_open_position_per_account_code "
                       "ON positions(account_id,code) WHERE status='OPEN'")
            db.execute("CREATE INDEX IF NOT EXISTS position_fills_position_date "
                       "ON position_fills(position_id,trade_date,created)")
            db.execute("CREATE INDEX IF NOT EXISTS closed_trades_account_date "
                       "ON closed_trades(account_id,close_date)")
            db.execute("CREATE INDEX IF NOT EXISTS cash_flows_account_date "
                       "ON cash_flows(account_id,flow_date)")
            db.execute("UPDATE meta SET value='8' WHERE key='schema_version'")

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

    def backtest_history(self, execution_model, *, include_deleted=False):
        """Stable display identities in existing meta; reports remain immutable."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            counter = db.execute("SELECT value FROM meta WHERE key='backtest_display_counter'").fetchone()
            number = int(counter['value']) if counter else 0
            history = []
            for item in db.execute("SELECT id,status,created,result FROM jobs WHERE kind='backtest' "
                                   "AND status NOT IN ('running','queued') ORDER BY created,rowid").fetchall():
                row = dict(item)
                try:
                    report = json.loads(row['result'])
                except (ValueError, TypeError):
                    continue
                if not isinstance(report, dict) or report.get('execution_model') != execution_model:
                    continue
                key = 'backtest_display:' + row['id']
                saved = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
                if saved:
                    display = json.loads(saved['value'])
                else:
                    number += 1
                    display = dict(number=number, name='', deleted=False)
                    db.execute('INSERT INTO meta VALUES(?,?)', (key, dumps(display)))
                row['display'] = display
                if include_deleted or not display['deleted']:
                    history.append(row)
            db.execute("INSERT INTO meta VALUES('backtest_display_counter',?) ON CONFLICT(key) "
                       "DO UPDATE SET value=excluded.value", (str(number),))
            return list(reversed(history))

    def rename_backtest(self, job, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or any(ord(c)<32 for c in name):
            raise ValueError('名称请使用 1～40 个可见字符')
        self._update_backtest_display(job, name=name.strip())

    def set_backtest_deleted(self, job, deleted=True):
        if type(deleted) is not bool:
            raise ValueError('删除状态必须为布尔值')
        self._update_backtest_display(job, deleted=deleted)

    def _update_backtest_display(self, job, **changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT kind,status FROM jobs WHERE id=?', (job,)).fetchone()
            if not row or row['kind'] != 'backtest' or row['status'] in ('running','queued'):
                raise ValueError('只能管理已经结束或停止的回测')
            key = 'backtest_display:' + job
            saved = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            if not saved:
                raise ValueError('请先加载回测记录')
            display = json.loads(saved['value'])
            display.update(changes)
            db.execute('UPDATE meta SET value=? WHERE key=?', (dumps(display), key))
            db.execute('INSERT INTO events(time,job_id,action,path,detail) VALUES(?,?,?,?,?)',
                       (now(),job,'更新回测显示记录',str(self.path),dumps(changes)))

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

    def list_accounts(self, active_only=True):
        sql = "SELECT * FROM accounts"
        if active_only:
            sql += " WHERE active=1"
        return self.rows(sql + " ORDER BY created,id")

    def save_account(self, name, initial_equity, risk_limit_pct=3.0,
                     per_trade_risk_pct=1.0, max_position_pct=30.0,
                     cash_reserve_pct=10.0, account_id=None, active=True,
                     accounting_mode="snapshot", current_total_assets=None,
                     broker_account_no="", snapshot_reference=None):
        """Create or update a local manual-trading account profile."""
        name = str(name or "").strip()
        values = (initial_equity, risk_limit_pct, per_trade_risk_pct,
                  max_position_pct, cash_reserve_pct)
        if not name:
            raise ValueError("账户名称不能为空")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError("账户资金和风险参数必须是有限数字")
        accounting_mode = str(accounting_mode or "snapshot").strip().lower()
        broker_account_no = str(broker_account_no or "").strip()
        if accounting_mode not in {"snapshot", "history"}:
            raise ValueError("建账方式只能是当前资产快照或历史清仓")
        if initial_equity <= 0:
            raise ValueError("账户资金必须大于 0")
        if current_total_assets not in (None, ""):
            if not isinstance(current_total_assets, (int, float)) or not math.isfinite(current_total_assets) or current_total_assets <= 0:
                raise ValueError("当前总资产必须是大于 0 的有限数字")
            current_total_assets = float(current_total_assets)
        elif accounting_mode == "snapshot":
            current_total_assets = float(initial_equity)
        if not (0 < risk_limit_pct <= 100 and 0 < per_trade_risk_pct <= 100):
            raise ValueError("风险比例必须在 0% 到 100% 之间")
        if not (0 < max_position_pct <= 100 and 0 <= cash_reserve_pct < 100):
            raise ValueError("单票上限需在 0% 到 100% 之间，现金保留需在 0% 到 100% 以内")
        if per_trade_risk_pct > risk_limit_pct:
            raise ValueError("单笔风险不能高于总持仓风险上限")
        if snapshot_reference is not None:
            if not isinstance(snapshot_reference, dict) or not all(
                isinstance(snapshot_reference.get(k), (int, float))
                and math.isfinite(snapshot_reference[k]) for k in ("cash", "invested", "adjustments")
            ):
                raise ValueError("快照现金基准无效")
        stamp = now()
        account_id = account_id or uuid.uuid4().hex[:16]
        with self.connect() as db:
            existing = db.execute("SELECT id FROM accounts WHERE id=?", (account_id,)).fetchone()
            if existing:
                db.execute(
                    "UPDATE accounts SET name=?,initial_equity=?,risk_limit_pct=?,"
                    "per_trade_risk_pct=?,max_position_pct=?,cash_reserve_pct=?,active=?,updated=?,"
                    "accounting_mode=?,current_total_assets=?,broker_account_no=? WHERE id=?",
                    (name, *values, int(bool(active)), stamp, accounting_mode,
                     current_total_assets, broker_account_no, account_id),
                )
            else:
                db.execute(
                    "INSERT INTO accounts(id,name,initial_equity,risk_limit_pct,per_trade_risk_pct,"
                    "max_position_pct,cash_reserve_pct,active,created,updated,accounting_mode,current_total_assets,"
                    "broker_account_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (account_id, name, *values, int(bool(active)), stamp, stamp,
                     accounting_mode, current_total_assets, broker_account_no),
                )
            db.execute("DELETE FROM meta WHERE key=?", ("holding_cash_reference:" + account_id,))
            if accounting_mode == "snapshot" and snapshot_reference is not None:
                db.execute("INSERT INTO meta VALUES(?,?)",
                           ("holding_cash_reference:" + account_id, dumps(snapshot_reference)))
        self.event(None, "保存交易账户", self.path, account_id=account_id, name=name)
        return account_id

    def holding_auto_quotes(self, enabled=None):
        """Local UI preference only; no migration and no trading/audit writes."""
        if enabled is not None:
            if not isinstance(enabled, bool):
                raise ValueError("自动行情开关必须为布尔值")
            self.execute("INSERT INTO meta VALUES('holding_auto_quotes',?) ON CONFLICT(key) "
                         "DO UPDATE SET value=excluded.value", ("1" if enabled else "0",))
        rows = self.rows("SELECT value FROM meta WHERE key='holding_auto_quotes'")
        return bool(rows and rows[0]["value"] == "1")

    def holding_cash_reference(self, account_id, reference=None):
        """Freeze the existing snapshot cash basis once; prices cannot change it."""
        key = "holding_cash_reference:" + account_id
        if reference is not None:
            if not isinstance(reference, dict) or not all(
                isinstance(reference.get(k), (int, float)) and math.isfinite(reference[k])
                for k in ("cash", "invested", "adjustments")
            ):
                raise ValueError("快照现金基准无效")
            self.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", (key, dumps(reference)))
        rows = self.rows("SELECT value FROM meta WHERE key=?", (key,))
        try:
            value = json.loads(rows[0]["value"]) if rows else None
            if value is not None and not all(math.isfinite(float(value[k]))
                                             for k in ("cash", "invested", "adjustments")):
                return None
            return {k: float(value[k]) for k in ("cash", "invested", "adjustments")} if value is not None else None
        except (ValueError, KeyError, TypeError):
            return None

    def list_executions(self, account_id=None, code=None):
        sql = "SELECT * FROM executions WHERE 1=1"
        args = []
        if account_id:
            sql += " AND account_id=?"
            args.append(account_id)
        if code:
            sql += " AND code=?"
            args.append(code)
        return self.rows(sql + " ORDER BY trade_time,created,id", tuple(args))

    def _execution_plan_snapshot(self, db, plan_id):
        if not plan_id:
            return None, None, {}
        plan = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not plan:
            raise ValueError("关联的作战计划不存在")
        plan = dict(plan)
        obs = db.execute("SELECT * FROM observations WHERE id=?", (plan["observation_id"],)).fetchone()
        observation = dict(obs) if obs else {}
        try:
            signal_payload = json.loads(observation.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            signal_payload = {}
        snapshot = {
            "plan_id": plan["id"],
            "observation_id": plan["observation_id"],
            "code": plan["code"],
            "strategy": plan["strategy"],
            "timeframe": plan["timeframe"],
            "setup_date": plan["setup_date"],
            "plan_state": plan["state"],
            "entry": plan["entry"],
            "stop": plan["stop"],
            "target": plan["target"],
            "planned_quantity": plan["quantity"],
            "plan_updated": plan["updated"],
            "strategy_version": observation.get("version"),
            "signal_asof": observation.get("asof"),
            "signal_payload": signal_payload,
        }
        return plan, observation, snapshot

    def record_execution(self, account_id, code, side, trade_time, price,
                         quantity, fee=0.0, reason="", notes="", plan_id=None):
        """Append a user-confirmed fill; signals can never call this implicitly."""
        code = str(code or "").strip().lower()
        side = str(side or "").strip().upper()
        trade_time = str(trade_time or "").strip()
        if side not in {"BUY", "SELL"}:
            raise ValueError("成交方向只能是买入或卖出")
        if not code or not trade_time:
            raise ValueError("股票代码和成交时间不能为空")
        numeric = (price, fee)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in numeric):
            raise ValueError("成交价格和费用必须是有限数字")
        if price <= 0 or fee < 0 or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError("成交价和股数必须大于 0，费用不能为负数")
        execution_id = uuid.uuid4().hex
        stamp = now()
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=? AND active=1", (account_id,)).fetchone():
                raise ValueError("交易账户不存在或已停用")
            plan, observation, snapshot = self._execution_plan_snapshot(db, plan_id)
            if plan and plan["code"].lower() != code:
                raise ValueError("成交股票与关联作战计划不一致")
            bought = db.execute(
                "SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN quantity ELSE -quantity END),0) "
                "FROM executions WHERE account_id=? AND code=?",
                (account_id, code),
            ).fetchone()[0]
            if side == "SELL" and quantity > bought:
                raise ValueError(f"卖出 {quantity} 股超过当前实际持仓 {bought} 股")
            db.execute(
                "INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (execution_id, account_id, plan_id,
                 observation.get("id") if observation else None, code, side,
                 trade_time, float(price), quantity, float(fee), str(reason or "").strip(),
                 str(notes or "").strip(), dumps(snapshot), stamp),
            )
            if plan:
                remaining = bought + (quantity if side == "BUY" else -quantity)
                new_state = "已手工入场" if side == "BUY" else ("已手工退出" if remaining == 0 else plan["state"])
                if new_state != plan["state"]:
                    db.execute("UPDATE plans SET state=?,updated=? WHERE id=?", (new_state, stamp, plan_id))
                    db.execute(
                        "INSERT INTO plan_history(plan_id,time,payload) VALUES(?,?,?)",
                        (plan_id, stamp, dumps({"source": "manual_execution", "execution_id": execution_id,
                                               "state": new_state})),
                    )
        self.event(None, "记录人工成交", self.path, execution_id=execution_id,
                   account_id=account_id, code=code, side=side, quantity=quantity)
        return execution_id

    @staticmethod
    def _validate_batches(batches, label):
        if not isinstance(batches, list) or not batches:
            raise ValueError(f"请至少填写一批{label}记录")
        cleaned = []
        for batch in batches:
            date = str(batch.get("date") or "").strip()
            price = batch.get("price")
            hands = batch.get("hands")
            fees = batch.get("fees", 0)
            if not date or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                raise ValueError(f"{label}日期和价格不能为空，价格必须大于 0")
            if not isinstance(hands, int) or isinstance(hands, bool) or hands <= 0:
                raise ValueError(f"{label}手数必须是大于 0 的整数")
            if not isinstance(fees, (int, float)) or not math.isfinite(fees) or fees < 0:
                raise ValueError(f"{label}费用必须是非负数")
            cleaned.append({"date": date, "price": float(price), "hands": hands,
                            "quantity": hands * 100, "fees": float(fees)})
        return cleaned

    def save_position(self, account_id, code, name, entry, stop, tp1,
                      buy_batches, tp2=None, tp3=None, plan_id=None,
                      observation_id=None, position_id=None):
        """Create or edit one current holding and its buy batches."""
        code = str(code or "").strip().lower()
        name = str(name or "").strip()
        prices = (entry, stop, tp1)
        if not code or not name:
            raise ValueError("代码和名称不能为空")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in prices):
            raise ValueError("买点、止损和 TP1 必须填写大于 0 的价格")
        for label, value in (("TP2", tp2), ("TP3", tp3)):
            if value not in (None, "") and (not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{label} 必须是大于 0 的价格")
        batches = self._validate_batches(buy_batches, "买入")
        stamp = now()
        position_id = position_id or uuid.uuid4().hex
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=? AND active=1", (account_id,)).fetchone():
                raise ValueError("交易账户不存在或已停用")
            existing = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
            duplicate = db.execute(
                "SELECT id FROM positions WHERE account_id=? AND code=? AND status='OPEN' AND id<>?",
                (account_id, code, position_id),
            ).fetchone()
            if duplicate:
                raise ValueError("该账户已经存在这只股票的持仓，请编辑原持仓")
            sold = 0
            if existing:
                sold = db.execute(
                    "SELECT COALESCE(SUM(quantity),0) FROM position_fills WHERE position_id=? AND side='SELL'",
                    (position_id,),
                ).fetchone()[0]
            bought = sum(batch["quantity"] for batch in batches)
            if bought < sold:
                raise ValueError(f"修改后的买入股数不能少于已经卖出的 {sold} 股")
            values = (account_id, code, name, plan_id, observation_id, float(entry), float(stop),
                      float(tp1), float(tp2) if tp2 not in (None, "") else None,
                      float(tp3) if tp3 not in (None, "") else None, stamp)
            if existing:
                if existing["status"] != "OPEN":
                    raise ValueError("已清仓记录不能改回持仓")
                db.execute(
                    "UPDATE positions SET account_id=?,code=?,name=?,plan_id=?,observation_id=?,"
                    "entry=?,stop=?,tp1=?,tp2=?,tp3=?,updated=? WHERE id=?",
                    (*values, position_id),
                )
                db.execute("DELETE FROM position_fills WHERE position_id=? AND side='BUY'", (position_id,))
            else:
                db.execute(
                    "INSERT INTO positions(id,account_id,code,name,plan_id,observation_id,entry,stop,tp1,tp2,tp3,status,created,updated) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?)",
                    (position_id, account_id, code, name, plan_id, observation_id,
                     float(entry), float(stop), float(tp1),
                     float(tp2) if tp2 not in (None, "") else None,
                     float(tp3) if tp3 not in (None, "") else None, stamp, stamp),
                )
            for batch in batches:
                db.execute(
                    "INSERT INTO position_fills VALUES(?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, position_id, "BUY", batch["date"], batch["price"],
                     batch["quantity"], batch["fees"], stamp),
                )
            if plan_id:
                plan = db.execute("SELECT state FROM plans WHERE id=?", (plan_id,)).fetchone()
                if plan and plan["state"] != "已手工入场":
                    db.execute("UPDATE plans SET state='已手工入场',updated=? WHERE id=?", (stamp, plan_id))
                    db.execute(
                        "INSERT INTO plan_history(plan_id,time,payload) VALUES(?,?,?)",
                        (plan_id, stamp, dumps({"source": "position", "position_id": position_id,
                                               "state": "已手工入场"})),
                    )
        self.event(None, "保存持仓", self.path, position_id=position_id, code=code)
        return position_id

    def delete_position(self, position_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
            if not row or row["status"] != "OPEN":
                raise ValueError("当前持仓不存在")
            db.execute("DELETE FROM position_fills WHERE position_id=?", (position_id,))
            db.execute("DELETE FROM positions WHERE id=?", (position_id,))
            if row["plan_id"]:
                db.execute("UPDATE plans SET state='计划交易',updated=? WHERE id=?", (now(), row["plan_id"]))
        self.event(None, "删除误录持仓", self.path, position_id=position_id)

    def sell_position(self, position_id, sell_batches, fees_total):
        batches = self._validate_batches(sell_batches, "卖出")
        if not isinstance(fees_total, (int, float)) or not math.isfinite(fees_total) or fees_total < 0:
            raise ValueError("税费合计必须是非负数")
        stamp = now()
        with self.connect() as db:
            position = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
            if not position or position["status"] != "OPEN":
                raise ValueError("当前持仓不存在或已经清仓")
            fills = list(db.execute("SELECT * FROM position_fills WHERE position_id=?", (position_id,)))
            bought = sum(row["quantity"] for row in fills if row["side"] == "BUY")
            already_sold = sum(row["quantity"] for row in fills if row["side"] == "SELL")
            to_sell = sum(batch["quantity"] for batch in batches)
            remaining = bought - already_sold
            if to_sell > remaining:
                raise ValueError(f"卖出 {to_sell} 股超过当前持仓 {remaining} 股")
            total_value = sum(batch["price"] * batch["quantity"] for batch in batches)
            for batch in batches:
                weight = batch["price"] * batch["quantity"] / total_value if total_value else 0
                db.execute(
                    "INSERT INTO position_fills VALUES(?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, position_id, "SELL", batch["date"], batch["price"],
                     batch["quantity"], float(fees_total) * weight, stamp),
                )
            if to_sell == remaining:
                all_fills = list(db.execute(
                    "SELECT * FROM position_fills WHERE position_id=? ORDER BY trade_date,created", (position_id,)
                ))
                buys = [row for row in all_fills if row["side"] == "BUY"]
                sells = [row for row in all_fills if row["side"] == "SELL"]
                buy_cost = sum(row["price"] * row["quantity"] + row["fees"] for row in buys)
                sell_value = sum(row["price"] * row["quantity"] - row["fees"] for row in sells)
                pnl = sell_value - buy_cost
                return_pct = pnl / buy_cost * 100 if buy_cost else 0.0
                first = min(row["trade_date"] for row in buys)
                close_date = max(row["trade_date"] for row in sells)
                try:
                    holding_days = max((datetime.fromisoformat(close_date[:10]) - datetime.fromisoformat(first[:10])).days, 0)
                except ValueError:
                    holding_days = 0
                closed_id = uuid.uuid4().hex
                db.execute("UPDATE positions SET status='CLOSED',updated=? WHERE id=?", (stamp, position_id))
                db.execute(
                    "INSERT INTO closed_trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (closed_id, position["account_id"], position_id, position["code"], position["name"],
                     close_date, holding_days, pnl, return_pct, "分批成交自动归档", stamp, stamp),
                )
                if position["plan_id"]:
                    db.execute("UPDATE plans SET state='已手工退出',updated=? WHERE id=?",
                               (stamp, position["plan_id"]))
                    db.execute(
                        "INSERT INTO plan_history(plan_id,time,payload) VALUES(?,?,?)",
                        (position["plan_id"], stamp,
                         dumps({"source": "position", "position_id": position_id,
                                "state": "已手工退出"})),
                    )
        self.event(None, "记录持仓卖出", self.path, position_id=position_id,
                   quantity=sum(batch["quantity"] for batch in batches))

    def save_closed_trade(self, account_id, code, name, close_date, holding_days,
                          pnl, return_pct, notes="", closed_id=None):
        code = str(code or "").strip().lower()
        name = str(name or "").strip()
        close_date = str(close_date or "").strip()
        if not code or not name or not close_date:
            raise ValueError("代码、名称和清仓日期不能为空")
        if not isinstance(holding_days, int) or isinstance(holding_days, bool) or holding_days < 0:
            raise ValueError("持仓天数必须是非负整数")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (pnl, return_pct)):
            raise ValueError("盈亏和收益率必须是有限数字")
        stamp = now()
        closed_id = closed_id or uuid.uuid4().hex
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise ValueError("交易账户不存在")
            existing = db.execute("SELECT 1 FROM closed_trades WHERE id=?", (closed_id,)).fetchone()
            if existing:
                db.execute(
                    "UPDATE closed_trades SET code=?,name=?,close_date=?,holding_days=?,pnl=?,"
                    "return_pct=?,notes=?,updated=? WHERE id=? AND account_id=?",
                    (code, name, close_date, holding_days, float(pnl), float(return_pct),
                     str(notes or ""), stamp, closed_id, account_id),
                )
            else:
                db.execute(
                    "INSERT INTO closed_trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (closed_id, account_id, None, code, name, close_date, holding_days,
                     float(pnl), float(return_pct), str(notes or ""), stamp, stamp),
                )
        self.event(None, "保存历史清仓", self.path, closed_id=closed_id, code=code)
        return closed_id

    def save_closed_trades_batch(self, account_id, records):
        """Atomically import a compact set of historical closed trades."""
        prepared = []
        for number, record in enumerate(records, 1):
            code = str(record.get("code") or "").strip().lower()
            name = str(record.get("name") or "").strip()
            close_date = str(record.get("close_date") or "").strip()
            try:
                holding_days = int(record.get("holding_days"))
                pnl = float(record.get("pnl"))
                return_pct = float(record.get("return_pct"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"第 {number} 行的持仓天数、盈亏和收益率必须填写数字") from exc
            if not code or not name or not close_date:
                raise ValueError(f"第 {number} 行的代码、名称和清仓日期不能为空")
            if holding_days < 0:
                raise ValueError(f"第 {number} 行的持仓天数必须是非负整数")
            if not all(math.isfinite(value) for value in (pnl, return_pct)):
                raise ValueError(f"第 {number} 行的盈亏和收益率必须是有限数字")
            prepared.append((uuid.uuid4().hex, account_id, None, code, name, close_date,
                             holding_days, pnl, return_pct,
                             str(record.get("notes") or ""), now(), now()))
        if not prepared:
            raise ValueError("请至少填写一行历史清仓记录")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise ValueError("交易账户不存在")
            existing = {
                (row[0], row[1], row[2], int(row[3]), round(float(row[4]), 2), round(float(row[5]), 2))
                for row in db.execute(
                    "SELECT code,name,close_date,holding_days,pnl,return_pct "
                    "FROM closed_trades WHERE account_id=?", (account_id,)
                )
            }
            incoming = set()
            for number, row in enumerate(prepared, 1):
                fingerprint = (row[3], row[4], row[5], int(row[6]),
                               round(float(row[7]), 2), round(float(row[8]), 2))
                if fingerprint in existing or fingerprint in incoming:
                    raise ValueError(f"第 {number} 行与已有记录或本批其他记录重复")
                incoming.add(fingerprint)
            db.executemany("INSERT INTO closed_trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", prepared)
        self.event(None, "批量保存历史清仓", self.path, count=len(prepared))
        return [row[0] for row in prepared]

    def delete_closed_trade(self, closed_id):
        with self.connect() as db:
            row = db.execute("SELECT position_id FROM closed_trades WHERE id=?", (closed_id,)).fetchone()
            if not row:
                raise ValueError("清仓记录不存在")
            if row["position_id"]:
                raise ValueError("由完整卖出自动生成的清仓记录不能单独删除，请保留成交链路")
            db.execute("DELETE FROM closed_trades WHERE id=?", (closed_id,))
        self.event(None, "删除误录清仓", self.path, closed_id=closed_id)

    def list_cash_flows(self, account_id):
        return self.rows(
            "SELECT * FROM cash_flows WHERE account_id=? "
            "ORDER BY flow_date DESC,created DESC,id DESC", (account_id,)
        )

    def save_cash_flow(self, account_id, flow_date, category, amount, notes="", flow_id=None):
        """Create or edit a non-trade account cash adjustment."""
        flow_date = str(flow_date or "").strip()
        category = str(category or "").strip()
        notes = str(notes or "").strip()
        if not flow_date:
            raise ValueError("资金日期不能为空")
        try:
            datetime.fromisoformat(flow_date[:10])
        except ValueError as exc:
            raise ValueError("资金日期格式应为 YYYY-MM-DD") from exc
        if not category:
            raise ValueError("资金类别不能为空")
        if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount == 0:
            raise ValueError("金额必须是非零有限数字")
        if category in {"转出", "其他支出"} and amount > 0:
            amount = -amount
        if category in {"利息归本", "现金分红", "转入", "其他收入"} and amount < 0:
            raise ValueError(f"{category}金额应为正数")
        stamp = now()
        flow_id = flow_id or uuid.uuid4().hex
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise ValueError("交易账户不存在")
            if db.execute("SELECT 1 FROM cash_flows WHERE id=?", (flow_id,)).fetchone():
                db.execute(
                    "UPDATE cash_flows SET flow_date=?,category=?,amount=?,notes=?,updated=? "
                    "WHERE id=? AND account_id=?",
                    (flow_date, category, float(amount), notes, stamp, flow_id, account_id),
                )
            else:
                db.execute(
                    "INSERT INTO cash_flows VALUES(?,?,?,?,?,?,?,?)",
                    (flow_id, account_id, flow_date, category, float(amount), notes, stamp, stamp),
                )
        self.event(None, "保存资金流水", self.path, flow_id=flow_id, category=category,
                   amount=float(amount))
        return flow_id

    def delete_cash_flow(self, flow_id):
        if not self.execute("DELETE FROM cash_flows WHERE id=?", (flow_id,)):
            raise ValueError("资金流水不存在")
        self.event(None, "删除资金流水", self.path, flow_id=flow_id)

    def list_daily_returns(self, account_id):
        """Account-scoped observations in meta, compatible with schema 8."""
        rows = self.rows("SELECT value FROM meta WHERE key=?", (f"daily_returns:{account_id}",))
        return sorted(json.loads(rows[0]["value"]).values(), key=lambda r: r["date"]) if rows else []

    def save_daily_returns(self, account_id, rows, *, replace_local=False):
        """Atomically record confirmed broker observations or local estimates.

        Independent of fills/cash flows: these values never alter equity or
        realised P&L. Local refresh must not overwrite a broker observation.
        """
        from datetime import date

        prepared = {}
        for row in rows:
            day = str(row.get("date") or "")
            date.fromisoformat(day)
            source, status = row.get("source", "broker"), row.get("status", "recorded")
            value = row.get("pnl")
            if source not in {"broker", "local"} or status not in {"recorded", "closed"}:
                raise ValueError("日盈亏来源或状态无效")
            if status == "recorded":
                if value is None or isinstance(value, bool) or not math.isfinite(float(value)):
                    raise ValueError(f"{day} 盈亏金额无效")
                value = round(float(value), 2)
            elif value is not None:
                raise ValueError("休市不能同时填写盈亏")
            item = {"date": day, "pnl": value, "source": source, "status": status}
            if day in prepared and item != prepared[day]:
                raise ValueError(f"{day} 存在冲突记录")
            prepared[day] = item
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
                raise ValueError("交易账户不存在")
            key = f"daily_returns:{account_id}"
            existing = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            saved = json.loads(existing["value"]) if existing else {}
            if replace_local:
                saved = {day: item for day, item in saved.items() if item["source"] == "broker"}
            for day, item in prepared.items():
                if item["source"] == "local" and saved.get(day, {}).get("source") == "broker":
                    continue
                saved[day] = item
            db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) "
                       "DO UPDATE SET value=excluded.value", (key, dumps(saved)))
