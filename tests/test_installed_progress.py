"""A ready helper is not a ready desktop; long data protection remains visible."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from workbench.installed_data import atomic_json
from workbench.installed_progress import GUIDANCE, ProgressState, read_outcome


def test_elapsed_and_stage_do_not_end_during_long_data_protection():
    now=[100.0]
    state=ProgressState(lambda:now[0])
    state.stage='protecting';now[0]+=260
    assert state.elapsed()=='已用时 4分20秒'
    assert state.text()=='正在更新，请稍候。完成后自动打开。'
    assert GUIDANCE=='完成后自动打开。'
    state.stage='connecting'
    assert state.text()=='正在更新，请稍候。完成后自动打开。'
    state.stage='starting';now[0]+=3600
    assert state.elapsed()=='已用时 1小时4分20秒'
    assert state.text()=='正在更新，请稍候。完成后自动打开。'


@pytest.mark.parametrize('problem',['ready_only','missing_ack','wrong_version','wrong_commit','wrong_runtime','wrong_schema','corrupt_ack'])
def test_progress_never_finishes_from_ready_or_wrong_start_identity(tmp_path,problem):
    request=dict(target=dict(version='2026.11.01.3',commit='b'*40,database_schema=9,bridge_manifest={ 'protocol':1 }),
                 runtime=str(tmp_path/'synthetic-runtime'))
    ack=dict(started=True,version='2026.11.01.3',commit='b'*40,database_schema=9,runtime=request['runtime'])
    atomic_json(tmp_path/'ready.json',dict(ready=True))
    if problem!='ready_only':atomic_json(tmp_path/'receipt.json',dict(state='completed'))
    if problem not in ('ready_only','missing_ack'):
        field={'wrong_version':'version','wrong_commit':'commit','wrong_runtime':'runtime','wrong_schema':'database_schema'}.get(problem)
        if field:ack[field]=8 if field=='database_schema' else 'wrong'
        atomic_json(tmp_path/'started.json',ack)
        if problem=='corrupt_ack':(tmp_path/'started.json').write_text('{')
    assert read_outcome(tmp_path,request)==(None,None)


@pytest.mark.parametrize('receipt',['completed','startup_unconfirmed'])
def test_real_late_desktop_confirmation_ends_progress(tmp_path,receipt):
    request=dict(target=dict(version='2026.11.01.3',commit='b'*40,database_schema=9,bridge_manifest={'protocol':1}),
                 runtime=str(tmp_path/'synthetic-runtime'))
    atomic_json(tmp_path/'receipt.json',dict(state=receipt))
    assert read_outcome(tmp_path,request)==(None,None)
    atomic_json(tmp_path/'started.json',dict(started=True,version='2026.11.01.3',commit='b'*40,database_schema=9,runtime=request['runtime']))
    assert read_outcome(tmp_path,request)==('started',None)


@pytest.mark.parametrize('receipt',['rolled_back','blocked'])
def test_failure_is_not_presented_as_success(tmp_path,receipt):
    atomic_json(tmp_path/'receipt.json',dict(state=receipt,message='synthetic failure retained'))
    status,message=read_outcome(tmp_path,{})
    assert status=='error' and '未完成' in message and 'synthetic failure retained' in message


@pytest.mark.skipif(os.name!='nt',reason='Real Windows helper window')
@pytest.mark.native_process
@pytest.mark.parametrize('outcome',['success','late_start','failure','callback_failure'])
def test_native_helper_keeps_window_until_real_confirmation(tmp_path,outcome):
    script=Path(__file__).with_name('installed_progress_probe.py')
    result=subprocess.run([sys.executable,str(script),str(tmp_path),outcome],
        capture_output=True,text=True,encoding='utf-8',timeout=35,
        env=dict(os.environ,PYTHONIOENCODING='utf-8'))
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    proof=json.loads(result.stdout.strip().splitlines()[-1])
    assert proof['window_mapped_before_worker'] and proof['long_stage_visible']
    assert proof['old_desktop_exit_not_required_by_window'] and proof['no_early_close']
    assert proof['outcome']==outcome


@pytest.mark.skipif(os.name!='nt',reason='Real Windows desktop mapping')
@pytest.mark.native_process
def test_start_confirmation_requires_a_visible_native_window(tmp_path):
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('installed_start_visibility_probe.py')),str(tmp_path)],
        capture_output=True,text=True,encoding='utf-8',timeout=25,
        env=dict(os.environ,PYTHONIOENCODING='utf-8'))
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])==dict(hidden_not_confirmed=True,visible_confirmed=True)
