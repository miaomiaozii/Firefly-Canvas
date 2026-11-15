@echo off
setlocal EnableExtensions
rem issue #362: everything here is pinned to CPython 3.12 - the portable bundle ships embeddable
rem 3.12.10 (tools/build_portable.py PY_VER), packages\ wheels are downloaded for cp312 /
rem win_amd64, and rapidocr-onnxruntime 1.4.4 stops at 3.12. Version check lives in
rem tools\python_version_guard.py so the explanation can be Chinese (see the CJK note below).
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0" || goto :startup_failed

set "PYEXE=%~dp0python\python.exe"
if exist "%PYEXE%" (
    rem A freshly unpacked bundled Python may not have the online-service deps yet.
    "%PYEXE%" "%~dp0tools\check_dependencies.py" --quiet >nul 2>&1
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

rem issue #362: block a wrong interpreter here instead of letting pip fail later with
rem "No matching distribution found" on wheels built for cp312.
"%PYEXE%" "%~dp0tools\python_version_guard.py"
if errorlevel 1 goto :python_version

rem issue #363: this used to import only fastapi,requests,uvicorn, so a missing Pillow
rem still crashed the server in the hidden window. Now every dependency is checked one by
rem one (tools\check_dependencies.py prints the names in Chinese), and this last call is NOT
rem sent to >nul so the missing modules stay visible on screen.
rem Keep this file CRLF and ASCII (except the 安装依赖.bat file name): an LF-only batch file gets
rem shredded by cmd (seen while fixing #363 - every command came back as "not recognized"), and
rem Chinese in an echo is unreliable even under chcp 65001 (re-checked while fixing #362: two nearly
rem identical Chinese echo lines, one printed fine, the other was parsed as a command). So every
rem Chinese message here comes from a Python helper, never from an echo in this file.
"%PYEXE%" "%~dp0tools\check_dependencies.py"
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
echo Run 安装依赖.bat, or install Python 3.12 (the version this project standardizes on).
pause
endlocal & exit /b 1

:python_version
rem issue #362: tools\python_version_guard.py already printed the Chinese explanation above.
echo.
echo Wrong Python version - 3.12 is required (see the message above).
pause
endlocal & exit /b 1

:dependencies_missing
echo.
echo Python is available, but the app dependencies above are not complete.
echo Double-click 安装依赖.bat once, then run run.bat again.
pause
endlocal & exit /b 1

:server_launch_failed
echo.
echo Failed to launch the detached Firefly server.
echo The Chinese dependency list (if any) is printed above by tools\check_dependencies.py.
echo Check logs\ (server.log, boot-*.stderr.log) for details, then run 安装依赖.bat if needed.
pause
endlocal & exit /b 1
