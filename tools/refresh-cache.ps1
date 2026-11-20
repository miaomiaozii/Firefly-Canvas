param(
    [string]$PythonPath = ''
)

# Firefly Canvas 缓存刷新：
#   1) 停止运行中的服务（端口 3000 + 孤儿 run-server.py / main.py 进程）
#   2) 清理全部 __pycache__（后端字节码缓存）
#   3) 重启服务（#452：新进程在 HTML 响应中刷新前端缓存戳），就绪后自动打开浏览器
#
# 用法（在项目根目录）：  powershell -ExecutionPolicy Bypass -File tools\refresh-cache.ps1
# 或直接双击根目录的  刷新缓存.bat

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$uri = 'http://127.0.0.1:3000/'

function Resolve-Python {
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) { return $PythonPath }
    $bundled = Join-Path $root 'python\python.exe'
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    return 'python.exe'
}

function Stop-FireflyServer {
    $killed = $false
    # 1) Kill whatever is listening on port 3000 (most accurate).
    $conns = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            try {
                Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop
                Write-Host "[REFRESH] Stopped server PID $($conn.OwningProcess) (port 3000)"
                $killed = $true
            } catch {
                # Process may already have exited.
            }
        }
    }
    # 2) Kill orphaned python processes still running run-server.py / main.py.
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'run-server\.py|main\.py' }
    if ($procs) {
        foreach ($proc in $procs) {
            try {
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
                Write-Host "[REFRESH] Stopped orphan server PID $($proc.ProcessId)"
                $killed = $true
            } catch {
                # Ignore; already handled above or gone.
            }
        }
    }
    if (-not $killed) {
        Write-Host "[REFRESH] No running Firefly server found."
    }
    # Give the OS a moment to release the port (and any locked .pyc files).
    Start-Sleep -Milliseconds 800
}

function Clean-PythonCaches {
    # Backend cache: remove every __pycache__ under the project (skip node_modules).
    $removed = @()
    Get-ChildItem -Path $root -Recurse -Directory -Filter '__pycache__' -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\node_modules\\' } |
        ForEach-Object {
            try {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop
                $removed += $_.FullName
            } catch {
                Write-Host "[WARN] Could not remove $($_.FullName): $($_.Exception.Message)"
            }
        }
    Write-Host "[REFRESH] Removed $($removed.Count) __pycache__ director$(if ($removed.Count -eq 1) { 'y' } else { 'ies' }) (backend bytecode cache)."
}

function Start-FireflyServer {
    $py = Resolve-Python
    # issue #364：不再重定向到 data\ 下的 server.*.log（固定名、下次启动直接盖掉）。
    # 每次启动一对带 UTC 时间戳的 boot 日志；服务日志走 logs\server.log（UTF-8、追加、
    # 7 天 / 100MB 轮转，见 firefly_server_log.py）。
    $logDir = Join-Path $root 'logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    $stdout = Join-Path $logDir ("boot-$stamp.stdout.log")
    $stderr = Join-Path $logDir ("boot-$stamp.stderr.log")
    # 不设 UTF-8，Windows 子进程按 GBK 码页写，中文日志全成乱码。
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # Same entrypoint as run.bat -> launch-firefly.ps1.
    Start-Process -FilePath $py `
        -ArgumentList @('tools\run-server.py') `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr | Out-Null
    Write-Host "[REFRESH] Server starting via tools\run-server.py ... log: $stdout"
}

Write-Host "=== Firefly Canvas cache refresh ==="
Stop-FireflyServer
Clean-PythonCaches
Start-FireflyServer

# Reuse the existing wait + open-browser helper.
$waitScript = Join-Path $root 'tools\wait-for-local-server.ps1'
Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $waitScript) `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null

Write-Host "Done. The browser will open at $uri once the server is ready."
