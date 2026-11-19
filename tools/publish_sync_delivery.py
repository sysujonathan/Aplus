"""Publish approved packages through a transport; the CLI is a local rehearsal."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def file_sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class LocalRehearsal:
    """Exercise remote readback and latest replacement in an isolated directory."""

    def __init__(self, root):
        self.root = Path(root)

    def put(self, name, source):
        target = self.root / name
        with tempfile.TemporaryDirectory(dir=self.root) as staging:
            temporary = Path(staging) / 'upload'
            shutil.copyfile(source, temporary)
            if name == 'latest.json':
                temporary.replace(target)
            else:
                try:
                    target.hardlink_to(temporary)
                except FileExistsError:
                    if file_sha256(target) != file_sha256(temporary):
                        raise ValueError(f'不可变发布文件冲突：{name}')

    def verify(self, name, expected):
        if file_sha256(self.root / name) != expected:
            raise ValueError(f'远端文件校验失败：{name}')

    def read_visible_latest(self):
        return self.read_latest()

    def read_latest(self):
        try:
            return (self.root / 'latest.json').read_bytes()
        except FileNotFoundError:
            return None


def publish(staged, transport, receipts, approved, *, request=None, mode=None):
    """The caller supplies the trusted approval verifier and authorized transport."""
    from workbench.sync_packages import load_latest, package_name

    staged, receipts = Path(staged), Path(receipts)
    latest = staged / 'latest.json'
    body = latest.read_bytes()
    pointer_sha = hashlib.sha256(body).hexdigest()
    receipts.mkdir(parents=True, exist_ok=True)

    target_version = None
    started = time.monotonic()
    previous_tick = started
    stage_seconds = {}

    def record(state, **details):
        nonlocal previous_tick
        tick = time.monotonic()
        stage_seconds[state] = stage_seconds.get(state, 0) + tick - previous_tick
        previous_tick = tick
        event = dict(elapsed_seconds=round(tick - started, 3), at=datetime.now(timezone.utc).isoformat(), state=state,
                     target=target_version, pointer_sha256=pointer_sha, **details)
        with (receipts / 'publish.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            # Persist the approval and activation boundaries. Intermediate
            # progress is flushed with its next boundary on the same journal.
            if state in ('approved', 'remote-verified', 'latest-activated', 'client-visible', 'failed'):
                stream.flush()
                os.fsync(stream.fileno())

    state = 'validating'
    try:
        plan = load_latest(body)
        target_version = plan['chain'][-1]['version']
        state = 'generated'
        record(state)
        if not approved(pointer_sha):
            state = 'blocked-approval'
            record(state)
            raise PermissionError('阻塞在审批：缺少与本次发布清单及目标目录绑定的有效上传审批')
        state = 'approved'
        record(state)
        initial = transport.read_latest()
        if initial is not None and initial != body:
            previous = load_latest(initial)['chain'][-1]
            target = plan['chain'][-1]
            if target['asof'] < previous['asof'] or (
                    target['asof'] == previous['asof'] and target['parent'] != previous['version']):
                raise ValueError('发布版本早于当前 latest 或同日修订未锚定当前版本')
        packages = {item['sha256'] for item in plan['chain']}
        if plan.get('fallback'):
            packages.add(plan['fallback']['sha256'])
        for sha in sorted(packages):
            name = package_name(sha)
            path = staged / name
            if path.exists():
                if file_sha256(path) != sha:
                    raise ValueError(f'待上传包摘要不一致：{name}')
                transport.put(name, path)
                record('package-uploaded', package=name, sha256=sha)
        state = 'uploaded'
        record(state)
        for sha in sorted(packages):
            transport.verify(package_name(sha), sha)
            record('package-verified', package=package_name(sha), sha256=sha)
        state = 'remote-verified'
        record(state)
        if transport.read_latest() != initial:
            raise RuntimeError('核验期间 latest 已变化，请重新审批发布')
        if latest.read_bytes() != body:
            raise ValueError('审批后待发布清单已变化')
        if initial != body:
            with tempfile.TemporaryDirectory(dir=receipts) as staging:
                approved_pointer = Path(staging) / 'latest.json'
                approved_pointer.write_bytes(body)
                transport.put('latest.json', approved_pointer)
        state = 'latest-activated'
        record(state)
        readback = transport.read_latest()
        if readback != body:
            raise ValueError('远端 latest 回读与批准版本不一致')
        record('latest-readback-verified')
        if transport.read_visible_latest() != body:
            raise ValueError('桌面入口读取的 latest 与批准版本不一致')
        record('client-visible')
        if request is not None:
            receipt = dict(mode=mode, status='client-visible', asof=plan['chain'][-1]['asof'],
                           package_sha256=plan['chain'][-1]['sha256'], file_count=request['files'],
                           package_count=len(packages), pointer_sha256=pointer_sha,
                           remote_latest=load_latest(readback)['chain'][-1],
                           elapsed_seconds=round(time.monotonic() - started, 3),
                           stage_seconds={k: round(v, 3) for k, v in stage_seconds.items()})
            with tempfile.TemporaryDirectory(dir=receipts) as staging:
                path = Path(staging) / 'receipt.json'
                path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                path.replace(receipts / f'{pointer_sha}.json')
        return target_version
    except Exception as exc:
        record('failed', last_completed=state, error=str(exc))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='在全新本地测试目录演练同步发布')
    parser.add_argument('staged', type=Path)
    parser.add_argument('rehearsal_root', type=Path)
    parser.add_argument('--approve-rehearsal-sha256', required=True)
    args = parser.parse_args(argv)
    args.rehearsal_root.mkdir(parents=True, exist_ok=False)
    remote = args.rehearsal_root / 'remote'
    remote.mkdir()
    publish(args.staged, LocalRehearsal(remote), args.rehearsal_root,
            lambda sha: sha == args.approve_rehearsal_sha256)


if __name__ == '__main__':
    main()
