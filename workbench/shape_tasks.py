"""Independent low priority research queue; business jobs always take precedence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import uuid

from .shape_scan import FrameCache, scan
from .shape_store import read_meta, write_meta
from .store import now


class ShapeTasks:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='aplus-local-shape')
        self.cache = FrameCache()
        self.lock = threading.Lock()
        self.active = None
        self.closed = False
        self.pending_tracked = False
        old = read_meta(self.store,'shape_task',{})
        if old.get('state') in ('running','queued'):
            write_meta(self.store,'shape_task',dict(old,state='interrupted',message='上次程序退出；可按原条件重新匹配，已保存历史保留'))

    def status(self):
        return read_meta(self.store,'shape_task',{})

    def submit(self, templates, asof, source, *, checkpoint=None, automatic=False):
        with self.lock:
            if self.closed:
                raise ValueError('程序正在退出，形态任务未启动')
            if self.active:
                if automatic:
                    self.pending_tracked = True
                    return None
                raise ValueError('形态匹配正在进行，可先取消或等待完成')
            identifier = 'shape:'+uuid.uuid4().hex
            event = threading.Event()
            with self.service.lock:
                if self.service._code_update_reserved:
                    raise ValueError('正在准备软件更新，请更新完成后再匹配')
                self.service.cancel_flags[identifier] = event
            self.active = (identifier,event)
            write_meta(self.store,'shape_task',dict(id=identifier,state='queued',asof=asof,source=source,
                spec=dict(templates=templates,asof=asof,source=source),checkpoint=checkpoint,
                message='等待盘前任务完成后在本地匹配',created=now(),progress=0,total=0))
            self.pool.submit(self._run,identifier,event,templates,asof,source,checkpoint)
            return identifier

    def cancel(self):
        with self.lock:
            if self.active:
                self.active[1].set()

    def close(self):
        with self.lock:
            self.closed = True
            self.pending_tracked = False
        self.cancel()
        self.pool.shutdown(wait=True)

    def _run(self, identifier, event, templates, asof, source, checkpoint):
        def check():
            while True:
                if event.is_set():
                    raise InterruptedError('已取消形态匹配')
                with self.service.lock:
                    busy = any(not key.startswith(('shape:','chart:')) for key in self.service.cancel_flags)
                if not busy:
                    return
                event.wait(.1)
        last_progress = [0.]
        checkpoint_id = [checkpoint]
        spec = dict(templates=templates,asof=asof,source=source)
        def save_checkpoint(value):
            checkpoint_id[0] = value
            progress(0,0,'已保存恢复点')
        def progress(done,total,message):
            current = time.monotonic()
            if done not in (0,total) and current-last_progress[0]<.5:
                return
            last_progress[0] = current
            write_meta(self.store,'shape_task',dict(id=identifier,state='running',asof=asof,source=source,
                        spec=spec,checkpoint=checkpoint_id[0],
                        progress=done,total=total,message=message))
        try:
            check()
            report = scan(self.store,templates,asof,source,cancel=check,progress=progress,cache=self.cache,
                          checkpoint=checkpoint,on_checkpoint=save_checkpoint)
            write_meta(self.store,'shape_task',dict(id=identifier,state=report['status'],asof=asof,source=source,
                run_id=report['id'],message=f"形态匹配：{len(report['matches'])} 项相似结果（非交易信号）",
                spec=spec,checkpoint=report['id'],timings=report['timings'],progress=len(report['inputs']),finished=now()))
        except InterruptedError:
            write_meta(self.store,'shape_task',dict(id=identifier,state='cancelled',message='形态匹配已取消；历史记录保留',asof=asof,source=source,
                                                 spec=spec,checkpoint=checkpoint_id[0]))
        except Exception as exc:
            write_meta(self.store,'shape_task',dict(id=identifier,state='failed',message=str(exc),asof=asof,source=source,
                                                 spec=spec,checkpoint=checkpoint_id[0]))
        finally:
            with self.service.lock:
                self.service.cancel_flags.pop(identifier,None)
            with self.lock:
                self.active = None
                follow = self.pending_tracked and not self.closed
                self.pending_tracked = False
            if follow:
                self.tracked()

    def tracked(self):
        from .sources import market_source
        source = market_source(self.store)
        templates = [t for t in self.store.shape_templates() if t['tracking']]
        rows = self.store.rows("SELECT MAX(end) AS day FROM datasets WHERE source=? AND timeframe='daily'",(source,))
        if not templates or not rows or not rows[0]['day']:
            return
        asof = rows[0]['day']
        # Same exact inputs/settings can be resumed/reused, but a new snapshot
        # is always verified and scored. No remote service is involved.
        checkpoint = next((r['id'] for r in self.store.shape_runs(source) if r['asof']==asof),None)
        return self.submit(templates,asof,source,checkpoint=checkpoint,automatic=True)


def runner(service):
    with service.lock:
        if not getattr(service,'_shape_tasks',None):
            service._shape_tasks = ShapeTasks(service)
        return service._shape_tasks
