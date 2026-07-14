"""决策流水 JSONL + 市场状态快照（供控制台/日报读取）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

CHINA_TZ = timezone(timedelta(hours=8))


class DecisionLogger:
    def __init__(self, runtime_dir: str | Path = "data/runtime") -> None:
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def _jsonl_path(self, day: str | None = None) -> Path:
        day = day or datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
        return self.runtime_dir / f"decisions_{day}.jsonl"

    def append(self, record: dict[str, Any]) -> Path:
        record = {
            "record_type": "decision",
            "logged_at": datetime.now(CHINA_TZ).isoformat(),
            **record,
        }
        day = (record.get("news_time") or record.get("logged_at", ""))[:10]
        if len(day) < 10:
            day = datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
        path = self._jsonl_path(day)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    @staticmethod
    def from_signal(signal: dict[str, Any], *, signal_path: str = "") -> dict[str, Any]:
        news = signal.get("news_summary") or {}
        impact = signal.get("impact_analysis") or {}
        advice = signal.get("trade_advice") or {}
        exec_ = signal.get("execution") or {}
        risk = signal.get("risk_check") or {}
        market = signal.get("market_snapshot") or {}
        rules = signal.get("rule_adjustment") or {}

        return {
            "news_id": signal.get("news_id", ""),
            "news_time": news.get("time", ""),
            "title": news.get("title", ""),
            "source": news.get("source", ""),
            "direction": impact.get("direction", ""),
            "direction_cn": impact.get("direction_cn", ""),
            "trend_level": impact.get("trend_level", ""),
            "composite_score": impact.get("composite_score"),
            "action_cn": advice.get("action_cn", ""),
            "reasoning": advice.get("reasoning", ""),
            "bid": market.get("bid"),
            "rule_flags": rules.get("flags", []),
            "rule_note": rules.get("note", ""),
            "risk_passed": risk.get("passed"),
            "mt5_executed": bool(exec_.get("executed")),
            "mt5_actions": exec_.get("actions_taken") or [],
            "signal_path": signal_path,
        }

    def write_market_state(self, state: dict[str, Any]) -> Path:
        path = self.runtime_dir / "market_state.json"
        payload = {
            "updated_at": datetime.now(CHINA_TZ).isoformat(),
            **state,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_jsonl(self, day: str | None = None) -> list[dict[str, Any]]:
        path = self._jsonl_path(day)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
