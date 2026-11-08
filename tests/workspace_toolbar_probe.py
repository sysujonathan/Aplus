"""Actual Tk + isolated child WebViews; test-only inspection, no production hooks."""
import json
import math
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


INSPECT="""(() => ({tool:activeDrawingTool||'cross',count:userObjects.length,
 types:userObjects.map(o=>o.type),identity:chartBoot.identity,
 colors:userObjects.map(o=>drawingQuickbar.value(o,drawingQuickbar.controls(o).find(k=>k.endsWith('Color')))),
 top:getComputedStyle(document.querySelector('.topbar')).display,
 left:getComputedStyle(document.querySelector('.leftbar')).display,
 favorites:getComputedStyle(document.querySelector('#favoriteTools')).display,
 selected:getComputedStyle(document.querySelector('#selectedObject')).display,
 reference:document.querySelector('#referenceQuote').textContent,
 bidAsk:getComputedStyle(document.querySelector('.quote-buttons')).display,
 iconPicker:document.querySelector('#modalBackdrop').classList.contains('show') &&
   document.querySelector('#chartControlModal').classList.contains('show') &&
   document.querySelector('#chartControlTitle').textContent==='选择图标',
 settingsOpen:Boolean(document.querySelector('#workspaceChartSettings[open]')),
 horizontalGrid:indicatorSettings.horizontal_grid,
 state:window.aplusWorkspaceState()}))()"""


def capture_own_client(root, path):
    """Screen capture is only safe when the entire sample is our test window."""
    import ctypes
    from ctypes import wintypes
    from PIL import ImageGrab
    user=ctypes.WinDLL('user32')
    user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]
    user.GetAncestor.restype=wintypes.HWND
    user.WindowFromPoint.argtypes=[wintypes.POINT]
    user.WindowFromPoint.restype=wintypes.HWND
    user.GetForegroundWindow.restype=wintypes.HWND
    hwnd=user.GetAncestor(root.winfo_id(),2)
    root.attributes('-topmost',True);root.lift();root.focus_force();root.update()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    width,height=root.winfo_width(),root.winfo_height()
    samples=[(x+a,y+b) for a in (4,width//2,width-4) for b in (4,height//2,height-4)]
    if user.GetForegroundWindow()!=hwnd or any(
        user.GetAncestor(user.WindowFromPoint(wintypes.POINT(*point)),2)!=hwnd for point in samples):
        return False
    image=ImageGrab.grab(bbox=(x,y,x+width,y+height),all_screens=True)
    if user.GetForegroundWindow()!=hwnd:return False
    image.save(path)
    return True


def child(session):
    from gui import chart_embed, chart_shell, chart_snapshot
    chart_snapshot.copy_snapshot_link = lambda uri, _owner: {'ok':uri.startswith('file:')}
    chart_snapshot.open_snapshot_in_browser = lambda uri: uri.startswith('file:')
    original=chart_embed.run_embedded_transport
    session=Path(session)

    def transport(window,bridge,*args,**kwargs):
        stop=threading.Event()
        def inspect():
            last=None
            while not stop.wait(.05):
                request=chart_embed.read_notification(session.with_suffix('.probe-input.json'))
                if not request or request.get('id')==last or request.get('token')!=bridge.active_token:continue
                last=request['id']
                try:
                    operation=request['op']
                    if operation=='place':
                        kind=request['tool']
                        assert kind in ('fib','long','short','trend','ray','up','down','rectangle','channel','arrow','path')
                        expression="""(() => {
                          const kind=KIND;
                          if(activeDrawingTool!==kind)throw Error('wrong active native tool '+activeDrawingTool);
                          const at=Math.max(0,bars.length-40),price=bars[at].close;
                          placeDrawing({index:at,value:price,x:100,y:150});
                          if(kind==='path'){placeDrawing({index:at+5,value:price+.2,x:160,y:140});finishPath();}
                          else if(!['long','short','ray','up','down'].includes(kind)){
                            placeDrawing({index:at+12,value:price+.2,x:240,y:130});
                            if(kind==='channel')placeDrawing({index:at+6,value:price+.35,x:180,y:110});
                          }
                          const obj=userObjects.at(-1);
                          if(obj?.type!==kind)throw Error('tool did not create expected object');
                          selectedId=obj.id;renderWorkspace();
                          const control=drawingQuickbar.controls(obj).find(k=>k.endsWith('Color'));
                          if(!commitQuickDrawingStyle(obj,control,'#cc33aa'))throw Error('quick property edit refused');
                          return true;
                        })()""".replace('KIND',json.dumps(kind))
                        assert window.evaluate_js(expression)
                        window.evaluate_js('window.toolbarSaved=false;savePrototypeState(true).then(()=>window.toolbarSaved=!bridgeSaveFailed);')
                        deadline=time.monotonic()+12
                        while not window.evaluate_js('window.toolbarSaved'):
                            assert time.monotonic()<deadline,'drawing save timed out'
                            time.sleep(.05)
                    elif operation=='select':
                        window.evaluate_js('selectedId=userObjects.at(-1)?.id;renderWorkspace();')
                    elif operation=='place-icon':
                        assert window.evaluate_js("""(() => {
                          const star=[...document.querySelectorAll('#chartControlBody button')].find(button=>button.textContent==='★');
                          if (!star || !star.getClientRects().length) return false;
                          star.click();
                          if (activeDrawingTool!=='text') return false;
                          const index=bars.length-40;
                          placeDrawing({index,value:bars[index].close,x:100,y:150});
                          const object=userObjects.at(-1);
                          return object?.type==='text' && object.text==='★';
                        })()""")
                    elif operation=='key':
                        key = json.dumps(request['key'])
                        window.evaluate_js(
                            f"canvas.dispatchEvent(new KeyboardEvent('keydown',{{key:{key},bubbles:true,cancelable:true}}));"
                        )
                    elif operation=='toggle-grid':
                        assert window.evaluate_js("""(() => {
                          const dialog=document.querySelector('#workspaceChartSettings[open]');
                          dialog.querySelector('[data-grid="horizontal_grid"]').click();
                          dialog.close();
                          return indicatorSettings.horizontal_grid===false;
                        })()""")
                    elif operation!='inspect':raise AssertionError('unknown probe operation')
                    data=window.evaluate_js(INSPECT)
                    chart_embed.atomic_json(session.with_suffix('.probe-reply.json'),dict(id=last,data=data))
                except BaseException:
                    chart_embed.atomic_json(session.with_suffix('.probe-reply.json'),dict(id=last,error=traceback.format_exc()))
        worker=threading.Thread(target=inspect,daemon=True);worker.start()
        try:return original(window,bridge,*args,**kwargs)
        finally:stop.set();worker.join(2)
    chart_embed.run_embedded_transport=transport
    chart_shell.run_session(str(session))


def run(folder):
    import pandas as pd
    import ttkbootstrap as ttk
    from gui.chart_panel import ChartGrid
    from gui.chart_items import ChartItem
    from gui.chart_embed import atomic_json,read_notification
    from gui.workspace_toolbar import FAVORITES
    from workbench.store import Store
    from workbench import process_entry
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    store=Store(folder/'runtime')
    process_entry.chart_command=lambda session:[sys.executable,'-u',str(Path(__file__).resolve()),'--child',str(session)]
    root=ttk.Window(themename='darkly',title='Aplus synthetic shared toolbar acceptance')
    root.tk.call('tk','scaling',96/72)
    root.geometry('1800x1100+30+30')
    grid=ChartGrid(root,store,interactive=True);grid.pack(fill='both',expand=True)
    all_hosts=[];records=[];forced=[]
    def wait(predicate,label,seconds=45):
        deadline=time.monotonic()+seconds
        while not predicate():
            root.update()
            if time.monotonic()>deadline:raise AssertionError(label)
            time.sleep(.03)
        root.update()
    def ask(host,op='inspect',**values):
        identity=str(time.monotonic_ns())
        atomic_json(host.session.with_suffix('.probe-input.json'),dict(id=identity,token=host.token,op=op,**values))
        def reply():
            value=read_notification(host.session.with_suffix('.probe-reply.json'))
            return value if value and value.get('id')==identity else None
        wait(reply,'child inspection '+op,20)
        result=reply()
        assert not result.get('error'),result
        return result['data']
    try:
        dates=pd.bdate_range('2026-01-05','2026-11-04').strftime('%Y-%m-%d')
        for count in (1,4,6,9):
            root.update()
            grid.controls.layout_button.invoke()
            grid.controls._layout_popup.choices[count].invoke()
            contexts=[];items=[]
            for index,slot in enumerate(grid._slots):
                code=f'sh.{600000+count*10+index:06d}'
                close=[17.4+index*.6-(len(dates)-i-1)*.008+math.sin(i*.11+index)*.36+
                       math.sin(i*.61+index)*.10 for i in range(len(dates))]
                opening=[v+math.sin(i*.73)*.08 for i,v in enumerate(close)]
                frame=pd.DataFrame(dict(date=dates,open=opening,close=close,
                    high=[max(o,c)+.06 for o,c in zip(opening,close)],
                    low=[min(o,c)-.06 for o,c in zip(opening,close)],volume=1000))
                context=dict(frame=frame,code=code,name=f'合成图格 {index+1}',timeframe='daily',
                    context_kind='watch',record=dict(source='tickflow',adjustment='前复权'))
                contexts.append(context);items.append(ChartItem(code,f'合成图格 {index+1}',None,'tickflow','watch'))
                slot._code=code;slot._workspace_context=context
                slot._title_var.set(f'{code} 合成图格 {index+1} · 历史日K')
                slot._show_interactive()
                if slot._embedded not in all_hosts:all_hosts.append(slot._embedded)
            grid._display_items=items
            grid._active_slot=0;grid._refresh_active_styles()
            wait(lambda:all(h.ready() and (h._last_workspace_state or {}).get('token')==h.token for h in grid.embedded_hosts()),'all real renderers ready',60)
            tools=grid.controls.drawing_tools;tools.refresh()
            assert tools.ready and tools.favorites.winfo_ismapped()
            assert tools.favorites.winfo_manager()=='place'
            assert tools.favorites.place_info()['in'] is grid.host
            assert all(bool(slot.header.winfo_ismapped())==(count==1) for slot in grid._slots)
            host=grid._slots[0]._embedded
            wait(lambda:(host._last_workspace_state or {}).get('tool')=='cross','first active state')
            grid.controls.period_buttons['weekly'].invoke()
            wait(lambda:(host._last_workspace_state or {}).get('timeframe')=='weekly','weekly direct button')
            grid.controls.period_buttons['daily'].invoke()
            wait(lambda:(host._last_workspace_state or {}).get('timeframe')=='daily','daily direct button')
            grid.controls.chart_types['line'].invoke()
            wait(lambda:(host._last_workspace_state or {}).get('chartType')=='line','line icon command')
            grid.controls.chart_types['candles'].invoke()
            wait(lambda:(host._last_workspace_state or {}).get('chartType')=='candles','candles icon command')
            if count==6:
                for mode in ('ruler','zoom','cross'):
                    if mode=='cross':
                        # The cursor group now remembers Arrow after drawing.
                        # Choose Cross explicitly from its real native menu.
                        tools.menu('cross');root.update();tools.popup._entries[0][2].invoke()
                    else:tools.buttons[mode][0].invoke()
                    wait(lambda:(host._last_workspace_state or {}).get('tool')==mode,'rail command '+mode)
                for offset,mode in enumerate(('cursor-dot','cursor-arrow','cursor-highlight','cursor-eraser'),1):
                    tools.menu('cross');root.update();tools.popup._entries[offset][2].invoke()
                    wait(lambda:(host._last_workspace_state or {}).get('tool')==mode,'cursor command '+mode)
                for name,state in [('lock','locked'),('magnet','magnet'),('eye','hidden')]:
                    old=bool(host._last_workspace_state[state])
                    for value in (not old,old):
                        tools.buttons[name][0].invoke()
                        wait(lambda:bool(host._last_workspace_state.get(state))==value,'rail toggle '+name)
            for number,(tool,_title) in enumerate(FAVORITES,1):
                tools.buttons[tool][-1].invoke()
                wait(lambda:(host._last_workspace_state or {}).get('tool')==tool,'tool ack '+tool)
                data=ask(host,'place',tool=tool)
                assert data['count']==number and data['types'][-1]==tool,data
                assert data['colors'][-1]=='#cc33aa',data
                assert data['top']==data['left']==data['favorites']=='none'
                assert data['bidAsk']=='none'
            grid.controls.command('action','undo')
            wait(lambda:ask(host)['colors'][-1]!='#cc33aa','quick edit undo')
            grid.controls.command('action','redo')
            wait(lambda:ask(host)['colors'][-1]=='#cc33aa','quick edit redo')
            identity=ask(host)['identity']
            wait(lambda:len(store.chart_document(identity)['drawings'])==11,'real Store has 11 tools')
            host.show(contexts[0])
            wait(lambda:host.ready() and (host._last_workspace_state or {}).get('token')==host.token,'saved context reopened')
            data=ask(host)
            assert data['count']==11 and all(c=='#cc33aa' for c in data['colors']),data
            assert ask(host,'select')['selected']!='none'
            if count>1:
                grid._activate_slot(count-1)
                tools.refresh()
                wait(lambda:tools.ready,'next pane toolbar ready')
                assert ask(grid._slots[-1]._embedded)['count']==0,'drawings crossed identities'
                wait(lambda:ask(host)['selected']=='none','old object toolbar hidden')
                grid._activate_slot(0);tools.refresh()
            if count==6:
                tools.buttons['smile'][0].invoke()
                wait(lambda:ask(host)['iconPicker'],'native icon opens the real WebView picker')
                ask(host,'place-icon')
                wait(lambda:any(d.get('kind')=='text' and d.get('text')=='★'
                                for d in store.chart_document(identity)['drawings']),'picked symbol saved in Store')
                ask(host,'key',key='F11')
                wait(lambda:grid.controls.fullscreen,'F11 forwarded from active WebView')
                ask(host,'key',key='Escape')
                wait(lambda:not grid.controls.fullscreen,'Escape forwarded from active WebView')
                grid.controls.settings_button.invoke()
                wait(lambda:ask(host)['settingsOpen'],'right toolbar opens the activity chart settings')
                ask(host,'toggle-grid')
                pane_key = grid.controls.current['id']+':0'
                wait(lambda:(store.chart_document(identity)['settings'].get('pane_states',{})
                             .get(pane_key,{}).get('indicators',{}).get('horizontal_grid') is False),
                     'active chart grid preference saved')
                assert ask(grid._slots[-1]._embedded)['horizontalGrid'] is True
                handle = tools.favorite_handle
                x,y = handle.winfo_rootx()+3,handle.winfo_rooty()+3
                before = tools.position
                handle.event_generate('<ButtonPress-1>',rootx=x,rooty=y)
                handle.event_generate('<B1-Motion>',rootx=x+70,rooty=y+55,state=256)
                handle.event_generate('<ButtonRelease-1>',rootx=x+70,rooty=y+55)
                assert tools.position!=before and tools.favorites.place_info()['in'] is grid.host
                menu=grid.nametowidget(grid.controls.snapshot_button.cget('menu'))
                for index in (2,3):
                    menu.invoke(index)
                    wait(lambda:len(list((folder/'runtime/chart_snapshots').glob('*.png')))==index-1,
                         'snapshot command writes local PNG')
            ask(host,'select')
            root.update()
            widths=[s.winfo_width() for s in grid._slots]
            assert max(widths)-min(widths)<=1
            tools.menu('fib');root.update()
            captured=False
            if '--screenshots' in sys.argv:
                from PIL import ImageGrab
                ImageGrab.grab(window=tools.popup.winfo_id()).save(folder/f'fib-menu-{count}.png')
            tools.close_menu()
            if '--screenshots' in sys.argv:
                captured=capture_own_client(root,folder/f'live-grid-{count}.png')
            records.append(dict(panes=count,real_child_renderers=len(grid.embedded_hosts()),all_tools=11,
                real_quickbar_edit=True,undo_redo=True,real_store_reopened=True,
                old_selected_bar_hidden=count>1,equal_columns=True,only_shared_controls=True,
                direct_period_buttons=True,graphical_layout_picker=True,active_chart_style=True,
                floating_favorites=True,captured_own_client=captured))
        (folder/'result.json').write_text(json.dumps(dict(ok=True,synthetic=True,rounds=records),indent=2),encoding='utf-8')
        if '--hold' in sys.argv:
            (folder/'ready.json').write_text(json.dumps(dict(ready=True,panes=9,synthetic=True)),encoding='utf-8')
            deadline=time.monotonic()+240
            while time.monotonic()<deadline and not (folder/'finish').exists():
                root.update();time.sleep(.05)
    finally:
        for host in all_hosts:
            if host and not host.closed():host.request_close()
        deadline=time.monotonic()+25
        while any(h and not h.closed() for h in all_hosts) and time.monotonic()<deadline:
            root.update();time.sleep(.05)
        for host in all_hosts:
            if host and not host.closed():host.process.terminate();forced.append(host.process.pid)
        root.destroy()
        assert not forced,f'test children needed forced cleanup: {forced}'


if __name__=='__main__':
    if sys.argv[1]=='--child':child(sys.argv[2])
    else:run(sys.argv[1])
