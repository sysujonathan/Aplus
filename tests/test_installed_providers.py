"""Frozen provider dispatch must never start a second desktop."""
import io
import json
import sys
from unittest.mock import Mock

import pytest

from workbench import process_entry, provider_worker, installed_imports, installed_runtime
from tools import installed_entry


@pytest.mark.parametrize('vendor',['baostock','tickflow','tencent'])
def test_source_and_frozen_provider_commands(monkeypatch,vendor):
    monkeypatch.setattr(sys,'frozen',False,raising=False)
    assert process_entry.provider_command(vendor)==[
        sys.executable,'-u','-m','workbench.provider_worker',vendor]
    monkeypatch.setattr(sys,'frozen',True)
    assert process_entry.provider_command(vendor)==[sys.executable,'--provider-worker',vendor]


@pytest.mark.parametrize('vendor',['baostock','tickflow','tencent'])
def test_installed_dispatch_does_not_open_desktop(monkeypatch,vendor):
    monkeypatch.setattr(sys,'argv',['AplusApp.exe','--provider-worker',vendor])
    monkeypatch.setattr(installed_imports,'install',lambda:None)
    restore=Mock();worker=Mock();desktop=Mock(side_effect=AssertionError('duplicate desktop'))
    monkeypatch.setattr(process_entry,'restore_worker_stdio',restore)
    monkeypatch.setattr(provider_worker,'main',worker)
    monkeypatch.setattr(installed_runtime,'launch',desktop)
    installed_entry.main()
    restore.assert_called_once_with();worker.assert_called_once_with(vendor)
    desktop.assert_not_called()


@pytest.mark.parametrize('args',[
    ['-u','-m','workbench.provider_worker','tickflow'],['--unknown'],
    ['--provider-worker'],['--provider-worker','unknown']])
def test_unknown_worker_arguments_fail_without_desktop(monkeypatch,args):
    monkeypatch.setattr(sys,'argv',['AplusApp.exe',*args])
    monkeypatch.setattr(installed_imports,'install',lambda:None)
    desktop=Mock(side_effect=AssertionError('duplicate desktop'))
    monkeypatch.setattr(installed_runtime,'launch',desktop)
    with pytest.raises(ValueError):installed_entry.main()
    desktop.assert_not_called()


def test_worker_explicit_vendor_and_error_survive_ipc(monkeypatch):
    # TickFlow login/logout and invalid batch do not issue any network request.
    requests=[dict(operation='login',args=[]),
              dict(operation='batch',args=[[],'2026-10-29','2026-10-30']),
              dict(operation='logout',args=[])]
    output=io.StringIO()
    monkeypatch.setattr(sys,'argv',['AplusApp.exe','--provider-worker','tickflow'])
    monkeypatch.setattr(sys,'stdin',io.StringIO('\n'.join(map(json.dumps,requests))+'\n'))
    monkeypatch.setattr(sys,'stdout',output)
    from workbench import tickflow
    monkeypatch.setattr(tickflow.TickFlowHTTP,'request',Mock(side_effect=AssertionError('no network')))
    provider_worker.main('tickflow')
    replies=[json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]==replies[2]=={'data':None}
    assert replies[1]['error']['source']=='tickflow'
    assert '1～100' in replies[1]['error']['error']
