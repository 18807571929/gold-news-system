"""简易控制台：读取 market_state.json + 最新决策 JSONL。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.paths import load_config  # noqa: E402
from src.runtime.decision_log import DecisionLogger  # noqa: E402

CHINA_TZ = timezone(timedelta(hours=8))


def main() -> int:
    parser = argparse.ArgumentParser(description="运行时控制台快照")
    parser.add_argument("--day", default="", help="YYYY-MM-DD，默认今天")
    parser.add_argument("--tail", type=int, default=8, help="显示最近 N 条决策")
    args = parser.parse_args()

    config = load_config()
    runtime_dir = Path(config.get("runtime", {}).get("dir", "data/runtime"))
    day = args.day or datetime.now(CHINA_TZ).strftime("%Y-%m-%d")

    state_path = runtime_dir / "market_state.json"
    print("=" * 60)
    print(f"  黄金新闻系统 · 运行时控制台  ({day})")
    print("=" * 60)

    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        print(f"行情更新: {state.get('updated_at', '-')}")
        print(
            f"  {state.get('symbol', 'GOLD')}  "
            f"bid={state.get('bid')}  ask={state.get('ask')}  "
            f"equity={state.get('equity')}  balance={state.get('balance')}"
        )
    else:
        print("market_state.json 尚未生成（请先运行 news_engine）")

    rows = DecisionLogger(runtime_dir).read_jsonl(day)
    if not rows:
        sig_dir = Path(config.get("mt5", {}).get("signal_dir", "data/signals")) / day
        if sig_dir.is_dir():
            for p in sorted(sig_dir.glob("*.json")):
                rows.append(
                    DecisionLogger.from_signal(
                        json.loads(p.read_text(encoding="utf-8")),
                        signal_path=str(p),
                    )
                )

    print(f"\n决策记录: {len(rows)} 条")
    print("-" * 60)
    for r in rows[-args.tail :]:
        title = (r.get("title") or "")[:36]
        flags = ",".join(r.get("rule_flags") or []) or "-"
        print(
            f"{r.get('news_time', '')[:16]}  "
            f"{r.get('direction_cn', '?'):<4} {r.get('trend_level', '?'):<3}  "
            f"bid={r.get('bid', '-')}  flags={flags}  {title}"
        )
    print("-" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
