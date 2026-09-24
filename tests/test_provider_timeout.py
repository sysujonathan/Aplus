import subprocess
import sys
import threading
import time

import pytest

from workbench.provider_process import BaoStock
from tests.test_workbench import store, wait_for
from workbench.service import Service
import json


def fake_worker(monkeypatch, hang):
    real_popen = subprocess.Popen
    processes = []
    script = ('import sys,json,time\n'
              'for line in sys.stdin:\n'
              ' op=json.loads(line)["operation"]\n'
              f' if op=={hang!r}: time.sleep(90)\n'
              ' print(json.dumps({"data": []}),flush=True)\n')
    def popen(args, **kwargs):
        process=real_popen([sys.executable,'-u','-c',script],**kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess,'Popen',popen)
    return processes


@pytest.mark.parametrize('operation',['login','fetch','logout'])
def test_real_hung_process_is_terminated(monkeypatch,operation):
    processes=fake_worker(monkeypatch,operation)
    provider=BaoStock()
    provider.login_timeout=1
    provider.query_timeout=.3
    provider.logout_timeout=.3
    started=time.monotonic()
    if operation=='logout':
        with provider: pass
    else:
        with pytest.raises(TimeoutError):
            with provider:
                provider.fetch('sh.600000','2020-01-01','2020-02-01')
    assert time.monotonic()-started<5
    assert all(p.poll() is not None for p in processes)


def test_stop_interrupts_wait_without_waiting_for_timeout(monkeypatch):
    processes=fake_worker(monkeypatch,'fetch')
    provider=BaoStock()
    with provider:
        timer=threading.Timer(.5,provider.cancel_event.set)
        timer.start()
        started=time.monotonic()
        with pytest.raises(InterruptedError):
            provider.fetch('sh.600000','2020-01-01','2020-02-01')
        timer.join()
        assert time.monotonic()-started<4
    assert all(p.poll() is not None for p in processes)


@pytest.mark.parametrize('error,expected',[('请求超时',3),('黑名单封禁',1)])
def test_batch_stops_without_claiming_completion(store,monkeypatch,error,expected):
    calls=[]
    class Broken:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def fetch(self,*args):
            calls.append(args)
            raise TimeoutError(error)
    monkeypatch.setattr('workbench.service.BaoStock',Broken)
    service=Service(store)
    row=wait_for(store,service.submit('sync',{'codes':['sh.600000','sh.600001','sh.600002','sh.600003'],
                                            'start':'2020-01-01','end':'2020-02-01'}))
    service.pool.shutdown()
    report=json.loads(row['result'])
    assert row['status']=='partial' and len(calls)==expected
    assert report['remaining']==4-expected and report['success']==0
    assert report['stop_reason'] in row['message']
