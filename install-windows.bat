@echo off
rem Installs both apps (US stocks + China A-shares) to D:\QuantApps and puts two shortcuts
rem on the desktop. Optional first argument: another install folder.
chcp 65001 >nul
setlocal
title 安装量化助手
set "REPO=JUNYI80/cc-workspace"
set "CHECK=import sys; sys.exit(sys.version_info < (3, 10))"

set "TARGET=%~1"
if not defined TARGET if exist D:\ set "TARGET=D:\QuantApps"
if not defined TARGET echo 这台电脑没有 D 盘，改为安装到用户文件夹。
if not defined TARGET set "TARGET=%USERPROFILE%\QuantApps"
echo.
echo 安装位置：%TARGET%
echo.

rem ---- 1. Where the program files come from: next to this file, or downloaded from GitHub
set "SRC=%~dp0"
if exist "%SRC%projects\t212-quant\start-windows.bat" goto have_source
set "SRC="
set "WORK=%TEMP%\quantapps-%RANDOM%%RANDOM%"
mkdir "%WORK%"
for %%B in (main claude/focused-dirac-o1qp6d) do call :download %%B
if not defined SRC goto no_source

:have_source
echo [1/4] 正在复制程序文件……
robocopy "%SRC%projects\t212-quant" "%TARGET%\us-stocks" /E /NFL /NDL /NJH /NJS /NP /XD .venv data state reports __pycache__ .pytest_cache >nul
if errorlevel 8 goto copy_failed
robocopy "%SRC%projects\cn-quant" "%TARGET%\cn-stocks" /E /NFL /NDL /NJH /NJS /NP /XD .venv data state __pycache__ .pytest_cache >nul
if errorlevel 8 goto copy_failed

rem ---- 2. Python
echo [2/4] 正在检查 Python……
set "PY="
call :find_python
if defined PY goto have_python
echo       没有找到 Python，正在自动安装（1-2 分钟）……
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
call :find_python
if not defined PY goto no_python

:have_python
rem ---- 3. Components (each app gets its own private environment inside its folder)
set "FAILED="
echo [3/4] 正在安装组件，需要 3-8 分钟，请不要关闭窗口……
call :setup "%TARGET%\us-stocks" 美股
call :setup "%TARGET%\cn-stocks" A股
if defined FAILED goto setup_failed

rem ---- 4. Shortcuts: in the install folder, and on the desktop when there is one
echo [4/4] 正在创建快捷方式……
rem Chinese names are passed as character codes: the console code page garbles them otherwise.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t='%TARGET%'; $w=New-Object -ComObject WScript.Shell; $dirs=@($t); $d=[Environment]::GetFolderPath('Desktop'); if ($d -and (Test-Path $d)) { $dirs+=$d }; $apps=@(@((-join [char[]](0x91CF,0x5316,0x52A9,0x624B,0xFF08,0x7F8E,0x80A1,0xFF09)),'us-stocks'),@((-join [char[]](0x0041,0x80A1,0x91CF,0x5316,0x52A9,0x624B)),'cn-stocks')); foreach ($dir in $dirs) { foreach ($app in $apps) { $s=$w.CreateShortcut((Join-Path $dir ($app[0]+'.lnk'))); $s.TargetPath=(Join-Path $t ($app[1]+'\start-windows.bat')); $s.WorkingDirectory=(Join-Path $t $app[1]); $s.Save() } }"
if errorlevel 1 echo       快捷方式没有创建成功，可以直接双击 %TARGET%\us-stocks\start-windows.bat

echo.
echo ============================================================
echo  安装完成！
echo  桌面上有两个图标：「量化助手（美股）」和「A股量化助手」。
echo  双击图标，浏览器会自动打开软件。
echo  程序位置：%TARGET%
echo  卸载：删除这个文件夹和桌面上的两个图标即可。
echo ============================================================
if defined WORK rmdir /s /q "%WORK%" 2>nul
if not defined QA_NO_PAUSE pause
exit /b 0

rem ------------------------------------------------------------------ helpers
:download
if defined SRC exit /b 0
echo 正在从 GitHub 下载程序（%1）……
curl -L -s -f -o "%WORK%\src.zip" "https://github.com/%REPO%/archive/refs/heads/%1.zip"
if errorlevel 1 exit /b 0
if exist "%WORK%\x" rmdir /s /q "%WORK%\x"
mkdir "%WORK%\x"
tar -xf "%WORK%\src.zip" -C "%WORK%\x" 2>nul
for /d %%D in ("%WORK%\x\*") do if exist "%%D\projects\t212-quant\start-windows.bat" set "SRC=%%D\"
exit /b 0

:find_python
py -3 -c "%CHECK%" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY exit /b 0
python -c "%CHECK%" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY exit /b 0
for %%V in (314 313 312 311 310) do if not defined PY if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" set PY="%LocalAppData%\Programs\Python\Python%%V\python.exe"
exit /b 0

:setup
echo       %~2 软件……
if not exist "%~1\.venv\Scripts\python.exe" %PY% -m venv "%~1\.venv"
if not exist "%~1\.venv\Scripts\python.exe" set "FAILED=1"
if not exist "%~1\.venv\Scripts\python.exe" exit /b 0
pushd "%~1"
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e ".[app]"
if errorlevel 1 set "FAILED=1"
popd
exit /b 0

:no_source
echo 下载失败：请检查网络后重新运行。
goto fail
:copy_failed
echo 复制文件失败：请确认 %TARGET% 可以写入，并且软件没有正在运行。
goto fail
:no_python
echo 没有找到 Python，自动安装也没有成功。
echo 请到 https://www.python.org/downloads/ 下载安装（安装时勾选 Add python.exe to PATH），再重新运行。
goto fail
:setup_failed
echo 组件安装失败：请检查网络后重新运行本安装文件（已完成的部分不会重复下载）。
goto fail
:fail
echo.
echo 安装没有完成。请把这个窗口截图发给我。
if not defined QA_NO_PAUSE pause
exit /b 1
