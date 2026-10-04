"""Offline OCR and parsing helpers for broker closed-position screenshots."""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SCREENSHOTS = 6


@dataclass(frozen=True)
class OcrToken:
    text: str
    x: float
    y: float
    confidence: float = 1.0


_DATE_RE = re.compile(r"(20\d{2})[-/.年]?(\d{2})[-/.月]?(\d{2})")
_SUMMARY_RE = re.compile(
    r"(?P<month>20\d{4}).*?清仓次数\s*(?P<count>\d+).*?清仓盈(?:利|亏)\s*(?P<pnl>[+\-−－]?\s*[\d,]+(?:\.\d+)?)"
)


def _number(text, *, percent=False):
    cleaned = str(text or "").strip().replace(",", "").replace(" ", "")
    cleaned = cleaned.replace("−", "-").replace("－", "-").replace("＋", "+")
    cleaned = cleaned.replace("O", "0").replace("o", "0")
    if percent:
        cleaned = cleaned.replace("%", "")
    match = re.search(r"[+\-]?\d+(?:\.\d+)?", cleaned)
    if not match:
        raise ValueError(text)
    return float(match.group())


def _date(text):
    match = _DATE_RE.search(str(text or ""))
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def _looks_like_name(token):
    text = token.text.strip()
    if not text or _date(text) or any(word in text for word in (
        "清仓次数", "清仓盈利", "清仓盈亏", "持仓天数", "收益率", "证券", "已清仓的股票"
    )):
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", text))


def parse_broker_tokens(tokens):
    """Parse spatial OCR tokens from Ping An-style closed-position screenshots.

    Returns ``(records, summaries, warnings)``. Records are deliberately kept
    editable by the caller; no database writes happen in this module.
    """
    tokens = [token if isinstance(token, OcrToken) else OcrToken(**token) for token in tokens]
    tokens = [token for token in tokens if token.text.strip() and token.confidence >= 0.35]
    tokens.sort(key=lambda token: (token.y, token.x))
    summaries = []
    for token in tokens:
        match = _SUMMARY_RE.search(token.text.replace(" ", "").replace("，", ","))
        if match:
            summaries.append({
                "month": match.group("month"),
                "count": int(match.group("count")),
                "pnl": _number(match.group("pnl")),
            })

    dates = [token for token in tokens if _date(token.text) and "清仓" in token.text]
    records = []
    warnings = []
    for date_token in dates:
        close_date = _date(date_token.text)
        name_candidates = [
            token for token in tokens
            if _looks_like_name(token)
            and token.y < date_token.y
            and 5 <= date_token.y - token.y <= 105
            and token.x <= date_token.x + 160
        ]
        if not name_candidates:
            warnings.append(f"{close_date} 附近未识别到证券名称")
            continue
        name_token = min(name_candidates, key=lambda token: date_token.y - token.y)
        band = [
            token for token in tokens
            if token.x > name_token.x + 180
            and abs(token.y - name_token.y) <= 55
            and not _date(token.text)
        ]
        percent_tokens = [token for token in band if "%" in token.text]
        plain_numbers = []
        for token in band:
            if "%" in token.text:
                continue
            try:
                plain_numbers.append((token, _number(token.text)))
            except ValueError:
                pass
        plain_numbers.sort(key=lambda item: item[0].x)
        percent_tokens.sort(key=lambda token: token.x)
        complete = len(plain_numbers) >= 2 and bool(percent_tokens)
        days_value = plain_numbers[0][1] if plain_numbers else ""
        pnl_value = plain_numbers[-1][1] if len(plain_numbers) >= 2 else ""
        try:
            return_pct = _number(percent_tokens[-1].text, percent=True) if percent_tokens else ""
        except ValueError:
            return_pct = ""
            complete = False
        if not complete:
            warnings.append(f"{name_token.text} {close_date} 的持仓天数/盈亏/收益率识别不完整，请在预览中补全")
        records.append({
            "code": "",
            "name": name_token.text.strip(),
            "close_date": close_date,
            "holding_days": int(round(days_value)) if days_value != "" else "",
            "pnl": pnl_value,
            "return_pct": return_pct,
            "notes": "截图识别导入",
        })

    unique, seen = [], set()
    records.sort(key=lambda item: sum(item[key] != "" for key in ("holding_days", "pnl", "return_pct")),
                 reverse=True)
    complete_name_dates = {
        (item["name"], item["close_date"])
        for item in records
        if all(item[key] != "" for key in ("holding_days", "pnl", "return_pct"))
    }
    for record in records:
        if any(record[key] == "" for key in ("holding_days", "pnl", "return_pct")) \
                and (record["name"], record["close_date"]) in complete_name_dates:
            continue
        fingerprint = (
            record["name"], record["close_date"], record["holding_days"],
            round(record["pnl"], 2) if record["pnl"] != "" else "",
            round(record["return_pct"], 2) if record["return_pct"] != "" else "",
        )
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(record)
    return unique, summaries, warnings


def recognize_screenshot(image, engine=None):
    """Run bundled local OCR for one PIL image and return spatial tokens."""
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("本地截图识别组件未安装，请重新运行 Aplus 安装脚本后再试") from exc
    engine = engine or RapidOCR()
    result, _elapsed = engine(np.asarray(image.convert("RGB")))
    tokens = []
    for item in result or []:
        box, text, confidence = item
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        tokens.append(OcrToken(str(text), sum(xs) / len(xs), sum(ys) / len(ys), float(confidence)))
    return tokens


def recognize_screenshots(images):
    if not images:
        raise ValueError("请先粘贴至少一张截图")
    if len(images) > MAX_SCREENSHOTS:
        raise ValueError(f"一次最多粘贴 {MAX_SCREENSHOTS} 张截图")
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("本地截图识别组件未安装，请重新运行 Aplus 安装脚本后再试") from exc
    engine = RapidOCR()
    records, summaries, warnings = [], [], []
    for index, image in enumerate(images, 1):
        parsed, found_summaries, found_warnings = parse_broker_tokens(
            recognize_screenshot(image, engine=engine)
        )
        records.extend(parsed)
        summaries.extend(found_summaries)
        warnings.extend(f"第 {index} 张：{warning}" for warning in found_warnings)
    unique, seen = [], set()
    records.sort(key=lambda item: sum(item[key] != "" for key in ("holding_days", "pnl", "return_pct")),
                 reverse=True)
    complete_name_dates = {
        (item["name"], item["close_date"])
        for item in records
        if all(item[key] != "" for key in ("holding_days", "pnl", "return_pct"))
    }
    for record in records:
        if any(record[field] == "" for field in ("holding_days", "pnl", "return_pct")) \
                and (record["name"], record["close_date"]) in complete_name_dates:
            continue
        key = (record["name"], record["close_date"], record["holding_days"],
               round(record["pnl"], 2) if record["pnl"] != "" else "",
               round(record["return_pct"], 2) if record["return_pct"] != "" else "")
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique, summaries, warnings
