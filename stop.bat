@echo off
rem Double-click to stop IACF Finance.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
if "%~1"=="" (echo. & pause)
