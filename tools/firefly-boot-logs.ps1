function Get-FireflyLogDir {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $runtimeRoot = $env:FIREFLY_RUNTIME_ROOT
    if (-not $runtimeRoot -or -not $runtimeRoot.Trim()) { $runtimeRoot = $RepoRoot }
    return [IO.Path]::GetFullPath((Join-Path $runtimeRoot.Trim() 'logs'))
}

function New-FireflyBootLogPaths {
    param([Parameter(Mandatory = $true)][string]$LogDir)
    [void][IO.Directory]::CreateDirectory($LogDir)
    # Multiple attempts (and installations sharing a runtime) can start in the same second.
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N')
    return @{
        Stdout = Join-Path $LogDir "boot-$stamp.stdout.log"
        Stderr = Join-Path $LogDir "boot-$stamp.stderr.log"
    }
}
