import os
import re
import pandas as pd
import numpy as np
import logging
from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

def load_sop_rules():
    """读取 Al Brooks 16步 SOP 规则文件"""
    sop_path = os.path.join(BASE_DIR, 'config', 'sop_rules.md')
    try:
        with open(sop_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "警告：SOP 规则文件缺失。请严格基于 Price Action 进行分析。"

def parse_response(response_text):
    """
    [V8.5 通用解析器 V2]
    职责：从 AI 的回复中提取 XML 字段，支持思维链中间结果。
    """
    result = {
        "verdict": "ERROR",
        "reason": "解析失败",
        "raw": response_text
    }
    
    try:
        # 通用决策
        result["verdict"] = _extract_tag(response_text, "VERDICT") or "ERROR"
        result["reason"] = _extract_tag(response_text, "REASON") or _extract_tag(response_text, "PA_TAGS") or "无详细说明"
        result["analysis"] = _extract_tag(response_text, "ANALYSIS")
        result["pa_tags"] = _extract_tag(response_text, "PA_TAGS")
        result["tags"] = _extract_tag(response_text, "PA_TAGS")
        
        # 兼容原有遗留模板 WECHAT 及最新模板 DISCORD
        msg = _extract_tag(response_text, "DISCORD")
        if not msg:
            msg = _extract_tag(response_text, "WECHAT")
        result["discord_msg"] = msg

        # 思维链中间态 (Optional for debugging)
        if "<MARKET_STATE>" in response_text:
            result["market_state"] = _extract_tag(response_text, "MARKET_STATE")
        if "<ALWAYS_IN>" in response_text:
            result["always_in"] = _extract_tag(response_text, "ALWAYS_IN")
        if "<TRAP_CHECK>" in response_text:
            result["trap_check"] = _extract_tag(response_text, "TRAP_CHECK")
            
        # Hunter 专用
        if "<PROBABILITY>" in response_text:
            result["probability"] = _extract_tag(response_text, "PROBABILITY")
        if "<RR>" in response_text:
            result["rr"] = _extract_tag(response_text, "RR")
            
        # Guardian 专用
        if "<STATUS>" in response_text:
            result["status"] = _extract_tag(response_text, "STATUS")
        if "<WARNING>" in response_text:
            result["warning"] = _extract_tag(response_text, "WARNING")
        if "<NEW_STOP>" in response_text:
            result["new_stop"] = _extract_tag(response_text, "NEW_STOP")
        if "<POSITION_ADJUST>" in response_text:
             result["position_adjust"] = _extract_tag(response_text, "POSITION_ADJUST")
             
    except Exception as e:
        logger.error(f"⚠️ 解析器异常: {e}")
        
    return result

def _extract_tag(text, tag):
    """Helper to extract content between <TAG> and </TAG>"""
    try:
        pattern = f"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
             return match.group(1).strip()
        return None
    except Exception:        return None

def get_common_context(df, window_size=60):
    """提取 CSV 数据 (Reverted to 60 days, Raw Data Only)"""
    df = df.sort_values(by='date', ascending=True)
    
    # 微观数据表
    recent_df = df.tail(window_size).copy()
    # 仅保留基础列 + 核心 EMA 距离 (去除 V3 复杂指标)
    # 🟢 V1.9 Physics Engine: Inject Momentum Facts & Indicators
    cols = [
        'date', 'open', 'high', 'low', 'close', 'volume',
        'ema20', 'dist_ema20_atr', 'gap_down_count_20', 'relative_vol', 'linreg_res'
    ]
    final_cols = [c for c in cols if c in recent_df.columns]
    csv_str = recent_df[final_cols].to_string(index=False, float_format="%.2f")
    
    return {
        'csv_str': csv_str,
        'last_price': df.iloc[-1]['close']
    }

# Alias for compatibility with old internal calls
_get_common_context = get_common_context

def format_hunter_prompt(candidate_data):
    """
    [Legacy Wrapper]
    Redirects to the Default Strategy (Hunter V1).
    Kept for backward compatibility with tests/scripts.
    """
    from core.strategy_registry import StrategyRegistry
    strategy = StrategyRegistry.get_strategy("HUNTER_V1")
    
    # Adapt data structure
    ctx = get_common_context(candidate_data['df'], window_size=60)
    sop = load_sop_rules()
    
    context_data = {
        'code': candidate_data['code'],
        'df': candidate_data['df'],
        'ctx': ctx,
        'sop_content': sop,
        'candidate_data': candidate_data
    }
    return strategy.format_prompt(context_data)

def format_guardian_prompt(holding_data):
    """
    [Guardian Mode Prompt V2: Trade Thesis + PA Daily Diagnosis]
    Token 优化版: 不加载完整 SOP (33KB), 只内联持仓管理相关的 PA 规则。
    """
    code = holding_data['code']
    cost = holding_data.get('cost', 0)
    df = holding_data['df']
    
    # 交易论据上下文
    thesis = holding_data.get('trade_thesis', {})
    entry_price = thesis.get('entry_price', cost)
    sl_price = thesis.get('sl_price', 0)
    tp_price = thesis.get('tp_price', 0)
    strategy = thesis.get('strategy', '未知')
    # 🟢 展示名统一走注册表 display_name (零硬编码裸策略名), 分支判断仍用身份证号
    try:
        from core.strategy_registry import StrategyRegistry
        _dn = StrategyRegistry.get_metadata(strategy).get('display_name', strategy)
    except Exception:
        _dn = strategy
    ev_rating = thesis.get('ev_rating', 'N/A')
    gap_status = thesis.get('gap_status', '未知')
    signal_date = thesis.get('signal_date', '未知')

    # 仅取 30 天日线 (持仓管理不需要 60 天)
    ctx = _get_common_context(df, window_size=30)

    # 构建交易论据描述 (简洁)
    if 'STRUCTURAL_GAP' in strategy.upper():
        thesis_desc = (
            f"策略: {_dn} | 信号日: {signal_date} | 评级: {ev_rating}\n"
            f"SL(Gap Floor): {sl_price:.2f} | TP(MM): {tp_price:.2f} | 缺口: {gap_status}"
        )
    elif 'GAP_PINBAR' in strategy.upper() or 'PINBAR' in strategy.upper():
        thesis_desc = (
            f"策略: {_dn} | 信号日: {signal_date} | 评级: {ev_rating}\n"
            f"SL(Gap Floor): {sl_price:.2f} | TP(起涨区间上翻): {tp_price:.2f} | 缺口: {gap_status}"
        )
    elif 'GAP_H2' in strategy.upper() or 'H2' in strategy.upper():
        thesis_desc = (
            f"策略: {_dn} | 信号日: {signal_date} | 评级: {ev_rating}\n"
            f"SL(Gap Floor): {sl_price:.2f} | TP(起涨区间上翻): {tp_price:.2f} | 缺口: {gap_status}"
        )
    elif '3K' in strategy.upper():
        thesis_desc = f"策略: {_dn} | 信号日: {signal_date} | SL: {sl_price:.2f} | TP: {tp_price:.2f}"
    elif 'MTR' in strategy.upper():
        thesis_desc = f"策略: {_dn} | 信号日: {signal_date} | SL: {sl_price:.2f} | TP: {tp_price:.2f}"
    else:
        thesis_desc = f"成本: {cost:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}"
    
    prompt = f"""# ROLE: Al Brooks PA 持仓管家

## PA 规则 (仅持仓管理相关)

**市场状态判断 (Step 1)**:
- 强趋势: 连续 >=3 根同向 K 线, Body > Avg, Price > EMA20
- 弱通道: 回调深度 > 50% 前一波段, 收盘穿越 EMA20
- 震荡区间: K 线重叠度 > 50%, 实体 < 20% 范围

**高潮/陷阱识别 (Step 8)**:
- 买入高潮: 趋势持续 20+ 根后出现最大趋势 K 线, 60% 概率是穷尽性结束
- 穷尽性缺口: 趋势后期缺口, 随后无跟进, 应立即止盈

**加仓 (Step 14)**:
- 顺势加仓 (Pyramiding): 趋势强劲(Always In Long) + 回调出现新信号 K 线时加仓
- 条件: 回调幅度合理 (未跌破前一 Higher Low), 入场用 Stop Order
- 加仓后综合止损必须移至新的盈亏平衡点

**移动止损 (Step 15)**:
- Breakeven 触发: 利润 >= 目标距离一半时移至成本价
- 趋势追踪: 新 Higher Low 形成后, 止损移至其下方 1 tick
- 加速追踪: 趋势加速时逐 K 线追踪 (前一根低点下方)

**止盈 (Step 16)**:
- MM 目标处平仓 50%, 剩余持有至反转信号
- 出现高潮 K 线时在收盘价全部平仓
- 最小修正预期: 10 根 K 线 + 两段走势 (TBTL)

---

## 持仓信息
{thesis_desc}
现价: {ctx['last_price']:.2f} | 成本: {cost:.2f} | 代码: {code}

## 日线数据 (近30天)
{ctx['csv_str']}

## 请回答 (基于上述 PA 规则)
1. 论据是否成立? (Premise 未破坏?)
2. 止损调整? (Higher Low / Breakeven / 逐K追踪?)
3. 加仓机会? (回调企稳 + 信号 K 线? 加仓价位?)
4. 部分止盈? (距 TP {tp_price:.2f} 多远? 高潮信号?)
5. 全部离场? (SL {sl_price:.2f} 已破/即将失守?)

## 输出格式
<STATUS>HOLD / ADD / TRIM / EXIT</STATUS>
<WARNING>Climax / Exhaustion / Premise Weakening / None</WARNING>
<NEW_STOP>新止损价 (>= {sl_price:.2f})</NEW_STOP>
<POSITION_ADJUST>加仓价位/减仓比例/不动 + PA依据</POSITION_ADJUST>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>一句话: PA状态 + 操作建议 (含价格)</DISCORD>
<REASON>推理过程, 引用 K 线日期和特征</REASON>
"""
    return prompt

# Alias for compatibility
format_for_ai = format_hunter_prompt
