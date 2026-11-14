param(
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$uri = 'http://127.0.0.1:3000/'

function Test-FireflyServer {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $uri
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

if (-not $PythonPath) {
    $bundled = Join-Path $root 'python\python.exe'
    if (Test-Path -LiteralPath $bundled) {
        $PythonPath = $bundled
    } else {
        $PythonPath = 'python.exe'
    }
}

if ($PythonPath -eq 'python') {
    $PythonPath = 'python.exe'
}

if (-not (Test-FireflyServer)) {
    # issue #364：以前这里重定向到 data\ 下的 server.*.log——固定文件名，
    # 每次启动直接盖掉，崩溃前的日志就没了（也没轮转）。改成每次启动一对带 UTC 时间戳的
    # boot 日志：它只兜「Python 日志配置生效之前就崩」的现场（导 ImportError 之类）。
    # 服务自己的日志走 logs\server.log，UTF-8、追加写、7 天 / 100MB 轮转，见 firefly_server_log.py；
    # 这对 boot 日志超过 7 天由服务启动时自己清（prune_boot_logs）。
    $logDir = Join-Path $root 'logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    $stdout = Join-Path $logDir ("boot-$stamp.stdout.log")
    $stderr = Join-Path $logDir ("boot-$stamp.stderr.log")
    # 不设 UTF-8，Windows 子进程按 GBK 码页写，中文日志全成乱码（票面 #364）。
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # The Python helper executes runpy.run_path('main.py', run_name='__main__').
    Start-Process -FilePath $PythonPath `
        -ArgumentList @('tools\run-server.py') `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr | Out-Null
}

# Browser opening is also detached. The batch file may close immediately and
# the server process above continues independently until it is stopped by the
# user or the OS.
$waitScript = Join-Path $root 'tools\wait-for-local-server.ps1'
Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $waitScript) `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null
