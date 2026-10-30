"""Build an isolated Windows setup; official publication needs exact main CI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import re
from urllib.request import urlretrieve

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(command,**kwargs):
    subprocess.run(list(map(str,command)),check=True,cwd=ROOT,**kwargs)


def write_version_resources(release,folder):
    from workbench.release_identity import version_parts
    parts=version_parts(release['version']);version=release['version']
    info=folder/'AplusApp.version.txt'
    info.write_text(f'''VSVersionInfo(
ffi=FixedFileInfo(filevers={parts!r}, prodvers={parts!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1),
kids=[StringFileInfo([StringTable('040904B0',[
StringStruct('CompanyName','Aplus Project'),StringStruct('FileDescription','Aplus'),
StringStruct('FileVersion',{version!r}),StringStruct('ProductVersion',{version!r}),
StringStruct('ProductName','Aplus'),StringStruct('OriginalFilename','AplusApp.exe')])]),
VarFileInfo([VarStruct('Translation',[1033,1200])])])''',encoding='utf-8')
    assembly=folder/'LauncherVersion.cs'
    assembly.write_text('using System.Reflection;\n'+
        '\n'.join('[assembly: '+name+'("'+value+'")]' for name,value in (
            ('AssemblyVersion',version),('AssemblyFileVersion',version),
            ('AssemblyInformationalVersion',version),('AssemblyProduct','Aplus'))),encoding='utf-8')
    return info,assembly


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--iscc',required=True,type=Path)
    parser.add_argument('--official',action='store_true')
    parser.add_argument('--version')
    parser.add_argument('--sequence',type=int)
    parser.add_argument('--signtool',type=Path)
    parser.add_argument('--certificate-thumbprint')
    parser.add_argument('--skip-freeze',action='store_true',help='Recompile setup around an already-built payload')
    args=parser.parse_args(argv)
    release=json.loads((ROOT/'packaging/release.json').read_text(encoding='utf-8'))
    if args.version:release['version']=args.version
    if args.sequence:release['sequence']=args.sequence
    from workbench.release_identity import release_sequence,validate_identity
    if release_sequence(release['version'])!=release['sequence']:
        raise SystemExit('Release version and sequence disagree')
    if args.official and args.skip_freeze:
        raise SystemExit('Official builds must freshly freeze their exact source')
    if bool(args.signtool)!=bool(args.certificate_thumbprint):
        raise SystemExit('Both signtool and certificate thumbprint are required for signing')
    def sign(path):
        if args.signtool:
            run([args.signtool,'sign','/sha1',args.certificate_thumbprint,'/fd','SHA256',
                 '/tr','http://timestamp.digicert.com','/td','SHA256',path])
            run([args.signtool,'verify','/pa','/all',path])
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT,text=True).strip())
    if args.official:
        from workbench.version_update import github_json, acceptance
        if dirty or github_json('/commits/main')['sha']!=commit or not acceptance(commit):
            raise SystemExit('Official packaging requires a clean exact main with successful push acceptance')
    release.update(commit=commit,preview=not args.official)
    validate_identity(release)
    metadata=ROOT/'build'/'metadata'/'installed-release.json'
    metadata.parent.mkdir(parents=True,exist_ok=True)
    metadata.write_text(json.dumps(release,indent=2),encoding='utf-8')
    version_file,assembly=write_version_resources(release,metadata.parent)
    env=dict(os.environ,APLUS_BUILD_METADATA=str(metadata),APLUS_BUILD_VERSION_FILE=str(version_file))
    if not args.skip_freeze:
        run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',ROOT/'packaging/Aplus.spec'],env=env)
    payload=ROOT/'dist';app=payload/'AplusApp'
    # Never wrap payload from a different build in a new release identity.
    if json.loads((app/'_internal/installed-release.json').read_text())!=release:
        raise SystemExit('Payload release identity does not match this build')
    report=ROOT/'build'/'self-test.json'
    run([app/'AplusApp.exe','--self-test',report],timeout=60)
    if not json.loads(report.read_text())['chart_assets']:raise SystemExit('Chart assets missing')
    # A working desktop alone cannot prove that its real windowed EXE can
    # launch a provider child and exchange pipe messages. Probe offline before
    # wrapping any installer; never contact a supplier during this build gate.
    run([sys.executable,ROOT/'tools/test_installed_providers.py',app/'AplusApp.exe'],timeout=120)
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    run([compiler,'/nologo','/target:winexe','/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll',
         '/win32icon:'+str(ROOT/'icon_aplus.ico'),'/out:'+str(payload/'Aplus.exe'),ROOT/'packaging/Launcher.cs',assembly])
    sign(payload/'Aplus.exe')
    sign(app/'AplusApp.exe')
    from workbench.installed_cleanup import write_inventory
    write_inventory(app)
    (payload/'aplus-install.json').write_text(json.dumps({'protocol':1,'app_id':'Aplus-installed-v1'}),encoding='utf-8')
    bootstrap=payload/'MicrosoftEdgeWebview2Setup.exe'
    if not bootstrap.exists():
        urlretrieve('https://go.microsoft.com/fwlink/p/?LinkId=2124703',bootstrap)
    verify="""$signature=Get-AuthenticodeSignature -LiteralPath $env:APLUS_VERIFY_FILE
Write-Output ($signature.Status.ToString()+': '+$signature.SignerCertificate.Subject)
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { exit 1 }
"""
    verify_env=dict(os.environ,APLUS_VERIFY_FILE=str(bootstrap))
    # A Python child of PowerShell 7 inherits its Core module search path;
    # Windows PowerShell 5 must load its own Authenticode module instead.
    verify_env={key:value for key,value in verify_env.items() if key.casefold()!='psmodulepath'}
    run(['powershell.exe','-NoProfile','-NonInteractive','-Command',verify],env=verify_env)
    output=payload/'installers';output.mkdir(exist_ok=True)
    run([args.iscc.resolve(),'/DAppVersion='+release['version'],'/DPayload='+str(payload),
         '/DOutput='+str(output),ROOT/'packaging/Aplus.iss'])
    setup=output/('AplusSetup-'+release['version']+'-x64.exe')
    sign(setup)
    release['package']=dict(url='https://github.com/sysujonathan/Aplus/releases/download/v'+
        release['version']+'/'+setup.name,sha256=hashlib.sha256(setup.read_bytes()).hexdigest(),bytes=setup.stat().st_size)
    (output/'latest.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    setup.with_suffix('.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    print(json.dumps(dict(setup=str(setup),sha256=release['package']['sha256'],preview=release['preview'])))


if __name__=='__main__':main()
