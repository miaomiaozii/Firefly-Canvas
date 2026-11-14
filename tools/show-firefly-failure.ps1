# 启动失败时把话说清楚（票 #363）。
#
# 现场：服务在隐藏窗口里崩，tools/wait-for-local-server.ps1 等 60 秒后裸 exit 1，
# run.bat 只写一句英文，用户完全不知道发生了什么。这个文件被别的启动脚本 dot-source，
# 提供两个函数：
#   Get-FireflyBootLogTail  取最近一次 boot stderr 日志的尾部（那对日志只兜「Python 日志
#                           配置生效之前就崩」的现场，见 firefly_server_log.py / 票 #364）
#   Show-FireflyFailure     控制台打中文 + 弹窗；没有桌面会话（计划任务、远程）时弹窗会失败，
#                           失败本身也要说出来，不能又变回静默。
#
# 编码：Windows PowerShell 5.1 读无 BOM 的 .ps1 会按 GBK 码页解析，中文变乱码（实测）。
# 所以带中文的 .ps1 必须存成 UTF-8 with BOM —— tools/restart-firefly.ps1 已经是这么做的。

function Get-FireflyBootLogTail {
    param(
        [Parameter(Mandatory = $true)][string]$LogDir,
        [int]$Lines = 15
    )
    if (-not (Test-Path -LiteralPath $LogDir)) { return '' }
    $latest = Get-ChildItem -LiteralPath $LogDir -Filter 'boot-*.stderr.log' -ErrorAction SilentlyContinue |
        Sort-Object -Property LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $latest) { return '' }
    try {
        return (@(Get-Content -LiteralPath $latest.FullName -Tail $Lines -Encoding UTF8) -join [Environment]::NewLine)
    } catch {
        return ''
    }
}

function Show-FireflyFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [switch]$NoPopup,
        # 0 = 不自动关（给用户看默认这样）；自动化验收传几秒，弹窗自己关掉，不挡住人。
        [int]$PopupTimeoutSec = 0
    )
    # 隐藏窗口里控制台看不见，但被重定向时能进日志、测试也能断言；弹窗才是给用户看的。
    Write-Output $Message
    if ($NoPopup) { return }
    try {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($Message, $PopupTimeoutSec, 'Firefly Canvas 启动失败', 16)
    } catch {
        Write-Output ("（弹窗没弹出来：" + $_.Exception.Message + "。请把上面的文字读一遍。）")
    }
}
