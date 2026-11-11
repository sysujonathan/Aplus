# -*- coding: utf-8 -*-
"""
core/rating.py — 信号评级统一输出契约 (RATING_PLAN Phase 0)

⛔ PA 硬约束 (最高优先级, 违反即破坏系统铁律):
  本模块及所有调用它的策略, 评级因子严禁引入以下非纯价格行为指标:
    - volume / vol_ma20 / relative_vol / amount / 成交额
    - ADX / trend_strength / linreg_res (非纯 PA 动量指标)
    - EMA 斜率 / 均线多头排列 (EMA20 仅可作磁力/背景/MA_Gap 翻转证据, 不可作动量打分因子)
  所有评级因子必须能在 config/sop_rules.md 的 16-Step SOP 中找到 PA 理论依据 (标注 sop_ref)。
  任何引入黑名单因子的提交, 视为违反 PA 铁律, 一律拒绝合入。
"""
import os
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# 评级字母常量
LETTER_A_PLUS = 'A+'
LETTER_A = 'A'
LETTER_B = 'B'
LETTER_C = 'C'
LETTER_D = 'D'

# [P0-2] 生产端加载评级校准产物 (数据驱动评级落地, 带兜底)。
# rating_factors.json 由 tools/rating_calibration_backtest.py 生成;
# 缺失/损坏时回退手调全局阈值 (见 THRESHOLD_*), 不影响扫描。
_FACTORS_CACHE = None
_FACTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'config', 'rating_factors.json'
)


def load_rating_factors(force: bool = False) -> Dict[str, Any]:
    """加载 config/rating_factors.json (生产评级校准产物)。

    任意异常 (文件缺失/结构异常/JSON 损坏) -> 返回 {} (回退手调阈值)。
    结果按进程缓存, 避免每次评级都读盘。
    """
    global _FACTORS_CACHE
    if _FACTORS_CACHE is not None and not force:
        return _FACTORS_CACHE
    try:
        if not os.path.exists(_FACTORS_PATH):
            logger.warning(f"[rating] 校准文件缺失 {_FACTORS_PATH}, 回退手调阈值")
            _FACTORS_CACHE = {}
            return _FACTORS_CACHE
        with open(_FACTORS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or 'strategies' not in data:
            raise ValueError("校准文件结构异常 (缺 strategies)")
        _FACTORS_CACHE = data
        n = len(data.get('strategies', {}))
        # [P0-5] 如实说明: 校准产物已加载, 但字母评级(A+/A/B/C/D)经验证为统计噪声(9/9策略全噪声),
        #         生产不再据其渲染字母; 仅因子命中作证据、按策略历史EV排优先级。避免误导。
        logger.debug(f"[rating] 评级校准产物已加载 ({n} 策略); 字母评级经回测验证为噪声, 不参与输出")
    except Exception as e:
        logger.warning(f"[rating] 校准文件加载失败 ({e}), 回退手调阈值")
        _FACTORS_CACHE = {}
    return _FACTORS_CACHE


def is_calibration_available() -> bool:
    """校准产物是否可用 (生产评级据此把 calibrated 标为 True)。"""
    return bool(load_rating_factors().get('strategies'))


def get_strategy_calibration(calib_key: str) -> Dict[str, Any]:
    return load_rating_factors().get('strategies', {}).get(calib_key, {})


def letter_evidence(calib_key: str, letter: str) -> Dict[str, Any]:
    """返回该策略该字母的历史净胜率/样本量/EV (来自 full_sample_letter_net)。无则 {}。

    用途: 让推送/报表能如实展示 "本字母在该策略上的历史表现", 而非空泛宣称。
    """
    strat = get_strategy_calibration(calib_key)
    return strat.get('full_sample_letter_net', {}).get(letter, {})


# 全局分档阈值 (Phase 0 暂用; 校准产物 rating_factors.json 提供逐策略切点后优先使用, 见下)
THRESHOLD_A_PLUS = 80
THRESHOLD_A = 65
THRESHOLD_B = 50
THRESHOLD_D = 30  # toxic 或 <30 -> D

# [P0-2/P0-3] 策略名 -> 校准 base 键映射 (cls.name 或 registry name 均可命中).
# 校准 json 的键为 f"{base}_{tf}" (如 'mtr_daily' / 'structural_gap_weekly').
_STRATEGY_CALIB_BASE = {
    'MTR_MASTER': 'mtr', 'MTR_V35_STRUCTURAL': 'mtr',
    'STRATEGY_3K': 'three_k',
    'STRATEGY_STRUCTURAL_GAP': 'structural_gap',
    'STRATEGY_GAP_PINBAR': 'gap_pinbar',
    'STRATEGY_GAP_H2': 'gap_h2',
    'STRATEGY_AWIL': 'awil',
}

# 逐策略分位切点方案 (与校准脚本 _compute_calib_cuts 一致): A+前10% / A前30% / B前55% / C前80%
_CUT_QUANTILES = [('A+', 0.90), ('A', 0.70), ('B', 0.45), ('C', 0.20)]


def _resolve_strategy_name(s) -> Optional[str]:
    """把策略类 / property 描述符 / 字符串 统一解析为注册名字符串 (命中 _STRATEGY_CALIB_BASE)。

    调用处统一传 cls (类对象)。name 在 BaseStrategy 是 @property, 类访问取到的是描述符
    对象而非字符串值, 必须实例化才能取到真实字符串。此处集中处理, 避免各策略散落实例化。
    """
    if isinstance(s, type):
        try:
            s = s().name          # 实例化取 name property 的字符串值
        except Exception:
            s = getattr(s, '__name__', '')
    if not isinstance(s, str):
        return None              # property 对象等无法取值 -> 不匹配, 回退全局阈值
    return s or None


def get_strategy_cuts(strategy_name: str, timeframe: str = 'daily') -> Optional[List[List]]:
    """返回该策略该周期的训练集分位切点 [[letter, threshold], ...] (降序).

    切点来自 rating_factors.json 的 score_cuts (由 calibrate 脚本按生产 compute_rating
    分数分布生成, 数据驱动, 非手调). 缺失/无校准时返回 None (band 回退全局阈值).
    """
    strategy_name = _resolve_strategy_name(strategy_name)
    if not strategy_name:
        return None
    base = _STRATEGY_CALIB_BASE.get(strategy_name) or _STRATEGY_CALIB_BASE.get(strategy_name.upper())
    if not base:
        return None
    key = f"{base}_{timeframe}"
    cuts = get_strategy_calibration(key).get('score_cuts')
    if not cuts:
        # 回退到另一周期 (若该周期未单独校准)
        other = 'weekly' if timeframe == 'daily' else 'daily'
        cuts = get_strategy_calibration(f"{base}_{other}").get('score_cuts')
    if not cuts:
        return None
    try:
        return [[str(c[0]), float(c[1])] for c in cuts]
    except Exception:
        return None


def band_calibrated(strategy_name, score: int, toxic: bool = False,
                    timeframe: str = 'daily') -> str:
    """[P0-2/P0-3] 数据驱动定档: 优先逐策略切点, 回退全局阈值.

    strategy_name 接受策略类 (cls) 或字符串; 见 _resolve_strategy_name。
    """
    strategy_name = _resolve_strategy_name(strategy_name)
    return band(score, toxic=toxic, cuts=get_strategy_cuts(strategy_name, timeframe))


@dataclass
class RatingFactor:
    """单个评级因子 (纯 PA, 可溯源 SOP)."""
    name: str                                  # 因子中文名, 如 "回调速度"
    value: float                               # 实际数值, 如 pb_bars=3
    hit: bool                                  # 是否命中加分条件
    weight: float                              # 贡献分 (如 +2 / -1)
    win_rate: Optional[float] = None           # 该因子历史胜率 (Phase 2 填充, Phase 0 为 None)
    sop_ref: str = ''                          # PA 理论依据, 如 "SOP Step 8"
    note: str = ''                             # PA 理据 (可进卡片)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'value': self.value,
            'hit': self.hit,
            'weight': self.weight,
            'win_rate': self.win_rate,
            'sop_ref': self.sop_ref,
            'note': self.note,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'RatingFactor':
        return cls(
            name=d.get('name', ''),
            value=d.get('value', 0.0),
            hit=d.get('hit', False),
            weight=d.get('weight', 0.0),
            win_rate=d.get('win_rate'),
            sop_ref=d.get('sop_ref', ''),
            note=d.get('note', ''),
        )


@dataclass
class RatingResult:
    """统一评级输出 (所有策略经 compute_rating 产出)."""
    raw_score: float                           # 策略原生分 (structural: ev_score -5~+9; mtr: 0-100)
    score: int                                 # 归一化 0-100 (经 score_map / map_native)
    letter: str                                # 'A+' / 'A' / 'B' / 'C' / 'D'
    factors: List[RatingFactor] = field(default_factory=list)
    toxic: bool = False                        # D 级(毒性)标记
    calibrated: bool = False                   # Phase 0 False(手调权重); Phase 2 True(回测校准)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'raw_score': self.raw_score,
            'score': self.score,
            'letter': self.letter,
            'factors': [f.to_dict() for f in self.factors],
            'toxic': self.toxic,
            'calibrated': self.calibrated,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'RatingResult':
        return cls(
            raw_score=d.get('raw_score', 0.0),
            score=d.get('score', 0),
            letter=d.get('letter', LETTER_C),
            factors=[RatingFactor.from_dict(x) for x in d.get('factors', [])],
            toxic=d.get('toxic', False),
            calibrated=d.get('calibrated', False),
        )


def band(score: int, toxic: bool = False, cuts: Optional[List] = None) -> str:
    """分档: toxic -> D; 若有逐策略切点 cuts 则按切点定档, 否则回退全局阈值.

    cuts: [[letter, threshold], ...] 降序 (A+ -> A -> B -> C). 低于 C 切点 -> D.
    """
    if toxic:
        return LETTER_D
    if cuts:
        for letter, t in cuts:
            if score >= t:
                return letter
        return LETTER_D
    if score < THRESHOLD_D:
        return LETTER_D
    if score >= THRESHOLD_A_PLUS:
        return LETTER_A_PLUS
    if score >= THRESHOLD_A:
        return LETTER_A
    if score >= THRESHOLD_B:
        return LETTER_B
    return LETTER_C


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> int:
    """数值夹取到 [lo, hi] 并取整."""
    return int(max(lo, min(hi, x)))


def map_native(native: float, intercept: float = 50.0, slope: float = 10.0) -> int:
    """各策略单调映射原生分 -> 0-100 (clamp).
       score = clamp(intercept + slope * native).
       例:
         structural ev_score∈[-5,+9] -> 50 + 10*ev_score (与原四因子映射兼容)
         mtr 0-100 直通 -> intercept=0, slope=1
    """
    return clamp(intercept + slope * native)
