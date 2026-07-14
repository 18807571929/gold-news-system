@echo off
chcp 65001 >nul
title 黄金新闻系统 - MT5连接测试

echo ========================================
echo   MT5 连接测试
echo ========================================
echo.
echo 请先确认：
echo   1. MT5 已打开
echo   2. 已登录 FxPro-MT5 Demo（591838672）
echo   3. 已勾选：工具-选项-EA交易-允许算法交易
echo   4. 没有其他 python 在运行
echo.
pause

cd /d E:\gold-news-system
python src\mt5_bridge\connector.py

echo.
echo ========================================
pause
