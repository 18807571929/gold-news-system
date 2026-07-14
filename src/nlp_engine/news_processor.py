"""批量处理 news_cache 中的新闻，输出情感分析结果。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from .sentiment_analyzer import SentimentAnalyzer

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))


class NewsProcessor:
    """读取新闻缓存，执行情感分析并写入 sentiment_cache。"""

    def __init__(
        self,
        news_cache_dir: str | Path = "data/news_cache",
        output_dir: str | Path = "data/sentiment_cache",
        analyzer: SentimentAnalyzer | None = None,
    ) -> None:
        self.news_cache_dir = Path(news_cache_dir)
        self.output_dir = Path(output_dir)
        self.analyzer = analyzer or SentimentAnalyzer.from_config()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._processed_path = self.output_dir / "processed_ids.json"
        self._processed_ids: set[str] = self._load_processed_ids()

    @classmethod
    def from_config(cls, config_path: str | Path = "config/config.yaml") -> NewsProcessor:
        path = Path(config_path)
        with path.open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        jin10_cfg = config.get("news_sources", {}).get("jin10", {})
        nlp_cfg = config.get("nlp", {})
        return cls(
            news_cache_dir=jin10_cfg.get("cache_dir", "data/news_cache"),
            output_dir=nlp_cfg.get("output_dir", "data/sentiment_cache"),
            analyzer=SentimentAnalyzer.from_config(config_path),
        )

    def _load_processed_ids(self) -> set[str]:
        if not self._processed_path.exists():
            return set()
        try:
            with self._processed_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            return set()

    def _persist_processed_ids(self) -> None:
        with self._processed_path.open("w", encoding="utf-8") as f:
            json.dump(sorted(self._processed_ids), f, ensure_ascii=False, indent=2)

    def _iter_news_files(self):
        for json_file in sorted(self.news_cache_dir.glob("*/*.json")):
            if json_file.name == "seen_urls.json":
                continue
            yield json_file

    def process_once(self) -> list[dict]:
        """处理所有未分析的新闻，返回新增分析结果。"""
        results: list[dict] = []

        for json_file in self._iter_news_files():
            with json_file.open(encoding="utf-8") as f:
                news = json.load(f)

            news_id = str(news.get("id", ""))
            if not news_id or news_id in self._processed_ids:
                continue

            sentiment = self.analyzer.analyze(news)
            result = {
                **sentiment.to_dict(),
                "news": {
                    "title": news.get("title"),
                    "time": news.get("time"),
                    "source": news.get("source"),
                    "url": news.get("url"),
                    "summary": news.get("summary", ""),
                },
            }

            from .consensus import calc_multi_source_consensus

            result["consensus"] = calc_multi_source_consensus(result, self.output_dir)

            date_part = (news.get("time") or "")[:10] or datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
            day_dir = self.output_dir / date_part
            day_dir.mkdir(parents=True, exist_ok=True)
            out_path = day_dir / f"{news_id}.json"
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            self._processed_ids.add(news_id)
            results.append(result)
            logger.info(
                "分析完成 [%s] %s → %s (score=%.0f, events=%d, method=%s)",
                news_id,
                sentiment.direction_cn,
                sentiment.coarse_category,
                sentiment.score,
                len(sentiment.events),
                sentiment.method,
            )

        if results:
            self._persist_processed_ids()
        return results


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    processor = NewsProcessor.from_config()
    results = processor.process_once()
    print(f"本次分析 {len(results)} 条新闻")


if __name__ == "__main__":
    main()
