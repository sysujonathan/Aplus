"""Bounded cleanup of build-owned payloads; never recursive-delete unknown data."""
import json
import os
from pathlib import Path
import re
import subprocess

from .installed_data import atomic_json, file_sha256
from .release_identity import read_identity, version_parts


def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


def contents(root):
    files={};directories=set()
    if linked(root):raise ValueError('linked payload')
    for directory,children,names in os.walk(root,followlinks=False):
        for name in children+names:
            if linked(Path(directory)/name):raise ValueError('linked payload item')
        directories.update((Path(directory)/name).relative_to(root).as_posix() for name in children)
        for name in names:
            path=Path(directory)/name;relative=path.relative_to(root).as_posix()
            if relative!='installed-files.json':files[relative]=path
        if len(files)>50000:raise ValueError('unexpected payload size')
    return files,directories


def write_inventory(root):
    root=Path(root);files,directories=contents(root)
    atomic_json(root/'installed-files.json',dict(protocol=1,
        files={name:file_sha256(path) for name,path in files.items()},directories=sorted(directories)))


def active_images():
    script="""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$items=@(Get-CimInstance Win32_Process -Filter \"Name='AplusApp.exe'\" | ForEach-Object {
 if(-not $_.ExecutablePath) { throw 'Unknown process identity' }; $_.ExecutablePath })
ConvertTo-Json -InputObject $items -Compress"""
    env={k:v for k,v in os.environ.items() if k.casefold()!='psmodulepath'}
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=env,capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    return {Path(p).resolve() for p in json.loads(result.stdout.decode('utf-8-sig'))}


def prune_versions(root,runtime,*,images=None):
    root=Path(root).resolve();base=root/'app';runtime=Path(runtime).resolve()
    if linked(base):return []
    keep=set()
    for name in ('current.txt','previous.txt'):
        value=(root/name).read_text().strip();version_parts(value);keep.add(value)
    candidates=[]
    for path in base.iterdir():
        if path.name in keep or not path.is_dir() or linked(path):continue
        try:
            version_parts(path.name)
            if path.resolve().parent!=base or runtime==path or path in runtime.parents or runtime in path.parents:continue
            if read_identity(path/'_internal')['version']!=path.name:continue
            inventory=json.loads((path/'installed-files.json').read_text(encoding='utf-8'))
            files,directories=contents(path)
            if any(p.name.casefold().startswith('workbench.sqlite3') for p in files.values()):continue
            expected=inventory['files']
            if inventory['protocol']!=1 or set(expected)!=set(files) or set(inventory['directories'])!=directories:continue
            if any(not re.fullmatch('[0-9a-f]{64}',value) or file_sha256(files[name])!=value for name,value in expected.items()):continue
            candidates.append((path,files,expected,directories))
        except (OSError,ValueError,KeyError,TypeError):continue
    if not candidates:return []
    running=active_images() if images is None else set(map(lambda p:Path(p).resolve(),images))
    removed=[]
    for path,files,expected,directories in candidates:
        if any(path==p or path in p.parents for p in running):continue
        try:
            for name,item in files.items():
                if item.resolve().parent!=path and path not in item.resolve().parents:raise ValueError('payload moved')
                if file_sha256(item)!=expected[name]:raise ValueError('payload changed')
                item.unlink()
            (path/'installed-files.json').unlink()
            for directory in sorted(directories,key=lambda p:len(Path(p).parts),reverse=True):(path/directory).rmdir()
            path.rmdir();removed.append(path.name)
        except (OSError,ValueError):pass  # Unknown/new files remain; never force removal.
    return removed


def finish_update(root,runtime,folder,target):
    # Cleanup can never turn a successful startup into a rollback.
    result=dict(removed=[],download_removed=False)
    try:
        setup=Path(folder)/'setup.exe';package=target['package']
        if setup.is_file() and setup.stat().st_size==package['bytes'] and file_sha256(setup)==package['sha256']:
            setup.unlink();result['download_removed']=True
        result['removed']=prune_versions(root,runtime)
    except Exception as exc:result['deferred']=str(exc)
    try:atomic_json(Path(folder)/'cleanup.json',result)
    except OSError:pass
