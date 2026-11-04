import shutil
import subprocess
from pathlib import Path

import pytest


def test_ime_composition_and_late_search_preserve_input():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    result = subprocess.run([node, str(Path(__file__).with_name('pa_ime_regression.cjs'))],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
