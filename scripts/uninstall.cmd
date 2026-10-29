@echo off
chcp 65001 >nul
setlocal
if not exist "%~dp0WOFO Login.exe" (
  echo 未找到 WOFO Login.exe，请使用程序设置中的卸载入口或 Windows“已安装的应用”。
  pause
  exit /b 1
)
start "" "%~dp0WOFO Login.exe" --uninstall
