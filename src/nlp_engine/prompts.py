"""LLM 四跳思维链（CoT）提示词模板。"""

SYSTEM_PROMPT = """你是一位专业的黄金量化交易分析师，擅长从财经新闻中判断对黄金价格的影响方向。
请严格按照四跳思维链分析，并以 JSON 格式输出结果。"""

COT_USER_PROMPT = """请分析以下黄金相关新闻，判断其对黄金价格的利多/利空影响。

【新闻标题】{title}
【新闻来源】{source}
【发布时间】{time}
【新闻正文】{summary}

请按四跳思维链逐步推理：
1. 实体识别：提取关键实体（如美联储、非农、地缘冲突等）
2. 粗粒度分类：金融事务 / 商业运营 / 合规信用 / 地缘政治 / 宏观数据
3. 细粒度分类：利率决策 / 非农数据 / 通胀数据 / 地缘冲突 / 央行政策 / 其他
4. 情感极性：利多 / 利空 / 中性

输出严格 JSON，不要包含 markdown 代码块：
{{
  "entities": ["实体1", "实体2"],
  "coarse_category": "分类",
  "fine_category": "子类型",
  "direction": "bullish|bearish|neutral",
  "direction_cn": "利多|利空|中性",
  "score": <-100到100的整数>,
  "confidence": <0到1的小数>,
  "causal_chain": "事件→传导→对金价影响",
  "reasoning": "简要推理说明"
}}"""

MULTI_EVENT_SYSTEM_PROMPT = """你是黄金量化交易分析师。一篇新闻可能包含多个独立事件，每个事件对金价的影响可能不同。
请识别所有与黄金/美元/利率/通胀/地缘/央行相关的事件，分别做四跳 CoT 分析，再给出综合判断。"""

MULTI_EVENT_USER_PROMPT = """分析以下新闻对黄金价格的影响。若含多个独立事件，请分别分析。

【标题】{title}
【来源】{source}
【时间】{time}
【正文】{summary}

输出严格 JSON（无 markdown），结构如下：
{{
  "events": [
    {{
      "event_text": "事件简述（20字内）",
      "entities": ["实体1"],
      "coarse_category": "宏观数据|地缘政治|金融事务|央行政策|市场分析",
      "fine_category": "利率决策|非农数据|通胀数据|地缘冲突|央行政策|其他",
      "direction": "bullish|bearish|neutral",
      "direction_cn": "利多|利空|中性",
      "score": <-100到100整数>,
      "confidence": <0到1>,
      "causal_chain": "事件→传导→金价",
      "reasoning": "简要说明"
    }}
  ],
  "aggregate": {{
    "direction": "bullish|bearish|neutral",
    "direction_cn": "利多|利空|中性",
    "score": <-100到100整数>,
    "confidence": <0到1>,
    "reasoning": "多事件综合结论"
  }}
}}

要求：至少 1 个事件；若无明确黄金影响，score 接近 0，direction 为 neutral。"""
