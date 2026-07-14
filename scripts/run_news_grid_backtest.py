"""新闻冲击网格回测：baseline vs news_adapter A/B。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter import (  # noqa: E402
    align_signals_to_price_window,
    allocate_run_dir,
    load_m1_parquet,
    load_price_bars,
    load_signals,
    make_run_id,
    run_ab_comparison,
    write_backtest_report,
)
from src.backtest_adapter.timeline import ShockTimeline  # noqa: E402
from src.backtest_adapter.risk_overlay import load_sim_risk_overlay_config  # noqa: E402
from src.paths import get_backtest_output_root, get_history_data_root, load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="新闻冲击网格 A/B 回测")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--years",
        type=float,
        default=None,
        help="回测年数（正式回测，默认读 config.backtest.default_years=1）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="仅发烟测试：回测天数（<=21 视为 smoke，与 --years 互斥）",
    )
    parser.add_argument("--smoke", action="store_true", help="发烟测试模式（1~2 周，仅验证能跑通）")
    parser.add_argument("--align", action="store_true", help="将 live 信号映射到行情窗口（演示，不推荐）")
    parser.add_argument(
        "--signal-source",
        choices=["merged", "historical", "live"],
        default=None,
        help="信号来源：historical=宏观日历，live=实时 JSON，merged=合并",
    )
    parser.add_argument("--spacing", type=float, default=None)
    parser.add_argument("--layers", type=int, default=None)
    parser.add_argument("--initial", type=float, default=None, help="初始资金，默认 500")
    parser.add_argument(
        "--timeframe",
        choices=["H1", "M1"],
        default=None,
        help="回测 K 线周期，默认 config.backtest.timeframe",
    )
    parser.add_argument("--spread", type=float, default=None, help="买卖全点差（美元），默认 0.20")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    bt_cfg = config.get("backtest", {})
    history_root = get_history_data_root(config)
    backtest_root = get_backtest_output_root(config)

    initial = float(args.initial if args.initial is not None else bt_cfg.get("initial_balance", 500))
    spacing = float(args.spacing if args.spacing is not None else bt_cfg.get("base_spacing", 2.0))
    layers = int(args.layers if args.layers is not None else bt_cfg.get("max_layers", 5))
    default_lot = float(bt_cfg.get("default_lot", 0.01))
    leverage = float(bt_cfg.get("leverage", 100))
    stop_out = float(bt_cfg.get("stop_out_margin_level", 50))
    signal_source = args.signal_source or bt_cfg.get("signal_source", "historical")
    timeframe = (args.timeframe or bt_cfg.get("timeframe", "H1")).upper()
    spread_price = float(args.spread if args.spread is not None else bt_cfg.get("spread_price", 0.0))

    if args.smoke or (args.days is not None and args.days <= 21):
        window_days = args.days or int(bt_cfg.get("smoke_test_days", 14))
        backtest_mode = "smoke"
        years = None
    else:
        window_days = None
        years = args.years if args.years is not None else float(bt_cfg.get("default_years", 1.0))
        backtest_mode = "production"
        if timeframe == "M1":
            window_days = int(bt_cfg.get("m1_max_days", 30))
            years = None
            backtest_mode = "production_m1"
            print(f"M1 正式回测窗口 capped 为最近 {window_days} 天（可在 config m1_max_days 调整）")

    hist_sig_path = history_root / "signals" / bt_cfg.get("historical_signals_file", "historical_signals.parquet")
    if signal_source in ("historical", "merged") and not hist_sig_path.is_file():
        print(f"历史信号不存在: {hist_sig_path}")
        print("请先运行: python scripts/build_historical_signals.py --years 3")
        if signal_source == "historical":
            return 1

    price_csv = history_root / "data" / "XAUUSD_H1.csv"
    m1_parquet = history_root / str(bt_cfg.get("m1_parquet", "clean/XAUUSD_M1_clean.parquet"))

    if timeframe == "M1":
        if not m1_parquet.is_file():
            print(f"M1 行情不存在: {m1_parquet}")
            return 1
        full_bars = load_m1_parquet(m1_parquet)
        price_source = str(m1_parquet)
    else:
        full_bars = load_price_bars(price_csv)
        price_source = str(price_csv)

    if full_bars.empty:
        print(f"行情为空: {price_source}")
        return 1

    window_end = full_bars["datetime"].max().to_pydatetime()
    if window_days is not None:
        window_start = window_end - timedelta(days=window_days)
    else:
        window_start = window_end - timedelta(days=int(years * 365.25))

    if timeframe == "M1":
        bars = load_m1_parquet(m1_parquet, start=window_start, end=window_end)
    else:
        bars = load_price_bars(price_csv, start=window_start, end=window_end)

    print(f"回测: {timeframe}  bars={len(bars)}  spread={spread_price}  窗口 {window_days or years}d/y")

    signals = load_signals(
        signals_dir=PROJECT_ROOT / "data" / "signals",
        parquet_path=history_root / "signals" / "signals.parquet",
        historical_path=hist_sig_path,
        source=signal_source,
        window_start=window_start,
        window_end=window_end,
    )
    if not signals:
        print(
            f"窗口 {window_start} ~ {window_end} 内无信号。"
            f"请 build_historical_signals --years {years or 1}"
        )
        return 1

    aligned = False
    if backtest_mode == "smoke" and signal_source == "live":
        sig_min = min(s["timestamp"] for s in signals)
        sig_max = max(s["timestamp"] for s in signals)
        if sig_min > window_end or sig_max < window_start:
            if args.align:
                signals = align_signals_to_price_window(signals, window_start, window_end)
                aligned = True
            else:
                print("发烟测试：live 信号与行情不重叠，请用 --signal-source historical 或 --align")
                return 1

    timeline = ShockTimeline(signals)
    risk_overlay = load_sim_risk_overlay_config(config)
    baseline, treatment = run_ab_comparison(
        bars,
        timeline,
        initial_balance=initial,
        base_spacing=spacing,
        max_layers=layers,
        default_lot=default_lot,
        leverage=leverage,
        stop_out_margin_level=stop_out,
        directional_grid=bool(bt_cfg.get("directional_grid", False)),
        risk_overlay=risk_overlay,
        spread_price=spread_price,
    )

    run_id, out_dir = allocate_run_dir(backtest_root / "results" / "news-grid")
    meta = {
        "run_id": run_id,
        "run_id_format": "YYYYMMDDHHmm_文件名",
        "run_id_timezone": "Asia/Shanghai",
        "backtest_mode": backtest_mode,
        "backtest_mode_cn": {
            "smoke": "发烟测试",
            "production": "正式年度回测",
            "production_m1": "M1 窗口回测",
        }.get(backtest_mode, backtest_mode),
        "timeframe": timeframe,
        "spread_price": spread_price,
        "price_source": price_source,
        "bar_count": len(bars),
        "price_csv": price_source if timeframe == "H1" else None,
        "price_m1_parquet": price_source if timeframe == "M1" else None,
        "window_start": str(window_start),
        "window_end": str(window_end),
        "window_days": (window_end - window_start).days,
        "years": years,
        "initial_balance": initial,
        "signal_count": len(signals),
        "signal_source": signal_source,
        "aligned": aligned,
        "base_spacing": spacing,
        "max_layers": layers,
        "default_lot": default_lot,
        "leverage": leverage,
        "stop_out_margin_level": stop_out,
        "directional_grid": bool(bt_cfg.get("directional_grid", False)),
        "l3_shock_hours": int(bt_cfg.get("l3_shock_hours", 4)),
        "exclude_event_categories": bt_cfg.get("exclude_event_categories", ["claims"]),
        "risk_overlay_enabled": risk_overlay.enabled,
    }
    paths = write_backtest_report(out_dir, baseline, treatment, timeline, meta)

    summary = {
        "run_id": run_id,
        "output_dir": str(out_dir),
        "meta": meta,
        "baseline": baseline.to_dict(),
        "news_adapter": treatment.to_dict(),
        "files": {k: str(v) for k, v in paths.items()},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
