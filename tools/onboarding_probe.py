"""Drive only our isolated synthetic welcome window, never another app."""
import json
import tkinter as tk
from contextlib import nullcontext
from pathlib import Path


def run(runtime, folder, *, new=False, fail=False, cancel=False):
    from workbench import installed_runtime as bootstrap, installed_data as data, version_update
    from unittest.mock import patch
    folder=Path(folder)
    before=(runtime/'workbench.sqlite3').read_bytes()
    observed={'success':False, 'errors':[]}
    root_type=tk.Tk

    class ProbeWindow(root_type):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.withdraw()
            self.after(100,self.advance)
            self.after(10000,self.expired)

        def expired(self):
            observed['timeout']=True
            self.destroy()

        def advance(self):
            def children(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from children(child)
            widgets=list(children(self))
            texts=[str(w.cget('text')) for w in widgets if 'text' in w.keys()]
            labels=[str(w.getvar(w.cget('textvariable'))) for w in widgets
                    if 'textvariable' in w.keys() and w.cget('textvariable')]
            if cancel:
                self.after(150,self.destroy)
                return
            if '打开 Aplus' in texts:
                observed['success']=True
                observed['message']='\n'.join(labels)
                assert ('新数据位置已就绪' if new else '数据已安全接续') in observed['message']
                if not new:
                    assert str(runtime) in observed['message']
                    assert '备份位置：' in observed['message']
                next(w for w in widgets if 'text' in w.keys() and w.cget('text')=='打开 Aplus').invoke()
                return
            if observed['errors']:
                self.destroy()
                return
            if not observed.get('clicked') and not any('正在查找' in label for label in labels):
                observed['clicked']=True
                target='首次使用' if new else '保护并接续数据'
                next(w for w in widgets if 'text' in w.keys() and w.cget('text')==target).invoke()
            self.after(100,self.advance)

    def failure(*args,**kwargs):
        raise OSError('synthetic backup failure')

    # Only the synthetic test process substitutes the desktop mutex. A running
    # user's app need not close, and production binding retains its real lock.
    with patch.object(tk,'Tk',ProbeWindow), \
         patch.object(bootstrap,'discover_hints',lambda:[data.candidate_data(runtime)]), \
         patch.object(version_update,'application_mutex',nullcontext), \
         patch.object(version_update,'require_web_closed',lambda:None), \
         patch.object(bootstrap.messagebox,'askyesno',lambda *a,**kw:True), \
         patch.object(bootstrap.messagebox,'showerror',lambda *a,**kw:observed['errors'].append(a)), \
         patch.object(bootstrap,'bind_existing',failure if fail else data.bind_existing):
        result=bootstrap.choose_data(folder/'program')
    assert not observed.get('timeout'),observed
    assert (runtime/'workbench.sqlite3').read_bytes()==before
    profile=data.state_home()/'data.json'
    if fail or cancel:
        assert result is None and not profile.exists() and not observed['success']
        assert bool(observed['errors'])==fail
    else:
        assert observed['success'] and result==data.bound_runtime()
        value=json.loads(profile.read_text(encoding='utf-8'))
        if not new:
            assert Path(value['backup']).read_bytes()==before
    return observed
