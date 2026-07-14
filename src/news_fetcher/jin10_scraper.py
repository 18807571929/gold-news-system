"""金十数据快讯抓取模块。"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
import yaml
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS = ["黄金", "美联储", "非农", "CPI", "地缘政治", "央行"]
FLASH_API = "https://flash-api.jin10.com/get_flash_list"
SUMMARY_MAX_LEN = 200
REQUEST_TIMEOUT = 15
CHINA_TZ = timezone(timedelta(hours=8))


class Jin10Scraper:
    """从金十快讯 API 抓取黄金相关新闻。"""

    def __init__(
        self,
        cache_dir: str | Path = "data/news_cache",
        keywords: list[str] | None = None,
        poll_interval: int = 60,
        api_url: str = FLASH_API,
        channel: str = "-8200",
        x_app_id: str = "SO1EJGmNgCtmpcPF",
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.keywords = keywords or DEFAULT_KEYWORDS.copy()
        self.poll_interval = poll_interval
        self.api_url = api_url
        self.channel = channel
        self.x_app_id = x_app_id

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._seen_urls_path = self.cache_dir / "seen_urls.json"
        self._seen_urls: set[str] = self._load_seen_urls()

        self._session = requests.Session()
        self._session.headers.update(
            {
                "x-app-id": self.x_app_id,
                "x-version": "1.0.0",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
        )

    @classmethod
    def from_config(cls, config_path: str | Path = "config/config.yaml") -> Jin10Scraper:
        """从 YAML 配置文件构建抓取器。"""
        path = Path(config_path)
        with path.open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        jin10_cfg = config.get("news_sources", {}).get("jin10", {})
        return cls(
            cache_dir=jin10_cfg.get("cache_dir", "data/news_cache"),
            keywords=jin10_cfg.get("keywords", DEFAULT_KEYWORDS),
            poll_interval=jin10_cfg.get("poll_interval", 60),
            api_url=jin10_cfg.get("api_url", FLASH_API),
            channel=jin10_cfg.get("channel", "-8200"),
            x_app_id=jin10_cfg.get("x_app_id", "SO1EJGmNgCtmpcPF"),
        )

    def _load_seen_urls(self) -> set[str]:
        if not self._seen_urls_path.exists():
            return set()
        try:
            with self._seen_urls_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("加载 seen_urls 失败，将重建索引: %s", exc)
            return set()

    def _persist_seen_urls(self) -> None:
        with self._seen_urls_path.open("w", encoding="utf-8") as f:
            json.dump(sorted(self._seen_urls), f, ensure_ascii=False, indent=2)

    def _is_duplicate(self, url: str) -> bool:
        return url in self._seen_urls

    def _mark_seen(self, url: str) -> None:
        self._seen_urls.add(url)

    def _fetch_raw_flash(self) -> list[dict[str, Any]]:
        params = {"channel": self.channel, "vip": "1"}
        response = self._session.get(
            self.api_url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data") or []

        filtered: list[dict[str, Any]] = []
        for item in items:
            extras = item.get("extras") or {}
            if extras.get("ad"):
                continue
            data = item.get("data") or {}
            if not (data.get("content") or "").strip():
                continue
            filtered.append(item)
        return filtered

    def _strip_html(self, html: str) -> str:
        text = BeautifulSoup(html, "lxml").get_text(separator="", strip=True)
        return re.sub(r"\s+", " ", text).strip()

    def _extract_title(self, title: str, plain_text: str) -> str:
        if title.strip():
            return title.strip()

        bracket_match = re.search(r"【([^】]+)】", plain_text)
        if bracket_match:
            return f"【{bracket_match.group(1)}】"

        first_sentence = re.split(r"[。！？\n]", plain_text, maxsplit=1)[0].strip()
        if first_sentence:
            return first_sentence[:80]
        return plain_text[:80]

    def _match_keywords(self, text: str) -> list[str]:
        lowered = text.lower()
        matched: list[str] = []
        for keyword in self.keywords:
            if keyword.lower() in lowered:
                matched.append(keyword)
        return matched

    def _get_unique_url(self, item_id: str, source_link: str) -> str:
        if source_link.strip():
            return source_link.strip()
        return f"jin10://flash/{item_id}"

    def _parse_item(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        data = raw.get("data") or {}
        content_html = data.get("content") or ""
        plain_text = self._strip_html(content_html)
        title = self._extract_title(data.get("title") or "", plain_text)

        search_text = f"{title} {plain_text}"
        keywords_matched = self._match_keywords(search_text)
        if not keywords_matched:
            return None

        item_id = str(raw.get("id", ""))
        source_link = data.get("source_link") or ""
        summary = plain_text[:SUMMARY_MAX_LEN]
        if len(plain_text) > SUMMARY_MAX_LEN:
            summary += "..."

        return {
            "id": item_id,
            "title": title,
            "time": raw.get("time", ""),
            "source": (data.get("source") or "金十数据").strip() or "金十数据",
            "summary": summary,
            "url": self._get_unique_url(item_id, source_link),
            "keywords_matched": keywords_matched,
            "fetched_at": datetime.now(CHINA_TZ).isoformat(),
        }

    def _save_to_cache(self, news: dict[str, Any]) -> Path:
        news_time = news.get("time") or news["fetched_at"]
        date_part = news_time[:10] if len(news_time) >= 10 else datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
        day_dir = self.cache_dir / date_part
        day_dir.mkdir(parents=True, exist_ok=True)

        file_path = day_dir / f"{news['id']}.json"
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(news, f, ensure_ascii=False, indent=2)
        return file_path

    def fetch_once(self) -> list[dict[str, Any]]:
        """拉取最新快讯，过滤、去重并缓存，返回新增新闻列表。"""
        try:
            raw_items = self._fetch_raw_flash()
        except requests.RequestException as exc:
            logger.warning("金十快讯 API 请求失败: %s", exc)
            return []

        new_items: list[dict[str, Any]] = []
        for raw in raw_items:
            parsed = self._parse_item(raw)
            if parsed is None:
                continue
            if self._is_duplicate(parsed["url"]):
                continue

            self._save_to_cache(parsed)
            self._mark_seen(parsed["url"])
            new_items.append(parsed)

        if new_items:
            self._persist_seen_urls()
            logger.info("新增 %d 条金十新闻", len(new_items))
        else:
            logger.debug("本轮无新增金十新闻")

        return new_items

    def run_forever(self) -> None:
        """每 poll_interval 秒轮询一次，持续抓取。"""
        logger.info(
            "金十抓取器启动，轮询间隔 %ds，关键词: %s",
            self.poll_interval,
            ", ".join(self.keywords),
        )
        while True:
            try:
                self.fetch_once()
            except Exception:
                logger.exception("抓取循环发生未预期错误")
            time.sleep(self.poll_interval)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> None:
    _configure_logging()
    scraper = Jin10Scraper.from_config()
    scraper.run_forever()


if __name__ == "__main__":
    main()
