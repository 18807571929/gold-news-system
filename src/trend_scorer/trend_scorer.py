"""五因子综合评分与 L1-L4 趋势强度分级。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from src.nlp_engine.consensus import calc_multi_source_consensus
from src.nlp_engine.direction_rules import apply_direction_rules

from .duration_estimator import apply_duration_to_level, estimate_duration
from .price_context import get_price_context_score, momentum_at_time

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_DIRECTION_THRESHOLD = 12.0
DEFAULT_PRICE_BLEND = 0.20

TREND_LEVELS = [
    ("L1", "弱", (-30, 30)),
    ("L2", "中等", None),
    ("L3", "强", None),
    ("L4", "极强", None),
]


@dataclass
class TrendScore:
    news_id: str
    composite_score: float
    direction: str
    direction_cn: str
    trend_level: str
    trend_level_cn: str
    factors: dict[str, float]
    scored_at: str
    rule_adjustment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "news_id": self.news_id,
            "scored_at": self.scored_at,
            "composite_score": round(self.composite_score, 2),
            "direction": self.direction,
            "direction_cn": self.direction_cn,
            "trend_level": self.trend_level,
            "trend_level_cn": self.trend_level_cn,
            "factors": {k: round(v, 2) for k, v in self.factors.items()},
        }
        if self.rule_adjustment:
            out["rule_adjustment"] = self.rule_adjustment
        return out


class TrendScorer:
    """基于情感分析结果计算综合评分与趋势等级。"""

    DEFAULT_WEIGHTS = {
        "sentiment": 0.35,
        "credibility": 0.15,
        "novelty": 0.20,
        "consensus": 0.15,
        "magnitude": 0.15,
    }

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        sentiment_cache_dir: str | Path = "data/sentiment_cache",
        history_window: int = 50,
        *,
        price_blend: float = DEFAULT_PRICE_BLEND,
        direction_threshold: float = DEFAULT_DIRECTION_THRESHOLD,
        history_root: Path | None = None,
    ) -> None:
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self.sentiment_cache_dir = Path(sentiment_cache_dir)
        self.history_window = history_window
        self.price_blend = price_blend
        self.direction_threshold = direction_threshold
        self.history_root = history_root
        self._recent_summaries: list[str] = []

    @classmethod
    def from_config(cls, config_path: str | Path = "config/config.yaml") -> TrendScorer:
        path = Path(config_path)
        with path.open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        scoring_cfg = config.get("trend_scoring", {})
        nlp_cfg = config.get("nlp", {})
        paths_cfg = config.get("paths", {})
        history_root = Path(paths_cfg.get("history_data", "E:/量化项目/02 History Data"))
        return cls(
            weights=scoring_cfg.get("weights", cls.DEFAULT_WEIGHTS),
            sentiment_cache_dir=nlp_cfg.get("output_dir", "data/sentiment_cache"),
            price_blend=float(scoring_cfg.get("price_blend", DEFAULT_PRICE_BLEND)),
            direction_threshold=float(scoring_cfg.get("direction_threshold", DEFAULT_DIRECTION_THRESHOLD)),
            history_root=history_root,
        )

    def _classify_trend_level(self, score: float) -> tuple[str, str]:
        abs_score = abs(score)
        if abs_score <= 30:
            return "L1", "弱"
        if abs_score <= 60:
            return "L2", "中等"
        if abs_score <= 85:
            return "L3", "强"
        return "L4", "极强"

    def _calc_novelty(self, summary: str) -> float:
        if not self._recent_summaries:
            return 80.0
        max_sim = max(
            SequenceMatcher(None, summary, prev).ratio()
            for prev in self._recent_summaries[-self.history_window:]
        )
        return max(0.0, (1.0 - max_sim) * 100)

    def _calc_magnitude(self, sentiment_score: float, matched_count: int) -> float:
        base = min(abs(sentiment_score), 100)
        boost = min(matched_count * 5, 20)
        return min(base + boost, 100)

    def _calc_consensus(self, sentiment_result: dict[str, Any], sentiment_score: float) -> float:
        """多源共识：优先跨源匹配，否则用单源情感强度近似。"""
        precomputed = sentiment_result.get("consensus")
        if isinstance(precomputed, dict) and "score" in precomputed:
            return float(precomputed["score"])

        cache_dir = self.sentiment_cache_dir
        if cache_dir.is_dir():
            info = calc_multi_source_consensus(sentiment_result, cache_dir)
            if info.get("peer_count", 0) > 0:
                return float(info["score"])

        return min(abs(sentiment_score) * 0.8 + 20, 100)

    def score(
        self,
        sentiment_result: dict[str, Any],
        source_credibility: float = 0.7,
    ) -> TrendScore:
        """对单条情感分析结果计算综合评分。"""
        news_id = sentiment_result.get("news_id", "")
        sentiment = sentiment_result.get("sentiment", {})
        raw_score = float(sentiment.get("score", 0))
        direction = sentiment.get("direction", "neutral")
        direction_cn = sentiment.get("direction_cn", "中性")

        news_info = sentiment_result.get("news", {})
        summary = news_info.get("title", "") or ""

        sentiment_factor = max(-100.0, min(100.0, raw_score))
        credibility_factor = (source_credibility * 2 - 1) * 100
        novelty_factor = self._calc_novelty(summary)
        if direction == "bullish":
            novelty_factor = novelty_factor
        elif direction == "bearish":
            novelty_factor = -novelty_factor * 0.3 + novelty_factor * 0.7

        matched_count = len(sentiment_result.get("matched_terms", []))
        magnitude_factor = self._calc_magnitude(raw_score, matched_count)
        consensus_factor = self._calc_consensus(sentiment_result, raw_score)

        factors = {
            "sentiment": sentiment_factor,
            "credibility": credibility_factor * (1 if raw_score >= 0 else -1) if raw_score != 0 else 0,
            "novelty": novelty_factor * (1 if raw_score >= 0 else -1) if raw_score != 0 else 0,
            "consensus": consensus_factor * (1 if raw_score >= 0 else -1) if raw_score != 0 else 0,
            "magnitude": magnitude_factor * (1 if raw_score >= 0 else -1) if raw_score != 0 else 0,
        }

        composite = sum(factors[k] * self.weights[k] for k in self.weights)
        composite = max(-100.0, min(100.0, composite))

        price_score = 0.0
        price_meta: dict[str, Any] = {}
        if self.history_root and self.price_blend > 0:
            try:
                price_score, price_meta = get_price_context_score(self.history_root)
                composite = composite * (1 - self.price_blend) + price_score * self.price_blend
                composite = max(-100.0, min(100.0, composite))
            except Exception as exc:
                logger.debug("价格上下文不可用: %s", exc)

        duration = estimate_duration(sentiment_result)
        composite, duration_note, _ = apply_duration_to_level(composite, duration)

        th = self.direction_threshold
        if composite > th:
            direction, direction_cn = "bullish", "利多"
        elif composite < -th:
            direction, direction_cn = "bearish", "利空"
        else:
            direction, direction_cn = "neutral", "中性"

        title = news_info.get("title", "") or ""
        summary = news_info.get("summary", "") or ""
        rule_adj = apply_direction_rules(title, summary, direction, composite)
        if rule_adj.flags:
            direction = rule_adj.direction
            direction_cn = rule_adj.direction_cn
            composite = rule_adj.composite_score
            level, level_cn = self._classify_trend_level(composite)
        else:
            level, level_cn = self._classify_trend_level(composite)

        rule_meta = {"flags": rule_adj.flags, "note": rule_adj.note} if rule_adj.flags else None

        if summary:
            self._recent_summaries.append(summary)
            if len(self._recent_summaries) > self.history_window:
                self._recent_summaries = self._recent_summaries[-self.history_window:]

        return TrendScore(
            news_id=news_id,
            composite_score=composite,
            direction=direction,
            direction_cn=direction_cn,
            trend_level=level,
            trend_level_cn=level_cn,
            factors={
                "sentiment": sentiment_factor,
                "credibility": abs(credibility_factor),
                "novelty": novelty_factor,
                "consensus": consensus_factor,
                "magnitude": magnitude_factor,
                "price_context": price_score,
                "price_momentum": price_meta.get("momentum_score", 0.0) if price_meta else 0.0,
            },
            scored_at=datetime.now(CHINA_TZ).isoformat(),
            rule_adjustment=rule_meta,
        )

    def process_sentiment_cache(
        self,
        output_dir: str | Path | None = None,
        credibility_fn=None,
    ) -> list[dict]:
        """批量评分 sentiment_cache 中的所有分析结果。"""
        out_dir = Path(output_dir or self.sentiment_cache_dir)
        results: list[dict] = []

        for json_file in sorted(self.sentiment_cache_dir.glob("*/*.json")):
            if json_file.name in ("processed_ids.json",):
                continue

            with json_file.open(encoding="utf-8") as f:
                sentiment_data = json.load(f)

            if "trend_score" in sentiment_data and "duration" in sentiment_data:
                continue

            source = sentiment_data.get("news", {}).get("source", "")
            credibility = credibility_fn(source) if credibility_fn else 0.7
            trend = self.score(sentiment_data, source_credibility=credibility)
            duration = estimate_duration(sentiment_data)

            merged = {
                **sentiment_data,
                "trend_score": trend.to_dict(),
                "duration": duration,
            }
            if trend.rule_adjustment:
                merged["rule_adjustment"] = trend.rule_adjustment
            with json_file.open("w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)

            results.append(merged)
            logger.info(
                "趋势评分 [%s] %s %s (composite=%.1f)",
                trend.news_id,
                trend.direction_cn,
                trend.trend_level,
                trend.composite_score,
            )

        return results


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    from src.nlp_engine import SentimentAnalyzer

    analyzer = SentimentAnalyzer.from_config()
    scorer = TrendScorer.from_config()
    results = scorer.process_sentiment_cache(credibility_fn=analyzer.get_source_credibility)
    print(f"本次评分 {len(results)} 条")


if __name__ == "__main__":
    main()
