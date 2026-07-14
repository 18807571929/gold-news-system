"""运行未来 K 线趋势判断并生成文档（不交易）。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter.naming import make_run_id, output_filename  # noqa: E402
from src.forecast.kline_trend import KlineTrendForecaster  # noqa: E402
from src.paths import get_history_data_root, load_config  # noqa: E402

CHINA_TZ = timezone(timedelta(hours=8))


def _write_markdown(report: dict, path: Path) -> None:
    lines = [
        f"# K 线趋势预测报告",
        "",
        f"- **生成时间：** {report['generated_at']}",
        f"- **周期：** {report['timeframe']}",
        f"- **预测根数：** {report['horizon_bars']} 根",
        f"- **综合判断：** {report['overall_direction_cn']}（置信度 {report['overall_confidence']:.0%}）",
        f"- **说明：** {report['note']}",
        "",
        "## 锚定 K 线（当前最新已收盘）",
        "",
        f"| 时间 | 开 | 高 | 低 | 收 |",
        f"|------|-----|-----|-----|-----|",
        f"| {report['anchor_bar']['datetime']} | {report['anchor_bar']['open']:.2f} | "
        f"{report['anchor_bar']['high']:.2f} | {report['anchor_bar']['low']:.2f} | {report['anchor_bar']['close']:.2f} |",
        "",
        "## 输入依据",
        "",
    ]
    inp = report["inputs"]
    lines += [
        f"- 新闻评分：{inp.get('news_score', 0)}",
        f"- 价格动量分：{inp.get('momentum_score', 0)}",
        f"- 综合分：{inp.get('combined_score', 0)}",
        f"- ATR({14})：{inp.get('atr', '—')}",
    ]
    if report.get("news_context"):
        nc = report["news_context"]
        lines.append(f"- 参考新闻：{nc.get('title', '—')}（{nc.get('time', '')}）")
    lines += [
        "",
        f"## 未来 {report['horizon_bars']} 根 K 线趋势判断",
        "",
        "| 第几根 | 预计时间(北京) | 方向 | 置信度 | 偏向 | 依据 |",
        "|--------|----------------|------|--------|------|------|",
    ]
    for f in report["forecasts"]:
        lines.append(
            f"| +{f['bar_index']} | {f['expected_datetime']} | {f['direction_cn']} | "
            f"{f['confidence']:.0%} | {f['expected_bias']} | {f['reason']} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_html(report: dict, path: Path, run_id: str) -> None:
    rows = ""
    for f in report["forecasts"]:
        cls = {"看涨": "up", "看跌": "down", "震荡": "flat"}.get(f["direction_cn"], "")
        rows += (
            f"<tr><td>+{f['bar_index']}</td><td>{f['expected_datetime']}</td>"
            f"<td class=\"{cls}\">{f['direction_cn']}</td><td>{f['confidence']:.0%}</td>"
            f"<td>{f['expected_bias']}</td><td class=\"reason\">{f['reason']}</td></tr>\n"
        )
    nc = report.get("news_context") or {}
    ab = report["anchor_bar"]
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>{run_id}_K线趋势预测</title>
  <style>
    body {{ font-family: "Microsoft YaHei", sans-serif; max-width: 960px; margin: 32px auto; padding: 0 20px; line-height: 1.6; color: #222; }}
    h1 {{ font-size: 22px; border-bottom: 3px solid #c9a227; padding-bottom: 8px; }}
    h2 {{ font-size: 16px; margin-top: 28px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 12px 0; }}
    th, td {{ border: 1px solid #ccc; padding: 8px 10px; text-align: center; }}
    th {{ background: #f5f0e6; }}
    td.reason {{ text-align: left; font-size: 12px; color: #444; }}
    .up {{ color: #c62828; font-weight: 600; }}
    .down {{ color: #1565c0; font-weight: 600; }}
    .flat {{ color: #666; }}
    .meta {{ background: #fafafa; border: 1px solid #eee; padding: 14px; border-radius: 6px; font-size: 14px; }}
    .tag {{ display: inline-block; background: #1565c0; color: #fff; padding: 2px 10px; border-radius: 4px; font-size: 12px; }}
    .note {{ background: #e3f2fd; border-left: 4px solid #1565c0; padding: 12px; margin: 16px 0; font-size: 13px; }}
  </style>
</head>
<body>
  <h1>K 线趋势预测（不交易）</h1>
  <p><span class="tag">{run_id}</span> &nbsp; {report['timeframe']} · 未来 <strong>{report['horizon_bars']}</strong> 根</p>
  <div class="meta">
    <strong>综合判断：</strong>{report['overall_direction_cn']} &nbsp; 置信度 {report['overall_confidence']:.0%}<br />
    <strong>锚定 K 线：</strong>{ab['datetime']} &nbsp; 收 {ab['close']:.2f}<br />
    <strong>新闻：</strong>{nc.get('title') or '（无缓存，仅价格动量）'}<br />
    新闻分 {report['inputs'].get('news_score', 0)} · 动量分 {report['inputs'].get('momentum_score', 0)} · ATR {report['inputs'].get('atr', '—')}
  </div>
  <div class="note">{report['note']}</div>
  <h2>未来 {report['horizon_bars']} 根 K 线趋势</h2>
  <table>
    <tr><th>第几根</th><th>预计时间(北京)</th><th>方向</th><th>置信度</th><th>偏向</th><th>依据</th></tr>
    {rows}
  </table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="未来 N 根 K 线趋势判断（不交易）")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--bars", type=int, default=4, help="预测 K 线根数，默认 4")
    parser.add_argument("--timeframe", default="H1", choices=["H1", "M15", "H4"])
    parser.add_argument("--output-dir", default="", help="输出目录，默认 docs/汇报")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    history_root = get_history_data_root(config)
    nlp_cfg = config.get("nlp", {})

    tf_file = {"H1": "XAUUSD_H1.csv", "M15": "XAUUSD_M15.csv", "H4": "XAUUSD_H1.csv"}
    price_csv = history_root / "data" / tf_file.get(args.timeframe.upper(), "XAUUSD_H1.csv")
    sentiment_dir = PROJECT_ROOT / nlp_cfg.get("output_dir", "data/sentiment_cache")

    forecaster = KlineTrendForecaster()
    report_obj = forecaster.run_from_paths(
        price_csv,
        horizon_bars=args.bars,
        timeframe=args.timeframe,
        sentiment_cache_dir=sentiment_dir,
    )
    report = report_obj.to_dict()

    run_id = make_run_id()
    out_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "docs" / "汇报"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / output_filename(run_id, "K线趋势预测.json")
    md_path = out_dir / output_filename(run_id, "K线趋势预测.md")
    html_path = out_dir / output_filename(run_id, "K线趋势预测.html")

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)
    _write_html(report, html_path, run_id)

    summary = {
        "run_id": run_id,
        "overall_direction_cn": report["overall_direction_cn"],
        "overall_confidence": report["overall_confidence"],
        "horizon_bars": report["horizon_bars"],
        "forecasts": report["forecasts"],
        "files": {
            "json": str(json_path),
            "markdown": str(md_path),
            "html": str(html_path),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
