"""风控状态机 v1：常规 / 预警 / 紧急。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RiskState(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    EMERGENCY = "emergency"


@dataclass
class RiskStateResult:
    state: RiskState
    allow_new_orders: bool
    allow_place_grid: bool
    lot_multiplier: float
    max_new_layers: int | None
    reasons: list[str]
    spacing_multiplier: float = 1.0
    cancel_all_pending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "allow_new_orders": self.allow_new_orders,
            "allow_place_grid": self.allow_place_grid,
            "lot_multiplier": self.lot_multiplier,
            "max_new_layers": self.max_new_layers,
            "spacing_multiplier": self.spacing_multiplier,
            "cancel_all_pending": self.cancel_all_pending,
            "reasons": self.reasons,
        }


class RiskStateMachine:
    """根据账户、信号等级、风控阈值判定执行模式。"""

    def __init__(
        self,
        max_drawdown_pct: float = 20.0,
        margin_limit_pct: float = 50.0,
        warning_drawdown_ratio: float = 0.7,
        warning_margin_ratio: float = 0.8,
        *,
        overlay_enabled: bool = True,
        warning_margin_level: float = 80.0,
        emergency_margin_level: float = 65.0,
        warning_spacing_factor: float = 1.2,
        warning_lot_multiplier: float = 0.5,
        warning_max_layers_factor: float = 0.5,
    ) -> None:
        self.max_drawdown_pct = max_drawdown_pct
        self.margin_limit_pct = margin_limit_pct
        self.warning_drawdown_ratio = warning_drawdown_ratio
        self.warning_margin_ratio = warning_margin_ratio
        self.overlay_enabled = overlay_enabled
        self.warning_margin_level = warning_margin_level
        self.emergency_margin_level = emergency_margin_level
        self.warning_spacing_factor = warning_spacing_factor
        self.warning_lot_multiplier = warning_lot_multiplier
        self.warning_max_layers_factor = warning_max_layers_factor

    @classmethod
    def from_config(cls, config_path: str = "config/config.yaml") -> RiskStateMachine:
        from pathlib import Path

        import yaml

        from src.backtest_adapter.risk_overlay import load_sim_risk_overlay_config

        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)
        risk_cfg = config.get("risk_control", {})
        overlay = load_sim_risk_overlay_config(config)
        return cls(
            max_drawdown_pct=float(risk_cfg.get("max_drawdown_pct", 20)),
            margin_limit_pct=float(risk_cfg.get("margin_limit_pct", 50)),
            warning_drawdown_ratio=float(risk_cfg.get("warning_drawdown_ratio", 0.7)),
            warning_margin_ratio=float(risk_cfg.get("warning_margin_ratio", 0.8)),
            overlay_enabled=overlay.enabled,
            warning_margin_level=overlay.warning_margin_level,
            emergency_margin_level=overlay.emergency_margin_level,
            warning_spacing_factor=overlay.warning_spacing_factor,
            warning_lot_multiplier=overlay.warning_lot_multiplier,
            warning_max_layers_factor=overlay.warning_max_layers_factor,
        )

    def evaluate(
        self,
        signal: dict[str, Any],
        account_info: dict[str, Any] | None = None,
    ) -> RiskStateResult:
        reasons: list[str] = []
        level = signal.get("impact_analysis", {}).get("trend_level", "L1")
        trade = signal.get("trade_advice", {})

        state = RiskState.NORMAL
        allow_new = not trade.get("pause_all_new_orders", False)
        allow_grid = allow_new and level not in ("L4",)
        lot_mult = 1.0
        spacing_mult = 1.0
        cancel_all = False
        max_layers: int | None = trade.get("max_positions")

        if level == "L4":
            state = RiskState.EMERGENCY
            allow_new = False
            allow_grid = False
            reasons.append("L4 极强信号：禁止新增挂单")

        if account_info:
            balance = float(account_info.get("balance", 0))
            equity = float(account_info.get("equity", 0))
            margin = float(account_info.get("margin", 0))
            peak_equity = float(account_info.get("peak_equity", max(balance, equity)))

            if self.overlay_enabled and margin > 0 and equity > 0:
                margin_level = equity / margin * 100.0
                drawdown_pct = 0.0
                if peak_equity > 0:
                    drawdown_pct = max(0.0, (peak_equity - equity) / peak_equity * 100.0)
                warn_dd = self.max_drawdown_pct * self.warning_drawdown_ratio

                if margin_level <= self.emergency_margin_level:
                    state = RiskState.EMERGENCY
                    allow_new = False
                    allow_grid = False
                    cancel_all = True
                    spacing_mult = self.warning_spacing_factor
                    reasons.append(
                        f"margin_level {margin_level:.0f}% ≤ {self.emergency_margin_level:.0f}%"
                    )
                elif margin_level <= self.warning_margin_level or drawdown_pct >= warn_dd:
                    if state != RiskState.EMERGENCY:
                        state = RiskState.WARNING
                    allow_new = False
                    allow_grid = False
                    lot_mult = min(lot_mult, self.warning_lot_multiplier)
                    spacing_mult = self.warning_spacing_factor
                    if max_layers:
                        max_layers = max(1, int(max_layers * self.warning_max_layers_factor))
                    if margin_level <= self.warning_margin_level:
                        reasons.append(
                            f"margin_level {margin_level:.0f}% ≤ {self.warning_margin_level:.0f}%"
                        )
                    if drawdown_pct >= warn_dd:
                        reasons.append(f"峰值回撤 {drawdown_pct:.1f}% ≥ {warn_dd:.1f}%")
            else:
                if balance > 0:
                    dd = (balance - equity) / balance * 100
                    if dd >= self.max_drawdown_pct:
                        state = RiskState.EMERGENCY
                        allow_new = False
                        allow_grid = False
                        reasons.append(f"回撤 {dd:.1f}% ≥ 上限 {self.max_drawdown_pct}%")
                    elif dd >= self.max_drawdown_pct * self.warning_drawdown_ratio:
                        if state != RiskState.EMERGENCY:
                            state = RiskState.WARNING
                        lot_mult = min(lot_mult, 0.5)
                        if max_layers:
                            max_layers = max(1, int(max_layers * 0.5))
                        reasons.append(f"回撤 {dd:.1f}% 接近上限")

                if equity > 0 and margin > 0:
                    margin_pct = margin / equity * 100
                    if margin_pct >= self.margin_limit_pct:
                        state = RiskState.EMERGENCY
                        allow_new = False
                        allow_grid = False
                        reasons.append(f"保证金占用 {margin_pct:.1f}% ≥ 上限")
                    elif margin_pct >= self.margin_limit_pct * self.warning_margin_ratio:
                        if state != RiskState.EMERGENCY:
                            state = RiskState.WARNING
                        lot_mult = min(lot_mult, 0.5)
                        reasons.append(f"保证金占用 {margin_pct:.1f}% 偏高")

        if level == "L3" and state == RiskState.NORMAL:
            state = RiskState.WARNING
            allow_grid = False
            reasons.append("L3 强信号：仅删单/减仓，不新增网格")

        if trade.get("pause_all_new_orders"):
            allow_new = False
            allow_grid = False

        return RiskStateResult(
            state=state,
            allow_new_orders=allow_new,
            allow_place_grid=allow_grid,
            lot_multiplier=lot_mult,
            max_new_layers=max_layers,
            spacing_multiplier=spacing_mult,
            cancel_all_pending=cancel_all,
            reasons=reasons,
        )
