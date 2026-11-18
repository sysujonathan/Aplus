"""Deploy to ~/workspace/aplus-runner; call after tonight's scan AND snapshot.

This replaces the old publish_sync_source.py automatic upload tail. It only
prepares files and a real-approval handoff. A JSON request is not approval.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess


def main(argv=None):
    parser = argparse.ArgumentParser(description='盘前收尾：备齐桌面同步源并交监督方审批上传')
    parser.add_argument('--repo', type=Path, default=Path.home() / 'workspace/Aplus')
    parser.add_argument('--runtime', type=Path, default=Path.home() / '.local/share/Aplus/runtime')
    parser.add_argument('--asof', default=None)
    parser.add_argument('--out', type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or (Path.home() / 'workspace/sync-source-upload' /
                       datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    command = [str(args.repo / '.venv/bin/python'), str(args.repo / 'tools/prepare_sync_delivery.py'),
               '--runtime', str(args.runtime), '--out', str(out)]
    if args.asof:
        command += ['--asof', args.asof]
    subprocess.run(command, check=True)
    print(f'待审批上传：{out / "upload-request.json"}；请监督方发起上传审批卡，尚未上传。', flush=True)


if __name__ == '__main__':
    main()
