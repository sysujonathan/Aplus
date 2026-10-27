"""One explicit local approval, repeatable startup, and read-only mode inspection."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.acceleration_profile import (
    STRATEGIES, apply_profile, approve, effective_status, profile_path, set_off,
)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='A++ 稳定快速版本机授权与实际模式')
    sub = parser.add_subparsers(dest='command', required=True)
    approval = sub.add_parser('approve', help='明确授权当前精确版本及环境')
    approval.add_argument('--confirm', action='store_true')
    sub.add_parser('status', help='只读核对实际模式及回退原因')
    off = sub.add_parser('off', help='关闭指定策略；重启生效')
    off.add_argument('--strategy', choices=['all', *STRATEGIES], default='all')
    args = parser.parse_args(argv)
    try:
        if args.command == 'approve':
            approve(confirmed=args.confirm)
        elif args.command == 'off':
            set_off(args.strategy)
        report = effective_status(profile=apply_profile())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print('本机配置：' + str(profile_path()))
        print('GAP H2：保持原版；关闭模式或新授权须重启已有桌面/网页后生效。')
        return 0 if args.command == 'off' or all(
            row['effective'] == 'ACCELERATED' for row in report['strategies'].values()) else 2
    except Exception as exc:
        print(f'未启用：{type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
