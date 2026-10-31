"""Truthful legacy bootstrap identity and its separately declared primary payload."""
import copy
import json
from pathlib import Path
import re

from .release_identity import read_identity

BRIDGE_COMPONENT='schema_bridge'
PRIMARY_ENTRY='primary/AplusApp.exe'


def bridge_plan(value):
    plan=value.get('upgrade')
    if (value.get('component')!=BRIDGE_COMPONENT or value.get('database_schema')!=8
            or type(plan) is not dict or set(plan)!={'protocol','from_schema','to_schema','entry','inventory_sha256'}
            or type(plan.get('protocol')) is not int or plan['protocol']!=1
            or type(plan.get('from_schema')) is not int or plan['from_schema']!=8
            or type(plan.get('to_schema')) is not int or plan['to_schema']!=9
            or plan.get('entry')!=PRIMARY_ENTRY
            or not isinstance(plan.get('inventory_sha256'),str)
            or not re.fullmatch('[0-9a-f]{64}',plan['inventory_sha256'])):
        raise ValueError('安装包的数据升级入口声明不完整，原版本继续可用。')
    return plan


def primary_target(bundle):
    plan=bridge_plan(bundle)
    return dict(copy.deepcopy(bundle),database_schema=plan['to_schema'],bridge_manifest=copy.deepcopy(bundle))


def canonical_manifest(target):
    bundle=target.get('bridge_manifest')
    if bundle is None:return target
    if type(bundle) is not dict or 'bridge_manifest' in bundle or target!=primary_target(bundle):
        raise ValueError('主程序与兼容升级入口身份不一致。')
    return bundle


def verify_payload(root, target):
    """Bind every installed byte to the hash in the official release manifest."""
    from .installed_cleanup import contents
    from .installed_data import file_sha256
    bundle=canonical_manifest(target);plan=bridge_plan(bundle);root=Path(root)
    inventory=root/'installed-files.json'
    if file_sha256(inventory)!=plan['inventory_sha256']:
        raise ValueError('安装包文件清单与正式发布不一致。')
    value=json.loads(inventory.read_text(encoding='utf-8'))
    files,directories=contents(root)
    if (value.get('protocol')!=1 or set(value.get('files',{}))!=set(files)
            or set(value.get('directories',[]))!=directories
            or any(file_sha256(path)!=value['files'][name] for name,path in files.items())):
        raise ValueError('安装包程序文件不完整，未升级数据库。')
    bridge=read_identity(root/'_internal');primary=read_identity(root/'primary'/'_internal')
    for key in ('version','sequence','commit','preview'):
        if bridge.get(key)!=bundle.get(key) or primary.get(key)!=target.get(key):
            raise ValueError('安装包主程序身份不匹配。')
    if (bridge.get('component')!=BRIDGE_COMPONENT or bridge.get('database_schema')!=8
            or primary.get('database_schema')!=9 or primary.get('component')==BRIDGE_COMPONENT):
        raise ValueError('安装包数据兼容身份不匹配。')
    return root/PRIMARY_ENTRY


def installed_identity(root, version, schema):
    path=Path(root)/'app'/version
    value=read_identity(path/'_internal')
    if value.get('component')==BRIDGE_COMPONENT and schema==9:
        primary=read_identity(path/'primary'/'_internal')
        if any(primary.get(k)!=value.get(k) for k in ('version','sequence','commit','preview')):
            raise ValueError('当前主程序身份不一致。')
        if primary.get('database_schema')!=9:raise ValueError('当前数据版本不一致。')
        return primary
    return value
