@echo off
rem issue #362: this project standardizes on CPython 3.12. Everything user-facing in Chinese is
rem printed by the Python helpers under tools\ (version guard, offline set check, dependency audit)
rem - cmd mis-parses long Chinese strings inside a UTF-8 batch file (re-checked
rem while fixing #362: two nearly identical Chinese echo lines, one printed, one ran as a command), so
rem this file stays ASCII and CRLF. Same reason no line here ends with a comma: inside `if (...)`
rem a trailing comma continues the command onto the next line and cmd then executes the remainder.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

echo ============================================
echo   Install Dependencies - Firefly Canvas
echo ============================================
echo.

set "PYEXE=%~dp0python\python.exe"

if exist "%PYEXE%" (
    echo [OK] Using the bundled python\ directory
) else (
    echo [INFO] Bundled Python not found, trying system Python...
    python --version >nul 2>&1
    if errorlevel 1 goto :python_missing
    set "PYEXE=python"
    echo [OK] Using system Python
)

echo.

rem issue #362: refuse anything but 3.12 before touching pip - packages\ holds cp312 / win_amd64
rem wheels and rapidocr-onnxruntime 1.4.4 stops at 3.12. The Chinese explanation comes from the helper.
"%PYEXE%" "%~dp0tools\python_version_guard.py"
if errorlevel 1 goto :version_mismatch

"%PYEXE%" -m pip --version >nul 2>&1
if not errorlevel 1 goto :pip_ready
echo [1/4] Installing pip via get-pip.py...
if exist "%~dp0get-pip.py" goto :get_pip_run
echo Downloading get-pip.py...
powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%~dp0get-pip.py'" 2>nul
if not exist "%~dp0get-pip.py" goto :get_pip_failed
:get_pip_run
"%PYEXE%" "%~dp0get-pip.py" --quiet
if errorlevel 1 goto :pip_failed
echo [OK] pip installed.
:pip_ready

echo.

rem issue #362: check packages\ against requirements.lock first. Dependencies used to be added to
rem requirements.txt without ever re-downloading wheels (truststore, added in #363, never landed
rem here), so offline installs died on other machines with "No matching distribution found".
echo [2/4] Checking packages\ against requirements.lock...
"%PYEXE%" "%~dp0tools\offline_packages.py" check
if errorlevel 1 echo [HINT] Offline set is incomplete - trying it anyway, then falling back to the network.

echo.
echo [3/4] Offline install (pinned versions from requirements.lock, no network)...
"%PYEXE%" -m pip install --no-index --find-links=packages -r requirements.lock
if not errorlevel 1 goto :offline_ok

echo.
echo [4/4] Offline failed - online install, same pinned versions...
"%PYEXE%" -m pip install -r requirements.lock
if errorlevel 1 goto :install_failed

:offline_ok
rem issue #362: the old trailing `pip install "uvicorn[standard]"` is gone - it pulled uvicorn out
rem of the lock, and websocket support is already pinned there (websockets==14.2).

echo.
echo [verify] Checking dependencies one by one...
"%PYEXE%" "%~dp0tools\check_dependencies.py"
if not errorlevel 1 goto :done
echo.
echo [MISSING] The Chinese list above names exactly which packages are still missing.
echo Re-run this file, or run: "%PYEXE%" -m pip install -r requirements.lock
pause
exit /b 1

:done
echo.
echo ============================================
echo   Done. Run run.bat to start the app.
echo ============================================
pause
exit /b 0

:python_missing
echo.
echo [ERROR] Python not found. Put the extracted python folder next to this file
echo [ERROR] or install Python 3.12 and retry
pause
exit /b 1

:version_mismatch
echo.
echo Wrong Python version - 3.12 is required (see the message above).
pause
exit /b 1

:get_pip_failed
echo [ERROR] Failed to download get-pip.py. Check network connection.
pause
exit /b 1

:pip_failed
echo [ERROR] Failed to install pip.
pause
exit /b 1

:install_failed
echo.
echo [ERROR] Install failed. Check the network, or run:
echo    "%PYEXE%" tools\offline_packages.py download
echo then re-run this file.
pause
exit /b 1
