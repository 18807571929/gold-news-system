"""简化网格回测：baseline vs 新闻冲击适配。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from .policy import ShockPolicy
from .risk_overlay import SimRiskDecision, SimRiskOverlayConfig, evaluate_sim_risk
from .timeline import ShockTimeline

CONTRACT_OZ_PER_LOT = 100.0  # 1.0 手 = 100 盎司；0.01 手 = 1 盎司
DEFAULT_LEVERAGE = 100.0
DEFAULT_STOP_OUT_MARGIN_LEVEL = 50.0  # 保证金比例 %，低于此强平


@dataclass
class PendingOrder:
    side: str  # buy | sell
    price: float
    volume: float
    layer: int


@dataclass
class Position:
    side: str
    entry_price: float
    volume: float


@dataclass
class SimState:
    balance: float
    positions: list[Position] = field(default_factory=list)
    pending: list[PendingOrder] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    shock_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SimResult:
    mode: str
    initial_balance: float
    final_equity: float
    total_pnl: float
    max_drawdown_pct: float
    trade_count: int
    shock_actions: int
    stopped_out: bool
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    shock_events: pd.DataFrame
    risk_actions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "initial_balance": self.initial_balance,
            "final_equity": round(self.final_equity, 2),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "trade_count": self.trade_count,
            "shock_actions": self.shock_actions,
            "stopped_out": self.stopped_out,
            "risk_actions": self.risk_actions,
        }


class GridShockSimulator:
    """对称网格 + 可选新闻冲击策略的 bar 级模拟。"""

    def __init__(
        self,
        bars: pd.DataFrame,
        *,
        initial_balance: float = 10000.0,
        base_spacing: float = 2.0,
        max_layers: int = 5,
        default_lot: float = 0.01,
        leverage: float = DEFAULT_LEVERAGE,
        stop_out_margin_level: float = DEFAULT_STOP_OUT_MARGIN_LEVEL,
        timeline: ShockTimeline | None = None,
        use_news_adapter: bool = False,
        directional_grid: bool = False,
        risk_overlay: SimRiskOverlayConfig | None = None,
        spread_price: float = 0.0,
    ) -> None:
        self.bars = bars
        self.initial_balance = initial_balance
        self.base_spacing = base_spacing
        self.max_layers = max_layers
        self.default_lot = default_lot
        self.leverage = max(leverage, 1.0)
        self.stop_out_margin_level = stop_out_margin_level
        self.timeline = timeline
        self.use_news_adapter = use_news_adapter
        self.directional_grid = directional_grid
        self.risk_overlay = risk_overlay or SimRiskOverlayConfig(enabled=False)
        self.spread_price = max(0.0, float(spread_price))

    def _spread_half(self) -> float:
        return self.spread_price / 2.0

    def _entry_fill_price(self, side: str, limit_price: float) -> float:
        half = self._spread_half()
        if side == "buy":
            return limit_price + half
        return limit_price - half

    def _exit_fill_price(self, side: str, mark_price: float) -> float:
        """平多按 bid、平空按 ask。"""
        half = self._spread_half()
        if side == "buy":
            return mark_price - half
        return mark_price + half

    def run(self) -> SimResult:
        state = SimState(balance=self.initial_balance)
        mode = "news_adapter" if self.use_news_adapter else "baseline"
        shock_actions = 0
        risk_actions = 0
        activated_news: set[str] = set()
        stopped_out = False
        peak_equity = self.initial_balance

        for _, bar in self.bars.iterrows():
            ts = bar["datetime"].to_pydatetime()
            mid = (float(bar["open"]) + float(bar["close"])) / 2.0
            mark = float(bar["close"])

            policy = self.timeline.policy_at(ts) if (self.use_news_adapter and self.timeline) else None
            spacing = policy.grid_spacing if policy else self.base_spacing
            max_layers = policy.max_positions if policy else self.max_layers
            lot = policy.default_lot if policy else self.default_lot

            if policy and policy.news_id and policy.news_id not in activated_news:
                shock_actions += max(1, self._apply_shock_entry(state, policy, mark, ts))
                activated_news.add(policy.news_id)

            equity_pre = self._mark_equity(state, mark)
            used_margin_pre = self._used_margin(state, mark)
            peak_equity = max(peak_equity, equity_pre)
            risk = evaluate_sim_risk(
                equity=equity_pre,
                used_margin=used_margin_pre,
                peak_equity=peak_equity,
                cfg=self.risk_overlay,
            )

            if risk.cancel_pending and state.pending:
                risk_actions += len(state.pending)
                state.pending.clear()
                state.trades.append(
                    {
                        "datetime": ts,
                        "action": "risk_cancel_pending",
                        "side": "",
                        "volume": 0,
                        "price": mid,
                        "pnl": 0,
                        "reason": risk.reason,
                    }
                )

            eff_spacing = spacing * risk.spacing_factor
            self._process_take_profits(state, bar, ts, eff_spacing)
            self._process_fills(state, bar, ts)

            if not stopped_out and risk.allow_new_orders:
                eff_lot = max(0.001, lot * risk.lot_multiplier)
                eff_layers = max(1, int(max_layers * risk.max_layers_factor))
                self._replenish_grid(state, mid, eff_spacing, eff_layers, eff_lot, policy)

            equity = self._mark_equity(state, mid)
            used_margin = self._used_margin(state, mid)
            margin_level = (equity / used_margin * 100.0) if used_margin > 0 else float("inf")
            state.equity_curve.append(
                {
                    "datetime": ts,
                    "equity": equity,
                    "balance": state.balance,
                    "used_margin": used_margin,
                    "margin_level_pct": margin_level if margin_level != float("inf") else None,
                }
            )

            if not stopped_out and used_margin > 0 and margin_level <= self.stop_out_margin_level:
                self._liquidate_all(state, mark, ts, reason="stop_out")
                stopped_out = True
                equity = state.balance
                state.equity_curve[-1]["equity"] = equity
                state.equity_curve[-1]["balance"] = state.balance
                break

        final_mid = float(self.bars.iloc[-1]["close"])
        if stopped_out:
            final_equity = state.balance
        else:
            final_equity = self._mark_equity(state, final_mid)
        total_pnl = final_equity - self.initial_balance
        eq_df = pd.DataFrame(state.equity_curve)
        max_dd = self._max_drawdown(eq_df["equity"]) if not eq_df.empty else 0.0

        return SimResult(
            mode=mode,
            initial_balance=self.initial_balance,
            final_equity=final_equity,
            total_pnl=total_pnl,
            max_drawdown_pct=max_dd,
            trade_count=len(state.trades),
            shock_actions=shock_actions,
            stopped_out=stopped_out,
            risk_actions=risk_actions,
            equity_curve=eq_df,
            trades=pd.DataFrame(state.trades),
            shock_events=pd.DataFrame(state.shock_events),
        )

    def _process_take_profits(
        self,
        state: SimState,
        bar: pd.Series,
        ts: datetime,
        spacing: float,
    ) -> None:
        """网格止盈：价格回归间距时平仓。"""
        low = float(bar["low"])
        high = float(bar["high"])
        remaining: list[Position] = []

        for pos in state.positions:
            closed = False
            if pos.side == "buy":
                tp = pos.entry_price + spacing
                if high >= tp:
                    exit_px = self._exit_fill_price("buy", tp)
                    pnl = self._close_pnl(pos, exit_px, pos.volume)
                    state.balance += pnl
                    state.trades.append(
                        {
                            "datetime": ts,
                            "action": "take_profit",
                            "side": "buy",
                            "volume": pos.volume,
                            "price": exit_px,
                            "pnl": pnl,
                        }
                    )
                    closed = True
            else:
                tp = pos.entry_price - spacing
                if low <= tp:
                    exit_px = self._exit_fill_price("sell", tp)
                    pnl = self._close_pnl(pos, exit_px, pos.volume)
                    state.balance += pnl
                    state.trades.append(
                        {
                            "datetime": ts,
                            "action": "take_profit",
                            "side": "sell",
                            "volume": pos.volume,
                            "price": exit_px,
                            "pnl": pnl,
                        }
                    )
                    closed = True
            if not closed:
                remaining.append(pos)

        state.positions = remaining

    def _oz(self, lot: float) -> float:
        return lot * CONTRACT_OZ_PER_LOT

    def _margin_required(self, price: float, volume: float) -> float:
        """单仓保证金 = 名义价值 / 杠杆。"""
        return price * self._oz(volume) / self.leverage

    def _used_margin(self, state: SimState, mark_price: float) -> float:
        return sum(self._margin_required(mark_price, p.volume) for p in state.positions)

    def _free_margin(self, state: SimState, mark_price: float) -> float:
        return self._mark_equity(state, mark_price) - self._used_margin(state, mark_price)

    def _side_exposure_count(self, state: SimState, side: str) -> int:
        pos_n = sum(1 for p in state.positions if p.side == side)
        pend_n = sum(1 for p in state.pending if p.side == side)
        return pos_n + pend_n

    def _liquidate_all(self, state: SimState, price: float, ts: datetime, reason: str = "stop_out") -> None:
        for pos in list(state.positions):
            exit_px = self._exit_fill_price(pos.side, price)
            pnl = self._close_pnl(pos, exit_px, pos.volume)
            state.balance += pnl
            state.trades.append(
                {
                    "datetime": ts,
                    "action": reason,
                    "side": pos.side,
                    "volume": pos.volume,
                    "price": exit_px,
                    "pnl": pnl,
                }
            )
        state.positions.clear()
        state.pending.clear()

    def _mark_equity(self, state: SimState, price: float) -> float:
        floating = 0.0
        for pos in state.positions:
            mark = self._exit_fill_price(pos.side, price)
            sign = 1 if pos.side == "buy" else -1
            floating += sign * (mark - pos.entry_price) * self._oz(pos.volume)
        return state.balance + floating

    def _max_drawdown(self, equity: pd.Series) -> float:
        if equity.empty:
            return 0.0
        peak = equity.cummax()
        dd = (peak - equity) / peak.replace(0, 1) * 100
        return float(min(dd.max(), 100.0))

    def _apply_shock_entry(
        self,
        state: SimState,
        policy: ShockPolicy,
        price: float,
        ts: datetime,
    ) -> int:
        actions = 0
        if policy.pause_reverse or policy.pause_all_new:
            before = len(state.pending)
            if policy.pause_all_new:
                state.pending.clear()
            else:
                state.pending = [
                    p
                    for p in state.pending
                    if not self._is_reverse_pending(p.side, policy.direction)
                ]
            actions += before - len(state.pending)

        if policy.reverse_ratio < 1.0 and policy.direction in ("bullish", "bearish"):
            close_frac = 1.0 - policy.reverse_ratio
            new_positions: list[Position] = []
            for pos in state.positions:
                if self._is_reverse_position(pos.side, policy.direction) and close_frac > 0:
                    close_vol = pos.volume * close_frac
                    exit_px = self._exit_fill_price(pos.side, price)
                    pnl = self._close_pnl(pos, exit_px, close_vol)
                    state.balance += pnl
                    state.trades.append(
                        {
                            "datetime": ts,
                            "action": "close_reverse",
                            "side": pos.side,
                            "volume": close_vol,
                            "price": exit_px,
                            "pnl": pnl,
                            "news_id": policy.news_id,
                        }
                    )
                    remain = pos.volume - close_vol
                    if remain > 1e-8:
                        new_positions.append(Position(pos.side, pos.entry_price, remain))
                    actions += 1
                else:
                    new_positions.append(pos)
            state.positions = new_positions

        state.shock_events.append(
            {
                "datetime": ts,
                "news_id": policy.news_id,
                "trend_level": policy.trend_level,
                "direction": policy.direction,
                "title": policy.title,
                "actions": actions,
            }
        )
        return actions

    def _is_reverse_pending(self, side: str, direction: str) -> bool:
        if direction == "bullish":
            return side == "sell"
        if direction == "bearish":
            return side == "buy"
        return False

    def _is_reverse_position(self, side: str, direction: str) -> bool:
        return self._is_reverse_pending(side, direction)

    def _close_pnl(self, pos: Position, price: float, volume: float) -> float:
        sign = 1 if pos.side == "buy" else -1
        return sign * (price - pos.entry_price) * self._oz(volume)

    def _process_fills(self, state: SimState, bar: pd.Series, ts: datetime) -> None:
        low = float(bar["low"])
        high = float(bar["high"])
        still_pending: list[PendingOrder] = []

        for order in state.pending:
            filled = False
            if order.side == "buy" and low <= order.price:
                fill_px = self._entry_fill_price("buy", order.price)
                state.positions.append(Position("buy", fill_px, order.volume))
                state.trades.append(
                    {
                        "datetime": ts,
                        "action": "fill",
                        "side": "buy",
                        "volume": order.volume,
                        "price": fill_px,
                        "layer": order.layer,
                    }
                )
                filled = True
            elif order.side == "sell" and high >= order.price:
                fill_px = self._entry_fill_price("sell", order.price)
                state.positions.append(Position("sell", fill_px, order.volume))
                state.trades.append(
                    {
                        "datetime": ts,
                        "action": "fill",
                        "side": "sell",
                        "volume": order.volume,
                        "price": fill_px,
                        "layer": order.layer,
                    }
                )
                filled = True
            if not filled:
                still_pending.append(order)

        state.pending = still_pending

    def _existing_layers(self, state: SimState, side: str) -> set[int]:
        layers: set[int] = set()
        for p in state.pending:
            if p.side == side:
                layers.add(p.layer)
        return layers

    def _replenish_grid(
        self,
        state: SimState,
        mid: float,
        spacing: float,
        max_layers: int,
        lot: float,
        policy: ShockPolicy | None,
    ) -> None:
        if policy and policy.pause_all_new:
            return

        dir_ok = policy and policy.direction in ("bullish", "bearish")
        if dir_ok and (policy.pause_reverse or (self.use_news_adapter and self.directional_grid)):
            self._replenish_one_side(state, mid, spacing, max_layers, lot, policy.direction)
        else:
            self._replenish_symmetric(state, mid, spacing, max_layers, lot)

    def _replenish_symmetric(
        self,
        state: SimState,
        mid: float,
        spacing: float,
        max_layers: int,
        lot: float,
    ) -> None:
        self._replenish_side(state, "buy", mid, spacing, max_layers, lot)
        self._replenish_side(state, "sell", mid, spacing, max_layers, lot)

    def _replenish_one_side(
        self,
        state: SimState,
        mid: float,
        spacing: float,
        max_layers: int,
        lot: float,
        direction: str,
    ) -> None:
        if direction == "bullish":
            self._replenish_side(state, "buy", mid, spacing, max_layers, lot)
        elif direction == "bearish":
            self._replenish_side(state, "sell", mid, spacing, max_layers, lot)

    def _replenish_side(
        self,
        state: SimState,
        side: str,
        mid: float,
        spacing: float,
        max_layers: int,
        lot: float,
    ) -> None:
        if self._side_exposure_count(state, side) >= max_layers:
            return
        if self._free_margin(state, mid) < self._margin_required(mid, lot):
            return
        existing = self._existing_layers(state, side)
        for layer in range(1, max_layers + 1):
            if layer in existing:
                continue
            if side == "buy":
                price = round(mid - spacing * layer, 2)
            else:
                price = round(mid + spacing * layer, 2)
            state.pending.append(PendingOrder(side, price, lot, layer))


def run_ab_comparison(
    bars: pd.DataFrame,
    timeline: ShockTimeline,
    *,
    initial_balance: float = 10000.0,
    base_spacing: float = 2.0,
    max_layers: int = 5,
    default_lot: float = 0.01,
    leverage: float = DEFAULT_LEVERAGE,
    stop_out_margin_level: float = DEFAULT_STOP_OUT_MARGIN_LEVEL,
    directional_grid: bool = False,
    risk_overlay: SimRiskOverlayConfig | None = None,
    spread_price: float = 0.0,
) -> tuple[SimResult, SimResult]:
    common = dict(
        initial_balance=initial_balance,
        base_spacing=base_spacing,
        max_layers=max_layers,
        default_lot=default_lot,
        leverage=leverage,
        stop_out_margin_level=stop_out_margin_level,
        risk_overlay=risk_overlay,
        spread_price=spread_price,
    )
    baseline = GridShockSimulator(
        bars,
        use_news_adapter=False,
        **common,
    ).run()

    treatment = GridShockSimulator(
        bars,
        timeline=timeline,
        use_news_adapter=True,
        directional_grid=directional_grid,
        **common,
    ).run()

    return baseline, treatment
