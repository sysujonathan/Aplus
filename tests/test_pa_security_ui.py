"""Production security epoch JavaScript, without a replacement implementation."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_security_epoch_clears_content_and_rejects_late_replies():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    result = subprocess.run([node, str(Path(__file__).with_name('pa_security_regression.cjs'))],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
