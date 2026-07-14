"""gs_v17 M1 全引擎：baseline vs news_adapter A/B（新闻冲击 timeline）。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter.naming import allocate_run_dir, make_run_id, output_filename  # noqa: E402
from src.paths import get_backtest_output_root, get_history_data_root, get_strategy_root, load_config  # noqa: E402


def _bootstrap_gs_v17(config: dict | None = None) -> None:
    config = config or load_config(PROJECT_ROOT / "config/config.yaml")
    backtest_root = get_backtest_output_root(config)
    bt_pkg = backtest_root / "gs_v17"
    if not bt_pkg.is_dir():
        raise SystemExit(f"gs_v17 不存在: {bt_pkg}")

    strategy_root = get_strategy_root(config)
    if not strategy_root.is_dir():
        raise SystemExit(
            f"策略目录缺失: {strategy_root}\n"
            "请在 config.yaml paths.strategy_root 填写 golden_shield_v1.7 路径，"
            "或设置环境变量 GS_STRATEGY_ROOT。"
        )

    if str(backtest_root) not in sys.path:
        sys.path.insert(0, str(backtest_root))
    from gs_v17.bootstrap import ensure_paths

    ensure_paths()


def _apply_production_config(*, pure_grid: bool = True) -> dict:
    import config as cfg
    import golden_shield_trend_grid as gsg
    import grid_manager as gm
    import model_profile

    model_profile.apply_production_profile()
    cfg.PURE_GRID_MODE = bool(pure_grid)
    cfg.PURE_GRID_PAUSE_ON_TREND = False
    cfg.ENABLE_MAX_DRAWDOWN_RISK = False
    cfg.BASKET_FLOAT_PARTIAL_CLOSE_FRAC = 0.0
    cfg.ENABLE_BREAKEVEN_SL = False
    cfg.ENABLE_FEE_CALCULATION = True
    cfg.UTC_HOURS_BLOCK_NEW_LIMITS = [1, 2]
    gm.apply_buy_pivot_cap_config(mode="off", baseline_label="bullish_ohlc_pullback_v1")
    gm.apply_sell_pivot_cap_config(mode="off", baseline_label="bearish_ohlc_pullback_v1")
    snap = gsg.configure_dd_gate_variant("v3B_regime_filtered_hard")
    return {
        **snap,
        "PURE_GRID_MODE": cfg.PURE_GRID_MODE,
        "ENABLE_DAILY_LOSS_RISK": cfg.ENABLE_DAILY_LOSS_RISK,
        "ENABLE_FEE_CALCULATION": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="gs_v17 新闻冲击 A/B 回测（M1 全引擎）")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--years", type=float, default=None, help="正式回测年数（默认 config.backtest.default_years）")
    parser.add_argument("--smoke", action="store_true", help="发烟测试（约 1 周 M1）")
    parser.add_argument("--initial", type=float, default=None)
    parser.add_argument("--trend", action="store_true", help="PURE_GRID_MODE=False（M5 趋势单侧）")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    bt_cfg = config.get("backtest", {})
    history_root = get_history_data_root(config)
    backtest_root = get_backtest_output_root(config)

    initial = float(args.initial if args.initial is not None else bt_cfg.get("initial_balance", 500))
    signal_source = bt_cfg.get("signal_source", "historical")
    hist_sig = history_root / "signals" / bt_cfg.get("historical_signals_file", "historical_signals.parquet")

    if signal_source in ("historical", "merged") and not hist_sig.is_file():
        print(f"历史信号不存在: {hist_sig}")
        print("请先运行: python scripts/build_historical_signals.py --years 3")
        return 1

    _bootstrap_gs_v17(config)

    from gs_v17.analysis_window import m1_data_span, window_from_last
    from news_shock_adapter.patch_gs_v17 import load_timeline_from_parquet, run_news_shock_backtest

    if args.smoke:
        smoke_days = max(7, int(bt_cfg.get("smoke_test_days", 14) // 2))
        _, t1_file, _ = m1_data_span()
        we = t1_file + timedelta(minutes=1)
        ws = t1_file - timedelta(days=smoke_days)
        window_kw = {"window_start": ws, "window_end": we}
        backtest_mode = "smoke"
        years = None
        win_meta = {
            "label": f"{smoke_days}d",
            "window_start": str(ws),
            "window_end": str(we),
            "actual_days": smoke_days,
        }
    else:
        years = float(args.years if args.years is not None else bt_cfg.get("default_years", 3.0))
        ws, we, win_meta = window_from_last(years=years)
        window_kw = {"window_start": ws, "window_end": we}
        backtest_mode = "production"

    cfg_snap = _apply_production_config(pure_grid=not args.trend)
    cfg_snap["directional_grid"] = bool(bt_cfg.get("directional_grid", True))
    cfg_snap["signal_source"] = signal_source
    cfg_snap["historical_signals"] = str(hist_sig)

    out_base = backtest_root / "results" / "news-grid"
    run_id, _ = allocate_run_dir(out_base)
    run_dir = out_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    timeline_fn = load_timeline_from_parquet(hist_sig) if hist_sig.is_file() else None
    common_run = {
        **window_kw,
        "return_stats": True,
        "write_outputs": True,
        "record_run": False,
        "extra_conditions": cfg_snap,
        "exec_timeframe": "M1",
    }

    print(f"Mode: {'smoke' if args.smoke else f'{years}y'}  initial={initial}  run_id={run_id}")
    print(f"Window: {win_meta.get('window_start')} -> {win_meta.get('window_end')}")
    print(f"Output: {run_dir}")
    print("Running baseline (no timeline)...")

    baseline_dir = run_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_stats = run_news_shock_backtest(
        initial=initial,
        results_dir=baseline_dir,
        use_news_adapter=False,
        run_label=f"{run_id}_baseline",
        run_notes="gs_v17 baseline (no news shock)",
        **common_run,
    )

    print("Running news_adapter (with timeline)...")
    news_dir = run_dir / "news_adapter"
    news_dir.mkdir(parents=True, exist_ok=True)
    news_stats = run_news_shock_backtest(
        initial=initial,
        results_dir=news_dir,
        use_news_adapter=True,
        timeline_policy_fn=timeline_fn,
        manifest_path=news_dir / "adapter_manifest.json",
        run_label=f"{run_id}_news",
        run_notes="gs_v17 news shock adapter",
        **common_run,
    )

    def _row(mode: str, stats: dict) -> dict:
        return {
            "mode": mode,
            "initial_usd": initial,
            "final_usd": round(float(stats.get("final_equity", 0)), 2),
            "return_pct": round(float(stats.get("return_pct", 0)), 2),
            "max_dd_pct": round(float(stats.get("max_dd_pct", 0)), 2),
            "n_closes": int(stats.get("n_closes", 0)),
            "shock_actions": int(stats.get("news_shock_actions", 0)),
            "shock_events": int(stats.get("news_shock_events", 0)),
            "stopped_out": bool(stats.get("circuit", False)),
            "profit_factor": round(float(stats.get("profit_factor", 0)), 3),
        }

    comparison = {
        "run_id": run_id,
        "engine": "gs_v17 BacktestV1 M1",
        "backtest_mode": backtest_mode,
        "window": win_meta,
        "config_snapshot": cfg_snap,
        "baseline": _row("baseline", baseline_stats),
        "news_adapter": _row("news_adapter", news_stats),
        "delta_pnl_usd": round(
            float(news_stats.get("final_equity", 0)) - float(baseline_stats.get("final_equity", 0)),
            2,
        ),
    }

    comp_name = output_filename(run_id, "comparison.json")
    comp_path = out_base / comp_name
    comp_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    print("\n=== gs_v17 A/B Summary ===")
    for key in ("baseline", "news_adapter"):
        r = comparison[key]
        print(
            f"  {r['mode']:12s}  final={r['final_usd']:8.2f}  "
            f"ret={r['return_pct']:7.2f}%  dd={r['max_dd_pct']:6.2f}%  "
            f"shock={r['shock_actions']}  stop_out={r['stopped_out']}"
        )
    print(f"  delta_pnl_usd: {comparison['delta_pnl_usd']:+.2f}")
    print(f"  comparison: {comp_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
