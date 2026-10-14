"""Bridge an exited old updater to accepted main; no compatibility bypass.

Run from a tested maintainer checkout, with the user's existing runtime explicit.
Uses the same independent worker, fresh CI checks, backups, probe and restart.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.version_update import (UpdateError, _digest, application_mutex, check_version,
    prepare_worker, require_web_closed)


def prepare(root, runtime, python):
    root, runtime = Path(root).resolve(), Path(runtime).resolve()
    database = runtime/'workbench.sqlite3'
    if not database.is_file():
        raise UpdateError('必须指定原有运行数据目录，不能新建空账户来升级。')
    with application_mutex():
        require_web_closed()
        if any((runtime/('workbench.sqlite3'+suffix)).exists() for suffix in ('-wal','-journal')):
            raise UpdateError('数据库仍有事务日志，请先完整退出所有业务服务后核对。')
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]:
                raise UpdateError('仍有未结束任务，请先确认完成或停止，未替换软件。')
        checksum = _digest(database)
        plan = check_version(root,force=True)
        if not plan.get('can_update'):
            raise UpdateError(plan.get('message','当前版本不能安全升级'))
        folder = prepare_worker(root,plan,runtime,python=python,data_checksum=checksum)
        # Worker cannot install before this maintainer process exits.
        try:
            shutil.copyfile(database,folder/'original-workbench.sqlite3')
            if _digest(database) != checksum or _digest(folder/'original-workbench.sqlite3') != checksum:
                raise UpdateError('运行数据在准备时发生变化，已取消升级。')
            deadline = time.monotonic()+8
            while time.monotonic()<deadline:
                if (folder/'ready.json').exists():
                    return folder
                if (folder/'receipt.json').exists():
                    raise UpdateError('独立升级工具未就绪，请查看回执。')
                time.sleep(.1)
            raise UpdateError('独立升级工具未就绪，已取消准备。')
        except Exception:
            from workbench.version_update import write_json
            write_json(folder/'cancelled.json',{'cancelled':True})
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--python',required=True)
    options=parser.parse_args()
    try:
        folder=prepare(options.root,options.runtime,options.python)
        print(json.dumps({'prepared':str(folder)},ensure_ascii=False))
    except (UpdateError,OSError,sqlite3.Error) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
