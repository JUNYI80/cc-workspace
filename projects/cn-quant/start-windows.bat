@echo off
rem Double-click to start the app. First run sets everything up (3-5 minutes).
chcp 65001 >nul
cd /d "%~dp0"
title A股量化助手

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY set "PY=python"
%PY% -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 (
  echo 没有找到 Python 3.10 或更新的版本。
  echo 请先从 https://www.python.org/downloads/ 下载安装，安装时勾选 "Add python.exe to PATH"，再双击本文件。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 第一次运行：正在准备环境，大约需要 3-5 分钟……
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
echo 正在检查组件……
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e ".[app]"
if errorlevel 1 echo 安装组件时出错，尝试直接启动……

echo.
echo 正在启动，浏览器会自动打开 http://localhost:8502
echo 用完后关闭这个窗口即可退出。
start "" /b cmd /c "ping -n 7 127.0.0.1 >nul & start http://localhost:8502"
".venv\Scripts\python.exe" -m streamlit run src\cnquant\app.py
pause
exit /b 0

:fail
echo 准备环境失败，请截图这个窗口发给我。
pause
exit /b 1
