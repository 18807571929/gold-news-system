@echo off
chcp 65001 >nul
title 黄金新闻 - 上班前准备

cd /d E:\gold-news-system

echo ========================================
echo   黄金 MT5 上班前准备（周一）
echo ========================================
echo.
echo [1/4] 环境诊断...
python scripts\diagnose_mt5_env.py
echo.
echo [2/4] MT5 连接测试（需 MT5 已登录 FxPro）...
python src\mt5_bridge\connector.py
echo.
echo [3/4] 单次新闻流水线（抓取-^>分析-^>评分-^>MT5信号）...
python src\main.py --mode once
echo.
echo [4/4] L2 验证（可选，约 1 分钟）...
echo   干跑预览: python scripts\verify_l2_execution.py --dry-run
echo   实盘验证: python scripts\verify_l2_execution.py --setup
echo.
echo ----------------------------------------
echo 若需持续轮询新闻，另开窗口运行:
echo   python src\main.py --mode loop
echo.
echo 工位无 MT5 时只跑 main.py + sync，勿开 auto_execute
echo ========================================
pause
