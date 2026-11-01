"""Approved vectors reach actual Windows icon outlets without Store or UI redesign."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace
import queue
from unittest.mock import Mock

from PIL import Image
import pytest

from workbench import branding

ROOT=Path(__file__).resolve().parents[1]


def test_production_ico_is_the_approved_seven_frame_export():
    raw=(ROOT/'icon_aplus.ico').read_bytes()
    assert raw==(ROOT/'assets/brand/aplus.ico').read_bytes()
    manifest=json.loads((ROOT/'assets/brand/manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(raw).hexdigest()==manifest['files']['aplus.ico']['sha256']
    with Image.open(ROOT/'icon_aplus.ico') as image:
        assert image.ico.sizes()=={(n,n) for n in (16,24,32,48,64,128,256)}


def test_branding_is_cwd_independent_and_has_no_data_dependency(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert branding.application_icon()==ROOT/'icon_aplus.ico'
    result=subprocess.run([sys.executable,'-c',
        "import sys; from workbench.branding import application_icon; assert application_icon().is_file(); "
        "assert 'workbench.store' not in sys.modules; assert 'ttkbootstrap' not in sys.modules"],
        cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_root_sets_default_icon_for_child_dialogs():
    window=Mock()
    assert branding.apply_window_icon(window)
    window.iconbitmap.assert_called_once_with(default=str(ROOT/'icon_aplus.ico'))


def test_actual_tray_entry_loads_the_approved_icon(monkeypatch):
    from gui.main_window import AplusMainWindow
    calls={}
    def icon(name,image,title,menu):
        calls.update(image=image.copy(),title=title)
        return SimpleNamespace(run_detached=Mock())
    monkeypatch.setitem(sys.modules,'pystray',SimpleNamespace(Icon=icon,
        Menu=lambda *items:items,MenuItem=lambda *args,**kwargs:(args,kwargs)))
    window=SimpleNamespace(_tray_actions=queue.Queue(),_hide_to_tray=Mock(),
        _exit_from_tray=Mock(),_poll_tray_actions=Mock(),protocol=Mock(),after=Mock(),_status_text=Mock())
    AplusMainWindow._setup_tray(window)
    assert calls['title']==branding.APPLICATION_TITLE
    with Image.open(ROOT/'assets/brand/aplus.ico') as image:
        assert calls['image'].tobytes()==image.convert('RGBA').tobytes()
    window._status_text.set.assert_not_called()


@pytest.mark.parametrize('failure',['missing','tk_error'])
def test_cosmetic_icon_failure_does_not_block_startup(tmp_path,monkeypatch,failure):
    from tkinter import TclError
    window=Mock()
    if failure=='missing':monkeypatch.setattr(branding,'application_icon',lambda:tmp_path/'missing.ico')
    else:window.iconbitmap.side_effect=TclError('synthetic icon unavailable')
    assert branding.apply_window_icon(window) is False


def test_shortcuts_use_versioned_icon_but_keep_stable_launch_target():
    text=(ROOT/'packaging/Aplus.iss').read_text(encoding='utf-8')
    icons=text.split('[Icons]',1)[1].split('[Run]',1)[0]
    for line in icons.strip().splitlines():
        assert 'Filename: "{app}\\Aplus.exe"' in line
        assert 'IconFilename: "{app}\\app\\{#AppVersion}\\_internal\\icon_aplus.ico"' in line
    for name in ('Aplus.spec','Bridge.spec'):
        assert "root/'icon_aplus.ico'" in (ROOT/'packaging'/name).read_text(encoding='utf-8')


@pytest.mark.skipif(os.name!='nt',reason='Real native PE icon resources')
def test_compiled_launcher_contains_exact_approved_ico_frames(tmp_path):
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    executable=tmp_path/'Aplus.exe'
    subprocess.run([str(compiler),'/nologo','/target:winexe','/reference:System.Windows.Forms.dll',
        '/reference:System.Web.Extensions.dll','/win32icon:'+str(ROOT/'icon_aplus.ico'),
        '/out:'+str(executable),str(ROOT/'packaging/Launcher.cs')],
        capture_output=True,check=True,timeout=30)
    raw=(ROOT/'icon_aplus.ico').read_bytes();count=struct.unpack_from('<H',raw,4)[0]
    expected=[]
    for i in range(count):
        size,offset=struct.unpack_from('<II',raw,6+i*16+8)
        expected.append(raw[offset:offset+size])
    kernel=ctypes.windll.kernel32
    kernel.LoadLibraryExW.argtypes=[ctypes.c_wchar_p,ctypes.c_void_p,ctypes.c_uint32]
    kernel.LoadLibraryExW.restype=ctypes.c_void_p
    kernel.FindResourceW.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    kernel.FindResourceW.restype=ctypes.c_void_p
    kernel.LoadResource.argtypes=[ctypes.c_void_p,ctypes.c_void_p];kernel.LoadResource.restype=ctypes.c_void_p
    kernel.SizeofResource.argtypes=[ctypes.c_void_p,ctypes.c_void_p];kernel.SizeofResource.restype=ctypes.c_uint32
    kernel.LockResource.argtypes=[ctypes.c_void_p];kernel.LockResource.restype=ctypes.c_void_p
    library=kernel.LoadLibraryExW(str(executable),None,2)
    assert library
    try:
        actual=[]
        names=[]
        callback_type=ctypes.WINFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ssize_t)
        def collect(_module,_type,name,_context):
            names.append(name or 0)
            return 1
        callback=callback_type(collect)
        kernel.EnumResourceNamesW.argtypes=[ctypes.c_void_p,ctypes.c_void_p,callback_type,ctypes.c_ssize_t]
        assert kernel.EnumResourceNamesW(library,ctypes.c_void_p(3),callback,0)
        assert len(names)==count
        for name in names:
            resource=kernel.FindResourceW(library,ctypes.c_void_p(name),ctypes.c_void_p(3))
            assert resource
            size=kernel.SizeofResource(library,resource)
            pointer=kernel.LockResource(kernel.LoadResource(library,resource))
            actual.append(ctypes.string_at(pointer,size))
        assert sorted(hashlib.sha256(v).digest() for v in actual)==sorted(hashlib.sha256(v).digest() for v in expected)
    finally:kernel.FreeLibrary(ctypes.c_void_p(library))
