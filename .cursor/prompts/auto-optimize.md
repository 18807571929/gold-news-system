# Cursor Agent 自动优化任务（无需人工干预）

你是本仓 `gold-news-system` 的量化优化 Agent。目标：在无人值守模式下用历史数据做多轮回测，选择更优网格/风控参数，并把结果提交到 Git。

## 硬性步骤（必须按序执行）

1. **当前状态保存到 Git**
   - `git status` / `git diff` / `git log -5`
   - 若有未提交、且不含密钥的改动：暂存后提交，说明写清「为何改」
   - **禁止**提交：`config/config.yaml`、`.env`、`data/`、密码、API Key
   - 若无 GitHub remote：先跳过 push，把结果写进 `docs/操作记录/`

2. **使用过去 2 年数据，跑满 20 轮回测优化**
   - 主入口：`python scripts/auto_optimize_20rounds.py --years 2 --rounds 20`
   - 也可在同一次任务里手工拆轮：`run_news_grid_backtest.py` / `scan_grid_params.py`
   - 自行选择本轮优化方向（间距、层数、L2/L3 冲击、margin overlay、TP/减仓相关配置等），**不要等用户确认**
   - 每轮记录简短记录写入 `docs/操作记录/logs/auto_optimize_<timestamp>.jsonl`
   - 用一致目标排序：优先降低 stop_out，其次提高 news_adapter 终值/盈亏，再看回撤

3. **自动落地最优候选**
   - 只改 `config/config.yaml` 中**无密钥**的回测/网格/风控数值字段（本地），并同步更新 `config/config.example.yaml` 中相同字段
   - 生成可读摘要：`docs/操作记录/YYYYMMDD_自动优化摘要.md`
   - 再次 git commit（不含密钥文件）

4. **收口**
   - 若 remote 可用：`git push`
   - 在任务结束消息里给出：最优参数、相对基线的改善、失败轮次、建议下一波搜索方向

## 约束

- Demo 账户：不要在云端明文写入密码；本地 `config.yaml` 已 gitignore
- 不要 force push / 不要改 git config
- 单轮回测超时可降采样（仍声称 2 年窗口时先用 H1），但必须在报告中说明
- 不允许伪造回测数字：数字必须来自脚本输出文件

## 成功标准

- ≥15/20 轮成功产出 comparison/scan 结果
- 摘要 MD 与 JSONL 已入库
- 最优参数相对本轮基线有可核对的指标对比表
