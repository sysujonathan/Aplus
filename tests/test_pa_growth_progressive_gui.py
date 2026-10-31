import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Edge WebView acceptance')
def test_case_driven_atlas_and_mask_pixels_in_real_webview():
    root = Path(__file__).resolve().parents[1]
    folder = root / '.test-pa-growth' / ('progressive-gui-' + uuid.uuid4().hex[:8])
    (folder / 'temp').mkdir(parents=True)
    env = dict(os.environ, TEMP=str(folder / 'temp'), TMP=str(folder / 'temp'),
               A_WORKBENCH_HOME=str(folder / 'runtime'), PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, str(root / 'tests/pa_growth_progressive_probe.py'), str(folder)],
                            env=env, cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=90)
    failure = folder / 'failure.txt'
    detail = failure.read_text(encoding='utf-8') if failure.exists() else ''
    assert result.returncode == 0, result.stdout + result.stderr + detail
    receipt = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    assert receipt['ok'] and receipt['synthetic'] and receipt['real_webview']
    assert set(receipt['checks']) == {
        'empty_universe_single_origin_no_phantom_topics',
        'unclassified_case_grows_one_real_star',
        'case_backed_galaxy_drilldown_zoom_and_return',
        'mask_hides_future_pixels_title_and_fullscreen',
        'explicit_four_stage_reveal_original_bytes_and_judgment_preserved',
    }
