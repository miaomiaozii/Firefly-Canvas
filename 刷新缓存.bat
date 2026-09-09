@echo off
setlocal EnableExtensions
cd /d "%~dp0" || goto :failed

set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo === Firefly Canvas cache refresh ===
echo Will run: stop server, clean __pycache__, bump asset versions, restart, open browser.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\refresh-cache.ps1" -PythonPath "%PYEXE%"
if errorlevel 1 goto :failed

echo.
echo Done. The browser opens once the server is ready (frontend + backend caches invalidated).
ping -n 4 127.0.0.1 >nul
endlocal & exit /b 0

:failed
echo.
echo Refresh FAILED. Check the messages above. Server log: data\server.stderr.log
pause
endlocal & exit /b 1
