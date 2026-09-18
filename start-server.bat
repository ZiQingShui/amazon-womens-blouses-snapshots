@echo off
chcp 936 >nul
setlocal

REM 切换到脚本所在目录。必须用 pushd：cd /d 不支持 UNC 网络路径
REM （直接从 \\192.168.16.75\... 双击时，cd /d 会失败并落到 C:\WINDOWS）
pushd "%~dp0"

if not exist "docs\index.html" (
  echo.
  echo   [错误] 当前目录下没有找到 docs\index.html
  echo   请确认本脚本与 docs 文件夹在同一层目录里。
  echo.
  popd
  pause
  exit /b 1
)

REM 探测可用的 Python
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "C:\Program Files\python\python.exe" set "PY=C:\Program Files\python\python.exe"
if not defined PY (where py >nul 2>nul && set "PY=py -3")

if not defined PY (
  echo.
  echo   [错误] 这台电脑没有找到 Python，无法启动看板服务。
  echo.
  echo   解决办法：安装 Python 3（官网 python.org/downloads），
  echo   安装时勾选 "Add Python to PATH"，装完后重新双击本脚本。
  echo.
  popd
  pause
  exit /b 1
)

set "PORT=8000"

echo.
echo   正在启动 Amazon 女装衬衫快照看板...
echo.
echo   本机访问：http://localhost:%PORT%/
echo   同事访问：把 localhost 换成本机内网 IP（在命令行敲 ipconfig 可查）
echo.
echo   保持本窗口开着，服务就在运行。按 Ctrl+C 可停止。
echo.

%PY% -m http.server %PORT% --bind 0.0.0.0 --directory docs

popd
pause
