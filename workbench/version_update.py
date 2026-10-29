"""Explicit, conservative code updates. Never opens the user's data store.

The copied worker uses only the standard library so changing application files
cannot change the updater halfway through an update.
"""
from __future__ import annotations

import argparse
import ast
import copy
import contextlib
import ctypes
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
import uuid
import zipfile


REPOSITORY = "sysujonathan/Aplus"
REMOTE = f"https://github.com/{REPOSITORY}.git"
API = f"https://api.github.com/repos/{REPOSITORY}"
SHA = re.compile(r"^[0-9a-f]{40}$")
DATA_SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROTECTED = {"runtime", "data", ".git", ".venv", "venv", ".workbuddy", "upgrade-backups"}
MANUAL_FILES = {"setup.cmd"}
# Public fixture reviewed with the release. This is an exact path AND byte
# fingerprint exception, never a general certificate/private-key exemption.
PUBLIC_TEST_CERTIFICATES = {
    "tests/fixtures/tls/emsign-ecc-root-g3.pem":
        "36e68e205b53c67c7a013894e0d5c8583063468118d1ce78ecbc2200d1dd185c",
}
DEPENDENCY_FILES = {"requirements.txt", "requirements-lock.txt"}
RELEASE_INSTALLATION = "release_installation.json"
INSTALLATION_PROTOCOL = 1
ENVIRONMENT_ID = re.compile(r"^py\d{3}-[0-9a-f]{12}(?:-[0-9a-f]{12})?$")
COUNTED_TABLES = ("accounts", "executions", "positions", "position_fills", "closed_trades",
                  "cash_flows", "datasets", "observations", "plans", "watchlist")
PROTECTED_FACT_TABLES = ("accounts", "executions", "positions", "position_fills",
                         "closed_trades", "cash_flows", "datasets", "observations",
                         "plans", "watchlist")
ENVIRONMENT_MARKER = ".aplus-environment.json"
ACTIVE_TRANSACTION = "active-request.txt"
TRANSACTION_FILE = "transaction.json"
TERMINAL_PHASES = {"completed", "rolled_back"}
RECOVERY_BLOCKED = 20
PRESERVED_LOCAL_FILES = {"push.cmd"}
MANAGED_RELEASE_BRANCH = re.compile(r"^codex/local-release-\d{8}$")
DISCARDABLE_RELEASE_PATHS = {"README.md", "release_catalog.json"}

# Official main plus its exact successful acceptance is the release authority.
# Do not duplicate human release review in a client-side strategy allowlist.


class UpdateError(ValueError):
    pass


class ManualUpdateError(UpdateError):
    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


def require_web_closed(port=8517):
    """Conservatively reject the normal local Web entry before replacing code."""
    try:
        # Windows may take ~2s to report WSAECONNREFUSED on a closed loopback
        # port. A subsecond timeout mistakes this for an uncertain live service.
        # Only an explicit refusal allows updating; timeout/other errors reject.
        connection = socket.create_connection(('127.0.0.1', port), timeout=3)
    except ConnectionRefusedError:
        return
    except OSError:
        raise UpdateError('无法确认本地Web工作台是否退出，请关闭后再更新。') from None
    connection.close()
    raise UpdateError('本地Web工作台端口仍在使用，请先退出Web服务再更新代码。')


def shanghai_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def run_git(root, *args, timeout=35, binary=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=", "-C", str(root), *args],
        capture_output=True, timeout=timeout, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        # Do not expose repository credentials or private paths from git stderr.
        raise UpdateError("Git检查或更新失败；未强行覆盖，请检查网络及本地仓库。")
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def metadata_dir(root):
    root = Path(root).resolve()
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise UpdateError("此安装不是普通Git检出；请人工更新，数据不会被移动。")
    return root / ".git" / "aplus-update"


def _changed_paths(root):
    paths = set()
    commands = (("diff", "--name-only", "-z"),
                ("diff", "--cached", "--name-only", "-z"),
                ("ls-files", "--others", "--exclude-standard", "-z"))
    for args in commands:
        raw = run_git(root, "-c", "core.quotepath=false", *args, binary=True)
        paths.update(part.decode("utf-8", "surrogateescape")
                     for part in raw.split(b"\0") if part)
    return paths


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def local_version(root):
    root = Path(root).resolve()
    metadata_dir(root)
    if Path(run_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise UpdateError("代码目录与Git根目录不一致，请人工检查。")
    remote = run_git(root, "remote", "get-url", "origin")
    if remote not in {REMOTE, REMOTE[:-4], f"git@github.com:{REPOSITORY}.git"}:
        raise UpdateError("仓库不是约定的Aplus来源；不会自动更新。")
    changes = _changed_paths(root)
    preserved = {}
    unsafe = []
    for name in sorted(changes):
        path = root / name
        if (name in PRESERVED_LOCAL_FILES and path.is_file() and not path.is_symlink()
                and not (hasattr(path, "is_junction") and path.is_junction())):
            preserved[name] = _digest(path)
        else:
            unsafe.append(name)
    return {
        "head": run_git(root, "rev-parse", "HEAD"),
        "branch": run_git(root, "branch", "--show-current") or "游离版本",
        "dirty": bool(unsafe),
        "changes": unsafe,
        "preserved": preserved,
    }


def github_json(path):
    req = Request(API + path, headers={"Accept": "application/vnd.github+json",
                                      "User-Agent": "Aplus-version-check"})
    with urlopen(req, timeout=12) as response:
        return json.load(response)


def acceptance_status(target, api=github_json):
    payload = api(f"/actions/runs?head_sha={target}&branch=main&event=push&per_page=30")
    runs = [r for r in payload.get("workflow_runs", [])
            if r.get("head_sha") == target and r.get("head_branch") == "main"
            and r.get("event") == "push" and r.get("name") == "A acceptance"
            and r.get("path") == ".github/workflows/checks.yml"]
    if not runs:
        return 'missing'
    latest = max(runs, key=lambda r: (int(r.get("id", 0)), int(r.get("run_attempt", 0))))
    if latest.get("status") != "completed":
        return 'running'
    return 'passed' if latest.get("conclusion") == "success" else 'failed'


def acceptance(target, api=github_json):
    return acceptance_status(target, api) == 'passed'


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _discardable_release_path(name):
    return (name in DISCARDABLE_RELEASE_PATHS
            or name.startswith("docs/") or name.startswith("tests/"))


def managed_release_absorbed(root, local, target):
    """True only when main already contains all runtime code from our managed build.

    A local release commit may have different merge ancestry even after the same
    reviewed changes reached main.  We virtually merge it into target and allow
    migration only when the result differs from target solely in documentation,
    tests or display-only release metadata.  No working-tree file is changed.
    """
    if not MANAGED_RELEASE_BRANCH.fullmatch(local.get("branch", "")):
        return False
    current = local.get("head", "")
    if not SHA.fullmatch(current) or not SHA.fullmatch(target):
        return False
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    process = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=", "-C", str(root),
         "merge-tree", "--write-tree", target, current],
        capture_output=True, timeout=35, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    lines = process.stdout.decode("utf-8", "replace").splitlines()
    tree = lines[0].strip() if lines else ""
    if process.returncode not in {0, 1} or not SHA.fullmatch(tree):
        return False
    changed = set(run_git(root, "-c", "core.quotepath=false", "diff", "--name-only",
                          f"{target}^{{tree}}", tree).splitlines())
    return all(_discardable_release_path(name) for name in changed)


def _main_can_fast_forward(root, target):
    try:
        main = run_git(root, "rev-parse", "refs/heads/main")
        run_git(root, "merge-base", "--is-ancestor", main, target)
    except UpdateError:
        raise UpdateError("本机main不能安全前进到正式版，请维护者核对。") from None
    worktrees = run_git(root, "worktree", "list", "--porcelain").splitlines()
    current_path = None
    for line in worktrees:
        if line.startswith("worktree "):
            current_path = Path(line[9:]).resolve()
        elif line == "branch refs/heads/main" and current_path != Path(root).resolve():
            raise UpdateError("本机main正被其他工作目录使用，请维护者核对。")


def compatible_store_extension(before, after):
    """Check storage boundaries, not byte identity of reviewed business logic.

    Never import either revision or open a database. Keep initialization, data
    location, schema and transaction/artifact infrastructure unchanged. Ordinary
    business methods may evolve within that boundary on accepted official main.
    """
    def clean(node):
        node = copy.deepcopy(node)
        for item in ast.walk(node):
            if isinstance(item, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.body and isinstance(item.body[0], ast.Expr) and isinstance(item.body[0].value, ast.Constant) and isinstance(item.body[0].value.value, str):
                    item.body.pop(0)
        return node

    def dump(node):
        return ast.dump(clean(node), include_attributes=False)

    def split(source):
        tree = ast.parse(source)
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Store']
        if len(classes) != 1:
            raise ValueError('Store definition missing or ambiguous')
        cls = classes[0]
        methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if len({n.name for n in methods}) != len(methods) or '__init__' not in {n.name for n in methods}:
            raise ValueError('Store methods missing or ambiguous')
        # Retain bases, decorators, class attributes and all top-level code.
        skeleton = copy.deepcopy(tree)
        store_node = next(n for n in skeleton.body if isinstance(n, ast.ClassDef) and n.name == 'Store')
        store_node.body = [n for n in store_node.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return dump(skeleton), {n.name: n for n in methods}

    old_temp = ast.parse('tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")').body[0]
    new_temp = ast.parse('tmp = path.with_name(".a-" + uuid.uuid4().hex + ".tmp")').body[0]

    def writer(node):
        node = copy.deepcopy(node)
        for index, statement in enumerate(node.body):
            if dump(statement) in (dump(old_temp), dump(new_temp)):
                node.body[index] = copy.deepcopy(new_temp)
        return dump(node)

    infrastructure = {'__init__', 'connect', 'atomic_write', 'rows', 'execute',
                      'event', 'write_artifact', '_write_artifact', 'read_artifact'}
    annotations_deferred = False

    def business_method_safe(method, previous=None):
        # Existing definition-time behavior must not gain execution. Optional
        # literal arguments may be added without freezing the whole API shape.
        annotations = [method.returns, *(arg.annotation for arg in
            (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs,
             *([method.args.vararg] if method.args.vararg else []),
             *([method.args.kwarg] if method.args.kwarg else [])))]
        annotation_nodes = (ast.Name, ast.Load, ast.Attribute, ast.Subscript,
                            ast.Tuple, ast.List, ast.Constant, ast.BinOp, ast.BitOr, ast.Slice)
        if method.decorator_list or any(
                not isinstance(part, annotation_nodes)
                for annotation in annotations if annotation is not None
                for part in ast.walk(annotation)):
            return False
        if any(annotation is not None for annotation in annotations) and not annotations_deferred:
            return False  # Only postponed hints avoid definition-time type evaluation.
        for default in (*method.args.defaults, *(v for v in method.args.kw_defaults if v is not None)):
            try:
                ast.literal_eval(default)
            except (ValueError, TypeError):
                return False
        def transaction_sql(node):
            return [part.args[0].value for part in ast.walk(node)
                    if isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute)
                    and part.func.attr in {'execute', 'executemany', 'executescript'}
                    and part.args and isinstance(part.args[0], ast.Constant)
                    and isinstance(part.args[0].value, str)
                    and re.match(r'^\s*(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|END)\b', part.args[0].value, re.I)]
        if previous is not None and transaction_sql(method) != transaction_sql(previous):
            return False
        for node in ast.walk(method):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
                targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
                if any(isinstance(part, ast.Attribute) and part.attr in
                       {'root', 'path', '_files_lock', '_atomic_local'}
                       for target in targets for part in ast.walk(target)):
                    return False
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in {
                        'exec', 'eval', 'compile', 'setattr', 'delattr', '__import__', 'getattr'}:
                    return False
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in {'connect', 'commit', 'rollback', 'close'}:
                        if node.func.attr != 'connect' or not isinstance(node.func.value, ast.Name) or node.func.value.id != 'self':
                            return False  # Transactions belong to unchanged infrastructure.
                    if node.func.attr in {'execute', 'executemany', 'executescript', 'rows'}:
                        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                            return False
                        if re.search(r'\b(CREATE|ALTER|DROP|ATTACH|DETACH|VACUUM)\b|\bPRAGMA\b|schema_version',
                                     node.args[0].value, re.I):
                            return False
        return True

    try:
        old_structure, old = split(before)
        new_structure, new = split(after)
        annotations_deferred = any(isinstance(node, ast.ImportFrom) and node.module == '__future__'
            and any(alias.name == 'annotations' for alias in node.names)
            for node in ast.parse(after).body)
        if old_structure != new_structure or not old.keys() <= new.keys():
            return False
        for name, method in old.items():
            if name in infrastructure:
                normalize = writer if name == '_write_artifact' else dump
                if normalize(method) != normalize(new[name]):
                    return False
            elif dump(method) != dump(new[name]) and not business_method_safe(new[name], method):
                return False
        for name in new.keys() - old.keys():
            if name.startswith('__') or name in infrastructure:
                return False  # No new lifecycle/dispatch hooks.
            if not business_method_safe(new[name]):
                return False
        return True
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return False


def release_manifest_valid(root, target):
    """Check target package integrity, not permission for a strategy change.

    Official-main identity and exact acceptance are checked by check_version
    and validate_plan. Allow skipped versions, any reviewed strategy/config
    changes and an evolved manifest; never execute candidate code here.
    """
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate manifest key')
            result[key] = value
        return result

    try:
        manifest = json.loads(run_git(root, 'show', f'{target}:frozen_manifest.json',
                                      binary=True), object_pairs_hook=unique_keys)
        if not isinstance(manifest, dict):
            raise ValueError('invalid manifest')
        entries = manifest['files']
        if not isinstance(entries, list) or not entries:
            raise ValueError('invalid manifest records')
        files = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
                raise ValueError('invalid file record')
            path, checksum = entry['path'], entry['sha256']
            if (not isinstance(path, str) or not path.startswith(('core/', 'config/'))
                    or '\\' in path or ':' in path or any(p in {'', '.', '..'} for p in path.split('/'))
                    or path in files or not isinstance(checksum, str)
                    or not DATA_SHA256.fullmatch(checksum)):
                raise ValueError('invalid protected path or fingerprint')
            files[path] = checksum
        # Inspect the target tree, not the installed checkout or only changed
        # paths. A valid checksum list must not silently omit protected files.
        tree = run_git(root, 'ls-tree', '-r', '-z', '--full-tree', target,
                       '--', 'core', 'config', binary=True)
        protected = set()
        for record in tree.split(b'\0'):
            if not record:
                continue
            metadata, raw_path = record.split(b'\t', 1)
            mode, kind, _oid = metadata.split()
            if kind != b'blob' or mode not in {b'100644', b'100755'}:
                return False  # No symlinks or embedded repositories in frozen code.
            protected.add(raw_path.decode('utf-8', 'surrogateescape'))
        if set(files) != protected:
            return False
        for path, expected in files.items():
            blob = run_git(root, 'show', f'{target}:{path}', binary=True)
            if hashlib.sha256(blob).hexdigest() != expected:
                return False
        return True
    except (UpdateError, KeyError, TypeError, ValueError, RecursionError):
        return False


def _revision_bytes(root, revision, name):
    try:
        return run_git(root, "show", f"{revision}:{name}", binary=True)
    except UpdateError:
        return None


def _release_environment_id(python_version, requirements_hash, hashes_hash):
    base = f"py{python_version.replace('.', '')}-{requirements_hash[:12]}"
    return base + (f"-{hashes_hash[:12]}" if hashes_hash else "")


def release_installation(root, revision, required=False):
    """Read the release transaction contract without importing target code."""
    raw = _revision_bytes(root, revision, RELEASE_INSTALLATION)
    if raw is None:
        if required:
            raise ManualUpdateError("正式版缺少自动迁移清单，请维护者补齐后再更新。", "installation")
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
        if set(value) != {"protocol", "python", "environment_id", "requirements_sha256",
                          "requirements_hashes_sha256", "database"}:
            raise ValueError
        if value["protocol"] != INSTALLATION_PROTOCOL:
            raise ValueError
        python_version = value["python"]
        if not re.fullmatch(r"\d+\.\d+", python_version):
            raise ValueError
        requirements_hash = value["requirements_sha256"]
        lock = _revision_bytes(root, revision, "requirements-lock.txt")
        if lock is None or not DATA_SHA256.fullmatch(requirements_hash):
            raise ValueError
        if hashlib.sha256(lock).hexdigest() != requirements_hash:
            raise ValueError
        hashes_hash = value["requirements_hashes_sha256"]
        if hashes_hash is not None:
            hashes = _revision_bytes(root, revision, "requirements-hashes.txt")
            if (hashes is None or not isinstance(hashes_hash, str)
                    or not DATA_SHA256.fullmatch(hashes_hash)
                    or hashlib.sha256(hashes).hexdigest() != hashes_hash):
                raise ValueError
        expected_environment = _release_environment_id(python_version, requirements_hash, hashes_hash)
        if value["environment_id"] != expected_environment or not ENVIRONMENT_ID.fullmatch(expected_environment):
            raise ValueError
        database = value["database"]
        if set(database) != {"schema", "supported_from", "migration"}:
            raise ValueError
        schema = database["schema"]
        supported = database["supported_from"]
        if (not isinstance(schema, int) or schema < 1
                or not isinstance(supported, list) or not supported
                or any(not isinstance(item, int) or item < 1 for item in supported)
                or len(set(supported)) != len(supported)
                or not isinstance(database["migration"], str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", database["migration"])):
            raise ValueError
        return copy.deepcopy(value)
    except (UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise ManualUpdateError("正式版自动迁移清单无效，未安装或打开正式数据。", "installation") from None


def installation_plan(root, current, target, changed, store_compatible=True):
    """Return a deterministic, re-checkable environment/database transaction."""
    dependency_changed = bool(changed & DEPENDENCY_FILES)
    target_contract = release_installation(root, target, required=False)
    source_contract = release_installation(root, current, required=False)
    if target_contract is None:
        if dependency_changed:
            raise ManualUpdateError("新版依赖变化但缺少自动安装清单，请维护者补齐。", "dependencies")
        if store_compatible is False:
            raise ManualUpdateError("新版数据结构变化但缺少自动迁移清单，正式数据未打开。", "data")
        return {"protocol": INSTALLATION_PROTOCOL, "environment_changed": False,
                "database_changed": False}
    environment_changed = (source_contract is None and (
        dependency_changed or target_contract["requirements_hashes_sha256"] is not None)) or (
        source_contract is not None and any(source_contract[key] != target_contract[key]
            for key in ("python", "environment_id", "requirements_sha256", "requirements_hashes_sha256"))
    )
    running_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if target_contract["python"] != running_python:
        raise ManualUpdateError(
            f"正式版需要 Python {target_contract['python']}；当前更新助手只使用已验收的 {running_python} 运行时。",
            "python",
        )
    if (dependency_changed or environment_changed) and target_contract["requirements_hashes_sha256"] is None:
        raise ManualUpdateError(
            "正式版环境变化但缺少逐包哈希锁定清单，旧环境保持可用。", "dependencies"
        )
    source_schema = (source_contract or {}).get("database", {}).get("schema")
    target_schema = target_contract["database"]["schema"]
    if source_schema is not None and target_schema < source_schema:
        raise ManualUpdateError("正式版未允许数据库降级，已保留旧版本。", "data")
    database_changed = store_compatible is False or (source_schema is not None and source_schema != target_schema)
    if database_changed:
        if source_schema is None or source_schema not in target_contract["database"]["supported_from"]:
            raise ManualUpdateError("正式版未声明支持当前数据库版本，未打开或改写正式数据。", "data")
    return {
        "protocol": INSTALLATION_PROTOCOL,
        "python": target_contract["python"],
        "environment_id": target_contract["environment_id"],
        "requirements_sha256": target_contract["requirements_sha256"],
        "requirements_hashes_sha256": target_contract["requirements_hashes_sha256"],
        "environment_changed": environment_changed,
        "database_changed": database_changed,
        "source_schema": source_schema,
        "target_schema": target_contract["database"]["schema"],
        "migration": target_contract["database"]["migration"],
    }


def _approved_public_test_certificate(root, target, name):
    expected = PUBLIC_TEST_CERTIFICATES.get(name)
    if expected is None:
        return False
    blob = run_git(root, "show", f"{target}:{name}", binary=True)
    return hashlib.sha256(blob).hexdigest() == expected


def compatible_target(root, current, target, local=None, details=None):
    if not SHA.fullmatch(current) or not SHA.fullmatch(target):
        raise UpdateError("版本编号无效，未更新。")
    # A fast forward cannot discard local commits or move back to an old main.
    managed = bool(local and managed_release_absorbed(root, local, target))
    if not managed:
        run_git(root, "merge-base", "--is-ancestor", current, target)
    else:
        _main_can_fast_forward(root, target)
    # Git quotes non-ASCII names by default; quoted "core/..." would evade the
    # prefix guard. Read NUL-delimited actual paths, including Unicode/newlines.
    raw_paths = run_git(root, 'diff', '--name-only', '-z', current, target, binary=True)
    changed = {p.decode('utf-8', 'surrogateescape') for p in raw_paths.split(b'\0') if p}
    if changed & MANUAL_FILES:
        raise ManualUpdateError("新版需要调整Python运行依赖或安装环境，请人工安装。", 'dependencies')
    if ('frozen_manifest.json' in changed or any(p.startswith(('core/', 'config/')) for p in changed)):
        if not release_manifest_valid(root, target):
            raise ManualUpdateError("正式版文件与校验清单不一致，请等待项目负责人修复发布。", 'release_integrity')
    store_compatible = True
    if 'workbench/store.py' in changed:
        try:
            before = run_git(root, 'show', f'{current}:workbench/store.py')
            after = run_git(root, 'show', f'{target}:workbench/store.py')
        except UpdateError:
            raise ManualUpdateError('无法核对数据保存程序的兼容性，请人工安装。', 'data') from None
        store_compatible = compatible_store_extension(before, after)
    transaction = installation_plan(root, current, target, changed, store_compatible)
    files = run_git(root, "-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", target).splitlines()
    if local and any(name not in files for name in local.get("preserved", {})):
        raise UpdateError("新版不再包含本机受保护启动文件，请维护者核对。")
    for name in files:
        p = PurePosixPath(name)
        if (p.parts[0].lower() in PROTECTED
                or name.lower().endswith((".db", ".sqlite3", ".key"))
                or (name.lower().endswith(".pem") and not _approved_public_test_certificate(root, target, name))
                or p.name.startswith(".env")):
            raise UpdateError("新版包含受保护路径，停止自动更新。")
        dest = Path(root) / name
        for parent in (dest, *dest.parents):
            if parent == Path(root).resolve():
                break
            if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
                raise UpdateError("本地代码路径包含链接，请人工更新。")
    # Reject symlinks before archive extraction or a git checkout can redirect writes.
    tree = run_git(root, "ls-tree", "-r", target)
    if any(line.startswith("120000 ") or line.startswith("160000 ") for line in tree.splitlines()):
        raise UpdateError("新版包含链接或子模块，请人工更新。")
    # Compare tracked paths, not diff's A/R/C labels: a rename/copy destination
    # is also newly tracked and Git can overwrite an ignored local file there.
    tracked = set(run_git(root, "-c", "core.quotepath=false", "ls-tree", "-r",
                          "--name-only", current).splitlines())
    for name in files:
        if name not in tracked and (Path(root) / name).exists():
            raise UpdateError("新版文件与本地保留文件重名，未覆盖。")
    if details is not None:
        details["installation"] = transaction
    return managed


def check_version(root, force=False, api=github_json, fetch=None):
    """Automatic checks are at most once per Shanghai calendar day, including failures.

    Cache lives in git metadata, not in the trading database. Every cache hit
    rechecks local identity; a local edit never inherits an old safe-update flag.
    """
    # UI metadata must not make the copied update worker depend on live code.
    from workbench.releases import identity
    local = local_version(root)
    def decorate(result):
        result["local_release"] = identity(root, local)
        result["target_release"] = identity(root, target=result.get("target", ""),
            git_reader=lambda sha, name: run_git(root, "show", f"{sha}:{name}")) if result.get("target") else {}
        return result
    cache = metadata_dir(root) / "check.json"
    day = shanghai_day()
    previous = {}
    try:
        previous = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not force and previous.get("day") == day:
        result = dict(previous, local=local)
        result["can_update"] = False  # Requires a fresh explicit check/CI verification.
        result["block_reason"] = "cached"
        if result.get("target") == local["head"]:
            result.update(state="current", message="已是主线当前提交。" if not local["dirty"] else
                          "提交号与主线相同，但本地有修改；不能按提交号认定代码一致。")
        return decorate(result)
    result = {"day": day, "local": local, "state": "unavailable", "can_update": False,
              "message": "版本检查未完成，不影响行情更新。"}
    # Persist the attempt before networking so a failed/offline check won't loop.
    write_json(cache, result)
    try:
        target = api("/branches/main")["commit"]["sha"]
        if not SHA.fullmatch(target):
            raise UpdateError("GitHub版本编号无效。")
        result["target"] = target
        # Read-only fetch: identify the candidate while CI is running, without
        # changing application files or granting installation permission.
        if fetch is None:
            run_git(root, "fetch", "--no-tags", "--no-recurse-submodules", REMOTE,
                    "main:refs/aplus-update/main")
        else:
            fetch(target)
        if run_git(root, "rev-parse", "refs/aplus-update/main") != target:
            raise UpdateError("主线在检查中发生变化，请重新检查。")
        result['ci_status'] = acceptance_status(target, api)
        passed = result['ci_status'] == 'passed'
        result["ci_passed"] = passed
        if target == local["head"]:
            result.update(state="current", message="已是主线当前版本。")
        elif not passed:
            result.update(state="pending", message="主线有变化，但新版验收未通过或尚未完成；暂不更新。")
        else:
            managed = False
            try:
                run_git(root, "merge-base", "--is-ancestor", target, local['head'])
            except UpdateError:
                try:
                    run_git(root, "merge-base", "--is-ancestor", local['head'], target)
                except UpdateError:
                    managed = managed_release_absorbed(root, local, target)
                    if not managed:
                        result.update(state="diverged", message="本机与正式版各有未吸收的代码，请维护者核对。")
                        write_json(cache, result)
                        return decorate(result)
            else:
                result.update(state="ahead", message="本地包含尚未进入主线的提交；不会退回较旧主线。")
                write_json(cache, result)
                return decorate(result)
            if local["branch"] != "main" and not managed:
                managed = managed_release_absorbed(root, local, target)
            result.update(state="available", message="有验收通过的主线版本，可查看更新。")
            if local["dirty"]:
                result["block_reason"] = "modified"
                raise UpdateError("本地有修改／新增文件，需先保留并人工核对；不能直接PULL。")
            if local["branch"] != "main" and not managed:
                result["block_reason"] = "development"
                raise UpdateError("当前不是main日常使用分支，请先人工对齐；不会切分支覆盖修复。")
            result["block_reason"] = "manual"
            details = {}
            managed = compatible_target(root, local["head"], target, local, details)
            if managed:
                result["migration"] = "managed_release"
            result["installation"] = details["installation"]
            result["summary"] = run_git(root, "log", "--format=%h %s", "-8", f'{local["head"]}..{target}')
            result["can_update"] = True
            result.pop("block_reason", None)
    except Exception as exc:
        if isinstance(exc, ManualUpdateError):
            result['manual_kind'] = exc.kind
        result["message"] = str(exc) if isinstance(exc, UpdateError) else "版本检查暂不可用（网络或GitHub限制），不影响行情更新。"
    if local["dirty"] and result["state"] == "current":
        result["message"] = "提交号与主线相同，但本地有修改；不能按提交号认定代码一致。"
    write_json(cache, result)
    return decorate(result)


def validate_plan(root, plan, api=github_json):
    local = local_version(root)
    if not plan.get("can_update") or not SHA.fullmatch(plan.get("target", "")):
        raise UpdateError("请先重新检查并确认可安全更新的版本。")
    managed = plan.get("migration") == "managed_release"
    if (local != plan.get("local") or local["dirty"]
            or (local["branch"] != "main" and not managed)):
        raise UpdateError("本地版本或文件已经变化，未更新；请重新检查。")
    if api("/branches/main")["commit"]["sha"] != plan["target"] or not acceptance(plan["target"], api):
        raise UpdateError("目标版本或验收状态已经变化，请重新检查。")
    details = {}
    if compatible_target(root, local["head"], plan["target"], local, details) != managed:
        raise UpdateError("本机与正式版的安全关系已经变化，请重新检查。")
    if details.get("installation") != plan.get("installation"):
        raise UpdateError("运行环境或数据库迁移计划已经变化，请重新检查。")


def extract_archive(data, folder):
    folder = Path(folder).resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            p = PurePosixPath(info.filename)
            if (p.is_absolute() or ".." in p.parts or "\\" in info.orig_filename or ":" in info.filename
                    or not p.parts or p.parts[0].lower() in PROTECTED
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise UpdateError("下载代码的路径不安全，未解压到安装目录。")
        archive.extractall(folder)


def probe(folder, python):
    """Native startup probe with a brand-new isolated Store, never formal data."""
    with tempfile.TemporaryDirectory(prefix="aplus-probe-") as runtime:
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from workbench.strategies import verify_frozen; verify_frozen(); "
            "from workbench.store import Store; from workbench.service import Service; "
            "from gui.main_window import AplusMainWindow; "
            "s=Store(); service=Service(s); app=AplusMainWindow(service=service,store=s); "
            "app.withdraw(); app.update(); app.destroy(); service.pool.shutdown(wait=True)"
        )
        env = dict(os.environ, A_WORKBENCH_HOME=runtime)
        env.pop("APLUS_UPDATE_ACK", None)
        p = subprocess.run([str(python), "-I", "-c", script, str(folder)], cwd=folder,
                           env=env, capture_output=True, timeout=90,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if p.returncode:
            raise UpdateError("新版启动检查失败；正式数据未打开，代码未更新。")


def _preserved_payloads(root, local):
    payloads = {}
    for name, expected in local.get("preserved", {}).items():
        if name not in PRESERVED_LOCAL_FILES:
            raise UpdateError("本机保留文件超出安全范围，请维护者核对。")
        path = Path(root) / name
        if not path.is_file() or path.is_symlink() or _digest(path) != expected:
            raise UpdateError("本机保留文件已经变化，请重新检查。")
        payloads[name] = path.read_bytes()
    return payloads


def _replace_file(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(value)
        # A tracked launcher may inherit the Windows read-only attribute after a
        # branch switch.  Clear only that attribute; never delete the original
        # before the atomic replacement has succeeded.
        if path.exists():
            mode = path.stat().st_mode
            os.chmod(path, mode | 0o200)
            os.chmod(temporary, mode | 0o200)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                # Virus scanners and indexers can briefly hold a newly switched
                # launcher on Windows.  A bounded retry keeps rollback reliable.
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _clean_preserved(root, revision, payloads):
    for name in payloads:
        _replace_file(Path(root) / name, run_git(root, "show", f"{revision}:{name}", binary=True))


def _restore_preserved(root, payloads):
    for name, value in payloads.items():
        _replace_file(Path(root) / name, value)


def _append_preserved_backup(backup, payloads):
    if not payloads:
        return
    with zipfile.ZipFile(backup, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in payloads.items():
            archive.writestr("__aplus_local__/" + name, value)


def _install_target(root, plan, payloads):
    old, target = plan["local"]["head"], plan["target"]
    _clean_preserved(root, old, payloads)
    try:
        if plan.get("migration") == "managed_release":
            run_git(root, "switch", "main")
        run_git(root, "merge", "--ff-only", "--no-edit", target)
    except Exception:
        try:
            if plan.get("migration") == "managed_release":
                run_git(root, "switch", plan["local"]["branch"])
        finally:
            _restore_preserved(root, payloads)
        raise


def _environment_python(folder, windowless=False):
    name = "pythonw.exe" if windowless and os.name == "nt" else ("python.exe" if os.name == "nt" else "python")
    return Path(folder) / ("Scripts" if os.name == "nt" else "bin") / name


def _run_checked(command, *, cwd=None, env=None, timeout=900, message, diagnostics=None):
    process = subprocess.run([str(item) for item in command], cwd=cwd, env=env, capture_output=True,
                             timeout=timeout,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if process.returncode:
        if diagnostics is not None:
            output = (process.stdout + process.stderr).decode('utf-8', errors='replace')
            output = re.sub(r'https?://\S+', '[package-source]', output)
            _replace_file(diagnostics, output.encode('utf-8'))
        raise UpdateError(message)


def _environment_marker(folder):
    return Path(folder) / ENVIRONMENT_MARKER


def _environment_contract(contract, python):
    return {"state": "ready", "environment_id": contract["environment_id"],
            "python": contract["python"], "requirements_sha256": contract["requirements_sha256"],
            "requirements_hashes_sha256": contract.get("requirements_hashes_sha256"),
            "executable": str(Path(python).resolve())}


def _python_identity(python):
    process = subprocess.run(
        [str(python), "-I", "-c",
         "import json,platform,sys;print(json.dumps({'executable':sys.executable,'version':platform.python_version()}))"],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if process.returncode:
        raise UpdateError("无法确认更新解释器身份，未开始更新。")
    try:
        return json.loads(process.stdout)
    except ValueError:
        raise UpdateError("更新解释器身份回执无效，未开始更新。") from None


def _verify_environment(folder, stage, contract):
    folder = Path(folder).resolve()
    python = _environment_python(folder).resolve()
    if not python.is_file():
        raise UpdateError("新版环境缺少解释器，未切换日常启动环境。")
    try:
        marker = json.loads(_environment_marker(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateError("新版环境缺少完整的验收标记，未复用该环境。") from None
    if marker != _environment_contract(contract, python):
        raise UpdateError("新版环境身份与正式依赖清单不一致，未复用该环境。")
    return _verify_python_environment(python, stage, contract)


def _verify_python_environment(python, stage, contract):
    python = Path(python).resolve()
    if not python.is_file():
        raise UpdateError("待复用环境缺少解释器，未开始更新。")
    script = (
        "import importlib.metadata as m,json,pathlib,sys;"
        "lock=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines();"
        "wanted={};"
        "[(wanted.__setitem__(*line.split('==',1))) for line in lock "
        "if line.strip() and not line.lstrip().startswith('#') and '==' in line];"
        "actual={name:m.version(name) for name in wanted};"
        "ok=(actual==wanted and pathlib.Path(sys.executable).resolve()==pathlib.Path(sys.argv[2]).resolve() "
        "and f'{sys.version_info.major}.{sys.version_info.minor}'==sys.argv[3]);"
        "print(json.dumps({'ok':ok,'actual':actual},sort_keys=True));"
        "raise SystemExit(0 if ok else 3)"
    )
    _run_checked([python, "-I", "-c", script, Path(stage) / "requirements-lock.txt",
                  python, contract["python"]], timeout=180,
                 message="新版环境中的解释器或依赖版本与正式清单不一致。")
    _run_checked([python, "-I", "-m", "pip", "check"], timeout=180,
                 message="新版环境依赖完整性检查失败；旧环境仍可使用。")
    return python


def _build_environment(folder, current_python, stage, contract):
    expected = contract["python"]
    version = subprocess.run(
        [str(current_python), "-I", "-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if version.returncode or version.stdout.strip() != expected:
        raise ManualUpdateError(
            f"正式版需要 Python {expected}；当前尚无已验收的同版本解释器，未替换旧环境。", "python"
        )
    _run_checked([current_python, "-I", "-m", "venv", folder], timeout=180,
                 message="新版独立运行环境创建失败；旧环境仍可使用。")
    python = _environment_python(folder)
    hashed = contract.get("requirements_hashes_sha256")
    requirements = Path(stage) / ("requirements-hashes.txt" if hashed else "requirements-lock.txt")
    hash_args = ["--require-hashes"] if hashed else []
    _run_checked([python, "-I", "-m", "pip", "install", "--disable-pip-version-check",
                  *hash_args, "--requirement", requirements], timeout=1800,
                 diagnostics=Path(folder).parent / (Path(folder).name + '-install.log'),
                 message="新版依赖安装失败；旧环境和代码均未替换。")


def prepare_target_environment(root, stage, plan, current_python, smoke=probe, on_stage=None):
    contract = plan.get("installation") or {}
    if not contract.get("environment_changed"):
        if contract.get("requirements_sha256"):
            python = Path(current_python).resolve()
            folder = python.parent.parent
            if folder.parent == (metadata_dir(root) / "environments").resolve():
                _verify_environment(folder, stage, contract)
            else:
                # Existing .venv/global interpreters have no managed marker.
                # Record adoption outside that environment, only after all
                # identity/dependency checks and the real startup probe pass.
                key = hashlib.sha256(str(python).encode("utf-8")).hexdigest()
                marker = metadata_dir(root) / "adopted-environments" / (key + ".json")
                expected = _environment_contract(contract, python)
                if marker.exists():
                    try:
                        if json.loads(marker.read_text(encoding="utf-8")) != expected:
                            raise UpdateError("旧环境接纳记录与正式清单不一致，未复用。")
                    except (OSError, ValueError) as exc:
                        raise UpdateError("旧环境接纳记录无效，未复用。") from exc
                _verify_python_environment(python, stage, contract)
                smoke(Path(stage), python)
                if not marker.exists():
                    write_json(marker, expected)
                return python, None
        smoke(Path(stage), current_python)
        return Path(current_python), None
    environment_id = contract.get("environment_id", "")
    if not ENVIRONMENT_ID.fullmatch(environment_id):
        raise UpdateError("运行环境编号无效，未替换旧环境。")
    base = metadata_dir(root) / "environments"
    base.mkdir(parents=True, exist_ok=True)
    final = base / environment_id
    if final.exists():
        python = _verify_environment(final, stage, contract)
        smoke(Path(stage), python)
        return python, None
    final.mkdir()
    write_json(_environment_marker(final), {"state": "building", "environment_id": environment_id})
    if on_stage:
        on_stage("environment_building", target_environment=str(final),
                 created_environment=str(final))
    try:
        _build_environment(final, current_python, stage, contract)
        python = _environment_python(final).resolve()
        write_json(_environment_marker(final), _environment_contract(contract, python))
        python = _verify_environment(final, stage, contract)
        smoke(Path(stage), python)
        if on_stage:
            on_stage("environment_ready", target_python=str(python),
                     target_environment=str(final), created_environment=str(final))
        return python, final
    except Exception:
        pointer = environment_pointer(root)
        pointer_value = pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None
        if pointer_value != str(_environment_python(final).resolve()):
            shutil.rmtree(final, ignore_errors=True)
        raise


def environment_pointer(root):
    return metadata_dir(root) / "current-python.txt"


def write_environment_pointer(root, python):
    python = Path(python).resolve()
    if not python.is_file():
        raise UpdateError("新版解释器不存在，未切换启动环境。")
    pointer = environment_pointer(root)
    temporary = pointer.with_name(pointer.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(str(python), encoding="utf-8")
    os.replace(temporary, pointer)


def restore_environment_pointer(root, value):
    pointer = environment_pointer(root)
    if value is None:
        pointer.unlink(missing_ok=True)
        return
    temporary = pointer.with_name(pointer.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, pointer)


def _sqlite_snapshot(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    with contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src:
        with contextlib.closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def _stable_cell(value):
    if isinstance(value, bytes):
        return {"blob": value.hex()}
    return value


def _table_facts(db, table, columns=None):
    available = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
    columns = list(columns or available)
    if not set(columns).issubset(available):
        raise UpdateError("数据库迁移意外删除了受保护业务字段。")
    quoted = ",".join(f'"{name}"' for name in columns)
    rows = [[_stable_cell(value) for value in row]
            for row in db.execute(f'SELECT {quoted} FROM "{table}"')]
    encoded = sorted(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False) for row in rows)
    payload = "\n".join(encoded).encode("utf-8")
    return {"columns": columns, "count": len(rows),
            "sha256": hashlib.sha256(payload).hexdigest()}


def _dataset_files(db, data_root):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "datasets" not in tables:
        return {"count": 0, "sha256": hashlib.sha256(b"").hexdigest()}
    facts = []
    for dataset_id, raw_path, expected in db.execute(
            "SELECT id,path,sha256 FROM datasets ORDER BY id"):
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(data_root) / path
        if not path.is_file() or not DATA_SHA256.fullmatch(str(expected)) or _digest(path) != expected:
            raise UpdateError("数据库引用的行情文件缺失或校验不一致，未继续迁移。")
        facts.append([dataset_id, str(raw_path), expected])
    payload = json.dumps(facts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"count": len(facts), "sha256": hashlib.sha256(payload).hexdigest()}


def _database_state(path, data_root=None, fact_columns=None):
    path = Path(path)
    data_root = Path(data_root or path.parent)
    with contextlib.closing(sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise UpdateError("数据库完整性检查失败，未继续自动迁移。")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise UpdateError("数据库外键关系检查失败，未继续自动迁移。")
        row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        schema = int(row[0]) if row else None
        tables = {item[0] for item in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                  for name in COUNTED_TABLES if name in tables}
        objects = sorted(f"{kind}:{name}" for kind, name in db.execute(
            "SELECT type,name FROM sqlite_master WHERE type IN ('table','index','trigger') "
            "AND name NOT LIKE 'sqlite_%'") if not name.startswith("sqlite_autoindex"))
        facts = {name: _table_facts(db, name, (fact_columns or {}).get(name))
                 for name in PROTECTED_FACT_TABLES if name in tables}
        external = _dataset_files(db, data_root)
        # Read through SQLite, including committed WAL pages. A main-file hash
        # alone cannot certify that the database has not received new writes.
        logical = {name: _table_facts(db, name) for name in sorted(tables)
                   if not name.startswith("sqlite_")}
        definitions = sorted(db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
        logical_sha = hashlib.sha256(json.dumps(
            [definitions, logical], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"schema": schema, "counts": counts, "objects": objects, "facts": facts,
            "external": external, "sha256": _digest(path), "logical_sha256": logical_sha}


def _migration_boundary(state):
    return {name: copy.deepcopy(state[name])
            for name in ("schema", "objects", "facts", "external")}


def _validate_migration(before, after, target_schema):
    if after["schema"] != target_schema:
        raise UpdateError("数据库迁移后的版本与正式清单不一致。")
    if not set(before["objects"]).issubset(after["objects"]):
        raise UpdateError("数据库迁移意外丢失数据表、索引或触发器。")
    for table, fact in before["facts"].items():
        if after["facts"].get(table) != fact:
            raise UpdateError("数据库迁移改变了受保护的交易或行情事实。")
    if after["external"] != before["external"]:
        raise UpdateError("数据库迁移改变了行情文件引用关系。")


def _run_store_migration(code, python, runtime):
    script = ("import sys;sys.path.insert(0,sys.argv[1]);"
              "from workbench.store import Store;Store(sys.argv[2])")
    env = dict(os.environ, A_WORKBENCH_HOME=str(runtime))
    env.pop("APLUS_UPDATE_ACK", None)
    _run_checked([python, "-I", "-c", script, str(code), str(runtime)], cwd=code, env=env, timeout=300,
                 message="新版数据库迁移执行失败；正式库已保留备份。")


def prepare_database_migration(stage, target_python, runtime, folder, contract, on_stage=None):
    runtime = Path(runtime).resolve()
    database = runtime / "workbench.sqlite3"
    if not database.exists():
        return {"changed": False, "schema_before": None,
                "schema_after": contract.get("target_schema"), "backup": None}
    before = _database_state(database, runtime)
    expected_schema = contract.get("source_schema") or contract.get("target_schema")
    if expected_schema is not None and before["schema"] != expected_schema:
        raise UpdateError("正式数据库版本与迁移计划不一致，未改写数据。")
    if not contract.get("database_changed"):
        return {"changed": False}
    backup = Path(folder) / "workbench-before.sqlite3"
    _sqlite_snapshot(database, backup)
    transaction = {"changed": True, "database": str(database), "backup": str(backup),
                   "backup_sha256": _digest(backup), "pre_migration_sha256": before["sha256"],
                   "pre_logical_sha256": before["logical_sha256"],
                   "schema_before": before["schema"], "schema_after": contract.get("target_schema"),
                   "before_boundary": _migration_boundary(before)}
    if on_stage:
        on_stage("database_backup_ready", database=copy.deepcopy(transaction))
    trial_root = Path(folder) / "trial-runtime"
    trial_root.mkdir(exist_ok=True)
    trial = trial_root / "workbench.sqlite3"
    _sqlite_snapshot(backup, trial)
    _run_store_migration(stage, target_python, trial_root)
    columns = {name: fact["columns"] for name, fact in before["facts"].items()}
    after = _database_state(trial, runtime, columns)
    _validate_migration(before, after, contract.get("target_schema"))
    transaction["trial_boundary"] = _migration_boundary(after)
    if on_stage:
        on_stage("database_trial_ready", database=copy.deepcopy(transaction))
    return transaction


def migrate_formal_database(root, target_python, runtime, transaction):
    if not transaction.get("changed"):
        return transaction
    _run_store_migration(root, target_python, runtime)
    columns = {name: fact["columns"]
               for name, fact in transaction["before_boundary"]["facts"].items()}
    after = _database_state(transaction["database"], runtime, columns)
    if _migration_boundary(after) != transaction.get("trial_boundary"):
        raise UpdateError("正式数据库迁移结果与副本演练不一致。")
    transaction["post_migration_sha256"] = after["sha256"]
    transaction["post_logical_sha256"] = after["logical_sha256"]
    return transaction


def restore_database(transaction, expected_current=None):
    if not transaction or not transaction.get("changed"):
        return
    database = Path(transaction["database"])
    backup = Path(transaction["backup"])
    if not backup.is_file() or _digest(backup) != transaction["backup_sha256"]:
        raise UpdateError("数据库备份校验失败，不能自动回退；请保留现场并联系维护者。")
    current = _digest(database)
    if transaction.get("post_logical_sha256"):
        if _database_state(database)["logical_sha256"] != transaction["post_logical_sha256"]:
            raise UpdateError("数据库在新版开放后又有写入，不能自动覆盖新增交易记录。")
    elif any(database.with_name(database.name + suffix).exists() and
             database.with_name(database.name + suffix).stat().st_size
             for suffix in ("-wal", "-journal")):
        raise UpdateError("数据库存在未核验的附属日志，不能自动覆盖新增交易记录。")
    if (not transaction.get("post_logical_sha256") and expected_current is not None
            and current not in set(expected_current)):
        raise UpdateError("数据库在新版开放后又有写入，不能自动覆盖新增交易记录。")
    temporary = database.with_name(database.name + ".restore-" + uuid.uuid4().hex)
    shutil.copy2(backup, temporary)
    if _database_state(temporary)["schema"] != transaction["schema_before"]:
        temporary.unlink(missing_ok=True)
        raise UpdateError("数据库备份版本校验失败，不能自动回退。")
    for attempt in range(10):
        try:
            os.replace(temporary, database)
            break
        except PermissionError:
            # SQLite/antivirus handles can linger briefly on Windows after the
            # validation connection closes.  Keep the original intact and retry
            # for a bounded interval rather than deleting either side.
            if attempt == 9:
                raise
            time.sleep(0.1 * (attempt + 1))
    for suffix in ("-wal", "-shm", "-journal"):
        database.with_name(database.name + suffix).unlink(missing_ok=True)
    if _digest(database) != transaction["backup_sha256"]:
        raise UpdateError("数据库回退后的校验值不一致，请停止使用并联系维护者。")


def active_transaction_path(root):
    return metadata_dir(root) / ACTIVE_TRANSACTION


def _transaction_path(request_path):
    return Path(request_path).resolve().parent / TRANSACTION_FILE


def _load_transaction(request_path):
    try:
        value = json.loads(_transaction_path(request_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateError("更新事务记录缺失或损坏；为保护数据已阻止启动。") from None
    if value.get("protocol") != INSTALLATION_PROTOCOL:
        raise UpdateError("更新事务版本无法识别；为保护数据已阻止启动。")
    return value


def _persist_transaction(request_path, phase=None, **changes):
    path = _transaction_path(request_path)
    current = {}
    if path.exists():
        current = _load_transaction(request_path)
    current.update(copy.deepcopy(changes))
    if phase is not None:
        current["phase"] = phase
    current["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(path, current)
    return current


def _clear_active_transaction(root, request_path):
    active = active_transaction_path(root)
    try:
        recorded = active.read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""
    if recorded and Path(recorded).resolve() == Path(request_path).resolve():
        active.unlink(missing_ok=True)
    (metadata_dir(root) / "update.lock").unlink(missing_ok=True)


def _write_receipt(request_path, transaction, message):
    receipt = {"state": transaction["phase"], "message": message,
               "old": transaction.get("old"), "target": transaction.get("target"),
               "old_python": transaction.get("old_python"),
               "target_python": transaction.get("target_python"),
               "environment_id": transaction.get("environment_id"),
               "database_schema_before": transaction.get("database", {}).get("schema_before"),
               "database_schema_after": transaction.get("database", {}).get("schema_after"),
               "database_migrated": bool(transaction.get("database_changed")),
               "code_changed": bool(transaction.get("code_changed")),
               "recovery": transaction.get("recovery"),
               "materials": {"folder": str(Path(request_path).resolve().parent),
                             "code_backup": transaction.get("code_backup"),
                             "database_backup": transaction.get("database", {}).get("backup")}}
    write_json(Path(request_path).resolve().parent / "receipt.json", receipt)


def _process_alive(pid):
    if not pid or os.name != "nt":
        return False
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    handle = k.OpenProcess(0x100000, False, int(pid))
    if not handle:
        return False
    try:
        return k.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 258
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))


def _remove_created_environment(root, transaction):
    created = transaction.get("created_environment")
    if not created:
        return
    created = Path(created).resolve()
    base = (metadata_dir(root) / "environments").resolve()
    pointer = environment_pointer(root)
    current = pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None
    if (created.parent != base or current == str(_environment_python(created).resolve())
            or not _environment_marker(created).is_file()):
        return
    shutil.rmtree(created)


def _reconcile_interrupted_changes(root, request, transaction):
    request_payload = json.loads(Path(request).read_text(encoding="utf-8"))
    local = local_version(root)
    if local["dirty"]:
        raise UpdateError("更新中断后发现代码又被修改，已阻止自动恢复。")
    if local["head"] == transaction["target"]:
        transaction = _persist_transaction(request, code_changed=True)
    elif local["head"] == transaction["old"]:
        transaction = _persist_transaction(request, code_changed=False)
    else:
        raise UpdateError("更新中断后的代码版本无法识别，已阻止启动。")
    database = transaction.get("database") or {"changed": False}
    if database.get("changed") and Path(database.get("database", "")).is_file():
        state_now = _database_state(database["database"], request_payload["runtime"])
        current = state_now["sha256"]
        logical = state_now["logical_sha256"]
        if database.get("post_logical_sha256") and logical not in {
                database["post_logical_sha256"], database.get("pre_logical_sha256")}:
            raise UpdateError("数据库逻辑内容已发生未知写入，已阻止自动覆盖。")
        has_logical = bool(database.get("pre_logical_sha256") or database.get("post_logical_sha256"))
        if (not has_logical and any(Path(str(database["database"]) + suffix).exists() and
                Path(str(database["database"]) + suffix).stat().st_size
                for suffix in ("-wal", "-journal"))):
            raise UpdateError("数据库存在未知附属日志，已阻止自动恢复。")
        if logical == database.get("pre_logical_sha256") or (not has_logical and current == database.get("backup_sha256")):
            transaction = _persist_transaction(request, database_changed=False)
        elif logical == database.get("post_logical_sha256") or (not has_logical and current == database.get("post_migration_sha256")):
            transaction = _persist_transaction(request, database_changed=True)
        elif transaction.get("phase") == "database_migrating" and database.get("trial_boundary"):
            columns = {name: fact["columns"]
                       for name, fact in database["before_boundary"]["facts"].items()}
            state = _database_state(database["database"], request_payload["runtime"], columns)
            if _migration_boundary(state) == database["trial_boundary"]:
                database["post_migration_sha256"] = state["sha256"]
                database["post_logical_sha256"] = state["logical_sha256"]
                transaction = _persist_transaction(request, database=database, database_changed=True)
            elif current == database.get("pre_migration_sha256"):
                transaction = _persist_transaction(request, database_changed=False)
            else:
                raise UpdateError("数据库迁移中断后的内容无法证明安全，已阻止启动。")
        elif current != database.get("pre_migration_sha256"):
            raise UpdateError("数据库在更新中断后发生未知写入，已阻止自动覆盖。")
    return transaction


def _target_combination_valid(root, request, transaction):
    local = local_version(root)
    if local["dirty"] or local["head"] != transaction["target"]:
        raise UpdateError("新版启动回执存在，但磁盘代码不是对应正式版本。")
    python = Path(transaction.get("target_python") or "").resolve()
    if not python.is_file():
        raise UpdateError("新版启动回执存在，但目标解释器已经缺失。")
    installation = json.loads(Path(request).read_text(encoding="utf-8"))["plan"].get("installation") or {}
    if installation.get("environment_changed"):
        _verify_environment(python.parent.parent, root, installation)
    database = transaction.get("database") or {}
    if database.get("changed"):
        state = _database_state(database["database"], json.loads(
            Path(request).read_text(encoding="utf-8"))["runtime"])
        if state["schema"] != database.get("schema_after"):
            raise UpdateError("新版启动回执存在，但正式数据库版本不一致。")
    return python


def _rollback_transaction(request_path):
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    root = Path(request["root"]).resolve()
    transaction = _reconcile_interrupted_changes(root, request_path, _load_transaction(request_path))
    errors = []
    if transaction.get("code_changed"):
        try:
            rollback(root, transaction["target"], transaction["old"], transaction.get("old_branch"))
            transaction = _persist_transaction(request_path, code_changed=False, code_recovered=True)
        except Exception as exc:
            errors.append("代码恢复失败")
    database = transaction.get("database") or {}
    if transaction.get("database_changed"):
        try:
            restore_database(database, expected_current={database.get("post_migration_sha256")})
            transaction = _persist_transaction(request_path, database_changed=False, database_recovered=True)
        except Exception as exc:
            errors.append(f"数据库恢复失败：{exc}")
    if errors:
        transaction = _persist_transaction(
            request_path, "recovery_failed", recovery={"outcome": "blocked", "errors": errors}
        )
        _write_receipt(request_path, transaction, "；".join(errors) + "，已保留全部现场并阻止启动。")
        raise UpdateError("；".join(errors) + "，已保留全部现场并阻止启动。")
    restore_environment_pointer(root, transaction.get("old_pointer"))
    _remove_created_environment(root, transaction)
    transaction = _persist_transaction(
        request_path, "rolled_back", pointer_recovered=True,
        recovery={"outcome": "rolled_back", "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    )
    _write_receipt(request_path, transaction, "未完成更新已安全回退，可继续使用旧版。")
    _clear_active_transaction(root, request_path)
    return {"action": "proceed", "transaction": transaction}


def recover_transaction(request_path):
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    root = Path(request["root"]).resolve()
    if request_path.parent.parent != metadata_dir(root).resolve():
        raise UpdateError("更新恢复入口不属于当前安装，已阻止启动。")
    transaction = _load_transaction(request_path)
    if transaction["phase"] in TERMINAL_PHASES:
        _clear_active_transaction(root, request_path)
        return {"action": "proceed", "transaction": transaction}
    ack = request_path.parent / "started.json"
    alive = _process_alive(transaction.get("startup_pid"))
    if ack.exists():
        python = _target_combination_valid(root, request_path, transaction)
        transaction = _persist_transaction(request_path, "committing", target_python=str(python))
        write_environment_pointer(root, python)
        transaction = _persist_transaction(
            request_path, "completed", pointer_committed=True,
            recovery={"outcome": "completed_forward", "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        )
        _write_receipt(request_path, transaction, "新版组合已确认并完成环境切换。")
        _clear_active_transaction(root, request_path)
        cleanup_committed_transactions(root, request_path.parent)
        return {"action": "block" if alive else "proceed", "transaction": transaction}
    if alive:
        transaction = _persist_transaction(
            request_path, "startup_unconfirmed", recovery={"outcome": "waiting_for_startup", "pid": transaction.get("startup_pid")}
        )
        _write_receipt(request_path, transaction, "新版进程仍在启动确认中；已阻止第二个实例混用环境。")
        return {"action": "block", "transaction": transaction}
    with application_mutex():
        require_web_closed()
        return _rollback_transaction(request_path)


def apply_update(root, plan, backup, python, api=github_json, smoke=probe,
                 runtime=None, transaction_dir=None, on_stage=None):
    """Called only after old process exits and the global application mutex is held."""
    validate_plan(root, plan, api)
    contract = plan.get("installation") or {}
    if contract.get("database_changed"):
        try:
            if runtime is None or not str(runtime).strip():
                raise ValueError("missing runtime")
            runtime = Path(runtime).resolve(strict=True)
            if not runtime.is_dir():
                raise ValueError("runtime is not a directory")
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            raise UpdateError("数据库迁移缺少有效运行目录，未开始更新。") from exc
    old, target = plan["local"]["head"], plan["target"]
    payloads = _preserved_payloads(root, plan["local"])
    backup = Path(backup)
    if on_stage:
        on_stage("code_backup_writing", code_backup=str(backup))
    backup.write_bytes(run_git(root, "archive", "--format=zip", old, binary=True))
    _append_preserved_backup(backup, payloads)
    if on_stage:
        on_stage("code_backup_ready", code_backup=str(backup), code_backup_sha256=_digest(backup))
    transaction_dir = Path(transaction_dir or backup.parent)
    environment_created = None
    database_transaction = {"changed": False}
    installed = False
    target_python = Path(python)
    try:
        with tempfile.TemporaryDirectory(prefix="aplus-code-") as stage_name:
            stage = Path(stage_name)
            extract_archive(run_git(root, "archive", "--format=zip", target, binary=True), stage)
            target_python, environment_created = prepare_target_environment(
                root, stage, plan, python, smoke=smoke, on_stage=on_stage
            )
            if runtime is not None:
                database_transaction = prepare_database_migration(
                    stage, target_python, runtime, transaction_dir, contract, on_stage=on_stage
                )
        # Revalidate after the slow probes, including fresh CI and local edits.
        validate_plan(root, plan, api)
        if on_stage:
            on_stage("switching_code", target_python=str(Path(target_python).resolve()),
                     environment_id=contract.get("environment_id"),
                     created_environment=str(environment_created) if environment_created else None,
                     database=copy.deepcopy(database_transaction))
        _install_target(root, plan, payloads)
        installed = True
        if on_stage:
            on_stage("code_installed", code_changed=True)
        if run_git(root, "rev-parse", "HEAD") != target:
            raise UpdateError("更新结果未能确认，请人工检查；代码备份已保留。")
        smoke(Path(root), target_python)
        if database_transaction.get("changed"):
            if on_stage:
                on_stage("database_migrating", database=copy.deepcopy(database_transaction))
            migrate_formal_database(root, target_python, runtime, database_transaction)
            if on_stage:
                on_stage("database_migrated", database=copy.deepcopy(database_transaction),
                         database_changed=True)
        _restore_preserved(root, payloads)
        return {"python": str(Path(target_python).resolve()),
                "environment_id": (plan.get("installation") or {}).get("environment_id"),
                "environment_changed": bool((plan.get("installation") or {}).get("environment_changed")),
                "environment_created": str(environment_created) if environment_created else None,
                "database": database_transaction}
    except Exception as exc:
        if on_stage:
            raise
        rollback_error = None
        if installed:
            try:
                rollback(root, target, old, plan["local"].get("branch"), payloads)
            except Exception as rollback_exc:
                rollback_error = rollback_exc
        try:
            restore_database(database_transaction)
        except Exception as data_exc:
            rollback_error = rollback_error or data_exc
        if environment_created:
            shutil.rmtree(environment_created, ignore_errors=True)
        if not installed and isinstance(exc, UpdateError) and rollback_error is None:
            raise
        if rollback_error:
            raise UpdateError("更新失败且自动回退未能完整确认；请保留代码和数据库备份并联系维护者。") from rollback_error
        if isinstance(exc, ManualUpdateError):
            raise
        raise UpdateError("更新验收失败，已回到旧代码并恢复原数据库；旧环境仍可使用。") from exc


def rollback(root, target, old, old_branch=None, payloads=None):
    local = local_version(root)
    if local["head"] != target or local["dirty"]:
        raise UpdateError("代码在更新后被修改，不能安全回退；请用代码备份人工恢复。")
    payloads = payloads if payloads is not None else _preserved_payloads(root, local)
    _clean_preserved(root, target, payloads)
    try:
        if old_branch and (old_branch == "main" or MANAGED_RELEASE_BRANCH.fullmatch(old_branch)):
            branch_head = run_git(root, "rev-parse", f"refs/heads/{old_branch}")
            if branch_head == target:
                # A normal main checkout is fast-forwarded by the installer.  Move
                # it back through a detached, clean checkout so no user payload is
                # discarded and the old branch identity is restored as well.
                run_git(root, "switch", "--detach", old)
                run_git(root, "branch", "--force", old_branch, old)
            elif branch_head != old:
                raise UpdateError("旧发行分支位置已经变化，请用代码备份人工恢复。")
            run_git(root, "switch", old_branch)
        else:
            run_git(root, "switch", "--detach", old)
    finally:
        _restore_preserved(root, payloads)


def cleanup_committed_transactions(root, current_folder, keep_previous=2):
    """Bound only completed recovery material; unresolved transactions are never deleted."""
    base = metadata_dir(root).resolve()
    current_folder = Path(current_folder).resolve()
    active = None
    try:
        active = Path(active_transaction_path(root).read_text(encoding="utf-8").strip()).resolve().parent
    except (OSError, ValueError):
        pass
    records = []
    for path in base.iterdir():
        transaction_path = path / TRANSACTION_FILE
        if not path.is_dir() or path.name == "environments" or not transaction_path.is_file():
            continue
        try:
            transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        records.append((path.resolve(), transaction))
    completed = sorted((item for item in records if item[1].get("phase") == "completed"),
                       key=lambda item: item[1].get("updated_at", ""), reverse=True)
    keep = {current_folder}
    previous = 0
    for path, _transaction in completed:
        if path == current_folder:
            continue
        if previous >= keep_previous:
            break
        keep.add(path)
        previous += 1
    if active:
        keep.add(active)
    keep.update(path for path, transaction in records if transaction.get("phase") not in TERMINAL_PHASES)
    for path, transaction in records:
        if transaction.get("phase") == "completed" and path not in keep:
            shutil.rmtree(path)
    references = set()
    pointer = environment_pointer(root)
    if pointer.exists():
        references.add(str(Path(pointer.read_text(encoding="utf-8").strip()).resolve()))
    for path, transaction in records:
        if path not in keep:
            continue
        for key in ("old_python", "target_python"):
            if transaction.get(key):
                references.add(str(Path(transaction[key]).resolve()))
    environments = base / "environments"
    if environments.is_dir():
        for folder in environments.iterdir():
            if not folder.is_dir() or not _environment_marker(folder).is_file():
                continue
            if str(_environment_python(folder).resolve()) not in references:
                shutil.rmtree(folder)


def prepare_worker(root, plan, runtime, python=None, spawn=None, data_checksum=None):
    """Prepare a technical receipt/helper outside changing tracked code."""
    root = Path(root).resolve()
    local = local_version(root)
    if local != plan.get("local") or not plan.get("can_update"):
        raise UpdateError("版本或文件已经变化，请重新检查。")
    base = metadata_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    lock = base / "update.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise UpdateError("已有更新保护锁，请查看上次更新回执，勿重复更新。") from None
    os.close(fd)
    folder = base / uuid.uuid4().hex
    try:
        folder.mkdir()
        helper = folder / "worker.py"
        shutil.copyfile(__file__, helper)
        python = Path(python or sys.executable).resolve()
        request = {"root": str(root), "plan": plan, "runtime": str(Path(runtime).resolve()),
                   "python": str(python), "parent_pid": os.getpid()}
        if data_checksum is not None:
            if not isinstance(data_checksum, str) or not DATA_SHA256.fullmatch(data_checksum):
                raise UpdateError('运行数据校验值无效，未准备更新。')
            request['data_checksum'] = data_checksum
        request_path = folder / "request.json"
        write_json(request_path, request)
        pointer = environment_pointer(root)
        old_pointer = pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None
        _persist_transaction(
            request_path, "prepared", protocol=INSTALLATION_PROTOCOL,
            old=plan["local"]["head"], old_branch=plan["local"].get("branch"),
            target=plan["target"], old_python=str(python), old_python_identity=_python_identity(python),
            old_pointer=old_pointer, target_python=None,
            environment_id=(plan.get("installation") or {}).get("environment_id"),
            created_environment=None, code_changed=False, database_changed=False,
            database={"changed": False}, startup_pid=None, formal_opened=False,
            recovery={"outcome": "pending"}
        )
        active = active_transaction_path(root)
        temporary = active.with_name(active.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(str(request_path.resolve()), encoding="utf-8")
        os.replace(temporary, active)
        (spawn or subprocess.Popen)([str(python), str(helper), "--worker", str(request_path)], cwd=folder,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return folder
    except Exception:
        active = active_transaction_path(root)
        try:
            if Path(active.read_text(encoding="utf-8").strip()).resolve() == (folder / "request.json").resolve():
                active.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        lock.unlink(missing_ok=True)
        raise


def application_mutex():
    from contextlib import contextmanager

    @contextmanager
    def held():
        k = ctypes.windll.kernel32
        k.CreateMutexW.restype = ctypes.c_void_p
        handle = k.CreateMutexW(None, False, "Local\\AplusDesktopWorkbench")
        if not handle or k.GetLastError() == 183:
            if handle:
                k.CloseHandle(ctypes.c_void_p(handle))
            raise UpdateError("另一个Aplus仍在运行，未更新代码。")
        try:
            yield
        finally:
            k.CloseHandle(ctypes.c_void_p(handle))
    return held()


def verify_maintenance_data(request):
    """Optional maintenance proof, before restart can legitimately write meta."""
    expected = request.get('data_checksum')
    if expected is None:
        return
    path = Path(request['runtime'])/'workbench.sqlite3'
    if not DATA_SHA256.fullmatch(str(expected)) or not path.is_file() or _digest(path) != expected:
        raise UpdateError('运行数据在升级期间发生变化，请停止其他程序后核对；未回退或删除数据。')


def supervise_delayed_start(request_path, launched, timeout=300):
    """Bounded supervision after the visible startup deadline; never kill a writer."""
    deadline = time.monotonic() + timeout
    ack = Path(request_path).parent / "started.json"
    while time.monotonic() < deadline and launched.poll() is None and not ack.exists():
        time.sleep(.2)
    if ack.exists() or launched.poll() is not None:
        return recover_transaction(request_path)
    return None


def worker(request_path):
    request_path = Path(request_path).resolve()
    folder = request_path.parent
    request = json.loads(request_path.read_text(encoding="utf-8"))
    root = Path(request["root"]).resolve()
    if folder.parent != metadata_dir(root).resolve():
        raise UpdateError("更新回执目录无效。")
    exited = False
    result = {"state": "failed", "message": "未开始更新"}

    def stage(phase, **changes):
        return _persist_transaction(request_path, phase, **changes)

    try:
        if os.name != "nt":
            raise UpdateError("自动重启仅支持已验收的Windows桌面安装。")
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        parent = k.OpenProcess(0x100000, False, int(request["parent_pid"]))
        if not parent:
            raise UpdateError("无法确认旧进程状态，未更新。")
        try:
            stage("waiting_for_old_process")
            write_json(folder / "ready.json", {"ready": True})
            if k.WaitForSingleObject(ctypes.c_void_p(parent), 60000) != 0:
                raise UpdateError("旧进程尚未退出，未更新任何代码。")
            exited = True
            stage("old_process_exited")
        finally:
            k.CloseHandle(ctypes.c_void_p(parent))
        if (folder / "cancelled.json").exists():
            raise UpdateError("用户界面已取消更新准备，未更新代码。")
        with application_mutex():
            stage("preflight")
            require_web_closed()
            verify_maintenance_data(request)
            applied = apply_update(
                root, request["plan"], folder / "old-code.zip", request["python"],
                github_json, probe, request["runtime"], folder, stage
            ) or {"python": request["python"], "database": {"changed": False}}
            if not applied.get("database", {}).get("changed"):
                verify_maintenance_data(request)
            stage("ready_to_launch", target_python=applied.get("python") or request["python"],
                  environment_id=applied.get("environment_id"),
                  created_environment=applied.get("environment_created"),
                  database=applied.get("database", {"changed": False}),
                  database_changed=bool(applied.get("database", {}).get("changed")),
                  code_changed=True)
        transaction = _load_transaction(request_path)
        target_python = transaction.get("target_python") or request["python"]
        env = dict(os.environ, A_WORKBENCH_HOME=request["runtime"],
                   APLUS_UPDATE_ACK=str(folder / "started.json"))
        # From this point any failure is recoverable only through the persisted
        # transaction. Mark formal data as potentially opened before Popen.
        stage("launching", formal_opened=True)
        try:
            launched = subprocess.Popen(
                [target_python, str(root / "launch_dashboard.py")], cwd=root, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except Exception as exc:
            raise UpdateError("新版启动命令创建失败，正在恢复上一组版本。") from exc
        stage("startup_wait", startup_pid=getattr(launched, "pid", None))
        deadline = time.monotonic() + 35
        while (time.monotonic() < deadline and launched.poll() is None
               and not (folder / "started.json").exists()):
            time.sleep(.2)
        if (folder / "started.json").exists():
            outcome = recover_transaction(request_path)
            transaction = outcome["transaction"]
            version = request["plan"].get("target_release", {}).get("version")
            label = f"v{version}" if version else "最新正式版"
            data_message = ("行情和交易记录已完成兼容迁移并保留。"
                            if transaction.get("database", {}).get("changed")
                            else "行情和交易记录保持不变。")
            result = {"state": "completed", "message": f"已更新到{label}并重新启动。\n{data_message}"}
            _write_receipt(request_path, transaction, result["message"])
        elif launched.poll() is None:
            transaction = stage(
                "startup_unconfirmed",
                recovery={"outcome": "waiting_for_startup", "pid": getattr(launched, "pid", None)}
            )
            result = {"state": "startup_unconfirmed",
                      "message": "新版进程仍在启动确认中；下次启动会继续确认，期间不会混用旧环境。"}
            _write_receipt(request_path, transaction, result["message"])
            outcome = supervise_delayed_start(request_path, launched)
            if outcome:
                transaction = outcome["transaction"]
                result = {"state": transaction["phase"], "message": "延迟启动确认已安全收尾。"}
        else:
            raise UpdateError("新版桌面在确认前退出，正在恢复上一组版本。")
    except Exception as exc:
        original = str(exc) if isinstance(exc, UpdateError) else "更新过程异常中断。"
        try:
            outcome = recover_transaction(request_path)
            transaction = outcome["transaction"]
            if transaction["phase"] == "rolled_back":
                result = {"state": "rolled_back", "message": original + "\n已安全回退，可继续使用旧版。"}
                _write_receipt(request_path, transaction, result["message"])
                if exited:
                    env = dict(os.environ, A_WORKBENCH_HOME=request["runtime"])
                    env.pop("APLUS_UPDATE_ACK", None)
                    subprocess.Popen([request["python"], str(root / "launch_dashboard.py")], cwd=root,
                                     env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                result = {"state": transaction["phase"], "message": original}
        except Exception as recovery_exc:
            try:
                transaction = _persist_transaction(
                    request_path, "recovery_failed",
                    recovery={"outcome": "blocked", "error": str(recovery_exc)}
                )
                _write_receipt(request_path, transaction,
                               "自动恢复未能完整确认；已保留现场并阻止不安全启动。")
            except Exception:
                pass
            result = {"state": "recovery_failed",
                      "message": "自动恢复未能完整确认；已保留现场并阻止不安全启动。"}
    if os.name == "nt":
        if result["state"] == "completed":
            ctypes.windll.user32.MessageBoxW(0, result["message"], "Aplus更新完成", 0x40)
        elif result["state"] != "startup_unconfirmed":
            ctypes.windll.user32.MessageBoxW(
                0, result["message"] + "\n详细回执已保留。",
                "Aplus版本更新", 0x30)


def recover_for_startup(request_path):
    try:
        outcome = recover_transaction(request_path)
        return RECOVERY_BLOCKED if outcome["action"] == "block" else 0
    except Exception as exc:
        try:
            transaction = _persist_transaction(
                request_path, "recovery_failed",
                recovery={"outcome": "blocked", "error": str(exc)}
            )
            _write_receipt(request_path, transaction,
                           "未完成更新无法安全恢复；已阻止启动，请保留现场并联系维护者。")
        except Exception:
            pass
        return RECOVERY_BLOCKED


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--recover")
    arguments = parser.parse_args()
    if bool(arguments.worker) == bool(arguments.recover):
        parser.error("exactly one of --worker or --recover is required")
    if arguments.worker:
        worker(arguments.worker)
    else:
        raise SystemExit(recover_for_startup(arguments.recover))
