@echo off
setlocal EnableExtensions
cd /d "%~dp0" || goto :startup_failed

set "PYEXE=%~dp0python\python.exe"
if exist "%PYEXE%" (
    rem A freshly unpacked bundled Python may not have the online-service deps yet.
    "%PYEXE%" -c "import fastapi,requests,uvicorn" >nul 2>&1
    if errorlevel 1 (
        echo [INFO] Bundled Python is missing app dependencies; trying system Python...
        set "PYEXE=python"
    )
) else (
    set "PYEXE=python"
)

if "%PYEXE%"=="python" (
    python --version >nul 2>&1
    if errorlevel 1 goto :python_missing
)

"%PYEXE%" -c "import fastapi,requests,uvicorn" >nul 2>&1
if errorlevel 1 goto :dependencies_missing

rem The bundled Python uses an isolated python310._pth. The PowerShell helper
rem starts the server in its own hidden process and adds the project directory
rem to sys.path, so closing this launcher does not stop the app.
echo Starting Firefly Canvas...
echo Visit: http://127.0.0.1:3000/
echo This launcher can be closed after startup; the server runs independently.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launch-firefly.ps1" -PythonPath "%PYEXE%"
if errorlevel 1 goto :server_launch_failed
echo Server launch request sent. The browser will open after the local server is ready.
endlocal & exit /b 0

:startup_failed
echo.
echo Unable to enter the Firefly Canvas directory.
pause
endlocal & exit /b 1

:python_missing
echo.
echo Python was not found.
echo Run 安装依赖.bat, or install Python 3.10+ and try again.
pause
endlocal & exit /b 1

:dependencies_missing
echo.
echo Python is available, but the required app dependencies are missing.
echo Double-click 安装依赖.bat once, then run run.bat again.
pause
endlocal & exit /b 1

:server_launch_failed
echo.
echo Failed to launch the detached Firefly server.
echo Check data\server.stderr.log for details, then run 安装依赖.bat if needed.
pause
endlocal & exit /b 1
