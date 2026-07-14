"""从已有 signal JSON 回填 decisions_YYYY-MM-DD.jsonl。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.paths import load_config  # noqa: E402
from src.runtime.decision_log import DecisionLogger  # noqa: E402


def backfill_day(day: str, *, signal_dir: Path, runtime_dir: Path, force: bool = False) -> int:
    logger = DecisionLogger(runtime_dir)
    out_path = logger._jsonl_path(day)
    if out_path.exists() and not force:
        existing = logger.read_jsonl(day)
        if existing:
            print(f"跳过 {day}：已有 {len(existing)} 条（加 --force 覆盖）")
            return 0

    sig_day = signal_dir / day
    if not sig_day.is_dir():
        print(f"无信号目录: {sig_day}")
        return 0

    rows: list[dict] = []
    for p in sorted(sig_day.glob("*.json")):
        signal = json.loads(p.read_text(encoding="utf-8"))
        rec = DecisionLogger.from_signal(signal, signal_path=str(p))
        rec["record_type"] = "decision"
        rec["logged_at"] = signal.get("timestamp") or rec.get("news_time") or ""
        rows.append(rec)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"回填 {day}: {len(rows)} 条 → {out_path}")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 signal JSON 回填 decisions JSONL")
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="覆盖已有 JSONL")
    args = parser.parse_args()

    config = load_config()
    signal_dir = Path(config.get("mt5", {}).get("signal_dir", "data/signals"))
    runtime_dir = Path(config.get("runtime", {}).get("dir", "data/runtime"))
    backfill_day(args.day, signal_dir=signal_dir, runtime_dir=runtime_dir, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
