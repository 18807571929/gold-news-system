"""回测用 margin / 回撤预警 overlay（对齐 risk_control 配置）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SimRiskOverlayConfig:
    enabled: bool = True
    warning_margin_level: float = 80.0
    emergency_margin_level: float = 65.0
    max_drawdown_pct: float = 20.0
    warning_drawdown_ratio: float = 0.7
    warning_spacing_factor: float = 1.2
    warning_lot_multiplier: float = 0.5
    warning_max_layers_factor: float = 0.5


@dataclass
class SimRiskDecision:
    state: str = "normal"
    allow_new_orders: bool = True
    cancel_pending: bool = False
    spacing_factor: float = 1.0
    lot_multiplier: float = 1.0
    max_layers_factor: float = 1.0
    reason: str = ""


def load_sim_risk_overlay_config(config: dict[str, Any]) -> SimRiskOverlayConfig:
    bt = config.get("backtest", {})
    risk = config.get("risk_control", {})
    return SimRiskOverlayConfig(
        enabled=bool(bt.get("risk_overlay_enabled", True)),
        warning_margin_level=float(bt.get("warning_margin_level", 80.0)),
        emergency_margin_level=float(bt.get("emergency_margin_level", 65.0)),
        max_drawdown_pct=float(risk.get("max_drawdown_pct", 20.0)),
        warning_drawdown_ratio=float(risk.get("warning_drawdown_ratio", 0.7)),
        warning_spacing_factor=float(bt.get("warning_spacing_factor", 1.2)),
        warning_lot_multiplier=float(bt.get("warning_lot_multiplier", 0.5)),
        warning_max_layers_factor=float(bt.get("warning_max_layers_factor", 0.5)),
    )


def evaluate_sim_risk(
    *,
    equity: float,
    used_margin: float,
    peak_equity: float,
    cfg: SimRiskOverlayConfig,
) -> SimRiskDecision:
    if not cfg.enabled:
        return SimRiskDecision()

    margin_level = (equity / used_margin * 100.0) if used_margin > 0 else float("inf")
    drawdown_pct = 0.0
    if peak_equity > 0:
        drawdown_pct = max(0.0, (peak_equity - equity) / peak_equity * 100.0)

    warn_dd = cfg.max_drawdown_pct * cfg.warning_drawdown_ratio
    reasons: list[str] = []

    if used_margin > 0 and margin_level <= cfg.emergency_margin_level:
        reasons.append(f"margin_level {margin_level:.0f}%≤{cfg.emergency_margin_level:.0f}%")
        return SimRiskDecision(
            state="emergency",
            allow_new_orders=False,
            cancel_pending=True,
            spacing_factor=cfg.warning_spacing_factor,
            reason="; ".join(reasons),
        )

    in_warning = False
    if used_margin > 0 and margin_level <= cfg.warning_margin_level:
        in_warning = True
        reasons.append(f"margin_level {margin_level:.0f}%≤{cfg.warning_margin_level:.0f}%")
    if drawdown_pct >= warn_dd:
        in_warning = True
        reasons.append(f"drawdown {drawdown_pct:.1f}%≥{warn_dd:.1f}%")

    if in_warning:
        return SimRiskDecision(
            state="warning",
            allow_new_orders=False,
            cancel_pending=False,
            spacing_factor=cfg.warning_spacing_factor,
            lot_multiplier=cfg.warning_lot_multiplier,
            max_layers_factor=cfg.warning_max_layers_factor,
            reason="; ".join(reasons),
        )

    return SimRiskDecision()
