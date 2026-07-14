@echo off
chcp 65001 >nul
title 黄金新闻 - 运行时控制台

cd /d E:\gold-news-system
python scripts\show_runtime_console.py %*
