"""Synthetic repositories and static source checks; never use a business DB."""
import ast
import json
from pathlib import Path
from unittest.mock import Mock
from contextlib import nullcontext

import pytest

from workbench import version_update as v
from tests.test_version_update import repo, git, api_for, installation_contract
from tests.test_workbench import store


BASE = '''from pathlib import Path
def resolve_runtime_root(root):
    return Path(root)
class Store:
    def __init__(self, root):
        self.root = resolve_runtime_root(root)
        self.version = 8
    def execute(self, sql, params=()):
        return (sql, params)
    def save(self, value):
        return value
'''
ADDITION = '''
    def save_preference(self, value):
        return self.execute('INSERT INTO meta VALUES(?,?)', ('preference', value))
'''


def test_additive_methods_and_comments_are_compatible_without_executing_code():
    assert v.compatible_store_extension(BASE, BASE + ADDITION)
    assert v.compatible_store_extension(BASE, '# formatting only\n' + BASE)


@pytest.mark.parametrize('change', [
    lambda s: s.replace('self.version = 8', 'self.version = 9'),
    lambda s: s.replace('self.version = 8', "self.execute('ALTER TABLE accounts ADD COLUMN x TEXT')"),
    lambda s: s.replace('return Path(root)', "return Path('another-runtime')"),
    lambda s: s.replace('class Store:', 'class Store(object):'),
    lambda s: s.replace('class Store:', "class Store:\n    schema_version = 8"),
    lambda s: s.replace('    def save(self, value):\n        return value\n', ''),
    lambda s: s + '\n    def __new__(cls):\n        return None\n',
    lambda s: s + '\n    def _migration(self):\n        self.execute("ALTER TABLE positions ADD COLUMN x TEXT")\n',
    lambda s: s + '\n    @property\n    def root(self):\n        return None\n',
    lambda s: s + '\n    def extra(self, value=print("side effect")):\n        return value\n',
    lambda s: s + '\n    def extra(self, value: print("annotation")):\n        return value\n',
    lambda s: s + '\n    def extra(self):\n        return self.execute("DROP TABLE positions")\n',
    lambda s: s + '\n    def extra(self, sql):\n        return self.execute(sql)\n',
    lambda s: s + '\n    def extra(self):\n        exec("dynamic operation")\n',
    lambda s: s + '\nprint("module initialization")\n',
    lambda s: 'not valid python (',
])
def test_unproven_store_changes_remain_manual(change):
    assert not v.compatible_store_extension(BASE, change(BASE))


def test_real_store_additions_and_short_temp_name_have_same_existing_semantics():
    source=(Path(__file__).resolve().parents[1]/'workbench/store.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Store')
    additions={'chart_document','save_chart_document','save_chart_series','load_chart_series',
               'premarket_sources','save_premarket_sources'}
    cls.body=[n for n in cls.body if not isinstance(n,ast.FunctionDef) or n.name not in additions]
    before=ast.unparse(tree).replace("path.with_name('.a-' + uuid.uuid4().hex + '.tmp')",
                                    "path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')")
    assert v.compatible_store_extension(before,source)
    assert not v.compatible_store_extension(before,source.replace('f.flush()', 'pass'))
    assert not v.compatible_store_extension(before,source.replace('os.fsync(f.fileno())', 'pass'))
    assert not v.compatible_store_extension(before,source.replace('with tmp.open("xb")', 'with tmp.open("wb")'))


@pytest.mark.parametrize('body', [
    "return str(value).strip()",
    "values = {value} if isinstance(value, str) else set(value)\n        return sorted(values)",
    "return self.execute('SELECT value FROM meta WHERE key=?', (value,))",
    "return self.execute('UPDATE meta SET value=? WHERE key=?', (value, 'preference'))",
    "return 'Create or drop a display filter'",
    "return 'Begin displaying history'",
])
def test_reviewed_business_methods_can_evolve_without_a_per_method_exception(body):
    for method_name in ('save', 'query_history', 'filter_reports'):
        before=BASE.replace('def save(',f'def {method_name}(')
        after=before.replace('return value',body)
        assert v.compatible_store_extension(before,after)


def test_optional_literal_business_arguments_do_not_require_manual_install():
    after=BASE.replace('def save(self, value):', 'def save(self, value, *, include_archived=False):')
    assert v.compatible_store_extension(BASE,after)


def test_pure_private_helpers_and_static_type_hints_are_normal_business_evolution():
    before='from __future__ import annotations\n'+BASE
    after=before.replace('def save(self, value):', 'def save(self, value: str, *, include_archived: bool = False) -> str:')
    after+='\n    def _summarize(self, values: list[str]) -> list[str]:\n        return sorted(values)\n'
    assert v.compatible_store_extension(before,after)


@pytest.mark.parametrize('body', [
    "self.root = Path('other')\n        return value",
    "self.path = 'other.sqlite3'\n        return value",
    "self.execute('ALTER TABLE positions ADD COLUMN x TEXT')\n        return value",
    "return self.execute(value)",
    "db = sqlite3.connect('another.db')\n        return value",
    "db.commit()\n        return value",
    "return getattr(self, value)()",
    "return eval(value)",
])
def test_business_changes_cannot_escape_storage_boundary(body):
    assert not v.compatible_store_extension(BASE,BASE.replace('return value',body))


def test_real_multi_model_history_evolution_is_compatible_but_not_transaction_or_write_changes():
    source=(Path(__file__).resolve().parents[1]/'workbench/store.py').read_text(encoding='utf-8')
    before=source.replace('        models = {execution_model} if isinstance(execution_model, str) else set(execution_model)\n','')
    before=before.replace("report.get('execution_model') not in models", "report.get('execution_model') != execution_model")
    assert before!=source
    assert v.compatible_store_extension(before,source)
    assert not v.compatible_store_extension(before,source.replace("db.execute('BEGIN IMMEDIATE')", "db.execute('BEGIN EXCLUSIVE')"))
    assert not v.compatible_store_extension(before,source.replace('db.commit()', 'pass'))
    assert not v.compatible_store_extension(before,source.replace('db.rollback()', 'pass'))


def store_target(repo, source):
    root,_,_=repo
    folder=root/'workbench'; folder.mkdir()
    (folder/'store.py').write_text(BASE,encoding='utf-8')
    git(root,'add','.'); git(root,'commit','-m','Store baseline')
    old=git(root,'rev-parse','HEAD')
    git(root,'switch','-c','store-release')
    (folder/'store.py').write_text(source,encoding='utf-8')
    git(root,'add','.'); git(root,'commit','-m','Store target')
    target=git(root,'rev-parse','HEAD')
    git(root,'switch','main'); git(root,'update-ref','refs/aplus-update/main',target)
    return root,old,target


@pytest.mark.parametrize('source', [BASE+ADDITION,
    BASE.replace('return value', "values = {value} if isinstance(value, str) else set(value)\n        return sorted(values)")])
def test_compatible_store_change_can_update_but_keeps_runtime_and_local_launcher(repo,source):
    root,old,target=store_target(repo,source)
    runtime=root/'runtime'; runtime.mkdir()
    sentinel=runtime/'personal.db'; sentinel.write_bytes(b'never opened or rewritten')
    launcher=root/'push.cmd'; launcher.write_text('kept personal launcher\n',encoding='utf-8')
    plan=v.check_version(root,force=True,api=api_for(target),fetch=Mock())
    assert plan['can_update'] and plan['ci_passed'] and not plan.get('block_reason')
    smoke=Mock()
    v.apply_update(root,plan,root/'.git/backup.zip','python',api=api_for(target),smoke=smoke)
    assert git(root,'rev-parse','HEAD')==target
    assert sentinel.read_bytes()==b'never opened or rewritten'
    assert launcher.read_text(encoding='utf-8')=='kept personal launcher\n'
    assert smoke.call_count==2


@pytest.mark.parametrize('source,conclusion', [
    (BASE.replace('self.version = 8','self.version = 9'),'success'),
    (BASE+ADDITION,'failure'),
])
def test_layout_change_or_ci_failure_still_blocks_before_install(repo,source,conclusion):
    root,old,target=store_target(repo,source)
    plan=v.check_version(root,force=True,api=api_for(target,conclusion),fetch=Mock())
    assert not plan['can_update'] and git(root,'rev-parse','HEAD')==old


def test_explicit_supported_schema_migration_is_planned_not_silently_executed(repo):
    root, _, _ = repo
    git(root, 'switch', 'main')
    folder = root/'workbench'; folder.mkdir()
    (folder/'store.py').write_text(BASE, encoding='utf-8')
    lock = b'example-package==1.0\n'
    (root/'requirements-lock.txt').write_bytes(lock)
    (root/v.RELEASE_INSTALLATION).write_text(
        json.dumps(installation_contract(lock)), encoding='utf-8')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'schema eight baseline')
    old = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', '-c', 'schema-nine-release')
    (folder/'store.py').write_text(BASE.replace('self.version = 8', 'self.version = 9'), encoding='utf-8')
    (root/v.RELEASE_INSTALLATION).write_text(
        json.dumps(installation_contract(
            lock, schema=9, supported=(8,), migration='store-v9')),
        encoding='utf-8')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'declared schema nine')
    target = git(root, 'rev-parse', 'HEAD')
    git(root, 'switch', 'main'); git(root, 'update-ref', 'refs/aplus-update/main', target)
    plan = v.check_version(root, force=True, api=api_for(target), fetch=Mock())
    assert git(root, 'rev-parse', 'HEAD') == old
    assert plan['can_update']
    assert plan['installation']['database_changed'] is True
    assert plan['installation']['source_schema'] == 8
    assert plan['installation']['target_schema'] == 9


def test_compatible_plan_rechecks_source_after_a_new_main_commit(repo):
    root,old,target=store_target(repo,BASE+ADDITION)
    plan=v.check_version(root,force=True,api=api_for(target),fetch=Mock())
    assert plan['can_update']
    git(root,'switch','store-release')
    (root/'workbench/store.py').write_text(BASE.replace('self.version = 8','self.version = 9'),encoding='utf-8')
    git(root,'add','.'); git(root,'commit','-m','new migration')
    newer=git(root,'rev-parse','HEAD'); git(root,'switch','main')
    with pytest.raises(v.UpdateError,match='已经变化'):
        v.apply_update(root,plan,root/'.git/backup.zip','python',api=api_for(newer),smoke=Mock())
    assert git(root,'rev-parse','HEAD')==old


def test_maintenance_checksum_rejects_changed_data_before_restart(tmp_path):
    path=tmp_path/'workbench.sqlite3'; path.write_bytes(b'original data')
    request=dict(runtime=str(tmp_path),data_checksum=v._digest(path))
    v.verify_maintenance_data(request)
    path.write_bytes(b'changed data')
    with pytest.raises(v.UpdateError,match='发生变化'):
        v.verify_maintenance_data(request)
    assert path.read_bytes()==b'changed data'  # Never roll back the user's DB.


def test_maintenance_bridge_backup_pins_runtime_and_waits_for_ready(store, tmp_path, monkeypatch):
    from tools import maintenance_update as m
    monkeypatch.setattr(m,'application_mutex',nullcontext)
    monkeypatch.setattr(m,'require_web_closed',lambda: None)
    monkeypatch.setattr(m,'check_version',lambda *_a,**_k: dict(can_update=True))
    folder=tmp_path/'receipt'; folder.mkdir(); (folder/'ready.json').write_text('{}')
    captured={}
    def prepare(*args,**kwargs):
        captured.update(kwargs); return folder
    monkeypatch.setattr(m,'prepare_worker',prepare)
    before=store.path.read_bytes()
    assert m.prepare(tmp_path/'code',store.root,'python')==folder
    assert captured['data_checksum']==v._digest(store.path)
    assert (folder/'original-workbench.sqlite3').read_bytes()==before
    assert store.path.read_bytes()==before


@pytest.mark.parametrize('reason',['active','journal','ci'])
def test_maintenance_bridge_blocks_unfinished_work_or_unaccepted_target(store,tmp_path,monkeypatch,reason):
    from tools import maintenance_update as m
    monkeypatch.setattr(m,'application_mutex',nullcontext)
    monkeypatch.setattr(m,'require_web_closed',lambda: None)
    monkeypatch.setattr(m,'check_version',lambda *_a,**_k: dict(can_update=False,message='CI not accepted'))
    worker=Mock(); monkeypatch.setattr(m,'prepare_worker',worker)
    if reason=='active':
        store.execute("INSERT INTO jobs(id,kind,status,created,spec) VALUES('active','scan','running','now','{}')")
    elif reason=='journal':
        (store.root/'workbench.sqlite3-journal').write_bytes(b'transaction marker')
    before=store.path.read_bytes()
    with pytest.raises(v.UpdateError): m.prepare(tmp_path/'code',store.root,'python')
    worker.assert_not_called()
    assert store.path.read_bytes()==before
