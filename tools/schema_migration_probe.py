"""Explicit frozen EXE probe, restricted to a marked synthetic installer fixture."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def run(folder):
    from workbench import installed_migration as migration, installed_data as data
    folder=Path(folder).resolve();runtime=folder/'runtime'
    marker=json.loads((folder/'probe.json').read_text(encoding='utf-8'))
    if (folder.parent.name!='.test-installer' or not folder.name.startswith('migration-probe-')
            or marker.get('synthetic_schema8_probe') is not True or runtime.is_symlink()
            or runtime.resolve().parent!=folder):
        raise ValueError('This entry accepts only its isolated synthetic fixture')
    database=runtime/'workbench.sqlite3'
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('SELECT notes FROM watchlist').fetchall()!=[('synthetic schema-eight probe',)]:
            raise ValueError('Not the expected synthetic data')
    before=database.read_bytes();first=folder/'first';first.mkdir()
    plan=migration.prepare(runtime,first);migration.commit(plan,runtime,first)
    assert data.inspect_data(runtime)['schema']==9
    assert migration.restore_if_unchanged(plan,runtime,first) and database.read_bytes()==before
    second=folder/'second';second.mkdir();plan=migration.prepare(runtime,second);migration.commit(plan,runtime,second)
    with closing(sqlite3.connect(database)) as db:
        db.execute("UPDATE watchlist SET notes='synthetic new write'");db.commit()
    retained=database.read_bytes()
    assert not migration.restore_if_unchanged(plan,runtime,second) and database.read_bytes()==retained
    result=dict(ok=True,real_frozen_exe=True,synthetic=True,shadow_upgrade=True,
                original_bytes_restored=True,new_write_retained=True,schema=9)
    (folder/'result.json').write_text(json.dumps(result),encoding='utf-8')
    return result
