param(
    [string]$PythonPath = '',
    # 票 #363：自动化测试里不能真弹窗，也不能挨在用户的 3000 端口上。
    # 默认值就是原来的行为（双击 run.bat 时两者都不传）。
    [switch]$NoPopup,
    [int]$Port = 3000
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$uri = "http://127.0.0.1:$Port/"

$failureHelper = Join-Path $PSScriptRoot 'show-firefly-failure.ps1'
if (Test-Path -LiteralPath $failureHelper) {
    . $failureHelper
}

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
    # issue #363：依赖没装齐时，服务在隐藏窗口里崩，用户什也看不到（票面：缺 Pillow 等依赖时
    # wait-for-local-server.ps1 等 60 秒后静默退出）。先跑一遍依赖体检，缺就直接点名，不要等超时。
    $checkScript = Join-Path $root 'tools\check_dependencies.py'
    $missingNames = @()
    $checkExit = 0
    try {
        # --names-only：只回纯 ASCII 的包名。PowerShell 5.1 按 OEM 码页解码子进程输出，
        # 中间接中文文本容易乱码，所以中文提示由本脚本自己拼。
        $missingNames = @(
            & $PythonPath $checkScript --names-only 2>$null |
                ForEach-Object { ([string]$_).Trim() } |
                Where-Object { $_ }
        )
        $checkExit = $LASTEXITCODE
    } catch {
        $checkExit = 1
        $missingNames = @("找不到可用的 Python：$PythonPath")
    }
    if ($checkExit -ne 0) {
        $lines = @("Firefly Canvas 启动失败：Python 依赖不完整，服务没有启动（端口 $Port）。", '')
        if ($missingNames.Count) {
            $lines += ('缺的依赖（包名 (导入名)）：' + ($missingNames -join '、'))
            $lines += ''
        }
        $lines += '处理办法：双击「安装依赖.bat」，装完再双击 run.bat。'
        $lines += "也可以在命令行里执行：python -m pip install -r requirements.txt"
        $lines += "完整日志：$root\logs\server.log"
        if (Test-Path -LiteralPath $failureHelper) {
            Show-FireflyFailure -Message ($lines -join [Environment]::NewLine) -NoPopup:$NoPopup
        } else {
            Write-Output ($lines -join [Environment]::NewLine)
        }
        exit 1
    }

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
