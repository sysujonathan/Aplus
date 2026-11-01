"""Execute the shipped allocator, including GitHub's paginated draft inventory."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


@pytest.mark.skipif(sys.platform != 'win32', reason='Actual Windows workflow PowerShell')
@pytest.mark.parametrize('failed', [False, True])
def test_reserved_draft_on_later_page_is_not_reallocated(tmp_path, failed):
    root=Path(__file__).resolve().parents[1]
    job=yaml.load((root/'.github/workflows/installed-release.yml').read_text(),Loader=yaml.BaseLoader)['jobs']['identity']
    block=next(s['run'] for s in job['steps'] if s.get('id')=='identity')
    block=block.replace('[DateTime]::UtcNow',"[DateTime]::Parse('2026-11-01T05:00:00Z').ToUniversalTime()")
    pages=[[dict(tag_name='v2026.11.01.1',draft=False)]*100,
           [dict(tag_name='v2026.11.01.2',draft=True,target_commitish='old-head')]]
    (tmp_path/'packaging').mkdir()
    (tmp_path/'packaging/release.json').write_text(json.dumps(dict(sequence=202610300008)))
    output=tmp_path/'outputs.txt';envfile=tmp_path/'env.txt'
    stub=("$ErrorActionPreference='Stop'\n$global:LASTEXITCODE=0\nfunction git {'accepted-head'}\n"
          "function gh { if($args -contains '--jq') {'accepted-head'} else {"
          "$global:LASTEXITCODE="+str(int(failed))+"; '"+json.dumps(pages)+"' } }\n")
    script=tmp_path/'allocate.ps1';script.write_text(stub+block,encoding='utf-8-sig')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script)],cwd=tmp_path,
        env=dict(os.environ,GITHUB_ENV=str(envfile),GITHUB_OUTPUT=str(output),GITHUB_REPOSITORY='sysujonathan/Aplus'),
        capture_output=True,timeout=30)
    if failed:
        assert result.returncode!=0
        assert not output.exists() and not envfile.exists()
    else:
        assert result.returncode==0,result.stderr.decode(errors='replace')
        assert 'version=2026.11.01.3' in output.read_text(encoding='utf-16')
        assert 'sequence=202611010003' in output.read_text(encoding='utf-16')
