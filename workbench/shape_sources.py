"""Shared price-basis rules for shape templates, candidates and auto batches."""
from .sources import SOURCES

_ADJUSTMENTS = {
    'qfq':'qfq','前复权':'qfq','2':'qfq',
    'hfq':'hfq','后复权':'hfq','1':'hfq',
    'raw':'raw','none':'raw','不复权':'raw','未复权':'raw','3':'raw',
}


def require_adjustment(source, value):
    """Normalize known aliases, then enforce the selected source's price basis."""
    if source not in SOURCES:
        raise ValueError('未知形态行情来源')
    normalized = _ADJUSTMENTS.get(value.strip().casefold()) if isinstance(value,str) else None
    expected = 'raw' if source=='tencent' else 'qfq'
    if normalized != expected:
        label = '不复权' if expected=='raw' else '前复权'
        raise ValueError(f'{SOURCES[source]}复权口径不一致：需要{label}，不能混合价格')
    return normalized


def template_problem(template, source, asof):
    """Neutral contours have no price basis; real OHLC origins must match."""
    if template.get('kind') not in ('case','chart'):
        return None
    origin = template['origin']
    if origin.get('source') != source:
        return '案例模板与匹配行情来源不同，请从当前来源重新关联真实案例行情'
    try:
        require_adjustment(source,origin.get('adjustment'))
    except ValueError as exc:
        return str(exc)
    if origin['cutoff'] > asof:
        return '模板包含匹配日之后的行情，不能用于该历史日回放'
    return None


def skipped_summary(values):
    if not values:
        return ''
    names = '；'.join(f"{v['name']}（{v['reason']}）" for v in values[:3])
    suffix = '…' if len(values)>3 else ''
    return f"；{len(values)}个模板未运行：{names}{suffix}"
