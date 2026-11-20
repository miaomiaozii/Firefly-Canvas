param(
    [string]$PythonPath = '',
    [switch]$AutoStart,
    [switch]$NoBrowser,
    [switch]$NoPopup,
    [string]$StartupFolder = [Environment]::GetFolderPath('Startup'),
    [ValidateRange(1, 65535)][int]$Port = $(if ($env:FIREFLY_PORT) { [int]$env:FIREFLY_PORT } else { 3000 })
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$uri = "http://127.0.0.1:$Port/"
. (Join-Path $PSScriptRoot 'firefly-boot-logs.ps1')
. (Join-Path $PSScriptRoot 'firefly-desktop-links.ps1')
. (Join-Path $PSScriptRoot 'show-firefly-failure.ps1')

$script:server = $null
$script:boot = $null
$script:readyPath = $null
$script:ready = $false
$script:wantBrowser = -not $AutoStart
$script:transition = ''
$script:deadline = $null
$script:timer = $null
$script:tray = $null
$script:context = $null
$script:failed = $false
$activation = $null
$mutex = $null
$ownsMutex = $false

function Get-FireflyDesktopKey {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $bytes = [Text.Encoding]::UTF8.GetBytes("$sid|$($root.ToUpperInvariant())|$Port")
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return 'Local\FireflyCanvas-' + [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '') }
    finally { $sha.Dispose() }
}

function Clear-FireflyReadyFile {
    if ($script:readyPath) {
        Remove-Item -LiteralPath $script:readyPath -ErrorAction SilentlyContinue
    }
}

function Stop-FireflyDesktopTree {
    if ($script:server) {
        $script:server.Dispose()
        $script:server = $null
    }
    Clear-FireflyReadyFile
    $script:ready = $false
}

function Start-FireflyDesktopService {
    $script:boot = New-FireflyBootLogPaths -LogDir $logDir
    $script:readyPath = $script:boot.Stdout + '.ready'
    $script:ready = $false
    $script:server = [FireflyDesktop.FireflyDesktopProcess]::new(
        $python, $root, $script:boot.Stdout, $script:boot.Stderr, $script:readyPath)
    $script:deadline = [DateTime]::UtcNow.AddSeconds(60)
}

function Show-FireflyDesktopError([string]$Message) {
    $script:failed = $true
    if ($script:timer) { $script:timer.Stop() }
    if ($script:tray) { $script:tray.Visible = $false }
    Stop-FireflyDesktopTree
    if ($script:boot) {
        $tail = Get-FireflyBootLogTail -LogDir $logDir -StderrPath $script:boot.Stderr -Lines 8
        if ($tail) { $Message += "`n`n启动日志末尾：`n$tail" }
    }
    $Message += "`n`n检查 Python 及依赖可双击「安装依赖.bat」。如果端口被其他程序使用，请先关闭它再试；托盘不会接管其他进程。`n日志目录：$logDir"
    Show-FireflyFailure -Message $Message -NoPopup:$NoPopup | Out-Host
    if ($script:context) { $script:context.ExitThread() }
}

function Request-FireflyTransition([string]$Action) {
    if ($script:transition -eq 'exit') { return }
    $script:transition = $Action
    $script:deadline = [DateTime]::UtcNow.AddSeconds(8)
    if ($script:server) { $script:server.RequestStop() }
    if ($Action -eq 'exit') { $script:tray.Visible = $false }
}

function Update-FireflyDesktop {
    if ($activation.WaitOne(0)) { $script:wantBrowser = $true }
    if ($script:transition) {
        if ($script:server -and -not $script:server.HasExited -and [DateTime]::UtcNow -lt $script:deadline) { return }
        Stop-FireflyDesktopTree
        if ($script:transition -eq 'exit') {
            $script:timer.Stop()
            $script:context.ExitThread()
            return
        }
        $script:transition = ''
        Start-FireflyDesktopService
    }
    if ($script:server.HasExited) { throw "服务进程提前退出（端口 $Port）。" }
    if (-not $script:ready) {
        if (Test-Path -LiteralPath $script:readyPath -PathType Leaf) {
            $script:ready = $true
            Clear-FireflyReadyFile
        } elseif ([DateTime]::UtcNow -ge $script:deadline) {
            throw "等待服务就绪超时（端口 $Port）。"
        }
    }
    if ($script:ready -and $script:wantBrowser) {
        $script:wantBrowser = $false
        if (-not $NoBrowser) {
            try { Start-Process -FilePath $uri }
            catch { Show-FireflyFailure -Message "无法打开浏览器。服务仍在运行，可以手动访问 $uri" -NoPopup:$NoPopup | Out-Host }
        }
    }
}

try {
    $logDir = Get-FireflyLogDir -RepoRoot $root
    $key = Get-FireflyDesktopKey
    # Create the activation event first so even a second launch during boot can ask the owner to open.
    $activation = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::AutoReset, "$key-open")
    $mutex = [Threading.Mutex]::new($false, "$key-owner")
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) {
        if (-not $AutoStart) { [void]$activation.Set() }
        exit 0
    }

    $env:FIREFLY_PORT = [string]$Port
    $python = Resolve-FireflyDesktopPython -RepoRoot $root -PythonPath $PythonPath
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    Add-Type -Path (Join-Path $PSScriptRoot 'FireflyDesktopProcess.cs')

    $script:context = [Windows.Forms.ApplicationContext]::new()
    $script:tray = [Windows.Forms.NotifyIcon]::new()
    $icon = [Drawing.Icon]::new((Join-Path $root 'static\img\firefly-logo.ico'))
    $menu = [Windows.Forms.ContextMenuStrip]::new()
    $openItem = $menu.Items.Add('打开萤火画布')
    $restartItem = $menu.Items.Add('重启服务')
    $autoItem = $menu.Items.Add('开机自启')
    $exitItem = $menu.Items.Add('退出')
    $openItem.add_Click({ $script:wantBrowser = $true })
    $restartItem.add_Click({ if (-not $script:transition) { Request-FireflyTransition 'restart' } })
    $exitItem.add_Click({ Request-FireflyTransition 'exit' })
    $autoItem.Enabled = [bool]$startupFolder
    if ($autoItem.Enabled) {
        $autoItem.Checked = Test-FireflyAutoStart -RepoRoot $root -Folder $startupFolder
        $autoItem.add_Click({
            try {
                Set-FireflyAutoStart -RepoRoot $root -Folder $startupFolder -Enabled (-not $autoItem.Checked)
                $autoItem.Checked = Test-FireflyAutoStart -RepoRoot $root -Folder $startupFolder
            } catch {
                Show-FireflyFailure -Message "未能修改开机自启：$($_.Exception.Message)" -NoPopup:$NoPopup | Out-Host
            }
        })
    }
    $script:tray.Icon = $icon
    $script:tray.Text = '萤火画布'
    $script:tray.ContextMenuStrip = $menu
    $script:tray.add_DoubleClick({ $script:wantBrowser = $true })

    Start-FireflyDesktopService
    $script:timer = [Windows.Forms.Timer]::new()
    $script:timer.Interval = 200
    $script:timer.add_Tick({
        try { Update-FireflyDesktop }
        catch { Show-FireflyDesktopError "萤火画布启动或运行失败：$($_.Exception.Message)" }
    })
    $script:tray.Visible = $true
    $script:timer.Start()
    [Windows.Forms.Application]::Run($script:context)
} catch {
    Show-FireflyDesktopError "无法启动萤火画布：$($_.Exception.Message)"
} finally {
    if ($script:timer) { $script:timer.Dispose() }
    if ($script:tray) { $script:tray.Visible = $false; $script:tray.Dispose() }
    if ($menu) { $menu.Dispose() }
    if ($icon) { $icon.Dispose() }
    if ($script:context) { $script:context.Dispose() }
    if ($script:server) { $script:server.RequestStop() }
    Stop-FireflyDesktopTree
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    if ($activation) { $activation.Dispose() }
}
if ($script:failed) { exit 1 }
