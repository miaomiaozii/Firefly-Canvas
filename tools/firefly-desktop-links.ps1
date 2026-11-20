function Resolve-FireflyDesktopPython {
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [string]$PythonPath)
    if (-not $PythonPath) {
        $bundled = Join-Path $RepoRoot 'python\python.exe'
        $PythonPath = if (Test-Path -LiteralPath $bundled -PathType Leaf) { $bundled } else { 'python.exe' }
    }
    $command = Get-Command -Name $PythonPath -CommandType Application -ErrorAction Stop | Select-Object -First 1
    return $command.Source
}

function Get-FireflyDesktopLinkPath {
    param([Parameter(Mandatory = $true)][string]$Folder)
    Join-Path $Folder '萤火画布.lnk'
}

function New-FireflyDesktopLink {
    param([Parameter(Mandatory = $true)][string]$RepoRoot,
          [Parameter(Mandatory = $true)][string]$Folder,
          [switch]$AutoStart)
    $launcher = Join-Path $RepoRoot 'tools\open-firefly-desktop.vbs'
    $icon = Join-Path $RepoRoot 'static\img\firefly-logo.ico'
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf) -or -not (Test-Path -LiteralPath $icon -PathType Leaf)) {
        throw '启动脚本或图标不完整，请重新解压萤火画布。'
    }
    [void][IO.Directory]::CreateDirectory($Folder)
    $shell = New-Object -ComObject WScript.Shell
    $path = Get-FireflyDesktopLinkPath -Folder $Folder
    $target = Join-Path ([Environment]::SystemDirectory) 'wscript.exe'
    $arguments = '//nologo "' + $launcher + '"'
    if ($AutoStart) { $arguments += ' /autostart' }
    $link = $shell.CreateShortcut($path)
    if ((Test-Path -LiteralPath $path) -and ($link.TargetPath -ne $target -or $link.Arguments -ne $arguments)) {
        throw "快捷方式已存在且不属于此安装：$path。请先移走旧快捷方式再试。"
    }
    $link.TargetPath = $target
    $link.Arguments = $arguments
    $link.WorkingDirectory = $RepoRoot
    $link.IconLocation = "$icon,0"
    $link.Description = '萤火画布'
    $link.Save()
}

function Test-FireflyAutoStart {
    param([string]$RepoRoot, [string]$Folder)
    $path = Get-FireflyDesktopLinkPath -Folder $Folder
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $launcher = Join-Path $RepoRoot 'tools\open-firefly-desktop.vbs'
    return $link.TargetPath -eq (Join-Path ([Environment]::SystemDirectory) 'wscript.exe') -and
        $link.Arguments -eq ('//nologo "' + $launcher + '" /autostart')
}

function Set-FireflyAutoStart {
    param([string]$RepoRoot, [string]$Folder, [bool]$Enabled)
    if ($Enabled) { New-FireflyDesktopLink -RepoRoot $RepoRoot -Folder $Folder -AutoStart }
    elseif (Test-FireflyAutoStart -RepoRoot $RepoRoot -Folder $Folder) {
        Remove-Item -LiteralPath (Get-FireflyDesktopLinkPath -Folder $Folder) -ErrorAction Stop
    }
}
