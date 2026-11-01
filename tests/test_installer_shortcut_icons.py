"""Execute the production Pascal repair on real links in a fake desktop only."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_repair_is_post_verified_pointer_switch_and_independent_of_tasks():
    source=(ROOT/'packaging/Aplus.iss').read_text(encoding='utf-8')
    body=source.split('procedure CurStepChanged',1)[1]
    assert body.index('MoveFileEx(')<body.index('RefreshExistingShortcutIcons(')
    assert 'WizardIsTaskSelected' not in body
    assert "ExpandConstant('{userdesktop}')" in body
    assert '#include "ShortcutIcons.iss"' in source


def compiler_path():
    candidates=[os.environ.get('APLUS_ISCC'),shutil.which('ISCC.exe'),
        str(Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Inno Setup 7/ISCC.exe'),
        str(Path(os.environ.get('ProgramFiles',''))/'Inno Setup 6/ISCC.exe'),
        str(ROOT.parent/'aplus-windows-installer/.build-tools/Inno/ISCC.exe')]
    found=next((Path(p) for p in candidates if p and Path(p).is_file()),None)
    if found is None and os.environ.get('APLUS_ISCC'):
        pytest.fail('CI compiler was configured but is missing; native tests must not skip')
    return found


def test_configured_ci_compiler_missing_cannot_silently_skip(monkeypatch, tmp_path):
    monkeypatch.setenv('APLUS_ISCC',str(tmp_path/'missing.exe'))
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    monkeypatch.setenv('ProgramFiles',str(tmp_path/'programs'))
    monkeypatch.setattr(shutil,'which',lambda _:None)
    monkeypatch.setitem(compiler_path.__globals__,'ROOT',tmp_path/'checkout')
    with pytest.raises(pytest.fail.Exception,match='native tests must not skip'):
        compiler_path()


def powershell(script, fixture):
    environment={k:v for k,v in os.environ.items() if k.casefold()!='psmodulepath'}
    environment['APLUS_SHORTCUT_FIXTURE']=str(fixture)
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=environment,capture_output=True,text=True,encoding='utf-8-sig',timeout=30)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    return result.stdout


PROTECT_READONLY_LINKS=r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$fixture=Get-Content -LiteralPath $env:APLUS_SHORTCUT_FIXTURE -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($entry in $fixture.links) {
  if($entry.readonly) { (Get-Item -LiteralPath $entry.path).IsReadOnly=$true }
}
"""

def read_links(links):
    """Independent Unicode Shell reader; WSH TargetPath loses non-ACP characters."""
    import ctypes as c
    import uuid
    class GUID(c.Structure):
        _fields_=[('a',c.c_uint32),('b',c.c_uint16),('c',c.c_uint16),('d',c.c_ubyte*8)]
    def guid(value):return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)
    def invoke(pointer,index,types,*values):
        address=c.cast(pointer,c.POINTER(c.POINTER(c.c_void_p))).contents[index]
        result=c.WINFUNCTYPE(c.c_long,c.c_void_p,*types)(address)(pointer,*values)
        if result<0:raise OSError('Shell COM HRESULT '+hex(result & 0xffffffff))
    ole=c.WinDLL('ole32')
    ole.CoInitializeEx.argtypes=[c.c_void_p,c.c_uint32];ole.CoInitializeEx.restype=c.c_long
    ole.CoCreateInstance.argtypes=[c.POINTER(GUID),c.c_void_p,c.c_uint32,c.POINTER(GUID),c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype=c.c_long
    initialized=ole.CoInitializeEx(None,2)
    if initialized<0 and initialized!=-2147417850:raise OSError('COM initialization failed')
    values=[]
    try:
        for entry in links:
            shell=c.c_void_p();persist=c.c_void_p()
            cls=guid('00021401-0000-0000-c000-000000000046')
            interface=guid('000214f9-0000-0000-c000-000000000046')
            assert ole.CoCreateInstance(c.byref(cls),None,1,c.byref(interface),c.byref(shell))==0
            try:
                iid=guid('0000010b-0000-0000-c000-000000000046')
                invoke(shell,0,(c.POINTER(GUID),c.POINTER(c.c_void_p)),c.byref(iid),c.byref(persist))
                invoke(persist,5,(c.c_wchar_p,c.c_uint32),entry['path'],0)
                def text(index):
                    buffer=c.create_unicode_buffer(32768)
                    invoke(shell,index,(c.c_wchar_p,c.c_int),buffer,len(buffer))
                    return buffer.value
                target=c.create_unicode_buffer(32768)
                invoke(shell,3,(c.c_wchar_p,c.c_int,c.c_void_p,c.c_uint32),target,len(target),None,4)
                icon=c.create_unicode_buffer(32768);icon_index=c.c_int();style=c.c_int()
                invoke(shell,16,(c.c_wchar_p,c.c_int,c.POINTER(c.c_int)),icon,len(icon),c.byref(icon_index))
                invoke(shell,14,(c.POINTER(c.c_int),),c.byref(style))
                values.append(dict(name=entry['name'],target=target.value,arguments=text(10),working=text(8),
                                   description=text(6),style=style.value,icon=icon.value+','+str(icon_index.value)))
            finally:
                if persist.value:invoke(persist,2,())
                if shell.value:invoke(shell,2,())
    finally:
        if initialized>=0:ole.CoUninitialize()
    return values


def quoted(value):
    return str(value).replace("'","''")


@pytest.mark.skipif(os.name!='nt',reason='Native Inno compiler')
def test_full_production_installer_compiles_with_shortcut_include(tmp_path):
    compiler=compiler_path()
    if compiler is None:pytest.skip('Inno compiler unavailable')
    payload=tmp_path/'payload';(payload/'AplusApp').mkdir(parents=True)
    for name in ('Aplus.exe','MicrosoftEdgeWebview2Setup.exe','AplusApp/AplusApp.exe'):
        (payload/name).write_bytes(b'synthetic payload - compile only, never installed')
    (payload/'aplus-install.json').write_text('{}',encoding='utf-8')
    result=subprocess.run([str(compiler),'/Q','/DAppVersion=2026.11.01.5',
        '/DPayload='+str(payload),'/DOutput='+str(tmp_path/'output'),
        str(ROOT/'packaging/Aplus.iss')],capture_output=True,text=True,
        encoding='utf-8',errors='replace',timeout=45)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert (tmp_path/'output/AplusSetup-2026.11.01.5-x64.exe').is_file()


@pytest.mark.skipif(os.name!='nt',reason='Native Inno and Windows Shell links')
@pytest.mark.parametrize('case',['upgrade','missing_icon','no_links'])
@pytest.mark.parametrize('program_name',['ASCII App','程序 🧪, App'])
def test_real_inno_repairs_only_owned_managed_existing_links(tmp_path,case,program_name):
    compiler=compiler_path()
    if compiler is None:pytest.skip('Inno compiler unavailable; native repair requires packaging environment')
    desktop=tmp_path/'synthetic desktop';desktop.mkdir()
    app=tmp_path/program_name;app.mkdir()
    target=app/'Aplus.exe';target.write_bytes(b'synthetic executable - never launched')
    other=tmp_path/'other app';other.mkdir();(other/'Aplus.exe').write_bytes(b'not Aplus installation')
    working=tmp_path/'custom working';working.mkdir()
    icon=app/'app/2026.11.01.5/_internal/icon_aplus.ico';icon.parent.mkdir(parents=True)
    if case!='missing_icon':shutil.copy2(ROOT/'icon_aplus.ico',icon)
    personal=tmp_path/'personal.ico';shutil.copy2(ROOT/'icon_aplus.ico',personal)
    old_icon=app/'app/2026.11.01.4/_internal/icon_aplus.ico';old_icon.parent.mkdir(parents=True)
    shutil.copy2(ROOT/'icon_aplus.ico',old_icon)
    links=[]
    for name,link_target,location,readonly in [
        ('Aplus',target,'',False),('renamed A+',target,str(target)+',0',False),
        ('old-version-icon',target,str(old_icon)+',0',False),
        ('personal-icon',target,str(personal)+',0',False),
        ('custom-index',target,str(target)+',2',False),
        ('another-app',other/'Aplus.exe','',False),
        ('readonly',target,'',True),
    ]:
        if case!='no_links':links.append(dict(name=name,path=str(desktop/(name+'.lnk')),target=str(link_target),icon=location,readonly=readonly))
    fixture=tmp_path/'fixture.json';fixture.write_text(json.dumps(dict(links=links,working=str(working))),encoding='utf-8')
    broken=desktop/'broken.lnk';broken.write_bytes(b'not a shortcut')
    (desktop/'directory.lnk').mkdir()
    script=tmp_path/'probe.iss';proof=tmp_path/'proof.txt'
    # Create real links through the native installer API, not the WSH TargetPath
    # setter rejected by the hosted Windows runner before repair can run.
    # https://jrsoftware.org/ishelp/topic_isxfunc_createshelllink.htm
    prepare='\n'.join("    CreateShellLink("+','.join("'"+quoted(value)+"'" for value in (
        item['path'],'Preserve user description',item['target'],
        '--synthetic "keep parameters"',working,item['icon'].rsplit(',',1)[0] if item['icon'] else ''))+
        ','+(item['icon'].rsplit(',',1)[1] if item['icon'] else '0')+',3);'
        for item in links)
    script.write_text('''[Setup]
AppName=Aplus shortcut isolated test
AppVersion=1.0
PrivilegesRequired=lowest
CreateAppDir=no
Uninstallable=no
CreateUninstallRegKey=no
OutputDir=.
OutputBaseFilename=shortcut-probe
Compression=none
[Code]
#include "'''+str(ROOT/'packaging/ShortcutIcons.iss')+'''"
function InitializeSetup(): Boolean;
var Count: Integer;
begin
  if ExpandConstant('{param:prepare|0}') = '1' then begin
'''+prepare+'''
  end else begin
  Count := RefreshExistingShortcutIcons('''+"'"+quoted(desktop)+"','"+quoted(target)+"','"+quoted(icon)+"');\n"+
        "  SaveStringToFile('"+quoted(proof)+"',IntToStr(Count),False);\n"+
        '  end;\n  Result := False;\nend;\n',encoding='utf-8-sig')
    compiled=subprocess.run([str(compiler),'/Q',str(script)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
    assert compiled.returncode==0,compiled.stdout+'\n'+compiled.stderr
    command=[str(tmp_path/'shortcut-probe.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LOG='+str(tmp_path/'probe.log')]
    try:
        result=subprocess.run([*command,'/prepare=1'],capture_output=True,timeout=30)
        assert result.returncode==1  # Native fixture creation only; no installation.
        powershell(PROTECT_READONLY_LINKS,fixture)
        before=read_links(links)
        assert len(before)==len(links)
        for expected,actual in zip(links,before):
            assert Path(actual['target'])==Path(expected['target'])
            assert actual['arguments']=='--synthetic "keep parameters"'
            assert Path(actual['working'])==working
            assert actual['description']=='Preserve user description' and actual['style']==3
            assert actual['icon']==(expected['icon'] or ',0')
        retained={item['path']:Path(item['path']).read_bytes() for item in links
                  if item['name'] in ('personal-icon','custom-index','another-app','readonly')}
        result=subprocess.run(command,capture_output=True,timeout=30)
        assert result.returncode==1  # Harness exits before any installation.
        after=read_links(links)
        expected_changes=3 if case=='upgrade' else 0
        assert int(proof.read_text())==expected_changes
        assert (desktop/'broken.lnk').read_bytes()==b'not a shortcut'
        assert all(Path(path).read_bytes()==raw for path,raw in retained.items())
        for original,updated in zip(before,after):
            assert {k:v for k,v in original.items() if k!='icon'}=={k:v for k,v in updated.items() if k!='icon'}
            if case=='upgrade' and original['name'] in ('Aplus','renamed A+','old-version-icon'):
                assert updated['icon']==str(icon)+',0'
            else:assert updated['icon']==original['icon']
        result=subprocess.run(command,capture_output=True,timeout=30)
        assert result.returncode==1 and int(proof.read_text())==0  # Idempotent retry.
        assert len(list(desktop.glob('*.lnk')))==len(links)+2  # broken file + directory, no recreated links.
    finally:
        for item in links:
            if item['readonly'] and Path(item['path']).exists():Path(item['path']).chmod(0o666)
