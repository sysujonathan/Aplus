"""Small versioned Brooks vocabulary; strategy identifiers are never aliases."""
import re
from collections import Counter
from itertools import combinations

SOURCE = 'https://www.brookstradingcourse.com/price-action-trading-terms-glossary/'
_ROWS = (
    ('trend', 'Trend', 'context', ('trend',)),
    ('trading-range', 'Trading Range', 'context', ('tr',)),
    ('breakout', 'Breakout', 'movement', ('bo',)),
    ('pullback', 'Pullback', 'movement', ('pb',)),
    ('channel', 'Channel', 'context', ()),
    ('wedge', 'Wedge', 'pattern', ()),
    ('climax', 'Climax', 'movement', ()),
    ('reversal', 'Reversal', 'pattern', ()),
    ('high-1', 'High 1', 'entry', ('h1',)),
    ('high-2', 'High 2', 'entry', ('h2',)),
    ('low-1', 'Low 1', 'entry', ('l1',)),
    ('low-2', 'Low 2', 'entry', ('l2',)),
    ('double-top', 'Double Top', 'pattern', ('dt',)),
    ('double-bottom', 'Double Bottom', 'pattern', ('db',)),
    ('gap', 'Gap', 'movement', ()),
    ('major-trend-reversal', 'Major Trend Reversal', 'pattern', ('mtr',)),
)
COLORS = {'context': '#80a3c3', 'movement': '#a5b987', 'pattern': '#bd9db7', 'entry': '#c8b487'}
TERMS = tuple(dict(id='brooks-' + key, name=name, category=category,
                   color=COLORS[category], aliases=list(aliases), parent_id=None)
              for key, name, category, aliases in _ROWS)
IDS = frozenset(t['id'] for t in TERMS)


def fold(text):
    return re.sub(r'[\s_-]+', '', str(text).casefold())


def matching(query):
    q = fold(query)
    return [t['id'] for t in TERMS if any(q in fold(s) for s in [t['name'], *t['aliases']])]


def selected(values, required=False):
    if not isinstance(values, list) or any(v not in IDS for v in values):
        raise ValueError('请选择已有的 Brooks 标准术语')
    result = list(dict.fromkeys(values))
    if required and not result:
        raise ValueError('正式保存或提交前请选择至少一个 Brooks 标准术语；也可先存草稿')
    return result


def graph(cases):
    counts, edges = Counter(), Counter()
    for case in cases:
        ids = sorted(set(case.get('term_ids', case.get('topic_ids', []))) & IDS)
        counts.update(ids)
        edges.update(combinations(ids, 2))
    return {'nodes': [dict(t, case_count=counts[t['id']]) for t in TERMS if counts[t['id']]],
            'edges': [dict(source=a, target=b, count=n) for (a, b), n in sorted(edges.items())]}
