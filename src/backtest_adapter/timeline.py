"""新闻冲击时间线：信号 → 活跃窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .policy import ShockPolicy, merge_policies


@dataclass
class ShockWindow:
    start: datetime
    end: datetime
    policy: ShockPolicy


class ShockTimeline:
    """按 bar 时间查询当前活跃的新闻冲击策略。"""

    def __init__(self, signals: list[dict[str, Any]]) -> None:
        self.windows: list[ShockWindow] = []
        for sig in signals:
            start = pd.Timestamp(sig["timestamp"]).tz_convert("UTC").to_pydatetime()
            hours = int(sig.get("shock_duration_hours", sig.get("duration_hours", 12)))
            end = start + timedelta(hours=hours)
            self.windows.append(
                ShockWindow(
                    start=start,
                    end=end,
                    policy=ShockPolicy.from_signal(sig),
                )
            )
        self.windows.sort(key=lambda w: w.start)

    def policy_at(self, ts: datetime) -> ShockPolicy | None:
        ts_utc = pd.Timestamp(ts).tz_convert("UTC").to_pydatetime()
        active: ShockPolicy | None = None
        for win in self.windows:
            if win.start <= ts_utc < win.end:
                active = merge_policies(active, win.policy)
        return active

    def events_dataframe(self) -> pd.DataFrame:
        rows = []
        for win in self.windows:
            p = win.policy
            rows.append(
                {
                    "news_id": p.news_id,
                    "start": win.start,
                    "end": win.end,
                    "trend_level": p.trend_level,
                    "direction": p.direction,
                    "grid_spacing": p.grid_spacing,
                    "title": p.title,
                }
            )
        return pd.DataFrame(rows)
