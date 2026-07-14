"""7×24 新闻决策引擎：抓取 → NLP → 评分 → 写 JSON/JSONL（默认不执行 MT5）。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_once(*, execute_mt5: bool = False) -> dict:
    from src.news_fetcher import EastMoneyScraper, Jin10Scraper
    from src.nlp_engine import NewsProcessor, SentimentAnalyzer
    from src.trend_scorer import TrendScorer
    from src.mt5_bridge import SignalBridge
    from src.runtime.decision_log import DecisionLogger

    config = load_config()
    stats = {"news_fetched": 0, "analyzed": 0, "scored": 0, "signals": 0}

    jin10_cfg = config.get("news_sources", {}).get("jin10", {})
    em_cfg = config.get("news_sources", {}).get("eastmoney", {})

    if jin10_cfg.get("enabled", True):
        stats["news_fetched"] += len(Jin10Scraper.from_config().fetch_once())
    if em_cfg.get("enabled", False):
        stats["news_fetched"] += len(EastMoneyScraper.from_config().fetch_once())

    stats["analyzed"] = len(NewsProcessor.from_config().process_once())

    analyzer = SentimentAnalyzer.from_config()
    scorer = TrendScorer.from_config()
    stats["scored"] = len(
        scorer.process_sentiment_cache(credibility_fn=analyzer.get_source_credibility)
    )

    bridge = SignalBridge.from_config()
    signals = bridge.process_once(execute=execute_mt5)
    stats["signals"] = len(signals)

    mt5_cfg = config.get("mt5", {})
    if not execute_mt5 and mt5_cfg.get("auto_execute"):
        executed = bridge.execute_pending_signals()
        stats["mt5_executed"] = len(executed)
        if executed:
            logger.info("auto_execute 补执行 %d 条信号", len(executed))

    runtime_dir = Path(config.get("runtime", {}).get("dir", "data/runtime"))
    logger_obj = DecisionLogger(runtime_dir)
    if bridge.enabled:
        try:
            if bridge.connector.connect():
                tick = bridge.connector.get_tick()
                acct = bridge.connector.get_account_info()
                bridge.connector.disconnect()
                if tick and acct:
                    logger_obj.write_market_state(
                        {
                            "symbol": tick.symbol,
                            "bid": tick.bid,
                            "ask": tick.ask,
                            "equity": acct.equity,
                            "balance": acct.balance,
                        }
                    )
        except Exception:
            logger.exception("写入 market_state 失败")

    logger.info("news_engine 本轮: %s", stats)
    return stats


def run_loop(*, execute_mt5: bool = False) -> None:
    config = load_config()
    interval = int(
        config.get("runtime", {}).get("poll_interval")
        or config.get("news_sources", {}).get("jin10", {}).get("poll_interval", 60)
    )
    logger.info("news_engine loop 启动 interval=%ds execute_mt5=%s", interval, execute_mt5)
    while True:
        try:
            run_once(execute_mt5=execute_mt5)
        except Exception:
            logger.exception("news_engine loop 异常")
        time.sleep(interval)
