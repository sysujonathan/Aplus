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
