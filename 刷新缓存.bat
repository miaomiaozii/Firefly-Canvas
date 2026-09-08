@echo off
setlocal EnableExtensions
cd /d "%~dp0" || goto :failed

set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo === Firefly Canvas 缓存刷新 ===
echo 将执行：停止服务 → 清理 Python 字节码缓存 → 刷新前端资源版本号 → 重启服务 → 自动打开浏览器
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\refresh-cache.ps1" -PythonPath "%PYEXE%"
if errorlevel 1 goto :failed

echo.
echo 刷新完成：浏览器打开后即可测试最新代码（前后端缓存均已失效）。
timeout /t 3 >nul
endlocal & exit /b 0

:failed
echo.
echo 刷新失败。请检查上方输出；服务日志在 data\server.stderr.log
pause
endlocal & exit /b 1
