@echo off
chcp 65001 >nul
title 黄金新闻系统 - 打开MT5

set MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

if exist "%MT5_PATH%" (
    echo 正在启动 MT5...
    start "" "%MT5_PATH%"
    echo MT5 已启动，请在 MT5 里登录 FxPro 模拟盘
) else (
    echo MT5 未找到，尝试用安装包安装...
    set SETUP=E:\量化项目\mt5setup.exe
    if exist "%SETUP%" (
        echo 找到安装包: %SETUP%
        start "" "%SETUP%"
    ) else (
        echo 错误：找不到 MT5，请从 FxPro 官网下载安装
        pause
        exit /b 1
    )
)

timeout /t 3 >nul
