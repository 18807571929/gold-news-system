"""风控校验：执行交易建议前的安全检查。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    passed: bool
    warnings: list[str]
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "warnings": self.warnings,
            "blocked_reason": self.blocked_reason,
        }


class RiskManager:
    """四维风控矩阵基础校验。"""

    def __init__(
        self,
        max_drawdown_pct: float = 20.0,
        max_positions: int = 5,
        margin_limit_pct: float = 50.0,
    ) -> None:
        self.max_drawdown_pct = max_drawdown_pct
        self.max_positions = max_positions
        self.margin_limit_pct = margin_limit_pct

    @classmethod
    def from_config(cls, config_path: str = "config/config.yaml") -> RiskManager:
        from pathlib import Path

        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        risk_cfg = config.get("risk_control", {})
        return cls(
            max_drawdown_pct=float(risk_cfg.get("max_drawdown_pct", 20)),
            max_positions=int(risk_cfg.get("max_positions", 5)),
            margin_limit_pct=float(risk_cfg.get("margin_limit_pct", 50)),
        )

    def check(
        self,
        advice: dict[str, Any],
        account_info: dict[str, Any] | None = None,
        current_positions: int = 0,
    ) -> RiskCheckResult:
        warnings: list[str] = []
        trade = advice.get("trade_advice", {})

        if current_positions >= self.max_positions:
            return RiskCheckResult(
                passed=False,
                warnings=warnings,
                blocked_reason=f"持仓数 {current_positions} 已达上限 {self.max_positions}",
            )

        advice_max_pos = trade.get("max_positions", self.max_positions)
        if advice_max_pos > self.max_positions:
            warnings.append(f"建议最大层数 {advice_max_pos} 超过风控上限，将截断为 {self.max_positions}")

        if account_info:
            balance = account_info.get("balance", 0)
            equity = account_info.get("equity", 0)
            margin = account_info.get("margin", 0)

            if balance > 0:
                drawdown_pct = (balance - equity) / balance * 100
                if drawdown_pct >= self.max_drawdown_pct:
                    return RiskCheckResult(
                        passed=False,
                        warnings=warnings,
                        blocked_reason=f"回撤 {drawdown_pct:.1f}% 超过上限 {self.max_drawdown_pct}%",
                    )
                if drawdown_pct >= self.max_drawdown_pct * 0.7:
                    warnings.append(f"回撤 {drawdown_pct:.1f}% 接近上限")

            if equity > 0 and margin > 0:
                margin_pct = margin / equity * 100
                if margin_pct >= self.margin_limit_pct:
                    return RiskCheckResult(
                        passed=False,
                        warnings=warnings,
                        blocked_reason=f"保证金占用 {margin_pct:.1f}% 超过上限 {self.margin_limit_pct}%",
                    )
                if margin_pct >= self.margin_limit_pct * 0.8:
                    warnings.append(f"保证金占用 {margin_pct:.1f}% 偏高")

        level = advice.get("impact_analysis", {}).get("trend_level", "L1")
        if level == "L4":
            warnings.append("L4 极强信号：建议暂停全部网格交易")

        return RiskCheckResult(passed=True, warnings=warnings)
