import pytest
from tkinter import TclError

from tests import native_ui


def setup_error(path):
    return TclError("Can't find a usable init.tcl in the following directories:\n"
                    f'couldn\'t read file "{path.as_posix()}": No error\n')


def test_successful_native_setup_is_not_repeated(monkeypatch):
    calls=[]; window=object()
    monkeypatch.setattr(native_ui,'sleep',lambda *_:pytest.fail('成功时不等待'))
    assert native_ui.create_window(lambda:calls.append(True) or window) is window
    assert calls==[True]


def test_only_readable_windows_init_failure_is_retried_once(tmp_path,monkeypatch):
    script=tmp_path/'init.tcl'; script.write_bytes(b'# synthetic test initialization')
    monkeypatch.setattr(native_ui,'WINDOWS',True)
    events=[]; window=object()
    monkeypatch.setattr(native_ui,'collect',lambda:events.append('cleanup'))
    monkeypatch.setattr(native_ui,'sleep',lambda delay:events.append(('wait',delay)))
    def factory():
        events.append('create')
        if events.count('create')==1: raise setup_error(script)
        return window
    with pytest.warns(RuntimeWarning,match='retrying native window setup once'):
        assert native_ui.create_window(factory) is window
    assert events==['create','cleanup',('wait',.1),'create']


def test_persistent_init_failure_still_fails_after_two_attempts(tmp_path,monkeypatch):
    script=tmp_path/'init.tcl'; script.write_bytes(b'# synthetic')
    monkeypatch.setattr(native_ui,'WINDOWS',True)
    monkeypatch.setattr(native_ui,'sleep',lambda *_:None)
    calls=[]; error=setup_error(script)
    def factory():
        calls.append(True); raise error
    with pytest.warns(RuntimeWarning),pytest.raises(TclError) as caught:
        native_ui.create_window(factory)
    assert caught.value is error and len(calls)==2


@pytest.mark.parametrize('problem',['missing','empty','other_tcl','assertion','not_windows','read_error'])
def test_real_setup_problems_are_not_retried(tmp_path,monkeypatch,problem):
    script=tmp_path/'init.tcl'
    if problem!='missing': script.write_bytes(b'' if problem=='empty' else b'# synthetic')
    monkeypatch.setattr(native_ui,'WINDOWS',problem!='not_windows')
    monkeypatch.setattr(native_ui,'sleep',lambda *_:pytest.fail('不应重试'))
    if problem=='other_tcl': error=TclError('invalid command name "some-widget"')
    elif problem=='assertion': error=AssertionError('真实断言失败')
    elif problem=='read_error':
        error=TclError(str(setup_error(script)).replace(': No error', ': permission denied'))
    else: error=setup_error(script)
    calls=[]
    def factory():
        calls.append(True); raise error
    with pytest.raises(type(error)) as caught:
        native_ui.create_window(factory)
    assert caught.value is error and calls==[True]
