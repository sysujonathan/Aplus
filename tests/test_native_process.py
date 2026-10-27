"""Exercise real worker processes; failures must never turn into passes."""
import os
import subprocess
import sys

import pytest

from tests.native_process import ROOT, run_native_test


@pytest.mark.parametrize('failure', ['', 'fixture', 'assertion'])
@pytest.mark.skipif(os.name != 'nt', reason='Windows native process protocol')
def test_native_worker_isolates_fixtures_and_preserves_failures(tmp_path, failure):
    (tmp_path / 'conftest.py').write_text(
        f'import sys\nsys.path.insert(0, {str(ROOT)!r})\n'
        "pytest_plugins = ['tests.native_process']\n", encoding='utf-8')
    (tmp_path / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    (tmp_path / 'test_probe.py').write_text(
        'import os\nfrom pathlib import Path\nimport pytest\n'
        '@pytest.fixture\ndef native_fixture():\n'
        f'    assert os.getpid() != {os.getpid()}\n'
        "    assert os.environ.get('APLUS_NATIVE_TEST_NODE') == 'test_probe.py::test_probe'\n"
        "    path = Path('setups')\n"
        "    path.write_text(path.read_text() + 'setup\\n' if path.exists() else 'setup\\n')\n"
        f"    if {failure == 'fixture'}: raise RuntimeError('FIXTURE_FAILURE_SENTINEL')\n"
        '@pytest.mark.native_process\ndef test_probe(native_fixture):\n'
        f"    assert not {failure == 'assertion'}, 'ASSERTION_FAILURE_SENTINEL'\n",
        encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', 'test_probe.py::test_probe'],
        cwd=tmp_path, capture_output=True, text=True, timeout=45,
    )
    assert (tmp_path / 'setups').exists(), result.stdout + result.stderr
    assert (tmp_path / 'setups').read_text() == 'setup\n'
    if failure:
        assert result.returncode != 0
        assert f'{failure.upper()}_FAILURE_SENTINEL' in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert '1 passed' in result.stdout


def test_native_worker_does_not_hide_timeout(tmp_path):
    (tmp_path / 'test_slow.py').write_text(
        'import time\ndef test_slow(): time.sleep(30)\n', encoding='utf-8')
    with pytest.raises(subprocess.TimeoutExpired):
        run_native_test('test_slow.py::test_slow', root=tmp_path, timeout=1)


@pytest.mark.skipif(os.name != 'nt', reason='Windows native process protocol')
@pytest.mark.parametrize('teardown_error', [False, True])
def test_native_protocol_cleans_previous_module_before_next(tmp_path, teardown_error):
    (tmp_path / 'conftest.py').write_text(
        f'import sys\nsys.path.insert(0, {str(ROOT)!r})\n'
        "pytest_plugins = ['tests.native_process']\n", encoding='utf-8')
    (tmp_path / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    (tmp_path / 'test_a.py').write_text(
        'from pathlib import Path\nimport pytest\n'
        "@pytest.fixture(scope='module', autouse=True)\ndef module_state():\n"
        '    yield\n'
        "    Path('cleaned').write_text('yes')\n"
        f"    if {teardown_error}: raise RuntimeError('TEARDOWN_FAILURE_SENTINEL')\n"
        'def test_ordinary(): pass\n'
        '@pytest.mark.native_process\ndef test_native(): pass\n', encoding='utf-8')
    (tmp_path / 'test_b.py').write_text(
        "from pathlib import Path\ndef test_next(): assert Path('cleaned').exists()\n",
        encoding='utf-8')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=45)
    if teardown_error:
        assert result.returncode != 0
        assert 'TEARDOWN_FAILURE_SENTINEL' in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert '3 passed' in result.stdout
