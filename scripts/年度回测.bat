@echo off
chcp 65001 >nul
cd /d E:\gold-news-system
echo === 1. 构建 3 年历史宏观信号 ===
python scripts\build_historical_signals.py --years 3
echo.
echo === 2. 正式回测 1 年（初始 500 刀）===
python scripts\run_news_grid_backtest.py --years 1 --initial 500
echo.
echo === 3. 正式回测 3 年（初始 500 刀）===
python scripts\run_news_grid_backtest.py --years 3 --initial 500
pause
