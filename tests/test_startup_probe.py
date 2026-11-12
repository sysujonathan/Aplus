"""The manual startup probe must never silently run against an ordinary store."""
from pathlib import Path
import subprocess
import sys

import pytest


def test_probe_help_and_unmarked_runtime_fail_before_gui(tmp_path):
    script=Path(__file__).resolve().parents[1]/'tools/benchmark_startup.py'
    help_result=subprocess.run([sys.executable,str(script),'--help'],capture_output=True,text=True)
    assert help_result.returncode==0 and '--confirm-isolated-copy' in help_result.stdout
    result=subprocess.run([sys.executable,str(script),'--runtime',str(tmp_path),
        '--output',str(tmp_path/'nested/result.json'),'--confirm-isolated-copy'],capture_output=True,text=True)
    assert result.returncode==2 and 'marked isolated copy' in result.stderr
    assert not (tmp_path/'workbench.sqlite3').exists()


def test_copy_rejects_unfinished_backup_without_creating_destination(tmp_path):
    from tools.benchmark_startup import prepare_copy
    source=tmp_path/'backup.sqlite3';source.write_bytes(b'source sentinel')
    source.with_name(source.name+'-wal').write_bytes(b'unfinished')
    target=tmp_path/'new-copy'
    with pytest.raises(ValueError,match='static'):
        prepare_copy(source,tmp_path,target)
    assert source.read_bytes()==b'source sentinel' and not target.exists()


def test_large_watch_fixture_is_identical_across_reuse_and_excludes_archived_watches(tmp_path):
    import pandas as pd
    from tools.benchmark_startup import prepare_watches
    from workbench.store import Store
    from workbench.market import save_dataset
    store=Store(tmp_path/'runtime')
    original=store.add_manual_watch('600001',notes='original')
    archived=store.add_manual_watch('600002',notes='keep archived anchor')
    store.execute('UPDATE watchlist SET active=0 WHERE code=?',(archived,))
    frame=pd.DataFrame(dict(date=['2026-11-10','2026-11-11'],open=10.,high=11.,low=9.,close=10.5,volume=100.))
    for i in range(1,7):save_dataset(store,f'60000{i}',frame,'tickflow','qfq')
    def members():return [r['code'] for r in store.rows('SELECT code FROM watchlist WHERE active=1 ORDER BY updated DESC')]
    prepare_watches(store.root,3);first=members()
    assert len(first)==3 and archived not in first and original in first
    prepare_watches(store.root,1);assert members()==[original]
    prepare_watches(store.root,3);assert members()==first
    assert store.rows('SELECT active,notes FROM watchlist WHERE code=?',(archived,))==[
        dict(active=0,notes='keep archived anchor')]
