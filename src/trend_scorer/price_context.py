"""价格上下文：H1 动量 + DXY 辅助，供趋势评分融合。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest_adapter.loader import load_price_bars
from src.strategy_adapter.atr_provider import _calc_atr_from_bars


def _tail_csv(path: Path, n: int = 30) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["datetime"])
    if "dxy_close" in df.columns:
        df = df.rename(columns={"dxy_close": "close"})
    elif "vix_close" in df.columns:
        df = df.rename(columns={"vix_close": "close"})
    return df.tail(n)


def momentum_score_from_bars(bars: pd.DataFrame, *, lookback: int = 4, atr_period: int = 14) -> tuple[float, dict[str, Any]]:
    """基于 H1 收盘计算动量分 (-100~100)。"""
    if len(bars) < lookback + 2:
        return 0.0, {"reason": "bars_insufficient"}
    closes = bars["close"].astype(float).tolist()
    highs = bars["high"].astype(float).tolist()
    lows = bars["low"].astype(float).tolist()
    atr = _calc_atr_from_bars(highs, lows, closes, atr_period)
    ret = closes[-1] - closes[-1 - lookback]
    ma3 = sum(closes[-3:]) / 3
    ma6 = sum(closes[-6:]) / 6 if len(closes) >= 6 else closes[-1]
    slope = ma3 - ma6
    if atr and atr > 0:
        norm = max(-1.0, min(1.0, ret / (atr * 1.5))) * 0.7 + max(-1.0, min(1.0, slope / atr)) * 0.3
    else:
        norm = 0.0
    score = norm * 100.0
    return score, {
        "return_n": round(ret, 2),
        "atr": round(atr, 2) if atr else None,
        "ma_spread": round(slope, 2),
    }


def dxy_adjustment(history_root: Path, *, lookback: int = 5) -> tuple[float, dict[str, Any]]:
    """DXY 近期涨跌 → 黄金反向辅助分。DXY 涨 → 负分（利空黄金）。"""
    path = history_root / "external_market_cache" / "dxy_close.csv"
    df = _tail_csv(path, lookback + 2)
    if len(df) < lookback + 1:
        return 0.0, {"reason": "dxy_missing"}
    closes = df["close"].astype(float).tolist()
    chg = closes[-1] - closes[-1 - lookback]
    pct = chg / closes[-1 - lookback] * 100 if closes[-1 - lookback] else 0
    # DXY +0.5% ≈ -25 黄金分
    score = max(-50.0, min(50.0, -pct * 50.0))
    return score, {"dxy_change_pct": round(pct, 3)}


def get_price_context_score(
    history_root: Path,
    *,
    bars: pd.DataFrame | None = None,
    lookback: int = 4,
    dxy_weight: float = 0.25,
) -> tuple[float, dict[str, Any]]:
    """综合价格上下文分 (-100~100)。"""
    if bars is None:
        csv = history_root / "data" / "XAUUSD_H1.csv"
        bars = load_price_bars(csv).tail(lookback + 20)
    mom, mom_meta = momentum_score_from_bars(bars, lookback=lookback)
    dxy, dxy_meta = dxy_adjustment(history_root, lookback=lookback)
    combined = mom * (1 - dxy_weight) + dxy * (dxy_weight * 2)  # dxy scale ±50 → blend
    combined = max(-100.0, min(100.0, combined))
    return combined, {"momentum": mom_meta, "dxy": dxy_meta, "momentum_score": round(mom, 2), "dxy_score": round(dxy, 2)}


def momentum_at_time(bars: pd.DataFrame, ts: pd.Timestamp, *, lookback: int = 4) -> float:
    """事件时点之前的动量分（回测用）。"""
    sub = bars[bars["datetime"] <= ts].tail(lookback + 20)
    score, _ = momentum_score_from_bars(sub, lookback=lookback)
    return score
