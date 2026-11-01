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
    return next((Path(p) for p in candidates if p and Path(p).is_file()),None)


def powershell(script, fixture):
    environment={k:v for k,v in os.environ.items() if k.casefold()!='psmodulepath'}
    environment['APLUS_SHORTCUT_FIXTURE']=str(fixture)
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=environment,capture_output=True,text=True,encoding='utf-8-sig',timeout=30)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    return result.stdout


SETUP_LINKS=r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$fixture=Get-Content -LiteralPath $env:APLUS_SHORTCUT_FIXTURE -Raw -Encoding UTF8 | ConvertFrom-Json
$shell=New-Object -ComObject WScript.Shell
foreach($entry in $fixture.links) {
  $link=$shell.CreateShortcut($entry.path)
  $link.TargetPath=$entry.target
  $link.Arguments='--synthetic "keep parameters"'
  $link.WorkingDirectory=$fixture.working
  $link.Description='Preserve user description'
  $link.WindowStyle=3
  if($entry.icon) { $link.IconLocation=$entry.icon }
  $link.Save()
  if($entry.readonly) { (Get-Item -LiteralPath $entry.path).IsReadOnly=$true }
}
"""

READ_LINKS=r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$fixture=Get-Content -LiteralPath $env:APLUS_SHORTCUT_FIXTURE -Raw -Encoding UTF8 | ConvertFrom-Json
$shell=New-Object -ComObject WScript.Shell
$values=@(foreach($entry in $fixture.links) {
  $link=$shell.CreateShortcut($entry.path)
  [pscustomobject]@{name=$entry.name;target=$link.TargetPath;arguments=$link.Arguments;
    working=$link.WorkingDirectory;description=$link.Description;style=$link.WindowStyle;icon=$link.IconLocation}
})
ConvertTo-Json -InputObject $values -Compress
"""


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
def test_real_inno_repairs_only_owned_managed_existing_links(tmp_path,case):
    compiler=compiler_path()
    if compiler is None:pytest.skip('Inno compiler unavailable; native repair requires packaging environment')
    desktop=tmp_path/'synthetic desktop';desktop.mkdir()
    app=tmp_path/'程序 App';app.mkdir()
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
        ('another-app',other/'Aplus.exe','',False),
        ('readonly',target,'',True),
    ]:
        if case!='no_links':links.append(dict(name=name,path=str(desktop/(name+'.lnk')),target=str(link_target),icon=location,readonly=readonly))
    fixture=tmp_path/'fixture.json';fixture.write_text(json.dumps(dict(links=links,working=str(working))),encoding='utf-8')
    powershell(SETUP_LINKS,fixture)
    broken=desktop/'broken.lnk';broken.write_bytes(b'not a shortcut')
    (desktop/'directory.lnk').mkdir()
    before=json.loads(powershell(READ_LINKS,fixture))
    script=tmp_path/'probe.iss';proof=tmp_path/'proof.txt'
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
  Count := RefreshExistingShortcutIcons('''+"'"+quoted(desktop)+"','"+quoted(target)+"','"+quoted(icon)+"');\n"+
        "  SaveStringToFile('"+quoted(proof)+"',IntToStr(Count),False);\n"+
        '  Result := False;\nend;\n',encoding='utf-8')
    compiled=subprocess.run([str(compiler),'/Q',str(script)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
    assert compiled.returncode==0,compiled.stdout+'\n'+compiled.stderr
    command=[str(tmp_path/'shortcut-probe.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART']
    try:
        result=subprocess.run(command,capture_output=True,timeout=30)
        assert result.returncode==1  # Harness exits before any installation.
        after=json.loads(powershell(READ_LINKS,fixture))
        expected_changes=3 if case=='upgrade' else 0
        assert int(proof.read_text())==expected_changes
        assert (desktop/'broken.lnk').read_bytes()==b'not a shortcut'
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
            if item['readonly']:Path(item['path']).chmod(0o666)
