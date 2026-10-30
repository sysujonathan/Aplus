"""One artifact identity for Windows resources, user display and ordering."""
from datetime import date
import json
from pathlib import Path
import re


def version_parts(version):
    if not isinstance(version,str) or not re.fullmatch(r'[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{1,4}',version):
        raise ValueError('发布版本应为 YYYY.MM.DD.当日序号')
    parts=tuple(map(int,version.split('.')))
    date(*parts[:3])
    if not 1<=parts[3]<=9999 or str(parts[3])!=version.split('.')[-1]:
        raise ValueError('发布当日序号无效')
    return parts


def release_sequence(version):
    y,m,d,n=version_parts(version)
    return (y*10000+m*100+d)*10000+n


def validate_identity(value):
    version_parts(value.get('version'))
    if type(value.get('sequence')) is not int or value['sequence']!=release_sequence(value['version']):
        raise ValueError('发布版本与序号不一致')
    if not re.fullmatch('[0-9a-f]{40}',value.get('commit','')):
        raise ValueError('发布提交无效')
    if type(value.get('preview')) is not bool:
        raise ValueError('发布通道标识无效')
    return value


def read_identity(root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    return validate_identity(json.loads((root/'installed-release.json').read_text(encoding='utf-8')))


def version_label(value):
    return 'v'+value['version']+(' · 试用版' if value.get('preview') else '')
