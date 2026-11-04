from types import SimpleNamespace
import os

import pytest

from workbench.security_names import parse_security_name, fetch_security_name, MAX_RESPONSE


@pytest.mark.parametrize('payload', [
    '', 'v_sz160723="51~~160723";', 'v_sz160723="51~错误~159570";',
    'v_sh160723="51~错误~160723";',
    'v_sz160723="51~甲~160723";v_sz160723="51~乙~160723";',
    'v_sz160723="51~坏\x01名称~160723";',
])
def test_name_identity_and_duplicate_validation(payload):
    with pytest.raises(ValueError):parse_security_name(payload, '160723')


def test_metadata_lookup_needs_no_quote_history_or_trading_timestamp():
    body = 'v_sz160723="51~嘉实原油LOF~160723";'.encode('gbk')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self, maximum):
            assert maximum==MAX_RESPONSE+1
            return body
    class Opener:
        def open(self, request, timeout):
            assert request.full_url=='https://qt.gtimg.cn/q=sz160723' and timeout==8
            return Response()
    assert fetch_security_name('160723',opener=Opener())=='嘉实原油LOF'


def test_saved_fund_names_survive_missing_or_broken_stock_catalogs(tmp_path):
    from workbench.store import Store
    from gui.data import code_names
    store=Store(tmp_path)
    store.add_manual_watch('160723','嘉实原油LOF')
    store.execute("INSERT INTO meta VALUES('universe_date','2026-11-03')")
    (tmp_path/'directories').mkdir()
    (tmp_path/'directories/2026-11-03-basics.csv').write_text('code,code_name\nsz.159570,样本ETF\n',encoding='utf-8')
    assert code_names(store)['sz.159570']=='样本ETF'  # universe.csv is missing
    assert code_names(store)['sz.160723']=='嘉实原油LOF'
    (tmp_path/'universe.csv').write_text('bad_column\nbad_value\n',encoding='utf-8')
    assert code_names(store)['sz.159570']=='样本ETF'
    assert store.rows('SELECT * FROM observations')==[]
    assert store.rows('SELECT * FROM plans')==[]


class Variable:
    def __init__(self, value=''):self.value,self.traces=value,{}
    def get(self):return self.value
    def set(self, value):
        self.value=value
        for callback in list(self.traces.values()):callback()
    def trace_add(self, _mode, callback):
        self.traces['trace']=callback
        return 'trace'
    def trace_remove(self, _mode, identity):self.traces.pop(identity)


class Parent:
    def __init__(self):self.callbacks={}
    def bind(self, *args, **kwargs):pass
    def after(self, delay, callback):
        self.callbacks['timer']=callback
        return 'timer'
    def after_cancel(self, timer):self.callbacks.pop(timer,None)


def controller(monkeypatch, fetch):
    import gui.security_name_lookup as module
    workers=[]
    monkeypatch.setattr(module.threading,'Thread',lambda **kwargs:SimpleNamespace(start=lambda:workers.append(kwargs['target'])))
    parent,code,name,status=Parent(),Variable(),Variable(),Variable()
    lookup=module.SecurityNameLookup(parent,code,name,{},status,fetch=fetch)
    return lookup,code,name,status,workers


def test_late_result_cannot_rename_new_code_and_user_name_wins(monkeypatch):
    lookup,code,name,status,workers=controller(monkeypatch,lambda code:{'sz.160723':'原油样本','sh.600000':'股票样本'}[code])
    code.set('160723')
    assert status.get()=='名称（查询中…）' and not name.get()
    code.set('600000')
    assert len(workers)==1  # at most one network request in flight
    workers.pop(0)();lookup.poll()
    assert not name.get() and len(workers)==1
    name.set('人工名称')
    workers.pop(0)();lookup.poll()
    assert name.get()=='人工名称'
    assert lookup.names['sz.160723']=='原油样本'


def test_owned_name_clears_on_code_change_and_close_ignores_pending_result(monkeypatch):
    lookup,code,name,status,workers=controller(monkeypatch,lambda _: '原油样本')
    code.set('160723');workers.pop(0)();lookup.poll()
    assert name.get()=='原油样本'
    code.set('159570')
    assert not name.get()
    lookup.close()
    workers.pop(0)();lookup.poll()
    assert not name.get() and not code.traces


def test_lookup_failure_keeps_form_usable_without_retry_storm(monkeypatch):
    def failed(_):raise TimeoutError()
    lookup,code,name,status,workers=controller(monkeypatch,failed)
    code.set('160723');workers.pop(0)();lookup.poll()
    assert status.get()=='名称（可手动填写）' and not lookup.busy
    lookup.request('sz.160723')
    assert not workers
    name.set('手填名称')
    assert name.get()=='手填名称'


@pytest.mark.skipif(os.name!='nt', reason='Native Windows holding form')
def test_native_holding_form_resolves_fund_without_focus_out(tmp_path):
    import subprocess,sys
    from pathlib import Path
    result=subprocess.run([sys.executable,'-c',
        'from tests.test_security_names import native_form; import sys; native_form(sys.argv[1])',str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


def native_form(path):
    from pathlib import Path
    import time
    import ttkbootstrap as ttk
    import gui.security_name_lookup as lookup_module
    from gui.trade_management import _PositionDialog
    from workbench.store import Store
    lookup_module.fetch_security_name=lambda _: '合成原油LOF'
    store=Store(Path(path))
    root=ttk.Window(themename='darkly');root.withdraw()
    errors=[]
    root.report_callback_exception=lambda *args:errors.append(args)
    try:
        dialog=_PositionDialog(root,store,{})
        dialog.code.set('160723')  # no FocusOut event
        deadline=time.monotonic()+5
        while not dialog.name.get() and time.monotonic()<deadline:
            root.update();time.sleep(.01)
        assert dialog.name.get()=='合成原油LOF'
        dialog.code.set('16072')
        assert not dialog.name.get()
        dialog.code.set('160723')
        assert dialog.name.get()=='合成原油LOF'  # session cache, no request
        dialog.top.destroy();root.update()
        assert not errors
        assert not store.rows('SELECT * FROM positions')
    finally:root.destroy()
