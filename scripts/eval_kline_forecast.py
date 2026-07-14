"""K 线趋势预测走查评估：对比预测方向与实际未来 N 根涨跌。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter.loader import load_price_bars  # noqa: E402
from src.backtest_adapter.naming import make_run_id, output_filename  # noqa: E402
from src.forecast.kline_trend import KlineTrendForecaster  # noqa: E402
from src.paths import get_history_data_root, load_config  # noqa: E402
from src.strategy_adapter.atr_provider import _calc_atr_from_bars  # noqa: E402


def _actual_direction(ret: float, atr: float | None, *, threshold_ratio: float = 0.3) -> str:
    """根据未来 N 根涨跌判断实际方向。"""
    if atr and atr > 0:
        norm = ret / (atr * 1.5)
        if norm > threshold_ratio:
            return "bullish"
        if norm < -threshold_ratio:
            return "bearish"
        return "neutral"
    if ret > 1.0:
        return "bullish"
    if ret < -1.0:
        return "bearish"
    return "neutral"


def _ret_to_score(ret: float, atr: float | None) -> float:
    if atr and atr > 0:
        return max(-100.0, min(100.0, ret / (atr * 1.5) * 100.0))
    return max(-100.0, min(100.0, ret * 10.0))


def walk_forward_eval(
    bars: pd.DataFrame,
    *,
    years: float = 1.0,
    horizon_bars: int = 4,
    warmup_bars: int = 30,
    direction_threshold: float = 12.0,
) -> dict:
    """走查评估：每步用动量预测未来 horizon_bars 根方向。"""
    if bars.empty:
        raise ValueError("行情为空")

    end_ts = bars["datetime"].max()
    start_ts = end_ts - pd.Timedelta(days=int(years * 365.25))
    subset = bars[bars["datetime"] >= start_ts].reset_index(drop=True)

    forecaster = KlineTrendForecaster(
        news_weight=0.0,
        momentum_weight=1.0,
        direction_threshold=direction_threshold,
    )

    hits = 0
    neutral_skips = 0
    total = 0
    score_errors: list[float] = []
    by_direction: dict[str, dict[str, int]] = {
        "bullish": {"hit": 0, "total": 0},
        "bearish": {"hit": 0, "total": 0},
        "neutral": {"hit": 0, "total": 0},
    }

    for i in range(warmup_bars, len(subset) - horizon_bars):
        window = subset.iloc[: i + 1].reset_index(drop=True)
        pred = forecaster.forecast(window, horizon_bars=horizon_bars, timeframe="H1", sentiment=None)
        pred_dir = pred.overall_direction
        pred_score = float(pred.inputs.get("combined_score", 0))

        closes = window["close"].astype(float).tolist()
        highs = window["high"].astype(float).tolist()
        lows = window["low"].astype(float).tolist()
        atr = _calc_atr_from_bars(highs, lows, closes, forecaster.atr_period)

        close_start = float(subset.iloc[i]["close"])
        close_end = float(subset.iloc[i + horizon_bars]["close"])
        ret = close_end - close_start
        actual_dir = _actual_direction(ret, atr)
        actual_score = _ret_to_score(ret, atr)

        if pred_dir == "neutral" and actual_dir == "neutral":
            neutral_skips += 1
            continue

        total += 1
        if pred_dir == actual_dir:
            hits += 1

        by_direction.setdefault(pred_dir, {"hit": 0, "total": 0})
        by_direction[pred_dir]["total"] += 1
        if pred_dir == actual_dir:
            by_direction[pred_dir]["hit"] += 1

        score_errors.append(abs(pred_score - actual_score))

    hit_rate = hits / total if total else 0.0
    mae = sum(score_errors) / len(score_errors) if score_errors else 0.0

    return {
        "years": years,
        "horizon_bars": horizon_bars,
        "warmup_bars": warmup_bars,
        "direction_threshold": direction_threshold,
        "window_start": str(subset["datetime"].iloc[0]),
        "window_end": str(subset["datetime"].iloc[-1]),
        "eval_steps": total,
        "neutral_both_skipped": neutral_skips,
        "hit_count": hits,
        "hit_rate": round(hit_rate, 4),
        "hit_rate_pct": round(hit_rate * 100, 2),
        "score_mae": round(mae, 2),
        "by_predicted_direction": {
            k: {
                "hit": v["hit"],
                "total": v["total"],
                "hit_rate": round(v["hit"] / v["total"], 4) if v["total"] else 0.0,
            }
            for k, v in by_direction.items()
        },
        "note": "走查评估仅用价格动量（无历史新闻），预测 vs 实际未来 N 根收盘涨跌。",
    }


def _write_markdown(result: dict, path: Path, run_id: str) -> None:
    lines = [
        f"# K 线预测走查评估",
        "",
        f"- **Run ID：** {run_id}",
        f"- **评估窗口：** {result['window_start']} ~ {result['window_end']}",
        f"- **预测根数：** {result['horizon_bars']} 根 H1",
        f"- **方向阈值：** ±{result['direction_threshold']}",
        "",
        "## 汇总",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 评估步数 | {result['eval_steps']} |",
        f"| 命中数 | {result['hit_count']} |",
        f"| **命中率** | **{result['hit_rate_pct']}%** |",
        f"| 评分 MAE | {result['score_mae']} |",
        f"| 双中性跳过 | {result['neutral_both_skipped']} |",
        "",
        "## 按预测方向",
        "",
        "| 预测方向 | 命中/总数 | 命中率 |",
        "|----------|-----------|--------|",
    ]
    for d, stats in result.get("by_predicted_direction", {}).items():
        hr = round(stats["hit_rate"] * 100, 1) if stats["total"] else 0
        lines.append(f"| {d} | {stats['hit']}/{stats['total']} | {hr}% |")
    lines += ["", f"> {result.get('note', '')}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="K 线趋势预测走查评估")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--years", type=float, default=1.0, help="评估回溯年数，默认 1")
    parser.add_argument("--bars", type=int, default=4, help="预测/验证 K 线根数，默认 4")
    parser.add_argument("--output-dir", default="", help="输出目录，默认 docs/操作记录")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    history_root = get_history_data_root(config)
    scoring_cfg = config.get("trend_scoring", {})
    direction_threshold = float(scoring_cfg.get("direction_threshold", 12))

    price_csv = history_root / "data" / "XAUUSD_H1.csv"
    bars = load_price_bars(price_csv)
    if bars.empty:
        print(f"行情为空: {price_csv}")
        return 1

    result = walk_forward_eval(
        bars,
        years=args.years,
        horizon_bars=args.bars,
        direction_threshold=direction_threshold,
    )

    run_id = make_run_id()
    out_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "docs" / "操作记录"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / output_filename(run_id, "K线预测评估.json")
    md_path = out_dir / output_filename(run_id, "K线预测评估.md")

    payload = {"run_id": run_id, **result}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(result, md_path, run_id)

    summary = {
        "run_id": run_id,
        "hit_rate_pct": result["hit_rate_pct"],
        "score_mae": result["score_mae"],
        "eval_steps": result["eval_steps"],
        "files": {"json": str(json_path), "markdown": str(md_path)},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
