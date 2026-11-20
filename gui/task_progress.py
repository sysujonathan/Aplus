"""Human-readable premarket progress; no influence on job/scan decisions."""
from workbench.sources import SOURCES


def premarket_progress(sources, ended, total, fallback=''):
    parts = []
    for source, item in sources.items():
        name = SOURCES.get(source, source)
        phase = item.get('phase', '等待')
        done, count = item.get('progress', 0), item.get('total', 0)
        if phase == '核验缺日':
            parts.append(f'{name}核验缺日中')
        elif phase in ('更新', '扫描', '补扫', '核对缓存'):
            action = {'更新': '更新行情', '扫描': '策略扫描', '补扫': '策略补扫',
                      '核对缓存': '核对已有行情'}[phase]
            unit = '只'
            if phase in ('扫描', '补扫'):
                if item.get('message', '').startswith('扫描前核验'):
                    action = '核对扫描范围'
                else:
                    unit = '次'
            detail = f'已处理{done}{unit}，共{count}{unit}' if count else '正在准备'
            tail = '；随后自动扫描' if phase in ('更新', '核对缓存') else ''
            if '整日回退逐股' in item.get('message', ''):
                detail += '（逐股更新）'
            parts.append(f'{name}正在{action}：{detail}{tail}')
        else:
            parts.append(f'{name}：{phase}' + (f'，{item["message"][:75]}'
                         if phase in ('失败', '等待续做') and item.get('message') else ''))
    if not parts:
        return fallback or '正在准备行情更新，之后自动扫描'
    prefix = f'本次共{total}个数据源，已结束{ended}个 · ' if total > 1 else ''
    return prefix + ' · '.join(parts)
