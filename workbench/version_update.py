"""Explicit, conservative code updates. Never opens the user's data store.

The copied worker uses only the standard library so changing application files
cannot change the updater halfway through an update.
"""
from __future__ import annotations

import argparse
import ast
import copy
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
MANUAL_FILES = {"requirements.txt",
                "requirements-lock.txt", "setup.cmd"}
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
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
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
    """Static proof for additive Store APIs, not a schema-number-only exemption.

    Never import either revision or open a database. All module initialization,
    class structure and existing methods must remain semantically identical.
    The sole existing-method exception is the audited same-directory short
    atomic temp name; every other statement in that writer stays unchanged.
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

    try:
        old_structure, old = split(before)
        new_structure, new = split(after)
        if old_structure != new_structure or not old.keys() <= new.keys():
            return False
        for name, method in old.items():
            normalize = writer if name == '_write_artifact' else dump
            if normalize(method) != normalize(new[name]):
                return False
        for name in new.keys() - old.keys():
            if name.startswith('_'):
                return False  # No new lifecycle/dispatch hooks.
            method = new[name]
            if method.decorator_list or method.returns or any(
                    arg.annotation for arg in (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs,
                                              *([method.args.vararg] if method.args.vararg else []),
                                              *([method.args.kwarg] if method.args.kwarg else []))):
                return False  # No new class-definition-time execution.
            for default in (*method.args.defaults, *(v for v in method.args.kw_defaults if v is not None)):
                try:
                    ast.literal_eval(default)
                except (ValueError, TypeError):
                    return False
            for node in ast.walk(method):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(
                        r'\b(CREATE|ALTER|DROP|ATTACH|DETACH|VACUUM)\b|\bPRAGMA\b|schema_version', node.value, re.I):
                    return False
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in {'exec','eval','compile','setattr','delattr','__import__'}:
                        return False
                    if isinstance(node.func, ast.Attribute) and node.func.attr in {'execute','executemany','executescript','rows'}:
                        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                            return False  # Dynamic SQL cannot be proved safe here.
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
        for path, expected in files.items():
            blob = run_git(root, 'show', f'{target}:{path}', binary=True)
            if hashlib.sha256(blob).hexdigest() != expected:
                return False
        return True
    except (UpdateError, KeyError, TypeError, ValueError, RecursionError):
        return False


def compatible_target(root, current, target, local=None):
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
    if 'workbench/store.py' in changed:
        try:
            before = run_git(root, 'show', f'{current}:workbench/store.py')
            after = run_git(root, 'show', f'{target}:workbench/store.py')
        except UpdateError:
            raise ManualUpdateError('无法核对数据保存程序的兼容性，请人工安装。', 'data') from None
        if not compatible_store_extension(before, after):
            raise ManualUpdateError('新版改变了数据结构、已有保存逻辑或无法确认的初始化行为，请人工安装并验收。', 'data')
    files = run_git(root, "-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", target).splitlines()
    if local and any(name not in files for name in local.get("preserved", {})):
        raise UpdateError("新版不再包含本机受保护启动文件，请维护者核对。")
    for name in files:
        p = PurePosixPath(name)
        if p.parts[0].lower() in PROTECTED or name.lower().endswith((".db", ".sqlite3", ".key", ".pem")) or p.name.startswith(".env"):
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
            managed = compatible_target(root, local["head"], target, local)
            if managed:
                result["migration"] = "managed_release"
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
    if compatible_target(root, local["head"], plan["target"], local) != managed:
        raise UpdateError("本机与正式版的安全关系已经变化，请重新检查。")


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


def apply_update(root, plan, backup, python, api=github_json, smoke=probe):
    """Called only after old process exits and the global application mutex is held."""
    validate_plan(root, plan, api)
    old, target = plan["local"]["head"], plan["target"]
    payloads = _preserved_payloads(root, plan["local"])
    backup = Path(backup)
    backup.write_bytes(run_git(root, "archive", "--format=zip", old, binary=True))
    _append_preserved_backup(backup, payloads)
    with tempfile.TemporaryDirectory(prefix="aplus-code-") as stage:
        extract_archive(run_git(root, "archive", "--format=zip", target, binary=True), stage)
        smoke(Path(stage), python)
    # Revalidate after the slow probe, including fresh CI and any local edits.
    validate_plan(root, plan, api)
    _install_target(root, plan, payloads)
    if run_git(root, "rev-parse", "HEAD") != target:
        _restore_preserved(root, payloads)
        raise UpdateError("更新结果未能确认，请人工检查；代码备份已保留。")
    try:
        smoke(Path(root), python)
    except Exception:
        # No reset/stash/clean: refuse if someone edited code meanwhile.
        rollback(root, target, old, plan["local"].get("branch"), payloads)
        raise UpdateError("更新后启动检查失败，已回到旧代码；数据未回退，请查看更新回执。")
    _restore_preserved(root, payloads)


def rollback(root, target, old, old_branch=None, payloads=None):
    local = local_version(root)
    if local["head"] != target or local["dirty"]:
        raise UpdateError("代码在更新后被修改，不能安全回退；请用代码备份人工恢复。")
    payloads = payloads if payloads is not None else _preserved_payloads(root, local)
    _clean_preserved(root, target, payloads)
    try:
        if old_branch and MANAGED_RELEASE_BRANCH.fullmatch(old_branch):
            run_git(root, "switch", old_branch)
            if run_git(root, "rev-parse", "HEAD") != old:
                raise UpdateError("旧发行分支位置已经变化，请用代码备份人工恢复。")
        else:
            run_git(root, "switch", "--detach", old)
    finally:
        _restore_preserved(root, payloads)


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
        (spawn or subprocess.Popen)([str(python), str(helper), "--worker", str(request_path)], cwd=folder,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return folder
    except Exception:
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


def worker(request_path):
    folder = Path(request_path).resolve().parent
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    root = Path(request["root"]).resolve()
    if folder.parent != metadata_dir(root).resolve():
        raise UpdateError("更新回执目录无效。")
    receipt = folder / "receipt.json"
    result = {"state": "failed", "message": "未开始更新", "old": request["plan"]["local"]["head"],
              "old_branch": request["plan"]["local"].get("branch"),
              "target": request["plan"]["target"]}
    exited = False
    restarted = False
    try:
        if os.name != "nt":
            raise UpdateError("自动重启仅支持已验收的Windows桌面安装。")
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        parent = k.OpenProcess(0x100000, False, int(request["parent_pid"]))
        if not parent:
            raise UpdateError("无法确认旧进程状态，未更新。")
        try:
            write_json(folder / "ready.json", {"ready": True})
            if k.WaitForSingleObject(ctypes.c_void_p(parent), 60000) != 0:
                raise UpdateError("旧进程尚未退出，未更新任何代码。")
            exited = True
        finally:
            k.CloseHandle(ctypes.c_void_p(parent))
        if (folder / 'cancelled.json').exists():
            raise UpdateError('用户界面已取消更新准备，未更新代码。')
        with application_mutex():
            require_web_closed()
            verify_maintenance_data(request)
            apply_update(root, request["plan"], folder / "old-code.zip", request["python"])
            verify_maintenance_data(request)
            if request.get('data_checksum'):
                result['data_unchanged_before_restart'] = True
        env = dict(os.environ, A_WORKBENCH_HOME=request["runtime"], APLUS_UPDATE_ACK=str(folder / "started.json"))
        launched = subprocess.Popen([request["python"], str(root / "launch_dashboard.py")], cwd=root, env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        restarted = True
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline and launched.poll() is None and not (folder / "started.json").exists():
            time.sleep(.2)
        if (folder / "started.json").exists():
            version = request["plan"].get("target_release", {}).get("version")
            label = f"v{version}" if version else "最新正式版"
            result.update(state="completed", message=f"已更新到{label}并重新启动。\n行情和交易记录保持不变。")
        elif launched.poll() is not None:
            with application_mutex():
                rollback(root, result["target"], result["old"], result.get("old_branch"))
            env.pop("APLUS_UPDATE_ACK", None)
            subprocess.Popen([request["python"], str(root / "launch_dashboard.py")], cwd=root, env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            raise UpdateError("新桌面启动失败，已恢复旧代码并尝试重启；请检查回执及分支。")
        else:
            result.update(state="unconfirmed", message="代码已更新，但桌面启动尚未确认；未强制关闭进程或回退数据。")
    except Exception as exc:
        result["message"] = str(exc) if isinstance(exc, UpdateError) else "更新中断，请查看回执；未删除本地数据。"
        # Preflight/network rejection closes no data and leaves old code intact.
        # Restore usability after the confirmed old GUI has exited, not while it runs.
        if exited and not restarted:
            try:
                env = dict(os.environ, A_WORKBENCH_HOME=request["runtime"])
                env.pop("APLUS_UPDATE_ACK", None)
                remaining = local_version(root)
                if remaining['head'] == result["old"] and not remaining['dirty']:
                    subprocess.Popen([request["python"], str(root / "launch_dashboard.py")], cwd=root,
                                     env=env, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    result['message'] += '；代码状态未确认，请人工检查后启动。'
            except Exception:
                result["message"] += "；请手动重新启动。"
    finally:
        write_json(receipt, result)
        (metadata_dir(root) / "update.lock").unlink(missing_ok=True)
    if os.name == "nt":
        if result["state"] == "completed":
            ctypes.windll.user32.MessageBoxW(0, result["message"], "Aplus更新完成", 0x40)
        elif result["state"] != "unconfirmed":
            ctypes.windll.user32.MessageBoxW(
                0, result["message"] + "\n详细回执已保留；若重试仍失败，请联系维护者。",
                "Aplus版本更新", 0x30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True)
    worker(parser.parse_args().worker)
