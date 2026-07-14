"""黄金新闻情感分析引擎：词典匹配 + DeepSeek 多事件 CoT。"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml

from .prompts import (
    COT_USER_PROMPT,
    MULTI_EVENT_SYSTEM_PROMPT,
    MULTI_EVENT_USER_PROMPT,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))
DIRECTION_MAP = {
    "bullish": "利多",
    "bearish": "利空",
    "neutral": "中性",
}


@dataclass
class EventResult:
    """单条事件级分析结果（EFSA）。"""

    event_text: str
    direction: str
    direction_cn: str
    score: float
    confidence: float
    entities: list[str] = field(default_factory=list)
    coarse_category: str = ""
    fine_category: str = ""
    causal_chain: str = ""
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_text": self.event_text,
            "direction": self.direction,
            "direction_cn": self.direction_cn,
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 3),
            "entities": self.entities,
            "coarse_category": self.coarse_category,
            "fine_category": self.fine_category,
            "causal_chain": self.causal_chain,
            "reasoning": self.reasoning,
        }


@dataclass
class SentimentResult:
    news_id: str
    direction: str
    direction_cn: str
    score: float
    confidence: float
    method: str
    entities: list[str] = field(default_factory=list)
    coarse_category: str = ""
    fine_category: str = ""
    causal_chain: str = ""
    reasoning: str = ""
    matched_terms: list[dict[str, Any]] = field(default_factory=list)
    events: list[EventResult] = field(default_factory=list)
    analyzed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "news_id": self.news_id,
            "analyzed_at": self.analyzed_at,
            "method": self.method,
            "sentiment": {
                "direction": self.direction,
                "direction_cn": self.direction_cn,
                "score": round(self.score, 2),
                "confidence": round(self.confidence, 3),
            },
            "chain_of_thought": {
                "hop1_entities": self.entities,
                "hop2_coarse_category": self.coarse_category,
                "hop3_fine_category": self.fine_category,
                "hop4_sentiment": self.direction_cn,
                "causal_chain": self.causal_chain,
                "reasoning": self.reasoning,
            },
            "matched_terms": self.matched_terms,
        }
        if self.events:
            out["events"] = [e.to_dict() for e in self.events]
        return out


class SentimentAnalyzer:
    """分析单条新闻对黄金的利多/利空方向。"""

    def __init__(
        self,
        dict_path: str | Path = "data/sentiment_dict/gold_sentiment.yaml",
        mode: str = "dict",
        model: str = "deepseek-chat",
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        max_tokens: int = 2000,
        multi_event: bool = True,
    ) -> None:
        self.mode = mode
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.multi_event = multi_event

        self.dict_path = Path(dict_path)
        self._terms: dict[str, dict] = {}
        self._source_credibility: dict[str, float] = {}
        self._load_dictionary()

        self._llm_client = None
        if mode == "api" and api_key:
            self._init_llm_client()

    @classmethod
    def from_config(cls, config_path: str | Path = "config/config.yaml") -> SentimentAnalyzer:
        path = Path(config_path)
        with path.open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        nlp_cfg = config.get("nlp", {})
        api_key = os.environ.get("DEEPSEEK_API_KEY") or nlp_cfg.get("api_key", "")
        return cls(
            dict_path=nlp_cfg.get("dict_path", "data/sentiment_dict/gold_sentiment.yaml"),
            mode=nlp_cfg.get("mode", "dict"),
            model=nlp_cfg.get("model", "deepseek-chat"),
            api_key=api_key,
            base_url=nlp_cfg.get("base_url", "https://api.deepseek.com"),
            max_tokens=nlp_cfg.get("max_tokens", 2000),
            multi_event=bool(nlp_cfg.get("multi_event", True)),
        )

    def _load_dictionary(self) -> None:
        if not self.dict_path.exists():
            logger.warning("情感词典不存在: %s", self.dict_path)
            return
        with self.dict_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self._terms = data.get("terms", {})
        self._source_credibility = data.get("source_credibility", {})

    def _init_llm_client(self) -> None:
        try:
            from openai import OpenAI

            self._llm_client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        except ImportError:
            logger.warning("openai 包未安装，LLM 模式不可用")

    def _analyze_by_dict(self, news: dict[str, Any]) -> SentimentResult:
        text = f"{news.get('title', '')} {news.get('summary', '')}"
        matched: list[dict[str, Any]] = []
        total_score = 0.0

        sorted_terms = sorted(self._terms.keys(), key=len, reverse=True)
        covered: set[str] = set()

        for term in sorted_terms:
            if term in text and term not in covered:
                meta = self._terms[term]
                score = meta.get("score", 0)
                matched.append(
                    {
                        "term": term,
                        "sentiment": meta.get("sentiment", "neutral"),
                        "score": score,
                        "mechanism": meta.get("mechanism", ""),
                    }
                )
                total_score += score
                covered.add(term)

        if total_score > 10:
            direction, direction_cn = "bullish", "利多"
        elif total_score < -10:
            direction, direction_cn = "bearish", "利空"
        else:
            direction, direction_cn = "neutral", "中性"

        confidence = min(0.95, 0.4 + len(matched) * 0.1) if matched else 0.3

        entities = news.get("keywords_matched", [])
        coarse = self._infer_coarse_category(text, matched)
        fine = self._infer_fine_category(text, matched)
        causal = matched[0]["mechanism"] if matched else ""

        event = EventResult(
            event_text=(news.get("title") or "")[:40],
            direction=direction,
            direction_cn=direction_cn,
            score=float(total_score),
            confidence=confidence,
            entities=list(entities) if isinstance(entities, list) else [],
            coarse_category=coarse,
            fine_category=fine,
            causal_chain=causal,
            reasoning=f"词典匹配 {len(matched)} 个术语",
        )

        return SentimentResult(
            news_id=str(news.get("id", "")),
            direction=direction,
            direction_cn=direction_cn,
            score=float(total_score),
            confidence=confidence,
            method="dict",
            entities=event.entities,
            coarse_category=coarse,
            fine_category=fine,
            causal_chain=causal,
            reasoning=event.reasoning,
            matched_terms=matched,
            events=[event],
            analyzed_at=datetime.now(CHINA_TZ).isoformat(),
        )

    def _infer_coarse_category(self, text: str, matched: list[dict]) -> str:
        if any(k in text for k in ("地缘", "冲突", "战争", "军事")):
            return "地缘政治"
        if any(k in text for k in ("非农", "CPI", "通胀", "GDP", "就业", "PCE")):
            return "宏观数据"
        if any(k in text for k in ("美联储", "央行", "利率", "加息", "降息", "黄金", "金价")):
            return "金融事务"
        if matched:
            return "金融事务"
        return "其他"

    def _infer_fine_category(self, text: str, matched: list[dict]) -> str:
        rules = [
            (("非农", "就业数据"), "非农数据"),
            (("CPI", "PCE", "通胀"), "通胀数据"),
            (("加息", "降息", "利率"), "利率决策"),
            (("地缘", "冲突", "战争"), "地缘冲突"),
            (("央行", "美联储"), "央行政策"),
        ]
        for keywords, category in rules:
            if any(k in text for k in keywords):
                return category
        return "其他"

    def _call_llm(self, system: str, user: str) -> str:
        if not self._llm_client:
            return ""
        response = self._llm_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=self.max_tokens,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""

    def _parse_llm_json(self, content: str) -> dict[str, Any] | None:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return None

    def _event_from_dict(self, data: dict[str, Any]) -> EventResult:
        direction = data.get("direction", "neutral")
        return EventResult(
            event_text=str(data.get("event_text", ""))[:80],
            direction=direction,
            direction_cn=data.get("direction_cn", DIRECTION_MAP.get(direction, "中性")),
            score=float(data.get("score", 0)),
            confidence=float(data.get("confidence", 0.5)),
            entities=list(data.get("entities") or []),
            coarse_category=str(data.get("coarse_category", "")),
            fine_category=str(data.get("fine_category", "")),
            causal_chain=str(data.get("causal_chain", "")),
            reasoning=str(data.get("reasoning", "")),
        )

    def _analyze_by_llm_multi_event(self, news: dict[str, Any]) -> SentimentResult | None:
        prompt = MULTI_EVENT_USER_PROMPT.format(
            title=news.get("title", ""),
            source=news.get("source", ""),
            time=news.get("time", ""),
            summary=news.get("summary", ""),
        )
        try:
            content = self._call_llm(MULTI_EVENT_SYSTEM_PROMPT, prompt)
            parsed = self._parse_llm_json(content)
            if not parsed:
                return None

            raw_events = parsed.get("events") or []
            events = [self._event_from_dict(e) for e in raw_events if isinstance(e, dict)]
            if not events:
                return None

            agg = parsed.get("aggregate") or {}
            direction = agg.get("direction") or events[0].direction
            all_entities: list[str] = []
            for e in events:
                all_entities.extend(e.entities)

            return SentimentResult(
                news_id=str(news.get("id", "")),
                direction=direction,
                direction_cn=agg.get("direction_cn", DIRECTION_MAP.get(direction, "中性")),
                score=float(agg.get("score", sum(e.score for e in events) / len(events))),
                confidence=float(agg.get("confidence", sum(e.confidence for e in events) / len(events))),
                method="llm_multi_event",
                entities=list(dict.fromkeys(all_entities)),
                coarse_category=events[0].coarse_category,
                fine_category=events[0].fine_category,
                causal_chain=events[0].causal_chain,
                reasoning=str(agg.get("reasoning", events[0].reasoning)),
                events=events,
                analyzed_at=datetime.now(CHINA_TZ).isoformat(),
            )
        except Exception as exc:
            logger.warning("多事件 LLM 分析失败: %s", exc)
            return None

    def _analyze_by_llm_single(self, news: dict[str, Any]) -> SentimentResult | None:
        prompt = COT_USER_PROMPT.format(
            title=news.get("title", ""),
            source=news.get("source", ""),
            time=news.get("time", ""),
            summary=news.get("summary", ""),
        )
        try:
            content = self._call_llm(SYSTEM_PROMPT, prompt)
            parsed = self._parse_llm_json(content)
            if not parsed:
                return None

            direction = parsed.get("direction", "neutral")
            event = self._event_from_dict({**parsed, "event_text": (news.get("title") or "")[:40]})
            return SentimentResult(
                news_id=str(news.get("id", "")),
                direction=direction,
                direction_cn=parsed.get("direction_cn", DIRECTION_MAP.get(direction, "中性")),
                score=float(parsed.get("score", 0)),
                confidence=float(parsed.get("confidence", 0.5)),
                method="llm",
                entities=event.entities,
                coarse_category=event.coarse_category,
                fine_category=event.fine_category,
                causal_chain=event.causal_chain,
                reasoning=event.reasoning,
                events=[event],
                analyzed_at=datetime.now(CHINA_TZ).isoformat(),
            )
        except Exception as exc:
            logger.warning("LLM 分析失败: %s", exc)
            return None

    def get_source_credibility(self, source: str) -> float:
        for name, score in self._source_credibility.items():
            if name in source:
                return score
        return self._source_credibility.get("默认", 0.7)

    def analyze(self, news: dict[str, Any]) -> SentimentResult:
        """分析单条新闻：API 模式优先 DeepSeek 多事件 CoT，失败回退词典。"""
        if self.mode == "api" and self._llm_client:
            if self.multi_event:
                result = self._analyze_by_llm_multi_event(news)
            else:
                result = self._analyze_by_llm_single(news)
            if result:
                dict_check = self._analyze_by_dict(news)
                result.matched_terms = dict_check.matched_terms
                return result

        return self._analyze_by_dict(news)
