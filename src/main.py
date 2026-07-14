"""黄金新闻分析系统主入口。

推荐（老师架构）:
  python scripts/run_news_engine.py --mode loop   # 7×24 新闻决策 → JSONL
  python scripts/run_mt5_executor.py            # 独立 MT5 执行（auto_execute=true 时）
  python scripts/generate_daily_report.py       # 日终报告
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="黄金新闻实时分析系统")
    parser.add_argument(
        "--mode",
        choices=["once", "loop", "engine", "executor"],
        default="once",
        help="once/loop=兼容旧入口; engine=仅新闻决策; executor=仅MT5补执行",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="once/loop/engine 模式下同时执行 MT5（默认否）",
    )
    args = parser.parse_args()

    if args.mode == "executor":
        from src.mt5_bridge.signal_bridge import SignalBridge

        bridge = SignalBridge.from_config()
        n = len(bridge.execute_pending_signals())
        logger.info("executor 补执行 %d 条", n)
        return

    from src.news_engine.runner import run_loop, run_once

    if args.mode in ("engine", "loop"):
        if args.mode == "loop":
            run_loop(execute_mt5=args.execute)
        else:
            run_once(execute_mt5=args.execute)
        return

    # 兼容旧 once/loop
    if args.mode == "loop":
        run_loop(execute_mt5=args.execute)
    else:
        run_once(execute_mt5=args.execute)


if __name__ == "__main__":
    main()
