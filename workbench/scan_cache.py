"""Calculation identities separate from archived release-version identities."""
import ast
import inspect
import json
from functools import lru_cache
from copy import deepcopy
from pathlib import Path

from .store import ROOT, digest, dumps


def file_state(paths):
    """Cheap input identity; missing inputs remain visible to the real reader."""
    result = []
    for path in sorted({Path(p).absolute() for p in paths}):
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            result.append((str(path), None, None))
    return tuple(result)


@lru_cache(maxsize=16)
def _protected_paths(manifest_state):
    path = Path(manifest_state[0][0])
    manifest = json.loads(path.read_text(encoding='utf-8'))
    return tuple(path.parent / row['path'] for row in manifest['files'])


def engine_inputs(root=ROOT):
    root = Path(root).resolve()
    manifest = root / 'frozen_manifest.json'
    return file_state((manifest, *_protected_paths(file_state((manifest,))),
                       *(root / 'workbench' / name for name in
                         ('strategies.py', 'market.py', 'h2_plan.py', 'prices.py'))))


def store_inputs(store):
    """Existing Store commits invalidate readers, including WAL-mode writers."""
    path = Path(store.path)
    return file_state((path, Path(str(path) + '-wal')))


@lru_cache(maxsize=16)
def _engine_version(inputs, include_frozen, function, semantics, verifier):
    return function(include_frozen=include_frozen)


def scan_engine_version(include_frozen=True):
    from . import strategies as adapters
    return _engine_version(engine_inputs(adapters.ROOT), include_frozen,
                           adapters.scan_engine_version, adapters._scan_engine_semantics,
                           adapters.verify_frozen)


_latest_cache = {}


def latest_datasets(store, source='baostock'):
    from .market import latest_datasets as read
    key = (store_inputs(store), source)
    cached = _latest_cache.get(store.root)
    if cached is None or cached[0] != key:
        rows = read(store, source)
        if key == (store_inputs(store), source):
            if len(_latest_cache) >= 16:
                _latest_cache.clear()
            _latest_cache[store.root] = (key, deepcopy(rows))
        return rows
    return deepcopy(cached[1])


# Exact audited PR45 addition. It is still executed for every calculation;
# this string only proves an old engine's unchanged behavior for native
# detectors that do NOT opt into signal-quality validation. Any gate edit
# disables the bridge; MTR and plugins never receive the old-engine alias.
PR45_QUALITY_GATE = '''    quality_col = meta.get('signal_close_position_column')
    quality_min = _number(meta.get('signal_close_position_min'))
    if quality_col and quality_min is not None:
        if quality_col not in result:
            raise ValueError(f'策略没有返回声明的信号质量列 {quality_col}')
        signal_rows = result[result[col].fillna(False).astype(bool)]
        quality = pd.to_numeric(signal_rows[quality_col], errors='coerce')
        if quality.isna().any() or (quality < quality_min).any():
            raise ValueError('MTR 正式候选缺少合格光头阳线证明，已停止记录该股票')
'''


def compatible_engines(strategies):
    """Read/promote proven pre-PR45 per-detector answers, never old MTR ones.

    All other current semantic functions/files stay in the byte recipe. A
    shared change elsewhere cannot accidentally match the historical key.
    The new key/report always uses the full current engine fingerprint.
    """
    from . import strategies as adapters
    source = inspect.getsource(adapters.calculate_prepared)
    if source.count(PR45_QUALITY_GATE) != 1:
        return {}
    previous = digest(adapters._scan_engine_semantics(source.replace(PR45_QUALITY_GATE,'')))
    result = {}
    for strategy in strategies:
        try:
            path = Path(strategy.file).resolve().relative_to(ROOT).as_posix()
            meta = strategy.cls.get_metadata()
            if path.startswith('core/strategies/') and not (
                    meta.get('signal_close_position_column') or meta.get('signal_close_position_min') is not None):
                result[strategy.id] = previous
        except (ValueError,TypeError,AttributeError):
            continue  # Unknown/dynamic metadata cannot prove compatibility.
    return result


def _calculation_versions(strategies, root=ROOT):
    """Bind each native detector to its own files and conservative shared deps.

    Frozen validation still protects the whole release. This only prevents an
    MTR-only revision from invalidating the other detectors' finished work.
    Unknown/dynamic native imports fall back to the full release fingerprint.
    Registered plugins also retain their original conservative version key.
    """
    root = Path(root).resolve()
    manifest = json.loads((root/'frozen_manifest.json').read_text(encoding='utf-8'))
    files = {r['path']: r['sha256'] for r in manifest['files']}
    common = {p for p in files if not p.startswith('core/strategies/')
              or p == 'core/strategies/__init__.py'}

    def closure(path, seen):
        if path in seen:
            return
        if path not in files:
            raise ValueError('unresolved protected dependency')
        seen.add(path)
        content = (root/path).read_bytes()
        if digest(content) != files[path]:
            raise ValueError('protected dependency changed')
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id == '__import__'
                    or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'):
                raise ValueError('dynamic dependency')
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name.replace('.', '/') for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = list(Path(path).parent.parts)
                    if node.level > len(base):
                        raise ValueError('unknown relative import')
                    base = base[:len(base)-node.level+1]
                    module = '/'.join(base + (node.module or '').split('.')).rstrip('/')
                else:
                    module = (node.module or '').replace('.', '/')
                modules = [module]
                if module == 'core/strategies':
                    modules = [module+'/'+a.name for a in node.names]
            for module in modules:
                if module.startswith('core/strategies/'):
                    closure(module+'.py', seen)

    result = {}
    for strategy in strategies:
        try:
            path = Path(strategy.file).resolve().relative_to(root).as_posix()
            if not path.startswith('core/strategies/'):
                raise ValueError('plugin or unknown detector')
            selected = set(common)
            closure(path, selected)
            result[strategy.id] = digest(dumps(
                [strategy.id, strategy.params, [(p,files[p]) for p in sorted(selected)]]).encode())
        except (OSError, ValueError, SyntaxError, KeyError):
            result[strategy.id] = strategy.version
    return result



_versions_cache = {}


def calculation_versions(strategies, root=ROOT):
    strategies = list(strategies)
    key = (engine_inputs(root), dumps([(s.id, s.file, s.params, s.version) for s in strategies]))
    if key not in _versions_cache:
        result = _calculation_versions(strategies, root)
        if key[0] == engine_inputs(root):
            if len(_versions_cache) >= 32:
                _versions_cache.clear()
            _versions_cache[key] = result
        return result.copy()
    return _versions_cache[key].copy()


def cache_key(did, strategy, version, timeframe, asof, engine):
    return digest(dumps([did,strategy,version,timeframe,asof,engine]).encode())


def cached_answers(store, keys):
    keys = list(dict.fromkeys(keys))
    result = {}
    for offset in range(0,len(keys),500):
        batch = keys[offset:offset+500]
        result.update((r['key'],r['signal']) for r in store.rows(
            'SELECT key,signal FROM scan_cache WHERE key IN ('+','.join('?' for _ in batch)+')',batch))
    return result


def finished_scan(store, checkpoint, timeframe, keys):
    """A completed receipt AND intact answers/observations, never receipt alone."""
    previous = None
    for row in store.rows("SELECT result FROM jobs WHERE kind='scan' AND status IN ('completed','partial') "
                          "ORDER BY created DESC,rowid DESC LIMIT 32"):
        try:
            candidate = json.loads(row['result'] or '{}').get('checkpoints',{}).get(timeframe)
            if candidate and candidate.get('identity') == checkpoint:
                previous = candidate
                break
        except (ValueError,TypeError,AttributeError):
            continue
    if previous is None:
        return None
    answers = cached_answers(store, keys)
    if len(answers) != len(keys):
        return None
    try:
        signals = sum(json.loads(value) is not None for value in answers.values())
    except (ValueError,TypeError):
        return None
    ids = list(dict.fromkeys(previous.get('observation_ids', [])))
    if signals != len(ids):
        return None
    found = 0
    for offset in range(0,len(ids),500):
        batch = ids[offset:offset+500]
        found += len(store.rows('SELECT id FROM observations WHERE id IN ('+
                                ','.join('?' for _ in batch)+')',batch))
    return previous if found == len(ids) else None


def filter_candidate_revisions(store, rows, source, timeframe):
    """A successful current no-signal judgment supersedes an archived old hit.

    UI-only reads never authorize a scan or delete history. When there is no
    matching current judgment, retain the latest saved historical observation.
    """
    from .sources import SOURCES
    if not rows or source not in SOURCES:
        return rows
    from gui.data import catalog
    entries = catalog(store)
    selected = [entries[k] for k in {r['strategy'] for r in rows} if k in entries]
    versions = calculation_versions(selected)
    compatible = compatible_engines(selected)
    engine = scan_engine_version(include_frozen=False)
    latest = {r['code']:r['id'] for r in latest_datasets(store,source) if r['timeframe']=='daily'}
    lookups = []
    for row in rows:
        key = row['strategy']
        ids = list(dict.fromkeys([latest.get(row['code'],row['dataset_id']),row['dataset_id']]))
        engines = [engine] + ([compatible[key]] if key in compatible else [])
        lookups.append([(cache_key(did,key,versions[key],timeframe,str(row['asof'])[:10],stamp),did)
                        for did in ids for stamp in engines] if key in versions else [])
    answers = cached_answers(store, (key for group in lookups for key,_ in group))
    result = []
    for row, keys in zip(rows,lookups):
        matched = next(((key,did) for key,did in keys if key in answers), None)
        if matched is not None:
            try:
                answer = json.loads(answers[matched[0]])
                archived = json.loads(row['payload'])
            except (ValueError,TypeError):
                # Broken judgment evidence cannot supersede saved history.
                result.append(row)
                continue
            if answer is None:
                continue
            # Prefer new snapshot/current release observations whenever their
            # atomic cache+observation checkpoint exists, not stale revisions.
            preferred = latest.get(row['code'],row['dataset_id'])
            if matched[1] == preferred and row['dataset_id'] != preferred:
                continue
            # Release versions cover the whole frozen manifest. An unrelated
            # MTR upgrade must not hide a still-valid 3K hit before the user
            # scans again. Match the proven judgment, not its release label.
            if archived != answer:
                continue
        result.append(row)
    return result
