"""测试 GridExecutor 干跑 / 实盘（需 MT5 已登录）。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def load_sample_signal(level: str = "L2", direction: str = "bullish") -> dict:
    from src.strategy_adapter.grid_adapter import GridAdapter

    sample = {
        "news_id": "test_phase3",
        "trend_score": {
            "direction": direction,
            "direction_cn": "利多" if direction == "bullish" else "利空",
            "trend_level": level,
            "trend_level_cn": "中等",
            "composite_score": 45.0,
        },
        "sentiment": {"confidence": 0.7},
        "news": {"title": "Phase3 测试信号"},
        "duration": {"duration_hours": 12, "duration_label": "medium"},
    }
    advice = GridAdapter.from_config().adapt(sample, atr_factor=1.0)
    return {
        "news_id": "test_phase3",
        **advice.to_dict(),
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="测试网格执行器")
    parser.add_argument("--level", default="L2", choices=["L1", "L2", "L3", "L4"])
    parser.add_argument("--direction", default="bullish", choices=["bullish", "bearish", "neutral"])
    parser.add_argument("--live", action="store_true", help="实盘执行（非 dry_run）")
    args = parser.parse_args()

    from src.mt5_bridge.grid_executor import GridExecutor

    signal = load_sample_signal(args.level, args.direction)
    executor = GridExecutor.from_config()
    result = executor.execute(signal, dry_run=not args.live)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
