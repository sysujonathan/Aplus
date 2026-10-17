"""Copy an explicitly requested chart PNG to the Windows image clipboard."""
import base64
import ctypes
from ctypes import wintypes
import io
import os

from PIL import Image


def png_dib(data_url):
    prefix = 'data:image/png;base64,'
    if not isinstance(data_url, str) or not data_url.startswith(prefix) or len(data_url) > 64_000_000:
        raise ValueError('快照不是有效 PNG')
    data = base64.b64decode(data_url[len(prefix):], validate=True)
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or image.width * image.height > 32_000_000:
            raise ValueError('快照格式或尺寸无效')
        output = io.BytesIO()
        image.convert('RGB').save(output, format='BMP')
    return output.getvalue()[14:]


def copy_chart_png(data_url, owner):
    if os.name != 'nt':
        raise RuntimeError('当前系统请使用浏览器图片剪贴板')
    data = png_dib(data_url)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    handle = kernel.GlobalAlloc(2, len(data))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    opened = False
    try:
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(pointer, data, len(data))
        kernel.GlobalUnlock(handle)
        if not user.OpenClipboard(int(owner)):
            raise ctypes.WinError(ctypes.get_last_error())
        opened = True
        if not user.EmptyClipboard() or not user.SetClipboardData(8, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = None  # Clipboard owns the allocation after success.
        return {'ok': True}
    finally:
        if opened:
            user.CloseClipboard()
        if handle:
            kernel.GlobalFree(handle)
