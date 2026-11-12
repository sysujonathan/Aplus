"""Real Tk/WebView startup probe. Requires an explicitly isolated runtime copy."""
import argparse
import builtins
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def peak_memory():
    if os.name!='nt':return None
    import ctypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[
            (name,ctypes.c_size_t) for name in ('peak_working','working','peak_paged','paged',
                                             'peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
    value=Counters();value.cb=ctypes.sizeof(value)
    if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1),ctypes.byref(value),value.cb):
        return dict(peak_working_bytes=value.peak_working,peak_pagefile_bytes=value.peak_pagefile)
    return None


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
    parser.add_argument('--prepared',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--visual-overlay',action='store_true',
                        help='Isolated visual fixture: hold the real native panel for two loops')
    parser.add_argument('--panel-scale',type=float,choices=(1,1.25,1.5),default=1)
    parser.add_argument('--watch-count',type=int,choices=(15,300),default=15)
    parser.add_argument('--candidate-date',help='Existing historical result date (isolated fixture)')
    parser.add_argument('--backup-maintenance',action='store_true',
                        help='Exercise existing rotation against a protected backup inside the clone')
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if args.source_backup:
        if not args.assets_from:
            parser.error('--assets-from is required when preparing a copy')
        print(json.dumps({'artifacts_copied':prepare_copy(args.source_backup,args.assets_from,runtime)}))
        return
    if not (runtime/'startup-probe-isolated.json').is_file():
        parser.error('Only a marked isolated copy is accepted; never run on the official runtime')
    # Fixture setup is outside the observed child process; all identities refer
    # to existing real daily observations, never invented trading signals.
    import sqlite3
    if not args.prepared:
        prepare_layout(runtime,args.layout)
        prepare_watches(runtime,args.watch_count)
        if args.backup_maintenance:prepare_backup(runtime)
        import subprocess
        command=[sys.executable,str(Path(__file__).resolve()),'--runtime',str(runtime),
            '--code-root',str(args.code_root.resolve()),'--output',str(args.output.resolve()),
            '--layout',str(args.layout),'--seconds',str(args.seconds),
            '--confirm-isolated-copy','--prepared','--panel-scale',str(args.panel_scale)]
        if args.candidate_date:command+=['--candidate-date',args.candidate_date]
        if args.backup_maintenance:command.append('--backup-maintenance')
        if args.visual_overlay:command.append('--visual-overlay')
        subprocess.run(command,check=True)
        return
    run_probe(args,runtime)


def prepare_layout(runtime,layout_count):
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
            layout['rows'],layout['columns']={1:(1,1),4:(2,2),9:(3,3)}[layout_count]
            layout['panes']=[dict(item=item,timeframe='daily',range=None) for item in items[:layout_count]]
            layout['active']=max(0,len(layout['panes'])-1)
            document['data']['active']=layout['id'];document['revision']+=1
            db.execute("UPDATE meta SET value=? WHERE key='ui:chart_layouts'",(json.dumps(document),))


def prepare_watches(runtime,count):
    # Expand only the marked clone, using real symbols and Store's existing
    # manual-watch path. No observations/plans/signals are fabricated.
    from workbench.store import Store
    store=Store(runtime)
    tag='startup-probe-extra-watch'
    original=runtime/'startup-probe-original-watches.json'
    if not original.exists():
        original.write_text(json.dumps([r['code'] for r in store.rows(
            'SELECT code FROM watchlist WHERE active=1 ORDER BY code')]),encoding='utf-8')
    original_codes=set(json.loads(original.read_text(encoding='utf-8')))
    known=set(original_codes)
    with store.connect() as db:
        db.execute('UPDATE watchlist SET active=0')
        db.executemany('UPDATE watchlist SET active=1 WHERE code=?',[(code,) for code in known])
    # Re-adding an archived user watch preserves its old anchor/notes. Those
    # rows must not accidentally become different fixtures on the next run.
    archived={r['code'] for r in store.rows('SELECT code FROM watchlist WHERE notes<>?',(tag,))}
    if len(known)>=count:return
    for row in store.rows("SELECT DISTINCT code FROM datasets WHERE source='tickflow' AND timeframe='daily' ORDER BY code"):
        if row['code'] in known or row['code'] in archived:continue
        store.add_manual_watch(row['code'],notes=tag);known.add(row['code'])
        if len(known)>=count:break
    if len(known)<count:raise ValueError('Not enough real symbols in the isolated fixture')
    # Store timestamps have second precision. Re-adding hundreds across a
    # second boundary otherwise changes tie groups despite identical symbols.
    # Only fixture-owned rows get deterministic ordering; user rows stay exact.
    with store.connect() as db:
        db.executemany('UPDATE watchlist SET updated=? WHERE code=? AND notes=?',[
            (f'2000-01-01T00:{i//60:02d}:{i%60:02d}+00:00',code,tag)
            for i,code in enumerate(sorted(known-original_codes))])


def prepare_backup(runtime):
    import shutil
    from workbench.installed_data import file_sha256,atomic_json
    home=runtime/'startup-probe-backups';folder=home/'backups/20261112T000000Z-00000001'
    if (home/'data.json').is_file():return
    folder.mkdir(parents=True)
    database=folder/'workbench.sqlite3'
    shutil.copy2(runtime/'workbench.sqlite3',database)
    sha=file_sha256(database)
    atomic_json(folder/'receipt.json',dict(info=dict(path=str(runtime)),source_sha256=sha,backup_sha256=sha))
    atomic_json(home/'data.json',dict(runtime=str(runtime),backup=str(database)))


def run_probe(args,runtime):
    os.environ.update(A_WORKBENCH_HOME=str(runtime), APLUS_ACCELERATION_PROFILE=str(runtime/'no-profile.json'),
                      APLUS_STARTUP_TRACE_PATH=str(args.output.with_suffix('.jsonl')),
                      APLUS_SNAPSHOT_DIR='',APLUS_SNAPSHOT_URL='')
    sys.path.insert(0,str(args.code_root.resolve()))
    from workbench.startup_metrics import record, span, events, io_counters
    record('probe.entry', io=io_counters())
    durations = []
    app_holder = []
    patched = set()
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
                    target=func
                    if getattr(func,'__name__','')=='callit':
                        closure=dict(zip(func.__code__.co_freevars,func.__closure__ or ()))
                        if 'func' in closure:target=closure['func'].cell_contents
                    record('tk.callback', duration=elapsed,
                           callback=getattr(target, '__qualname__', type(target).__name__),
                           line=getattr(getattr(target,'__code__',None),'co_firstlineno',None))
        return original_register(self, measured, *a, **kw)
    def wrap(cls, name, stage):
        original=getattr(cls,name)
        def measured(self, *a, _fn=original, _stage=stage, **kw):
            with span(_stage):
                return _fn(self, *a, **kw)
        setattr(cls, name, measured)
    def setup_window(app):
        app.title('APLUS 操盘台 · 隔离启动验收')
        app_holder.append(app)
        mapped, beats, last = [], [], [time.perf_counter()]
        app.bind('<Map>',lambda e:(mapped.append(True),record('window.visible'))
                 if e.widget is app and not mapped else None,add='+')
        def heartbeat():
            now=time.perf_counter();beats.append(now-last[0]);last[0]=now
            if len(beats)==1:record('window.responsive')
            if beats[-1]>.1:record('tk.heartbeat_gap',duration=beats[-1])
            app.after(20,heartbeat)
        def finish():
            import hashlib
            from dataclasses import asdict
            def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
                default=str).encode('utf-8')).hexdigest()
            record('probe.finished',watch_rows=len(app.watch.rows()),
                   candidate_rows=len(getattr(app.candidates,'_all_rows',[])),
                   layout=app.chart.layout_count,io=io_counters(),
                   parent_memory=peak_memory(),
                   watch_digest=digest(app.watch.rows()),
                   candidate_digest=digest(getattr(app.candidates,'_all_rows',[])),
                   layout_digest=digest([asdict(item) if item else None for item in app.chart._display_items]))
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(dict(events=events(),
                max_callback_ms=round(max(durations,default=0)*1000,3),
                max_heartbeat_gap_ms=round(max(beats,default=0)*1000,3)),ensure_ascii=False,indent=2),encoding='utf-8')
            app._exit_from_tray()
        app.after(0,heartbeat);app.after(int(args.seconds*1000),finish)
        if args.visual_overlay:
            def show_panel():
                # This deliberate visual fixture is NOT the production
                # lifecycle: hold two loops over the genuine workbench so a
                # reviewer can capture motion/DPI without racing a fast boot.
                from gui.startup_loading import LoadingPanel
                previous=app.tk.call('tk','scaling')
                app.tk.call('tk','scaling',args.panel_scale*96/72)
                panel=LoadingPanel(app)
                app.tk.call('tk','scaling',previous)
                panel.status('正在验证启动提示…')
                record('probe.visual_panel',scale=panel.scale,width=300*panel.scale)
                panel.after(200,lambda:record('probe.visual_mapped',mapped=panel.winfo_ismapped(),
                    x=panel.winfo_rootx(),y=panel.winfo_rooty(),w=panel.winfo_width(),h=panel.winfo_height()))
                def done():
                    record('probe.visual_complete',cycles=panel.cycles)
                    panel.destroy()
                panel.after(max(14000,round((args.seconds-5)*1000)),done)
            app.after(1500,show_panel)
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
        mod=sys.modules.get('workbench.strategies')
        if 'integrity' not in patched and mod and hasattr(mod,'verify_frozen'):
            patched.add('integrity');original_verify=mod.verify_frozen
            def verify(*a,_fn=original_verify,**kw):
                with span('backend.integrity'):return _fn(*a,**kw)
            mod.verify_frozen=verify
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
            if args.candidate_date:
                original_load=mod.AplusMainWindow._load_strategies
                def load_for_date(app,*a,_fn=original_load,**kw):
                    if not getattr(app.toolbar,'_dates_pending',False):
                        if not app.toolbar._select_date(args.candidate_date):
                            raise ValueError('Requested historical date is unavailable in this source: '+args.candidate_date)
                    return _fn(app,*a,**kw)
                mod.AplusMainWindow._load_strategies=load_for_date
        for module,cls in [('workbench.holding_quotes','QuotePoller'),('workbench.live_minutes','MinutePoller')]:
            mod=sys.modules.get(module)
            if module not in patched and mod and hasattr(mod,cls):
                patched.add(module);getattr(mod,cls).start=lambda *a,**kw:False
        mod=sys.modules.get('gui.version_update')
        if 'version' not in patched and mod and hasattr(mod,'VersionController'):
            patched.add('version');mod.VersionController.startup_check=lambda *_:None
        return value
    original_register=None
    builtins.__import__=hooked_import
    import launch_dashboard
    launch_dashboard.acquire_single_instance=lambda **kw:object()
    launch_dashboard.release_single_instance=lambda *_:None
    if args.backup_maintenance:
        def maintenance():
            from workbench.installed_backup_rotation import rotate_backups
            with span('maintenance.backup_rotation'):
                value=rotate_backups(home=runtime/'startup-probe-backups',runtime=runtime)
            record('maintenance.result',blocked=value['blocked'],removed=len(value['removed']))
        if hasattr(launch_dashboard,'idle_maintenance'):
            launch_dashboard.idle_maintenance=maintenance
        else:
            import threading
            threading.Thread(target=maintenance,daemon=True,name='aplus-backup-rotation').start()
    launch_dashboard.main()


if __name__ == '__main__':
    main()
