"""Real production navigation under a controlled late search callback."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


def test_delayed_search_cannot_steal_explicit_navigation():
    node=shutil.which('node')
    if not node:pytest.skip('Node.js is supplied by the Windows CI image')
    result=subprocess.run([node,str(Path(__file__).with_name('pa_navigation_regression.cjs'))],
                          capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['explicitNavigationRetained'] is True
