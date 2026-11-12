"""Real Tk/WebView startup probe. Requires an explicitly isolated runtime copy."""
import argparse
import builtins
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare_copy(backup, assets, runtime):
    """Only a finished backup is accepted; no Store is opened on the source."""
    import shutil
    import sqlite3
    backup, assets, runtime = backup.resolve(), assets.resolve(), runtime.resolve()
    if runtime.exists() or runtime == assets or assets.is_relative_to(runtime):
        raise ValueError('Use a new independent destination directory')
    if any(p.exists() and p.stat().st_size for p in
           (backup.with_name(backup.name+'-wal'), backup.with_name(backup.name+'-journal'))):
        raise ValueError('Source backup must be static with no unfinished journal')
    runtime.mkdir(parents=True)
    with sqlite3.connect(backup.as_uri()+'?mode=ro', uri=True) as source:
        with sqlite3.connect(runtime/'workbench.sqlite3') as target:
            source.backup(target)
        paths = {r[0] for r in source.execute('SELECT DISTINCT path FROM datasets WHERE id IN '
                 '(SELECT dataset_id FROM observations) OR rowid IN '
                 '(SELECT rowid FROM (SELECT rowid,ROW_NUMBER() OVER '
                 '(PARTITION BY source,timeframe,code ORDER BY end DESC,created DESC,rowid DESC) n '
                 'FROM datasets) WHERE n=1)')}
    for name in ('directories','sources','reports','evidence','watch_history'):
        if (assets/name).is_dir():
            shutil.copytree(assets/name, runtime/name)
    copied = 0
    for relative in paths | {'universe.csv','trading_calendar.json'}:
        original, destination = (assets/relative).resolve(), runtime/relative
        if not original.is_relative_to(assets) or Path(relative).is_absolute():
            raise ValueError('Invalid immutable artifact path')
        if original.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination); copied += 1
    (runtime/'startup-probe-isolated.json').write_text(json.dumps(
        dict(source='finished backup + read-only immutable artifact copies', artifacts=copied)), encoding='utf-8')
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--layout', type=int, choices=(1, 4, 9), default=1)
    parser.add_argument('--seconds', type=float, default=25)
    parser.add_argument('--confirm-isolated-copy', action='store_true', required=True)
    parser.add_argument('--source-backup', type=Path)
    parser.add_argument('--assets-from', type=Path)
    parser.add_argument('--code-root', type=Path, default=ROOT)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if args.source_backup:
        if not args.assets_from:
            parser.error('--assets-from is required when preparing a copy')
        print(json.dumps({'artifacts_copied':prepare_copy(args.source_backup,args.assets_from,runtime)}))
        return
    if not (runtime/'startup-probe-isolated.json').is_file():
        parser.error('Only a marked isolated copy is accepted; never run on the official runtime')
    # Fixture setup is outside the observed startup; all pane identities refer
    # to existing real daily observations, never invented trading signals.
    import sqlite3
    with sqlite3.connect(runtime/'workbench.sqlite3') as db:
        db.row_factory=sqlite3.Row
        seed=runtime/'startup-probe-items.json'
        if not seed.exists():
            items=[];seen=set()
            for row in db.execute("SELECT o.id,o.code,o.strategy,o.asof,d.source,d.id dataset_id,d.end "
                "FROM observations o JOIN datasets d ON d.id=o.dataset_id WHERE o.timeframe='daily' "
                "ORDER BY o.asof DESC,o.created DESC LIMIT 300"):
                if row['code'] in seen:continue
                seen.add(row['code']);items.append(dict(code=row['code'],name=row['code'],
                    observation_id=row['id'],source=row['source'],mode='candidate',strategy=row['strategy'],
                    timeframe='daily',market_dataset_id=row['dataset_id'],market_asof=row['end'],anchor_asof=row['asof']))
                if len(items)==9:break
            seed.write_text(json.dumps(items),encoding='utf-8')
        items=json.loads(seed.read_text(encoding='utf-8'))
        row=db.execute("SELECT value FROM meta WHERE key='ui:chart_layouts'").fetchone()
        document=json.loads(row['value']) if row else {'revision':0,'data':{}}
        layouts=document['data'].get('layouts',[])
        if layouts:
            layout=layouts[0]
            layout['rows'],layout['columns']={1:(1,1),4:(2,2),9:(3,3)}[args.layout]
            layout['panes']=[dict(item=item,timeframe='daily',range=None) for item in items[:args.layout]]
            layout['active']=max(0,len(layout['panes'])-1)
            document['data']['active']=layout['id'];document['revision']+=1
            db.execute("UPDATE meta SET value=? WHERE key='ui:chart_layouts'",(json.dumps(document),))
    os.environ.update(A_WORKBENCH_HOME=str(runtime), APLUS_ACCELERATION_PROFILE=str(runtime/'no-profile.json'),
                      APLUS_STARTUP_TRACE_PATH=str(args.output.with_suffix('.jsonl')))
    sys.path.insert(0,str(args.code_root.resolve()))
    from workbench.startup_metrics import record, span, events, io_counters
    record('probe.entry', io=io_counters())
    durations = []
    app_holder = []
    patched = set()
    import importlib
    original_import = builtins.__import__
    def register(self, func, *a, **kw):
        def measured(*args):
            start = time.perf_counter()
            try:
                return func(*args)
            finally:
                elapsed = time.perf_counter()-start
                durations.append(elapsed)
                if elapsed >= .1:
                    record('tk.callback', duration=elapsed, callback=getattr(func, '__qualname__', type(func).__name__))
        return original_register(self, measured, *a, **kw)
    def wrap(cls, name, stage):
        original=getattr(cls,name)
        def measured(self, *a, _fn=original, _stage=stage, **kw):
            with span(_stage):
                return _fn(self, *a, **kw)
        setattr(cls, name, measured)
    def setup_window(app):
        app_holder.append(app)
        mapped, beats, last = [], [], [time.perf_counter()]
        app.bind('<Map>',lambda e:(mapped.append(True),record('window.visible'))
                 if e.widget is app and not mapped else None,add='+')
        def heartbeat():
            now=time.perf_counter();beats.append(now-last[0]);last[0]=now
            if len(beats)==1:record('window.responsive')
            app.after(20,heartbeat)
        def finish():
            record('probe.finished',watch_rows=len(app.watch.rows()),
                   candidate_rows=len(getattr(app.candidates,'_all_rows',[])),
                   layout=app.chart.layout_count,io=io_counters())
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(dict(events=events(),
                max_callback_ms=round(max(durations,default=0)*1000,3),
                max_heartbeat_gap_ms=round(max(beats,default=0)*1000,3)),ensure_ascii=False,indent=2),encoding='utf-8')
            app._exit_from_tray()
        app.after(0,heartbeat);app.after(int(args.seconds*1000),finish)
    def hooked_import(name,*a,**kw):
        nonlocal original_register
        value=original_import(name,*a,**kw)
        # Observe each actual import once; no eager heavy imports before the
        # product's real entry point, and no per-function sys.setprofile overhead.
        tk=sys.modules.get('tkinter')
        if tk and hasattr(tk,'Misc') and 'tk' not in patched:
            patched.add('tk');original_register=tk.Misc._register;tk.Misc._register=register
        targets=(('gui.watch_panel','WatchPanel','reload','watch.request'),
                 ('gui.match_list','MatchList','load_from_store','candidates.request'),
                 ('gui.workspace_controls','WorkspaceControls','restore_saved_layout','workspace.restore'),
                 ('gui.toolbar','ToolBar','_load_date_options','toolbar.dates'))
        for module,cls,method,stage in targets:
            mod=sys.modules.get(module)
            if module not in patched and mod and hasattr(mod,cls):
                patched.add(module);wrap(getattr(mod,cls),method,stage)
        mod=sys.modules.get('workbench.store')
        if 'store' not in patched and mod and hasattr(mod,'Store'):
            patched.add('store');original=mod.Store.__init__
            def init(store,*a,_fn=original,**kw):
                with span('backend.store'):_fn(store,*a,**kw)
            mod.Store.__init__=init
        mod=sys.modules.get('gui.chart_panel')
        if 'chart' not in patched and mod and hasattr(mod,'ChartPanel'):
            patched.add('chart');original=mod.ChartPanel._interactive_ready
            def ready(panel,*a,_fn=original,**kw):
                record('chart.interactive',slot=id(panel));return _fn(panel,*a,**kw)
            mod.ChartPanel._interactive_ready=ready
        mod=sys.modules.get('gui.main_window')
        if 'window' not in patched and mod and hasattr(mod,'AplusMainWindow'):
            patched.add('window');original=mod.AplusMainWindow.__init__
            def init_window(app,*a,_fn=original,**kw):
                kw['enable_tray']=False
                with span('window.construct'):_fn(app,*a,**kw)
                setup_window(app)
            mod.AplusMainWindow.__init__=init_window
        for module,cls in [('workbench.holding_quotes','QuotePoller'),('workbench.live_minutes','MinutePoller')]:
            mod=sys.modules.get(module)
            if module not in patched and mod and hasattr(mod,cls):
                patched.add(module);getattr(mod,cls).start=lambda *a,**kw:False
        return value
    original_register=None
    builtins.__import__=hooked_import
    import launch_dashboard
    launch_dashboard.acquire_single_instance=lambda **kw:object()
    launch_dashboard.release_single_instance=lambda *_:None
    launch_dashboard.main()


if __name__ == '__main__':
    main()
