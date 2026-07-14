@echo off
chcp 65001 >nul
title 黄金新闻 - 7x24 决策引擎

cd /d E:\gold-news-system

echo 新闻引擎：抓取 -^> LLM -^> 判断 -^> JSONL（默认不执行 MT5）
echo 按 Ctrl+C 停止
echo.

python scripts\run_news_engine.py --mode loop
