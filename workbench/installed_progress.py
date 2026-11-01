"""A visible window owned by the update helper, never by the exiting desktop."""
import json
from pathlib import Path
import queue
import threading
import time
import traceback


GUIDANCE = '完成后自动打开。'
WAIT_MESSAGE = '正在更新，请稍候。'+GUIDANCE


class ProgressState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.stage = 'waiting'
        self.error = None

    def elapsed(self):
        seconds = max(0, int(self.clock()-self.started))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return ('已用时 '+(f'{hours}小时' if hours else '')+f'{minutes}分{seconds:02d}秒')

    def text(self):
        return self.error or WAIT_MESSAGE


def read_outcome(folder, request):
    """A spawned process or a ready marker is never a successful UI startup."""
    from .installed_update import confirmed_start
    try:
        receipt = json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, None
    state = receipt.get('state')
    if state in ('completed', 'startup_unconfirmed'):
        if confirmed_start(folder, request['target'], request['runtime']):
            return 'started', None
        return None, None
    if state in ('rolled_back', 'blocked'):
        lead = '本次更新未完成，已恢复原版本。' if state == 'rolled_back' else '本次更新尚未完成。'
        return 'error', lead+'\n'+receipt.get('message', '请保留原数据，稍后重试。')
    return None, None


def run_update(request_path, *, worker=None):
    """Tk stays in this helper's main thread while its original transaction runs."""
    from tkinter import ttk
    import tkinter as tk
    from .installed_data import state_home
    from .installed_runtime import setup_window
    if worker is None:
        from .installed_update import worker
    path = Path(request_path).resolve()
    if path.name != 'request.json' or path.parent.parent != state_home()/'updates':
        raise ValueError('更新请求位置无效。')
    request = json.loads(path.read_text(encoding='utf-8'))
    window = setup_window()
    window.title('Aplus 正在更新')
    state = ProgressState()
    status = tk.StringVar(window, value=state.text())
    elapsed = tk.StringVar(window, value=state.elapsed())
    body = ttk.Frame(window, padding=24)
    body.pack(fill='both', expand=True)
    ttk.Label(body, textvariable=status, wraplength=535).pack(anchor='w', pady=(0, 12))
    bar = ttk.Progressbar(body, mode='indeterminate')
    bar.pack(fill='x');bar.start(15)
    ttk.Label(body, textvariable=elapsed).pack(anchor='w', pady=(12, 8))
    guidance = ttk.Label(body, wraplength=535)
    close = ttk.Button(body, text='关闭提示', command=window.destroy)
    window.update_idletasks()
    window.minsize(590, window.winfo_reqheight())
    messages = queue.Queue()
    done = [False]

    def work():
        try:
            worker(path, progress=lambda stage: messages.put(('stage', stage)))
            messages.put(('finished', None))
        except Exception as exc:
            try:(path.parent/'progress-error.log').write_text(traceback.format_exc(), encoding='utf-8')
            except OSError:pass
            messages.put(('error', '更新未能完成。\n'+str(exc)))

    def show_error(message):
        state.error = message
        bar.stop()
        guidance.configure(text='请按上方提示处理。关闭此提示不会删除原数据。')
        guidance.pack(anchor='w')
        close.pack(anchor='e', pady=(8, 0))
        window.protocol('WM_DELETE_WINDOW', window.destroy)

    def poll():
        while True:
            try:kind, value = messages.get_nowait()
            except queue.Empty:break
            if kind == 'stage':state.stage = value
            elif kind == 'finished':done[0] = True
            elif kind == 'error':done[0] = True;show_error(value)
        if (done[0] or state.stage == 'started') and state.error is None:
            outcome, detail = read_outcome(path.parent, request)
            if outcome == 'started':window.destroy();return
            if outcome == 'error':show_error(detail)
            else:
                state.stage = 'starting'
        status.set(state.text());elapsed.set(state.elapsed())
        window.after(200, poll)

    # Never create ready.json until this window is actually mapped. The old
    # desktop (or bootstrap) may exit as soon as it sees that marker.
    def start():
        if not window.winfo_ismapped():window.after(50, start);return
        threading.Thread(target=work, daemon=False).start()
        poll()
    window.protocol('WM_DELETE_WINDOW', lambda: None)
    window.after(50, start)
    window.mainloop()
