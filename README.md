# 黄金新闻实时利多利空分析与趋势强度判断系统

基于 LLM 的黄金新闻情感分析 → 趋势强度 L1–L4 → 网格策略适配 → MT5 模拟盘执行。

## 环境要求

- Python 3.10+
- MetaTrader 5（FxPro 模拟盘已登录）
- `pip install -r requirements.txt`

## 快速开始

```powershell
# 1. 复制配置并填写 MT5 账号（FxPro Demo）
copy config\config.example.yaml config\config.yaml

# 2. 打开 MT5，登录 591838672 @ FxPro-MT5 Demo，开启算法交易

# 3. 连接测试
python scripts/diagnose_mt5_env.py
python src/mt5_bridge/connector.py

# 4. 跑一遍完整流水线
python src/main.py --mode once

# 5. 同步 JSON 缓存到量化项目 History Data（Parquet）
python scripts/sync_to_parquet.py
```

## 一键脚本（scripts/）

| 脚本 | 作用 |
|------|------|
| `打开MT5.bat` | 启动 MT5 |
| `MT5连接测试.bat` | Python 连接测试 |
| `运行一次.bat` | 完整流水线 |
| `同步数据.bat` | JSON → Parquet 归档 |

## 项目结构

```
src/
  news_fetcher/      # 金十快讯抓取
  nlp_engine/        # 情感分析（词典 + LLM CoT）
  trend_scorer/      # 五因子 + L1-L4
  strategy_adapter/  # 网格策略建议
  risk_manager/      # 风控校验
  mt5_bridge/        # MT5 连接与信号执行
  paths.py           # 量化项目路径解析
data/                # 运行时 JSON 缓存（gitignore）
config/              # 配置（config.yaml 含密码，勿提交）
docs/                # 使用说明与交接文档
scripts/             # 诊断、同步、bat 脚本
```

## 路径配置

`config.yaml` 中 `paths` 指向量化项目目录，也可用环境变量覆盖：

```powershell
set GOLD_DATA_ROOT=E:\量化项目\02 History Data
set GOLD_BACKTEST_ROOT=E:\量化项目\05 Backtest
```

Parquet 归档位置：`{history_data}/news/`、`sentiment/`、`signals/`

## MT5 说明

- FxPro 黄金品种为 **GOLD**（不是 XAUUSD）
- 详见 `docs/MT5使用说明.md`

## 开发阶段

- Phase 0：环境 + 数据管道 ✅
- Phase 1：NLP 分析引擎 ✅（DeepSeek 多事件 CoT + 东财 + 词典扩充）
- Phase 2：趋势评分 + 策略对齐
- Phase 3：MT5 自动执行
- Phase 4：回测验证

### Phase 1 快速测试

```powershell
python scripts/test_nlp_api.py     # DeepSeek 多事件分析测试
python src/main.py --mode once     # 金十+东财抓取 → NLP → 评分 → MT5
```

重新分析旧新闻：删除 `data/sentiment_cache/processed_ids.json` 后重跑。

完整规划见：`E:\量化项目\01 thesis\黄金新闻分析系统_框架规划.md`

## Cursor Agent 自动优化（老师要求）

无人值守回测优化提示词：`.cursor/prompts/auto-optimize.md`

```powershell
# 本地直接跑：过去 2 年 × 20 轮参数扫描
python scripts/auto_optimize_20rounds.py --years 2 --rounds 20
```

协作说明与 Automations 配置：`docs/agent/老师要求_Cursor自动优化与GitHub协作.md`

**安全提醒：** 切勿提交 `config/config.yaml`（含 MT5 密码与 API Key）。只使用/分享 `config/config.example.yaml`。
