"""Progress reads must never block Tk, fail a job or lose the next poll."""
import json
import sqlite3
import time
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from gui.background_status import load_job
from gui.toolbar import ToolBar
from tests.test_workbench import store


def running(**changes):
    return dict(dict(status='running',progress=1,total=3,message='TickFlow 更新 23/100',
                     result=json.dumps({'sources':{'tickflow':{'phase':'更新'}}})),**changes)


def toolbar():
    reader = SimpleNamespace(poll=Mock(return_value=None),submit=Mock())
    bar = SimpleNamespace(_job_id='job',_job_kind='一键盘前',store=Mock(),_job_poll=None,
        _job_reader=reader,_job_read_pending=False,_job_read_error=False,_job_snapshot=None,
        _job_started_at=time.monotonic()-2,_premarket_progress={},
        source_var=SimpleNamespace(get=lambda:'TickFlow'),
        _refresh_timing_status=Mock(),set_status=Mock(),after=Mock(return_value='timer'),
        _load_market_status=Mock(),_finish_job=Mock())
    bar._display_source = MethodType(ToolBar._display_source,bar)
    bar._render_job_snapshot = MethodType(ToolBar._render_job_snapshot,bar)
    bar._poll_job = MethodType(ToolBar._poll_job,bar)
    bar.store.rows.side_effect = AssertionError('Tk must not query the database')
    return bar


def reply(bar, value=None, error=None, job='job'):
    bar._job_reader.poll.return_value = (1,job,value,error)
    bar._poll_job()


def test_read_error_keeps_identity_stop_and_last_progress_then_recovers():
    bar = toolbar()
    reply(bar,running())
    reply(bar,error=sqlite3.OperationalError('database is locked'))
    assert bar._job_id == 'job' and not bar._finish_job.called
    assert '23/100' in bar.set_status.call_args[0][0]
    assert '上次进度' in bar.set_status.call_args[0][0]
    assert '正在重试' in bar.set_status.call_args[0][0]
    reply(bar,running(message='TickFlow 更新 80/100'))
    assert '80/100' in bar.set_status.call_args[0][0]
    assert '上次进度' not in bar.set_status.call_args[0][0]
    assert bar.after.call_count == 3 and not bar.store.rows.called


def test_pending_read_is_bounded_while_timer_keeps_painting_elapsed():
    bar = toolbar()
    for _ in range(5): bar._poll_job()
    assert bar._job_reader.submit.call_count == 1
    assert bar.after.call_count == bar._refresh_timing_status.call_count == 5
    assert not bar._finish_job.called


def test_slow_read_identifies_last_snapshot_without_inventing_new_progress():
    bar = toolbar()
    bar._job_snapshot = ('job',running(),time.monotonic()-4)
    bar._poll_job()
    assert '上次进度' in bar.set_status.call_args[0][0]
    assert '23/100' in bar.set_status.call_args[0][0]


def test_refresh_callback_exception_does_not_break_poll_and_is_retried():
    bar = toolbar()
    bar._load_market_status.side_effect = [sqlite3.OperationalError('database is locked'),None]
    value = running(result=json.dumps({'sources':{'tickflow':{'phase':'完成'}}}))
    reply(bar,value)
    assert bar.after.call_count == 1 and not bar._finish_job.called
    reply(bar,value)
    assert bar._load_market_status.call_count == 2
    assert bar._premarket_progress['tickflow']['phase'] == '完成'
    assert bar.after.call_count == 2


@pytest.mark.parametrize('bad_result',['not json','[]','{"sources":[]}'])
def test_malformed_display_payload_is_logged_not_misreported_as_task_failure(bad_result,caplog):
    bar = toolbar()
    reply(bar,running(result=bad_result))
    assert not bar._finish_job.called and bar._job_id == 'job'
    assert '任务进度显示异常' in caplog.text
    reply(bar,running())
    assert '23/100' in bar.set_status.call_args[0][0]
    assert bar.after.call_count == 2


def test_only_successful_empty_read_reports_missing_job():
    bar = toolbar()
    reply(bar,error=sqlite3.OperationalError('locked'))
    assert not bar._finish_job.called
    reply(bar,None)
    assert bar._finish_job.call_args[0][0] == 'failed'


def test_old_job_result_cannot_finish_current_job():
    bar = toolbar()
    reply(bar,dict(running(),status='completed'),job='previous-job')
    assert not bar._finish_job.called and bar._job_id == 'job'


def test_terminal_receipt_finishes_once_and_chain_has_no_extra_timer():
    bar = toolbar()
    bar._finish_job.side_effect = lambda *_:setattr(bar,'_job_id','next-job')
    reply(bar,dict(running(),status='completed',message='完成'))
    bar._finish_job.assert_called_once()
    assert not bar.after.called


def test_job_view_reads_only_existing_database_and_preserves_bytes(store):
    store.execute("INSERT INTO jobs(id,kind,status,created,spec) VALUES('job','sync','running','now','{}')")
    before = store.path.read_bytes()
    assert load_job(store.root,'job',lambda:None)['status'] == 'running'
    assert load_job(store.root,"job' OR 1=1 --",lambda:None) is None
    assert store.path.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        load_job(store.root/'missing','job',lambda:None)
    assert not (store.root/'missing').exists()


def test_premarket_progress_names_source_stage_and_stock_count_not_zero_percent():
    bar = toolbar()
    item = dict(phase='更新',progress=690,total=3199)
    reply(bar,running(progress=0,total=1,result=json.dumps({'sources':{'tickflow':item}})))
    text = bar.set_status.call_args[0][0]
    assert 'TickFlow正在更新行情：已处理690只，共3199只；随后自动扫描' in text
    assert '0/1' not in text and '0%' not in text
    item.update(phase='扫描',progress=20)
    reply(bar,running(result=json.dumps({'sources':{'tickflow':item}})))
    assert '正在策略扫描：已处理20只，共3199只' in bar.set_status.call_args[0][0]


def test_multiple_source_progress_does_not_claim_failed_source_completed():
    from gui.task_progress import premarket_progress
    text = premarket_progress({'baostock':{'phase':'失败','message':'限制访问'},
                              'tickflow':{'phase':'扫描','progress':50,'total':100}},1,2)
    assert '本次共2个数据源，已结束1个' in text
    assert 'BaoStock：失败，限制访问' in text
    assert 'TickFlow正在策略扫描' in text
