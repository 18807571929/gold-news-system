"""生成以交易日志为主体的回测报告（HTML + Markdown）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

ACTION_CN = {"fill": "成交", "take_profit": "止盈", "stop_out": "强平", "close_reverse": "平反向仓"}
SIDE_CN = {"buy": "买", "sell": "卖"}


def _load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d %H:%M")
    df["action_cn"] = df["action"].map(ACTION_CN).fillna(df["action"])
    df["side_cn"] = df["side"].map(SIDE_CN).fillna(df["side"])
    df["layer"] = df["layer"].apply(lambda x: "" if pd.isna(x) else str(int(x)) if float(x) == int(float(x)) else str(x))
    df["pnl"] = df["pnl"].apply(lambda x: "" if pd.isna(x) else f"{float(x):+.2f}")
    return df


def _monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    raw = pd.read_csv(df.attrs.get("_source", "")) if False else df.copy()
    # rebuild from formatted df is lossy; caller passes raw
    return pd.DataFrame()


def _monthly_from_path(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["month"] = df["datetime"].dt.to_period("M").astype(str)
    rows = []
    for month, g in df.groupby("month"):
        row = {"月份": month, "合计": len(g)}
        for act in ["fill", "take_profit", "stop_out"]:
            row[ACTION_CN.get(act, act)] = int((g["action"] == act).sum())
        pnl = g.loc[g["action"].isin(["take_profit", "stop_out", "close_reverse"]), "pnl"].sum()
        row["盈亏(USD)"] = round(float(pnl), 2) if pd.notna(pnl) else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def _table_html(df: pd.DataFrame, cols: list[str], headers: list[str]) -> str:
    lines = ["<table class=\"log\">", "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"]
    for _, r in df[cols].iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cls = ""
            if c == "pnl" and v and str(v).startswith("-"):
                cls = ' class="neg"'
            elif c == "pnl" and v and str(v).startswith("+"):
                cls = ' class="pos"'
            if c == "action_cn" and r[c] == "强平":
                cls = ' class="neg"'
            cells.append(f"<td{cls}>{v}</td>")
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _monthly_html(monthly: pd.DataFrame) -> str:
    cols = list(monthly.columns)
    headers = cols
    lines = ["<table class=\"summary\">", "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"]
    for _, r in monthly.iterrows():
        lines.append("<tr>" + "".join(f"<td>{r[c]}</td>" for c in cols) + "</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def generate_html(run_id: str, comp: dict, baseline_path: Path, news_path: Path, out: Path) -> None:
    meta = comp["meta"]
    b_raw = baseline_path
    n_raw = news_path
    b = _load_trades(baseline_path)
    n = _load_trades(news_path)
    b_monthly = _monthly_from_path(b_raw)
    n_monthly = _monthly_from_path(n_raw)

    log_cols = ["datetime", "action_cn", "side_cn", "volume", "price", "layer", "pnl"]
    log_headers = ["时间(UTC)", "动作", "方向", "手数", "价格", "层", "盈亏"]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>{run_id} — 三年回测交易日志</title>
  <style>
    body {{ font-family: "Microsoft YaHei", sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 20px 40px; color: #222; font-size: 14px; line-height: 1.5; }}
    h1 {{ font-size: 22px; border-bottom: 3px solid #c9a227; padding-bottom: 8px; }}
    h2 {{ font-size: 16px; margin-top: 28px; color: #333; }}
    h3 {{ font-size: 14px; margin-top: 20px; color: #555; }}
    .meta {{ background: #f7f7f7; padding: 12px 16px; border-radius: 6px; font-size: 13px; margin: 12px 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0 16px; }}
    th, td {{ border: 1px solid #ccc; padding: 5px 8px; text-align: center; font-size: 12px; }}
    th {{ background: #f5f0e6; position: sticky; top: 0; }}
    table.log td:nth-child(5) {{ text-align: right; }}
    table.log td:last-child {{ text-align: right; }}
    table.summary td:first-child {{ text-align: left; }}
    .neg {{ color: #c62828; font-weight: 600; }}
    .pos {{ color: #2e7d32; }}
    .scroll {{ max-height: 480px; overflow-y: auto; border: 1px solid #ddd; border-radius: 4px; }}
    .note {{ font-size: 13px; color: #666; margin: 8px 0; }}
    code {{ background: #eee; padding: 1px 5px; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>三年回测交易日志</h1>
  <p><strong>Run ID：</strong><code>{run_id}</code> &nbsp;|&nbsp; 窗口 2023-07-11 ~ 2026-07-10 &nbsp;|&nbsp; $500 · 100x杠杆</p>
  <div class="meta">
    完整 CSV：<code>{run_id}_trades_baseline.csv</code>（{len(b)} 笔）、
    <code>{run_id}_trades_news_adapter.csv</code>（{len(n)} 笔）。
    动作：成交(fill) / 止盈(take_profit) / 强平(stop_out)。
  </div>

  <h2>一、Baseline 交易日志（{len(b)} 笔）</h2>
  <h3>按月汇总</h3>
  {_monthly_html(b_monthly)}
  <h3>全部明细</h3>
  <div class="scroll">
  {_table_html(b, log_cols, log_headers)}
  </div>

  <h2>二、News Adapter 交易日志（{len(n)} 笔）</h2>
  <h3>按月汇总</h3>
  {_monthly_html(n_monthly)}
  <h3>全部明细</h3>
  <div class="scroll">
  {_table_html(n, log_cols, log_headers)}
  </div>

  <p class="note">源文件目录：E:\\gold-news-system\\docs\\汇报\\</p>
</body>
</html>
"""
    out.write_text(html, encoding="utf-8")


def generate_md(run_id: str, comp: dict, baseline_path: Path, news_path: Path, out: Path) -> None:
    b = _load_trades(baseline_path)
    n = _load_trades(news_path)
    b_m = _monthly_from_path(baseline_path)
    n_m = _monthly_from_path(news_path)

    lines = [
        f"# {run_id} 三年回测交易日志",
        "",
        "- 窗口：2023-07-11 ~ 2026-07-10",
        "- 初始资金：$500 · 100x 杠杆",
        "",
        "## Baseline 按月汇总",
        "",
        b_m.to_markdown(index=False),
        "",
        f"## Baseline 全部明细（{len(b)} 笔）",
        "",
        "| 时间(UTC) | 动作 | 方向 | 手数 | 价格 | 层 | 盈亏 |",
        "|-----------|------|------|------|------|-----|------|",
    ]
    for _, r in b.iterrows():
        lines.append(f"| {r['datetime']} | {r['action_cn']} | {r['side_cn']} | {r['volume']} | {r['price']} | {r['layer']} | {r['pnl']} |")

    lines += [
        "",
        "## News Adapter 按月汇总",
        "",
        n_m.to_markdown(index=False),
        "",
        f"## News Adapter 全部明细（{len(n)} 笔）",
        "",
        "| 时间(UTC) | 动作 | 方向 | 手数 | 价格 | 层 | 盈亏 |",
        "|-----------|------|------|------|------|-----|------|",
    ]
    for _, r in n.iterrows():
        lines.append(f"| {r['datetime']} | {r['action_cn']} | {r['side_cn']} | {r['volume']} | {r['price']} | {r['layer']} | {r['pnl']} |")

    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--docs-dir", default=str(PROJECT_ROOT / "docs" / "汇报"))
    args = parser.parse_args()

    docs = Path(args.docs_dir)
    run_id = args.run_id
    comp = json.loads((docs / f"{run_id}_comparison.json").read_text(encoding="utf-8"))
    baseline = docs / f"{run_id}_trades_baseline.csv"
    news = docs / f"{run_id}_trades_news_adapter.csv"

    html_out = docs / f"{run_id}_回测报告.html"
    md_out = docs / f"{run_id}_三年回测报告.md"

    generate_html(run_id, comp, baseline, news, html_out)
    generate_md(run_id, comp, baseline, news, md_out)
    print(json.dumps({"html": str(html_out), "md": str(md_out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
