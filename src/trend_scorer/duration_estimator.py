"""趋势持续性评估：基于事件类型估计影响时长。"""

from __future__ import annotations

from typing import Any

# fine_category → (duration_label, duration_hours, level_boost)
DURATION_RULES: dict[str, tuple[str, int, float]] = {
    "非农数据": ("short", 4, 0.0),
    "通胀数据": ("short", 6, 0.0),
    "利率决策": ("medium", 24, 5.0),
    "央行政策": ("medium", 48, 5.0),
    "地缘冲突": ("long", 72, 10.0),
    "其他": ("medium", 12, 0.0),
}

COARSE_FALLBACK: dict[str, tuple[str, int, float]] = {
    "宏观数据": ("short", 6, 0.0),
    "地缘政治": ("long", 72, 10.0),
    "金融事务": ("medium", 24, 3.0),
    "央行政策": ("medium", 48, 5.0),
    "市场分析": ("medium", 12, 0.0),
}


def estimate_duration(sentiment_result: dict[str, Any]) -> dict[str, Any]:
    """根据事件分类估计趋势持续时长，并给出 L 等级微调建议。"""
    events = sentiment_result.get("events") or []
    cot = sentiment_result.get("chain_of_thought") or {}

    categories: list[str] = []
    for ev in events:
        if isinstance(ev, dict) and ev.get("fine_category"):
            categories.append(str(ev["fine_category"]))
    if not categories and cot.get("hop3_fine_category"):
        categories.append(str(cot["hop3_fine_category"]))

    if not categories:
        return {
            "duration_label": "medium",
            "duration_hours": 12,
            "level_boost": 0.0,
            "primary_category": "其他",
        }

    best = ("medium", 12, 0.0, "其他")
    for cat in categories:
        rule = DURATION_RULES.get(cat) or COARSE_FALLBACK.get(
            next((ev.get("coarse_category", "") for ev in events if isinstance(ev, dict)), ""),
            ("medium", 12, 0.0),
        )
        label, hours, boost = rule[0], rule[1], rule[2]
        if hours > best[1]:
            best = (label, hours, boost, cat)

    return {
        "duration_label": best[0],
        "duration_hours": best[1],
        "level_boost": best[2],
        "primary_category": best[3],
    }


def apply_duration_to_level(composite_score: float, duration: dict[str, Any]) -> tuple[float, str, str]:
    """根据持续性对综合分做小幅调整，并返回说明。"""
    boost = float(duration.get("level_boost", 0))
    if boost <= 0:
        return composite_score, "", ""

    sign = 1 if composite_score >= 0 else -1
    adjusted = composite_score + sign * boost
    adjusted = max(-100.0, min(100.0, adjusted))
    note = f"持续性加成 +{boost:.0f}（{duration.get('primary_category')}，约{duration.get('duration_hours')}h）"
    return adjusted, note, duration.get("duration_label", "medium")
