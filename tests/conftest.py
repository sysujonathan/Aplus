"""All test entry points, including a bare import of app, use isolated storage."""
import pytest


@pytest.fixture(autouse=True)
def release_destroyed_tk_interpreters():
    """Test windows must not retain ttkbootstrap's process-wide old Tk root.

    Production uses one persistent root. Tests create/destroy multiple roots;
    dispose stale styles and Tk callback cycles on the test's main thread.
    """
    import gc
    import tkinter as tk
    from ttkbootstrap import Style
    if tk._default_root is None:
        Style.instance = None
        gc.collect()
    yield
    if tk._default_root is None:
        Style.instance = None
        gc.collect()


@pytest.fixture(autouse=True)
def isolate_default_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('A_WORKBENCH_HOME',str(tmp_path/'default-runtime'))
