"""多源新闻共识因子：匹配相近标题，统计多源方向一致性。"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip(), b.strip()).ratio()


def calc_multi_source_consensus(
    current: dict[str, Any],
    sentiment_cache_dir: Path,
    similarity_threshold: float = 0.55,
    lookback_files: int = 200,
) -> dict[str, Any]:
    """与缓存中其他来源的相近新闻对比，计算共识分。"""
    title = (current.get("news") or {}).get("title") or ""
    source = (current.get("news") or {}).get("source") or ""
    direction = (current.get("sentiment") or {}).get("direction", "neutral")
    if not title:
        return {"score": 50.0, "matched_sources": [], "same_direction_count": 0}

    peers: list[dict[str, Any]] = []
    files = sorted(sentiment_cache_dir.glob("*/*.json"), reverse=True)[:lookback_files]
    for path in files:
        if path.name == "processed_ids.json":
            continue
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        peer_title = (data.get("news") or {}).get("title") or ""
        peer_source = (data.get("news") or {}).get("source") or ""
        if not peer_title or peer_source == source:
            continue
        if data.get("news_id") == current.get("news_id"):
            continue
        if _similar(title, peer_title) >= similarity_threshold:
            peers.append(data)

    if not peers:
        raw = abs(float((current.get("sentiment") or {}).get("score", 0)))
        return {
            "score": min(raw * 0.5 + 25, 100),
            "matched_sources": [],
            "same_direction_count": 0,
            "note": "单源，无跨源匹配",
        }

    same_dir = sum(
        1 for p in peers if (p.get("sentiment") or {}).get("direction") == direction
    )
    ratio = same_dir / len(peers)
    consensus_score = min(30 + ratio * 70, 100)

    return {
        "score": consensus_score,
        "matched_sources": list({(p.get("news") or {}).get("source", "") for p in peers}),
        "same_direction_count": same_dir,
        "peer_count": len(peers),
    }
