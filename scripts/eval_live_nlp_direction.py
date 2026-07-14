"""实时 NLP 方向命中率：信号后 N 根 H1 / 或 MT5 tick 验证。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter.loader import load_price_bars  # noqa: E402
from src.paths import get_history_data_root, load_config  # noqa: E402
from src.runtime.decision_log import DecisionLogger  # noqa: E402

CHINA_TZ = timezone(timedelta(hours=8))


def _price_direction_at_event(
    bars: pd.DataFrame,
    event_ts: pd.Timestamp,
    *,
    horizon_bars: int = 4,
    min_move: float = 0.5,
) -> tuple[str, float]:
    ts = pd.Timestamp(event_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("Asia/Shanghai").tz_convert("UTC")
    else:
        ts = ts.tz_convert("UTC")
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


def _build_bid_series(decisions: list[dict]) -> list[tuple[pd.Timestamp, float]]:
    series: list[tuple[pd.Timestamp, float]] = []
    for d in decisions:
        bid = d.get("bid")
        ts_str = d.get("news_time") or d.get("logged_at")
        if bid is None or not ts_str:
            continue
        try:
            ts = pd.Timestamp(ts_str)
            if ts.tzinfo is None:
                ts = ts.tz_localize("Asia/Shanghai")
            series.append((ts, float(bid)))
        except (TypeError, ValueError):
            continue
    series.sort(key=lambda x: x[0])
    return series


def _price_direction_from_snapshots(
    bid_series: list[tuple[pd.Timestamp, float]],
    event_ts: pd.Timestamp,
    *,
    horizon_hours: float = 4.0,
    min_move: float = 0.5,
) -> tuple[str, float]:
    if not bid_series:
        return "neutral", 0.0
    ts = pd.Timestamp(event_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("Asia/Shanghai")

    start_bid = None
    for t, b in bid_series:
        if t <= ts:
            start_bid = b
        else:
            break
    if start_bid is None:
        return "neutral", 0.0

    target = ts + pd.Timedelta(hours=horizon_hours)
    end_bid = start_bid
    for t, b in bid_series:
        if t >= target:
            end_bid = b
            break
        end_bid = b

    ret = end_bid - start_bid
    if ret > min_move:
        return "bullish", ret
    if ret < -min_move:
        return "bearish", ret
    return "neutral", ret


def eval_live_nlp(
    decisions: list[dict],
    bars: pd.DataFrame,
    *,
    horizon_bars: int = 4,
    min_move: float = 0.5,
) -> dict:
    pred_rows = [
        d for d in decisions
        if d.get("direction") in ("bullish", "bearish")
        and d.get("trend_level") in ("L2", "L3", "L4")
    ]
    hits = total = skipped = 0
    by_level: dict[str, list[int]] = {}
    bid_series = _build_bid_series(decisions)
    price_source = "h1_csv"

    for d in pred_rows:
        pred = str(d["direction"])
        ts_str = d.get("logged_at") or d.get("news_time") or ""
        if not ts_str:
            skipped += 1
            continue
        ts = pd.Timestamp(ts_str)
        actual, ret = _price_direction_at_event(
            bars, ts, horizon_bars=horizon_bars, min_move=min_move,
        )
        if actual == "neutral" and bid_series:
            actual, ret = _price_direction_from_snapshots(
                bid_series, ts, horizon_hours=float(horizon_bars), min_move=min_move,
            )
            if actual != "neutral":
                price_source = "signal_bid_snapshots"
        if actual == "neutral":
            skipped += 1
            continue
        total += 1
        if pred == actual:
            hits += 1
        lv = d.get("trend_level", "?")
        by_level.setdefault(lv, [0, 0])
        by_level[lv][1] += 1
        if pred == actual:
            by_level[lv][0] += 1

    rate = hits / total if total else 0.0
    level_rates = {
        k: {"hits": v[0], "total": v[1], "rate": round(v[0] / v[1], 4) if v[1] else None}
        for k, v in by_level.items()
    }
    return {
        "evaluated": total,
        "skipped_neutral_move": skipped,
        "hits": hits,
        "hit_rate": round(rate, 4),
        "horizon_bars_h1": horizon_bars,
        "min_move_usd": min_move,
        "subset": "L2+ with bullish/bearish",
        "price_source": price_source,
        "by_level": level_rates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="实时 NLP 方向 4H 命中率")
    parser.add_argument("--day", default="", help="YYYY-MM-DD")
    parser.add_argument("--horizon", type=int, default=4)
    args = parser.parse_args()

    config = load_config()
    history_root = get_history_data_root(config)
    h1_csv = history_root / "data" / "XAUUSD_H1.csv"
    bars = load_price_bars(h1_csv, timeframe="H1")

    day = args.day or datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
    runtime_dir = Path(config.get("runtime", {}).get("dir", "data/runtime"))
    decisions = DecisionLogger(runtime_dir).read_jsonl(day)

    if not decisions:
        sig_dir = Path(config.get("mt5", {}).get("signal_dir", "data/signals")) / day
        if sig_dir.is_dir():
            for p in sig_dir.glob("*.json"):
                decisions.append(
                    DecisionLogger.from_signal(json.loads(p.read_text(encoding="utf-8")))
                )

    result = eval_live_nlp(decisions, bars, horizon_bars=args.horizon)
    result["day"] = day

    out_dir = PROJECT_ROOT / "docs" / "操作记录" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"live_nlp_eval_{day.replace('-', '')}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
