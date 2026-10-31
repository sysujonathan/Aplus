"""Small installed compatibility entry; never imports a business Store."""
import os
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode=True
try:
    from workbench.installed_bridge import main
    raise SystemExit(main())
except Exception:
    path=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'Aplus'/'logs'/'bridge-startup.log'
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(traceback.format_exc(),encoding='utf-8')
    if len(sys.argv)==1:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0,'更新接续尚未完成，原数据已保留。\n诊断：'+str(path),'Aplus',0x10)
    raise SystemExit(1)
