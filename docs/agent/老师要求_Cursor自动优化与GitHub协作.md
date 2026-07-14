# 老师要求落地：Cursor Agent 自动优化 + GitHub 协作

## 老师原话对应

| 老师说的 | 本项目落地 |
|----------|-----------|
| Agent 自己优化提示词（存 Git、2 年数据、20 轮回测、自行选方向） | `.cursor/prompts/auto-optimize.md` |
| Cursor Agent 上打开自动开关 | Cursor → **Automations**，启用本仓「夜班自动优化」自动化（见下文） |
| GitHub 开仓共享、老师一起优化 | 私有/公开仓库 + 邀请协作（见仓库 URL） |

## 学生本地怎么用提示词

1. 打开 Cursor Agents
2. 新建任务，粘贴或 `@` 引用：`.cursor/prompts/auto-optimize.md`
3. 直接运行（或等 Automations 定时触发）

也可手工先跑回测入口：

```powershell
python scripts/auto_optimize_20rounds.py --years 2 --rounds 20
```

## 老师如何协作

1. 接受 GitHub 协作邀请（或 clone 公开仓库）
2. 阅读 `README.md` 与本文件
3. 本地：`copy config\config.example.yaml config\config.yaml`，填自己的 Demo 账号（**不要提交密码**）
4. 用同样的 Agent 提示词跑优化，开 PR / 直接 push 到约定分支

## Cursor Automations「自动开关」草稿

| 字段 | 内容 |
|------|------|
| 名称 | 夜班自动优化（2年×20轮） |
| 说明 | 定时用过去 2 年数据做最多 20 轮网格参数回测优化，自动提交无密钥结果 |
| 触发 | 每天 02:00（cron `0 2 * * *`，按你本机/Cloud 显示时区理解） |
| 工具 | 默认 Cloud Agent 能力即可（无需 Slack） |
| 指令 | 严格按仓库内 `.cursor/prompts/auto-optimize.md` 执行：先 Git 保存 → 2 年×20 轮回测 → 写摘要 → 再提交；禁止提交密钥 |
| 仓库/分支 | 本仓 `gold-news-system` · `main` |
| 编辑器内还需完成 | 打开 Automation 后打开 **Enable**；确认 Cloud Agent 额度；确认 cron 时区显示 |

## GitHub 仓库状态

- 本地已 `git init` + 首次提交（不含 `config/config.yaml`）
- 需本机执行一次 `gh auth login` 后才能创建并 push 远程仓库
- 仓库建好后，把老师的 GitHub 用户名发给我，我帮你加 Collaborator

## 安全

- `config/config.yaml` 含 MT5 密码与 API Key，**永不上传**
- 上传的是 `config.example.yaml`（占位符）
