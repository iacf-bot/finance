@echo off
rem Double-click to start IACF Finance in the background. "start.bat -Foreground" runs it in this window.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if "%~1"=="" (echo. & pause)
