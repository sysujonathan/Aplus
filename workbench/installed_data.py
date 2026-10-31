"""Read-only legacy discovery and explicit, backed-up installed data binding.

No Store is constructed until the user has chosen a validated data home.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import subprocess
import uuid


def state_home():
    return Path(os.environ.get('APLUS_INSTALL_STATE') or
                Path(os.environ.get('LOCALAPPDATA', Path.home()))/'Aplus'/'installed').resolve()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


class RecoveryRequired(ValueError):
    """Only an already bound library may opt in to SQLite crash recovery."""


def unfinished_files(database):
    return [p for suffix in ('-wal','-journal')
            if (p:=database.with_name(database.name+suffix)).exists() and p.stat().st_size]


def inspect_data(root):
    root = Path(root).expanduser().resolve()
    database = root/'workbench.sqlite3'
    if not database.is_file():
        raise ValueError('这里没有 Aplus 数据，请选择原数据或原程序文件夹。')
    if unfinished_files(database):
        raise RecoveryRequired('检测到上次未收尾事务。首次接续请先完整退出原程序；已接续数据可备份后恢复。')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'meta','datasets','observations','plans','jobs'} <= tables:
            raise ValueError('这份数据库不是可直接接续的 Aplus 数据；原件未修改。')
        row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not row or row[0] not in {str(v) for v in range(1,10)}:
            raise ValueError('该数据版本尚不支持接续；请保留原件交维护者迁移。')
        counts = {name: db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                  if name in tables else 0 for name in ('watchlist','accounts','positions','position_fills','closed_trades')}
        tail = db.execute('SELECT MAX(end) FROM datasets').fetchone()[0]
        active = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        # Full integrity validation is performed on the protected backup when
        # binding/updating, not on every ordinary installed desktop startup.
    return dict(path=str(root), schema=int(row[0]), counts=counts, latest=tail, active=active)


def candidate_data(path):
    path = Path(path).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for root in (path, path/'runtime'):
        if (root/'workbench.sqlite3').is_file():
            return inspect_data(root)
    raise ValueError('未找到数据，请选择包含 workbench.sqlite3 的文件夹，或原 Aplus 程序文件夹。')


def discover_hints():
    """Bounded known locations/configuration/shortcuts, never a disk-wide search."""
    local = Path(os.environ.get('LOCALAPPDATA', Path.home()))
    hints = [local/'Aplus'/'runtime']
    if os.environ.get('APLUS_INSTALL_SMOKE')=='1':
        # Explicit integration probes may inspect only their supplied local
        # application-data home, never the developer's real shortcut targets.
        try:return [candidate_data(hints[0])]
        except (OSError,ValueError,sqlite3.Error):return []
    if os.environ.get('A_WORKBENCH_HOME'):
        hints.insert(0, Path(os.environ['A_WORKBENCH_HOME']))
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                hints.append(Path(winreg.QueryValueEx(key, 'A_WORKBENCH_HOME')[0]))
        except OSError:
            pass
        script = r'''[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$shell=New-Object -ComObject WScript.Shell
$folders=@([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('CommonDesktopDirectory'),[Environment]::GetFolderPath('Programs'))
$items=@(foreach($folder in $folders){Get-ChildItem -LiteralPath $folder -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Where-Object {$_.BaseName -match 'Aplus|A\+|A＋|Aplus_'} | Select-Object -First 30 | ForEach-Object {$link=$shell.CreateShortcut($_.FullName);@{target=$link.TargetPath;arguments=$link.Arguments;working=$link.WorkingDirectory}}})
ConvertTo-Json -InputObject $items -Compress'''
        try:
            result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                capture_output=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW,
                env={key:value for key,value in os.environ.items() if key.casefold()!='psmodulepath'})
            records = json.loads(result.stdout.decode('utf-8-sig', errors='replace'))
            for record in records[:60]:
                arguments=record.get('arguments','')
                for match in re.findall(r'"([A-Za-z]:[^"\r\n]+\.(?:vbs|cmd|bat))"',arguments,re.I):
                    hints.append(Path(match).parent)
                    # Shortcut targets may be wscript.exe; inspect its quoted
                    # script too, without executing any discovered code.
                    record=dict(record,target=match)
                for key in ('target','working'):
                    value = record.get(key)
                    if value:
                        target = Path(value)
                        hints.append(target if target.is_dir() else target.parent)
                        if target.suffix.lower() in ('.vbs','.cmd','.bat') and target.is_file():
                            raw = target.read_bytes()[:32768]
                            for encoding in ('utf-8-sig','utf-16','gb18030'):
                                try:
                                    text = raw.decode(encoding)
                                except UnicodeError:
                                    continue
                                for match in re.findall(r'[A-Za-z]:[\\/][^"\r\n<>|]*', text):
                                    p = Path(os.path.expandvars(match.strip().rstrip("'")))
                                    hints.append(p if p.is_dir() else p.parent)
                                break
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    found, seen = [], set()
    for hint in hints[:160]:
        try:
            item = candidate_data(hint)
            key = os.path.normcase(item['path'])
            if key not in seen:
                found.append(item); seen.add(key)
        except (OSError, ValueError, sqlite3.Error):
            pass
    return found


def bind_existing(root, *, home=None, install_root=None):
    """Caller holds the desktop mutex; keep the original database and cache in place."""
    home = Path(home or state_home())
    info = candidate_data(root)
    runtime = Path(info['path'])
    if install_root and (runtime == Path(install_root).resolve() or Path(install_root).resolve() in runtime.parents
                         or runtime in Path(install_root).resolve().parents):
        raise ValueError('数据位于新版程序目录内，请先联系维护者安全迁移，原件未修改。')
    if info['active']:
        raise ValueError('原数据中仍有运行或排队任务。请先在旧版结束任务并完整退出，再接续。')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    backup = home/'backups'/stamp/'workbench.sqlite3'
    backup.parent.mkdir(parents=True, exist_ok=False)
    source = runtime/'workbench.sqlite3'
    before = file_sha256(source)
    # Native desktop/web are closed and sidecar transactions were refused.
    # Preserve the exact original file, rather than SQLite.backup() which may
    # rewrite the page-1 change counters even with identical business rows.
    shutil.copy2(source,backup)
    if file_sha256(backup)!=before:
        raise ValueError('备份字节与原件不一致；尚未关联数据。')
    with closing(sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True,timeout=2)) as new:
        if new.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('备份完整性检查失败；尚未关联数据。')
    if file_sha256(source) != before:
        raise ValueError('备份期间原数据发生变化，尚未关联；请确认旧程序完整退出。')
    atomic_json(backup.with_name('receipt.json'), dict(info=info, source_sha256=before,
                backup_sha256=before))
    atomic_json(home/'data.json', dict(protocol=1, runtime=str(runtime), existing=True,
                backup=str(backup), schema_before=info['schema']))
    return runtime


def bind_new(*, home=None):
    home = Path(home or state_home())
    runtime = home.parent/'runtime'
    if runtime.exists() and any(runtime.iterdir()):
        raise ValueError('默认位置已有文件，请选择接续已有数据；不会覆盖或创建替代空库。')
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_json(home/'data.json', dict(protocol=1, runtime=str(runtime), existing=False))
    return runtime


def bound_runtime(*, home=None):
    path = Path(home or state_home())/'data.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    runtime = Path(value['runtime']).resolve()
    if value.get('existing') or (runtime/'workbench.sqlite3').exists():
        inspect_data(runtime)
    elif not runtime.is_dir() or any(runtime.iterdir()):
        raise ValueError('已关联数据的位置发生变化；为避免创建空库，请重新确认原数据。')
    return runtime


def recover_bound_runtime(*, home=None):
    """Explicit recovery: raw snapshot first, SQLite owns all journal handling.

    Discovery/binding stays read-only. No sidecar is deleted by this code and
    an arbitrary selected legacy folder cannot be repaired through this entry.
    """
    from .version_update import application_mutex, require_web_closed
    home=Path(home or state_home()).resolve()
    profile=json.loads((home/'data.json').read_text(encoding='utf-8'))
    runtime=Path(profile['runtime']).resolve();database=runtime/'workbench.sqlite3'
    with application_mutex():
        require_web_closed()
        if not database.is_file():raise ValueError('已关联数据库不存在，未创建替代空库。')
        if not unfinished_files(database):return bound_runtime(home=home)
        sources=[database]+[database.with_name(database.name+s) for s in ('-journal','-wal','-shm')
                            if database.with_name(database.name+s).is_file()]
        if shutil.disk_usage(home).free < sum(p.stat().st_size for p in sources)+64*1024*1024:
            raise ValueError('恢复备份空间不足，未打开或修改原数据库。')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        folder=home/'backups'/('recovery-'+stamp);folder.mkdir(parents=True)
        hashes={p.name:file_sha256(p) for p in sources}
        for path in sources:
            shutil.copy2(path,folder/path.name)
            if file_sha256(folder/path.name)!=hashes[path.name]:
                raise ValueError('原始事务备份不一致，未执行恢复。')
        if any(file_sha256(p)!=hashes[p.name] for p in sources):
            raise ValueError('备份期间数据库仍在变化，未执行恢复。请先退出其他 Aplus。')
        receipt=dict(runtime=str(runtime),files=hashes,state='snapshot_saved')
        atomic_json(folder/'receipt.json',receipt)
        try:
            with closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)) as db:
                db.execute('BEGIN EXCLUSIVE')
                try:
                    if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                        raise ValueError('恢复后的数据完整性检查未通过')
                finally:db.execute('ROLLBACK')
                if (database.with_name(database.name+'-wal')).exists():
                    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                        raise ValueError('数据库仍被其他连接使用')
            inspect_data(runtime)
        except Exception as exc:
            atomic_json(folder/'receipt.json',dict(receipt,state='recovery_failed',detail=str(exc)))
            raise ValueError('暂不能完成恢复，原始数据库及事务备份已保留：'+str(folder)+'\n'+str(exc)) from exc
        atomic_json(folder/'receipt.json',dict(receipt,state='recovered'))
        return runtime
