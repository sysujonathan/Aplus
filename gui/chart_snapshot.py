"""Export an explicitly requested chart PNG."""
import base64
import ctypes
from ctypes import wintypes
import io
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from PIL import Image


def _png_bytes(data_url):
    prefix = 'data:image/png;base64,'
    if not isinstance(data_url, str) or not data_url.startswith(prefix) or len(data_url) > 64_000_000:
        raise ValueError('快照不是有效 PNG')
    return base64.b64decode(data_url[len(prefix):], validate=True)


def _check_png(image):
    if image.format != 'PNG' or image.width * image.height > 32_000_000:
        raise ValueError('快照格式或尺寸无效')


def png_dib(data_url):
    data = _png_bytes(data_url)
    with Image.open(io.BytesIO(data)) as image:
        _check_png(image)
        output = io.BytesIO()
        image.convert('RGB').save(output, format='BMP')
    return output.getvalue()[14:]


def _validated_png(data_url):
    data = _png_bytes(data_url)
    with Image.open(io.BytesIO(data)) as image:
        _check_png(image)
        image.verify()
    return data


def save_chart_png(data_url, filename, choose_path):
    data = _validated_png(data_url)
    if not isinstance(filename, str) or not re.fullmatch(r'Aplus-[a-zA-Z0-9.-]{1,60}\.png', filename):
        filename = 'Aplus-chart.png'
    paths = choose_path(filename)
    if not paths:
        return {'ok': False}
    Path(paths[0]).write_bytes(data)
    return {'ok': True}


def local_chart_png(data_url, runtime_root):
    data = _validated_png(data_url)
    folder = runtime_root / 'chart_snapshots'
    folder.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=folder, prefix='Aplus-', suffix='.png', delete=False) as output:
        output.write(data)
        return Path(output.name).resolve().as_uri()


def copy_chart_png(data_url, owner):
    return _set_clipboard(owner, 8, png_dib(data_url))  # CF_DIB


def copy_snapshot_link(uri, owner):
    return _set_clipboard(owner, 13, (uri+'\0').encode('utf-16-le'))  # CF_UNICODETEXT


def open_snapshot_in_browser(uri):
    if os.name != 'nt':
        import webbrowser
        return webbrowser.open_new_tab(uri)

    # Ask Windows for the HTTPS browser so a local PNG cannot open in Photos.
    query = ctypes.WinDLL('shlwapi').AssocQueryStringW
    query.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                      wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = ctypes.c_long
    length = wintypes.DWORD(32768)
    executable = ctypes.create_unicode_buffer(length.value)
    assocf_is_protocol, assocstr_executable = 0x1000, 2
    if query(assocf_is_protocol, assocstr_executable, 'https', 'open', executable, ctypes.byref(length)):
        return False
    browser = Path(executable.value)
    if browser.name.casefold() in ('openwith.exe','rundll32.exe','launchwinapp.exe') or not browser.is_file():
        return False
    import subprocess
    subprocess.Popen([executable.value, uri])
    return True


def _set_clipboard(owner, clipboard_format, data):
    if os.name != 'nt':
        raise RuntimeError('当前系统请使用浏览器图片剪贴板')
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
        if not user.EmptyClipboard() or not user.SetClipboardData(clipboard_format, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = None  # Clipboard owns the allocation after success.
        return {'ok': True}
    finally:
        if opened:
            user.CloseClipboard()
        if handle:
            kernel.GlobalFree(handle)
