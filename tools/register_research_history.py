"""Register TickFlow research bars or migrate the verified TickFlow calendar."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.market import parse_codes
from workbench.research_market import migrate_calendar, register
from workbench.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate-calendar')
    command = commands.add_parser('register')
    command.add_argument('folder', type=Path)
    command.add_argument('codes', help='A-share codes separated by commas')
    args = parser.parse_args()
    store = Store(args.runtime)
    if args.command == 'migrate-calendar':
        calendar = migrate_calendar(store)
        print(calendar['start'], calendar['end'], len(calendar['trading_days']))
    else:
        for record in register(store, args.folder, parse_codes(args.codes)):
            print(record['id'], record['code'], record['start'], record['end'], record['rows'])


if __name__ == '__main__':
    main()
