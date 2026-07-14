"""warning_margin_level 敏感性扫描（100x / $500）。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter import load_price_bars, load_signals, run_ab_comparison  # noqa: E402
from src.backtest_adapter.risk_overlay import SimRiskOverlayConfig, load_sim_risk_overlay_config  # noqa: E402
from src.backtest_adapter.timeline import ShockTimeline  # noqa: E402
from src.backtest_adapter.naming import make_run_id  # noqa: E402
from src.paths import get_history_data_root, load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="margin_level 预警阈值敏感性")
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--initial", type=float, default=500.0)
    parser.add_argument("--levels", type=float, nargs="+", default=[75, 80, 85, 90])
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    bt_cfg = config.get("backtest", {})
    history_root = get_history_data_root(config)

    price_csv = history_root / "data" / "XAUUSD_H1.csv"
    hist_sig = history_root / "signals" / bt_cfg.get("historical_signals_file", "historical_signals.parquet")
    full_bars = load_price_bars(price_csv)
    window_end = full_bars["datetime"].max().to_pydatetime()
    window_start = window_end - timedelta(days=int(args.years * 365.25))
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
    base_overlay = load_sim_risk_overlay_config(config)
    leverage = float(bt_cfg.get("leverage", 100))
    stop_out = float(bt_cfg.get("stop_out_margin_level", 50))
    spacing = float(bt_cfg.get("base_spacing", 3.5))
    layers = int(bt_cfg.get("max_layers", 3))
    lot = float(bt_cfg.get("default_lot", 0.01))

    results: list[dict] = []
    for level in args.levels:
        overlay = replace(base_overlay, warning_margin_level=float(level))
        baseline, news = run_ab_comparison(
            bars,
            timeline,
            initial_balance=args.initial,
            base_spacing=spacing,
            max_layers=layers,
            default_lot=lot,
            leverage=leverage,
            stop_out_margin_level=stop_out,
            directional_grid=bool(bt_cfg.get("directional_grid", False)),
            risk_overlay=overlay,
        )
        b, n = baseline.to_dict(), news.to_dict()
        results.append({
            "warning_margin_level": level,
            "baseline_pnl": b["total_pnl"],
            "news_pnl": n["total_pnl"],
            "news_vs_baseline": round(n["total_pnl"] - b["total_pnl"], 2),
            "news_stopped_out": n.get("stopped_out", False),
            "baseline_stopped_out": b.get("stopped_out", False),
            "news_max_dd": n.get("max_drawdown_pct"),
            "news_risk_actions": n.get("risk_actions", 0),
        })

    results.sort(key=lambda r: (-r["news_vs_baseline"], r["news_stopped_out"]))
    run_id = make_run_id()
    out_dir = PROJECT_ROOT / "docs" / "操作记录"
    json_path = out_dir / f"{run_id}_margin敏感性.json"
    md_path = out_dir / f"{run_id}_margin敏感性.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# margin_level 预警阈值敏感性 {run_id}",
        "",
        "| warning_level | news PnL | vs base | news stop_out | news DD% | risk_actions |",
        "|---------------|----------|---------|---------------|----------|--------------|",
    ]
    for r in results:
        so = "Y" if r["news_stopped_out"] else "N"
        lines.append(
            f"| {r['warning_margin_level']:.0f} | {r['news_pnl']:.2f} | {r['news_vs_baseline']:+.2f} | "
            f"{so} | {r['news_max_dd']:.1f} | {r['news_risk_actions']} |"
        )
    best = results[0]
    lines.append("")
    lines.append(
        f"**推荐：** warning_margin_level={best['warning_margin_level']:.0f} "
        f"(news PnL {best['news_pnl']:.2f}, stop_out={'是' if best['news_stopped_out'] else '否'})"
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"run_id": run_id, "results": results, "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
