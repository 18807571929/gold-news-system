"""网格执行器：删反向挂单、减仓、网格止盈、按间距布顺势网格。"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from src.mt5_bridge.connector import MT5Connector
from src.risk_manager.risk_state import RiskState, RiskStateMachine

logger = logging.getLogger(__name__)


class GridExecutor:
    """将 trade_advice 转化为 MT5 具体操作。"""

    def __init__(
        self,
        connector: MT5Connector,
        risk_state_machine: RiskStateMachine | None = None,
        auto_place_orders: bool = True,
        magic: int = 20260711,
        comment_prefix: str = "gold-news",
        price_tolerance: float = 0.05,
        grid_take_profit_enabled: bool = True,
    ) -> None:
        self.connector = connector
        self.risk_state_machine = risk_state_machine or RiskStateMachine()
        self.auto_place_orders = auto_place_orders
        self.magic = magic
        self.comment_prefix = comment_prefix
        self.price_tolerance = price_tolerance
        self.grid_take_profit_enabled = grid_take_profit_enabled

    @classmethod
    def from_config(
        cls,
        connector: MT5Connector | None = None,
        config_path: str = "config/config.yaml",
    ) -> GridExecutor:
        from pathlib import Path

        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        grid_cfg = config.get("grid_strategy", {})
        mt5_cfg = config.get("mt5", {})
        return cls(
            connector=connector or MT5Connector.from_config(config_path),
            risk_state_machine=RiskStateMachine.from_config(config_path),
            auto_place_orders=bool(grid_cfg.get("auto_place_orders", True)),
            magic=int(mt5_cfg.get("magic", 20260711)),
            comment_prefix=str(mt5_cfg.get("comment_prefix", "gold-news")),
            price_tolerance=float(grid_cfg.get("price_tolerance", 0.05)),
            grid_take_profit_enabled=bool(grid_cfg.get("grid_take_profit_enabled", True)),
        )

    def execute(
        self,
        signal: dict[str, Any],
        *,
        dry_run: bool = False,
        account_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行信号：止盈 → 删单 → 减/平反向仓 → 布网格。"""
        result: dict[str, Any] = {
            "executed": False,
            "dry_run": dry_run,
            "actions_taken": [],
        }

        trade = signal.get("trade_advice", {})
        impact = signal.get("impact_analysis", {})
        direction = impact.get("direction", "neutral")
        level = impact.get("trend_level", "L1")
        spacing = float(trade.get("grid_spacing", 2.0))

        risk_state = self.risk_state_machine.evaluate(signal, account_info)
        result["risk_state"] = risk_state.to_dict()

        if dry_run:
            result["actions_taken"] = self._plan_actions(signal, risk_state)
            result["executed"] = True
            logger.info("[DRY RUN] 计划操作: %s", result["actions_taken"])
            return result

        if not self.connector.connect():
            result["error"] = "MT5 连接失败"
            return result

        try:
            actions: list[str] = []
            eff_spacing = spacing * risk_state.spacing_multiplier

            # 0. 网格止盈（对齐回测：entry ± spacing）
            if self.grid_take_profit_enabled:
                actions.extend(self._process_grid_take_profits(eff_spacing))

            # 1. 取消挂单
            if risk_state.cancel_all_pending:
                actions.extend(self._cancel_all_pending())
            else:
                actions.extend(self._cancel_pending_orders(trade, direction, risk_state))

            # 2. 反向仓位减仓/平仓（L2/L3/L4 均按 reverse_ratio 执行）
            reverse_ratio = float(trade.get("reverse_position_ratio", 1.0))
            if direction != "neutral" and reverse_ratio < 1.0:
                actions.extend(self._reduce_reverse_positions(direction, reverse_ratio))

            # L4 紧急：取消全部挂单（已在 step1 若 pause_all）
            if risk_state.state == RiskState.EMERGENCY and level == "L4":
                actions.extend(self._cancel_all_pending())

            # 3. L2 布顺势网格（L1 维持、L3/L4 不新增）
            if self.auto_place_orders and risk_state.allow_place_grid and level == "L2":
                actions.extend(self._place_trend_grid(signal, risk_state))

            tick = self.connector.get_tick()
            if tick and tick.bid > 0:
                result["current_price"] = {"bid": tick.bid, "ask": tick.ask, "symbol": tick.symbol}

            acct = self.connector.get_account_info()
            if acct:
                result["account"] = {
                    "login": acct.login,
                    "equity": acct.equity,
                    "balance": acct.balance,
                }

            result["positions_count"] = len(self._our_positions())
            result["pending_count"] = len(self._our_pending_orders())
            result["actions_taken"] = actions
            result["executed"] = True
        finally:
            self.connector.disconnect()

        return result

    def _our_positions(self) -> list[dict[str, Any]]:
        return [p for p in self.connector.get_positions() if p.get("magic") == self.magic]

    def _our_pending_orders(self) -> list[dict[str, Any]]:
        return [o for o in self.connector.get_pending_orders() if o.get("magic") == self.magic]

    def _plan_actions(self, signal: dict[str, Any], risk_state: Any) -> list[str]:
        trade = signal.get("trade_advice", {})
        impact = signal.get("impact_analysis", {})
        direction = impact.get("direction", "neutral")
        level = impact.get("trend_level", "L1")
        spacing = float(trade.get("grid_spacing", 2.0)) * risk_state.spacing_multiplier
        actions: list[str] = []

        if self.grid_take_profit_enabled:
            actions.append(f"grid_take_profit(spacing={spacing:.2f})")

        if risk_state.cancel_all_pending:
            actions.append("cancel_all_pending(margin_emergency)")
        elif trade.get("pause_all_new_orders"):
            actions.append("cancel_all_pending")
        elif trade.get("pause_reverse_orders"):
            actions.append(f"cancel_reverse_pending({direction})")

        reverse_ratio = float(trade.get("reverse_position_ratio", 1.0))
        if direction != "neutral" and reverse_ratio < 1.0:
            pct = int((1 - reverse_ratio) * 100)
            actions.append(f"reduce_reverse_positions({pct}%)")

        if self.auto_place_orders and risk_state.allow_place_grid and level == "L2" and direction != "neutral":
            max_pos = risk_state.max_new_layers or trade.get("max_positions", 3)
            lot = float(trade.get("default_lot", 0.01)) * risk_state.lot_multiplier
            side = "buy_limit" if direction == "bullish" else "sell_limit"
            actions.append(
                f"place_grid({side}, spacing={spacing:.2f}, layers={max_pos}, lot={lot:.2f}, tp=entry±spacing)"
            )

        if not actions:
            actions.append("no_action")
        return actions

    def _is_reverse_order(self, order_type: str, direction: str) -> bool:
        if direction == "bullish":
            return "sell" in order_type
        if direction == "bearish":
            return "buy" in order_type
        return False

    def _is_trend_order(self, order_type: str, direction: str) -> bool:
        if direction == "bullish":
            return "buy" in order_type
        if direction == "bearish":
            return "sell" in order_type
        return False

    def _is_reverse_position(self, pos_type: str, direction: str) -> bool:
        return (
            (direction == "bullish" and pos_type == "sell")
            or (direction == "bearish" and pos_type == "buy")
        )

    def _tp_price(self, pos: dict[str, Any], spacing: float) -> float:
        entry = float(pos["price_open"])
        if pos["type"] == "buy":
            return self.connector.normalize_price(entry + spacing)
        return self.connector.normalize_price(entry - spacing)

    def _tp_reached(self, pos: dict[str, Any], spacing: float, bid: float, ask: float) -> bool:
        tp = self._tp_price(pos, spacing)
        if pos["type"] == "buy":
            return bid >= tp - self.price_tolerance
        return ask <= tp + self.price_tolerance

    def _process_grid_take_profits(self, spacing: float) -> list[str]:
        """网格止盈：市价已触及则平仓；否则为缺失 TP 的持仓/挂单补设止盈。"""
        actions: list[str] = []
        if spacing <= 0:
            return actions

        tick = self.connector.get_tick()
        if not tick or tick.bid <= 0:
            actions.append("take_profit:skipped_no_tick")
            return actions

        for pos in self._our_positions():
            if self._tp_reached(pos, spacing, tick.bid, tick.ask):
                res = self.connector.close_position(
                    pos["ticket"],
                    magic=self.magic,
                    comment=f"{self.comment_prefix}-tp",
                )
                actions.append(
                    f"take_profit_close_{pos['ticket']}:{'ok' if res.get('ok') else res.get('error')}"
                )
                continue

            if float(pos.get("tp") or 0) <= 0:
                tp = self._tp_price(pos, spacing)
                res = self.connector.modify_position_sltp(pos["ticket"], tp=tp)
                actions.append(
                    f"set_tp_{pos['ticket']}@{tp}:{'ok' if res.get('ok') else res.get('error')}"
                )

        for order in self._our_pending_orders():
            if float(order.get("tp") or 0) > 0:
                continue
            entry = float(order["price_open"])
            otype = order["type"]
            if "buy" in otype:
                tp = self.connector.normalize_price(entry + spacing)
            elif "sell" in otype:
                tp = self.connector.normalize_price(entry - spacing)
            else:
                continue
            res = self.connector.modify_order_sltp(order["ticket"], tp=tp)
            actions.append(
                f"set_pending_tp_{order['ticket']}@{tp}:{'ok' if res.get('ok') else res.get('error')}"
            )

        return actions

    def _cancel_pending_orders(
        self,
        trade: dict[str, Any],
        direction: str,
        risk_state: Any,
    ) -> list[str]:
        actions: list[str] = []
        pending = self._our_pending_orders()

        for order in pending:
            should_cancel = False
            if trade.get("pause_all_new_orders") or risk_state.state == RiskState.EMERGENCY:
                should_cancel = True
            elif trade.get("pause_reverse_orders"):
                should_cancel = self._is_reverse_order(order["type"], direction)

            if should_cancel:
                ok = self.connector.delete_order(order["ticket"])
                actions.append(f"cancel_order_{order['ticket']}:{'ok' if ok else 'fail'}")

        return actions

    def _cancel_all_pending(self) -> list[str]:
        actions: list[str] = []
        for order in self._our_pending_orders():
            ok = self.connector.delete_order(order["ticket"])
            actions.append(f"cancel_all_{order['ticket']}:{'ok' if ok else 'fail'}")
        return actions

    def _reduce_reverse_positions(self, direction: str, reverse_ratio: float) -> list[str]:
        """关闭部分或全部反向持仓。reverse_ratio=0 表示全平逆势仓。"""
        actions: list[str] = []
        close_fraction = 1.0 - reverse_ratio
        if close_fraction <= 0:
            return actions

        for pos in self._our_positions():
            if not self._is_reverse_position(pos["type"], direction):
                continue

            close_vol = pos["volume"] * close_fraction
            res = self.connector.close_position(
                pos["ticket"],
                volume=close_vol,
                magic=self.magic,
                comment=f"{self.comment_prefix}-reduce",
            )
            actions.append(
                f"close_reverse_{pos['ticket']}:{'ok' if res.get('ok') else res.get('error')}"
            )
        return actions

    def _existing_prices(self, direction: str) -> set[float]:
        prices: set[float] = set()
        for order in self._our_pending_orders():
            if self._is_trend_order(order["type"], direction):
                prices.add(round(order["price_open"], 2))
        return prices

    def _price_exists(self, price: float, existing: set[float]) -> bool:
        return any(abs(price - p) <= self.price_tolerance for p in existing)

    def _place_trend_grid(self, signal: dict[str, Any], risk_state: Any) -> list[str]:
        actions: list[str] = []
        trade = signal.get("trade_advice", {})
        impact = signal.get("impact_analysis", {})
        direction = impact.get("direction", "neutral")

        if direction == "neutral":
            return actions

        spacing = float(trade.get("grid_spacing", 2.0)) * risk_state.spacing_multiplier
        max_layers = int(risk_state.max_new_layers or trade.get("max_positions", 3))
        lot = float(trade.get("default_lot", 0.01)) * risk_state.lot_multiplier

        tick = self.connector.get_tick()
        if not tick or tick.bid <= 0:
            actions.append("place_grid:skipped_no_tick")
            return actions

        existing = self._existing_prices(direction)
        placed = 0

        if direction == "bullish":
            order_type = "buy_limit"
            anchor = tick.bid
            for layer in range(1, max_layers + 1):
                price = self.connector.normalize_price(anchor - spacing * layer)
                if self._price_exists(price, existing):
                    continue
                tp = self.connector.normalize_price(price + spacing)
                res = self.connector.place_pending_order(
                    order_type,
                    price,
                    lot,
                    magic=self.magic,
                    comment=f"{self.comment_prefix}-L{impact.get('trend_level', 'L1')}",
                    tp=tp,
                )
                if res.get("ok"):
                    placed += 1
                    existing.add(price)
                    actions.append(f"place_{order_type}_{res['ticket']}@{price}_tp@{tp}")
                else:
                    actions.append(f"place_{order_type}_fail:{res.get('error')}")
        else:
            order_type = "sell_limit"
            anchor = tick.ask
            for layer in range(1, max_layers + 1):
                price = self.connector.normalize_price(anchor + spacing * layer)
                if self._price_exists(price, existing):
                    continue
                tp = self.connector.normalize_price(price - spacing)
                res = self.connector.place_pending_order(
                    order_type,
                    price,
                    lot,
                    magic=self.magic,
                    comment=f"{self.comment_prefix}-L{impact.get('trend_level', 'L1')}",
                    tp=tp,
                )
                if res.get("ok"):
                    placed += 1
                    existing.add(price)
                    actions.append(f"place_{order_type}_{res['ticket']}@{price}_tp@{tp}")
                else:
                    actions.append(f"place_{order_type}_fail:{res.get('error')}")

        if placed == 0 and not actions:
            actions.append("place_grid:no_new_layers")
        return actions
