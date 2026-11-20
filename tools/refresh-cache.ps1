param(
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonPath) {
    $bundled = Join-Path $root 'python\python.exe'
    $PythonPath = if (Test-Path -LiteralPath $bundled) { $bundled } else { 'python.exe' }
}

$exitCode = 1
Push-Location -LiteralPath $root
try {
    $env:PYTHONUTF8 = '1'
    # 不生成本次清理工具自身的字节码。
    & $PythonPath -B -m tools.refresh_cache
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        Write-Host '刷新完成。无需重启服务，重新载入页面即可使用新的资源版本。'
    }
} catch {
    Write-Host "[REFRESH] RESULT: failures=refresh_tool_failed"
    Write-Host $_.Exception.Message
} finally {
    Pop-Location
}
exit $exitCode
