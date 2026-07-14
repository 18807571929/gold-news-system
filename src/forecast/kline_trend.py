"""未来 N 根 K 线趋势判断：新闻评分 + 价格动量，不触发交易。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest_adapter.loader import load_price_bars
from src.strategy_adapter.atr_provider import _calc_atr_from_bars

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))

DIRECTION_CN = {
    "bullish": "看涨",
    "bearish": "看跌",
    "neutral": "震荡",
}


def direction_cn(direction: str) -> str:
    return DIRECTION_CN.get(direction, direction)


def _score_to_direction(score: float, threshold: float = 12.0) -> str:
    if score > threshold:
        return "bullish"
    if score < -threshold:
        return "bearish"
    return "neutral"


@dataclass
class BarTrendForecast:
    bar_index: int
    expected_datetime: str
    direction: str
    direction_cn: str
    confidence: float
    expected_bias: str  # 偏多 / 偏空 / 横盘
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bar_index": self.bar_index,
            "expected_datetime": self.expected_datetime,
            "direction": self.direction,
            "direction_cn": self.direction_cn,
            "confidence": round(self.confidence, 3),
            "expected_bias": self.expected_bias,
            "reason": self.reason,
        }


@dataclass
class TrendForecastReport:
    generated_at: str
    timeframe: str
    horizon_bars: int
    anchor_bar: dict[str, Any]
    overall_direction: str
    overall_direction_cn: str
    overall_confidence: float
    forecasts: list[BarTrendForecast] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    news_context: dict[str, Any] | None = None
    note: str = "本模块仅输出趋势判断，不执行任何交易。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "timeframe": self.timeframe,
            "horizon_bars": self.horizon_bars,
            "anchor_bar": self.anchor_bar,
            "overall_direction": self.overall_direction,
            "overall_direction_cn": self.overall_direction_cn,
            "overall_confidence": round(self.overall_confidence, 3),
            "forecasts": [f.to_dict() for f in self.forecasts],
            "inputs": self.inputs,
            "news_context": self.news_context,
            "note": self.note,
        }


class KlineTrendForecaster:
    """基于最新新闻趋势分 + 近期价格动量，判断未来 N 根 K 线方向。"""

    def __init__(
        self,
        *,
        news_weight: float = 0.55,
        momentum_weight: float = 0.45,
        momentum_lookback: int = 4,
        atr_period: int = 14,
        direction_threshold: float = 12.0,
        confidence_decay_per_bar: float = 0.12,
    ) -> None:
        total = news_weight + momentum_weight
        self.news_weight = news_weight / total
        self.momentum_weight = momentum_weight / total
        self.momentum_lookback = momentum_lookback
        self.atr_period = atr_period
        self.direction_threshold = direction_threshold
        self.confidence_decay_per_bar = confidence_decay_per_bar

    def load_latest_sentiment(self, cache_dir: Path) -> dict[str, Any] | None:
        files = sorted(cache_dir.glob("*/*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        try:
            return json.loads(files[0].read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("读取 sentiment 失败: %s", exc)
            return None

    def compute_momentum_score(self, bars: pd.DataFrame) -> tuple[float, float | None, dict[str, float]]:
        """返回动量分 (-100~100)、ATR、辅助指标。"""
        if len(bars) < self.momentum_lookback + 2:
            return 0.0, None, {}

        closes = bars["close"].astype(float).tolist()
        highs = bars["high"].astype(float).tolist()
        lows = bars["low"].astype(float).tolist()
        atr = _calc_atr_from_bars(highs, lows, closes, self.atr_period)

        ret = closes[-1] - closes[-1 - self.momentum_lookback]
        ma_short = sum(closes[-3:]) / 3
        ma_long = sum(closes[-6:]) / 6 if len(closes) >= 6 else closes[-1]
        slope = ma_short - ma_long

        if atr and atr > 0:
            norm_ret = max(-1.0, min(1.0, ret / (atr * 1.5)))
            norm_slope = max(-1.0, min(1.0, slope / atr))
        else:
            norm_ret = 0.0
            norm_slope = 0.0

        momentum = (norm_ret * 0.7 + norm_slope * 0.3) * 100.0
        aux = {
            "close": closes[-1],
            f"return_{self.momentum_lookback}bar": round(ret, 2),
            "ma3_ma6_spread": round(slope, 2),
            "atr": round(atr, 2) if atr else None,
        }
        return momentum, atr, aux

    def _bar_step(self, timeframe: str) -> timedelta:
        tf = timeframe.upper()
        if tf in ("H1", "1H"):
            return timedelta(hours=1)
        if tf in ("M15", "15M"):
            return timedelta(minutes=15)
        if tf in ("H4", "4H"):
            return timedelta(hours=4)
        return timedelta(hours=1)

    def forecast(
        self,
        bars: pd.DataFrame,
        *,
        horizon_bars: int = 4,
        timeframe: str = "H1",
        sentiment: dict[str, Any] | None = None,
    ) -> TrendForecastReport:
        if bars.empty:
            raise ValueError("行情为空，无法预测")

        horizon_bars = max(1, int(horizon_bars))
        last = bars.iloc[-1]
        anchor_dt = last["datetime"].to_pydatetime()
        if anchor_dt.tzinfo is None:
            anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)

        news_score = 0.0
        news_dir = "neutral"
        news_level = "L1"
        news_title = ""
        news_time = ""
        if sentiment:
            ts = sentiment.get("trend_score") or {}
            news_score = float(ts.get("composite_score", sentiment.get("sentiment", {}).get("score", 0)) or 0)
            news_dir = ts.get("direction", sentiment.get("sentiment", {}).get("direction", "neutral"))
            news_level = ts.get("trend_level", "L1")
            news_title = (sentiment.get("news") or {}).get("title", "")
            news_time = (sentiment.get("news") or {}).get("time", "")

        momentum_score, atr, aux = self.compute_momentum_score(bars)
        combined = self.news_weight * news_score + self.momentum_weight * momentum_score
        overall_dir = _score_to_direction(combined, self.direction_threshold)

        # 基础置信度：新闻与动量同向则更高
        agree = (news_score > 0 and momentum_score > 0) or (news_score < 0 and momentum_score < 0)
        base_conf = min(0.92, 0.45 + abs(combined) / 200.0 + (0.12 if agree else 0.0))
        if news_level == "L3":
            base_conf = min(0.95, base_conf + 0.08)
        elif news_level == "L4":
            base_conf = min(0.97, base_conf + 0.12)

        step = self._bar_step(timeframe)
        forecasts: list[BarTrendForecast] = []

        for i in range(1, horizon_bars + 1):
            expected_dt = anchor_dt + step * i
            conf = max(0.25, base_conf - self.confidence_decay_per_bar * (i - 1))

            # 远端 K 线向中性回归
            adj_score = combined * (1.0 - 0.08 * (i - 1))
            bar_dir = _score_to_direction(adj_score, self.direction_threshold * (1.0 + 0.05 * (i - 1)))

            if bar_dir == "bullish":
                bias = "偏多"
            elif bar_dir == "bearish":
                bias = "偏空"
            else:
                bias = "横盘"

            reasons = []
            if news_title:
                reasons.append(f"新闻「{news_title[:28]}…」趋势{direction_cn(news_dir)}({news_level})")
            else:
                reasons.append("无最新新闻，主要参考价格动量")
            if aux.get("atr"):
                reasons.append(
                    f"近{self.momentum_lookback}根涨跌{aux.get(f'return_{self.momentum_lookback}bar', 0):+.1f} "
                    f"/ ATR{aux['atr']:.1f}"
                )
            reasons.append(f"第{i}根置信度衰减至{conf:.0%}")

            forecasts.append(
                BarTrendForecast(
                    bar_index=i,
                    expected_datetime=expected_dt.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M"),
                    direction=bar_dir,
                    direction_cn=direction_cn(bar_dir),
                    confidence=conf,
                    expected_bias=bias,
                    reason="；".join(reasons),
                )
            )

        return TrendForecastReport(
            generated_at=datetime.now(CHINA_TZ).isoformat(),
            timeframe=timeframe.upper(),
            horizon_bars=horizon_bars,
            anchor_bar={
                "datetime": anchor_dt.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M"),
                "open": float(last["open"]),
                "high": float(last["high"]),
                "low": float(last["low"]),
                "close": float(last["close"]),
            },
            overall_direction=overall_dir,
            overall_direction_cn=direction_cn(overall_dir),
            overall_confidence=base_conf,
            forecasts=forecasts,
            inputs={
                "combined_score": round(combined, 2),
                "news_score": round(news_score, 2),
                "momentum_score": round(momentum_score, 2),
                "news_weight": round(self.news_weight, 2),
                "momentum_weight": round(self.momentum_weight, 2),
                "direction_threshold": self.direction_threshold,
                **aux,
            },
            news_context={
                "title": news_title,
                "time": news_time,
                "direction": news_dir,
                "trend_level": news_level,
                "composite_score": round(news_score, 2),
            }
            if sentiment
            else None,
        )

    def run_from_paths(
        self,
        price_csv: Path,
        *,
        horizon_bars: int = 4,
        timeframe: str = "H1",
        sentiment_cache_dir: Path | None = None,
        tail_bars: int = 120,
    ) -> TrendForecastReport:
        bars = load_price_bars(price_csv, timeframe=timeframe)
        if len(bars) > tail_bars:
            bars = bars.tail(tail_bars).reset_index(drop=True)
        sentiment = None
        if sentiment_cache_dir and sentiment_cache_dir.is_dir():
            sentiment = self.load_latest_sentiment(sentiment_cache_dir)
        return self.forecast(bars, horizon_bars=horizon_bars, timeframe=timeframe, sentiment=sentiment)
