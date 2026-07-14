"""重新评分 sentiment_cache（Phase 2 权重/持续性更新后使用）。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def rescore(cache_dir: Path, force: bool = False) -> int:
    from src.nlp_engine import SentimentAnalyzer
    from src.trend_scorer import TrendScorer

    analyzer = SentimentAnalyzer.from_config()
    scorer = TrendScorer.from_config()
    count = 0

    for json_file in sorted(cache_dir.glob("*/*.json")):
        if json_file.name == "processed_ids.json":
            continue

        with json_file.open(encoding="utf-8") as f:
            data = json.load(f)

        if not force and "trend_score" in data and "duration" in data:
            continue

        data.pop("trend_score", None)
        data.pop("duration", None)

        source = data.get("news", {}).get("source", "")
        credibility = analyzer.get_source_credibility(source)
        trend = scorer.score(data, source_credibility=credibility)

        from src.trend_scorer.duration_estimator import estimate_duration

        duration = estimate_duration(data)
        merged = {**data, "trend_score": trend.to_dict(), "duration": duration}

        with json_file.open("w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)

        count += 1
        logger.info(
            "重评分 [%s] %s %s (composite=%.1f, duration=%sh)",
            trend.news_id,
            trend.direction_cn,
            trend.trend_level,
            trend.composite_score,
            duration.get("duration_hours"),
        )

    return count


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="重新趋势评分 sentiment_cache")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有 trend_score/duration")
    args = parser.parse_args()

    cache_dir = Path("data/sentiment_cache")
    n = rescore(cache_dir, force=args.force)
    print(f"重评分 {n} 条")


if __name__ == "__main__":
    main()
