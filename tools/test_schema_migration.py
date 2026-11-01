"""Seed an actual old Store DDL, then exercise migration inside the real EXE."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'.test-installer'/('migration-probe-'+uuid.uuid4().hex)
    runtime=folder/'runtime';runtime.mkdir(parents=True)
    (folder/'probe.json').write_text(json.dumps(dict(synthetic_schema8_probe=True)))
    with closing(sqlite3.connect(runtime/'workbench.sqlite3')) as db:
        db.executescript((ROOT/'tests/fixtures/installed_schema8.sql').read_text(encoding='utf-8'))
        db.executescript((ROOT/'tests/fixtures/installed_schema8_business.sql').read_text(encoding='utf-8'))
        db.execute("INSERT INTO watchlist VALUES('sz.000001','','synthetic schema-eight probe',1,'now','now')");db.commit()
    subprocess.run([str(Path(sys.argv[1]).resolve()),'--migration-self-test',str(folder)],check=True,timeout=120)
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    assert result['ok'];print(json.dumps(dict(result,folder=str(folder))))


if __name__=='__main__':main()
