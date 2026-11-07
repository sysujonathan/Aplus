"""Read-only Win32 HICON evidence for native Tk branding tests/probes."""
import ctypes as ct
from ctypes import wintypes as wt
import hashlib

from PIL import Image


class IconReader:
    def __init__(self):
        self.user=ct.WinDLL('user32',use_last_error=True)
        self.gdi=ct.WinDLL('gdi32',use_last_error=True)
        signatures=[
            (self.user,'GetAncestor',[wt.HWND,wt.UINT],wt.HWND),
            (self.user,'IsWindow',[wt.HWND],wt.BOOL),
            (self.user,'SendMessageW',[wt.HWND,wt.UINT,ct.c_size_t,ct.c_ssize_t],ct.c_ssize_t),
            (self.user,'GetClassLongPtrW',[wt.HWND,ct.c_int],ct.c_size_t),
            (self.user,'GetDC',[wt.HWND],wt.HDC),
            (self.user,'ReleaseDC',[wt.HWND,wt.HDC],ct.c_int),
            (self.user,'DrawIconEx',[wt.HDC,ct.c_int,ct.c_int,wt.HICON,ct.c_int,ct.c_int,wt.UINT,wt.HBRUSH,wt.UINT],wt.BOOL),
            (self.gdi,'CreateCompatibleDC',[wt.HDC],wt.HDC),
            (self.gdi,'SelectObject',[wt.HDC,wt.HANDLE],wt.HANDLE),
            (self.gdi,'DeleteObject',[wt.HANDLE],wt.BOOL),
            (self.gdi,'DeleteDC',[wt.HDC],wt.BOOL),
            (self.gdi,'CreateDIBSection',[wt.HDC,ct.c_void_p,wt.UINT,ct.POINTER(ct.c_void_p),wt.HANDLE,wt.DWORD],wt.HBITMAP),
        ]
        for library,name,args,result in signatures:
            function=getattr(library,name);function.argtypes=args;function.restype=result

    def hwnd(self,window):
        window.update_idletasks()
        return self.user.GetAncestor(window.winfo_id(),2)

    def image(self,icon,size=32):
        if not icon:raise AssertionError('Window did not expose a native icon')
        # A top-down 32-bit DIB, fixed white backing, no global state changes.
        header=ct.create_string_buffer(40)
        import struct
        struct.pack_into('<IiiHH',header,0,40,size,-size,1,32)
        pixels=ct.c_void_p()
        screen=self.user.GetDC(None);dc=self.gdi.CreateCompatibleDC(screen)
        bitmap=self.gdi.CreateDIBSection(dc,header,0,ct.byref(pixels),None,0)
        assert bitmap and pixels.value
        old=self.gdi.SelectObject(dc,bitmap)
        try:
            ct.memset(pixels,255,size*size*4)
            assert self.user.DrawIconEx(dc,0,0,icon,size,size,0,None,3)
            assert self.gdi.GdiFlush()
            return Image.frombytes('RGB',(size,size),ct.string_at(pixels,size*size*4),'raw','BGRX')
        finally:
            self.gdi.SelectObject(dc,old);self.gdi.DeleteObject(bitmap)
            self.gdi.DeleteDC(dc);self.user.ReleaseDC(None,screen)

    def window_images(self,window):
        hwnd=self.hwnd(window)
        return {name:self.image(self.user.SendMessageW(hwnd,0x7F,kind,0) or
                               self.user.GetClassLongPtrW(hwnd,-14 if kind==1 else -34) or
                               self.user.GetClassLongPtrW(hwnd,-14))
                for name,kind in [('small',0),('large',1),('small2',2)]}

    def approved_hashes(self,path):
        # The installed Tk's ICO decoder differs from LoadImage on PNG frames.
        # Use an actual plain Tk child as the reference, with only the approved
        # file explicitly assigned; never mock Tk or accept arbitrary pixels.
        import tkinter as tk
        reference=tk.Toplevel()
        reference.withdraw()
        try:
            reference.iconbitmap(str(path))
            return {self.hash(image) for image in self.window_images(reference).values()}
        finally:reference.destroy()

    @staticmethod
    def hash(image):
        return hashlib.sha256(image.tobytes()).hexdigest()


def main():
    """Compare old initialization with the actual fix, or hold an isolated app."""
    import argparse
    import base64
    import io
    import json
    from pathlib import Path
    import ttkbootstrap as ttk
    from ttkbootstrap.icons import Icon
    from workbench.branding import application_icon,apply_window_icon
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    parser.add_argument('--mode',choices=['old','disable-photo','both','app'],default='old')
    parser.add_argument('--hold',type=int,default=0)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    Image.open(io.BytesIO(base64.b64decode(Icon.icon))).save(args.output/'ttkbootstrap-default.png')
    reader=IconReader();result={}
    kwargs={} if args.mode=='old' else {'iconphoto':None}
    if args.mode=='app':
        from gui.main_window import AplusMainWindow
        root=AplusMainWindow(enable_tray=True)
        root.title('PA 图标验收 · 隔离测试')
        root.after_cancel(root.version_controller.startup_check_id)
        root.version_controller.startup_check_id=None
    else:
        root=ttk.Window(themename='darkly',title='Aplus icon diagnosis',**kwargs)
        if args.mode=='both':apply_window_icon(root)
        else:root.iconbitmap(default=str(application_icon()))  # Exact old setter.
    def inspect():
        try:
            result['mode']=args.mode
            result['bitmap']=root.iconbitmap()
            expected=reader.approved_hashes(application_icon())
            for name,image in reader.window_images(root).items():
                image.save(args.output/f'{args.mode}-{name}.png')
                result[name]=dict(sha256=reader.hash(image),approved=reader.hash(image) in expected)
        except AssertionError as exc:result['native_error']=str(exc) or 'Invalid native icon handle'
        finally:
            (args.output/f'{args.mode}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            if not args.hold:
                if args.mode=='app':root._exit_from_tray()
                else:root.destroy()
    def capture_request():
        request=args.output/'capture.request'
        if request.exists():
            from PIL import ImageGrab
            label=request.read_text(encoding='utf-8-sig').strip()
            request.unlink()
            if label not in ('startup','minimize-restored','tray-restored','alt-tab'):
                raise ValueError('Unknown evidence label')
            expected=reader.approved_hashes(application_icon())
            result[label]={name:reader.hash(image) in expected for name,image in reader.window_images(root).items()}
            user=reader.user
            user.FindWindowW.argtypes=[wt.LPCWSTR,wt.LPCWSTR];user.FindWindowW.restype=wt.HWND
            user.GetWindowRect.argtypes=[wt.HWND,ct.POINTER(wt.RECT)]
            user.GetForegroundWindow.restype=wt.HWND
            hwnd=reader.hwnd(root)
            assert user.GetForegroundWindow()==hwnd,'Isolated app is not foreground'
            rect=wt.RECT();assert user.GetWindowRect(hwnd,ct.byref(rect))
            ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom),all_screens=True).save(args.output/f'{label}-window.png')
            rect=wt.RECT();taskbar=user.FindWindowW('Shell_TrayWnd',None)
            assert taskbar and user.GetWindowRect(taskbar,ct.byref(rect))
            ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom),all_screens=True).save(args.output/f'{label}-taskbar.png')
            (args.output/f'{args.mode}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        root.after(250,capture_request)
    root.after(700,inspect)
    if args.hold:
        root.after(1000,capture_request)
        root.after(args.hold*1000,root._exit_from_tray if args.mode=='app' else root.destroy)
    root.mainloop()
    (args.output/f'{args.mode}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
