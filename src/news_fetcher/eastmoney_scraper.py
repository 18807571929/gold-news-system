"""东方财富 7×24 快讯抓取（公开 API，关键词过滤）。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
import yaml

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_KEYWORDS = ["黄金", "美联储", "非农", "CPI", "地缘", "央行", "金价", "美元", "通胀"]
API_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
REQUEST_TIMEOUT = 15


class EastMoneyScraper:
    """东方财富 7×24 快讯 → 与金十共用 news_cache 目录。"""

    def __init__(
        self,
        cache_dir: str | Path = "data/news_cache",
        keywords: list[str] | None = None,
        api_url: str = API_URL,
        fast_column: str = "100",
        page_size: int = 50,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.keywords = keywords or DEFAULT_KEYWORDS.copy()
        self.api_url = api_url
        self.fast_column = fast_column
        self.page_size = page_size
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._seen_path = self.cache_dir / "seen_urls.json"
        self._seen: set[str] = self._load_seen()

        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://finance.eastmoney.com/",
            }
        )

    @classmethod
    def from_config(cls, config_path: str | Path = "config/config.yaml") -> EastMoneyScraper:
        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        em_cfg = config.get("news_sources", {}).get("eastmoney", {})
        jin10_cfg = config.get("news_sources", {}).get("jin10", {})
        return cls(
            cache_dir=jin10_cfg.get("cache_dir", "data/news_cache"),
            keywords=em_cfg.get("keywords", DEFAULT_KEYWORDS),
            api_url=em_cfg.get("api_url", API_URL),
            fast_column=str(em_cfg.get("fast_column", em_cfg.get("column", "100"))),
            page_size=int(em_cfg.get("page_size", 50)),
        )

    def _load_seen(self) -> set[str]:
        if not self._seen_path.exists():
            return set()
        try:
            with self._seen_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            return set()

    def _persist_seen(self) -> None:
        with self._seen_path.open("w", encoding="utf-8") as f:
            json.dump(sorted(self._seen), f, ensure_ascii=False, indent=2)

    def _match_keywords(self, text: str) -> list[str]:
        return [kw for kw in self.keywords if kw in text]

    def fetch_once(self) -> list[dict[str, Any]]:
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": self.fast_column,
            "page": "1",
            "pageSize": str(self.page_size),
            "sortEnd": "",
            "req_trace": str(uuid.uuid4()),
        }
        try:
            resp = self._session.get(self.api_url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            logger.warning("东财快讯抓取失败: %s", exc)
            return []

        inner = payload.get("data") or {}
        items = inner.get("fastNewsList") or inner.get("list") or []
        new_items: list[dict[str, Any]] = []

        for item in items:
            title = (item.get("title") or "").strip()
            digest = (item.get("summary") or item.get("digest") or title).strip()
            text = f"{title} {digest}"
            matched = self._match_keywords(text)
            if not matched:
                continue

            code = str(item.get("code") or item.get("newsId") or title)
            show_time = str(item.get("showTime") or item.get("publishTime") or "")
            dedup_key = f"eastmoney://{code}"

            if dedup_key in self._seen:
                continue

            news_id = f"em{code[-16:]}" if len(code) >= 16 else f"em{code}"

            record = {
                "id": news_id,
                "title": title,
                "time": show_time,
                "source": "东方财富",
                "summary": digest[:500],
                "url": dedup_key,
                "keywords_matched": matched,
                "fetched_at": datetime.now(CHINA_TZ).isoformat(),
            }

            date_part = (show_time or "")[:10] or datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
            day_dir = self.cache_dir / date_part
            day_dir.mkdir(parents=True, exist_ok=True)
            out_path = day_dir / f"{news_id}.json"
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)

            self._seen.add(dedup_key)
            new_items.append(record)
            logger.info("东财新增 [%s] %s", news_id, title[:40])

        if new_items:
            self._persist_seen()
        return new_items


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    scraper = EastMoneyScraper.from_config()
    items = scraper.fetch_once()
    print(f"东财本次新增 {len(items)} 条")


if __name__ == "__main__":
    main()
