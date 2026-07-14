@echo off
chcp 65001 >nul
cd /d E:\gold-news-system
echo === L2 删反向挂单验证（需 MT5 已登录 + 市场开盘）===
echo.
echo [1] 干跑预览
python scripts\verify_l2_execution.py --dry-run
echo.
echo [2] 挂测试单并执行（实盘）
set /p CONFIRM=是否继续实盘验证? (y/N): 
if /i not "%CONFIRM%"=="y" goto :end
python scripts\verify_l2_execution.py --setup
echo.
python scripts\generate_ops_log.py
:end
pause
