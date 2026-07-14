"""网格参数扫描：spacing × max_layers，对比 stop_out 与 news_adapter PnL。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter import (  # noqa: E402
    load_price_bars,
    load_signals,
    run_ab_comparison,
)
from src.backtest_adapter.timeline import ShockTimeline  # noqa: E402
from src.backtest_adapter.risk_overlay import load_sim_risk_overlay_config  # noqa: E402
from src.paths import get_history_data_root, load_config  # noqa: E402


def run_scan(
    *,
    years: float,
    initial: float,
    spacings: list[float],
    layers_list: list[int],
    config_path: Path,
) -> list[dict]:
    config = load_config(config_path)
    bt_cfg = config.get("backtest", {})
    history_root = get_history_data_root(config)

    price_csv = history_root / "data" / "XAUUSD_H1.csv"
    hist_sig = history_root / "signals" / bt_cfg.get("historical_signals_file", "historical_signals.parquet")
    full_bars = load_price_bars(price_csv)
    window_end = full_bars["datetime"].max().to_pydatetime()
    window_start = window_end - timedelta(days=int(years * 365.25))
    bars = load_price_bars(price_csv, start=window_start, end=window_end)

    signals = load_signals(
        signals_dir=PROJECT_ROOT / "data" / "signals",
        parquet_path=history_root / "signals" / "signals.parquet",
        historical_path=hist_sig,
        source="historical",
        window_start=window_start,
        window_end=window_end,
    )
    timeline = ShockTimeline(signals)
    leverage = float(bt_cfg.get("leverage", 100))
    stop_out = float(bt_cfg.get("stop_out_margin_level", 50))
    lot = float(bt_cfg.get("default_lot", 0.01))

    risk_overlay = load_sim_risk_overlay_config(config)
    results: list[dict] = []
    for spacing in spacings:
        for layers in layers_list:
            baseline, news = run_ab_comparison(
                bars,
                timeline,
                initial_balance=initial,
                base_spacing=spacing,
                max_layers=layers,
                default_lot=lot,
                leverage=leverage,
                stop_out_margin_level=stop_out,
                directional_grid=bool(bt_cfg.get("directional_grid", False)),
                risk_overlay=risk_overlay,
            )
            b, n = baseline.to_dict(), news.to_dict()
            results.append({
                "base_spacing": spacing,
                "max_layers": layers,
                "baseline_pnl": b["total_pnl"],
                "news_pnl": n["total_pnl"],
                "news_vs_baseline": round(n["total_pnl"] - b["total_pnl"], 2),
                "baseline_stopped_out": b.get("stopped_out", False),
                "news_stopped_out": n.get("stopped_out", False),
                "baseline_max_dd": b.get("max_drawdown_pct"),
                "news_max_dd": n.get("max_drawdown_pct"),
                "news_shock_actions": n.get("shock_actions", 0),
                "news_trades": n.get("trade_count", 0),
            })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="网格 spacing/layers 参数扫描")
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--initial", type=float, default=500.0)
    parser.add_argument("--spacing", type=float, nargs="+", default=[3.5, 4.0, 4.5, 5.0])
    parser.add_argument("--layers", type=int, nargs="+", default=[2, 3])
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    results = run_scan(
        years=args.years,
        initial=args.initial,
        spacings=args.spacing,
        layers_list=args.layers,
        config_path=PROJECT_ROOT / args.config,
    )
    results.sort(key=lambda r: (r["news_stopped_out"], -r["news_vs_baseline"]))

    out_dir = PROJECT_ROOT / "docs" / "操作记录"
    out_dir.mkdir(parents=True, exist_ok=True)
    from src.backtest_adapter.naming import make_run_id

    run_id = make_run_id()
    json_path = out_dir / f"{run_id}_参数扫描.json"
    md_path = out_dir / f"{run_id}_参数扫描.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 网格参数扫描 {run_id}",
        "",
        f"- 窗口: {args.years} 年 | 初始资金 ${args.initial:.0f}",
        "",
        "| spacing | layers | news PnL | vs base | news stop_out | news DD% | shocks |",
        "|---------|--------|----------|---------|---------------|----------|--------|",
    ]
    for r in results:
        so = "Y" if r["news_stopped_out"] else "N"
        lines.append(
            f"| {r['base_spacing']} | {r['max_layers']} | {r['news_pnl']:.2f} | "
            f"{r['news_vs_baseline']:+.2f} | {so} | {r['news_max_dd']:.1f} | {r['news_shock_actions']} |"
        )
    best = results[0]
    lines.extend([
        "",
        f"**推荐（未 stop_out 优先，其次 vs baseline）：** spacing={best['base_spacing']}, layers={best['max_layers']}",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"run_id": run_id, "results": results, "json": str(json_path), "md": str(md_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
