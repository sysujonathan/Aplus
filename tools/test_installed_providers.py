"""Offline IPC probe against the real windowed EXE, no vendor/data access."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(application,folder):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,LOCALAPPDATA=str(folder/'local'),APLUS_INSTALL_STATE=str(folder/'state'))
    proof={}
    for vendor in ('baostock','tickflow','tencent'):
        requests=[dict(operation='invalid_ipc_probe',args=[])]
        if vendor!='baostock':
            requests.insert(0,dict(operation='login',args=[]))
            if vendor=='tickflow':
                requests.append(dict(operation='batch',args=[[],'2026-10-29','2026-10-30']))
            requests.append(dict(operation='logout',args=[]))
        result=subprocess.run([str(application),'--provider-worker',vendor],
            input='\n'.join(map(json.dumps,requests))+'\n',capture_output=True,text=True,
            encoding='utf-8',env=env,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode==0,(vendor,result.stderr)
        responses=[json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses)==len(requests),(vendor,responses)
        rejected=responses[0 if vendor=='baostock' else 1]['error']
        assert rejected['source']==vendor and 'Unknown provider operation' in rejected['error']
        if vendor!='baostock':assert responses[0]==responses[-1]=={'data':None}
        if vendor=='tickflow':assert '1～100' in responses[2]['error']['error']
        proof[vendor]=dict(ipc=True,login_logout=vendor!='baostock',no_network=True)

    # Use the real parent adapter to prove cancellation terminates its own
    # EXE child and releases its isolated lease; never touch a user process.
    from workbench import provider_process
    provider=provider_process.BaoStock();provider.vendor='tickflow';provider.lock_root=folder/'locks'
    with patch.dict(os.environ,env), \
         patch.object(provider_process,'provider_command',lambda v:[str(application),'--provider-worker',v]):
        with provider:
            child=provider.process
            provider.cancel_event.set()
            try:provider._query('batch',[[],'2026-10-29','2026-10-30'])
            except InterruptedError:pass
            else:raise AssertionError('cancel did not stop the worker')
        assert child.poll() is not None and provider.lease is None
    proof['cancel_terminated_child']=True
    for args in (['--provider-worker'],['--provider-worker','invalid'],['--unknown']):
        result=subprocess.run([str(application),*args],capture_output=True,env=env,
                              timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode!=0
    proof['bad_arguments_no_desktop']=True
    (folder/'result.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps(dict(ok=True,folder=str(folder),proof=proof)))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('application',type=Path)
    args=parser.parse_args()
    run(args.application.resolve(),ROOT/'.test-installer'/('provider-'+uuid.uuid4().hex[:8]))
