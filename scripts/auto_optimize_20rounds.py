"""无人值守：2 年窗口 × N 轮网格参数扫描，选出并记录最优候选。"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import importlib.util  # noqa: E402

_scan_path = PROJECT_ROOT / "scripts" / "scan_grid_params.py"
_spec = importlib.util.spec_from_file_location("scan_grid_params", _scan_path)
_scan_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_scan_mod)
run_scan = _scan_mod.run_scan

CHINA_TZ = timezone(timedelta(hours=8))


def _pick_candidates(rng: random.Random, round_idx: int) -> tuple[list[float], list[int]]:
    """每轮自选一小批 spacing×layers，覆盖不同搜索侧重。"""
    pools = [
        ([3.0, 4.0, 5.0, 6.0], [1, 2, 3]),
        ([2.0, 2.5, 3.5, 5.0], [2, 3, 4]),
        ([4.0, 5.0, 7.0, 8.0], [1, 2]),
        ([1.5, 2.0, 3.0, 4.0], [2, 3]),
        ([5.0, 6.0, 8.0, 10.0], [1, 2, 3]),
    ]
    spacings, layers = pools[round_idx % len(pools)]
    if rng.random() < 0.35:
        spacings = sorted(set(spacings + [round(rng.uniform(1.5, 9.0), 1)]))
    if rng.random() < 0.35:
        layers = sorted(set(layers + [rng.choice([1, 2, 3, 4])]))
    return spacings[:5], layers[:4]


def _score_row(row: dict) -> float:
    """越高越好：惩罚 stop_out，奖励 news PnL 与相对基线。"""
    stop = 1.0 if row.get("news_stopped_out") else 0.0
    pnl = float(row.get("news_pnl", 0) or 0)
    vs = float(row.get("news_vs_baseline", 0) or 0)
    dd = float(row.get("news_max_dd", 0) or 0)
    return pnl + vs - stop * 200.0 - dd * 0.5


def main() -> int:
    parser = argparse.ArgumentParser(description="20 轮自动网格优化")
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--initial", type=float, default=500.0)
    parser.add_argument("--seed", type=int, default=20260714)
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_ROOT / "docs" / "操作记录" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"auto_optimize_{stamp}.jsonl"
    summary_path = PROJECT_ROOT / "docs" / "操作记录" / f"{stamp[:8]}_自动优化摘要.md"

    config_path = PROJECT_ROOT / args.config
    if not config_path.is_file():
        config_path = PROJECT_ROOT / "config" / "config.example.yaml"

    best: dict | None = None
    best_score = float("-inf")
    ok_rounds = 0

    print(f"auto_optimize start years={args.years} rounds={args.rounds} → {jsonl_path}")

    for i in range(args.rounds):
        spacings, layers = _pick_candidates(rng, i)
        record: dict = {
            "round": i + 1,
            "spacings": spacings,
            "layers": layers,
            "ts": datetime.now(CHINA_TZ).isoformat(),
        }
        try:
            rows = run_scan(
                years=args.years,
                initial=args.initial,
                spacings=spacings,
                layers_list=layers,
                config_path=config_path,
            )
            record["results"] = rows
            record["ok"] = True
            ok_rounds += 1
            for row in rows:
                sc = _score_row(row)
                row["_score"] = round(sc, 4)
                if sc > best_score:
                    best_score = sc
                    best = {**row, "round": i + 1, "spacings": spacings, "layers": layers}
        except Exception as exc:  # noqa: BLE001 — 无人值守需吞掉单轮失败并继续
            record["ok"] = False
            record["error"] = str(exc)
            print(f"round {i+1} FAILED: {exc}")

        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        print(f"round {i+1}/{args.rounds} ok={record.get('ok')} best_score={best_score:.2f}")

    md = [
        f"# 自动优化摘要 {stamp[:8]}",
        "",
        f"> 生成：{datetime.now(CHINA_TZ).isoformat()}",
        f"> 窗口：过去 {args.years} 年 · 目标轮次 {args.rounds} · 成功 {ok_rounds}",
        "",
        f"- 明细：`docs/操作记录/logs/{jsonl_path.name}`",
        "",
    ]
    if best:
        md += [
            "## 当前最优候选",
            "",
            "```json",
            json.dumps(best, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "建议写入 `config.example.yaml` / 本地 `config.yaml` 的 `backtest.base_spacing` 与 `backtest.max_layers`（若字段对应）。",
            "",
        ]
    else:
        md.append("未得到任何成功轮次，请检查 History Data 与信号 parquet。\n")

    summary_path.write_text("\n".join(md), encoding="utf-8")
    print(f"summary → {summary_path}")
    if best:
        print(json.dumps({"best": best, "ok_rounds": ok_rounds}, ensure_ascii=False, default=str))
    return 0 if ok_rounds >= max(1, args.rounds // 4) else 2


if __name__ == "__main__":
    raise SystemExit(main())
