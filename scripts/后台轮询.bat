@echo off
chcp 65001 >nul
title 黄金新闻 - 后台轮询（loop）

cd /d E:\gold-news-system

echo 【推荐】7×24 新闻决策引擎（不自动下单）:
echo   scripts\新闻引擎_7x24.bat
echo.
echo 本脚本等同: run_news_engine.py --mode loop
echo 按 Ctrl+C 停止
echo.

python scripts\run_news_engine.py --mode loop
