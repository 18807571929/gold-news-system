@echo off

chcp 65001 >nul

title 黄金新闻 - MT5 执行器



cd /d E:\gold-news-system

python scripts\run_mt5_executor.py %*

