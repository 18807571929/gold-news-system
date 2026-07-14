"""生成毕设操作记录摘要（系统状态 + 最近回测 + 验证结果）。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

CHINA_TZ = timezone(timedelta(hours=8))


def _latest_backtest(backtest_root: Path) -> dict | None:
    base = backtest_root / "results" / "news-grid"
    if not base.is_dir():
        return None
    comps = sorted(base.glob("*_comparison.json"), reverse=True)
    if not comps:
        # 兼容旧版子目录
        runs = [p for p in base.iterdir() if p.is_dir()]
        if not runs:
            return None
        runs.sort(key=lambda p: (p.name[:12] if p.name[:12].isdigit() else "", p.stat().st_mtime), reverse=True)
        latest = runs[0]
        comp = latest / "comparison.json"
        if comp.is_file():
            data = json.loads(comp.read_text(encoding="utf-8"))
            data.setdefault("run_id", latest.name)
            return {"run_dir": str(latest), **data}
        return {"run_dir": str(latest), "run_id": latest.name}
    latest = comps[0]
    run_id = latest.name.replace("_comparison.json", "")
    data = json.loads(latest.read_text(encoding="utf-8"))
    data.setdefault("run_id", run_id)
    return {"run_dir": str(base), "run_id": run_id, **data}


def _price_range(history_root: Path) -> dict:
    import pandas as pd

    h1 = history_root / "data" / "XAUUSD_H1.csv"
    if not h1.is_file():
        return {"error": "H1 CSV 不存在"}
    df = pd.read_csv(h1, usecols=["datetime"], parse_dates=["datetime"])
    return {"min": str(df["datetime"].min()), "max": str(df["datetime"].max()), "bars": len(df)}


def _signal_stats() -> dict:
    sig_dir = PROJECT_ROOT / "data" / "signals"
    count = sum(1 for _ in sig_dir.rglob("*.json") if _.name != "executed_ids.json")
    return {"signal_json_count": count}


def main() -> int:
    from src.paths import get_backtest_output_root, get_history_data_root, load_config

    config = load_config(PROJECT_ROOT / "config/config.yaml")
    history = get_history_data_root(config)
    backtest = get_backtest_output_root(config)

    l2_log = PROJECT_ROOT / "docs" / "操作记录" / "logs" / "l2_verify_latest.json"
    l2_result = None
    if l2_log.is_file():
        l2_result = json.loads(l2_log.read_text(encoding="utf-8"))

    summary = {
        "generated_at": datetime.now(CHINA_TZ).isoformat(),
        "price_data": _price_range(history),
        "signals": _signal_stats(),
        "latest_backtest": _latest_backtest(backtest),
        "l2_verification": l2_result,
        "mt5_config": {
            "account": config.get("mt5", {}).get("account"),
            "server": config.get("mt5", {}).get("server"),
            "symbol": config.get("mt5", {}).get("symbol"),
            "enabled": config.get("mt5", {}).get("enabled"),
        },
    }

    out = PROJECT_ROOT / "docs" / "操作记录" / "logs" / "ops_summary_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = PROJECT_ROOT / "docs" / "操作记录" / "05_运行摘要_自动生成.md"
    md_path.write_text(_to_markdown(summary), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _to_markdown(s: dict) -> str:
    lines = [
        "# 运行摘要（自动生成）",
        "",
        f"生成时间：{s['generated_at']}",
        "",
        "## 行情数据",
        "",
        f"- H1 范围：{s['price_data'].get('min', '?')} ~ {s['price_data'].get('max', '?')}",
        f"- K 线数：{s['price_data'].get('bars', '?')}",
        "",
        "## 信号",
        "",
        f"- JSON 信号数：{s['signals'].get('signal_json_count', 0)}",
        "",
        "## MT5",
        "",
        f"- 账号：{s['mt5_config'].get('account')} @ {s['mt5_config'].get('server')}",
        f"- 品种：{s['mt5_config'].get('symbol')}",
        "",
    ]
    bt = s.get("latest_backtest")
    if bt:
        lines += ["## 最近回测", ""]
        if "baseline" in bt:
            b, t = bt["baseline"], bt["news_adapter"]
            d = bt.get("delta", {})
            lines += [
                f"- 目录：`{bt.get('run_dir', '')}`",
                f"- Baseline PnL：{b.get('total_pnl')}",
                f"- News Adapter PnL：{t.get('total_pnl')}",
                f"- 改善：{d.get('pnl_improvement')}",
                "",
            ]
    l2 = s.get("l2_verification")
    if l2:
        v = l2.get("verification", {})
        lines += [
            "## L2 验证",
            "",
            f"- 通过：{v.get('passed', '未测')}",
            f"- sell 挂单：{v.get('sell_before', '?')} → {v.get('sell_after', '?')}",
            "",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
