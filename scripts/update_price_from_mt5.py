"""从 MT5 增量更新 History Data CSV 行情（H1/M15/M1）。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

TF_MAP = {
    "M1": ("XAUUSD_M1.csv", 1),
    "M15": ("XAUUSD_M15.csv", 15),
    "H1": ("XAUUSD_H1.csv", 60),
}


def _load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _csv_path(history_root: Path, timeframe: str) -> Path:
    fname, _ = TF_MAP[timeframe]
    return history_root / "data" / fname


def _read_last_datetime(csv_path: Path) -> datetime | None:
    if not csv_path.is_file():
        return None
    df = pd.read_csv(csv_path, usecols=["datetime"], parse_dates=["datetime"])
    if df.empty:
        return None
    ts = df["datetime"].max()
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.to_pydatetime()


def _fetch_mt5_bars(
    symbol: str,
    timeframe: str,
    date_from: datetime,
    date_to: datetime,
    terminal_path: str = "",
    account: int = 0,
    password: str = "",
    server: str = "",
) -> pd.DataFrame | None:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        logger.error("MetaTrader5 未安装")
        return None

    tf_const = {
        "M1": mt5.TIMEFRAME_M1,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
    }[timeframe]

    initialized = False
    if terminal_path and account and password and server:
        initialized = mt5.initialize(
            path=terminal_path,
            login=account,
            password=password,
            server=server,
        )
    elif terminal_path:
        initialized = mt5.initialize(path=terminal_path)
    else:
        initialized = mt5.initialize()

    if not initialized:
        logger.error("MT5 初始化失败: %s", mt5.last_error())
        return None

    try:
        for sym in (symbol, "GOLD", "XAUUSD"):
            if mt5.symbol_select(sym, True):
                symbol = sym
                break

        rates = mt5.copy_rates_range(sym, tf_const, date_from, date_to)
        if rates is None or len(rates) == 0:
            logger.warning("MT5 无新 K 线: %s", mt5.last_error())
            return None

        info = mt5.symbol_info(sym)
        spread_pts = float(info.spread * info.point) if info else 0.5

        df = pd.DataFrame(rates)
        df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df["symbol"] = "XAUUSD"
        df["timeframe"] = timeframe
        df["volume"] = df["tick_volume"].astype(float)
        df["spread"] = spread_pts
        return df[["datetime", "symbol", "timeframe", "open", "high", "low", "close", "volume", "spread"]]
    finally:
        mt5.shutdown()


def _fetch_yfinance_fallback(date_from: datetime, date_to: datetime, timeframe: str) -> pd.DataFrame | None:
    """GC=F 期货代理（MT5 不可用时兜底）。"""
    try:
        import yfinance as yf
    except ImportError:
        logger.info("yfinance 未安装，跳过兜底下载")
        return None

    interval = {"M1": "1m", "M15": "15m", "H1": "1h"}.get(timeframe, "1h")
    # yfinance 1m 仅最近 7 天；H1 可更长
    if interval == "1m":
        start = max(date_from, datetime.now(timezone.utc) - timedelta(days=6))
    else:
        start = date_from

    ticker = yf.Ticker("GC=F")
    hist = ticker.history(start=start, end=date_to + timedelta(days=1), interval=interval)
    if hist.empty:
        return None

    df = hist.reset_index()
    dt_col = "Datetime" if "Datetime" in df.columns else "Date"
    df["datetime"] = pd.to_datetime(df[dt_col], utc=True)
    df["symbol"] = "XAUUSD"
    df["timeframe"] = timeframe
    df["volume"] = df["Volume"].astype(float)
    df["spread"] = 0.5
    return df[["datetime", "symbol", "timeframe", "open", "high", "low", "close", "volume", "spread"]]


def append_bars(
    csv_path: Path,
    new_df: pd.DataFrame,
    timeframe: str,
) -> int:
    if new_df is None or new_df.empty:
        return 0

    new_df = new_df.copy()
    if csv_path.is_file():
        old = pd.read_csv(csv_path, parse_dates=["datetime"])
        if old["datetime"].dt.tz is None:
            old["datetime"] = old["datetime"].dt.tz_localize("UTC")
        last = old["datetime"].max()
        new_df = new_df[new_df["datetime"] > last]
        if new_df.empty:
            return 0
        merged = pd.concat([old, new_df], ignore_index=True)
    else:
        merged = new_df
        csv_path.parent.mkdir(parents=True, exist_ok=True)

    merged = merged.drop_duplicates(subset=["datetime"], keep="last").sort_values("datetime")
    merged.to_csv(csv_path, index=False)
    return len(new_df)


def update_timeframe(
    history_root: Path,
    mt5_cfg: dict,
    timeframe: str,
    *,
    use_yfinance: bool = True,
) -> dict:
    csv_path = _csv_path(history_root, timeframe)
    last = _read_last_datetime(csv_path)
    now = datetime.now(timezone.utc)

    if last is None:
        date_from = now - timedelta(days=30)
    else:
        _, minutes = TF_MAP[timeframe]
        date_from = last + timedelta(minutes=minutes)

    if date_from >= now:
        return {"timeframe": timeframe, "appended": 0, "reason": "already_up_to_date", "last": str(last)}

    symbol = str(mt5_cfg.get("symbol", "GOLD"))
    df = _fetch_mt5_bars(
        symbol,
        timeframe,
        date_from,
        now,
        terminal_path=str(mt5_cfg.get("path", "")),
        account=int(mt5_cfg.get("account", 0)),
        password=str(mt5_cfg.get("password", "")),
        server=str(mt5_cfg.get("server", "")),
    )

    source = "mt5"
    if df is None and use_yfinance:
        df = _fetch_yfinance_fallback(date_from, now, timeframe)
        source = "yfinance"

    appended = append_bars(csv_path, df, timeframe) if df is not None else 0
    new_last = _read_last_datetime(csv_path)
    return {
        "timeframe": timeframe,
        "appended": appended,
        "source": source if appended else "none",
        "csv": str(csv_path),
        "last_before": str(last),
        "last_after": str(new_last),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="从 MT5/yfinance 增量更新 History Data CSV")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--timeframes", default="H1,M15", help="逗号分隔: M1,M15,H1")
    parser.add_argument("--no-yfinance", action="store_true")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = _load_config(PROJECT_ROOT / args.config)
    history_root = Path(config.get("paths", {}).get("history_data", "E:/量化项目/02 History Data"))
    mt5_cfg = config.get("mt5", {})

    results = []
    for tf in args.timeframes.split(","):
        tf = tf.strip().upper()
        if tf not in TF_MAP:
            continue
        results.append(
            update_timeframe(
                history_root,
                mt5_cfg,
                tf,
                use_yfinance=not args.no_yfinance,
            )
        )

    print(json.dumps({"history_root": str(history_root), "updates": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
