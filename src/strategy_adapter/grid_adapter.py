"""根据新闻趋势等级生成网格策略调整建议。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

import yaml

CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class PriceShockConfig:
    enabled: bool = True
    conflict_threshold: float = 35.0
    align_boost: float = 0.1
    neutral_widen_only: bool = True  # 中性新闻只放宽间距，保留 pause_all_new


def _momentum_conflicts(direction: str, momentum: float, threshold: float) -> bool:
    return (
        (direction == "bullish" and momentum < -threshold)
        or (direction == "bearish" and momentum > threshold)
    )


def _momentum_aligns(direction: str, momentum: float, threshold: float) -> bool:
    return (
        (direction == "bullish" and momentum > threshold)
        or (direction == "bearish" and momentum < -threshold)
    )


def apply_price_shock_adjustment(
    level_cfg: dict[str, Any],
    direction: str,
    momentum: float,
    cfg: PriceShockConfig | None = None,
) -> tuple[dict[str, Any], str]:
    """按价格动量与新闻方向关系微调冲击策略参数。"""
    cfg = cfg or PriceShockConfig()
    if not cfg.enabled or abs(momentum) < 1:
        return level_cfg, ""

    has_shock = level_cfg["pause_reverse"] or level_cfg["pause_all_new"]

    if direction == "neutral":
        # 宏观日程无词典方向：强动量时仅放宽间距，保留 L3 停单保护
        if abs(momentum) > cfg.conflict_threshold and has_shock:
            adjusted = dict(level_cfg)
            adjusted["spacing_factor"] = min(1.0, level_cfg["spacing_factor"] * 1.12)
            if not cfg.neutral_widen_only:
                adjusted["pause_reverse"] = False
                adjusted["pause_all_new"] = False
                adjusted["reverse_ratio"] = 1.0
                adjusted["max_positions_factor"] = min(1.0, level_cfg["max_positions_factor"] + 0.1)
            return adjusted, f"中性新闻+动量{momentum:+.0f}→放宽间距"
        return level_cfg, ""

    adjusted = dict(level_cfg)
    note = ""

    if _momentum_conflicts(direction, momentum, cfg.conflict_threshold):
        adjusted["spacing_factor"] = min(1.0, level_cfg["spacing_factor"] * 1.2)
        adjusted["pause_reverse"] = False
        adjusted["pause_all_new"] = False
        adjusted["reverse_ratio"] = 1.0
        adjusted["max_positions_factor"] = min(1.0, level_cfg["max_positions_factor"] + 0.15)
        note = f"动量{momentum:+.0f}与{direction}冲突→降级冲击"
    elif _momentum_aligns(direction, momentum, cfg.conflict_threshold):
        adjusted["spacing_factor"] = max(0.4, level_cfg["spacing_factor"] * (1 - cfg.align_boost))
        adjusted["max_positions_factor"] = min(1.0, level_cfg["max_positions_factor"] + cfg.align_boost)
        note = f"动量{momentum:+.0f}与{direction}一致→略收紧"

    return adjusted, note


def load_price_shock_config(config: dict[str, Any]) -> PriceShockConfig:
    grid_cfg = config.get("grid_strategy", {})
    return PriceShockConfig(
        enabled=bool(grid_cfg.get("price_shock_enabled", True)),
        conflict_threshold=float(grid_cfg.get("momentum_conflict_threshold", 35)),
        align_boost=float(grid_cfg.get("momentum_align_boost", 0.1)),
        neutral_widen_only=bool(grid_cfg.get("neutral_widen_only", True)),
    )


LEVEL_ACTIONS = {
    "L1": {
        "action": "maintain",
        "action_cn": "维持现有挂单",
        "pause_reverse": False,
        "pause_all_new": False,
        "spacing_factor": 1.0,
        "reverse_ratio": 1.0,
        "max_positions_factor": 1.0,
    },
    "L2": {
        "action": "reduce_reverse",
        "action_cn": "暂停反向挂单，保留顺势挂单",
        "pause_reverse": True,
        "pause_all_new": False,
        "spacing_factor": 0.8,
        "reverse_ratio": 0.0,
        "max_positions_factor": 0.8,
    },
    "L3": {
        "action": "aggressive_defense",
        "action_cn": "暂停新增挂单，缩减50%反向仓位",
        "pause_reverse": True,
        "pause_all_new": True,
        "spacing_factor": 0.6,
        "reverse_ratio": 0.5,
        "max_positions_factor": 0.5,
    },
    "L4": {
        "action": "full_pause",
        "action_cn": "全部挂单暂停，转趋势跟踪或空仓观望",
        "pause_reverse": True,
        "pause_all_new": True,
        "spacing_factor": 0.5,
        "reverse_ratio": 0.0,
        "max_positions_factor": 0.3,
    },
}


@dataclass
class GridAdvice:
    news_id: str
    direction: str
    direction_cn: str
    trend_level: str
    trend_level_cn: str
    composite_score: float
    confidence: float
    action: str
    action_cn: str
    grid_spacing: float
    spacing_factor: float
    atr_factor: float
    pause_reverse: bool
    pause_all_new: bool
    reverse_ratio: float
    max_positions: int
    default_lot: float
    reasoning: str
    generated_at: str
    duration_label: str = ""
    duration_hours: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "news_id": self.news_id,
            "generated_at": self.generated_at,
            "impact_analysis": {
                "direction": self.direction,
                "direction_cn": self.direction_cn,
                "trend_level": self.trend_level,
                "trend_level_cn": self.trend_level_cn,
                "composite_score": round(self.composite_score, 2),
                "confidence": round(self.confidence, 3),
                "duration_label": self.duration_label,
                "duration_hours": self.duration_hours,
            },
            "trade_advice": {
                "action": self.action,
                "action_cn": self.action_cn,
                "grid_spacing": round(self.grid_spacing, 2),
                "spacing_factor": self.spacing_factor,
                "atr_factor": round(self.atr_factor, 4),
                "pause_reverse_orders": self.pause_reverse,
                "pause_all_new_orders": self.pause_all_new,
                "reverse_position_ratio": self.reverse_ratio,
                "max_positions": self.max_positions,
                "default_lot": self.default_lot,
                "reasoning": self.reasoning,
            },
        }


class GridAdapter:
    """将趋势评分转化为网格交易调整参数。

    间距公式：grid_spacing = base_spacing × atr_factor × trend_spacing_factor
    """

    def __init__(
        self,
        base_spacing: float = 2.0,
        max_layers: int = 10,
        default_lot: float = 0.01,
        price_shock_cfg: PriceShockConfig | None = None,
    ) -> None:
        self.base_spacing = base_spacing
        self.max_layers = max_layers
        self.default_lot = default_lot
        self.price_shock_cfg = price_shock_cfg or PriceShockConfig()

    @classmethod
    def from_config(cls, config_path: str = "config/config.yaml") -> GridAdapter:
        from pathlib import Path

        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        grid_cfg = config.get("grid_strategy", {})
        return cls(
            base_spacing=float(grid_cfg.get("base_spacing", 2.0)),
            max_layers=int(grid_cfg.get("max_layers", 10)),
            default_lot=float(grid_cfg.get("default_lot", 0.01)),
            price_shock_cfg=load_price_shock_config(config),
        )

    def adapt(
        self,
        sentiment_data: dict[str, Any],
        atr_factor: float = 1.0,
    ) -> GridAdvice:
        trend = sentiment_data.get("trend_score", {})
        sentiment = sentiment_data.get("sentiment", {})
        news = sentiment_data.get("news", {})
        duration = sentiment_data.get("duration") or {}

        level = trend.get("trend_level", "L1")
        level_cfg = LEVEL_ACTIONS.get(level, LEVEL_ACTIONS["L1"])

        direction = trend.get("direction", "neutral")
        direction_cn = trend.get("direction_cn", "中性")
        composite = float(trend.get("composite_score", 0))
        confidence = float(sentiment.get("confidence", 0.5))
        factors = trend.get("factors") or {}
        momentum = float(trend.get("price_momentum") or factors.get("price_momentum", 0))

        level_cfg, shock_note = apply_price_shock_adjustment(
            level_cfg, direction, momentum, self.price_shock_cfg,
        )

        trend_spacing = level_cfg["spacing_factor"]
        spacing = self.base_spacing * atr_factor * trend_spacing
        max_pos = max(1, int(self.max_layers * level_cfg["max_positions_factor"]))

        reasoning = self._build_reasoning(news, trend, level_cfg, atr_factor, duration)
        if shock_note:
            reasoning += f" {shock_note}。"

        return GridAdvice(
            news_id=sentiment_data.get("news_id", ""),
            direction=direction,
            direction_cn=direction_cn,
            trend_level=level,
            trend_level_cn=trend.get("trend_level_cn", ""),
            composite_score=composite,
            confidence=confidence,
            action=level_cfg["action"],
            action_cn=level_cfg["action_cn"],
            grid_spacing=spacing,
            spacing_factor=trend_spacing,
            atr_factor=atr_factor,
            pause_reverse=level_cfg["pause_reverse"],
            pause_all_new=level_cfg["pause_all_new"],
            reverse_ratio=level_cfg["reverse_ratio"],
            max_positions=max_pos,
            default_lot=self.default_lot,
            reasoning=reasoning,
            generated_at=datetime.now(CHINA_TZ).isoformat(),
            duration_label=str(duration.get("duration_label", "")),
            duration_hours=int(duration.get("duration_hours", 0)),
        )

    def _build_reasoning(
        self,
        news: dict,
        trend: dict,
        level_cfg: dict,
        atr_factor: float,
        duration: dict,
    ) -> str:
        title = news.get("title", "")
        level = trend.get("trend_level", "L1")
        direction_cn = trend.get("direction_cn", "中性")
        score = trend.get("composite_score", 0)
        dur_note = ""
        if duration.get("duration_hours"):
            dur_note = f"，预计影响约 {duration['duration_hours']} 小时（{duration.get('duration_label', '')}）"
        return (
            f"新闻「{title}」综合评分 {score:.1f}，方向 {direction_cn}，"
            f"趋势等级 {level}{dur_note}。建议：{level_cfg['action_cn']}，"
            f"间距 = 基准 {self.base_spacing} × ATR因子 {atr_factor:.2f} × 趋势因子 {level_cfg['spacing_factor']:.0%}。"
        )
