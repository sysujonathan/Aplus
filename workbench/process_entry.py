"""Commands shared by source and installed desktop subprocesses."""
import sys


def provider_command(vendor):
    if vendor not in ('baostock','tickflow','tencent'):
        raise ValueError('未知行情进程来源')
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--provider-worker', vendor]
    return [sys.executable, '-u', '-m', 'workbench.provider_worker', vendor]


def restore_worker_stdio():
    """A windowed Windows EXE has no Python stdio, even with inherited pipes."""
    import os
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    import ctypes
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetStdHandle.argtypes=[ctypes.c_uint32]
    kernel.GetStdHandle.restype=ctypes.c_void_p
    kernel.GetFileType.argtypes=[ctypes.c_void_p]
    kernel.GetFileType.restype=ctypes.c_uint32
    for name,identifier,mode in (('stdin',-10,'r'),('stdout',-11,'w'),('stderr',-12,'w')):
        if getattr(sys,name) is not None:
            continue
        handle=kernel.GetStdHandle(identifier & 0xffffffff)
        if handle in (None,ctypes.c_void_p(-1).value) or kernel.GetFileType(handle)!=3:
            if name=='stderr':
                setattr(sys,name,open(os.devnull,'w',encoding='utf-8'))
                continue
            raise RuntimeError('行情子进程缺少父进程管道：'+name)
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY if mode=='r' else os.O_WRONLY)
        setattr(sys,name,os.fdopen(fd,mode,encoding='utf-8',newline='\n',buffering=1))


def chart_command(session):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--chart', str(session)]
    return [sys.executable, '-m', 'gui.chart_shell', str(session)]
