# 黄金新闻分析系统 — Hermes 交接文档

> 生成时间：2026-07-07  
> 项目路径：`E:\gold-news-system`  
> 用途：供 Hermes 接入终端后排查 MT5 连接并完成剩余集成

---

## 一、项目背景

珠海科技学院毕设方向：基于大语言模型的**黄金新闻实时利多利空分析与趋势强度判断系统**。

数据流：

```
金十新闻抓取 → NLP情感分析 → 趋势评分(L1-L4) → 网格策略建议 → MT5模拟盘执行
```

技术栈：Python · MetaTrader5 API · requests · BeautifulSoup · YAML 配置 · Cursor 开发

---

## 二、当前完成度（约 75%）

| 模块 | 路径 | 状态 |
|------|------|------|
| 新闻抓取 | `src/news_fetcher/jin10_scraper.py` | ✅ 已完成，60秒轮询 |
| 情感分析 | `src/nlp_engine/` | ✅ 词典模式可用，LLM待配API Key |
| 趋势评分 | `src/trend_scorer/trend_scorer.py` | ✅ 五因子 + L1-L4 |
| 网格适配 | `src/strategy_adapter/grid_adapter.py` | ✅ 建议生成，未自动布单 |
| 风控 | `src/risk_manager/risk_checker.py` | ✅ 基础校验 |
| MT5对接 | `src/mt5_bridge/` | ⚠️ 代码完成，**连接待排查** |
| 主入口 | `src/main.py` | ✅ 一键流水线 |

---

## 三、目录结构

```
E:\gold-news-system\
├── config/
│   ├── config.yaml          # 主配置（含MT5账号，勿提交Git）
│   └── config.example.yaml  # 配置模板
├── data/
│   ├── news_cache/          # 金十新闻JSON
│   ├── sentiment_cache/     # 情感分析+趋势评分
│   ├── signals/             # MT5交易信号
│   └── sentiment_dict/gold_sentiment.yaml
├── src/
│   ├── news_fetcher/jin10_scraper.py
│   ├── nlp_engine/sentiment_analyzer.py
│   ├── nlp_engine/news_processor.py
│   ├── trend_scorer/trend_scorer.py
│   ├── strategy_adapter/grid_adapter.py
│   ├── risk_manager/risk_checker.py
│   ├── mt5_bridge/connector.py
│   ├── mt5_bridge/signal_bridge.py
│   └── main.py
├── requirements.txt
└── .gitignore               # 已忽略 config.yaml
```

---

## 四、MT5 配置信息（FxPro 模拟盘）

配置文件：`config/config.yaml`

```yaml
mt5:
  account: 591838672
  password: "<见 config.yaml，勿写入公开文档>"
  server: "FxPro-MT5 Demo"
  symbol: "XAUUSD"
  path: "C:/Program Files/MetaTrader 5/terminal64.exe"
  enabled: true
  dry_run: false
  auto_execute: true
  signal_dir: "data/signals"
```

**历史问题：**
- 曾用 MetaQuotes-Demo 账号 5052709141，已成功连接
- 切换 FxPro 后，`python src/mt5_bridge/connector.py` 报：
  - `(-6, 'Terminal: Authorization failed')` — initialize 失败
  - 或 `MT5 登录失败: Authorization failed` — login 失败
- 用户 MT5 界面显示在线，但 Python API 连不上
- 可能原因：PowerShell 同时跑多个 Python 进程、未勾选「允许算法交易」、MT5 未用 FxPro 登录、密码/服务器不匹配

---

## 五、关键命令

```powershell
# 进入项目
cd E:\gold-news-system

# 安装依赖
pip install -r requirements.txt

# 测试 MT5 连接（Hermes 优先排查这个）
python src/mt5_bridge/connector.py

# 单次完整流水线
python src/main.py --mode once

# 持续轮询（确认连接正常后再开）
python src/main.py --mode loop
```

**成功连接时 connector.py 输出示例：**
```json
{
  "connected": true,
  "account": { "login": 591838672, "server": "FxPro-MT5 Demo", "balance": ... },
  "tick": { "symbol": "XAUUSD", "bid": 4127, "ask": 4128 },
  "positions": 0,
  "pending_orders": 0
}
```

---

## 六、MT5 连接排查清单（Hermes 请逐项验证）

- [ ] MT5 终端已打开，右下角显示 `591838672  FxPro-MT5 Demo`
- [ ] **工具 → 选项 → EA交易** → 勾选「允许算法交易」「允许 DLL 导入」
- [ ] 同一时间只有一个 Python 进程连 MT5（无 loop 在跑）
- [ ] `pip show MetaTrader5` 已安装
- [ ] `config.yaml` 中 account/server/password 与 FxPro 邮件一致
- [ ] 若 API login 失败，尝试 `password: ""` 复用 MT5 终端已登录会话
- [ ] FxPro 上黄金品种可能是 `XAUUSD` 或 `GOLD`，需在 connector 中确认
- [ ] Python 不要以管理员身份运行（与 MT5 用户权限一致）

---

## 七、connector.py 连接逻辑（供调试参考）

1. 先 `mt5.initialize()` 附着已打开的 MT5
2. 失败则 `mt5.initialize(path=terminal_path)` 指定路径
3. 若 config 有 password，调用 `mt5.login(login, password, server)`
4. login 失败但终端已有账号 → 降级使用终端当前会话
5. password 留空 → 直接使用 MT5 界面已登录的账号

文件：`src/mt5_bridge/connector.py`

---

## 八、Hermes 待完成任务（优先级排序）

### P0 — MT5 连接（阻塞项）
1. 排查并修复 FxPro 模拟盘 Python API 连接
2. 确认 `connector.py` 输出 `connected: true`
3. 确认 XAUUSD 行情可读

### P1 — 验证完整流水线
4. 跑通 `python src/main.py --mode once`，确认 signals 含 account_snapshot
5. 在 MT5 手动挂 1-2 个 XAUUSD 限价单，验证 L2 信号能否删除反向挂单

### P2 — 待开发（老师下一步可能安排）
6. **网格自动布单**（当前只生成建议，不会自动挂单）
7. 与用户现有 MT5 量化系统买卖点整合
8. 整理运行日志/截图到 `docs/操作记录/`（毕设素材）

### P3 — 可选
9. 接入 DeepSeek API 启用 LLM 四跳 CoT（config 中 nlp.mode=api）
10. 多源新闻（东方财富、路透社）

---

## 九、信号 JSON 格式示例

路径：`data/signals/2026-07-07/{news_id}.json`

```json
{
  "impact_analysis": {
    "direction_cn": "利多",
    "trend_level": "L2",
    "composite_score": 55.15
  },
  "trade_advice": {
    "action_cn": "暂停反向挂单，保留顺势挂单",
    "grid_spacing": 1.6,
    "pause_reverse_orders": true
  },
  "account_snapshot": { "login": 591838672, "equity": ... },
  "market_snapshot": { "symbol": "XAUUSD", "bid": 4126, "ask": 4127 },
  "execution": { "executed": true, "actions_taken": [] }
}
```

---

## 十、L1-L4 网格策略规则

| 等级 | 评分区间 | 动作 |
|------|---------|------|
| L1 弱 | [-30, 30] | 维持现有挂单 |
| L2 中 | ±30~60 | 暂停反向挂单，间距×0.8 |
| L3 强 | ±60~85 | 暂停新增，反向仓减50%，间距×0.6 |
| L4 极强 | ±85~100 | 全部暂停，空仓观望 |

实现文件：`src/strategy_adapter/grid_adapter.py`

---

## 十一、老师布置的三项任务进度

| 任务 | 状态 |
|------|------|
| 1. MT5 + FxPro 模拟账号 | ⚠️ 账号已申请，Python连接待通 |
| 2. Cursor + 会员 | ✅ 已完成 |
| 3. 熟悉论文 + 操作记录 | 🔄 进行中，模板在 `E:\HomeWork\朱启亮毕业论文` |

论文模板：**珠海科技学院 2026届 智能系统方向**，正文不少于10000字。

---

## 十二、参考文档

- 论文初稿：`E:\量化项目\17 黄金新闻实时利多利空分析与趋势强度判断系统V1.1.docx`
- 学校模板：`E:\HomeWork\朱启亮毕业论文\2026届（2022级）-论文模板-智能系统方向.doc`
- MT5 安装包：`E:\量化项目\mt5setup.exe`
- MT5 已安装：`C:\Program Files\MetaTrader 5\terminal64.exe`

---

## 十三、注意事项

1. **不要**将 `config/config.yaml`（含密码）提交 Git 或发到公开渠道
2. 同一时间只运行一个 Python 进程连接 MT5
3. 先在模拟盘验证，不涉及真实资金
4. 修改代码后先跑 `connector.py` 再开 `loop`
