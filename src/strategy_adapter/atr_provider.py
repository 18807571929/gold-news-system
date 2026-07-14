"""ATR 波动率因子：MT5 实时优先，History Data CSV 兜底。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _calc_atr_from_bars(highs: list[float], lows: list[float], closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def get_atr_from_mt5(symbol: str, period: int = 14, timeframe: str = "H1") -> float | None:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    tf_map = {
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_H1)
    if not mt5.symbol_select(symbol, True):
        return None

    bars_needed = period + 2
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars_needed)
    if rates is None or len(rates) < period + 1:
        return None

    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    closes = [float(r["close"]) for r in rates]
    return _calc_atr_from_bars(highs, lows, closes, period)


def get_atr_from_csv(csv_path: Path, period: int = 14) -> float | None:
    if not csv_path.is_file():
        return None
    try:
        import pandas as pd

        df = pd.read_csv(csv_path)
        if len(df) < period + 2:
            return None
        tail = df.tail(period + 2)
        return _calc_atr_from_bars(
            tail["high"].astype(float).tolist(),
            tail["low"].astype(float).tolist(),
            tail["close"].astype(float).tolist(),
            period,
        )
    except Exception as exc:
        logger.warning("CSV ATR 计算失败: %s", exc)
        return None


def atr_factor(atr_value: float | None, ref_atr: float = 2.0, clamp: tuple[float, float] = (0.5, 2.0)) -> float:
    """ATR 相对参考值的比例，限制在 [0.5, 2.0]。"""
    if not atr_value or atr_value <= 0 or ref_atr <= 0:
        return 1.0
    raw = atr_value / ref_atr
    return max(clamp[0], min(clamp[1], raw))


class ATRProvider:
    """统一获取 ATR 与 spacing 乘数。"""

    def __init__(
        self,
        symbol: str = "GOLD",
        period: int = 14,
        timeframe: str = "H1",
        ref_atr: float = 2.0,
        csv_path: str | Path = "",
        use_mt5: bool = True,
    ) -> None:
        self.symbol = symbol
        self.period = period
        self.timeframe = timeframe
        self.ref_atr = ref_atr
        self.csv_path = Path(csv_path) if csv_path else Path()
        self.use_mt5 = use_mt5

    @classmethod
    def from_config(cls, config_path: str | Path = "config/config.yaml") -> ATRProvider:
        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        grid = config.get("grid_strategy", {})
        mt5_cfg = config.get("mt5", {})
        paths = config.get("paths", {})

        csv_default = Path(paths.get("history_data", "")) / "data" / "XAUUSD_H1.csv"
        return cls(
            symbol=str(mt5_cfg.get("symbol", "GOLD")),
            period=int(grid.get("atr_period", 14)),
            timeframe=str(grid.get("atr_timeframe", "H1")),
            ref_atr=float(grid.get("atr_ref", 2.0)),
            csv_path=grid.get("atr_csv_path") or csv_default,
            use_mt5=bool(grid.get("use_atr", True)),
        )

    def get_atr(self, connector: Any | None = None) -> dict[str, Any]:
        atr_val: float | None = None
        source = "default"

        if self.use_mt5 and connector is not None:
            connected = False
            try:
                connected = connector.connect()
                if connected:
                    atr_val = get_atr_from_mt5(self.symbol, self.period, self.timeframe)
                    if atr_val:
                        source = "mt5"
            finally:
                if connected:
                    connector.disconnect()

        if atr_val is None and self.csv_path:
            atr_val = get_atr_from_csv(self.csv_path, self.period)
            if atr_val:
                source = "csv"

        factor = atr_factor(atr_val, self.ref_atr)
        return {
            "atr": round(atr_val, 4) if atr_val else None,
            "atr_factor": round(factor, 4),
            "atr_source": source,
            "atr_period": self.period,
            "atr_ref": self.ref_atr,
        }
