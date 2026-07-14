"""标题/摘要规则层：修正 LLM 对黄金方向的明显误判（hybrid 方案）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RuleAdjustment:
    direction: str
    direction_cn: str
    composite_score: float
    flags: list[str] = field(default_factory=list)
    note: str = ""


_BEARISH_PATTERNS = [
    (re.compile(r"金银下跌|金价下跌|黄金.*下跌|现货黄金.*跌|金价.*走低|黄金.*走低|失守\d+"), "title_gold_fall"),
    (re.compile(r"WTI.*涨|原油涨超|油价大涨|国际油价.*涨|布伦特.*涨"), "oil_spike"),
    (re.compile(r"能源暴跌|油价大跌|原油大跌"), "oil_crash_cpi"),
]

_BULLISH_PATTERNS = [
    (re.compile(r"金银上涨|金价上涨|黄金.*上涨|现货黄金.*涨"), "title_gold_rise"),
    (re.compile(r"避险|地缘.*升级|冲突升级|袭击"), "safe_haven"),
]


def _dir_cn(direction: str) -> str:
    return {"bullish": "利多", "bearish": "利空", "neutral": "中性"}.get(direction, "中性")


def apply_direction_rules(
    title: str,
    summary: str,
    direction: str,
    composite_score: float,
    *,
    score_bump: float = 25.0,
) -> RuleAdjustment:
    """根据标题/摘要关键词覆盖或强化方向。"""
    text = f"{title} {summary}".strip()
    flags: list[str] = []
    new_dir = direction
    score = composite_score

    for pat, flag in _BEARISH_PATTERNS:
        if pat.search(text):
            flags.append(flag)
            if new_dir != "bearish":
                new_dir = "bearish"
                if score > 0:
                    score = -max(abs(score), score_bump)
                else:
                    score = min(score, -score_bump)

    for pat, flag in _BULLISH_PATTERNS:
        if pat.search(text):
            flags.append(flag)
            if "title_gold_fall" in flags or "oil_spike" in flags:
                continue
            if new_dir != "bullish" and "safe_haven" in flag:
                new_dir = "bullish"
                score = max(score, score_bump)

    if "title_gold_fall" in flags and direction == "bullish":
        flags.append("blocked_bullish_on_fall_title")

    note = ""
    if flags:
        note = f"规则层: {', '.join(flags)}"

    return RuleAdjustment(
        direction=new_dir,
        direction_cn=_dir_cn(new_dir),
        composite_score=score,
        flags=flags,
        note=note,
    )
