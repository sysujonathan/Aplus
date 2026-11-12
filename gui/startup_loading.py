"""Small native startup hint. Workers publish events; only Tk paints widgets."""
import gc
import logging
import math
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from workbench import startup_metrics as metrics

PHASES = {
    'backend.store': '正在读取本地交易数据…',
    'backend.service': '正在验证策略与运行配置…',
    'window.import': '正在准备交易工作台…',
    'installed.integrity': '正在验证策略与运行配置…',
    'installed.binding': '正在读取本地交易数据…',
}
# Fixed illustrative OHLC, never presented as prices or percent progress.
CANDLES = tuple((10+i*.8+math.sin(i)*1.1,
                 13.3+i*.8+math.sin(i)*1.1,
                 7.5+i*.8+math.sin(i)*1.1,
                 10+i*.8+math.sin(i)*1.1+(1.8 if i%4!=2 else -1.4)) for i in range(24))


class LoadingPanel(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        if master.winfo_viewable():self.transient(master)
        self.configure(background='#0c141b')
        self.attributes('-alpha', .97)
        self.attributes('-topmost',True)
        self.scale = self.winfo_fpixels('1i')/96
        s=self.scale
        w,h=round(300*s),round(155*s)
        if master.winfo_viewable():
            x=master.winfo_rootx()+(master.winfo_width()-w)//2
            y=master.winfo_rooty()+(master.winfo_height()-h)//2
        else:
            x=(self.winfo_screenwidth()-w)//2;y=(self.winfo_screenheight()-h)//2
        self.geometry(f'{w}x{h}+{max(0,x)}+{max(0,y)}')
        self.canvas=tk.Canvas(self,width=w,height=h,background='#0c141b',highlightthickness=0)
        # ttkbootstrap can restyle classic-widget constructor options after
        # the main workbench is imported. Set this native canvas explicitly.
        self.canvas.configure(background='#0c141b')
        self.canvas.pack()
        c=self.canvas
        c.create_rectangle(1,1,w-2,h-2,outline='#34424e',width=1)
        # Native DWM small round corners; a cosmetic hint, never a startup gate.
        if __import__('os').name=='nt':
            import ctypes
            try:
                hwnd=ctypes.windll.user32.GetParent(self.winfo_id())
                corner=ctypes.c_int(3)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd,33,ctypes.byref(corner),4)
            except (OSError,AttributeError):pass
        from workbench.branding import application_icon, apply_window_icon
        apply_window_icon(self)
        from PIL import Image, ImageTk
        try:
            with Image.open(application_icon()) as image:
                self.logo=ImageTk.PhotoImage(image.convert('RGBA').resize((round(24*s),round(24*s))),master=self)
            c.create_image(18*s,12*s,image=self.logo,anchor='nw')
        except OSError:
            logging.getLogger(__name__).warning('启动提示图标无法读取',exc_info=True)
        self.title_font=tkfont.Font(self,family='Microsoft YaHei UI',size=-round(14*s),weight='bold')
        self.status_font=tkfont.Font(self,family='Microsoft YaHei UI',size=-round(11*s))
        c.create_text(48*s,24*s,text='Aplus',anchor='w',fill='#eef2f7',font=self.title_font)
        c.create_text(20*s,112*s,text='正在加载交易工作台',anchor='w',fill='#eef2f7',font=self.title_font)
        self.status_item=c.create_text(20*s,136*s,text='',anchor='w',fill='#a6b0bc',font=self.status_font)
        self.phase=''
        self.bars=[]
        for i,(opened,high,low,closed) in enumerate(CANDLES):
            x=(21+i*11.1)*s
            y=lambda price:(91-(price-8)*1.7)*s
            wick=c.create_line(x,y(high),x,y(low),fill='#36414b',width=max(1,round(s)))
            body=c.create_rectangle(x-2*s,min(y(opened),y(closed)),x+2*s,
                                    max(y(opened),y(closed)),fill='#36414b',outline='')
            self.bars.append((wick,body,'#edf3fa' if closed>opened else '#168cfa'))
        self._start=time.monotonic();self._timer=None;self.cycles=0
        self.status('正在准备交易工作台…')
        self.bind('<Destroy>',self._destroyed,add='+')
        self.deiconify();self.lift();self._animate()

    def status(self, text):
        self.phase=str(text)
        text=' '.join(str(text).split())
        while self.status_font.measure(text)>260*self.scale:
            text=text[:-2]+'…' if not text.endswith('…') else text[:-2]+'…'
        self.canvas.itemconfigure(self.status_item,text=text)

    def _animate(self):
        elapsed=(time.monotonic()-self._start)/2.6
        self.cycles=int(elapsed)
        phase=elapsed%1
        edge=min(1,phase/.88)*len(self.bars)
        fade=min(1,(1-phase)/.12)
        for index,(wick,body,color) in enumerate(self.bars):
            # Soft reveal across one candle, no visible scanning beam.
            fraction=max(0,min(1,edge-index))*fade
            rgb=lambda value:tuple(int(value[j:j+2],16) for j in (1,3,5))
            value='#'+''.join(f'{round(a+(b-a)*fraction):02x}' for a,b in zip(rgb('#36414b'),rgb(color)))
            self.canvas.itemconfigure(wick,fill=value)
            self.canvas.itemconfigure(body,fill=value)
        self._timer=self.after(33,self._animate)

    def _destroyed(self,event):
        if event.widget is self and self._timer:
            self.after_cancel(self._timer);self._timer=None


def initialize(factory, *, handoff=True):
    """Keep a tiny Tk loop alive while backend/imports run on one worker.

    Returns the successful value, or None on explicit close. A failed factory
    remains visibly failed until Retry or Close; it never opens an empty shell.
    """
    if __import__('os').name=='nt':
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    root=tk.Tk();root.withdraw()
    panel=LoadingPanel(root)
    theme_names={str(root),str(panel),str(panel.canvas)}
    messages=queue.Queue(maxsize=32)
    def publish(event):
        try:messages.put_nowait(('phase',event))
        except queue.Full:pass  # Only diagnostics; final result uses put below.
    unsubscribe=metrics.subscribe(publish)
    result=[];working=[False];cancelled=[False];actions=[]
    if __import__('os').environ.get('APLUS_RESPONSIVENESS_WATCHDOG','1')!='0':
        from .responsiveness import ResponsivenessWatchdog
        metrics.early_watchdog=ResponsivenessWatchdog();metrics.early_watchdog.start()
    def close():
        if working[0]:
            cancelled[0]=True;panel.status('正在结束初始化，请稍候…')
        else:root.destroy()
    def start():
        for button in actions:button.destroy()
        actions.clear();working[0]=True;panel.status('正在准备交易工作台…')
        def work():
            try:messages.put(('ready',factory()))
            except Exception as exc:
                logging.getLogger(__name__).exception('工作台初始化失败')
                messages.put(('error',exc))
        threading.Thread(target=work,daemon=True,name='aplus-startup').start()
    def poll():
        if metrics.early_watchdog:metrics.early_watchdog.beat()
        for _ in range(32):
            try:kind,value=messages.get_nowait()
            except queue.Empty:break
            if kind=='phase' and not cancelled[0]:
                if value.get('state')=='started' and value['stage'] in PHASES:
                    panel.status(PHASES[value['stage']])
            elif kind in ('ready','error'):
                working[0]=False
                if cancelled[0]:root.destroy();return
                if kind=='ready':result.append(value);root.destroy();return
                failed_phase=panel.phase
                panel.status(failed_phase.rstrip('…')+'失败：'+str(value))
                # Error text stays single-line; the full cause is available via
                # a tooltip-free native dialog only when explicitly requested.
                from tkinter import messagebox
                for text,action,x in [('详情',lambda exc=value:messagebox.showerror(
                        '工作台初始化失败',str(exc),parent=panel),165),('重试',start,210),('关闭',close,255)]:
                    button=tk.Button(panel,text=text,command=action,bg='#14202b',fg='#cbd5df',
                        relief='flat',borderwidth=0,font=('Microsoft YaHei UI',8))
                    button.place(x=x*panel.scale,y=15*panel.scale,width=38*panel.scale,height=22*panel.scale)
                    theme_names.add(str(button))
                    actions.append(button)
        root.after(30,poll)
    root.protocol('WM_DELETE_WINDOW',close)
    panel.bind('<Escape>',lambda _:close())
    root.after(0,start);root.after(30,poll)
    try:root.mainloop()
    finally:
        # destroy() closes the native window, but its callbacks, fonts and
        # PhotoImage still form Python cycles holding the Tcl interpreter.
        # A later automatic GC on a data worker would then delete that Tcl
        # interpreter on the wrong thread (Tcl_AsyncDelete / 0x80000003).
        # Suspend automatic GC only for this short teardown, drop our closure
        # references and collect on the owning thread before allowing workers
        # to trigger automatic collection again. Preserve the caller's GC mode.
        collect_automatically = gc.isenabled()
        gc.disable()
        try:
            unsubscribe()
            if (not result or not handoff) and metrics.early_watchdog:
                metrics.early_watchdog.stop();metrics.early_watchdog=None
            try:
                if root.winfo_exists():root.destroy()
            except tk.TclError:
                pass  # The normal ready/close path already destroyed the root.
            # ttkbootstrap may already have patched classic Tk constructors
            # (second installed loader, or a retry). Its singleton must not
            # keep this destroyed interpreter alive for the next workbench.
            bootstrap = sys.modules.get('ttkbootstrap')
            if bootstrap is not None:
                from ttkbootstrap.publisher import Publisher
                for name in theme_names:
                    Publisher.unsubscribe(name)
                style = bootstrap.Style.instance
                if style is not None and style.master is root:
                    bootstrap.Style.instance = None
                style = None
            actions.clear()
            panel = root = None
            gc.collect()
        finally:
            if collect_automatically:gc.enable()
    return result[0] if result else None
