@echo off
chcp 65001 >nul
title 黄金新闻系统 - 同步数据到 History Data

cd /d E:\gold-news-system
echo 正在将 news/sentiment/signals JSON 同步到 Parquet...
python scripts\sync_to_parquet.py
echo.
pause
