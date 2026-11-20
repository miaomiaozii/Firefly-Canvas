@echo off
setlocal EnableExtensions
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\refresh-cache.ps1"
set "REFRESH_EXIT=%ERRORLEVEL%"
if not "%REFRESH_EXIT%"=="0" echo [REFRESH] FAILED. See messages above.
endlocal & exit /b %REFRESH_EXIT%
