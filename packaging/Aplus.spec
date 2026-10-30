# Build with the locked Windows x64 / Python 3.12 environment.
from pathlib import Path
import json
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

root = Path(SPECPATH).parent
datas = []
datas.append((os.environ['APLUS_BUILD_METADATA'], '.'))
for name in ('docs', 'workbench/evidence'):
    datas.append((str(root/name), name))
for name in ('frozen_manifest.json', 'release_catalog.json', 'release_installation.json',
             'launch_dashboard.py', 'app.py', 'icon_aplus.ico'):
    datas.append((str(root/name), '.'))
# Preserve every reviewed frozen byte. These are also the actual imported .py
# modules: py collection mode excludes our modules from the compiled PYZ.
for item in json.loads((root/'frozen_manifest.json').read_text(encoding='utf-8'))['files']:
    path = Path(item['path'])
    datas.append((str(root/path), str(path.parent)))
for package in ('ttkbootstrap', 'webview', 'rapidocr_onnxruntime'):
    datas += collect_data_files(package)
hidden = []
for package in ('gui', 'workbench', 'core', 'config', 'webview', 'clr_loader'):
    hidden += collect_submodules(package, filter=lambda name: name != 'workbench.dashboard')
hidden += ['tools.installer_smoke', 'tools.onboarding_probe']
a = Analysis([str(root/'tools/installed_entry.py')], pathex=[str(root)],
    datas=datas, hiddenimports=hidden, excludes=['streamlit', 'IPython', 'pytest'],
    module_collection_mode={name: 'py' for name in ('gui','workbench','core','config')})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AplusApp',
    console=False, icon=str(root/'icon_aplus.ico'), upx=False,
    version=os.environ['APLUS_BUILD_VERSION_FILE'])
coll = COLLECT(exe, a.binaries, a.datas, name='AplusApp', upx=False)
