param(
    [string]$DesktopFolder = [Environment]::GetFolderPath('DesktopDirectory'),
    [string]$ProgramsFolder = [Environment]::GetFolderPath('Programs'),
    [switch]$NoPopup
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'firefly-desktop-links.ps1')
. (Join-Path $PSScriptRoot 'show-firefly-failure.ps1')
try {
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    New-FireflyDesktopLink -RepoRoot $root -Folder $DesktopFolder
    New-FireflyDesktopLink -RepoRoot $root -Folder $ProgramsFolder
} catch {
    Show-FireflyFailure -Message "依赖已安装，但无法创建「萤火画布」快捷方式。$($_.Exception.Message)`n请检查桌面和开始菜单文件夹的写入权限，或运行 run.bat（开发启动）。" -NoPopup:$NoPopup
    exit 1
}
