"""GridExecutor 逻辑测试（无需 MT5）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_grid_executor import load_sample_signal  # noqa: E402
from src.mt5_bridge.grid_executor import GridExecutor  # noqa: E402


def test_l2_bearish_closes_reverse_and_places_sell_grid() -> None:
    executor = GridExecutor.from_config()
    signal = load_sample_signal("L2", "bearish")
    result = executor.execute(signal, dry_run=True)
    actions = result["actions_taken"]

    assert any("grid_take_profit" in a for a in actions), actions
    assert any("reduce_reverse_positions(100%)" in a for a in actions), actions
    assert any("place_grid(sell_limit" in a for a in actions), actions
    assert any("tp=entry±spacing" in a for a in actions), actions


def test_l2_bullish_places_buy_grid() -> None:
    executor = GridExecutor.from_config()
    signal = load_sample_signal("L2", "bullish")
    result = executor.execute(signal, dry_run=True)
    actions = result["actions_taken"]

    assert any("place_grid(buy_limit" in a for a in actions), actions
    assert any("reduce_reverse_positions(100%)" in a for a in actions), actions


def test_l1_no_reduce_or_place() -> None:
    executor = GridExecutor.from_config()
    signal = load_sample_signal("L1", "bearish")
    result = executor.execute(signal, dry_run=True)
    actions = result["actions_taken"]

    assert not any("reduce_reverse_positions" in a for a in actions), actions
    assert not any("place_grid" in a for a in actions), actions


if __name__ == "__main__":
    test_l2_bearish_closes_reverse_and_places_sell_grid()
    test_l2_bullish_places_buy_grid()
    test_l1_no_reduce_or_place()
    print("ok: grid_executor logic tests passed")
