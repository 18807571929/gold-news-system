"""新闻冲击策略：复用 GridAdapter L1-L4 规则。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.strategy_adapter.grid_adapter import LEVEL_ACTIONS


@dataclass
class ShockPolicy:
    news_id: str
    direction: str
    trend_level: str
    grid_spacing: float
    spacing_factor: float
    pause_reverse: bool
    pause_all_new: bool
    reverse_ratio: float
    max_positions: int
    default_lot: float
    composite_score: float = 0.0
    price_momentum: float = 0.0
    title: str = ""

    @classmethod
    def from_signal(cls, signal: dict[str, Any]) -> ShockPolicy:
        level = signal.get("trend_level", "L1")
        cfg = LEVEL_ACTIONS.get(level, LEVEL_ACTIONS["L1"])
        spacing = float(signal.get("grid_spacing", 2.0))
        return cls(
            news_id=str(signal.get("news_id", "")),
            direction=str(signal.get("direction", "neutral")),
            trend_level=level,
            grid_spacing=spacing,
            spacing_factor=float(signal.get("spacing_factor", cfg["spacing_factor"])),
            pause_reverse=bool(signal.get("pause_reverse_orders", cfg["pause_reverse"])),
            pause_all_new=bool(signal.get("pause_all_new_orders", cfg["pause_all_new"])),
            reverse_ratio=float(signal.get("reverse_position_ratio", cfg["reverse_ratio"])),
            max_positions=int(signal.get("max_positions", 5)),
            default_lot=float(signal.get("default_lot", 0.01)),
            composite_score=float(signal.get("composite_score", 0)),
            price_momentum=float(signal.get("price_momentum", 0)),
            title=str(signal.get("title", "")),
        )

    @classmethod
    def neutral(cls, spacing: float = 2.0) -> ShockPolicy:
        return cls(
            news_id="",
            direction="neutral",
            trend_level="L1",
            grid_spacing=spacing,
            spacing_factor=1.0,
            pause_reverse=False,
            pause_all_new=False,
            reverse_ratio=1.0,
            max_positions=5,
            default_lot=0.01,
        )


LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}


def merge_policies(current: ShockPolicy | None, incoming: ShockPolicy) -> ShockPolicy:
    """重叠窗口取更强等级；同等级保留 incoming。"""
    if current is None:
        return incoming
    if LEVEL_RANK.get(incoming.trend_level, 0) >= LEVEL_RANK.get(current.trend_level, 0):
        return incoming
    return current
