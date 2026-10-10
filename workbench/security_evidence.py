"""Explicit read-only public security evidence; never contains market prices."""
import csv
from datetime import date
import json
from pathlib import Path
import re

from .sources import source_file


def load_identity_facts(path=None):
    path = path or Path(__file__).with_name('evidence') / 'security_identities.json'
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 1:
        raise ValueError('证券身份事实版本不支持')
    result = {}
    for item in data['facts']:
        code, day, reason, url = (item[k] for k in ('code', 'effective_from', 'reason', 'evidence'))
        if (not re.fullmatch(r'(sh|sz|bj)\.\d{6}', code) or code in result or
                date.fromisoformat(day).isoformat() != day or not reason or not url.startswith('https://')):
            raise ValueError('证券身份事实格式无效或重复')
        result[code] = (day, reason, url)
    return result


def csv_rows(path):
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            return {r['code']: r for r in csv.DictReader(stream)}
    except (OSError, ValueError, KeyError):
        return {}


def read_baostock_dated_directory(store, day):
    """Exactly this day's BaoStock status facts, regardless of selected source."""
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('证券状态证据日期无效')
    path = source_file(store, f'directories/{day}.csv', 'baostock')
    return csv_rows(path), path
