"""Durable experiments reuse historical H2 receipts; no formal-state writes."""
from dataclasses import asdict
import json

from .backtest import Assumptions
from .h2_replay_service import load_receipt
from .market import load_dataset
from .stop_research import AnchorSettings, MODEL, RULE, compare_trade, summarize
from .store import ROOT, digest, dumps
from .strategies import prepare, verify_frozen


def execute_experiment(service, job, spec):
    settings = AnchorSettings(**spec.get('anchor_settings', {}))
    settings.validate()
    source_job = spec.get('source_job')
    source, source_report, originals = load_receipt(service.store, source_job)
    if source_report['strategy_version'] != verify_frozen():
        raise ValueError('源回测原策略版本不同，请先重新运行盘后回测')
    if source['status'] in ('running', 'queued'):
        raise ValueError('请先等待盘后回测结束')
    costs = Assumptions(**source_report['assumptions'])
    costs.validate()
    rows, errors, frames = [], [], {}
    cancelled = False
    for n, original in enumerate(originals):
        if service.cancel_flags[job].is_set():
            cancelled = True
            break
        try:
            did = original['dataset_id']
            if did not in frames:
                frames.clear()
                frame, snapshot = load_dataset(service.store, did, job)
                if snapshot['source'] == 'demo':
                    raise ValueError('合成数据不能作为真实研究样本')
                frame = prepare(frame, 'daily', source_report['end'])
                frame.attrs['code'] = snapshot['code']
                frames[did] = frame
            rows.append(compare_trade(frames[did], original, settings, costs))
        except Exception as exc:
            errors.append(dict(id=original.get('id'), error=str(exc)))
        service.progress(job,n+1,len(originals),f'止损对照 {n+1}/{len(originals)} · 失败 {len(errors)}')
    report = summarize(rows)
    report.update(execution_model=MODEL, rule=RULE, source_job=source_job,
                  source_status=source['status'], source_complete=source_report.get('complete',False),
                  requested_opportunities=len(originals), complete=not cancelled and not errors and source_report.get('complete',False),
                  stop_reason=('已停止；保留已完成对照' if cancelled else
                               '源回测是部分结果，研究也按部分结果展示' if not source_report.get('complete',False) else
                               '部分样本对照失败，请查看错误回执' if errors else ''),
                  errors=errors, start=source_report['start'], end=source_report['end'],
                  scope=source_report.get('scope'), boards=source_report.get('boards'),
                  real_data=source_report.get('real_data',False),
                  datasets=source_report['datasets'], strategy_version=source_report['strategy_version'],
                  anchor_settings=asdict(settings), assumptions=source_report['assumptions'],
                  frozen=verify_frozen(), engine_version=digest(b''.join((ROOT/'workbench'/f).read_bytes()
                      for f in ('stop_research.py','stop_research_service.py','h2_replay.py','prices.py'))),
                  limitations=['同一原模拟成交；仅改变初始止损，不研究新增入场机会',
                               '候选 C 仅按低点抬升定位，不等同于已确认强突破腿',
                               '持有期限、费用、T+1 及日线双触碰口径沿用源回测',
                               '统一 R 以原 SL1 风险为分母；各方案自身 R 另行展示',
                               '未结束交易不计入配对已结束均值；无法识别样本单列',
                               '历史快照与名单偏差沿用源回测；不是实际成交或组合收益'])
    report['records_path'] = f'research/{job}/stop-pairs.json'
    report['report_path'] = f'research/{job}/stop-report.json'
    service.store.write_artifact(report['records_path'], dumps(rows).encode('utf-8'), job)
    service.store.write_artifact(report['report_path'], dumps(report).encode('utf-8'), job)
    return report


def load_experiment(store, job):
    jobs=store.rows("SELECT * FROM jobs WHERE id=? AND kind='backtest'",(job,))
    if not jobs:
        raise ValueError('研究记录不存在')
    report=json.loads(jobs[0]['result'])
    if report.get('execution_model') != MODEL:
        raise ValueError('不是初始止损研究记录')
    rows=json.loads(store.read_artifact(report['records_path']).decode('utf-8'))
    return jobs[0],report,rows
