"""生成日终决策报告（Markdown + JSON），系统自己「说话」。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.decision_log import DecisionLogger  # noqa: E402

CHINA_TZ = timezone(timedelta(hours=8))


def _load_signals_day(signal_dir: Path, day: str) -> list[dict]:
    d = signal_dir / day
    if not d.is_dir():
        return []
    rows = []
    for p in sorted(d.glob("*.json")):
        rows.append(json.loads(p.read_text(encoding="utf-8")))
    return rows


def _flag_misjudgments(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        title = (r.get("title") or "") + " " + (r.get("reasoning") or "")
        flags = r.get("rule_flags") or []
        d = r.get("direction_cn", "")
        if any(k in title for k in ("下跌", "走低", "跌2", "WTI涨", "原油涨")):
            if d in ("中性", "利多") and "oil_spike" not in flags and "title_gold_fall" not in flags:
                out.append(r)
    return out[:10]


def build_report(day: str, runtime_dir: Path, signal_dir: Path) -> dict:
    logger = DecisionLogger(runtime_dir)
    rows = logger.read_jsonl(day)
    if not rows:
        rows = [
            DecisionLogger.from_signal(s, signal_path="")
            for s in _load_signals_day(signal_dir, day)
        ]

    levels = Counter(r.get("trend_level") for r in rows)
    dirs = Counter(r.get("direction_cn") for r in rows)
    executed = sum(1 for r in rows if r.get("mt5_executed"))
    rule_hits = sum(1 for r in rows if r.get("rule_flags"))

    equities = [r.get("bid") for r in rows if r.get("bid")]
    mis = _flag_misjudgments(rows)

    report = {
        "day": day,
        "generated_at": datetime.now(CHINA_TZ).isoformat(),
        "total_decisions": len(rows),
        "levels": dict(levels),
        "directions": dict(dirs),
        "mt5_executed_count": executed,
        "rule_adjustment_count": rule_hits,
        "price_first": equities[0] if equities else None,
        "price_last": equities[-1] if equities else None,
        "misjudgment_candidates": mis,
    }
    return report


def report_to_md(report: dict) -> str:
    lines = [
        f"# 决策日报 {report['day']}",
        "",
        f"> 生成时间：{report['generated_at']}",
        "",
        "## 汇总",
        "",
        f"- 决策条数：**{report['total_decisions']}**",
        f"- 等级分布：{report.get('levels', {})}",
        f"- 方向分布：{report.get('directions', {})}",
        f"- MT5 执行次数：**{report.get('mt5_executed_count', 0)}**",
        f"- 规则层修正：**{report.get('rule_adjustment_count', 0)}** 条",
        f"- 金价 bid（首/末）：{report.get('price_first')} → {report.get('price_last')}",
        "",
        "## 疑似误判（标题偏空但判中性/多）",
        "",
    ]
    mis = report.get("misjudgment_candidates") or []
    if not mis:
        lines.append("（无或未触发规则）")
    else:
        for m in mis:
            lines.append(f"- {m.get('news_time', '')[:19]} **{m.get('direction_cn')}** {m.get('title', '')[:50]}")
    lines.extend(["", "## 说明", "", "完整流水见 `data/runtime/decisions_{day}.jsonl`。".format(day=report["day"])])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default="", help="YYYY-MM-DD")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    import yaml

    with open(PROJECT_ROOT / args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    runtime_dir = Path(cfg.get("runtime", {}).get("dir", "data/runtime"))
    signal_dir = Path(cfg.get("mt5", {}).get("signal_dir", "data/signals"))

    day = args.day or datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
    report = build_report(day, runtime_dir, signal_dir)

    out_dir = PROJECT_ROOT / "docs" / "操作记录" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"daily_report_{day.replace('-', '')}.json"
    md_path = out_dir / f"daily_report_{day.replace('-', '')}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(report_to_md(report), encoding="utf-8")
    print(f"已生成: {md_path}")
    print(f"已生成: {json_path}")


if __name__ == "__main__":
    main()
