"""Stage a self-contained desktop source and an approval request; never upload."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.market import completed_date
from workbench.snapshot import load_snapshot, snapshot_digest
from workbench.store import digest, dumps

TARGET_FOLDER_ID = '1CpQsi8WdmouMv6T5EaK-JMG4OiZofuJo'
TARGET_PARENT_ID = '1Dl3Qjmb24H8VqBdrV9YeHSkAIuN2lSbp'


def delivery_files(source):
    """Only manifest-declared market/inventory/calendar artifacts may leave runtime."""
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['sha256'] != snapshot_digest(manifest):
        raise ValueError('同步源清单校验失败')
    records = [*manifest['datasets'], *manifest['directories'], manifest['calendar']]
    files = {}
    for record in records:
        relative = record['path']
        path = source / relative
        allowed = (relative.startswith('market/') and len(Path(relative).parts) == 2
                   and relative.endswith('.csv')) or relative in (
                       'sources/tickflow/universe.csv', 'trading_calendar.json',
                       'sources/tickflow/trading_calendar.json')
        if not allowed or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('同步源包含越界或未授权文件')
        if digest(path.read_bytes()) != record['sha256']:
            raise ValueError(f'同步源文件校验失败：{relative}')
        files[relative] = dict(path=relative, bytes=path.stat().st_size, sha256=record['sha256'])
    path = source / 'manifest.json'
    # Manifest always goes last, after every declared artifact is verified remotely.
    return manifest, [files[k] for k in sorted(files)] + [
        dict(path='manifest.json', bytes=path.stat().st_size, sha256=digest(path.read_bytes()))]


def prepare(runtime, asof, out):
    runtime, out = Path(runtime).resolve(), Path(out).resolve()
    if out.is_relative_to(runtime) or runtime.is_relative_to(out):
        raise ValueError('待上传目录必须与正式 runtime 隔离')
    if out.exists():
        raise ValueError('待上传目录已存在；请使用新的目录，保留上一批审批材料')
    snapshot = load_snapshot(runtime / 'snapshots' / asof / 'snapshot.json')
    if snapshot['asof'] != asof:
        raise ValueError('快照日期与本次晚间交付日期不一致；拒绝回落到旧数据')
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.sync-delivery-', dir=out.parent))
    try:
        source = temporary / 'source'
        tool = Path(__file__).with_name('export_sync_source.py')
        subprocess.run([sys.executable, str(tool), '--runtime', str(runtime),
                        '--asof', asof, '--out', str(source)], check=True, capture_output=True, text=True)
        manifest, files = delivery_files(source)
        # Export may hardlink immutable market files. Detach every staged inode so
        # review, packaging or future staging edits cannot modify runtime bytes.
        for record in files:
            path = source / record['path']
            detached = path.with_suffix(path.suffix + '.detached')
            shutil.copyfile(path, detached)
            detached.replace(path)
        with (temporary / 'upload-files.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=['order', 'target_root_id', 'path', 'bytes', 'sha256'])
            writer.writeheader()
            for order, record in enumerate(files, 1):
                writer.writerow(dict(order=order, target_root_id=TARGET_FOLDER_ID, **record))
        archive = temporary / 'sync-source.zip'
        with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
            for record in files:
                bundle.write(source / record['path'], record['path'])
        request = dict(status='awaiting-human-upload-approval', asof=asof,
                       target_folder_id=TARGET_FOLDER_ID, expected_parent_id=TARGET_PARENT_ID,
                       source=str(out / 'source'), manifest_digest=manifest['sha256'],
                       archive_sha256=digest(archive.read_bytes()),
                       files=len(files), datasets=len(manifest['datasets']),
                       observations=len(manifest['observations']),
                       coverage=manifest['coverage'],
                       instructions='监督方先发起真实上传审批卡；批准后按 upload-files.csv 上传 source 内容到目标根目录，'
                                    '逐文件校验后最后替换 manifest.json；ZIP 仅供运输，须解包。此文件不是审批凭据。')
        (temporary / 'upload-request.json').write_text(dumps(request), encoding='utf-8')
        temporary.rename(out)
        return request
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成桌面同步源、压缩包和逐文件审批清单（不上传）')
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--asof', default=None)
    parser.add_argument('--out', required=True, help='新的待审批目录；不得位于 runtime 内')
    args = parser.parse_args(argv)
    asof = args.asof or completed_date()
    print(dumps(prepare(args.runtime, asof, args.out)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
