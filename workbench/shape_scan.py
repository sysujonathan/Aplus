"""SHA-verified local snapshot matching; no network or strategy execution."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, timezone
import io
import time
import uuid

import pandas as pd

from .market import board_of, validate_bars
from .shape_matching import ALGORITHM, compare
from .shape_store import options,validate_definition
from .shape_sources import require_adjustment,template_problem
from .store import digest, now


class FrameCache:
    def __init__(self, limit=32):
        self.limit = limit
        self.frames = OrderedDict()

    def read(self, store, record, timings, *, verified_only=False):
        start = time.perf_counter()
        path = (store.root/record['path']).resolve()
        if not path.is_relative_to(store.root):
            raise ValueError('行情快照路径越过本机数据仓')
        content = path.read_bytes()
        timings['read'] += time.perf_counter()-start
        start = time.perf_counter()
        if digest(content) != record['sha256']:
            raise ValueError('行情 SHA 校验失败；请先修复行情，不使用该快照')
        timings['verify'] += time.perf_counter()-start
        if verified_only:
            timings['parsed_reused'] += 1
            return None
        key = record['id']
        if key in self.frames:
            self.frames.move_to_end(key)
            timings['parsed_reused'] += 1
            return self.frames[key]
        start = time.perf_counter()
        frame = validate_bars(pd.read_csv(io.BytesIO(content)))
        timings['parse'] += time.perf_counter()-start
        self.frames[key] = frame
        while len(self.frames) > self.limit:
            self.frames.popitem(last=False)
        return frame


def scan(store, templates, asof, source, *, cancel=None, progress=None, cache=None, checkpoint=None,
         on_checkpoint=None, skipped_templates=None):
    date.fromisoformat(asof)
    from .market import completed_date
    if asof > completed_date():
        raise ValueError('不能匹配尚未收盘的未来交易日')
    if source not in ('baostock','tickflow','tencent'):
        raise ValueError('请选择 Aplus 正式行情来源；不扫描旧工程数据库')
    if not templates and not skipped_templates:
        raise ValueError('请先保存并选择形态模板')
    configs = [(template,options(template.get('settings'))) for template in templates]
    for template,_ in configs:
        validate_definition(template)
        problem = template_problem(template,source,asof)
        if problem:
            raise ValueError(problem)
    records = store.rows("SELECT * FROM (SELECT *,ROW_NUMBER() OVER(PARTITION BY code ORDER BY end DESC,created DESC,rowid DESC) AS rank "
                         "FROM datasets WHERE source=? AND timeframe='daily') WHERE rank=1 ORDER BY code",(source,))
    if not records:
        raise ValueError('本地没有所选来源的日线，请先更新行情')
    latest = max(record['end'] for record in records)
    if asof > latest:
        raise ValueError(f'本地行情只到 {latest}，不能匹配 {asof}')
    records = [r for r in records if any((not c['codes'] or r['code'] in c['codes']) and
               board_of(r['code']) in c['boards'] for _,c in configs)]
    if configs and not records:
        raise ValueError('所选股票范围没有本地行情')
    cache = cache or FrameCache()
    timings = dict(read=0.,verify=0.,parse=0.,compute=0.,save=0.,parsed_reused=0,score_reused=0)
    report = dict(id=uuid.uuid4().hex,created=datetime.now(timezone.utc).isoformat(),asof=asof,source=source,algorithm=ALGORITHM,
                  mode='replay' if asof < completed_date() or asof < latest else 'observed',status='running',
                  templates=templates,matches=[],inputs=[],skipped=[],errors=[],timings=timings,
                  skipped_templates=list(skipped_templates or []))
    started = time.perf_counter()
    matches = []
    previous = {}
    def contract(values):
        return sorted((t['id'],t['version'],str(options(t.get('settings')))) for t in values)
    if checkpoint:
        # Reuse only exactly identical version/parameters/date/source/snapshot.
        old = store.shape_run(checkpoint)
        if old['asof']==asof and old['source']==source and contract(old['templates'])==contract(templates) and old['algorithm']==ALGORITHM:
            previous = {item['dataset_id']:item for item in old['inputs']}
            matches = list(old.get('evaluations',old['matches']))
    def capture(status):
        result = deepcopy(report)
        result.update(id=uuid.uuid4().hex,created=datetime.now(timezone.utc).isoformat(),status=status)
        ids = {v['dataset_id'] for v in result['inputs']}
        result['evaluations'] = [m for m in matches if m['dataset_id'] in ids]
        result['matches'] = []
        for template,config in configs:
            values = [m for m in result['evaluations'] if m['template_id']==template['id']]
            result['matches'] += sorted(values,key=lambda m:(-m['score'],m['code']))[:config['topn']]
        result['timings']['elapsed_before_save'] = time.perf_counter()-started
        store.save_shape_run(result)
        result['timings']['elapsed'] = time.perf_counter()-started
        return result
    checkpoint_at = time.monotonic()
    try:
        for index,record in enumerate(records):
            if cancel:
                cancel()
            if progress:
                progress(index,len(records),f"{record['code']} · {asof}")
            try:
                require_adjustment(source,record['adjustment'])
                lineage = dict(dataset_id=record['id'],sha256=record['sha256'],code=record['code'],
                               source=source,adjustment=record['adjustment'],snapshot_end=record['end'],cutoff=asof)
                reuse = previous.get(record['id']) == lineage
                frame = cache.read(store,record,timings,verified_only=reuse)
                if reuse:
                    report['inputs'].append(lineage)
                    report['skipped'].extend(v for v in old['skipped'] if v['code']==record['code'])
                    timings['score_reused'] += 1
                    continue
                prefix = frame[frame.date <= asof]
                if prefix.empty or str(prefix.date.iloc[-1]) != asof:
                    report['skipped'].append(dict(code=record['code'],reason='该交易日没有已完成K线，不借用旧价或未来价'))
                    continue
                # A resume may see a replacement snapshot; remove its old score.
                matches = [m for m in matches if m['code'] != record['code']]
                for template,config in configs:
                    if (config['codes'] and record['code'] not in config['codes']) or board_of(record['code']) not in config['boards']:
                        continue
                    window = config['window']
                    if len(prefix)<window:
                        report['skipped'].append(dict(code=record['code'],template_id=template['id'],reason=f'不足 {window} 根已完成K线'))
                        continue
                    start = time.perf_counter()
                    clip = prefix.tail(window)
                    score = compare(clip.close.to_numpy(),template['path'])
                    timings['compute'] += time.perf_counter()-start
                    if score['score'] >= config['threshold']:
                        matches.append(dict(id=uuid.uuid4().hex,code=record['code'],name='',
                            template_id=template['id'],template_version=template['version'],
                            dataset_id=record['id'],sha256=record['sha256'],adjustment=record['adjustment'],
                            market_asof=asof,start=str(clip.date.iloc[0]),end=asof,window=window,
                            window_return_pct=round(100*(clip.close.iloc[-1]/clip.close.iloc[0]-1),2),**score))
                report['inputs'].append(lineage)
            except (ValueError,OSError,pd.errors.ParserError) as exc:
                report['errors'].append(dict(code=record['code'],reason=str(exc)))
            # Yield CPU/IO to the main strategy workflow and interactive chart.
            time.sleep(.001)
            if on_checkpoint and time.monotonic()-checkpoint_at >= 5:
                saved = capture('checkpoint')
                on_checkpoint(saved['id'])
                checkpoint_at = time.monotonic()
        report['status'] = 'skipped' if not configs else ('partial' if report['errors'] else 'completed')
    except InterruptedError:
        report['status'] = 'cancelled'
    report = capture(report['status'])
    if progress:
        progress(len(report['inputs']),len(records),report['status'])
    return report
