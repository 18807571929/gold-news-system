"""MT5 执行器：读取已生成信号，补执行 pending（与新闻引擎分离）。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.mt5_bridge.signal_bridge import SignalBridge  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="MT5 执行器（读 JSON → 下单）")
    parser.add_argument("--day", default="", help="YYYY-MM-DD，默认今天")
    args = parser.parse_args()

    bridge = SignalBridge.from_config()
    day = args.day or None
    results = bridge.execute_pending_signals(day=day)
    print(f"补执行 {len(results)} 条信号")


if __name__ == "__main__":
    main()
