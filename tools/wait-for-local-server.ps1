# 等本地服务就绪，就绪后打开浏览器；等不到就把失败原因说清楚（票 #363）。
#
# 票面现场：服务在隐藏窗口里崩（常见是依赖没装齐），这个脚本等 60 秒后裸 exit 1，
# 用户看不到任何信息。现在等不到会：读最近一次 boot stderr 尾部 → 控制台中文 + 弹窗 → 再 exit 1。
#
# 参数是票 #363 加的：默认值就是原来的硬编码行为，加参数是为了能被自动化测试打到
# （不打到真实 3000 端口、不开浏览器、不弹窗）。
param(
    [string]$Uri = 'http://127.0.0.1:3000/',
    [int]$TimeoutSec = 60,
    [string]$LogDir = '',
    [switch]$NoBrowser,
    [switch]$NoPopup
)

$ErrorActionPreference = 'SilentlyContinue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $LogDir) { $LogDir = Join-Path $root 'logs' }

$helper = Join-Path $PSScriptRoot 'show-firefly-failure.ps1'
if (Test-Path -LiteralPath $helper) {
    . $helper
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $Uri
        if ($response.StatusCode -eq 200) {
            if (-not $NoBrowser) { Start-Process $uri }
            exit 0
        }
    } catch {
        # The server may still be importing dependencies or creating its data directories.
    }
    Start-Sleep -Milliseconds 500
}

if (Test-Path -LiteralPath $helper) {
    $tail = Get-FireflyBootLogTail -LogDir $LogDir -Lines 15
    $hint = '可能原因和处理办法：'
    $lines = @()
    $lines += "Firefly Canvas 启动失败：$TimeoutSec 秒内本地服务没有就绪（$Uri）。"
    $lines += ''
    if ($tail) {
        $lines += "最近一次启动的报错尾部（$LogDir\boot-<时间戳>.stderr.log）："
        $lines += '----'
        $lines += $tail
        $lines += '----'
        if ($tail -match 'ModuleNotFoundError' -or $tail -match 'ImportError') {
            $lines += '看起来是 Python 依赖缺失：双击「安装依赖.bat」，装完再双击 run.bat。'
        }
    } else {
        $lines += "（$LogDir 里没有 boot-*.stderr.log，说明进程连启动输出都没落到日志。）"
    }
    $lines += ''
    $lines += $hint
    $lines += '1) 依赖没装齐：双击「安装依赖.bat」再试；也可以在命令行执行 python -m pip install -r requirements.txt。'
    $lines += "2) 端口被占用或已有旧服务在跑：先关掉旧的 Firefly 服务窗口再试（本次探的是 $Uri）。"
    $lines += "3) 服务自己的日志：$LogDir\server.log（UTF-8、追加写、7 天 / 100MB 轮转）。"
    Show-FireflyFailure -Message ($lines -join [Environment]::NewLine) -NoPopup:$NoPopup
} else {
    # 兜底：连提示脚本都找不到时也不能静默退出。
    Write-Output "Firefly Canvas 启动失败：$TimeoutSec 秒内本地服务没有就绪（$Uri）。缺少 $helper，无法给出更详细的提示。"
}

exit 1
