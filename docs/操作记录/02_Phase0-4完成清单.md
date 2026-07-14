# Phase 0–4 完成清单

**验收日期：** 2026-07-11

## Phase 0 — 数据管道 ✅

- [x] `src/paths.py` — GOLD_DATA_ROOT 环境变量
- [x] `scripts/sync_to_parquet.py` — JSON → Parquet
- [x] `scripts/diagnose_mt5_env.py` — MT5 环境诊断

## Phase 1 — NLP 引擎 ✅

- [x] DeepSeek API 四跳 CoT + 多事件
- [x] 东方财富快讯抓取
- [x] 词典扩展 + 多源共识
- [x] `scripts/test_nlp_api.py` 验证通过

## Phase 2 — 趋势 + 网格策略 ✅

- [x] 五因子权重 0.35/0.15/0.20/0.15/0.15
- [x] `duration_estimator.py` 持续性评估
- [x] ATR 动态间距（MT5 / CSV 兜底）
- [x] `scripts/rescore_trends.py`

## Phase 3 — MT5 集成 ✅

- [x] `grid_executor.py` — 删反向单 / 减仓 / L2 布网格
- [x] `risk_state.py` — 常规/预警/紧急
- [x] `connector.py` — place_pending / close_position
- [x] `scripts/test_grid_executor.py`
- [x] `scripts/verify_l2_execution.py` — 实盘验证脚本

## Phase 4 — 回测验证 ✅

- [x] `src/backtest_adapter/` — A/B 模拟器
- [x] `scripts/run_news_grid_backtest.py`
- [x] 输出 → `05 Backtest/results/news-grid/`
- [x] `news_shock_adapter/` gs_v17 集成桩

## 待市场开盘后完成

- [ ] L2 实盘验证通过（周末 retcode 10018 Market closed，周一执行 `scripts/L2验证.bat`）
- [x] 行情 CSV 覆盖 2026-07（H1 更新至 2026-07-10，+455 根）
- [x] 正式回测（无 `--align`，PnL +180 vs baseline）
