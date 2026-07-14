"""构建 1~3 年历史宏观新闻冲击信号，供年度回测使用。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter.historical_signals import (  # noqa: E402
    build_historical_signals,
    save_historical_signals,
)
from src.paths import get_history_data_root, load_config  # noqa: E402
from src.strategy_adapter.grid_adapter import load_price_shock_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="构建历史宏观事件信号（年度回测用）")
    parser.add_argument("--years", type=float, default=3.0, help="回溯年数，默认 3 年")
    parser.add_argument("--force-generate", action="store_true", help="忽略经济日历，强制用内置 NFP/CPI/FOMC 日程")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    history_root = get_history_data_root(config)
    bt_cfg = config.get("backtest", {})
    scoring_cfg = config.get("trend_scoring", {})
    out = history_root / "signals" / "historical_signals.parquet"

    signals = build_historical_signals(
        years=args.years,
        history_root=history_root,
        dict_path=PROJECT_ROOT / "data/sentiment_dict/gold_sentiment.yaml",
        base_spacing=float(bt_cfg.get("base_spacing", 2.0)),
        default_lot=float(bt_cfg.get("default_lot", 0.01)),
        max_layers=int(bt_cfg.get("max_layers", 5)),
        price_blend=float(scoring_cfg.get("price_blend", 0.20)),
        price_shock_cfg=load_price_shock_config(config),
        exclude_categories=set(bt_cfg.get("exclude_event_categories", ["claims"])),
        l3_shock_hours=int(bt_cfg.get("l3_shock_hours", 4)),
        force_generate=args.force_generate,
        category_level_overrides=bt_cfg.get("category_level_overrides") or None,
        surprise_direction_exclude=set(bt_cfg.get("surprise_direction_exclude", [])),
        momentum_direction_enabled=bool(bt_cfg.get("momentum_direction_enabled", True)),
        momentum_direction_threshold=float(
            bt_cfg.get("momentum_direction_threshold", scoring_cfg.get("direction_threshold", 12))
        ),
    )
    n = save_historical_signals(signals, out)

    dir_counts: dict[str, int] = {}
    src_counts: dict[str, int] = {}
    for s in signals:
        d = str(s.get("direction", "neutral"))
        dir_counts[d] = dir_counts.get(d, 0) + 1
        src = str(s.get("_direction_source", "") or "none")
        src_counts[src] = src_counts.get(src, 0) + 1

    summary = {
        "years": args.years,
        "signal_count": n,
        "direction_breakdown": dir_counts,
        "direction_source_breakdown": src_counts,
        "output": str(out),
        "first": signals[0]["timestamp"].isoformat() if signals else None,
        "last": signals[-1]["timestamp"].isoformat() if signals else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
