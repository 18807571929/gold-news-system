"""7×24 新闻决策引擎入口（默认不执行 MT5）。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.news_engine.runner import run_loop, run_once  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="新闻决策引擎（抓取→判断→JSONL）")
    parser.add_argument("--mode", choices=["once", "loop"], default="once")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="本轮同时执行 MT5（默认仅写 JSON；建议用 run_mt5_executor.py）",
    )
    args = parser.parse_args()

    if args.mode == "loop":
        run_loop(execute_mt5=args.execute)
    else:
        stats = run_once(execute_mt5=args.execute)
        print(stats)


if __name__ == "__main__":
    main()
