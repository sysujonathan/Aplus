"""One explicit 8 -> 9 shadow migration; never roll back later business writes."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from .installed_data import atomic_json, file_sha256, inspect_data, unfinished_files


def business_fingerprint(database):
    result={}
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            quoted='"'+table.replace('"','""')+'"'
            columns=[r[1] for r in db.execute('PRAGMA table_info('+quoted+')')]
            digest=hashlib.sha256();count=0
            query='SELECT * FROM '+quoted
            if table=='meta':query+=" WHERE key!='schema_version'"
            query+=' ORDER BY rowid'
            for row in db.execute(query):
                values=[{'blob':value.hex()} if isinstance(value,bytes) else value for value in row]
                digest.update(json.dumps(values,ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\n');count+=1
            result[table]=dict(columns=columns,count=count,sha256=digest.hexdigest())
    return result


def prepare(runtime, folder):
    runtime=Path(runtime).resolve();folder=Path(folder).resolve()
    info=inspect_data(runtime)
    if info['schema']!=8 or info['active']:raise ValueError('只有已完整退出的 schema 8 数据可执行此次升级。')
    source=runtime/'workbench.sqlite3'
    if shutil.disk_usage(folder).free<source.stat().st_size*4+64*1024*1024:
        raise ValueError('数据升级备份空间不足，原件未修改。')
    if shutil.disk_usage(runtime).free<source.stat().st_size*2+64*1024*1024:
        raise ValueError('原数据磁盘空间不足，原件未修改。')
    home=folder/'migration';home.mkdir(exist_ok=False)
    backup=home/'original.sqlite3';shadow=home/'shadow'/'workbench.sqlite3';shadow.parent.mkdir()
    before=file_sha256(source);shutil.copy2(source,backup)
    if file_sha256(source)!=before or file_sha256(backup)!=before:
        raise ValueError('备份期间数据有变化，未升级。')
    facts=business_fingerprint(backup)
    shutil.copy2(backup,shadow)
    from .store import Store
    Store(shadow.parent)  # The existing reviewed schema implementation, isolated from the real library.
    if inspect_data(shadow.parent)['schema']!=9:raise ValueError('数据副本升级版本不正确。')
    after=business_fingerprint(shadow)
    if any(after.get(table)!=value for table,value in facts.items()):
        raise ValueError('数据副本升级改变了已有记录，原件未修改。')
    with closing(sqlite3.connect(shadow.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('升级副本完整性检查未通过。')
    plan=dict(protocol=1,from_schema=8,to_schema=9,runtime=str(runtime),
              original_sha256=before,migrated_sha256=file_sha256(shadow),
              backup=str(backup),shadow=str(shadow))
    atomic_json(home/'receipt.json',dict(plan,state='prepared',business=facts))
    return plan


def validate_plan(plan,runtime,folder):
    runtime=Path(runtime).resolve();folder=Path(folder).resolve()
    if (plan.get('protocol')!=1 or type(plan.get('from_schema')) is not int or plan['from_schema']!=8
            or type(plan.get('to_schema')) is not int or plan['to_schema']!=9
            or Path(plan.get('runtime','')).resolve()!=runtime
            or Path(plan.get('backup','')).resolve()!=folder/'migration'/'original.sqlite3'
            or Path(plan.get('shadow','')).resolve()!=folder/'migration'/'shadow'/'workbench.sqlite3'
            or any(not isinstance(plan.get(key),str) or not re.fullmatch('[0-9a-f]{64}',plan[key])
                   for key in ('original_sha256','migrated_sha256'))):
        raise ValueError('数据升级恢复身份不完整。')
    if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction())
           for p in (runtime,folder,folder/'migration',Path(plan['backup']),Path(plan['shadow']))):
        raise ValueError('数据升级路径已变化。')
    if file_sha256(plan['backup'])!=plan['original_sha256']:raise ValueError('原数据备份校验失败。')
    return runtime/'workbench.sqlite3'


def replace_bytes(source,database,expected):
    temp=database.with_name(database.name+'.aplus-'+uuid.uuid4().hex+'.tmp')
    try:
        with open(source,'rb') as reader,temp.open('xb') as writer:
            shutil.copyfileobj(reader,writer);writer.flush();os.fsync(writer.fileno())
        if file_sha256(temp)!=expected:raise ValueError('数据切换副本校验失败。')
        os.replace(temp,database)  # Same-volume atomic replacement; never remove runtime.
    finally:temp.unlink(missing_ok=True)


def commit(plan,runtime,folder):
    database=validate_plan(plan,runtime,folder)
    if (unfinished_files(database) or file_sha256(database)!=plan['original_sha256']
            or file_sha256(plan['shadow'])!=plan['migrated_sha256']):
        raise ValueError('数据或升级副本有变化，未切换。')
    replace_bytes(plan['shadow'],database,plan['migrated_sha256'])


def restore_if_unchanged(plan,runtime,folder):
    database=validate_plan(plan,runtime,folder)
    if unfinished_files(database):return False
    current=file_sha256(database)
    if current==plan['original_sha256']:return True
    if current!=plan['migrated_sha256']:return False  # New app may already have written real data.
    replace_bytes(plan['backup'],database,plan['original_sha256'])
    return True
