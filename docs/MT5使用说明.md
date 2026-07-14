# MT5 使用说明（傻瓜版）

## 你需要两个东西

| 名称 | 作用 | 状态 |
|------|------|------|
| **MT5 终端** | 交易软件，连 FxPro | 已安装：`C:\Program Files\MetaTrader 5\` |
| **MetaTrader5 Python包** | 让 Python 连 MT5 | 已安装：`pip install MetaTrader5` |

安装包备份位置：`E:\量化项目\mt5setup.exe`（重装 MT5 时用）

---

## 一键脚本（双击即可）

在 `E:\gold-news-system\scripts\` 文件夹：

| 脚本 | 干什么 |
|------|--------|
| `打开MT5.bat` | 启动 MT5 软件 |
| `MT5连接测试.bat` | 测试 Python 能否连上 MT5 |
| `运行一次.bat` | 跑一遍完整新闻分析流水线 |

**推荐顺序：** 打开MT5 → 登录 FxPro → MT5连接测试 → 运行一次

---

## FxPro 登录信息

- 登录号：`591852231`（500 美金模拟盘）
- 服务器：`FxPro-MT5 Demo`
- 密码：见 `config/config.yaml`（FxPro 邮件里的）

MT5 里：**文件 → 登录到交易账户** 填写以上三项。

---

## 成功标志

`MT5连接测试.bat` 运行后看到：

```json
"connected": true
```

且 account 里是 `591852231`，balance 约 500。

---

## 常见问题

**Q：连接失败 -6？**  
A：① MT5 没开 ② 没登录 FxPro ③ 没勾选「允许算法交易」④ 另一个 Python 在跑

**Q：要下载什么？**  
A：不用重复下载。MT5 和 Python 包都已装好，直接用 scripts 里的 bat 文件。

**Q：PowerShell 和 bat 区别？**  
A：效果一样。bat 是双击运行的，不用记命令。

---

## 故障排查（重要）

### 症状：日志里连的是 MetaQuotes-Demo，不是 FxPro

**根因**：MT5 数据目录 `config/common.ini` 缓存了错误服务器。

当前机器上曾发现：

```ini
Login=591838672
Server=MetaQuotes-Demo   ← 错误！FxPro 账号不能连 MetaQuotes 服务器
```

日志会显示：`'591838672': authorization on MetaQuotes-Demo failed (Invalid account)`

**修复步骤**（在 MT5 界面操作，不要手改 servers.dat）：

1. 打开 MT5 → **文件 → 开立账户**
2. 搜索 **FxPro**（或点「查找您的经纪商」）
3. 选择 **FxPro-MT5 Demo** 服务器
4. 选「使用现有交易账户登录」，填入账号/密码
5. 右下角应显示 `591838672  FxPro-MT5 Demo`

若搜索不到 FxPro：从 [FxPro 官网](https://www.fxpro.com) 下载 **FxPro 版 MT5**（比 MetaQuotes 标准版更省事）。

### 症状：算法交易开关刚打开就自动关闭

**根因**（按优先级）：

1. **账号未成功登录** — 未连上经纪商时，工具栏开关会在约 1 秒内被 MT5 自动关掉
2. **`common.ini` 中 `[Experts] Enabled=0`** — 持久关闭算法交易
3. **`common.ini` 中 `[Experts] Api=0`** — 禁止 Python API 触发算法交易（运行 `connector.py` 时可能反复开关）
4. 多个 Python 进程同时 `mt5.initialize()`

**修复**：

1. 先完成 FxPro 登录（见上）
2. **工具 → 选项 → EA交易** → 勾选：
   - 允许算法交易
   - 允许 DLL 导入
3. 关闭所有 Python 进程后再测
4. 运行诊断：`python scripts/diagnose_mt5_env.py`

### 诊断命令

```powershell
cd E:\gold-news-system
python scripts/diagnose_mt5_env.py
python src/mt5_bridge/connector.py
```

成功时 connector 输出 `"connected": true`，且 server 为 `FxPro-MT5 Demo`。
