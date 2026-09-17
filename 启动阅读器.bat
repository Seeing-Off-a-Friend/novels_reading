@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小说阅读器
setlocal enabledelayedexpansion

echo ================================
echo   小说阅读器 正在启动...
echo ================================
echo.

rem ============================================================
rem 找一个真正能用的 Python
rem
rem 两个讲究：
rem   1) 不写死路径。原来是 "C:\Program Files\Python311\python.exe"，
rem      换机器、换安装位置、换版本都得改这个文件。
rem   2) 不能只看命令存不存在，也不能只跑 --version。有些机器上
rem      py 装坏了（缺标准库），--version 照样返回 0，真调用时才报
rem      "No module named 'encodings'"。所以这里实际 import 一个
rem      标准库模块来验证。
rem ============================================================
set "PY="
for %%C in (python py) do (
    if not defined PY (
        %%C -c "import encodings" >nul 2>nul
        if !errorlevel! EQU 0 set "PY=%%C"
    )
)

if not defined PY (
    echo.
    echo [错误] 没有找到可用的 Python。
    echo.
    echo   请安装 Python 3.8 或更高版本:
    echo     https://www.python.org/downloads/
    echo.
    echo   安装时务必勾选 "Add Python to PATH"，装完重新双击本文件。
    echo.
    pause
    exit /b 1
)

echo 使用解释器: %PY%
echo 项目目录:   %~dp0
echo.
echo 浏览器会自动打开；关闭本窗口或按 Ctrl+C 停止服务。
echo.

%PY% server.py

echo.
echo 服务已停止。
pause
