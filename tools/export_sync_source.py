"""Export existing server results without opening a writable Store."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.snapshot import ReadOnlyStore
from workbench.sync_source import write_sync_source
from workbench.store import dumps


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成桌面同步来源（只读）')
    parser.add_argument('--runtime', default=None)
    parser.add_argument('--asof', default=None)
    parser.add_argument('--out', default=None)
    args = parser.parse_args(argv)
    out = write_sync_source(ReadOnlyStore(args.runtime), args.asof, args.out)
    print(dumps({'out': str(out)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
