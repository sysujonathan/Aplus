"""All test entry points, including a bare import of app, use isolated storage."""
import pytest


@pytest.fixture(autouse=True)
def isolate_default_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('A_WORKBENCH_HOME',str(tmp_path/'default-runtime'))
