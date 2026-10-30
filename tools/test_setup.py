"""Install/uninstall a real Setup in an isolated path, retaining synthetic data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def execute(command,**kwargs):
    result=subprocess.run(list(map(str,command)),timeout=180,**kwargs)
    return result.returncode


def main():
    parser=argparse.ArgumentParser();parser.add_argument('setup',type=Path)
    args=parser.parse_args();setup=args.setup.resolve()
    folder=ROOT/'.test-installer'/('setup-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    from workbench.store import Store
    from workbench.installed_data import bind_existing, bound_runtime
    runtime=folder/'original data';store=Store(runtime)
    store.execute("INSERT INTO watchlist(code,observation_id,notes,active,created,updated) VALUES('sz.000001','','synthetic',1,'now','now')")
    before=hashlib.sha256(store.path.read_bytes()).hexdigest()
    home=folder/'profile';bind_existing(runtime,home=home)
    env=dict(os.environ,APLUS_INSTALL_STATE=str(home),APLUS_INSTALL_SMOKE='1')
    target=folder/'程序 App'
    common=['/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS','/TASKS=']
    bad=folder/'nonempty';bad.mkdir();(bad/'keep.txt').write_text('retain')
    denied=execute([setup,*common,'/DIR='+str(bad),'/LOG='+str(folder/'rejected.log')],env=env)
    assert denied in (1,2,5,7,8) and (bad/'keep.txt').read_text()=='retain', denied
    assert not (bad/'Aplus.exe').exists()
    # A recognizable installation must not bypass nested user-data detection.
    nested=folder/'nested-data';(nested/'custom'/'saved').mkdir(parents=True)
    (nested/'aplus-install.json').write_text('{"protocol":1,"app_id":"Aplus-installed-v1"}')
    important=nested/'custom'/'saved'/'workbench.sqlite3';important.write_bytes(b'protected nested data')
    assert execute([setup,*common,'/DIR='+str(nested)],env=env)!=0
    assert important.read_bytes()==b'protected nested data' and not (nested/'Aplus.exe').exists()
    code=execute([setup,*common,'/DIR='+str(target),'/LOG='+str(folder/'install.log')],env=env)
    assert code==0, (folder/'install.log').read_text(errors='replace')[-5000:]
    assert (target/'Aplus.exe').exists()
    version=(target/'current.txt').read_text().strip()
    application=target/'app'/version/'AplusApp.exe'
    report=folder/'installed-self-test.json'
    assert execute([application,'--self-test',report],env=env)==0
    assert json.loads(report.read_text())['chart_assets']
    assert bound_runtime(home=home)==runtime
    assert hashlib.sha256(store.path.read_bytes()).hexdigest()==before
    gui=folder/'installed-gui'
    assert execute([application,'--smoke-test',gui],env=env)==0
    assert json.loads((gui/'result.json').read_text())['ok']
    assert execute([target/'unins000.exe',*common,'/LOG='+str(folder/'uninstall.log')],env=env)==0
    assert not (target/'current.txt').exists() and not (target/'previous.txt').exists()
    # Normal uninstall -> install into the very same default-shaped folder.
    assert execute([setup,*common,'/DIR='+str(target),'/LOG='+str(folder/'reinstall.log')],env=env)==0
    assert (target/'current.txt').read_text().strip()==version
    assert execute([target/'unins000.exe',*common],env=env)==0
    assert hashlib.sha256(store.path.read_bytes()).hexdigest()==before
    assert bound_runtime(home=home)==runtime
    result=dict(installed=True,webview=True,nonempty_blocked=True,nested_data_blocked=True,reinstalled=True,uninstalled=True,
                original_data_unchanged=True,folder=str(folder),synthetic=True)
    (folder/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
