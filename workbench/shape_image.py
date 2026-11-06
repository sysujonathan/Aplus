"""Conservative local screenshot contour extraction; never manufactures OHLC."""
from __future__ import annotations

import numpy as np

from .shape_matching import normalize


def candle_contour(image, scheme='white-blue'):
    rgb = np.asarray(image.convert('RGB'),dtype=float)
    if rgb.shape[0] < 40 or rgb.shape[1] < 120:
        raise ValueError('选区过小，请框选主价格区（不含成交量、坐标和工具栏）')
    r,g,b = rgb[:,:,0],rgb[:,:,1],rgb[:,:,2]
    if scheme == 'red-green':
        up = (r>120)&(r>g*1.3)&(r>b*1.2)
        down = (g>80)&(g>r*1.3)&(g>b*1.05)
    elif scheme == 'white-blue':
        if np.mean(rgb.mean(axis=2)<100) < .45:
            raise ValueError('白蓝模式只用于深色背景；请改用红绿模式或人工描绘')
        up = (r>175)&(g>175)&(b>175)
        down = (b>110)&(b>r*1.3)&(b>g*1.05)
    else:
        raise ValueError('请选择截图实际配色')
    mask = up|down
    # Wicks and text may be one pixel wide; retain body-width groups only.
    columns = np.flatnonzero(mask.sum(axis=0)>=4)
    groups = np.split(columns,np.flatnonzero(np.diff(columns)>1)+1)
    points,centers = [],[]
    for xs in groups:
        if not 2 <= len(xs) <= 25:
            continue
        body = mask[:,xs].sum(axis=1) >= max(2,len(xs)*.65)
        ys = np.flatnonzero(body)
        if len(ys)<2:
            continue
        # A huge disconnected row span is usually axis text or a grid crossing.
        segments = np.split(ys,np.flatnonzero(np.diff(ys)>2)+1)
        ys = max(segments,key=len)
        if len(ys)<2:
            continue
        white_or_red = up[np.ix_(ys,xs)].sum() > down[np.ix_(ys,xs)].sum()
        points.append(-float(ys.min() if white_or_red else ys.max()))
        centers.append(float(xs.mean()))
    if len(points)<15:
        raise ValueError('可靠蜡烛不足15根。请重新框选清晰主图，或在原图上从左往右描绘轮廓')
    gaps = np.diff(centers)
    irregularity = float(gaps.std()/gaps.mean())
    if irregularity > .5 or max(gaps)>np.median(gaps)*3:
        raise ValueError('蜡烛间距不规则，可能包含文字或多个图表。请缩小选区或人工描绘')
    path = normalize(points).tolist()
    return dict(path=path,candles=len(path),confidence=round(max(0,1-irregularity),3),
                method='local-colour-body-contour-v1',requires_confirmation=True,
                message='这是截图轮廓，不是还原价格／OHLC。请核对高低点与时间顺序后确认保存。')
