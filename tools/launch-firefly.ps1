param(
    [string]$PythonPath = '',
    [switch]$NoPopup,
    [switch]$NoBrowser,
    [ValidateRange(1, 65535)][int]$Port = $(if ($env:FIREFLY_PORT) { [int]$env:FIREFLY_PORT } else { 3000 })
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$uri = "http://127.0.0.1:$Port/"
$env:FIREFLY_PORT = [string]$Port
$server = $null
$stderr = ''
. (Join-Path $PSScriptRoot 'firefly-boot-logs.ps1')
$logDir = Get-FireflyLogDir -RepoRoot $root

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
    # #363：先点名无法导入的依赖，不把它们当成普通的就绪超时。
    $checkScript = Join-Path $root 'tools\check_dependencies.py'
    $missingNames = @()
    $checkExit = 0
    # 只有命令本身找不到才算「找不到 Python」。PowerShell 5.1 在 ErrorActionPreference=Stop 下，
    # 会把原生命令写到 stderr 的任何内容（例如 requests 的 RequestsDependencyWarning）
    # 当成终止错误抛出，即使已重定向到 $null。以前这里被误报成「找不到可用的 Python」，
    # 用户无法启动。依赖是否齐全只看退出码。
    if (-not (Get-Command $PythonPath -ErrorAction SilentlyContinue)) {
        $checkExit = 1
        $missingNames = @("找不到可用的 Python：$PythonPath")
    } else {
        # --names-only：只回纯 ASCII 的包名。PowerShell 5.1 按 OEM 码页解码子进程输出，
        # 中间接中文文本容易乱码，所以中文提示由本脚本自己拼。
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $missingNames = @(
                & $PythonPath $checkScript --names-only 2>$null |
                    ForEach-Object { ([string]$_).Trim() } |
                    Where-Object { $_ }
            )
            $checkExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    if ($checkExit -ne 0) {
        $lines = @("Firefly Canvas 启动失败：Python 依赖不完整，服务没有启动（端口 $Port）。", '')
        if ($missingNames.Count) {
            $lines += ('缺的依赖（包名 (导入名)）：' + ($missingNames -join '、'))
            $lines += ''
        }
        $lines += '处理办法：双击「安装依赖.bat」，装完再双击 run.bat。'
        $lines += "也可以在命令行里执行：python -m pip install -r requirements.txt"
        $lines += "日志目录：$logDir"
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
    try {
        $boot = New-FireflyBootLogPaths -LogDir $logDir
        $stdout = $boot.Stdout
        $stderr = $boot.Stderr
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        $env:FIREFLY_BOOT_REDIRECT = '1'
        # The Python helper executes runpy.run_path('main.py', run_name='__main__').
        $server = Start-Process -FilePath $PythonPath `
            -ArgumentList @('tools\run-server.py') `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr -PassThru
    } catch {
        Show-FireflyFailure -Message "Firefly Canvas 启动失败：无法启动服务进程。$($_.Exception.Message)`n请检查 Python 路径及日志目录的写入权限：$logDir" -NoPopup:$NoPopup
        exit 1
    }
}

# Browser opening is also detached. The batch file may close immediately and
# the server process above continues independently until it is stopped by the
# user or the OS.
$waitArgs = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', 'tools\wait-for-local-server.ps1', '-Uri', $uri, '-LogDir', ('"' + $logDir + '"'))
if ($server) { $waitArgs += @('-ServerProcessId', $server.Id, '-StderrPath', ('"' + $stderr + '"')) }
if ($NoPopup) { $waitArgs += '-NoPopup' }
if ($NoBrowser) { $waitArgs += '-NoBrowser' }
try {
    Start-Process -FilePath 'powershell.exe' -ArgumentList $waitArgs `
        -WorkingDirectory $root -WindowStyle Hidden | Out-Null
} catch {
    Show-FireflyFailure -Message "Firefly Canvas 无法检查服务是否就绪。$($_.Exception.Message)`n请查看日志：$logDir" -NoPopup:$NoPopup
    exit 1
}
