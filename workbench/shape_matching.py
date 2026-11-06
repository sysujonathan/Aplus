"""Local research only. Anchors/scoring adapted from Wen Xing's MIT project.

Upstream c51408a612dfd4979d95eb0e56b7348027825446; see
third_party/shape-catcher-LICENSE.txt. No downloader or remote client is used.
"""
from __future__ import annotations

import numpy as np

ALGORITHM = 'minmax-mae-pearson-v1'
WINDOWS = (30, 60, 120)
TEMPLATES = {
    'strong_trend': ('强趋势延续', [(0,.05),(.2,.18),(.45,.4),(.7,.66),(.9,.9),(1,1)],
                     ('强趋势','强势上涨','主升浪','创新高','趋势延续','strong trend')),
    'bottom_reversal': ('底部反转', [(0,.9),(.2,.62),(.4,.34),(.55,.08),(.7,.42),(.85,.72),(1,.95)],
                        ('底部反转','见底回升','V底','v型','超跌反弹','bottom reversal')),
    'w_bottom': ('W底／双底', [(0,.85),(.15,.45),(.3,.15),(.45,.55),(.55,.75),(.65,.55),(.75,.18),(.88,.55),(1,.95)],
                 ('w底','双重底','双底','二次探底','不破前低','double bottom')),
    'trend_pullback': ('趋势回踩', [(0,.15),(.2,.38),(.45,.62),(.6,.8),(.7,.78),(.82,.72),(.9,.84),(1,.95)],
                       ('趋势回踩','回踩','回调','洗盘','支撑确认','pullback')),
    'range_consolidation': ('箱体震荡', [(0,.5),(.17,.32),(.33,.68),(.5,.35),(.67,.65),(.83,.38),(1,.55)],
                            ('箱体','横盘','震荡','平台整理','蓄势','range')),
    'top_reversal': ('M头／顶部反转', [(0,.15),(.15,.55),(.3,.9),(.45,.45),(.55,.6),(.65,.42),(.75,.85),(.88,.5),(1,.08)],
                     ('m头','双重顶','双头','见顶回落','顶部反转','double top')),
}


def normalize(values):
    data = np.asarray(values, dtype=float)
    if data.ndim != 1 or not 3 <= len(data) <= 10000 or not np.isfinite(data).all():
        raise ValueError('形态须有 3—10000 个有效点')
    spread = float(np.ptp(data))
    if spread < 1e-12:
        raise ValueError('轮廓没有可识别的高低变化')
    return (data - data.min()) / spread


def resample(values, window):
    if window not in WINDOWS:
        raise ValueError('匹配长度只支持 30／60／120 BAR')
    values = normalize(values)
    return np.interp(np.linspace(0, 1, window), np.linspace(0, 1, len(values)), values)


def classic(key, window=60):
    if key not in TEMPLATES:
        raise ValueError('请选择六类经典形态之一')
    anchors = np.asarray(TEMPLATES[key][1])
    return np.interp(np.linspace(0, 1, window), anchors[:,0], anchors[:,1]).tolist()


def keywords(text):
    """Return all explicit matches; do not pretend arbitrary prose is understood."""
    text = str(text).casefold()
    return [key for key, (name, _, words) in TEMPLATES.items()
            if key in text or name.casefold() in text or any(word.casefold() in text for word in words)]


def compare(closes, template):
    """The score is shape similarity, never a win rate or trading instruction."""
    try:
        a = normalize(closes)
    except ValueError:
        return dict(score=0., mae=1., correlation=0.)
    f = resample(template, len(a))
    mae = float(np.mean(np.abs(a-f)))
    ca, cf = a-a.mean(), f-f.mean()
    denom = float(np.sqrt(np.dot(ca,ca)*np.dot(cf,cf)))
    rho = float(np.dot(ca,cf)/denom) if denom > 1e-12 else 0.
    return dict(score=round(100*(.5*(1-mae)+.5*max(0.,rho)),2),
                mae=round(mae,6), correlation=round(rho,6))


def drawing_path(points):
    """A drawing is a price-free contour, not fabricated OHLC."""
    data = np.asarray(points, dtype=float)
    if data.ndim != 2 or data.shape[1] != 2 or len(data) < 3 or len(data) > 10000 or not np.isfinite(data).all():
        raise ValueError('请从左往右绘制至少三个点')
    if np.any(np.diff(data[:,0]) < -2):
        raise ValueError('请从左往右描绘，时间轴不能倒退')
    unique = dict((float(x), -float(y)) for x,y in data)
    if len(unique) < 3:
        raise ValueError('轮廓的横向范围不足')
    xs = np.asarray(sorted(unique))
    values = [unique[x] for x in xs]
    return np.interp(np.linspace(xs[0],xs[-1],120), xs, normalize(values)).tolist()
