@echo off
chcp 65001 >nul
setlocal
if not exist "%~dp0WOFO Login.exe" (
  echo 未找到 WOFO Login.exe，请将本脚本放在程序旁边。
  pause
  exit /b 1
)
start "" "%~dp0WOFO Login.exe" --install
