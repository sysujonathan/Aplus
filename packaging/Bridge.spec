# This binary is an actual schema-eight bootstrap; the schema-nine UI is primary/.
from pathlib import Path
import os
root=Path(SPECPATH).parent
a=Analysis([str(root/'tools/installed_bridge_entry.py')],pathex=[str(root)],
    datas=[(os.environ['APLUS_BUILD_METADATA'],'.')],
    excludes=['workbench.store','workbench.installed_migration','core','config','gui',
              'pandas','numpy','matplotlib','streamlit','pytest','IPython'],
    module_collection_mode={'workbench':'py'})
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='AplusApp',console=False,
        icon=str(root/'icon_aplus.ico'),upx=False,version=os.environ['APLUS_BUILD_VERSION_FILE'])
coll=COLLECT(exe,a.binaries,a.datas,name='AplusBridge',upx=False)
