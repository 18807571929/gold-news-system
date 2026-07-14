"""回测报告生成。"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .simulator import SimResult
from .timeline import ShockTimeline

CHINA_TZ = timezone(timedelta(hours=8))


from .naming import output_filename


def write_backtest_report(
    out_dir: Path,
    baseline: SimResult,
    treatment: SimResult,
    timeline: ShockTimeline,
    meta: dict[str, Any],
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = meta.get("run_id", "")

    comparison = {
        "generated_at": datetime.now(CHINA_TZ).isoformat(),
        "meta": meta,
        "baseline": baseline.to_dict(),
        "news_adapter": treatment.to_dict(),
        "delta": {
            "pnl_improvement": round(treatment.total_pnl - baseline.total_pnl, 2),
            "max_dd_reduction": round(baseline.max_drawdown_pct - treatment.max_drawdown_pct, 2),
            "shock_actions": treatment.shock_actions,
        },
    }

    paths: dict[str, Path] = {}

    comp_path = out_dir / output_filename(run_id, "comparison.json")
    comp_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["comparison"] = comp_path

    eq_base = out_dir / output_filename(run_id, "equity_baseline.csv")
    eq_news = out_dir / output_filename(run_id, "equity_news_adapter.csv")
    baseline.equity_curve.to_csv(eq_base, index=False, encoding="utf-8-sig")
    treatment.equity_curve.to_csv(eq_news, index=False, encoding="utf-8-sig")
    paths["equity_baseline"] = eq_base
    paths["equity_news_adapter"] = eq_news

    if not treatment.trades.empty:
        p = out_dir / output_filename(run_id, "trades_news_adapter.csv")
        treatment.trades.to_csv(p, index=False, encoding="utf-8-sig")
        paths["trades_news_adapter"] = p
    if not baseline.trades.empty:
        p = out_dir / output_filename(run_id, "trades_baseline.csv")
        baseline.trades.to_csv(p, index=False, encoding="utf-8-sig")
        paths["trades_baseline"] = p

    events = timeline.events_dataframe()
    if not events.empty:
        p = out_dir / output_filename(run_id, "news_shock_events.csv")
        events.to_csv(p, index=False, encoding="utf-8-sig")
        paths["news_shock_events"] = p

    if not treatment.shock_events.empty:
        p = out_dir / output_filename(run_id, "shock_actions.csv")
        treatment.shock_events.to_csv(p, index=False, encoding="utf-8-sig")
        paths["shock_actions"] = p

    md = _build_markdown(comparison, meta)
    md_path = out_dir / output_filename(run_id, "report.md")
    md_path.write_text(md, encoding="utf-8")
    paths["report"] = md_path

    return paths


def _build_markdown(comparison: dict[str, Any], meta: dict[str, Any]) -> str:
    b = comparison["baseline"]
    t = comparison["news_adapter"]
    d = comparison["delta"]
    mode = meta.get("backtest_mode", "production")
    mode_cn = meta.get("backtest_mode_cn", "正式回测")
    initial = meta.get("initial_balance", 500)

    lines = [
        "# 新闻冲击网格回测报告",
        "",
        f"- 生成时间: {comparison['generated_at']}",
        f"- **Run ID: `{meta.get('run_id', '')}`**（命名规范 `{meta.get('run_id_format', 'YYYYMMDDHHmm_文件名')}` 北京时间）",
        f"- **回测类型: {mode_cn}** (`{mode}`)",
        f"- 初始资金: **${initial:.0f}**",
        f"- 杠杆: **{meta.get('leverage', 100):.0f}x**（保证金比例强平线 {meta.get('stop_out_margin_level', 50):.0f}%）",
        f"- 手数: {meta.get('default_lot', 0.01)} | 网格间距: {meta.get('base_spacing', 2.0)} | 最大层数: {meta.get('max_layers', 5)}",
        f"- 行情: `{meta.get('price_csv', '')}`",
        f"- 窗口: {meta.get('window_start', '')} ~ {meta.get('window_end', '')}（约 {meta.get('window_days', '?')} 天）",
        f"- 信号数: {meta.get('signal_count', 0)}（来源: {meta.get('signal_source', '')}）",
        "",
    ]

    if mode == "smoke":
        lines += [
            "> ⚠️ **发烟测试**：1~2 周仅验证模型能否跑通，**不具有策略指导意义**。",
            "> 正式结论请使用 `--years 1` 或 `--years 3` + 历史信号。",
            "",
        ]
    else:
        lines += [
            f"> ✅ **正式回测**：约 {meta.get('years', 1)} 年窗口，可用于毕设实验分析。",
            "",
        ]

    lines += [
        "## A/B 对比",
        "",
        "| 指标 | Baseline | News Adapter | Delta |",
        "|------|----------|--------------|-------|",
        f"| 最终权益 | {b['final_equity']:.2f} | {t['final_equity']:.2f} | {d['pnl_improvement']:+.2f} |",
        f"| 总盈亏 | {b['total_pnl']:.2f} | {t['total_pnl']:.2f} | {d['pnl_improvement']:+.2f} |",
        f"| 收益率% | {b['total_pnl']/initial*100:.2f} | {t['total_pnl']/initial*100:.2f} | {(t['total_pnl']-b['total_pnl'])/initial*100:+.2f} |",
        f"| 最大回撤% | {b['max_drawdown_pct']:.2f} | {t['max_drawdown_pct']:.2f} | {d['max_dd_reduction']:+.2f} |",
        f"| 成交笔数 | {b['trade_count']} | {t['trade_count']} | — |",
        f"| 冲击动作 | 0 | {t['shock_actions']} | — |",
        f"| 强平(stop_out) | {'是' if b.get('stopped_out') else '否'} | {'是' if t.get('stopped_out') else '否'} | — |",
        "",
        "## 结论",
        "",
    ]
    if mode == "smoke":
        lines.append("- 发烟测试通过即可；请勿将此结果写入论文正式实验章节。")
    elif d["pnl_improvement"] > 0:
        lines.append("- 新闻适配组在年度窗口内盈利优于 baseline，可继续扩大样本验证稳健性。")
    else:
        lines.append("- 年度窗口内未体现盈利优势，需检查信号质量或网格参数。")
    if meta.get("aligned"):
        lines.append("- ⚠️ 信号时间对齐模式，非真实历史对齐。")
    lines.append("")
    return "\n".join(lines)
