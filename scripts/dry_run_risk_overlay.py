"""Dry-run 验证 margin overlay + GridExecutor（无需真实下单）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _sample_l2_signal() -> dict:
    return {
        "news_id": "dry_run_margin_overlay",
        "impact_analysis": {
            "direction": "bullish",
            "direction_cn": "利多",
            "trend_level": "L2",
            "composite_score": 55.0,
        },
        "trade_advice": {
            "action": "reduce_reverse",
            "grid_spacing": 3.5,
            "pause_reverse_orders": True,
            "pause_all_new_orders": False,
            "reverse_position_ratio": 0.0,
            "max_positions": 3,
            "default_lot": 0.01,
        },
    }


def _account(*, equity: float, margin: float, balance: float | None = None) -> dict:
    bal = balance if balance is not None else equity
    return {
        "balance": bal,
        "equity": equity,
        "margin": margin,
        "peak_equity": max(bal, equity),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run 验证风控 overlay")
    parser.add_argument(
        "--scenario",
        choices=("normal", "warning", "emergency", "all"),
        default="all",
        help="normal=健康保证金; warning=margin_level≈75; emergency=margin_level≈60",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from src.mt5_bridge.grid_executor import GridExecutor

    scenarios = {
        "normal": ("正常账户", _account(equity=500, margin=100)),  # margin_level 500%
        "warning": ("预警账户", _account(equity=400, margin=520)),  # ~77%
        "emergency": ("紧急账户", _account(equity=300, margin=500)),  # 60%
    }
    pick = scenarios if args.scenario == "all" else {args.scenario: scenarios[args.scenario]}

    executor = GridExecutor.from_config()
    signal = _sample_l2_signal()
    out = []

    for key, (label, acct) in pick.items():
        margin_level = acct["equity"] / acct["margin"] * 100 if acct["margin"] else None
        result = executor.execute(signal, dry_run=True, account_info=acct)
        row = {
            "scenario": key,
            "label": label,
            "margin_level_pct": round(margin_level, 1) if margin_level else None,
            "risk_state": result.get("risk_state"),
            "actions_taken": result.get("actions_taken"),
        }
        out.append(row)
        print(f"\n=== {label} (margin_level≈{row['margin_level_pct']}%) ===")
        print(json.dumps(row, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
