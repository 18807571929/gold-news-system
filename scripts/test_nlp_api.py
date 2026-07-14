"""NLP 分析测试：单条新闻 DeepSeek 多事件 CoT。"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nlp_engine import SentimentAnalyzer

SAMPLE = {
    "id": "test_001",
    "title": "【美联储官员鸽派发言，非农不及预期，金价短线拉升】",
    "time": "2026-07-11 18:00:00",
    "source": "金十数据",
    "summary": "美联储官员暗示若就业放缓可能考虑降息；美国非农就业新增低于预期，美元指数回落，现货黄金升至4120美元上方。",
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    analyzer = SentimentAnalyzer.from_config()
    if analyzer.mode != "api" or not analyzer._llm_client:
        print("错误：nlp.mode 需为 api 且 api_key 已配置")
        raise SystemExit(1)

    result = analyzer.analyze(SAMPLE)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
