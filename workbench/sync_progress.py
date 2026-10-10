"""One short progress vocabulary, describing the actual range, not job targets."""
from .sources import SOURCES


def sync_progress(source, operation, subject, start=None, end=None):
    labels = {'history': '获取历史', 'tail': '更新尾部', 'prefix': '补齐早期历史',
              'repair': '补拉历史', 'refresh': '重取历史', 'cached': '核验缓存'}
    text = f'{SOURCES[source]} · {labels[operation]} · {subject}'
    if start is not None and end is not None:
        text += f' · {start}～{end}' if start != end else f' · {end}'
    if operation == 'tail':
        text += '（含衔接核对日）'
    return text
