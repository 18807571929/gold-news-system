@echo off
chcp 65001 >nul
title 黄金新闻系统 - 完整流水线

echo ========================================
echo   黄金新闻分析系统（单次运行）
echo ========================================
echo.
echo 抓取新闻 -^> 情感分析 -^> 趋势评分 -^> MT5信号
echo.
echo 请确保 MT5 已登录 FxPro 模拟盘
echo.
pause

cd /d E:\gold-news-system
python src\main.py --mode once

echo.
echo 结果查看：
echo   新闻: data\news_cache\
echo   分析: data\sentiment_cache\
echo   信号: data\signals\
echo.
pause
