"""宏观 surprise 方向命中率：事件后 4 根 H1 涨跌是否与 surprise 方向一致。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter.historical_signals import (  # noqa: E402
    build_historical_signals,
    load_historical_signals_parquet,
)
from src.backtest_adapter.loader import load_price_bars  # noqa: E402
from src.paths import get_history_data_root, load_config  # noqa: E402


def _price_direction_at_event(
    bars: pd.DataFrame,
    event_ts: pd.Timestamp,
    *,
    horizon_bars: int = 4,
    min_move: float = 0.5,
) -> tuple[str, float]:
    """事件时点起 horizon_bars 根 H1 的涨跌方向。"""
    ts = pd.Timestamp(event_ts).tz_convert("UTC") if event_ts.tzinfo else pd.Timestamp(event_ts, tz="UTC")
    idx = bars["datetime"].searchsorted(ts, side="right") - 1
    if idx < 0 or idx + horizon_bars >= len(bars):
        return "neutral", 0.0
    close_start = float(bars.iloc[idx]["close"])
    close_end = float(bars.iloc[idx + horizon_bars]["close"])
    ret = close_end - close_start
    if ret > min_move:
        return "bullish", ret
    if ret < -min_move:
        return "bearish", ret
    return "neutral", ret


def eval_surprise_hit_rate(
    signals: list[dict],
    bars: pd.DataFrame,
    *,
    horizon_bars: int = 4,
    min_move: float = 0.5,
) -> dict:
    """仅评估 _direction_source=surprise 且方向非 neutral 的信号。"""
    surprise = [
        s for s in signals
        if str(s.get("_direction_source", "")) == "surprise"
        and s.get("direction") in ("bullish", "bearish")
    ]

    hits = 0
    total = 0
    neutral_actual = 0
    by_dir: dict[str, dict[str, int]] = {
        "bullish": {"hit": 0, "total": 0},
        "bearish": {"hit": 0, "total": 0},
    }
    by_category: dict[str, dict[str, int]] = {}

    for sig in surprise:
        pred_dir = str(sig["direction"])
        ts = pd.Timestamp(sig["timestamp"])
        actual_dir, ret = _price_direction_at_event(
            bars, ts, horizon_bars=horizon_bars, min_move=min_move,
        )
        if actual_dir == "neutral":
            neutral_actual += 1
            continue

        total += 1
        if pred_dir == actual_dir:
            hits += 1

        by_dir.setdefault(pred_dir, {"hit": 0, "total": 0})
        by_dir[pred_dir]["total"] += 1
        if pred_dir == actual_dir:
            by_dir[pred_dir]["hit"] += 1

        title = str(sig.get("title", ""))
        cat = "macro"
        for key in ("nfp", "cpi", "pce", "gdp", "retail", "ism", "fomc"):
            if key in title.lower() or key in str(sig.get("news_id", "")):
                cat = key
                break
        by_category.setdefault(cat, {"hit": 0, "total": 0})
        by_category[cat]["total"] += 1
        if pred_dir == actual_dir:
            by_category[cat]["hit"] += 1

    hit_rate = hits / total if total else 0.0
    return {
        "horizon_bars": horizon_bars,
        "min_move_usd": min_move,
        "surprise_signals": len(surprise),
        "evaluated": total,
        "neutral_actual_skipped": neutral_actual,
        "hit_count": hits,
        "hit_rate": round(hit_rate, 4),
        "hit_rate_pct": round(hit_rate * 100, 2),
        "by_predicted_direction": {
            k: {
                "hit": v["hit"],
                "total": v["total"],
                "hit_rate": round(v["hit"] / v["total"], 4) if v["total"] else 0.0,
            }
            for k, v in by_dir.items()
        },
        "by_category": {
            k: {
                "hit": v["hit"],
                "total": v["total"],
                "hit_rate": round(v["hit"] / v["total"], 4) if v["total"] else 0.0,
            }
            for k, v in sorted(by_category.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="surprise 方向 vs 4h 金价命中率")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--bars", type=int, default=4, help="H1 根数，默认 4（约 4h）")
    parser.add_argument("--min-move", type=float, default=0.5, help="判定方向的最小涨跌（USD）")
    parser.add_argument("--rebuild", action="store_true", help="重建信号而非读 parquet")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    history_root = get_history_data_root(config)
    hist_path = history_root / "signals" / "historical_signals.parquet"
    price_csv = history_root / "data" / "XAUUSD_H1.csv"

    bars = load_price_bars(price_csv)
    if bars.empty:
        print(f"行情为空: {price_csv}")
        return 1

    if args.rebuild:
        signals = build_historical_signals(years=args.years, history_root=history_root)
    elif hist_path.is_file():
        signals = load_historical_signals_parquet(hist_path)
    else:
        print(f"历史信号不存在: {hist_path}，请先 build_historical_signals")
        return 1

    result = eval_surprise_hit_rate(
        signals, bars, horizon_bars=args.bars, min_move=args.min_move,
    )
    src_counts: dict[str, int] = {}
    for s in signals:
        src = str(s.get("_direction_source", "") or "none")
        src_counts[src] = src_counts.get(src, 0) + 1

    summary = {
        "signal_count": len(signals),
        "direction_source_breakdown": src_counts,
        **result,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
