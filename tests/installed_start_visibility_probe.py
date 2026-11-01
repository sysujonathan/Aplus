"""Exercise the actual installed-runtime confirmation against hidden/visible Tk."""
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import tkinter as tk

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench import installed_runtime as runtime, installed_update
root=Path(sys.argv[1]);home=root/'synthetic-state';ack=home/'updates/attempt/started.json'
os.environ.update(APLUS_INSTALL_STATE=str(home),APLUS_INSTALLED_ACK=str(ack),APLUS_INSTALL_ROOT=str(root/'program'))
runtime.bound_runtime=lambda:root/'synthetic-runtime'
installed_update.current_release=lambda:dict(version='2026.11.01.3',commit='b'*40,database_schema=9)
proof=dict(hidden_not_confirmed=False,visible_confirmed=False);failures=[]

class Window(tk.Tk):
    def __init__(self):
        super().__init__();self.withdraw()
        self.service=object();self.store=SimpleNamespace(root=root/'synthetic-runtime')
        def hidden():
            try:
                assert not self.winfo_ismapped() and not ack.exists()
                proof['hidden_not_confirmed']=True
                self.deiconify()
            except Exception as exc:failures.append(str(exc));self.destroy()
        def visible():
            try:
                assert self.winfo_ismapped()
                value=json.loads(ack.read_text(encoding='utf-8'))
                assert value['started'] is True and value['database_schema']==9
                proof['visible_confirmed']=True
            except Exception as exc:failures.append(str(exc))
            self.destroy()
        self.after(350,hidden);self.after(850,visible)

import gui
module=ModuleType('gui.main_window');module.AplusMainWindow=Window
sys.modules['gui.main_window']=module;gui.main_window=module
import launch_dashboard
launch_dashboard.main=lambda:gui.main_window.AplusMainWindow().mainloop()
runtime.launch()
assert not failures,failures
assert all(proof.values()),proof
print(json.dumps(proof))
