@echo off
chcp 65001 >nul
cd /d E:\gold-news-system
echo === 增量更新 History Data 行情 ===
python scripts\update_price_from_mt5.py --timeframes H1,M15
pause
