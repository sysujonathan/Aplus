"""Explicit read-only Aplus snapshot copy, real end-to-end local shape evidence.

Not collected by pytest. Never copies accounts, credentials or the legacy DB.
Usage: python tests/shape_real_probe.py <Aplus runtime> <isolated output>
"""
from pathlib import Path
import json
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(source_root,output):
    from workbench.store import Store,digest
    from workbench.shape_matching import classic,drawing_path
    from workbench.shape_scan import scan,FrameCache
    source_root=Path(source_root).resolve();output=Path(output).resolve()
    if 'workbuddy' in str(source_root).lower() or not output.is_relative_to(ROOT/'.test-shape-real'):
        raise ValueError('只允许Aplus数据的只读快照副本与隔离输出')
    output.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    target=Store(output/'runtime')
    with sqlite3.connect((source_root/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        records=[dict(r) for r in db.execute("SELECT * FROM (SELECT *,ROW_NUMBER() OVER(PARTITION BY code ORDER BY end DESC,created DESC,rowid DESC) AS rank FROM datasets WHERE source='tickflow' AND timeframe='daily') WHERE rank=1 ORDER BY code")]
    from workbench.market import board_of
    records=[r for r in records if board_of(r['code'])=='沪深主板']
    for offset in range(0,len(records),200):
        with target.atomic_write():
            for record in records[offset:offset+200]:
                path=(source_root/record['path']).resolve()
                if not path.is_relative_to(source_root):raise ValueError('源快照路径无效')
                content=path.read_bytes()
                if digest(content)!=record['sha256']:raise ValueError('源快照SHA不符')
                target.write_artifact(record['path'],content)
                target.execute('INSERT OR IGNORE INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(record[k] for k in ('id','code','timeframe','source','adjustment','start','end','rows','path','sha256','created')))
    target.execute("INSERT OR REPLACE INTO meta VALUES('daily_market_source','tickflow')")
    # Only the public security directory is needed for names; never copy tokens.
    name_path=source_root/'sources/tickflow/universe.csv'
    if name_path.exists():target.write_artifact('sources/tickflow/universe.csv',name_path.read_bytes())
    copied=time.perf_counter()-started
    templates=[]
    for key,name in (('w_bottom','W底／双底'),('top_reversal','M头／顶部反转')):
        templates.append(target.save_shape_template(dict(kind='classic',name=name,path=classic(key),origin={'classic_key':key},settings=dict(threshold=70,topn=10,boards=['沪深主板']))))
    path=drawing_path([(0,10),(10,35),(20,20),(30,60),(40,45),(50,90),(60,60),(70,88),(80,20)])
    templates.append(target.save_shape_template(dict(kind='drawing',name='手绘：三推后双底轮廓',path=path,origin={'method':'manual-contour'},settings=dict(threshold=70,topn=10,boards=['沪深主板']))))
    asof=max(r['end'] for r in records)
    cache=FrameCache()
    report=scan(target,templates,asof,'tickflow',cache=cache)
    second=scan(target,templates,asof,'tickflow',cache=cache,checkpoint=report['id'])
    assert [(m['code'],m['template_id'],m['score']) for m in report['matches']]==[(m['code'],m['template_id'],m['score']) for m in second['matches']]
    assert not target.rows('SELECT * FROM observations') and not target.rows('SELECT * FROM plans')
    summary=dict(real=True,source='tickflow',asof=asof,stocks=len(records),copy_seconds=copied,
        first=dict(id=report['id'],status=report['status'],inputs=len(report['inputs']),matches=len(report['matches']),skipped=len(report['skipped']),errors=report['errors'],timings=report['timings']),
        repeated=dict(id=second['id'],status=second['status'],timings=second['timings']),
        no_formal_signals_or_plans=True,remote_pattern_service=False)
    (output/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':run(*sys.argv[1:])
