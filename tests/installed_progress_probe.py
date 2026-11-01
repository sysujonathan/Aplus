"""Real Tk process, synthetic receipts only; no application/Store is launched."""
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench import installed_progress as ui, installed_runtime
from workbench.installed_data import atomic_json

root=Path(sys.argv[1]);outcome=sys.argv[2]
home=root/'synthetic-state';folder=home/'updates/attempt';folder.mkdir(parents=True)
os.environ['APLUS_INSTALL_STATE']=str(home)
request=dict(target=dict(version='2026.11.01.3',commit='b'*40,database_schema=9,bridge_manifest={'protocol':1}),runtime=str(root/'synthetic-runtime'))
path=folder/'request.json';atomic_json(path,request)
proof=dict(outcome=outcome,window_mapped_before_worker=False,long_stage_visible=False,
           old_desktop_exit_not_required_by_window=False,no_early_close=False)
failures=[];window=[None]
original_window=installed_runtime.setup_window
clock=[100.0]
original_state=ui.ProgressState
ui.ProgressState=lambda:original_state(lambda:clock[0])
main_thread=threading.get_ident();parent_exited=threading.Event()
def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)
def texts():
    values=[]
    for item in descendants(window[0]):
        try:values.append(str(item.cget('text')))
        except Exception:pass
    return '\n'.join(values)
def check():
    try:
        assert threading.get_ident()==main_thread
        text=texts()
        assert window[0].winfo_ismapped()
        assert '正在更新，请稍候。完成后自动打开。' in text and '已用时 4分20秒' in text
        assert all(detail not in text for detail in ('备份','校验','接续'))
        assert ui.GUIDANCE in text
        proof['long_stage_visible']=True
        proof['no_early_close']=True
        assert not parent_exited.is_set()
        proof['old_desktop_exit_not_required_by_window']=True
        parent_exited.set()
        if outcome in ('failure','callback_failure'):
            window[0].after(650,check_failure)
    except Exception as exc:failures.append(str(exc));window[0].destroy()
def check_failure():
    try:
        assert '更新未能完成' in texts() or '本次更新未完成' in texts()
        assert window[0].winfo_ismapped()
        window[0].destroy()
    except Exception as exc:failures.append(str(exc));window[0].destroy()
def create():
    w=original_window();window[0]=w
    def mapped(event):
        if event.widget is w:proof['window_mapped_before_worker']=True
    w.bind('<Map>',mapped)
    w.after(650,check)
    w.after(15000,lambda:(failures.append('window did not terminate'),w.destroy()))
    return w
installed_runtime.setup_window=create
def worker(_path,*,progress):
    assert threading.get_ident()!=main_thread
    # No need to wait for any desktop process: this helper owns its own Tk.
    assert proof['window_mapped_before_worker']
    progress('protecting');clock[0]+=260
    atomic_json(folder/'ready.json',dict(ready=True))
    assert parent_exited.wait(10)
    if outcome=='callback_failure':raise RuntimeError('synthetic helper error')
    if outcome=='failure':
        atomic_json(folder/'receipt.json',dict(state='rolled_back',message='synthetic restore'))
        return
    progress('connecting');time.sleep(.2);progress('starting')
    atomic_json(folder/'receipt.json',dict(state='startup_unconfirmed' if outcome=='late_start' else 'completed'))
    def confirm():
        time.sleep(.5)
        atomic_json(folder/'started.json',dict(started=True,version=request['target']['version'],commit='b'*40,
            database_schema=9,runtime=request['runtime']))
    if outcome=='late_start':threading.Thread(target=confirm).start()
    else:confirm()
ui.run_update(path,worker=worker)
assert not failures,failures
assert proof['long_stage_visible'],proof
print(json.dumps(proof))
