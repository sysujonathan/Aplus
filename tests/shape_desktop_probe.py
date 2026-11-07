"""Real production desktop visual probe against an explicit isolated data home."""
from pathlib import Path
import json
import os
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(home):
    home=Path(home).resolve()
    if not home.is_relative_to(ROOT/'.test-shape-real'):
        raise ValueError('必须使用隔离的形态验收目录')
    os.environ['A_WORKBENCH_HOME']=str(home/'runtime')
    from workbench.store import Store
    from workbench.service import Service
    from gui.main_window import AplusMainWindow
    from gui.shape_dialog import ShapeDialog
    from PIL import ImageGrab
    store=Store(home/'runtime');service=Service(store)
    window=AplusMainWindow(service=service,store=store,enable_tray=False)
    window.state('zoomed')
    controller=window.version_controller
    if controller.startup_check_id:
        window.after_cancel(controller.startup_check_id);controller.startup_check_id=None
    output=home/'visual';output.mkdir(exist_ok=True)
    evidence={}
    def pump(seconds=1):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            window.update();time.sleep(.03)
    def capture(widget,name):
        # Capture only this HWND, even when another app owns desktop focus.
        import ctypes
        from ctypes import wintypes
        from PIL import Image
        widget.update();pump(.5)
        user,gdi=ctypes.windll.user32,ctypes.windll.gdi32
        for fn in ('GetDC','GetWindowDC','GetAncestor'):
            getattr(user,fn).restype=ctypes.c_void_p
        for fn in ('CreateCompatibleDC','CreateCompatibleBitmap','SelectObject'):
            getattr(gdi,fn).restype=ctypes.c_void_p
        user.GetDC.argtypes=[wintypes.HWND]
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]
        gdi.CreateCompatibleDC.argtypes=[ctypes.c_void_p]
        gdi.CreateCompatibleBitmap.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int]
        gdi.SelectObject.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        user.PrintWindow.argtypes=[wintypes.HWND,ctypes.c_void_p,wintypes.UINT]
        hwnd=user.GetAncestor(widget.winfo_id(),2)
        rect=wintypes.RECT();user.GetClientRect(hwnd,ctypes.byref(rect))
        width,height=rect.right,rect.bottom
        dc=user.GetDC(hwnd);memory=gdi.CreateCompatibleDC(dc)
        bitmap=gdi.CreateCompatibleBitmap(dc,width,height);old=gdi.SelectObject(memory,bitmap)
        class Header(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('width',wintypes.LONG),('height',wintypes.LONG),
                ('planes',wintypes.WORD),('bits',wintypes.WORD),('compression',wintypes.DWORD),
                ('sizeImage',wintypes.DWORD),('x',wintypes.LONG),('y',wintypes.LONG),
                ('colors',wintypes.DWORD),('important',wintypes.DWORD)]
        header=Header(ctypes.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
        buffer=ctypes.create_string_buffer(width*height*4)
        gdi.GetDIBits.argtypes=[ctypes.c_void_p,ctypes.c_void_p,wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.c_void_p,wintypes.UINT]
        gdi.DeleteObject.argtypes=[ctypes.c_void_p];gdi.DeleteDC.argtypes=[ctypes.c_void_p]
        user.ReleaseDC.argtypes=[wintypes.HWND,ctypes.c_void_p]
        try:
            assert user.PrintWindow(hwnd,memory,3),'HWND截图未完成'
            assert gdi.GetDIBits(memory,bitmap,0,height,buffer,ctypes.byref(header),0)
            Image.frombytes('RGB',(width,height),buffer.raw,'raw','BGRX').save(output/name)
        finally:
            gdi.SelectObject(memory,old);gdi.DeleteObject(bitmap);gdi.DeleteDC(memory);user.ReleaseDC(hwnd,dc)
    def ready():
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            pump(.1)
            active=[s for s in window.chart._slots if s._code]
            if active and all(s._embedded and s._embedded.ready() for s in active):return
        raise AssertionError('真实内嵌图表未就绪')
    try:
        pump(2)
        assert not window.chart.controls._load_error,'已保存布局未能恢复'
        window.chart.set_layout(1)
        window._lists_override=True;window._apply_list_layout(1)
        window.candidates.set_source('形态捕手');ready()
        capture(window,'01-shared-list.png')
        before=(window.toolbar.selected_date(),window.candidates.origin,window.candidates.tree.selection(),
                window._sidebar_width,window.chart.current_item().observation_id)
        host=window.chart._slots[0]._embedded
        pid,token=host.process.pid,host.token
        width=window.chart.winfo_width()
        window._toggle_lists();pump(1)
        assert not window.list_pane.grid_info() and window.chart.winfo_width()>width
        assert (host.process.pid,host.token)==(pid,token)
        capture(window,'02-focus.png')
        window._toggle_lists();pump(1)
        after=(window.toolbar.selected_date(),window.candidates.origin,window.candidates.tree.selection(),
               window._sidebar_width,window.chart.current_item().observation_id)
        assert before==after
        evidence['focus_preserved']=True;evidence['same_chart_process_and_token']=True
        dialog=ShapeDialog(window,store,service,window.toolbar.selected_date(),on_results=window._shape_results,on_compare=window._compare_shape)
        dialog.geometry('1500x1200+80+40');pump(1)
        capture(dialog,'03-template-manager.png')
        last=next(r for r in store.shape_runs('tickflow') if any(t['kind']=='case' for t in store.shape_run(r['id'])['templates']))
        dialog._display_run(last['id']);pump(.4)
        capture(dialog,'04-case-results.png')
        row=dialog._selected();assert row
        dialog._close();window._compare_shape(row);ready()
        capture(window,'05-case-comparison.png')
        assert window.chart.layout_count==2
        left=window.chart._slots[0]._workspace_context
        right=window.chart._slots[1]._workspace_context
        assert str(left['frame'].date.iloc[-1])=='2026-07-07'
        assert str(right['frame'].date.iloc[-1])=='2026-11-05'
        evidence.update(real_charts=True,comparison_prefix=str(left['frame'].date.iloc[-1]),
                        candidate_cutoff=str(right['frame'].date.iloc[-1]),same_engine=True)
    finally:
        (output/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        window._exit_from_tray()
        service.pool.shutdown(wait=True)


if __name__=='__main__':run(sys.argv[1])
