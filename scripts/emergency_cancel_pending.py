"""紧急：撤销全部 pending 挂单（不删持仓）。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.mt5_bridge.connector import MT5Connector  # noqa: E402


def main() -> int:
    c = MT5Connector.from_config()
    if not c.connect():
        print("MT5 连接失败")
        return 1
    pending = c.get_pending_orders()
    n = 0
    for o in pending:
        if c.delete_order(o["ticket"]):
            n += 1
    c.disconnect()
    print(f"已撤销 pending {n}/{len(pending)} 笔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
