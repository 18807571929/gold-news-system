"""L2 信号 MT5 实盘验证：挂反向单 → 触发删单。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))


def build_l2_bullish_signal(spacing: float = 1.6) -> dict:
    return {
        "news_id": "verify_l2_test",
        "impact_analysis": {
            "direction": "bullish",
            "direction_cn": "利多",
            "trend_level": "L2",
            "trend_level_cn": "中等",
            "composite_score": 55.0,
        },
        "trade_advice": {
            "action": "reduce_reverse",
            "grid_spacing": spacing,
            "pause_reverse_orders": True,
            "pause_all_new_orders": False,
            "reverse_position_ratio": 0.0,
            "max_positions": 5,
            "default_lot": 0.01,
        },
    }


def setup_test_orders(connector, spacing: float = 5.0) -> list[dict]:
    """在当前价上下挂测试用反向/顺势限价单。"""
    from src.mt5_bridge.connector import MT5Connector

    assert isinstance(connector, MT5Connector)
    tick = connector.get_tick()
    if not tick or tick.bid <= 0:
        raise RuntimeError("无法获取行情，请确认市场开盘且 GOLD 有报价")

    lot = 0.01
    actions = []

    # 反向：sell limit 高于 ask（bullish L2 应删除）
    sell_price = connector.normalize_price(tick.ask + spacing)
    res_sell = connector.place_pending_order(
        "sell_limit", sell_price, lot, comment="verify-l2-reverse"
    )
    actions.append({"type": "sell_limit_reverse", **res_sell})

    # 顺势：buy limit 低于 bid（应保留）
    buy_price = connector.normalize_price(tick.bid - spacing)
    res_buy = connector.place_pending_order(
        "buy_limit", buy_price, lot, comment="verify-l2-trend"
    )
    actions.append({"type": "buy_limit_trend", **res_buy})

    return actions


def run_verification(*, setup: bool = False, live: bool = True, spacing: float = 5.0) -> dict:
    from src.mt5_bridge.connector import MT5Connector
    from src.mt5_bridge.grid_executor import GridExecutor

    connector = MT5Connector.from_config()
    result: dict = {
        "timestamp": datetime.now(CHINA_TZ).isoformat(),
        "connected": False,
        "steps": [],
    }

    if not connector.connect():
        result["error"] = "MT5 连接失败，请先打开 MT5 并登录 FxPro"
        return result

    result["connected"] = True
    try:
        pending_before = connector.get_pending_orders()
        result["pending_before"] = pending_before
        result["steps"].append(f"执行前挂单数: {len(pending_before)}")

        if setup:
            placed = setup_test_orders(connector, spacing=spacing)
            result["setup_orders"] = placed
            result["steps"].append(f"已挂测试单: {len(placed)}")
            if all(not p.get("ok") for p in placed):
                mc = any(p.get("comment") == "Market closed" or p.get("error") == "retcode_10018" for p in placed)
                if mc:
                    result["market_closed"] = True
                    result["steps"].append("市场休市(10018)，无法挂测试单，请开盘后再验")
            pending_before = connector.get_pending_orders()
            result["pending_after_setup"] = pending_before

        signal = build_l2_bullish_signal()
        executor = GridExecutor.from_config(connector)
        exec_result = executor.execute(signal, dry_run=not live, account_info=None)
        result["execution"] = exec_result
        result["steps"].append(f"执行动作: {exec_result.get('actions_taken', [])}")

        pending_after = connector.get_pending_orders()
        result["pending_after"] = pending_after

        sell_before = sum(1 for o in pending_before if "sell" in o["type"])
        sell_after = sum(1 for o in pending_after if "sell" in o["type"])
        buy_after = sum(1 for o in pending_after if "buy" in o["type"])

        result["verification"] = {
            "sell_orders_removed": sell_after < sell_before,
            "sell_before": sell_before,
            "sell_after": sell_after,
            "buy_orders_kept": buy_after > 0,
            "passed": sell_after < sell_before or (setup and sell_before > 0 and sell_after == 0),
            "skipped_market_closed": bool(result.get("market_closed")),
        }
        result["steps"].append(
            f"验证: sell {sell_before}→{sell_after}, buy保留={buy_after}, "
            f"通过={result['verification']['passed']}"
        )
    finally:
        connector.disconnect()

    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="L2 利多信号 MT5 删反向挂单验证")
    parser.add_argument("--setup", action="store_true", help="先挂测试用反向/顺势限价单")
    parser.add_argument("--dry-run", action="store_true", help="仅模拟，不实际操作 MT5")
    parser.add_argument("--spacing", type=float, default=5.0, help="测试挂单与现价间距（美元）")
    parser.add_argument("--out", default="docs/操作记录/logs/l2_verify_latest.json")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    result = run_verification(setup=args.setup, live=not args.dry_run, spacing=args.spacing)

    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("market_closed"):
        return 2  # 休市，非失败
    return 0 if result.get("verification", {}).get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
