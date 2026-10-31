"""Synthetic release authority for preview-only, path-restricted EXE integration.

Official payloads cannot enter this probe. Production signature/main/hash policy
is exercised separately; installation, migration, processes and ACK stay real.
"""
import copy
import json
import os
from pathlib import Path


def install_authority(path, root=None, runtime=None):
    from workbench import installed_update as update
    from workbench.release_identity import read_identity
    from workbench.installed_data import state_home
    if read_identity().get('preview') is not True:
        raise ValueError('Official EXEs cannot enter preview authority probes')
    path=Path(path).resolve();folder=path.parent
    if (path.name!='probe.json' or folder.parent.name!='.test-installer'
            or not folder.name.startswith('bridge-probe-') or path.stat().st_size>32768):
        raise ValueError('Not a marked isolated probe path')
    value=json.loads(path.read_text(encoding='utf-8'))
    if (path.name!='probe.json' or folder.parent.name!='.test-installer'
            or not folder.name.startswith('bridge-probe-') or value.get('synthetic_schema_bridge') is not True
            or Path(value['install_root']).resolve()!=folder/'program'
            or Path(value['runtime']).resolve()!=folder/'runtime'
            or state_home().resolve()!=folder/'state'
            or (root is not None and Path(root).resolve()!=folder/'program')
            or (runtime is not None and Path(runtime).resolve()!=folder/'runtime')):
        raise ValueError('Only a preview EXE and its marked synthetic paths may use this probe')
    for item in (folder,folder/'program',folder/'runtime',folder/'state'):
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):raise ValueError('Linked probe path')
    target=value['target']
    if target.get('preview') is not True:raise ValueError('Probe must keep preview identity truthful')
    original=update.validate_manifest
    def preview(value):
        if value.get('preview') is not True:return original(value)
        checked=copy.deepcopy(value);checked['preview']=False
        if 'bridge_manifest' in checked:checked['bridge_manifest']['preview']=False
        original(checked)
        return value
    preview(target)
    update.validate_manifest=preview
    update.load_manifest=lambda:target
    return target


def run(request):
    from workbench import installed_update as update
    from workbench.installed_data import atomic_json,inspect_data,file_sha256,state_home
    request=Path(request).resolve();value=json.loads(request.read_text(encoding='utf-8'))
    target=install_authority(os.environ['APLUS_SCHEMA_BRIDGE_PROBE'],value['install_root'],value['runtime'])
    if (request.parent.parent.name!='updates' or request.name!='request.json' or value['target']!=target
            or value.get('bundle_handoff') is not True):raise ValueError('Invalid isolated handoff request')
    settings=json.loads(Path(os.environ['APLUS_SCHEMA_BRIDGE_PROBE']).read_text(encoding='utf-8'))
    fault=settings.get('fault')
    if fault not in (None,'launch_failed'):raise ValueError('Unknown preview-only fault')
    if fault:
        original=update.subprocess.Popen;root=Path(value['install_root']);runtime=Path(value['runtime'])
        primary=root/'app'/target['version']/'AplusApp.exe'
        def start(command,**kwargs):
            if Path(command[0])==primary:raise OSError('Explicit isolated probe: primary launch failed')
            plan=json.loads((request.parent/'migration/receipt.json').read_text(encoding='utf-8'))
            ack=state_home()/'updates'/'rollback-start'/'started.json';ack.parent.mkdir(parents=True,exist_ok=True)
            proof=dict(restored_original_before_restart=file_sha256(runtime/'workbench.sqlite3')==plan['original_sha256'],
                       schema=inspect_data(runtime)['schema'],ack=str(ack))
            atomic_json(request.parent/'fault-proof.json',proof)
            kwargs['env']=dict(kwargs.get('env',os.environ),APLUS_INSTALLED_ACK=str(ack))
            return original(command,**kwargs)
        update.subprocess.Popen=start
    return update.worker(request)
